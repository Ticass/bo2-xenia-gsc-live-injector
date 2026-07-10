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
            ["_callbacksetup.gsc", "_objpoints.gsc"],
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


if __name__ == "__main__":
    unittest.main()
