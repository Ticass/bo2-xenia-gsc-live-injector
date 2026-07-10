from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, Signal, QObject
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import app as backend


APP_TITLE = "BO2 Xenia GSC Live Injector"
ACCENT = "#f26b21"
ACCENT_2 = "#ffb15c"
BG = "#0d0f10"
PANEL = "#15181b"
PANEL_2 = "#1d2227"
TEXT = "#dce2ea"
MUTED = "#8b949e"
GREEN = "#52d273"
RED = "#ff5a5f"
class WorkerSignals(QObject):
    log = Signal(str)
    error = Signal(str)
    done = Signal()
    info = Signal(dict)


class LineNumberArea(QWidget):
    def __init__(self, editor: "GscCodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.line_number_area_paint_event(event)


class GscHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []
        self._build_rules()

    def fmt(self, color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        if italic:
            f.setFontItalic(True)
        return f

    def _build_rules(self) -> None:
        keyword_fmt = self.fmt("#c792ea", bold=True)
        builtin_fmt = self.fmt("#82aaff")
        string_fmt = self.fmt("#c3e88d")
        comment_fmt = self.fmt("#697098", italic=True)
        number_fmt = self.fmt("#f78c6c")
        function_fmt = self.fmt("#ffcb6b")
        brace_fmt = self.fmt("#89ddff")

        for word in backend.GSC_KEYWORDS:
            self.rules.append((re.compile(rf"\b{re.escape(word)}\b"), keyword_fmt))
        for word in backend.GSC_BUILTINS:
            self.rules.append((re.compile(rf"\b{re.escape(word)}\b"), builtin_fmt))
        self.rules.extend(
            [
                (re.compile(r'"(?:\\.|[^"\\])*"'), string_fmt),
                (re.compile(r"//.*"), comment_fmt),
                (re.compile(r"\b(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)\b"), number_fmt),
                (re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\s*\()"), function_fmt),
                (re.compile(r"[{}\[\]();]"), brace_fmt),
            ]
        )

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self.rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


class GscCodeEditor(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.line_number_area = LineNumberArea(self)
        self.highlighter = GscHighlighter(self.document())
        self.completer = QCompleter(sorted(backend.GSC_KEYWORDS | backend.GSC_BUILTINS | set(backend.GSC_SNIPPETS)))
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.activated.connect(self.insert_completion)

        self.setFont(QFont("Cascadia Mono", 11))
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setPlainText(backend.DEFAULT_CODE)

        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        self.update_line_number_area_width(0)
        self.highlight_current_line()

    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 18 + self.fontMetrics().horizontalAdvance("9") * digits

    def update_line_number_area_width(self, _count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def line_number_area_paint_event(self, event) -> None:
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#111417"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#6e7681"))
                painter.drawText(0, top, self.line_number_area.width() - 8, self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, number)
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def highlight_current_line(self) -> None:
        extra = []
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#18202a"))
        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        extra.append(selection)
        self.setExtraSelections(extra)

    def completion_prefix(self) -> str:
        cursor = self.textCursor()
        cursor.select(cursor.SelectionType.WordUnderCursor)
        return cursor.selectedText()

    def insert_completion(self, completion: str) -> None:
        cursor = self.textCursor()
        cursor.select(cursor.SelectionType.WordUnderCursor)
        snippet = backend.GSC_SNIPPETS.get(completion)
        cursor.insertText(snippet if snippet else completion)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event) -> None:
        if self.completer.popup().isVisible() and event.key() in (
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Escape,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Backtab,
        ):
            event.ignore()
            return

        if event.key() == Qt.Key.Key_Return:
            cursor = self.textCursor()
            line = cursor.block().text()[: cursor.positionInBlock()]
            indent = re.match(r"\s*", line).group(0)
            extra = "    " if line.rstrip().endswith("{") else ""
            cursor.insertText("\n" + indent + extra)
            return

        if event.key() == Qt.Key.Key_Tab:
            cursor = self.textCursor()
            cursor.insertText("    ")
            return

        super().keyPressEvent(event)

        ctrl_space = event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Space
        prefix = self.completion_prefix()
        if not ctrl_space and len(prefix) < 2:
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix(prefix)
        popup = self.completer.popup()
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        cr = self.cursorRect()
        cr.setWidth(self.completer.popup().sizeHintForColumn(0) + self.completer.popup().verticalScrollBar().sizeHint().width())
        self.completer.complete(cr)


class InjectorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1220, 780)
        self.restore_cfg: Path | None = None
        self.signals = WorkerSignals()
        self.signals.log.connect(self.log)
        self.signals.error.connect(self.show_error)
        self.signals.info.connect(self.update_inspector)
        self._build_ui()
        self.apply_theme()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 10)
        outer.setSpacing(10)

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("BO2 XENIA GSC LIVE INJECTOR")
        title.setObjectName("Title")
        subtitle = QLabel("Runtime Xbox/T6 GSC compiler and live object injector")
        subtitle.setObjectName("Subtitle")
        title_box = QVBoxLayout()
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box)
        header_layout.addStretch()
        self.status_pill = QLabel("IDLE")
        self.status_pill.setObjectName("StatusPill")
        header_layout.addWidget(self.status_pill)
        outer.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        sidebar = self._build_sidebar()
        splitter.addWidget(sidebar)
        splitter.addWidget(self._build_editor_panel())
        splitter.addWidget(self._build_inspector())
        splitter.setSizes([230, 720, 270])
        outer.addWidget(splitter, 1)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(150)
        self.console.setObjectName("Console")
        outer.addWidget(self.console)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.log("Ready. Stay in MP/ZM menu, choose target, then Compile + Inject.")

    def _build_sidebar(self) -> QWidget:
        box = QFrame()
        box.setObjectName("Panel")
        layout = QVBoxLayout(box)
        layout.setSpacing(10)
        layout.addWidget(QLabel("TARGET"))
        self.game_type = QComboBox()
        self.game_type.addItems(["ZM", "MP"])
        layout.addWidget(self.game_type)
        layout.addWidget(QLabel("SCRIPT FILE"))
        self.script_file = QComboBox()
        layout.addWidget(self.script_file)
        self.game_type.currentTextChanged.connect(self.refresh_script_choices)
        self.refresh_script_choices()
        layout.addWidget(QLabel("ENTRY FUNCTION"))
        self.entry_function = QLineEdit("codex_main")
        layout.addWidget(self.entry_function)
        self.detect_btn = QPushButton("Detect Xenia")
        self.probe_btn = QPushButton("Freeze Probe")
        self.inject_btn = QPushButton("Compile + Inject")
        self.restore_btn = QPushButton("Restore Backup")
        self.inject_btn.setObjectName("PrimaryButton")
        for btn in (self.detect_btn, self.probe_btn, self.inject_btn, self.restore_btn):
            btn.setMinimumHeight(40)
            layout.addWidget(btn)
        self.detect_btn.clicked.connect(self.detect)
        self.probe_btn.clicked.connect(self.freeze_probe)
        self.inject_btn.clicked.connect(self.inject)
        self.restore_btn.clicked.connect(self.restore)
        layout.addStretch()
        hint = QLabel("Inject from the menu, then load or restart the map.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return box

    def refresh_script_choices(self) -> None:
        current = self.script_file.currentText() if hasattr(self, "script_file") else ""
        choices = backend.script_choices_for_game_type(self.game_type.currentText())
        self.script_file.blockSignals(True)
        self.script_file.clear()
        self.script_file.addItems(choices)
        if current in choices:
            self.script_file.setCurrentText(current)
        self.script_file.blockSignals(False)

    def current_target(self) -> str:
        return backend.target_for_script(self.game_type.currentText(), self.script_file.currentText())

    def _build_editor_panel(self) -> QWidget:
        box = QFrame()
        box.setObjectName("Panel")
        layout = QVBoxLayout(box)
        bar = QHBoxLayout()
        label = QLabel("GSC EDITOR")
        label.setObjectName("SectionTitle")
        bar.addWidget(label)
        bar.addStretch()
        for name, snippet in backend.GSC_SNIPPETS.items():
            btn = QToolButton()
            btn.setText(name)
            btn.clicked.connect(lambda _checked=False, s=snippet: self.editor.insertPlainText("\n" + s))
            bar.addWidget(btn)
        layout.addLayout(bar)
        self.editor = GscCodeEditor()
        layout.addWidget(self.editor, 1)
        return box

    def _build_inspector(self) -> QWidget:
        box = QFrame()
        box.setObjectName("Panel")
        layout = QVBoxLayout(box)
        title = QLabel("LIVE INSPECTOR")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        self.info_labels: dict[str, QLabel] = {}
        for key in ["Process", "Target", "Mode", "Entry", "Object", "Buffer", "Size", "Blob"]:
            k = QLabel(key.upper())
            k.setObjectName("Hint")
            v = QLabel("-")
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setWordWrap(True)
            self.info_labels[key] = v
            layout.addWidget(k)
            layout.addWidget(v)
        layout.addStretch()
        return box

    def apply_theme(self) -> None:
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font-family: Segoe UI; }}
            #Header, #Panel {{ background: {PANEL}; border: 1px solid #252b31; border-radius: 8px; }}
            #Title {{ color: {ACCENT}; font-size: 24px; font-weight: 800; letter-spacing: 1px; }}
            #Subtitle, #Hint {{ color: {MUTED}; }}
            #SectionTitle {{ color: {ACCENT_2}; font-weight: 700; }}
            #StatusPill {{ background: #252b31; color: {MUTED}; border-radius: 12px; padding: 6px 12px; font-weight: 700; }}
            QLabel {{ color: {TEXT}; }}
            QComboBox, QLineEdit, QPlainTextEdit {{
                background: #0f1215; color: {TEXT}; border: 1px solid #2b333b; border-radius: 6px; padding: 6px;
                selection-background-color: #3b5876;
            }}
            QPushButton, QToolButton {{
                background: {PANEL_2}; color: {TEXT}; border: 1px solid #303942; border-radius: 6px; padding: 8px 10px;
            }}
            QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; color: #ffffff; }}
            #PrimaryButton {{ background: {ACCENT}; color: #111111; font-weight: 800; border: none; }}
            #PrimaryButton:hover {{ background: #ff823a; }}
            #Console {{ background: #070809; color: #aeb7c2; border: 1px solid #252b31; border-radius: 8px; }}
            QSplitter::handle {{ background: #101316; width: 8px; }}
        """)

    def set_busy(self, busy: bool) -> None:
        for btn in (self.detect_btn, self.probe_btn, self.inject_btn, self.restore_btn):
            btn.setEnabled(not busy)
        self.status_pill.setText("WORKING" if busy else "READY")
        self.status_pill.setStyleSheet(f"background: {'#3a2b18' if busy else '#193522'}; color: {'#ffcb6b' if busy else GREEN};")

    def log(self, text: str) -> None:
        self.console.appendPlainText(text)
        self.statusBar().showMessage(text.splitlines()[-1] if text else "")

    def show_error(self, text: str) -> None:
        self.set_busy(False)
        self.status_pill.setText("ERROR")
        self.status_pill.setStyleSheet(f"background: #3a1d20; color: {RED};")
        self.log("ERROR: " + text)
        QMessageBox.critical(self, APP_TITLE, text)

    def update_inspector(self, values: dict) -> None:
        for key, value in values.items():
            if key in self.info_labels:
                self.info_labels[key].setText(str(value))

    def run_worker(self, fn) -> None:
        self.set_busy(True)

        def work() -> None:
            try:
                fn()
            except Exception as exc:
                self.signals.error.emit(str(exc))
            finally:
                self.signals.done.emit()

        self.signals.done.connect(lambda: self.set_busy(False), Qt.ConnectionType.SingleShotConnection)
        threading.Thread(target=work, daemon=True).start()

    def detect(self) -> None:
        self.run_worker(self._detect)

    def _detect(self) -> None:
        mem = backend.GuestMemory()
        try:
            info = mem.open()
            target = self.current_target()
            entry = backend.find_quick_gsc_entry(mem, target)
            self.signals.log.emit(
                f"{info}\nFound {target}\n"
                f"entry=0x{entry['entry_va']:X}, object=0x{entry['object_va']:X}, "
                f"size=0x{entry['object_size']:X}, source={entry.get('source', 'scan')}"
            )
            self.signals.info.emit(
                {
                    "Process": info,
                    "Target": target,
                    "Mode": entry.get("source", "scan"),
                    "Entry": f"0x{entry['entry_va']:X}",
                    "Object": f"0x{entry['object_va']:X}",
                    "Buffer": f"0x{entry['object_va']:X}",
                    "Size": f"0x{entry['object_size']:X}",
                }
            )
        finally:
            mem.close()

    def inject(self) -> None:
        self.run_worker(self._inject)

    def freeze_probe(self) -> None:
        self.run_worker(self._freeze_probe)

    def _freeze_probe(self) -> None:
        target = self.current_target()
        self.signals.log.emit(
            f"Starting BO2 freeze probe for {target}. Reproduce the freeze now; this records for 120 seconds."
        )
        report = backend.run_freeze_probe(target, duration_seconds=120, interval_seconds=1.0)
        self.signals.log.emit(
            "Freeze probe complete.\n"
            + "\n".join(f"- {item}" for item in report.get("findings", []))
            + f"\nReport: {report['txt_path']}\nRaw JSON: {report['json_path']}"
        )

    def _inject(self) -> None:
        code = self.editor.toPlainText().strip()
        if not code:
            raise RuntimeError("Editor is empty.")
        target_mode = self.game_type.currentText()
        script_name = self.script_file.currentText()
        entry = self.entry_function.text().strip() or "codex_main"
        source, target = backend.patch_template_for_target(target_mode, script_name, code, entry)
        self.signals.log.emit(f"Compiling {target}...")
        blob = backend.run_gsc_tool_compile(source, target)
        blob_size = backend.object_size_from_blob(blob)
        compiled_stem = script_name.removesuffix(".gsc").lstrip("_")
        compiled_path = backend.user_dir() / "build" / f"{target_mode.lower()}_{compiled_stem}_injected.gsc"
        compiled_path.write_bytes(blob)
        mem = backend.GuestMemory()
        try:
            info = mem.open()
            live_entry = backend.find_live_gsc_entry(mem, target)
            obj = live_entry["object_va"]
            size = live_entry["object_size"]
            if blob_size <= size:
                backup = mem.read(obj, size)
                backup_path = backend.user_dir() / "build" / f"backup_{target_mode.lower()}_{obj:X}.bin"
                backup_path.write_bytes(backup)
                mem.write(obj, blob + (b"\x00" * (size - len(blob))))
                mode = "in-place"
                cfg = {
                    "mode": "inplace",
                    "target_gsc": target,
                    "object_va": f"0x{obj:X}",
                    "object_size": f"0x{size:X}",
                    "backup_file": str(backup_path),
                    "compiled_file": str(compiled_path),
                    "script_len": f"0x{blob_size:X}",
                    "file_len": f"0x{len(blob):X}",
                }
                buffer_va = obj
            else:
                if target.replace("\\", "/").lower() in backend.INPLACE_ONLY_TARGETS:
                    raise RuntimeError(
                        f"Compiled {target} is larger than the live object "
                        f"(0x{blob_size:X} > 0x{size:X}). Relocating this MP startup target is unsafe "
                        "on system-link map load; reduce the script size or use a larger in-place target."
                    )
                if blob_size > backend.MAX_RELOCATED_BLOB_SIZE:
                    raise RuntimeError(
                        f"Compiled blob is too large for the relocation buffer: "
                        f"0x{blob_size:X} > 0x{backend.MAX_RELOCATED_BLOB_SIZE:X}"
                    )
                buffer_va = backend.find_relocation_buffer(mem, len(blob), obj)
                mem.write(buffer_va, b"\x00" * len(blob))
                mem.write(buffer_va, blob)
                mem.write(live_entry["size_va"], blob_size.to_bytes(4, "big"))
                mem.write(live_entry["buffer_va"], buffer_va.to_bytes(4, "big"))
                mode = "relocated"
                cfg = {
                    "mode": "relocated",
                    "target_gsc": target,
                    "entry_va": f"0x{live_entry['entry_va']:X}",
                    "size_va": f"0x{live_entry['size_va']:X}",
                    "buffer_va": f"0x{live_entry['buffer_va']:X}",
                    "old_size": f"0x{size:X}",
                    "old_buffer": f"0x{obj:X}",
                    "new_size": f"0x{blob_size:X}",
                    "new_buffer": f"0x{buffer_va:X}",
                    "object_va": f"0x{obj:X}",
                    "object_size": f"0x{size:X}",
                    "compiled_file": str(compiled_path),
                    "script_len": f"0x{blob_size:X}",
                    "file_len": f"0x{len(blob):X}",
                }
            cfg_path = backend.user_dir() / "last_injection.json"
            cfg_path.write_text(json.dumps(cfg, indent=2))
            self.restore_cfg = cfg_path
            self.signals.log.emit(
                f"{info}\nInjected {target} ({mode})\n"
                f"entry=0x{live_entry['entry_va']:X}, object=0x{obj:X}, buffer=0x{buffer_va:X}, "
                f"object_size=0x{size:X}, blob=0x{blob_size:X}, file=0x{len(blob):X}, "
                f"source={live_entry.get('source', 'scan')}\n"
                f"Load/restart the map."
            )
            self.signals.info.emit(
                {
                    "Process": info,
                    "Target": target,
                    "Mode": mode,
                    "Entry": f"0x{live_entry['entry_va']:X}",
                    "Object": f"0x{obj:X}",
                    "Buffer": f"0x{buffer_va:X}",
                    "Size": f"0x{size:X}",
                    "Blob": f"0x{blob_size:X}",
                }
            )
        finally:
            mem.close()

    def restore(self) -> None:
        self.run_worker(self._restore)

    def _restore(self) -> None:
        cfg_path = self.restore_cfg or backend.user_dir() / "last_injection.json"
        if not cfg_path.exists():
            raise RuntimeError("No last_injection.json found.")
        cfg = json.loads(cfg_path.read_text())
        mem = backend.GuestMemory()
        try:
            info = mem.open()
            if cfg.get("mode") == "relocated":
                mem.write(int(cfg["size_va"], 16), int(cfg["old_size"], 16).to_bytes(4, "big"))
                mem.write(int(cfg["buffer_va"], 16), int(cfg["old_buffer"], 16).to_bytes(4, "big"))
                self.signals.log.emit(
                    f"{info}\nRestored {cfg['target_gsc']} table entry "
                    f"to buffer={cfg['old_buffer']}, size={cfg['old_size']}."
                )
            else:
                backup = Path(cfg["backup_file"]).read_bytes()
                mem.write(int(cfg["object_va"], 16), backup)
                self.signals.log.emit(f"{info}\nRestored {cfg['target_gsc']} at {cfg['object_va']}.")
        finally:
            mem.close()


def main() -> int:
    qapp = QApplication(sys.argv)
    qapp.setApplicationName(APP_TITLE)
    window = InjectorWindow()
    window.show()
    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
