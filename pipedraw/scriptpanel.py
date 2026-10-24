"""Script workspace: pipeline outline, editor and run log for nmrproc.com etc."""
import os
import re
import subprocess

from PySide6 import QtCore, QtGui, QtWidgets

from . import script, theme
from .widgets import Card, CardHeader, Dot, button, clear_layout, hbox, icon, label, restyle, vbox

Qt = QtCore.Qt


class Highlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)

        def fmt(color, bold=False, italic=False):
            f = QtGui.QTextCharFormat()
            f.setForeground(QtGui.QColor(color))
            if bold:
                f.setFontWeight(QtGui.QFont.Bold)
            f.setFontItalic(italic)
            return f

        self.rules = [
            (re.compile(r"\b(nmrPipe|xyz2pipe|pipe2xyz|\w+2pipe)\b"), 0, fmt(theme.TEXT, bold=True)),
            (re.compile(r"-fn\s+(\w+)"), 1, fmt(theme.ACCENT, bold=True)),
            (re.compile(r"-(?:in|out)\s+(\S+)"), 1, fmt("#9A3412")),
        ]
        self.comment = fmt("#6B7280", italic=True)

    def highlightBlock(self, text):
        hash_at = text.find("#")
        code = text if hash_at < 0 else text[:hash_at]
        for rx, group, f in self.rules:
            for m in rx.finditer(code):
                self.setFormat(m.start(group), m.end(group) - m.start(group), f)
        if hash_at >= 0:
            self.setFormat(hash_at, len(text) - hash_at, self.comment)


class _Gutter(QtWidgets.QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QtCore.QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, ev):
        self.editor.paint_gutter(ev)


class CodeEditor(QtWidgets.QPlainTextEdit):
    """Plain-text editor with line numbers and tags after chosen lines."""

    def __init__(self):
        super().__init__()
        self.setObjectName("editor")
        self.setFont(theme.mono_font(13))
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.gutter = _Gutter(self)
        self.tags = {}        # line -> (text, background, foreground)
        self.marked = set()   # lines with a pending change
        self.blockCountChanged.connect(lambda _: self.setViewportMargins(self.gutter_width(), 0, 0, 0))
        self.updateRequest.connect(self._on_update)
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        Highlighter(self.document())

    def gutter_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 20 + self.fontMetrics().horizontalAdvance("9") * max(2, digits)

    def _on_update(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        cr = self.contentsRect()
        self.gutter.setGeometry(QtCore.QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, ev):
        p = QtGui.QPainter(self.gutter)
        p.fillRect(ev.rect(), QtGui.QColor(theme.CARD))
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        h = self.fontMetrics().height()
        while block.isValid() and top <= ev.rect().bottom():
            n = block.blockNumber()
            if n in self.marked:
                p.fillRect(0, top, self.gutter.width(), h, QtGui.QColor("#EAF1FE"))
                p.setPen(QtGui.QColor(theme.ACCENT_DARK))
            else:
                p.setPen(QtGui.QColor("#8A909C"))
            p.drawText(0, top, self.gutter.width() - 10, h, Qt.AlignRight | Qt.AlignVCenter,
                       str(n + 1))
            block = block.next()
            top += round(self.blockBoundingRect(block).height()) if block.isValid() else h

    def set_marks(self, tags, marked):
        self.tags, self.marked = tags, set(marked)
        sels = []
        for line in self.marked:
            block = self.document().findBlockByNumber(line)
            if not block.isValid():
                continue
            sel = QtWidgets.QTextEdit.ExtraSelection()
            sel.format.setBackground(QtGui.QColor("#EAF1FE"))
            sel.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            sel.cursor = QtGui.QTextCursor(block)
            sels.append(sel)
        self.setExtraSelections(sels)
        self.viewport().update()
        self.gutter.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self.tags:
            return
        p = QtGui.QPainter(self.viewport())
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        tag_font = theme.ui_font(11, QtGui.QFont.DemiBold)
        fm = self.fontMetrics()
        tfm = QtGui.QFontMetrics(tag_font)
        p.setFont(tag_font)
        offset = self.contentOffset()
        for line, (text, bg, fg) in self.tags.items():
            block = self.document().findBlockByNumber(line)
            if not block.isValid() or not block.isVisible():
                continue
            geo = self.blockBoundingGeometry(block).translated(offset)
            if geo.bottom() < 0 or geo.top() > self.viewport().height():
                continue
            x = fm.horizontalAdvance(block.text()) + 4 + 14 + offset.x()
            w = tfm.horizontalAdvance(text) + 12
            r = QtCore.QRectF(x, geo.top() + (geo.height() - 18) / 2, w, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(bg))
            p.drawRoundedRect(r, 4, 4)
            p.setPen(QtGui.QColor(fg))
            p.drawText(r, Qt.AlignCenter, text)


class ScriptPanel(QtWidgets.QWidget):
    """Shows, edits and runs the processing script.

    Scripts run as ``csh <script>`` in the script's folder. csh reads
    ~/.cshrc, which is where NMRPipe's installer sets up nmrPipe.
    """

    changed = QtCore.Signal()        # text edited (debounced)
    loaded = QtCore.Signal()         # a script was opened
    saved_state = QtCore.Signal()    # saved / unsaved changed
    run_started = QtCore.Signal()
    run_finished = QtCore.Signal(int)
    step_clicked = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self.proc = None
        self.last_run = None            # (exit code, seconds)
        self.dim_labels = {2: "F2", 1: "F1"}
        self.pending = {}               # pipe_dim -> (p0, p1) about to be written
        self.clock = QtCore.QElapsedTimer()
        self.tick = QtCore.QTimer(self, interval=200)
        self.tick.timeout.connect(self._update_elapsed)

        root = hbox(spacing=12, margins=(12, 12, 12, 12))
        self.setLayout(root)

        # Pipeline outline
        self.outline = Card(spacing=6)
        self.outline.setFixedWidth(300)
        self.outline.body.addWidget(label("Pipeline", "h2"))
        hint = label("Read from the script. Click a step to jump to its line.", "muted")
        hint.setWordWrap(True)
        self.outline.body.addWidget(hint)
        self.outline_list = vbox(spacing=6)
        self.outline.body.addLayout(self.outline_list)
        self.outline.body.addStretch(1)
        outline_scroll = QtWidgets.QScrollArea()
        outline_scroll.setWidget(self.outline)
        outline_scroll.setWidgetResizable(True)
        outline_scroll.setFixedWidth(306)
        root.addWidget(outline_scroll)

        right = QtWidgets.QSplitter(Qt.Vertical)
        right.setHandleWidth(12)
        root.addWidget(right, 1)

        # Editor card
        ed_card = Card(margins=(0, 0, 0, 0), spacing=0)
        self.file_lbl = label("No script", "h3")
        self.dirty_dot = Dot(theme.WARN, 7)
        self.dirty_dot.setToolTip("Unsaved changes")
        self.dirty_dot.hide()
        tab = QtWidgets.QFrame()
        tab.setObjectName("chip")
        tab.setLayout(hbox(self.file_lbl, self.dirty_dot, spacing=8, margins=(10, 4, 10, 4)))
        self.folder_lbl = label("", "muted")
        self.open_btn = button("Open…", tip="Open another script", slot=self.open_dialog)
        self.revert_btn = button("Revert", tip="Reload the script from disk", slot=self.revert)
        self.save_btn = button("Save  ⌘S", slot=self.save)
        ed_card.body.addWidget(CardHeader(tab, self.folder_lbl, None, self.open_btn,
                                          self.revert_btn, self.save_btn))

        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("banner")
        info = QtWidgets.QLabel()
        info.setPixmap(icon("info", "#1C44A8").pixmap(16, 16))
        self.banner_lbl = label("")
        self.banner_lbl.setWordWrap(True)
        undo = button("Undo change", kind="link", slot=self._undo_change)
        self.banner.setLayout(hbox(info, self.banner_lbl, None, undo, spacing=10,
                                   margins=(14, 8, 14, 8)))
        self.banner.hide()
        ed_card.body.addWidget(self.banner)

        self.editor = CodeEditor()
        self.editor.setPlaceholderText(
            "No nmrproc.com or proc.com next to the spectrum. Use Open… to pick a script.")
        self.editor.modificationChanged.connect(self._update_title)
        self.editor.textChanged.connect(lambda: self.change_timer.start())
        self.editor.setContentsMargins(0, 6, 0, 6)
        ed_card.body.addWidget(self.editor, 1)
        right.addWidget(ed_card)

        # Run log
        console = QtWidgets.QFrame()
        console.setObjectName("console")
        self.status_lbl = label("Idle", "status", state="idle")
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(160)
        self.progress.hide()
        self.elapsed_lbl = QtWidgets.QLabel("")
        self.elapsed_lbl.setFont(theme.mono_font(12))
        self.stop_btn = button("Stop", kind="console", icon=icon("stop", "#E6E8EC", 12),
                               slot=self.stop)
        self.stop_btn.hide()
        title = QtWidgets.QLabel("Run log")
        title.setStyleSheet("font-weight: 600; color: white;")
        head = hbox(title, self.status_lbl, self.progress, self.elapsed_lbl, None, self.stop_btn,
                    spacing=12, margins=(14, 10, 14, 10))
        self.log = QtWidgets.QPlainTextEdit(readOnly=True)
        self.log.setObjectName("log")
        self.log.setMaximumBlockCount(5000)
        self.log.setPlaceholderText("Output from running the script appears here.")
        console.setLayout(vbox(head, self.log, spacing=0, margins=(0, 0, 0, 8)))
        right.addWidget(console)
        right.setSizes([560, 220])

        self.change_timer = QtCore.QTimer(self, singleShot=True, interval=300)
        self.change_timer.timeout.connect(self.changed)
        self.change_timer.timeout.connect(self.refresh_outline)
        self._set_enabled(False)

    # ---- file -----------------------------------------------------------

    def load(self, path):
        with open(path) as fh:
            text = fh.read()
        self.path = path
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        self.editor.document().setModified(False)
        self.banner.hide()
        self._set_enabled(True)
        self._update_title()
        self.refresh_outline()
        self.loaded.emit()

    def unload(self):
        """Forget the script (after Close)."""
        self.path = None
        self.pending = {}
        self.editor.blockSignals(True)
        self.editor.clear()
        self.editor.blockSignals(False)
        self.editor.document().setModified(False)
        self.banner.hide()
        self.log.clear()
        self._set_status("idle", "Idle")
        self.elapsed_lbl.setText("")
        self.file_lbl.setText("No script")
        self.file_lbl.setToolTip("")
        self.folder_lbl.setText("")
        self.dirty_dot.hide()
        self._set_enabled(False)
        self.refresh_outline()
        self.saved_state.emit()

    def open_dialog(self):
        start = os.path.dirname(self.path) if self.path else os.getcwd()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open processing script", start, "Scripts (*.com *.csh *.sh);;All files (*)")
        if path and self.maybe_save():
            self.load(path)

    def revert(self):
        if self.path and (not self.editor.document().isModified() or QtWidgets.QMessageBox.question(
                self, "Revert", "Discard your unsaved changes?") == QtWidgets.QMessageBox.Yes):
            self.load(self.path)

    def save(self) -> bool:
        if not self.path:
            return False
        try:
            with open(self.path, "w") as fh:
                fh.write(self.editor.toPlainText())
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Could not save", str(exc))
            return False
        self.editor.document().setModified(False)
        return True

    def maybe_save(self) -> bool:
        """Offer to save unsaved edits. False if the user cancels."""
        if not self.path or not self.editor.document().isModified():
            return True
        ans = QtWidgets.QMessageBox.question(
            self, "Unsaved script", f"Save changes to {os.path.basename(self.path)}?",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel)
        if ans == QtWidgets.QMessageBox.Save:
            return self.save()
        return ans == QtWidgets.QMessageBox.Discard

    def text(self) -> str:
        return self.editor.toPlainText()

    def is_modified(self) -> bool:
        return self.editor.document().isModified()

    def set_text(self, text: str):
        """Replace the text as one undoable edit."""
        cur = QtGui.QTextCursor(self.editor.document())
        cur.beginEditBlock()
        cur.select(QtGui.QTextCursor.Document)
        cur.insertText(text)
        cur.endEditBlock()

    def highlight_line(self, line: int):
        block = self.editor.document().findBlockByNumber(line)
        if block.isValid():
            cur = QtGui.QTextCursor(block)
            self.editor.setTextCursor(cur)
            self.editor.centerCursor()
            self.editor.setFocus()

    def show_banner(self, text):
        self.banner_lbl.setText(text)
        self.banner.show()

    def _undo_change(self):
        self.editor.undo()
        self.banner.hide()

    # ---- outline and marks ---------------------------------------------

    def set_context(self, dim_labels, pending):
        """Dimension names (pipe_dim -> label) and PS values about to change."""
        self.dim_labels = dim_labels
        self.pending = pending
        self.refresh_outline()

    def refresh_outline(self):
        lay = self.outline_list
        clear_layout(lay)
        text = self.text()
        tags, marked = {}, []
        last_dim = None
        for st in script.steps(text) if self.path else []:
            if st.kind == "in":
                lay.addWidget(label(f"Input · {st.name}", "field"))
            elif st.kind == "out":
                lay.addSpacing(4)
                lay.addWidget(label(f"Output · {st.name}", "field"))
            elif st.kind == "transpose":
                lay.addLayout(self._divider(st.name))
                last_dim = None
            else:
                if st.pipe_dim != last_dim:
                    name = self.dim_labels.get(st.pipe_dim, "")
                    role = "direct" if st.pipe_dim == 2 else "indirect"
                    lay.addSpacing(4)
                    lay.addWidget(label(f"{name.upper()} · F{st.pipe_dim} {role.upper()}"
                                        if name else f"F{st.pipe_dim}", "section"))
                    last_dim = st.pipe_dim
                lay.addWidget(self._step_button(st))
                if st.name == "PS" and st.pipe_dim in self.dim_labels:
                    pend = st.pipe_dim in self.pending
                    tags[st.line] = (f"{self.dim_labels[st.pipe_dim]} · F{st.pipe_dim}",
                                     theme.ACCENT if pend else theme.DIVIDER,
                                     "#FFFFFF" if pend else theme.TEXT2)
        for ps in script.parse_ps(text):
            if ps.pipe_dim in self.pending:
                marked.append(ps.line)
        self.editor.set_marks(tags, marked)

    def _divider(self, name):
        def line():
            f = QtWidgets.QFrame()
            f.setFixedHeight(1)
            f.setStyleSheet(f"background: {theme.BORDER};")
            return f
        lbl = label(f"{name}  transpose", "muted")
        return hbox(line(), lbl, line(), spacing=8, margins=(0, 4, 0, 4))

    def _step_button(self, st):
        b = button(kind="outline", slot=lambda: self._jump(st.line))
        b.setMinimumHeight(32)
        name = label(st.name, "mono")
        name.setStyleSheet("font-weight: 600;")
        if st.name == "PS" and st.pipe_dim in self.pending:
            p0, p1 = self.pending[st.pipe_dim]
            new = f"{script._fmt(p0)} / {script._fmt(p1)}"
            summary = label(f"<span style='color:{theme.MUTED}; text-decoration: line-through'>"
                            f"{st.summary}</span>  →  <b style='color:{theme.ACCENT_DARK}'>{new}</b>",
                            "mono")
            b.setProperty("pending", True)
        else:
            summary = label(st.summary, "mono" if st.name == "PS" else "muted")
        for w in (name, summary):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        b.setLayout(hbox(name, None, summary, spacing=8, margins=(10, 4, 10, 4)))
        return b

    def _jump(self, line):
        self.highlight_line(line)
        self.step_clicked.emit(line)

    # ---- running --------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.proc is not None

    def run(self):
        if not self.path or self.running or not self.save():
            return
        self.banner.hide()
        self.proc = QtCore.QProcess(self)
        self.proc.setWorkingDirectory(os.path.dirname(self.path))
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read_output)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)
        self.log.clear()
        self.log.appendPlainText(f"$ csh {os.path.basename(self.path)}")
        self._set_status("running", "Running")
        self.progress.show()
        self.stop_btn.show()
        self.clock.start()
        self.tick.start()
        self.run_started.emit()
        self.proc.start("/bin/csh", [os.path.basename(self.path)])

    def stop(self):
        if not self.proc:
            return
        pid = self.proc.processId()
        if pid:
            # The pipeline's nmrPipe processes are children of csh.
            subprocess.run(["pkill", "-TERM", "-P", str(pid)], capture_output=True)
        self.proc.kill()
        self.log.appendPlainText("[stopped]")

    def _read_output(self):
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        # nmrPipe -verb redraws progress with carriage returns.
        data = data.replace("\r\n", "\n").replace("\r", "\n")
        self.log.moveCursor(QtGui.QTextCursor.End)
        self.log.insertPlainText(data)
        self.log.moveCursor(QtGui.QTextCursor.End)

    def _finished(self, code, _status):
        self._read_output()
        self._done(code)

    def _error(self, err):
        if err == QtCore.QProcess.FailedToStart:
            self.log.appendPlainText("[could not start /bin/csh]")
            self._done(-1)

    def _done(self, code):
        secs = self.clock.elapsed() / 1000.0
        self.last_run = (code, secs)
        self.tick.stop()
        self.proc = None
        self.progress.hide()
        self.stop_btn.hide()
        self.elapsed_lbl.setText(f"{secs:.1f} s")
        if code == 0:
            self._set_status("ok", "Finished")
        else:
            self._set_status("failed", f"Failed · exit {code}")
        self.run_finished.emit(code)

    def _update_elapsed(self):
        self.elapsed_lbl.setText(f"{self.clock.elapsed() / 1000.0:.1f} s")

    def _set_status(self, state, text):
        self.status_lbl.setText(text)
        self.status_lbl.setProperty("state", state)
        restyle(self.status_lbl)

    # ---- helpers --------------------------------------------------------

    def _set_enabled(self, on):
        for w in (self.save_btn, self.revert_btn, self.editor):
            w.setEnabled(on)

    def _update_title(self, *_):
        if self.path:
            self.file_lbl.setText(os.path.basename(self.path))
            self.file_lbl.setToolTip(self.path)
            self.folder_lbl.setText(f"{os.path.basename(os.path.dirname(self.path))}/ · csh")
            self.dirty_dot.setVisible(self.editor.document().isModified())
        self.saved_state.emit()
