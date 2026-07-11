import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app


class FakeMemory:
    def __init__(self, magic: bytes):
        self.magic = magic

    def read(self, _address: int, size: int) -> bytes:
        return self.magic[:size]


class AddressedMemory:
    """FakeMemory variant whose contents differ per guest address."""

    def __init__(self, mapping: dict[int, bytes]):
        self.mapping = mapping

    def read(self, address: int, size: int) -> bytes:
        try:
            return self.mapping[address][:size]
        except KeyError:
            raise OSError(f"read failed at guest 0x{address:X}")


class FakeCacheKeys:
    exe_name = "xenia_canary.exe"

    def cache_key(self, target_name: str) -> str:
        return f"exe|0x100000000|{target_name}"

    def stable_cache_key(self, target_name: str) -> str:
        return f"exe|{target_name}"


class AlphaVmTests(unittest.TestCase):
    def test_first_cache_load_creates_virgin_json(self):
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "address_cache.json"
            with patch.object(app, "address_cache_path", return_value=cache_path):
                data = app.load_address_cache()

            self.assertEqual(data, {"version": 2, "entries": {}})
            self.assertTrue(cache_path.is_file())
            self.assertEqual(app.json.loads(cache_path.read_text(encoding="utf-8")), data)

    def test_only_requested_script_targets_are_exposed(self):
        self.assertEqual(
            app.script_choices_for_game_type("MP"),
            ["_objpoints.gsc", "_callbacksetup.gsc"],
        )
        self.assertEqual(app.script_choices_for_game_type("ZM"), ["_callbacksetup.gsc"])

    def test_retail_blob_is_adapted_to_verified_alpha_revision(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        memory = FakeMemory(app.T6_ALPHA_VM_MAGIC)

        adapted = app.adapt_compiled_gsc_to_live_vm(memory, 0xA0000000, blob)

        self.assertEqual(adapted, app.T6_ALPHA_VM_MAGIC + b"payload")
        app.assert_gsc_vm_compatible(memory, 0xA0000000, adapted)

    def test_retail_blob_is_unchanged_for_retail_vm(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        memory = FakeMemory(app.T6_RETAIL_VM_MAGIC)

        self.assertEqual(
            app.adapt_compiled_gsc_to_live_vm(memory, 0xA0000000, blob), blob
        )

    def test_unknown_revision_is_not_rewritten_or_accepted(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        memory = FakeMemory(bytes.fromhex("804753430D0A0004"))

        self.assertEqual(
            app.adapt_compiled_gsc_to_live_vm(memory, 0xA0000000, blob), blob
        )
        with self.assertRaisesRegex(RuntimeError, "VM revision mismatch"):
            app.assert_gsc_vm_compatible(memory, 0xA0000000, blob)

    def test_truncated_blob_is_rejected(self):
        memory = FakeMemory(app.T6_ALPHA_VM_MAGIC)
        with self.assertRaisesRegex(RuntimeError, "too small"):
            app.assert_gsc_vm_compatible(memory, 0xA0000000, b"short")

    def test_auto_vm_adapts_retail_output_to_live_alpha(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        live = FakeMemory(app.T6_ALPHA_VM_MAGIC)
        prepared = app.prepare_compiled_gsc_for_vm(live, 0xA0000000, blob, app.VM_AUTO)
        self.assertEqual(prepared, app.T6_ALPHA_VM_MAGIC + b"payload")

    def test_auto_vm_rejects_unknown_live_revision(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        live = FakeMemory(bytes.fromhex("804753430D0A0004"))
        with self.assertRaisesRegex(RuntimeError, "VM revision mismatch"):
            app.prepare_compiled_gsc_for_vm(live, 0xA0000000, blob, app.VM_AUTO)

    def test_auto_vm_rejects_stale_non_gsc_target(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        live = FakeMemory(bytes.fromhex("48C1EA0383E207E8"))
        with self.assertRaisesRegex(RuntimeError, "stale or invalid"):
            app.prepare_compiled_gsc_for_vm(live, 0xA0000000, blob, app.VM_AUTO)

    def test_explicit_vm_selector_controls_output_revision(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        live = FakeMemory(app.T6_ALPHA_VM_MAGIC)
        self.assertEqual(
            app.prepare_compiled_gsc_for_vm(live, 0xA0000000, blob, app.VM_ALPHA)[:8],
            app.T6_ALPHA_VM_MAGIC,
        )
        self.assertEqual(
            app.prepare_compiled_gsc_for_vm(live, 0xA0000000, blob, app.VM_RETAIL)[:8],
            app.T6_RETAIL_VM_MAGIC,
        )

    def test_forced_vm_still_rejects_stale_non_gsc_target(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        memory = FakeMemory(bytes.fromhex("48C1EA0383E207E8"))
        with self.assertRaisesRegex(RuntimeError, "stale or invalid"):
            app.prepare_compiled_gsc_for_vm(
                memory, 0xA0000000, blob, app.VM_ALPHA
            )


class FreshInstallResolutionTests(unittest.TestCase):
    STALE_BYTES = bytes.fromhex("48C1EA0383E207E8")
    TARGET = "maps/mp/gametypes/_objpoints.gsc"

    def test_stale_cached_entry_is_evicted_and_rescanned(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        stale = {"object_va": 0xA5BD80E0, "source": "cache"}
        fresh = {"object_va": 0x82000000, "source": "scan"}
        memory = AddressedMemory({0xA5BD80E0: self.STALE_BYTES, 0x82000000: app.T6_ALPHA_VM_MAGIC})
        with patch.object(app, "find_live_gsc_entry", side_effect=[stale, fresh]) as resolver, \
                patch.object(app, "forget_gsc_entry") as forget:
            entry, prepared = app.resolve_entry_and_prepare_blob(memory, self.TARGET, blob, app.VM_AUTO)
        self.assertIs(entry, fresh)
        self.assertEqual(prepared, app.T6_ALPHA_VM_MAGIC + b"payload")
        forget.assert_called_once_with(memory, self.TARGET)
        self.assertEqual(resolver.call_args.kwargs.get("force_scan"), True)

    def test_fresh_scan_failure_is_not_retried(self):
        blob = app.T6_RETAIL_VM_MAGIC + b"payload"
        scanned = {"object_va": 0xA5BD80E0, "source": "scan"}
        memory = AddressedMemory({0xA5BD80E0: self.STALE_BYTES})
        with patch.object(app, "find_live_gsc_entry", return_value=scanned) as resolver:
            with self.assertRaisesRegex(RuntimeError, "stale or invalid"):
                app.resolve_entry_and_prepare_blob(memory, self.TARGET, blob, app.VM_AUTO)
        resolver.assert_called_once()

    def test_forget_gsc_entry_removes_cached_keys(self):
        memory = FakeCacheKeys()
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "address_cache.json"
            with patch.object(app, "address_cache_path", return_value=cache_path):
                cache = app.load_address_cache()
                cache["entries"][memory.cache_key(self.TARGET)] = {"object_va": "0x82000000"}
                cache["entries"][memory.stable_cache_key(self.TARGET)] = {"object_va": "0x82000000"}
                cache["entries"]["exe|other.gsc"] = {"object_va": "0x83000000"}
                app.save_address_cache(cache)
                app.forget_gsc_entry(memory, self.TARGET)
                remaining = app.load_address_cache()["entries"]
        self.assertEqual(list(remaining), ["exe|other.gsc"])

    def test_quick_detect_falls_back_to_full_scan(self):
        sentinel = {"object_va": 0x82000000, "source": "scan"}
        memory = FakeCacheKeys()
        with patch.object(app, "get_cached_gsc_entry", return_value=None), \
                patch.object(app, "database_gsc_entry", return_value=None), \
                patch.object(app, "find_live_gsc_entry", return_value=sentinel) as resolver:
            entry = app.find_quick_gsc_entry(memory, self.TARGET)
        self.assertIs(entry, sentinel)
        resolver.assert_called_once_with(memory, self.TARGET, force_scan=True)

    def test_unreadable_alias_candidate_falls_back_to_verified_object(self):
        obj_size = 0x1000
        alias_va = 0xA5BD80E0
        verified_va = 0x85BD80E0
        table_ref = 0x83000008

        class TableMemory(AddressedMemory):
            def scan(self, needle, limit=128):
                return [table_ref]

            def read_u32(self, address):
                return {table_ref - 4: obj_size, table_ref - 8: 0}[address]

        memory = TableMemory({alias_va: FreshInstallResolutionTests.STALE_BYTES, verified_va: app.T6_ALPHA_VM_MAGIC})
        candidates = app.find_table_candidates_for_object(memory, alias_va, obj_size, "scan", verified_va=verified_va)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["object_va"], verified_va)
        self.assertEqual(candidates[0]["buffer_va"], table_ref)

        readable = TableMemory({alias_va: app.T6_ALPHA_VM_MAGIC})
        candidates = app.find_table_candidates_for_object(readable, alias_va, obj_size, "scan", verified_va=verified_va)
        self.assertEqual(candidates[0]["object_va"], alias_va)


if __name__ == "__main__":
    unittest.main()
