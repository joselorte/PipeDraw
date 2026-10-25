"""Convert step: a Bruker experiment -> fid.com -> NMRPipe FID."""
import os

from PySide6 import QtCore, QtGui, QtWidgets

from . import bruker, theme
from .runner import CshRunner
from .scriptpanel import CodeEditor
from .widgets import (Card, CardHeader, Dot, FlowLayout, InfoTip, button, clear_layout, hbox,
                      heading, icon, label, restyle, vbox)

Qt = QtCore.Qt

FIELD_ROWS = (("N", "Points (N)"), ("T", "Complex points (T)"), ("MODE", "Mode"),
              ("SW", "Sweep width (Hz)"), ("OBS", "Frequency (MHz)"), ("CAR", "Carrier (ppm)"),
              ("LAB", "Label"))
OPTION_ROWS = (("decim", "Decimation"), ("dspfvs", "DSP firmware"), ("grpdly", "Group delay"))


class ConvertPage(QtWidgets.QWidget):
    view_requested = QtCore.Signal(str)      # path of the converted FID
    open_requested = QtCore.Signal()         # pick an experiment folder
    converted = QtCore.Signal(str)
    process_requested = QtCore.Signal(str)   # path of the converted FID

    def __init__(self, parent=None):
        super().__init__(parent)
        self.exp = None
        self.path = None                     # fid.com
        self.fid_script = None
        self._wrote_this_session = False
        self._filling = False
        self.fields = {}
        self.runner = CshRunner(self)
        self.runner.output.connect(self._log)
        self.runner.finished.connect(self._finished)
        self.tick = QtCore.QTimer(self, interval=200)
        self.tick.timeout.connect(lambda: self.elapsed_lbl.setText(f"{self.runner.elapsed():.1f} s"))

        self.stack = QtWidgets.QStackedLayout(self)
        self.stack.addWidget(self._build_empty())
        self.stack.addWidget(self._build_main())
        self.reparse_timer = QtCore.QTimer(self, singleShot=True, interval=300)
        self.reparse_timer.timeout.connect(self._reparse)

    # ---- layout ---------------------------------------------------------

    def _build_empty(self):
        w = QtWidgets.QWidget()
        card = Card(margins=(28, 28, 28, 28), spacing=12)
        card.setMaximumWidth(560)
        b = button("Choose experiment folder…", kind="primary", icon=icon("folder", "#FFFFFF"),
                   slot=self.open_requested,
                   tip="The folder with acqus and fid or ser")
        b.setMinimumHeight(36)
        card.body.addWidget(label("Convert a Bruker experiment", "h2"))
        card.body.addWidget(b, 0, Qt.AlignLeft)
        w.setLayout(vbox(None, hbox(None, card, None), None, margins=(16, 16, 16, 16)))
        return w

    def _build_main(self):
        page = QtWidgets.QWidget()
        root = hbox(spacing=12, margins=(12, 12, 12, 12))
        page.setLayout(root)

        left = QtWidgets.QWidget()
        self.left = vbox(spacing=12, margins=(0, 0, 10, 0))
        left.setLayout(self.left)

        # Experiment summary
        self.exp_card = Card(spacing=8)
        self.exp_title = label("", "h2")
        self.exp_sub = label("", "muted")
        self.exp_sub.setWordWrap(True)
        self.exp_chips = QtWidgets.QWidget()
        self.exp_chips.setLayout(FlowLayout(6))
        self.exp_card.body.addWidget(self.exp_title)
        self.exp_card.body.addWidget(self.exp_sub)
        self.exp_card.body.addWidget(self.exp_chips)
        self.left.addWidget(self.exp_card)

        # Parameters
        par = Card(spacing=10)
        par.body.addWidget(heading("Conversion parameters",
                                   "Editing a field rewrites that value in fid.com's bruk2pipe "
                                   "line. You can also edit fid.com directly."))
        self.param_grid = QtWidgets.QGridLayout()
        self.param_grid.setHorizontalSpacing(8)
        self.param_grid.setVerticalSpacing(6)
        par.body.addLayout(self.param_grid)
        self.option_grid = QtWidgets.QGridLayout()
        self.option_grid.setHorizontalSpacing(8)
        self.option_grid.setVerticalSpacing(6)
        par.body.addLayout(self.option_grid)
        self.left.addWidget(par)

        # Checks
        self.check_card = Card(spacing=8)
        self.check_card.body.addWidget(heading("Checks",
                                               "fid.com compared with the acquisition "
                                               "parameters in acqus."))
        self.check_list = vbox(spacing=8)
        self.check_card.body.addLayout(self.check_list)
        self.left.addWidget(self.check_card)

        # Referencing
        self.ref_card = Card(spacing=8)
        self.ref_card.body.addWidget(heading(
            "Referencing", "¹H on water at the sample temperature (7.83 − T/96.9 ppm); "
            "other nuclei from the IUPAC frequency ratios. Assumes the ¹H carrier was set "
            "on water."))
        self.ref_note = label("", "mono")
        self.ref_note.setWordWrap(True)
        self.ref_card.body.addWidget(self.ref_note)
        self.ref_grid = QtWidgets.QGridLayout()
        self.ref_grid.setHorizontalSpacing(14)
        self.ref_card.body.addLayout(self.ref_grid)
        self.ref_btn = button("Use water referencing", slot=self.use_water_referencing,
                              tip="Set every carrier from water at the sample temperature")
        self.ref_card.body.addWidget(self.ref_btn, 0, Qt.AlignLeft)
        self.left.addWidget(self.ref_card)

        self.left.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(620)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        root.addWidget(scroll)

        right = QtWidgets.QSplitter(Qt.Vertical)
        right.setHandleWidth(12)
        root.addWidget(right, 1)

        ed_card = Card(margins=(0, 0, 0, 0), spacing=0)
        tab = QtWidgets.QFrame()
        tab.setObjectName("chip")
        self.dirty_dot = Dot(theme.WARN, 7)
        self.dirty_dot.setToolTip("Not saved")
        self.dirty_dot.hide()
        tab.setLayout(hbox(label("fid.com", "h3"), self.dirty_dot, spacing=8, margins=(10, 4, 10, 4)))
        self.state_lbl = label("", "muted")
        self.gen_btn = button("Generate from acqus", slot=self.generate,
                              tip="Ask NMRPipe's bruker converter (bruker -AUTO) for a new fid.com")
        self.save_btn = button("Save", slot=self.save)
        self.convert_btn = button("Convert", kind="primary", icon=icon("play", "#FFFFFF", 14),
                                  slot=self.convert, tip="Save fid.com and run it")
        ed_card.body.addWidget(CardHeader(tab, self.state_lbl, None, self.gen_btn, self.save_btn,
                                          self.convert_btn))
        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("banner")
        self.banner_lbl = label("")
        self.banner_lbl.setWordWrap(True)
        self.view_btn = button("View FID", slot=self._view)
        self.next_btn = button("Next: Process  →", kind="primary", slot=self._process,
                               tip="Write a processing script for this FID")
        self.banner.setLayout(hbox(self.banner_lbl, None, self.view_btn, self.next_btn,
                                   spacing=10, margins=(14, 8, 14, 8)))
        self.banner.hide()
        ed_card.body.addWidget(self.banner)
        self.editor = CodeEditor()
        self.editor.textChanged.connect(lambda: self.reparse_timer.start())
        self.editor.modificationChanged.connect(self._update_state)
        ed_card.body.addWidget(self.editor, 1)
        right.addWidget(ed_card)

        console = QtWidgets.QFrame()
        console.setObjectName("console")
        title = QtWidgets.QLabel("Conversion log")
        title.setStyleSheet("font-weight: 600; color: white;")
        self.status_lbl = label("Idle", "status", state="idle")
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(160)
        self.progress.hide()
        self.elapsed_lbl = QtWidgets.QLabel("")
        self.elapsed_lbl.setFont(theme.mono_font(12))
        self.stop_btn = button("Stop", kind="console", icon=icon("stop", "#E6E8EC", 12),
                               slot=self.runner.stop)
        self.stop_btn.hide()
        self.log = QtWidgets.QPlainTextEdit(readOnly=True)
        self.log.setObjectName("log")
        self.log.setMaximumBlockCount(5000)
        console.setLayout(vbox(hbox(title, self.status_lbl, self.progress, self.elapsed_lbl, None,
                                    self.stop_btn, spacing=12, margins=(14, 10, 14, 10)),
                               self.log, spacing=0, margins=(0, 0, 0, 8)))
        right.addWidget(console)
        right.setSizes([520, 220])
        return page

    # ---- loading --------------------------------------------------------

    def load(self, folder):
        """Show a Bruker experiment. Uses its fid.com if there is one, else asks
        NMRPipe for a new one (not saved until Save or Convert)."""
        self.exp = bruker.load_experiment(folder)
        self.path = os.path.join(self.exp.folder, "fid.com")
        self._wrote_this_session = False
        self.banner.hide()
        self.log.clear()
        self._set_status("idle", "Idle")
        self._fill_summary()
        if os.path.isfile(self.path):
            with open(self.path) as fh:
                self._set_editor(fh.read(), modified=False)
            self.state_lbl.setText("Existing fid.com")
        else:
            self.generate()
        self._show_output_banner()
        self.stack.setCurrentIndex(1)

    def reset(self):
        """Back to the empty state (no experiment)."""
        self.exp = self.path = self.fid_script = None
        self.editor.blockSignals(True)
        self.editor.clear()
        self.editor.blockSignals(False)
        self.editor.document().setModified(False)
        self.log.clear()
        self.banner.hide()
        self._set_status("idle", "Idle")
        self.stack.setCurrentIndex(0)

    def generate(self):
        if not self.exp:
            return
        if not bruker.have_nmrpipe():
            QtWidgets.QMessageBox.warning(
                self, "NMRPipe not found",
                "bruk2pipe isn't available from csh, so fid.com can't be generated. "
                "Check that NMRPipe is set up in ~/.cshrc.")
            return
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            text, log = bruker.auto_fid_com(self.exp.folder)
        except Exception as exc:  # show NMRPipe's own message
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Could not generate fid.com", str(exc))
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self._set_editor(text, modified=True, undoable=bool(self.editor.toPlainText()))
        self.state_lbl.setText("Generated by bruker -AUTO · not saved")
        self.log.clear()
        self.log.appendPlainText("$ bruker -AUTO\n" + log.strip())

    def _set_editor(self, text, modified, undoable=False):
        if undoable:
            cur = QtGui.QTextCursor(self.editor.document())
            cur.beginEditBlock()
            cur.select(QtGui.QTextCursor.Document)
            cur.insertText(text)
            cur.endEditBlock()
        else:
            self.editor.setPlainText(text)
        self.editor.document().setModified(modified)
        self._reparse()

    # ---- saving and running ----------------------------------------------

    def save(self) -> bool:
        if not self.path:
            return False
        text = self.editor.toPlainText()
        try:
            if os.path.isfile(self.path) and not self._wrote_this_session:
                with open(self.path) as fh:
                    old = fh.read()
                backup = self.path + ".bak"
                if old != text and not os.path.exists(backup):
                    with open(backup, "w") as fh:
                        fh.write(old)
                    self._log(f"Kept the previous fid.com as {os.path.basename(backup)}\n")
            with open(self.path, "w") as fh:
                fh.write(text)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Could not save", str(exc))
            return False
        self._wrote_this_session = True
        self.editor.document().setModified(False)
        self.state_lbl.setText("Saved")
        return True

    def maybe_save(self) -> bool:
        if not self.path or not self.editor.document().isModified():
            return True
        ans = QtWidgets.QMessageBox.question(
            self, "Unsaved fid.com", "Save the changes to fid.com?",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel)
        if ans == QtWidgets.QMessageBox.Save:
            return self.save()
        return ans == QtWidgets.QMessageBox.Discard

    def convert(self):
        if self.runner.running or not self.save():
            return
        self.banner.hide()
        self.log.clear()
        self._set_status("running", "Converting")
        self.progress.show()
        self.stop_btn.show()
        self.convert_btn.setEnabled(False)
        self.tick.start()
        self.runner.run(self.path)

    def _finished(self, code, secs):
        self.tick.stop()
        self.elapsed_lbl.setText(f"{secs:.1f} s")
        self.progress.hide()
        self.stop_btn.hide()
        self.convert_btn.setEnabled(True)
        if code == 0:
            self._set_status("ok", "Finished")
            self._show_output_banner(just_converted=True)
            out = self.output_path()
            if out:
                self.converted.emit(out)
        else:
            self._set_status("failed", f"Failed · exit {code}")

    def output_path(self):
        if not (self.fid_script and self.fid_script.output and self.exp):
            return None
        return os.path.normpath(os.path.join(self.exp.folder, self.fid_script.output))

    def _output_exists(self, path):
        if path is None:
            return False
        if "%" in path:
            from . import volume
            return volume.first_plane(path) is not None
        return os.path.isfile(path)

    def _show_output_banner(self, just_converted=False):
        out = self.output_path()
        if not self._output_exists(out):
            self.banner.hide()
            return
        name = os.path.relpath(out, self.exp.folder)
        self.banner_lbl.setText(f"Converted → {name}" if just_converted else f"{name} exists")
        self.banner.show()

    def _process(self):
        out = self.output_path()
        if self._output_exists(out):
            self.process_requested.emit(out)

    def _view(self):
        out = self.output_path()
        if self._output_exists(out):
            self.view_requested.emit(out)

    def _log(self, text):
        self.log.moveCursor(QtGui.QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QtGui.QTextCursor.End)

    def _set_status(self, state, text):
        self.status_lbl.setText(text)
        self.status_lbl.setProperty("state", state)
        restyle(self.status_lbl)

    def _update_state(self, *_):
        self.dirty_dot.setVisible(self.editor.document().isModified())

    # ---- summary, form, checks ---------------------------------------------

    def _fill_summary(self):
        e = self.exp
        nuclei = " × ".join(theme.nice_label(d.nucleus) for d in e.dims)
        self.exp_title.setText(e.pulprog or os.path.basename(e.folder))
        parent = os.path.basename(os.path.dirname(e.folder))
        self.exp_sub.setText(f"{e.title + ' · ' if e.title else ''}{parent}/{os.path.basename(e.folder)}")
        self.exp_sub.setToolTip(e.folder)
        chips = self.exp_chips.layout()
        chips.clear()
        items = [f"{e.ndim}D · {nuclei}", f"NS {e.ns}", f"{e.temperature:.1f} K",
                 f"{e.data_file} · {e.data_bytes / 1e6:.1f} MB"]
        for text in items:
            chips.addWidget(label(text, "pill"))
        if e.nus:
            grid = " × ".join(str(d.nus_td // 2) for d in e.dims[1:])
            pill = label(f"NUS · {e.nus_fraction:.0%}", "pill")
            pill.setToolTip(f"{e.nus_points} of {grid} indirect points sampled. fid.com puts "
                            "them on the full grid with nusExpand.tcl and writes a sampling "
                            "mask; processing reconstructs the spectrum (SMILE).")
            chips.addWidget(pill)
        self.exp_chips.updateGeometry()

    def _reparse(self):
        self.fid_script = bruker.parse_fid_com(self.editor.toPlainText())
        if self.fid_script is None:
            return
        self._fill_form()
        self._fill_checks()
        self._fill_referencing()

    def _fill_form(self):
        fs = self.fid_script
        focus = QtWidgets.QApplication.focusWidget()
        if focus in self.fields.values():
            return   # don't disturb a field being edited
        self._filling = True
        grid = self.param_grid
        clear_layout(grid)
        self.fields = {}
        matched = bruker.match_dims(self.exp, fs) if self.exp else {}
        for c, d in enumerate(fs.dim_names, start=1):
            nuc = matched.get(d)
            head = f"{d.upper()} · {theme.nice_label(nuc.nucleus)}" if nuc else d.upper()
            grid.addWidget(label(head, "field"), 0, c)
        for r, (f, name) in enumerate(FIELD_ROWS, start=1):
            grid.addWidget(label(name, "muted"), r, 0)
            for c, d in enumerate(fs.dim_names, start=1):
                value = fs.dims[d].get(f, "")
                if f == "MODE":
                    w = QtWidgets.QComboBox()
                    w.addItems(bruker.MODES if value in bruker.MODES else bruker.MODES + [value])
                    w.setCurrentText(value)
                    w.activated.connect(lambda _i, d=d, w=w: self._edit(f"{d}MODE", w.currentText()))
                else:
                    w = QtWidgets.QLineEdit(value)
                    w.setAlignment(Qt.AlignRight)
                    w.editingFinished.connect(lambda d=d, f=f, w=w: self._edit(f"{d}{f}", w.text()))
                w.setMinimumWidth(96)
                grid.addWidget(w, r, c)
                self.fields[f"{d}{f}"] = w
        grid.setColumnStretch(len(fs.dim_names) + 1, 1)

        og = self.option_grid
        clear_layout(og)
        for i, (opt, name) in enumerate(OPTION_ROWS):
            if opt not in fs.options:
                continue
            w = QtWidgets.QLineEdit(fs.options[opt])
            w.setAlignment(Qt.AlignRight)
            w.editingFinished.connect(lambda opt=opt, w=w: self._edit(opt, w.text()))
            og.addWidget(label(name, "muted"), 0, i)
            og.addWidget(w, 1, i)
            self.fields[opt] = w
        self._filling = False

    def _edit(self, option, value):
        if self._filling or not value.strip():
            return
        text = self.editor.toPlainText()
        try:
            new = bruker.set_option(text, option, value.strip())
        except KeyError:
            return
        if new != text:
            self._set_editor(new, modified=True, undoable=True)

    def _fill_checks(self):
        lay = self.check_list
        clear_layout(lay)
        if not self.exp:
            return
        found = bruker.checks(self.exp, self.fid_script)
        if not found:
            ok = label("✓ Matches acqus")
            ok.setStyleSheet(f"color: {theme.PIVOT_TEXT};")
            lay.addWidget(ok)
            return
        for chk in found:
            text = label(chk.text)
            text.setWordWrap(True)
            row = hbox(Dot(theme.WARN), text, spacing=8)
            row.setStretchFactor(text, 1)
            if chk.detail:
                row.addWidget(InfoTip(chk.detail))
            row.setAlignment(Qt.AlignTop)
            if chk.fix:
                b = button(chk.fix_label, slot=lambda _=False, fx=chk.fix: self._edit(*fx))
                row.addWidget(b, 0, Qt.AlignTop)
            lay.addLayout(row)

    def _fill_referencing(self):
        g = self.ref_grid
        clear_layout(g)
        if not self.exp:
            return
        e = self.exp
        water = bruker.water_ppm(e.temperature)
        self.ref_note.setText(f"Water at {e.temperature:.1f} K: {water:.3f} ppm")
        refs = bruker.water_referenced(e)
        matched = bruker.match_dims(e, self.fid_script)
        for c, h in enumerate(("", "In fid.com", "Water-referenced"), start=0):
            g.addWidget(label(h, "field"), 0, c)
        r = 1
        self._ref_values = {}
        for d in self.fid_script.dim_names:
            dim = matched.get(d)
            if dim is None:
                continue
            ref = refs[e.dims.index(dim)]
            g.addWidget(label(f"{d.upper()} · {theme.nice_label(dim.nucleus)}"), r, 0)
            g.addWidget(label(self.fid_script.dims[d].get("CAR", ""), "mono"), r, 1)
            g.addWidget(label("—" if ref is None else f"{ref:.3f}", "mono"), r, 2)
            if ref is not None:
                self._ref_values[f"{d}CAR"] = f"{ref:.3f}"
            r += 1
        g.setColumnStretch(3, 1)

    def use_water_referencing(self):
        text = self.editor.toPlainText()
        for option, value in getattr(self, "_ref_values", {}).items():
            try:
                text = bruker.set_option(text, option, value)
            except KeyError:
                pass
        if text != self.editor.toPlainText():
            self._set_editor(text, modified=True, undoable=True)
