from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from ctypes import wintypes
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, TOP, X, Button, Label, StringVar, Tk, filedialog, messagebox, scrolledtext, ttk


APP_NAME = "BO2 GSC Live Injector"
GUEST_IMAGE_BASE = 0x82000000
SIG_AT_0 = bytes.fromhex("4d5a9000")
SIG_AT_100 = bytes.fromhex("50450000f2011300")
GSC_OBJ_NAME_FIELD_OFFSET = 0x30
GSC_MAGIC = b"\x80GSC"

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
PAGE_READWRITE = 0x04
TH32CS_SNAPPROCESS = 0x2

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

    def open(self) -> str:
        matches = [(pid, exe) for pid, exe in list_processes() if "xenia" in exe.lower()]
        if not matches:
            raise RuntimeError("No Xenia process found.")
        self.pid, self.exe_name = matches[0]
        access = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION
        self.handle = k32.OpenProcess(access, False, self.pid)
        if not self.handle:
            raise OSError(f"OpenProcess({self.pid}) failed. Run as Administrator.")
        self.membase = self._find_membase()
        if self.membase is None:
            raise RuntimeError("Xenia found, but CoD/guest memory is not mapped yet.")
        return f"{self.exe_name} pid={self.pid}, guest membase=0x{self.membase:X}"

    def _raw_read(self, host_addr: int, size: int) -> bytes | None:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(self.handle, ctypes.c_void_p(host_addr), buf, size, ctypes.byref(got))
        if not ok or got.value != size:
            return None
        return buf.raw

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
            if head and head[:4] == SIG_AT_0 and head[0x100:0x108] == SIG_AT_100:
                return base - GUEST_IMAGE_BASE
        for base, size in regions:
            if size < 0x8000000:
                continue
            for guest_start in (0, 0x80000000):
                cand = base - guest_start
                probe = self._raw_read(cand + GUEST_IMAGE_BASE, 0x108)
                if probe and probe[:4] == SIG_AT_0 and probe[0x100:0x108] == SIG_AT_100:
                    return cand
        return None

    def read(self, guest_va: int, size: int) -> bytes:
        if self.membase is None:
            raise RuntimeError("not connected")
        data = self._raw_read(self.membase + guest_va, size)
        if data is None:
            raise OSError(f"read failed at guest 0x{guest_va:X}")
        return data

    def write(self, guest_va: int, data: bytes) -> None:
        if self.membase is None:
            raise RuntimeError("not connected")
        host_addr = self.membase + guest_va
        got = ctypes.c_size_t(0)
        ok = k32.WriteProcessMemory(self.handle, ctypes.c_void_p(host_addr), data, len(data), ctypes.byref(got))
        if ok and got.value == len(data):
            return
        old = wintypes.DWORD(0)
        page_start = host_addr & ~0xFFF
        protect_size = ((host_addr + len(data) + 0xFFF) & ~0xFFF) - page_start
        changed = k32.VirtualProtectEx(self.handle, ctypes.c_void_p(page_start), protect_size, PAGE_READWRITE, ctypes.byref(old))
        if changed:
            got = ctypes.c_size_t(0)
            ok = k32.WriteProcessMemory(self.handle, ctypes.c_void_p(host_addr), data, len(data), ctypes.byref(got))
            restore_old = wintypes.DWORD(0)
            k32.VirtualProtectEx(self.handle, ctypes.c_void_p(page_start), protect_size, old.value, ctypes.byref(restore_old))
            if ok and got.value == len(data):
                return
        raise OSError(f"write failed at guest 0x{guest_va:X} (err {ctypes.get_last_error()})")

    def read_u32(self, guest_va: int) -> int:
        return struct.unpack(">I", self.read(guest_va, 4))[0]

    def iter_guest_regions(self):
        if self.membase is None:
            raise RuntimeError("not connected")
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
                yield base - self.membase, size
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
                data = self._raw_read(self.membase + gva + off, n)
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
    if current == want:
        return bytecode
    if len(buf) > 0xFFFF:
        return bytecode
    struct.pack_into(">H", buf, GSC_OBJ_NAME_FIELD_OFFSET, len(buf))
    buf += want + b"\x00"
    return bytes(buf)


def run_gsc_tool_compile(source: Path, output_name: str) -> bytes:
    tool = app_dir() / "tools" / "gsc-tool" / "gsc-tool.exe"
    if not tool.exists():
        raise FileNotFoundError(f"Missing gsc-tool: {tool}")
    with tempfile.TemporaryDirectory(prefix="bo2_gsc_") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "_callbacksetup.gsc"
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


def patch_template(game_type: str, user_code: str, entry_function: str) -> tuple[Path, str]:
    gt = game_type.lower()
    target = "maps/mp/gametypes_zm/_callbacksetup.gsc" if gt == "zm" else "maps/mp/gametypes/_callbacksetup.gsc"
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


def find_live_gsc_object(mem: GuestMemory, target_name: str) -> tuple[int, int]:
    basename = target_name.rsplit("/", 1)[-1].encode("ascii")
    headers: set[int] = set()
    for hit in mem.scan(basename, limit=128):
        if hit >= 0xF0000000:
            continue
        lo = max(0, hit - 0x8000)
        data = mem.read(lo, hit - lo)
        idx = data.rfind(GSC_MAGIC)
        if idx >= 0:
            headers.add(lo + idx)

    matches = sorted(h for h in headers if object_name_from_header(mem, h) == target_name)
    if not matches:
        seen = [(hex(h), object_name_from_header(mem, h)) for h in sorted(headers)[:16]]
        raise RuntimeError(f"Could not find loaded {target_name}. Found: {seen}")
    obj_va = matches[0]

    # Estimate writable object span from the next GSC header in the same loaded-script area.
    all_headers = sorted(h for h in mem.scan(GSC_MAGIC, limit=512) if obj_va <= h < 0xF0000000)
    next_headers = [h for h in all_headers if h > obj_va]
    size = (next_headers[0] - obj_va) if next_headers else 0x20000
    if size > 0x40000:
        size = 0x40000
    return obj_va, size


DEFAULT_CODE = """codex_main()
{
    for (;;)
    {
        wait 3;
        iprintlnbold( "Hello from BO2 GSC Live Injector" );
    }
}
"""


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
        self.editor = scrolledtext.ScrolledText(self.root, height=24, undo=True, font=("Consolas", 11))
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
