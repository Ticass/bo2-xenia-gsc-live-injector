from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from ctypes import wintypes
from pathlib import Path
from tkinter import BOTH, END, INSERT, LEFT, RIGHT, TOP, X, Button, Canvas, Frame, Label, Listbox, Scrollbar, StringVar, Text, Tk, Toplevel, filedialog, messagebox, scrolledtext, ttk


APP_NAME = "BO2 GSC Live Injector"
GUEST_IMAGE_BASE = 0x82000000
SIG_AT_0 = bytes.fromhex("4d5a9000")
PE_SIG = bytes.fromhex("50450000")
GSC_OBJ_NAME_FIELD_OFFSET = 0x30
GSC_OBJ_SIZE_FIELD_OFFSET = 0x24
GSC_MAGIC = b"\x80GSC"

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READWRITE = 0x40
TH32CS_SNAPPROCESS = 0x2
XENIA_LINEAR_BASE_CANDIDATES = (0x100000000,)

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wintypes.BOOL
k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.restype = wintypes.BOOL
k32.VirtualProtectEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
k32.VirtualProtectEx.restype = wintypes.BOOL


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("__align", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def app_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def user_dir() -> Path:
    path = Path.home() / "Documents" / "BO2 GSC Live Injector"
    path.mkdir(parents=True, exist_ok=True)
    return path


def address_cache_path() -> Path:
    return user_dir() / "address_cache.json"


def load_address_cache() -> dict:
    path = address_cache_path()
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "entries": {}}
        data.setdefault("version", 1)
        data.setdefault("entries", {})
        return data
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}


def save_address_cache(data: dict) -> None:
    path = address_cache_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_processes() -> list[tuple[int, str]]:
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        raise OSError("CreateToolhelp32Snapshot failed")
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
    out: list[tuple[int, str]] = []
    try:
        ok = k32.Process32First(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.szExeFile.decode("latin-1")))
            ok = k32.Process32Next(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


class GuestMemory:
    def __init__(self) -> None:
        self.pid: int | None = None
        self.exe_name = ""
        self.handle = None
        self.membase: int | None = None
        self.linear_bases: set[int] = set(XENIA_LINEAR_BASE_CANDIDATES)

    def open(self) -> str:
        matches = [(pid, exe) for pid, exe in list_processes() if "xenia" in exe.lower()]
        if not matches:
            raise RuntimeError("No Xenia process found.")
        access = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION
        errors: list[str] = []
        for pid, exe in matches:
            handle = k32.OpenProcess(access, False, pid)
            if not handle:
                errors.append(f"{exe} pid={pid}: OpenProcess failed")
                continue
            self.pid, self.exe_name, self.handle = pid, exe, handle
            self.membase = self._find_membase()
            if self.membase is not None:
                break
            errors.append(f"{exe} pid={pid}: no Xbox guest image found")
            k32.CloseHandle(handle)
            self.handle = None
            self.pid = None
            self.exe_name = ""
        if self.membase is None:
            detail = "; ".join(errors) if errors else "no readable Xenia processes"
            raise RuntimeError(f"Xenia found, but CoD/guest memory is not mapped yet. Checked: {detail}")
        return f"{self.exe_name} pid={self.pid}, guest membase=0x{self.membase:X}"

    def cache_key(self, target_name: str) -> str:
        exe = self.exe_name.lower() if self.exe_name else "unknown"
        base = f"0x{self.membase:X}" if self.membase is not None else "unknown"
        target = target_name.replace("\\", "/").lower()
        return f"{exe}|{base}|{target}"

    def _raw_read(self, host_addr: int, size: int) -> bytes | None:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(self.handle, ctypes.c_void_p(host_addr), buf, size, ctypes.byref(got))
        if not ok or got.value != size:
            return None
        return buf.raw

    def _is_readable_host_range(self, host_addr: int, size: int) -> bool:
        mbi = MEMORY_BASIC_INFORMATION()
        end = host_addr + size
        addr = host_addr
        while addr < end:
            res = k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not res:
                return False
            base = mbi.BaseAddress or 0
            region_size = mbi.RegionSize or 0
            readable = mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)) and mbi.Protect != 0
            if not readable or addr < base:
                return False
            nxt = base + region_size
            if nxt <= addr:
                return False
            addr = nxt
        return True

    def _host_candidates_for_guest(self, guest_va: int) -> list[int]:
        candidates: list[int] = []
        if self.membase is not None:
            candidates.append(self.membase + guest_va)
        for base in sorted(self.linear_bases):
            candidates.append(base + guest_va)
        out: list[int] = []
        for cand in candidates:
            if cand not in out:
                out.append(cand)
        return out

    def _guest_to_readable_host(self, guest_va: int, size: int) -> int | None:
        for host_addr in self._host_candidates_for_guest(guest_va):
            if self._is_readable_host_range(host_addr, size):
                return host_addr
        return None

    def _remember_linear_base_from_host(self, host_addr: int) -> None:
        if host_addr < 0x100000000:
            return
        guest_va = host_addr & 0xFFFFFFFF
        if 0x80000000 <= guest_va < 0xF0000000:
            self.linear_bases.add(host_addr - guest_va)

    def iter_readable_host_regions(self):
        addr = 0
        max_addr = 0x7FFFFFFFFFFF
        mbi = MEMORY_BASIC_INFORMATION()
        while addr < max_addr:
            res = k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not res:
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            readable = mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)) and mbi.Protect != 0
            if readable and size >= 0x1000:
                yield base, size
            nxt = base + size
            if nxt <= addr:
                break
            addr = nxt

    def _find_membase(self) -> int | None:
        addr = 0
        max_addr = 0x7FFFFFFFFFFF
        mbi = MEMORY_BASIC_INFORMATION()
        regions: list[tuple[int, int]] = []
        while addr < max_addr:
            res = k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not res:
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            readable = mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)) and mbi.Protect != 0
            if readable and size >= 0x1000:
                regions.append((base, size))
            nxt = base + size
            if nxt <= addr:
                break
            addr = nxt

        for base, _size in regions:
            head = self._raw_read(base, 0x108)
            if head and head[:4] == SIG_AT_0 and head[0x100:0x104] == PE_SIG:
                return base - GUEST_IMAGE_BASE
        for base, size in regions:
            if size < 0x8000000:
                continue
            for guest_start in (0, 0x80000000):
                cand = base - guest_start
                probe = self._raw_read(cand + GUEST_IMAGE_BASE, 0x108)
                if probe and probe[:4] == SIG_AT_0 and probe[0x100:0x104] == PE_SIG:
                    return cand
        return None

    def read(self, guest_va: int, size: int) -> bytes:
        if self.membase is None:
            raise RuntimeError("not connected")
        host_addr = self._guest_to_readable_host(guest_va, size)
        data = self._raw_read(host_addr, size) if host_addr is not None else None
        if data is None:
            raise OSError(f"read failed at guest 0x{guest_va:X}")
        return data

    def write(self, guest_va: int, data: bytes) -> None:
        if self.membase is None:
            raise RuntimeError("not connected")
        tried: list[str] = []
        for host_addr in self._host_candidates_for_guest(guest_va):
            if not self._is_readable_host_range(host_addr, max(1, len(data))):
                tried.append(f"0x{host_addr:X}:unmapped")
                continue
            got = ctypes.c_size_t(0)
            ok = k32.WriteProcessMemory(self.handle, ctypes.c_void_p(host_addr), data, len(data), ctypes.byref(got))
            if ok and got.value == len(data):
                return
            first_err = ctypes.get_last_error()
            old = wintypes.DWORD(0)
            page_start = host_addr & ~0xFFF
            protect_size = ((host_addr + len(data) + 0xFFF) & ~0xFFF) - page_start
            changed = k32.VirtualProtectEx(self.handle, ctypes.c_void_p(page_start), protect_size, PAGE_READWRITE, ctypes.byref(old))
            if not changed:
                changed = k32.VirtualProtectEx(self.handle, ctypes.c_void_p(page_start), protect_size, PAGE_EXECUTE_READWRITE, ctypes.byref(old))
            if changed:
                got = ctypes.c_size_t(0)
                ok = k32.WriteProcessMemory(self.handle, ctypes.c_void_p(host_addr), data, len(data), ctypes.byref(got))
                restore_old = wintypes.DWORD(0)
                k32.VirtualProtectEx(self.handle, ctypes.c_void_p(page_start), protect_size, old.value, ctypes.byref(restore_old))
                if ok and got.value == len(data):
                    return
                tried.append(f"0x{host_addr:X}:write_err={ctypes.get_last_error()}")
            else:
                tried.append(f"0x{host_addr:X}:write_err={first_err},protect_err={ctypes.get_last_error()}")
        detail = ", ".join(tried) if tried else "no host candidates"
        raise OSError(f"write failed at guest 0x{guest_va:X} ({detail})")

    def read_u32(self, guest_va: int) -> int:
        return struct.unpack(">I", self.read(guest_va, 4))[0]

    def iter_guest_regions(self):
        if self.membase is None:
            raise RuntimeError("not connected")
        yielded: set[tuple[int, int]] = set()
        lo = self.membase
        hi = self.membase + 0x100000000
        addr = lo
        mbi = MEMORY_BASIC_INFORMATION()
        while addr < hi:
            res = k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not res:
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            readable = mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)) and mbi.Protect != 0
            if readable and base >= lo:
                item = (base - self.membase, size)
                yielded.add(item)
                yield item
            nxt = base + size
            if nxt <= addr:
                break
            addr = nxt

        for linear_base in sorted(self.linear_bases):
            addr = linear_base
            hi = linear_base + 0x100000000
            while addr < hi:
                res = k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
                if not res:
                    break
                base = mbi.BaseAddress or 0
                size = mbi.RegionSize or 0
                readable = mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)) and mbi.Protect != 0
                if readable and base >= linear_base:
                    item = (base - linear_base, size)
                    if item not in yielded:
                        yielded.add(item)
                        yield item
                nxt = base + size
                if nxt <= addr:
                    break
                addr = nxt

    def scan(self, needle: bytes, limit: int = 128) -> list[int]:
        hits: list[int] = []
        chunk = 0x100000
        for gva, size in self.iter_guest_regions():
            off = 0
            while off < size:
                n = min(chunk, size - off)
                host_addr = self._guest_to_readable_host(gva + off, n)
                data = self._raw_read(host_addr, n) if host_addr is not None else None
                if data:
                    start = 0
                    while True:
                        i = data.find(needle, start)
                        if i < 0:
                            break
                        hits.append(gva + off + i)
                        if len(hits) >= limit:
                            return hits
                        start = i + 1
                off += n - (len(needle) - 1 if n == chunk else 0)
        if hits:
            return hits

        for host_base, size in self.iter_readable_host_regions():
            if size > 0x10000000:
                continue
            off = 0
            while off < size:
                n = min(chunk, size - off)
                data = self._raw_read(host_base + off, n)
                if data:
                    start = 0
                    while True:
                        i = data.find(needle, start)
                        if i < 0:
                            break
                        host_hit = host_base + off + i
                        self._remember_linear_base_from_host(host_hit)
                        guest_hit = host_hit & 0xFFFFFFFF
                        if 0x80000000 <= guest_hit < 0xF0000000:
                            hits.append(guest_hit)
                            if len(hits) >= limit:
                                return hits
                        start = i + 1
                off += n - (len(needle) - 1 if n == chunk else 0)
        return hits

    def close(self) -> None:
        if self.handle:
            k32.CloseHandle(self.handle)
            self.handle = None


def ensure_gsc_object_name(bytecode: bytes, name: str) -> bytes:
    if len(bytecode) < 0x3F or bytecode[:4] != GSC_MAGIC:
        return bytecode
    buf = bytearray(bytecode)
    want = name.encode("ascii", "replace")
    name_off = int.from_bytes(buf[GSC_OBJ_NAME_FIELD_OFFSET:GSC_OBJ_NAME_FIELD_OFFSET + 2], "big")
    current = b""
    if 0 < name_off < len(buf):
        end = buf.find(b"\x00", name_off)
        if end >= 0:
            current = bytes(buf[name_off:end])
    if current != want:
        if len(buf) > 0xFFFF:
            return bytecode
        struct.pack_into(">H", buf, GSC_OBJ_NAME_FIELD_OFFSET, len(buf))
        buf += want + b"\x00"
    final_size = len(buf)
    struct.pack_into(">I", buf, GSC_OBJ_SIZE_FIELD_OFFSET, final_size)
    struct.pack_into(">I", buf, GSC_OBJ_SIZE_FIELD_OFFSET + 4, final_size)
    return bytes(buf)


def object_size_from_blob(bytecode: bytes) -> int:
    if len(bytecode) < 0x2C or bytecode[:4] != GSC_MAGIC:
        return len(bytecode)
    size = int.from_bytes(bytecode[GSC_OBJ_SIZE_FIELD_OFFSET:GSC_OBJ_SIZE_FIELD_OFFSET + 4], "big")
    if 0x100 <= size <= len(bytecode):
        return size
    return len(bytecode)


def run_gsc_tool_compile(source: Path, output_name: str) -> bytes:
    tool = app_dir() / "tools" / "gsc-tool" / "gsc-tool.exe"
    if not tool.exists():
        raise FileNotFoundError(f"Missing gsc-tool: {tool}")
    with tempfile.TemporaryDirectory(prefix="bo2_gsc_") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / output_name.replace("\\", "/").rsplit("/", 1)[-1]
        shutil.copyfile(source, src)
        cmd = [str(tool), "-m", "comp", "-g", "t6", "-s", "xb2", "-i", "server", str(src)]
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, creationflags=0x08000000, startupinfo=startup)
        produced = tmp_path / "compiled" / "t6" / src.name
        if proc.returncode != 0 or not produced.exists():
            log = (proc.stdout or "") + "\n" + (proc.stderr or "")
            raise RuntimeError(log.strip() or f"gsc-tool failed with exit {proc.returncode}")
        return ensure_gsc_object_name(produced.read_bytes(), output_name)


SCRIPT_TARGETS = {
    ("ZM", "_callbacksetup.gsc"): "maps/mp/gametypes_zm/_callbacksetup.gsc",
    ("MP", "_callbacksetup.gsc"): "maps/mp/gametypes/_callbacksetup.gsc",
    ("MP", "_objpoints.gsc"): "maps/mp/gametypes/_objpoints.gsc",
}


def script_choices_for_game_type(game_type: str) -> list[str]:
    gt = game_type.upper()
    return [script for (mode, script), _target in SCRIPT_TARGETS.items() if mode == gt]


def target_for_script(game_type: str, script_name: str | None = None) -> str:
    gt = game_type.upper()
    script = script_name or "_callbacksetup.gsc"
    try:
        return SCRIPT_TARGETS[(gt, script)]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported target: {gt} {script}") from exc


def patch_callbacksetup_template(game_type: str, user_code: str, entry_function: str) -> tuple[Path, str]:
    gt = game_type.lower()
    target = target_for_script(game_type, "_callbacksetup.gsc")
    template = app_dir() / "templates" / gt / "_callbacksetup.gsc"
    text = template.read_text(encoding="utf-8", errors="replace")
    old = (
        "codecallback_startgametype()\n"
        "{\n"
        "    if ( !isdefined( level.gametypestarted ) || !level.gametypestarted )\n"
        "    {\n"
        "        [[ level.callbackstartgametype ]]();\n"
        "        level.gametypestarted = 1;\n"
        "    }\n"
        "}\n"
    )
    new = (
        "codecallback_startgametype()\n"
        "{\n"
        "    if ( !isdefined( level.codex_injector_started ) )\n"
        "    {\n"
        "        level.codex_injector_started = 1;\n"
        f"        level thread {entry_function}();\n"
        "    }\n\n"
        "    if ( !isdefined( level.gametypestarted ) || !level.gametypestarted )\n"
        "    {\n"
        "        [[ level.callbackstartgametype ]]();\n"
        "        level.gametypestarted = 1;\n"
        "    }\n"
        "}\n\n"
        f"{user_code.rstrip()}\n"
    )
    if old not in text:
        raise RuntimeError("Template patch point not found.")
    out_dir = user_dir() / "build"
    out_dir.mkdir(parents=True, exist_ok=True)
    source = out_dir / f"_callbacksetup_{gt}_patched.gsc"
    source.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    return source, target


def patch_objpoints_template(user_code: str, entry_function: str) -> tuple[Path, str]:
    target = target_for_script("MP", "_objpoints.gsc")
    template = app_dir() / "templates" / "mp" / "_objpoints.gsc"
    text = template.read_text(encoding="utf-8", errors="replace")
    marker = "    // CODEX_OBJPOINTS_LAUNCHER"
    launcher = (
        "    if ( !isdefined( level.codex_injector_started ) )\n"
        "    {\n"
        "        level.codex_injector_started = 1;\n"
        f"        level thread {entry_function}();\n"
        "    }"
    )
    if marker not in text:
        raise RuntimeError("Objpoints template patch point not found.")
    out_dir = user_dir() / "build"
    out_dir.mkdir(parents=True, exist_ok=True)
    source = out_dir / "_objpoints_mp_patched.gsc"
    source.write_text(text.replace(marker, launcher).rstrip() + "\n\n" + user_code.rstrip() + "\n", encoding="utf-8", newline="\n")
    return source, target


def patch_template_for_target(game_type: str, script_name: str, user_code: str, entry_function: str) -> tuple[Path, str]:
    if script_name == "_callbacksetup.gsc":
        return patch_callbacksetup_template(game_type, user_code, entry_function)
    if game_type.upper() == "MP" and script_name == "_objpoints.gsc":
        return patch_objpoints_template(user_code, entry_function)
    raise RuntimeError(f"Unsupported target: {game_type} {script_name}")


def patch_template(game_type: str, user_code: str, entry_function: str) -> tuple[Path, str]:
    return patch_callbacksetup_template(game_type, user_code, entry_function)


def load_gsc_database(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data.get("Files", [])


def default_gsc_db_path_for_target(target_name: str) -> Path:
    stem = "xbox-gsc-dump-zm.json" if "/gametypes_zm/" in target_name.replace("\\", "/").lower() else "xbox-gsc-dump-mp.json"
    return app_dir() / stem


def find_gsc_database_item(target_name: str) -> dict | None:
    path = default_gsc_db_path_for_target(target_name)
    if not path.exists():
        return None
    want = target_name.replace("\\", "/").lower()
    for item in load_gsc_database(path):
        if item.get("Name", "").replace("\\", "/").lower() == want:
            return item
    return None


def object_name_from_header(mem: GuestMemory, header_va: int) -> str:
    head = mem.read(header_va, 0x80)
    if head[:4] != GSC_MAGIC:
        return ""
    name_off = int.from_bytes(head[GSC_OBJ_NAME_FIELD_OFFSET:GSC_OBJ_NAME_FIELD_OFFSET + 2], "big")
    if not (0 < name_off < 0xFFFF):
        return ""
    data = mem.read(header_va + name_off, 256)
    end = data.find(b"\x00")
    if end < 0:
        return ""
    return data[:end].decode("ascii", "replace")


def object_size_from_header(mem: GuestMemory, header_va: int) -> int:
    head = mem.read(header_va, 0x40)
    if head[:4] != GSC_MAGIC:
        raise RuntimeError(f"0x{header_va:X} is not a GSC object")
    size = int.from_bytes(head[GSC_OBJ_SIZE_FIELD_OFFSET:GSC_OBJ_SIZE_FIELD_OFFSET + 4], "big")
    if not (0x100 <= size <= 0x40000):
        raise RuntimeError(f"Implausible GSC object size 0x{size:X} at 0x{header_va:X}")
    return size


def normalize_gsc_name(name: str) -> str:
    return name.replace("\\", "/").lower().removesuffix(".gsc")


def gsc_name_matches(found_name: str, target_name: str) -> bool:
    found = normalize_gsc_name(found_name)
    target = normalize_gsc_name(target_name)
    target_base = target.rsplit("/", 1)[-1]
    if found == target or found.endswith("/" + target):
        return True
    if "/" not in found:
        return found == target_base
    return False


def iter_gsc_magic_hits(mem: GuestMemory, limit: int = 4096):
    chunk = 0x100000
    overlap = len(GSC_MAGIC) - 1
    seen: set[int] = set()
    for gva, size in mem.iter_guest_regions():
        if gva >= 0xF0000000:
            continue
        off = 0
        while off < size:
            n = min(chunk, size - off)
            host_addr = mem._guest_to_readable_host(gva + off, n)
            data = mem._raw_read(host_addr, n) if host_addr is not None else None
            if data:
                start = 0
                while True:
                    idx = data.find(GSC_MAGIC, start)
                    if idx < 0:
                        break
                    hit = gva + off + idx
                    if hit not in seen:
                        seen.add(hit)
                        yield hit
                        if len(seen) >= limit:
                            return
                    start = idx + 1
            off += n - (overlap if n == chunk else 0)


def is_plausible_gsc_object(mem: GuestMemory, header_va: int) -> bool:
    try:
        if mem.read(header_va, 4) != GSC_MAGIC:
            return False
        object_size_from_header(mem, header_va)
        return bool(object_name_from_header(mem, header_va))
    except (OSError, RuntimeError):
        return False


def find_live_gsc_object(mem: GuestMemory, target_name: str) -> tuple[int, int]:
    name_needles = {
        target_name,
        target_name.replace("/", "\\"),
        target_name.removesuffix(".gsc"),
        target_name.replace("/", "\\").removesuffix(".gsc"),
        target_name.rsplit("/", 1)[-1],
        target_name.rsplit("/", 1)[-1].removesuffix(".gsc"),
    }
    headers: set[int] = set()
    for needle in sorted(name_needles, key=len, reverse=True):
        for hit in mem.scan(needle.encode("ascii"), limit=512):
            if hit >= 0xF0000000:
                continue
            lo = max(0, hit - 0x10000)
            try:
                data = mem.read(lo, hit - lo)
            except OSError:
                continue
            idx = data.rfind(GSC_MAGIC)
            if idx >= 0:
                header = lo + idx
                if is_plausible_gsc_object(mem, header):
                    headers.add(header)

    if not headers:
        for header in iter_gsc_magic_hits(mem):
            if is_plausible_gsc_object(mem, header):
                headers.add(header)

    matches = sorted(h for h in headers if gsc_name_matches(object_name_from_header(mem, h), target_name))
    if not matches:
        seen = [(hex(h), object_name_from_header(mem, h)) for h in sorted(headers)[:32]]
        raise RuntimeError(f"Could not find loaded {target_name}. Found: {seen}")
    obj_va = matches[0]
    return obj_va, object_size_from_header(mem, obj_va)


def gsc_object_aliases(obj_va: int) -> list[int]:
    aliases = [obj_va]
    for delta in (-0x20000000, 0x20000000):
        alias = obj_va + delta
        if 0x80000000 <= alias < 0xF0000000 and alias not in aliases:
            aliases.append(alias)
    return aliases


def find_table_candidates_for_object(mem: GuestMemory, obj_va: int, obj_size: int, source: str) -> list[dict]:
    refs = [r for r in mem.scan(struct.pack(">I", obj_va), limit=128) if r < 0x90000000 and (r & 3) == 0]
    candidates: list[dict] = []
    for ref in refs:
        try:
            size = mem.read_u32(ref - 4)
            name_ptr = mem.read_u32(ref - 8)
        except OSError:
            continue
        if size == obj_size and 0x80000000 <= name_ptr < 0xF0000000:
            candidates.append(
                {
                    "entry_va": ref - 8,
                    "name_ptr_va": ref - 8,
                    "size_va": ref - 4,
                    "buffer_va": ref,
                    "name_ptr": name_ptr,
                    "object_va": obj_va,
                    "object_size": obj_size,
                    "source": source,
                }
            )
    return candidates


def validate_cached_gsc_entry(mem: GuestMemory, target_name: str, cached: dict) -> dict | None:
    try:
        linear_bases = cached.get("linear_bases", [])
        if isinstance(linear_bases, list):
            for base in linear_bases:
                mem.linear_bases.add(int(str(base), 16))
        obj_va = int(str(cached["object_va"]), 16)
        obj_size = int(str(cached["object_size"]), 16)
        entry_va = int(str(cached["entry_va"]), 16)
        size_va = int(str(cached["size_va"]), 16)
        buffer_va = int(str(cached["buffer_va"]), 16)
        name_ptr_va = int(str(cached.get("name_ptr_va", entry_va)), 16)
        if mem.read(obj_va, 4) != GSC_MAGIC:
            return None
        if object_size_from_header(mem, obj_va) != obj_size:
            return None
        if not gsc_name_matches(object_name_from_header(mem, obj_va), target_name):
            return None
        if mem.read_u32(size_va) != obj_size:
            return None
        if mem.read_u32(buffer_va) != obj_va:
            return None
        name_ptr = mem.read_u32(name_ptr_va)
        return {
            "entry_va": entry_va,
            "name_ptr_va": name_ptr_va,
            "size_va": size_va,
            "buffer_va": buffer_va,
            "name_ptr": name_ptr,
            "object_va": obj_va,
            "object_size": obj_size,
            "source": "cache",
        }
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        return None


def get_cached_gsc_entry(mem: GuestMemory, target_name: str) -> dict | None:
    cache = load_address_cache()
    cached = cache.get("entries", {}).get(mem.cache_key(target_name))
    if not isinstance(cached, dict):
        return None
    return validate_cached_gsc_entry(mem, target_name, cached)


def remember_gsc_entry(mem: GuestMemory, target_name: str, entry: dict) -> None:
    if entry.get("source") == "cache":
        return
    cache = load_address_cache()
    entries = cache.setdefault("entries", {})
    entries[mem.cache_key(target_name)] = {
        "target_gsc": target_name,
        "exe_name": mem.exe_name,
        "membase": f"0x{mem.membase:X}" if mem.membase is not None else None,
        "linear_bases": [f"0x{x:X}" for x in sorted(mem.linear_bases)],
        "entry_va": f"0x{entry['entry_va']:X}",
        "name_ptr_va": f"0x{entry['name_ptr_va']:X}",
        "size_va": f"0x{entry['size_va']:X}",
        "buffer_va": f"0x{entry['buffer_va']:X}",
        "object_va": f"0x{entry['object_va']:X}",
        "object_size": f"0x{entry['object_size']:X}",
    }
    save_address_cache(cache)


def find_live_gsc_entry(mem: GuestMemory, target_name: str) -> dict:
    cached = get_cached_gsc_entry(mem, target_name)
    if cached is not None:
        return cached

    try:
        obj_va, obj_size = find_live_gsc_object(mem, target_name)
    except RuntimeError as scan_error:
        if not mem.scan(GSC_MAGIC, limit=1):
            raise RuntimeError(
                f"No loaded T6 GSC objects were found in the running Xenia guest memory. "
                f"{target_name} is probably not mapped yet. With the default/retail XEX, "
                "load the matching mode or map first, then press Detect/Inject again."
            ) from scan_error
        item = find_gsc_database_item(target_name)
        if item is None:
            raise
        pointer_va = int(item["Pointer"])
        db_size = int(item["Size"])
        try:
            obj_va = mem.read_u32(pointer_va)
            if not (0x80000000 <= obj_va < 0xF0000000):
                obj_va = int(item["Buffer"])
            if mem.read(obj_va, 4) != GSC_MAGIC:
                raise RuntimeError(f"database buffer 0x{obj_va:X} is not a loaded GSC object")
            obj_size = object_size_from_header(mem, obj_va)
            if obj_size <= 0:
                obj_size = db_size
        except (OSError, RuntimeError) as db_error:
            raise RuntimeError(f"{scan_error} Database fallback also failed: {db_error}") from db_error
        entry = {
            "entry_va": pointer_va - 8,
            "name_ptr_va": pointer_va - 8,
            "size_va": pointer_va - 4,
            "buffer_va": pointer_va,
            "name_ptr": 0,
            "object_va": obj_va,
            "object_size": obj_size,
            "source": "database",
        }
        remember_gsc_entry(mem, target_name, entry)
        return entry
    candidates: list[dict] = []
    tried_objects: list[int] = []
    for alias_va in gsc_object_aliases(obj_va):
        tried_objects.append(alias_va)
        candidates.extend(find_table_candidates_for_object(mem, alias_va, obj_size, "scan"))
    if not candidates:
        tried = ", ".join(f"0x{x:X}" for x in tried_objects)
        raise RuntimeError(f"Found {target_name} object at 0x{obj_va:X}, but no live table entry references it. Tried aliases: {tried}.")
    candidates.sort(key=lambda c: (0 if 0x83000000 <= c["entry_va"] < 0x85000000 else 1, c["entry_va"]))
    entry = candidates[0]
    remember_gsc_entry(mem, target_name, entry)
    return entry


DEFAULT_CODE = """codex_main()
{
    level thread codex_on_connect();
}

codex_on_connect()
{
    for (;;)
    {
        level waittill( "connecting", player );
        player thread codex_on_spawn();
    }
}

codex_on_spawn()
{
    self endon( "disconnect" );

    for (;;)
    {
        self waittill( "spawned_player" );
        wait 3;

        self.codex_menu_open = false;
        self.codex_menu_cursor = 0;
        self.codex_godmode = false;
        self.codex_infinite_ammo = false;

        self iprintlnbold( "^2GSC menu loaded^7 - press Dpad Left" );
        self notify( "codex_menu_restart" );
        self thread codex_menu_watch();
        self thread codex_power_loop();
    }
}

codex_menu_watch()
{
    self endon( "disconnect" );
    self endon( "codex_menu_restart" );

    for (;;)
    {
        if ( self actionslotthreebuttonpressed() )
        {
            self.codex_menu_open = !self.codex_menu_open;
            self codex_draw_menu();

            while ( self actionslotthreebuttonpressed() )
            {
                wait .05;
            }
        }

        if ( self.codex_menu_open )
        {
            if ( self actionslotonebuttonpressed() )
            {
                self.codex_menu_cursor--;
                if ( self.codex_menu_cursor < 0 )
                {
                    self.codex_menu_cursor = 1;
                }
                self codex_draw_menu();
                wait .2;
            }

            if ( self actionslottwobuttonpressed() )
            {
                self.codex_menu_cursor++;
                if ( self.codex_menu_cursor > 1 )
                {
                    self.codex_menu_cursor = 0;
                }
                self codex_draw_menu();
                wait .2;
            }

            if ( self usebuttonpressed() )
            {
                if ( self.codex_menu_cursor == 0 )
                {
                    self.codex_infinite_ammo = !self.codex_infinite_ammo;
                }
                else
                {
                    self.codex_godmode = !self.codex_godmode;
                }

                self codex_draw_menu();
                wait .25;
            }
        }

        wait .05;
    }
}

codex_draw_menu()
{
    if ( !self.codex_menu_open )
    {
        self iprintlnbold( "^1Menu closed" );
        return;
    }

    cursor0 = " ";
    cursor1 = " ";

    if ( self.codex_menu_cursor == 0 )
    {
        cursor0 = ">";
    }
    else
    {
        cursor1 = ">";
    }

    ammo = "^1OFF";
    god = "^1OFF";

    if ( self.codex_infinite_ammo )
    {
        ammo = "^2ON";
    }

    if ( self.codex_godmode )
    {
        god = "^2ON";
    }

    self iprintlnbold( "^5MENU^7 " + cursor0 + "Ammo:" + ammo + " ^7| " + cursor1 + "God:" + god + " ^7| X Toggle" );
}

codex_power_loop()
{
    self endon( "disconnect" );
    self endon( "codex_menu_restart" );

    for (;;)
    {
        if ( self.codex_godmode )
        {
            self.health = 999999;
            self.maxhealth = 999999;
        }

        if ( self.codex_infinite_ammo )
        {
            weapon = self getcurrentweapon();
            self givemaxammo( weapon );
        }

        wait .2;
    }
}
"""


GSC_KEYWORDS = {
    "break", "case", "continue", "default", "else", "false", "for", "foreach",
    "if", "in", "return", "switch", "true", "undefined", "wait", "while",
}

GSC_BUILTINS = {
    "iprintln", "iprintlnbold", "println", "thread", "endon", "notify", "waittill",
    "isdefined", "getentitynumber", "getent", "getentarray", "spawnstruct",
    "setdvar", "getdvar", "getdvarint", "getdvarfloat", "getdvarvector",
    "precachemodel", "precachestring", "precacheitem", "precachelocationselector",
    "setmodel", "origin", "angles", "health", "maxhealth", "disconnect",
    "spawned_player", "connecting", "level", "self", "player", "players",
    "actionslotonebuttonpressed", "actionslottwobuttonpressed",
    "actionslotthreebuttonpressed", "usebuttonpressed", "getcurrentweapon",
    "givemaxammo",
}

GSC_SNIPPETS = {
    "loop_spawn_message": (
        "codex_main()\n"
        "{\n"
        "    for (;;)\n"
        "    {\n"
        "        wait 3;\n"
        "        iprintlnbold( \"Hello from GSC\" );\n"
        "    }\n"
        "}\n"
    ),
    "on_player_connect": (
        "on_player_connect()\n"
        "{\n"
        "    for (;;)\n"
        "    {\n"
        "        level waittill( \"connecting\", player );\n"
        "        player thread on_player_spawned();\n"
        "    }\n"
        "}\n"
    ),
    "on_player_spawned": (
        "on_player_spawned()\n"
        "{\n"
        "    self endon( \"disconnect\" );\n\n"
        "    for (;;)\n"
        "    {\n"
        "        self waittill( \"spawned_player\" );\n"
        "        wait 3;\n"
        "    }\n"
        "}\n"
    ),
    "crude_mod_menu": DEFAULT_CODE,
}


class GscEditor:
    def __init__(self, master) -> None:
        self.frame = Frame(master, bg="#151515")
        self.gutter = Canvas(self.frame, width=54, highlightthickness=0, bg="#202225")
        self.text = Text(
            self.frame,
            undo=True,
            wrap="none",
            font=("Consolas", 11),
            bg="#151515",
            fg="#D8DEE9",
            insertbackground="#FFFFFF",
            selectbackground="#365C7D",
            borderwidth=0,
            padx=10,
            pady=8,
        )
        self.scroll_y = Scrollbar(self.frame, orient="vertical", command=self._yview)
        self.scroll_x = Scrollbar(self.frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=self._on_yscroll, xscrollcommand=self.scroll_x.set)

        self.gutter.pack(side=LEFT, fill="y")
        self.scroll_y.pack(side=RIGHT, fill="y")
        self.scroll_x.pack(side="bottom", fill=X)
        self.text.pack(side=LEFT, fill=BOTH, expand=True)

        self.popup: Toplevel | None = None
        self.popup_list: Listbox | None = None
        self.highlight_job: str | None = None
        self.completions = sorted(GSC_KEYWORDS | GSC_BUILTINS | set(GSC_SNIPPETS))
        self._configure_tags()
        self._bind_events()

    def pack(self, *args, **kwargs) -> None:
        self.frame.pack(*args, **kwargs)

    def get(self, *args, **kwargs) -> str:
        return self.text.get(*args, **kwargs)

    def insert(self, *args, **kwargs) -> None:
        self.text.insert(*args, **kwargs)
        self.schedule_highlight()

    def _configure_tags(self) -> None:
        self.text.tag_configure("current_line", background="#1C2430")
        self.text.tag_configure("keyword", foreground="#C792EA")
        self.text.tag_configure("builtin", foreground="#82AAFF")
        self.text.tag_configure("string", foreground="#C3E88D")
        self.text.tag_configure("comment", foreground="#697098")
        self.text.tag_configure("number", foreground="#F78C6C")
        self.text.tag_configure("function", foreground="#FFCB6B")
        self.text.tag_configure("brace", foreground="#89DDFF")

    def _bind_events(self) -> None:
        self.text.bind("<KeyRelease>", self._on_key_release)
        self.text.bind("<ButtonRelease-1>", lambda _e: self.refresh())
        self.text.bind("<MouseWheel>", lambda _e: self.frame.after_idle(self.refresh))
        self.text.bind("<Return>", self._smart_return)
        self.text.bind("<Tab>", self._tab_or_complete)
        self.text.bind("<Control-space>", lambda _e: (self.show_completions(force=True), "break")[1])
        self.text.bind("<Escape>", lambda _e: (self.hide_popup(), None)[1])
        self.text.bind("<Configure>", lambda _e: self.refresh())

    def _on_yscroll(self, first: str, last: str) -> None:
        self.scroll_y.set(first, last)
        self.draw_line_numbers()

    def _yview(self, *args) -> None:
        self.text.yview(*args)
        self.draw_line_numbers()

    def _on_key_release(self, event) -> None:
        if event.keysym in {"Up", "Down", "Left", "Right", "Return", "Tab", "Escape"}:
            self.refresh()
            return
        self.schedule_highlight()
        self.update_current_line()
        self.show_completions()

    def _smart_return(self, _event):
        line = self.text.get("insert linestart", "insert")
        indent = re.match(r"\s*", line).group(0)
        extra = "    " if line.rstrip().endswith("{") else ""
        self.text.insert(INSERT, "\n" + indent + extra)
        self.schedule_highlight()
        return "break"

    def _tab_or_complete(self, _event):
        if self.popup and self.popup.winfo_exists():
            self.apply_completion()
            return "break"
        self.text.insert(INSERT, "    ")
        self.schedule_highlight()
        return "break"

    def refresh(self) -> None:
        self.draw_line_numbers()
        self.update_current_line()

    def schedule_highlight(self) -> None:
        if self.highlight_job:
            self.text.after_cancel(self.highlight_job)
        self.highlight_job = self.text.after(120, self.highlight)
        self.refresh()

    def update_current_line(self) -> None:
        self.text.tag_remove("current_line", "1.0", END)
        self.text.tag_add("current_line", "insert linestart", "insert lineend+1c")
        self.text.tag_lower("current_line")

    def draw_line_numbers(self) -> None:
        self.gutter.delete("all")
        index = self.text.index("@0,0")
        while True:
            dline = self.text.dlineinfo(index)
            if dline is None:
                break
            y = dline[1]
            line = index.split(".")[0]
            self.gutter.create_text(44, y + 8, anchor="e", text=line, fill="#858B98", font=("Consolas", 10))
            index = self.text.index(f"{index}+1line")

    def highlight(self) -> None:
        self.highlight_job = None
        content = self.text.get("1.0", "end-1c")
        for tag in ("keyword", "builtin", "string", "comment", "number", "function", "brace"):
            self.text.tag_remove(tag, "1.0", END)

        self._highlight_regex(content, r'"(?:\\.|[^"\\])*"', "string")
        self._highlight_regex(content, r"//.*", "comment")
        self._highlight_regex(content, r"/#.*?#/", "comment", flags=re.DOTALL)
        self._highlight_regex(content, r"\b(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)\b", "number")
        self._highlight_regex(content, r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=\()", "function", group=1)
        self._highlight_regex(content, r"[{}\[\]();]", "brace")

        words = sorted(GSC_KEYWORDS | GSC_BUILTINS, key=len, reverse=True)
        self._highlight_regex(content, r"\b(" + "|".join(map(re.escape, words)) + r")\b", "keyword_or_builtin", group=1)
        self.text.tag_remove("keyword_or_builtin", "1.0", END)

        for word in GSC_KEYWORDS:
            self._highlight_word(word, "keyword")
        for word in GSC_BUILTINS:
            self._highlight_word(word, "builtin")
        self.refresh()

    def _offset_to_index(self, offset: int) -> str:
        return f"1.0+{offset}c"

    def _highlight_regex(self, content: str, pattern: str, tag: str, group: int = 0, flags: int = 0) -> None:
        if tag == "keyword_or_builtin":
            return
        for match in re.finditer(pattern, content, flags):
            start, end = match.span(group)
            self.text.tag_add(tag, self._offset_to_index(start), self._offset_to_index(end))

    def _highlight_word(self, word: str, tag: str) -> None:
        start = "1.0"
        while True:
            idx = self.text.search(rf"\m{word}\M", start, END, regexp=True)
            if not idx:
                break
            end = f"{idx}+{len(word)}c"
            self.text.tag_add(tag, idx, end)
            start = end

    def current_prefix(self) -> tuple[str, str]:
        before = self.text.get("insert linestart", INSERT)
        match = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", before)
        if not match:
            return "", INSERT
        start = f"insert-{len(match.group(0))}c"
        return match.group(0), start

    def show_completions(self, force: bool = False) -> None:
        prefix, _start = self.current_prefix()
        if not force and len(prefix) < 2:
            self.hide_popup()
            return
        matches = [item for item in self.completions if item.lower().startswith(prefix.lower())]
        if not matches:
            self.hide_popup()
            return
        if self.popup is None or not self.popup.winfo_exists():
            self.popup = Toplevel(self.text)
            self.popup.wm_overrideredirect(True)
            self.popup.configure(bg="#2B303B")
            self.popup_list = Listbox(
                self.popup,
                height=min(9, len(matches)),
                bg="#2B303B",
                fg="#D8DEE9",
                selectbackground="#365C7D",
                activestyle="none",
                font=("Consolas", 10),
            )
            self.popup_list.pack(fill=BOTH, expand=True)
            self.popup_list.bind("<Double-Button-1>", lambda _e: self.apply_completion())
            self.popup_list.bind("<Return>", lambda _e: self.apply_completion())
        self.popup_list.delete(0, END)
        for item in matches[:12]:
            self.popup_list.insert(END, item)
        self.popup_list.selection_set(0)
        bbox = self.text.bbox(INSERT)
        if bbox:
            x = self.text.winfo_rootx() + bbox[0]
            y = self.text.winfo_rooty() + bbox[1] + bbox[3] + 2
            self.popup.geometry(f"+{x}+{y}")

    def apply_completion(self) -> None:
        if not self.popup_list:
            return
        selection = self.popup_list.curselection()
        if not selection:
            return
        value = self.popup_list.get(selection[0])
        prefix, start = self.current_prefix()
        self.text.delete(start, INSERT)
        snippet = GSC_SNIPPETS.get(value)
        self.text.insert(INSERT, snippet if snippet else value)
        self.hide_popup()
        self.schedule_highlight()

    def hide_popup(self) -> None:
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()
        self.popup = None
        self.popup_list = None


class InjectorApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title(APP_NAME)
        self.root.geometry("980x720")
        self.game_type = StringVar(value="ZM")
        self.status = StringVar(value="Ready. Launch Xenia and stay in the menu before injecting.")
        self.entry_function = StringVar(value="codex_main")
        self.restore_cfg: Path | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=TOP, fill=X)
        Label(top, text="Target").pack(side=LEFT)
        ttk.Combobox(top, textvariable=self.game_type, values=["ZM", "MP"], width=6, state="readonly").pack(side=LEFT, padx=6)
        Label(top, text="Entry").pack(side=LEFT, padx=(14, 0))
        ttk.Entry(top, textvariable=self.entry_function, width=22).pack(side=LEFT, padx=6)
        Button(top, text="Detect Xenia", command=self.detect).pack(side=LEFT, padx=4)
        Button(top, text="Compile + Inject", command=self.inject).pack(side=LEFT, padx=4)
        Button(top, text="Restore", command=self.restore).pack(side=LEFT, padx=4)
        Button(top, text="Open Build Folder", command=self.open_build_folder).pack(side=RIGHT, padx=4)

        Label(self.root, textvariable=self.status, anchor="w").pack(fill=X, padx=8)
        self.editor = GscEditor(self.root)
        self.editor.pack(fill=BOTH, expand=True, padx=8, pady=(4, 6))
        self.editor.insert("1.0", DEFAULT_CODE)

        self.log = scrolledtext.ScrolledText(self.root, height=10, state="disabled", font=("Consolas", 9))
        self.log.pack(fill=BOTH, padx=8, pady=(0, 8))
        self.write_log("Tip: inject from the main menu, then load/restart the map.")

    def write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(END, text.rstrip() + "\n")
        self.log.see(END)
        self.log.configure(state="disabled")
        self.status.set(text.splitlines()[-1] if text else "")

    def run_worker(self, fn) -> None:
        def wrap():
            try:
                fn()
            except Exception as exc:
                self.root.after(0, lambda: (self.write_log(f"ERROR: {exc}"), messagebox.showerror(APP_NAME, str(exc))))
        threading.Thread(target=wrap, daemon=True).start()

    def detect(self) -> None:
        self.run_worker(self._detect)

    def _detect(self) -> None:
        mem = GuestMemory()
        try:
            info = mem.open()
            target = "maps/mp/gametypes_zm/_callbacksetup.gsc" if self.game_type.get() == "ZM" else "maps/mp/gametypes/_callbacksetup.gsc"
            obj, size = find_live_gsc_object(mem, target)
            self.root.after(0, lambda: self.write_log(f"{info}\nFound {target}: object=0x{obj:X}, span=0x{size:X}"))
        finally:
            mem.close()

    def inject(self) -> None:
        self.run_worker(self._inject)

    def _inject(self) -> None:
        code = self.editor.get("1.0", END).strip()
        if not code:
            raise RuntimeError("Editor is empty.")
        source, target = patch_template(self.game_type.get(), code, self.entry_function.get().strip() or "codex_main")
        self.root.after(0, lambda: self.write_log(f"Compiling {target}..."))
        blob = run_gsc_tool_compile(source, target)
        compiled_path = user_dir() / "build" / f"{self.game_type.get().lower()}_callbacksetup_injected.gsc"
        compiled_path.write_bytes(blob)

        mem = GuestMemory()
        try:
            info = mem.open()
            obj_va, obj_span = find_live_gsc_object(mem, target)
            if len(blob) > obj_span:
                raise RuntimeError(f"Compiled blob too large for live object: 0x{len(blob):X} > 0x{obj_span:X}")
            backup = mem.read(obj_va, obj_span)
            backup_path = user_dir() / "build" / f"backup_{self.game_type.get().lower()}_{obj_va:X}.bin"
            backup_path.write_bytes(backup)
            mem.write(obj_va, blob + (b"\x00" * (obj_span - len(blob))))
            cfg = {
                "target_gsc": target,
                "object_va": f"0x{obj_va:X}",
                "object_size": f"0x{obj_span:X}",
                "backup_file": str(backup_path),
                "compiled_file": str(compiled_path),
                "script_len": f"0x{len(blob):X}",
            }
            cfg_path = user_dir() / "last_injection.json"
            cfg_path.write_text(json.dumps(cfg, indent=2))
            self.restore_cfg = cfg_path
            self.root.after(0, lambda: self.write_log(
                f"{info}\nInjected {target}\nobject=0x{obj_va:X}, size=0x{obj_span:X}, blob=0x{len(blob):X}\nNow load/restart the map."
            ))
        finally:
            mem.close()

    def restore(self) -> None:
        self.run_worker(self._restore)

    def _restore(self) -> None:
        cfg_path = self.restore_cfg or user_dir() / "last_injection.json"
        if not cfg_path.exists():
            cfg_path = Path(filedialog.askopenfilename(title="Select last_injection.json", filetypes=[("JSON", "*.json")]))
        if not cfg_path.exists():
            return
        cfg = json.loads(cfg_path.read_text())
        backup = Path(cfg["backup_file"]).read_bytes()
        mem = GuestMemory()
        try:
            info = mem.open()
            mem.write(int(cfg["object_va"], 16), backup)
            self.root.after(0, lambda: self.write_log(f"{info}\nRestored {cfg['target_gsc']} at {cfg['object_va']}."))
        finally:
            mem.close()

    def open_build_folder(self) -> None:
        os.startfile(user_dir() / "build")

    def mainloop(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    InjectorApp().mainloop()
