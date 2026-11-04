"""Process step: FID + settings -> nmrproc.com (via basicFT2/3.com) -> spectrum."""
import os
import tempfile

from PySide6 import QtCore, QtWidgets

import subprocess

from . import autophase, procgen, theme, volume
from .scriptpanel import CodeEditor
from .widgets import Card, CardHeader, InfoTip, button, clear_layout, hbox, icon, label, vbox

Qt = QtCore.Qt


class ProcessPage(QtWidgets.QWidget):
    process_requested = QtCore.Signal(str)     # path of the saved nmrproc.com
    open_requested = QtCore.Signal()           # pick an FID
    log_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fid_path = None
        self.folder = None
        self.exp = None
        self.settings = None
        self.generated = ""
        self._wrote_this_session = False
        self.proc = None
        self._tmp = tempfile.mkdtemp(prefix="pipedraw-proc-")
        self.regen_timer = QtCore.QTimer(self, singleShot=True, interval=500)
        self.regen_timer.timeout.connect(self.regenerate)

        self.stack = QtWidgets.QStackedLayout(self)
        self.stack.addWidget(self._build_empty())
        self.stack.addWidget(self._build_main())

    # ---- layout ---------------------------------------------------------

    def _build_empty(self):
        w = QtWidgets.QWidget()
        card = Card(margins=(28, 28, 28, 28), spacing=12)
        card.setMaximumWidth(560)
        b = button("Choose FID…", kind="primary", icon=icon("folder", "#FFFFFF"),
                   slot=self.open_requested,
                   tip="test.fid, or one plane of fid/test%03d.fid")
        b.setMinimumHeight(36)
        card.body.addWidget(label("Process an FID", "h2"))
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

        self.input_card = Card(spacing=8)
        self.input_title = label("", "h2")
        self.input_sub = label("", "muted")
        self.input_pills = QtWidgets.QWidget()
        self.input_pills.setLayout(hbox(spacing=6))
        self.input_card.body.addWidget(self.input_title)
        self.input_card.body.addWidget(self.input_sub)
        self.input_card.body.addWidget(self.input_pills)
        self.left.addWidget(self.input_card)

        self.dims_box = vbox(spacing=12)
        self.left.addLayout(self.dims_box)

        out = Card(spacing=8)
        out.body.addWidget(label("Output", "h2"))
        self.out_edit = QtWidgets.QLineEdit()
        self.out_edit.editingFinished.connect(self._changed)
        out.body.addLayout(hbox(label("Spectrum", "muted"), self.out_edit, spacing=8))
        self.left.addWidget(out)
        self.left.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(560)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        root.addWidget(scroll)

        card = Card(margins=(0, 0, 0, 0), spacing=0)
        tab = QtWidgets.QFrame()
        tab.setObjectName("chip")
        tab.setLayout(hbox(label("nmrproc.com", "h3"),
                           InfoTip("Written by NMRPipe's basicFT2.com / basicFT3.com for these "
                                   "settings. After processing, edit it in the Script tab; "
                                   "phasing writes its PS values back into it."),
                           spacing=8, margins=(10, 4, 10, 4)))
        self.state_lbl = label("", "muted")
        self.save_btn = button("Save", slot=self.save,
                               tip="Write nmrproc.com without running it")
        self.process_btn = button("Process", kind="primary", icon=icon("play", "#FFFFFF", 14),
                                  slot=self.process, tip="Save nmrproc.com, run it and open "
                                  "the spectrum for phasing")
        card.body.addWidget(CardHeader(tab, self.state_lbl, None, self.save_btn, self.process_btn))
        self.notice = QtWidgets.QFrame()
        self.notice.setObjectName("banner")
        self.notice_lbl = label("")
        self.notice_lbl.setWordWrap(True)
        self.log_btn = button("Show log", kind="link", slot=self.log_requested)
        self.notice.setLayout(hbox(self.notice_lbl, None, self.log_btn, spacing=10,
                                   margins=(14, 8, 14, 8)))
        self.notice.hide()
        card.body.addWidget(self.notice)
        self.preview = CodeEditor()
        self.preview.setReadOnly(True)
        card.body.addWidget(self.preview, 1)
        root.addWidget(card, 1)
        return page

    # ---- loading ----------------------------------------------------------

    def load(self, fid_path, exp=None):
        self.fid_path = fid_path
        self.exp = exp
        self.folder = procgen.work_folder(fid_path)
        self._wrote_this_session = False
        try:
            self.settings = procgen.defaults(fid_path, exp)
        except Exception as exc:  # unreadable FID
            QtWidgets.QMessageBox.critical(self, "Could not read the FID", f"{fid_path}\n\n{exc}")
            return
        data = volume.load(fid_path)
        axes = (list(reversed(data.axes)) if isinstance(data, volume.Volume) else [data.x, data.y])
        self.axes = dict(zip("xyz", axes))
        s = self.settings
        nuclei = " × ".join(theme.nice_label(a.label) for a in axes)
        self.input_title.setText(f"{s.ndim}D FID · {nuclei}")
        self.input_sub.setText(f"{os.path.basename(self.folder)}/{s.fid}")
        pills = self.input_pills.layout()
        clear_layout(pills)
        if s.nus:
            p = label("NUS · SMILE", "pill")
            p.setToolTip("Sampled points listed in nuslist; the spectrum is reconstructed with "
                         "SMILE. Indirect phases are SMILE settings too.")
            pills.addWidget(p)
        if os.path.isfile(os.path.join(self.folder, "nmrproc.com")):
            p = label("Phases from nmrproc.com", "pill")
            p.setToolTip("The folder's nmrproc.com sets the starting phases. Saving replaces "
                          "it and keeps the old one as nmrproc.com.bak.")
            pills.addWidget(p)
        pills.addStretch(1)
        self.out_edit.setText(s.out)
        self._build_dim_cards()
        self.notice.hide()
        self.stack.setCurrentIndex(1)
        self.regenerate()

    def reset(self):
        """Back to the empty state (no FID)."""
        if self.proc is not None:
            self.proc.kill()
            self.proc = None
        self.fid_path = self.folder = self.exp = self.settings = None
        self.generated = ""
        self.preview.clear()
        self.notice.hide()
        self.stack.setCurrentIndex(0)

    def _build_dim_cards(self):
        clear_layout(self.dims_box)
        s = self.settings
        self.dim_widgets = {}
        for d, ds in s.dims.items():
            ax = self.axes[d]
            role = "direct" if d == "x" else "indirect"
            card = Card(spacing=8)
            card.body.addWidget(label(f"{d.upper()} · {theme.nice_label(ax.label)} · {role} · "
                                      f"{ax.size} points", "h3"))
            grid = QtWidgets.QGridLayout()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(6)
            w = {}
            w["window"] = self._combo({k: v[0] for k, v in procgen.WINDOWS.items()}, ds.window)
            w["q1"], w["q2"], w["q3"] = (self._edit(ds.q1), self._edit(ds.q2), self._edit(ds.q3))
            w["lb"] = self._edit(ds.lb)
            w["c1"] = self._combo({"Auto": "Auto", "0.5": "0.5", "1.0": "1.0"}, ds.c1)
            w["zf"] = self._combo(procgen.ZERO_FILLS, ds.zf)
            w["ft"] = self._combo(procgen.FT_ARGS, ds.ft)
            w["p0"], w["p1"] = self._edit(f"{ds.p0:.1f}"), self._edit(f"{ds.p1:.1f}")
            qnames = procgen.WINDOWS[ds.window][1]
            w["qlabels"] = [label(n, "muted") for n in qnames]
            grid.addWidget(label("Window", "muted"), 0, 0)
            grid.addWidget(w["window"], 0, 1, 1, 3)
            for i in range(3):
                grid.addWidget(w["qlabels"][i], 1, i + 1)
                grid.addWidget((w["q1"], w["q2"], w["q3"])[i], 2, i + 1)
            grid.addWidget(label("Line broadening (Hz)", "muted"), 3, 0)
            grid.addWidget(w["lb"], 3, 1)
            grid.addWidget(label("First point", "muted"), 3, 2)
            grid.addWidget(w["c1"], 3, 3)
            grid.addWidget(label("Zero fill", "muted"), 4, 0)
            grid.addWidget(w["zf"], 4, 1, 1, 3)
            grid.addWidget(label("Fourier transform", "muted"), 5, 0)
            grid.addWidget(w["ft"], 5, 1, 1, 3)
            grid.addWidget(label("Phase P0 / P1", "muted"), 6, 0)
            grid.addWidget(w["p0"], 6, 1)
            grid.addWidget(w["p1"], 6, 2)
            if d == "x":
                auto = button("Auto", slot=self._auto_x,
                              tip="NMRPipe's autophaser (basicAutoPhase.com) on the FID, "
                                  "using the indirect phases set here")
                grid.addWidget(auto, 6, 3)
            row = 7
            if d == "x":
                w["sol"] = QtWidgets.QCheckBox("Solvent filter (SOL)", checked=s.solvent)
                w["base"] = QtWidgets.QCheckBox("Baseline correction (POLY -auto)",
                                                checked=s.baseline)
                w["x1"], w["xn"] = self._edit(s.ext_x1), self._edit(s.ext_xn)
                grid.addWidget(label("Keep region", "muted"), row, 0)
                grid.addWidget(w["x1"], row, 1)
                grid.addWidget(w["xn"], row, 2)
                grid.addWidget(label("% or ppm", "muted"), row, 3)
                grid.addWidget(w["sol"], row + 1, 0, 1, 2)
                grid.addWidget(w["base"], row + 1, 2, 1, 2)
                for k in ("sol", "base"):
                    w[k].toggled.connect(self._changed)
            elif not s.nus:
                w["lp"] = QtWidgets.QCheckBox("Linear prediction", checked=ds.lp)
                w["lp"].setToolTip("Forward-backward, order 8")
                w["lp"].toggled.connect(self._changed)
                grid.addWidget(w["lp"], row, 0, 1, 4)
            if ds.ft == "alt":
                w["ft"].setToolTip("-alt: acquired States-TPPI")
            grid.setColumnStretch(4, 1)
            card.body.addLayout(grid)
            self.dims_box.addWidget(card)
            self.dim_widgets[d] = w

    def _auto_x(self):
        """Zero-order phase for the direct dimension from NMRPipe's autophaser."""
        self._read_form()
        s = self.settings
        y = s.dims.get("y")
        cmd = autophase.nmrpipe_command(s.fid, s.dims["x"].p1, y.p0 if y else 0.0,
                                        y.p1 if y else 0.0, y.ft if y else "None")
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            r = subprocess.run(["csh", "-c", cmd], cwd=self.folder, capture_output=True,
                               text=True, timeout=300)
            p0 = autophase.parse_nmrpipe(r.stdout + r.stderr)
        except Exception as exc:  # show NMRPipe's message
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Auto phase", str(exc))
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self.dim_widgets["x"]["p0"].setText(f"{p0:.1f}")
        self._changed()

    def _combo(self, items, current):
        c = QtWidgets.QComboBox()
        for key, text in items.items():
            c.addItem(text, key)
        i = c.findData(current)
        c.setCurrentIndex(i if i >= 0 else 0)
        c.currentIndexChanged.connect(self._changed)
        return c

    def _edit(self, value):
        e = QtWidgets.QLineEdit(str(value))
        e.setAlignment(Qt.AlignRight)
        e.setMaximumWidth(110)
        e.editingFinished.connect(self._changed)
        return e

    # ---- settings -> script ----------------------------------------------------

    def _read_form(self):
        s = self.settings
        for d, w in self.dim_widgets.items():
            ds = s.dims[d]
            ds.window = w["window"].currentData()
            ds.q1, ds.q2, ds.q3 = w["q1"].text(), w["q2"].text(), w["q3"].text()
            ds.lb, ds.c1 = w["lb"].text(), w["c1"].currentData()
            ds.zf, ds.ft = w["zf"].currentData(), w["ft"].currentData()
            ds.p0, ds.p1 = _float(w["p0"].text()), _float(w["p1"].text())
            for lbl, name in zip(w["qlabels"], procgen.WINDOWS[ds.window][1]):
                lbl.setText(name)
            if "lp" in w:
                ds.lp = w["lp"].isChecked()
            if d == "x":
                s.solvent, s.baseline = w["sol"].isChecked(), w["base"].isChecked()
                s.ext_x1, s.ext_xn = w["x1"].text().strip(), w["xn"].text().strip()
        s.out = self.out_edit.text().strip() or s.out

    def _changed(self, *_):
        self.regen_timer.start()

    def regenerate(self):
        """Ask basicFT for the script in the background."""
        if not self.settings:
            return
        self._read_form()
        if self.proc is not None:
            self.proc.kill()
        path = os.path.join(self._tmp, "nmr.com")
        if os.path.exists(path):
            os.remove(path)
        self.state_lbl.setText("Updating…")
        self.proc = QtCore.QProcess(self)
        self.proc.setWorkingDirectory(self.folder)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.finished.connect(lambda code, _s, p=self.proc, path=path: self._generated(p, code, path))
        self.proc.start("/bin/csh", ["-c", procgen.command(self.settings, path)])

    def _generated(self, proc, code, path):
        if proc is not self.proc:
            return   # superseded by a newer request
        out = bytes(proc.readAll()).decode(errors="replace")
        self.proc = None
        if code != 0 or not os.path.isfile(path):
            self.state_lbl.setText("basicFT reported a problem")
            self.preview.setPlainText(out[-3000:])
            self.process_btn.setEnabled(False)
            return
        with open(path) as fh:
            self.generated = fh.read()
        self.preview.setPlainText(self.generated)
        self.state_lbl.setText("Preview · not saved")
        self.process_btn.setEnabled(True)

    # ---- saving and running ------------------------------------------------------

    def script_path(self):
        return os.path.join(self.folder, "nmrproc.com") if self.folder else None

    def save(self) -> bool:
        if not self.generated or self.proc is not None:
            return False
        path = self.script_path()
        try:
            if os.path.isfile(path) and not self._wrote_this_session:
                with open(path) as fh:
                    old = fh.read()
                if old != self.generated and not os.path.exists(path + ".bak"):
                    with open(path + ".bak", "w") as fh:
                        fh.write(old)
            with open(path, "w") as fh:
                fh.write(self.generated)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Could not save", str(exc))
            return False
        self._wrote_this_session = True
        self.state_lbl.setText("Saved as nmrproc.com")
        return True

    def process(self):
        if self.save():
            self.process_requested.emit(self.script_path())

    def set_running(self, running, text=""):
        self.process_btn.setEnabled(not running)
        self.notice_lbl.setText(text)
        self.notice.setVisible(bool(text))


def _float(text):
    try:
        return float(text)
    except ValueError:
        return 0.0
