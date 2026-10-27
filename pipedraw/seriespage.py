"""Series tab: process a set of similar experiments (e.g. a titration) in one lot."""
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6 import QtCore, QtGui, QtWidgets

from . import bruker, series, theme, volume
from .widgets import Card, CardHeader, InfoTip, button, hbox, heading, icon, label, vbox

Qt = QtCore.Qt

COLS = ("", "Experiment", "Titrant", "Check", "Status", "Phase vs template")
# Phase differences from the template above this are flagged. The checks
# scatter by a few degrees between identical-phase points (NMRPipe's
# autophaser works in 1° steps over a region the peaks move through).
PHASE_WARN = 8.0
STATUS_TEXT = {"idle": "", "queued": "Queued", "running": "Running…", "done": "✓ Processed",
               "failed": "Failed", "exported": "✓ Exported"}


class SeriesPage(QtWidgets.QWidget):
    open_template = QtCore.Signal(str)          # experiment folder
    view_point = QtCore.Signal(str)             # spectrum path
    overlay_requested = QtCore.Signal(list)     # [(path, label, colour)]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder = None
        self.points = []
        self.template_check = None
        self.pool = None
        self.futures = {}
        self.stop_event = threading.Event()
        self.procs = set()
        self.poll = QtCore.QTimer(self, interval=200)
        self.poll.timeout.connect(self._poll)

        root = vbox(spacing=12, margins=(12, 12, 12, 12))
        self.setLayout(root)

        top = Card(spacing=10)
        self.title = heading("Series", "Process similar experiments (e.g. a titration) with "
                                       "one recipe: set up one experiment as the template "
                                       "(convert, process, phase), then run the rest.")
        self.folder_lbl = label("", "muted")
        choose = button("Choose folder…", icon=icon("folder", theme.TEXT),
                        slot=self.choose_folder)
        top.body.addLayout(hbox(self.title, self.folder_lbl, None, choose, spacing=10))

        self.template_combo = QtWidgets.QComboBox()
        self.template_combo.setMinimumWidth(120)
        self.template_combo.currentIndexChanged.connect(self._template_changed)
        self.template_state = label("", "muted")
        self.open_btn = button("Set up template", slot=self._open_template,
                               tip="Open the template in Convert to convert, process and "
                                   "phase it")
        self.water = QtWidgets.QCheckBox("Water-reference each point")
        self.water.setToolTip("Set each point's carriers from its own temperature. Off: all "
                              "points keep the template's carriers.")
        self.jobs = QtWidgets.QSpinBox(minimum=1, maximum=max(1, os.cpu_count() or 4),
                                       value=min(4, os.cpu_count() or 4))
        self.jobs.setToolTip("Experiments processed at the same time")
        top.body.addLayout(hbox(label("Template", "field"), self.template_combo,
                                self.template_state, self.open_btn, None, self.water,
                                label("Parallel", "field"), self.jobs, spacing=10))

        self.run_btn = button("Run series", kind="primary", icon=icon("play", "#FFFFFF", 14),
                              slot=self.run, tip="Convert and process every ticked experiment "
                                                 "with the template's recipe")
        self.stop_btn = button("Stop", icon=icon("stop", theme.TEXT, 12), slot=self.stop)
        self.stop_btn.hide()
        self.check_btn = button("Check phases", slot=self.check_phases,
                                tip="Compare each point's phase with the template's")
        self.overlay_btn = button("Overlay", slot=self.overlay,
                                  tip="Show the processed points together in View")
        self.export_btn = button("Export all", slot=self.export_all,
                                 tip="Sparky and NMRView files for every processed point, "
                                     "named by experiment and titrant")
        top.body.addLayout(hbox(self.run_btn, self.stop_btn, None, self.check_btn,
                                self.overlay_btn, self.export_btn, spacing=8))
        root.addWidget(top)

        table_card = Card(margins=(0, 0, 0, 0), spacing=0)
        self.table = QtWidgets.QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked
                                   | QtWidgets.QAbstractItemView.EditKeyPressed
                                   | QtWidgets.QAbstractItemView.SelectedClicked)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.table.setStyleSheet(f"QTableWidget {{ border: 0; background: {theme.CARD}; }}"
                                 f"QHeaderView::section {{ background: {theme.CARD}; border: 0;"
                                 f" border-bottom: 1px solid {theme.DIVIDER}; padding: 6px;"
                                 f" font-weight: 600; color: {theme.TEXT2}; }}")
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.summary = label("", "muted")
        table_card.body.addWidget(CardHeader(label("Experiments", "h3"), self.summary, None,
                                             InfoTip("Tick the points to process. Type the "
                                                     "titrant amount (it names exported "
                                                     "files). Double-click a processed "
                                                     "point to open it.")))
        table_card.body.addWidget(self.table, 1)
        root.addWidget(table_card, 1)

        self.log = QtWidgets.QPlainTextEdit(readOnly=True)
        self.log.setObjectName("log")
        self.log.setMaximumBlockCount(5000)
        self.log.setFixedHeight(130)
        self.log.setPlaceholderText("Run output appears here.")
        root.addWidget(self.log)

    # ---- choosing the experiments ------------------------------------------------

    def choose_folder(self):
        start = self.folder or os.getcwd()
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Folder with the series' experiments", start)
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder, template=None):
        """Every Bruker experiment directly inside ``folder``, in natural order."""
        if self.busy:
            return
        self.folder = os.path.abspath(folder)
        names = sorted((n for n in os.listdir(self.folder)
                        if bruker.is_experiment(os.path.join(self.folder, n))),
                       key=series.natural_key)
        self.points = [series.Point(os.path.join(self.folder, n)) for n in names]
        for p in self.points:
            # Points processed earlier (in another session, or by series.com).
            proc = os.path.join(p.folder, "nmrproc.com")
            if os.path.isfile(proc):
                out = series.output_path(p.folder, open(proc).read())
                if _exists(out):
                    p.spectrum, p.status = out, "done"
        self.template_check = None
        self.folder_lbl.setText(os.path.basename(self.folder) + "/")
        self.folder_lbl.setToolTip(self.folder)
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        for p in self.points:
            self.template_combo.addItem(p.name, p.folder)
        if template:
            i = self.template_combo.findData(os.path.abspath(template))
            if i >= 0:
                self.template_combo.setCurrentIndex(i)
        self.template_combo.blockSignals(False)
        self._fill_table()
        self._template_changed()

    @property
    def template(self):
        return self.template_combo.currentData()

    @property
    def busy(self):
        return bool(self.futures)

    def _template_changed(self, *_):
        """Re-check every point against the template's acquisition."""
        t = self.template
        self.template_check = None
        if not t:
            self.template_state.setText("")
            return
        texp = bruker.load_experiment(t)
        for p in self.points:
            p.issues = [] if p.folder == t else series.compare(texp, bruker.load_experiment(p.folder))
            p.phase_dev = {}
        ready = self._template_ready()
        self.template_state.setText("✓ Ready" if ready else "Not set up yet")
        self.template_state.setStyleSheet(
            f"color: {theme.PIVOT_TEXT if ready else theme.WARN}; font-size: 12px;")
        self.template_state.setToolTip("" if ready else "Convert, process and phase the "
                                       "template first; its fid.com and nmrproc.com are the "
                                       "recipe for the series.")
        self.run_btn.setEnabled(ready)
        self._fill_table()

    def _template_ready(self):
        """The template has fid.com, nmrproc.com and the spectrum they make."""
        t = self.template
        if not t:
            return False
        proc = os.path.join(t, "nmrproc.com")
        if not (os.path.isfile(os.path.join(t, "fid.com")) and os.path.isfile(proc)):
            return False
        return _exists(series.output_path(t, open(proc).read()))

    def _open_template(self):
        if self.template:
            self.open_template.emit(self.template)

    # ---- table -----------------------------------------------------------------

    def _fill_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.points))
        for r, p in enumerate(self.points):
            if self.table.item(r, 0) is None:
                inc = QtWidgets.QTableWidgetItem()
                inc.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                inc.setCheckState(Qt.Checked)
                self.table.setItem(r, 0, inc)
            name = QtWidgets.QTableWidgetItem(p.name + ("  · template" if p.folder == self.template else ""))
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            name.setFont(theme.mono_font(12))
            self.table.setItem(r, 1, name)
            val = QtWidgets.QTableWidgetItem(p.value)
            val.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self.table.setItem(r, 2, val)
            self._set_row(r)
        self.table.blockSignals(False)
        n = len(self.points)
        self.summary.setText(f"{n} experiment{'s' if n != 1 else ''}")

    def _set_row(self, r):
        p = self.points[r]
        blocked = self.table.blockSignals(True)
        if p.issues:
            check = QtWidgets.QTableWidgetItem("⚠ " + p.issues[0] + (f" (+{len(p.issues) - 1})"
                                                                     if len(p.issues) > 1 else ""))
            check.setForeground(QtGui.QColor(theme.WARN))
            check.setToolTip("Differs from the template:\n" + "\n".join(p.issues))
        else:
            check = QtWidgets.QTableWidgetItem("✓" if p.folder != self.template else "")
            check.setForeground(QtGui.QColor(theme.PIVOT_TEXT))
        status = QtWidgets.QTableWidgetItem(STATUS_TEXT.get(p.status, p.status))
        status.setToolTip(p.log[-1500:] if p.log else "")
        if p.status == "failed":
            status.setForeground(QtGui.QColor(theme.ERROR))
        dev = QtWidgets.QTableWidgetItem(self._dev_text(p))
        if any(abs(v) > PHASE_WARN for v in p.phase_dev.values()):
            dev.setForeground(QtGui.QColor(theme.WARN))
            dev.setToolTip(f"More than {PHASE_WARN:.0f}° from the template: check this point's phase.")
        for c, item in ((3, check), (4, status), (5, dev)):
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(r, c, item)
        self.table.blockSignals(blocked)

    def _dev_text(self, p):
        names = {2: "¹H", 1: "F1", 3: "F3"}
        if p.folder == self.template and p.phase_dev:
            return "reference"
        return "  ".join(f"{names.get(d, d)} {v:+.1f}°" for d, v in sorted(p.phase_dev.items()))

    def _item_changed(self, item):
        if item.column() == 2:
            self.points[item.row()].value = item.text().strip()

    def _double_clicked(self, r, c):
        p = self.points[r]
        if c != 2 and p.spectrum:
            self.view_point.emit(p.spectrum)

    def included(self):
        return [p for r, p in enumerate(self.points)
                if self.table.item(r, 0) and self.table.item(r, 0).checkState() == Qt.Checked]

    # ---- running ------------------------------------------------------------------

    def run(self):
        if self.busy or not self._template_ready():
            return
        points = self.included()
        if not points:
            return
        t = self.template
        flagged = [p.name for p in points if p.issues]
        if flagged and QtWidgets.QMessageBox.question(
                self, "Run series",
                f"{len(flagged)} experiment(s) differ from the template ({', '.join(flagged[:6])}"
                f"{'…' if len(flagged) > 6 else ''}). Run them anyway?") != QtWidgets.QMessageBox.Yes:
            return
        self.log.clear()
        self._log(f"Template {os.path.basename(t)}: reading its recipe…")
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            edits = series.template_edits(t)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        proc = open(os.path.join(t, "nmrproc.com")).read()
        self._log("Carried over from the template's fid.com: "
                  + (", ".join(f"-{k} {v}" for k, v in edits.items()) or "nothing"))
        with open(os.path.join(os.path.commonpath([p.folder for p in self.points]),
                               "series.com"), "w") as fh:
            fh.write(series.series_script(points))
        water = self.water.isChecked()
        for p in points:
            p.status, p.log, p.phase_dev = "queued", "", {}
        self._start(points, lambda p: self._process_point(p, t, edits, proc, water))

    def _process_point(self, p, template, edits, proc, water):
        if p.folder != template:
            series.write_point(p, series.fid_com_for(p.folder, edits, water), proc)
        code, out = self._run(series.run_command(), p.folder)
        p.log = out
        p.spectrum = series.output_path(p.folder, proc)
        return "done" if code == 0 and _exists(p.spectrum) else "failed"

    def check_phases(self):
        if self.busy:
            return
        done = [p for p in self.points if p.spectrum and p.status in ("done", "exported")]
        tp = next((p for p in self.points if p.folder == self.template), None)
        if tp is None or not done:
            return
        if not tp.spectrum:
            tp.spectrum = series.output_path(tp.folder, open(os.path.join(tp.folder, "nmrproc.com")).read())
        self._log("Checking phases against the template…")
        self.template_check = series.phase_check(tp)
        before = {p.folder: p.status for p in done}
        for p in done:
            p.status = "queued"

        def check(p):
            p.phase_dev = series.relative(series.phase_check(p), self.template_check)
            return before[p.folder]
        self._start(done, check)

    def export_all(self):
        if self.busy:
            return
        done = [p for p in self.points if p.spectrum and p.status in ("done", "exported")]
        for p in done:
            p.status = "queued"
        self._log("Exporting Sparky and NMRView files…")
        self._start(done, self._export_point)

    def _export_point(self, p):
        files = series.export_point(p)
        p.log = "\n".join(files)
        return "exported"

    def _start(self, points, job):
        self.stop_event.clear()
        self.pool = ThreadPoolExecutor(max_workers=self.jobs.value())
        self.futures = {}
        for p in points:
            self.futures[self.pool.submit(self._wrap, p, job)] = p
        self._busy_ui(True)
        self.poll.start()
        self._refresh()

    def _wrap(self, p, job):
        if self.stop_event.is_set():
            return "idle"
        p.status = "running"
        try:
            return job(p)
        except Exception as exc:  # report and carry on with the other points
            p.log = (p.log + "\n" if p.log else "") + str(exc)
            return "failed"

    def _run(self, command, cwd):
        """Run a csh command, stoppable; returns (exit code, output)."""
        proc = subprocess.Popen(["csh", "-c", command], cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.procs.add(proc)
        try:
            out, _ = proc.communicate()
        finally:
            self.procs.discard(proc)
        return proc.returncode, out or ""

    def stop(self):
        self.stop_event.set()
        for f in self.futures:
            f.cancel()
        for proc in list(self.procs):
            try:
                os.killpg(proc.pid, 15)
            except OSError:
                pass
        self._log("Stopped.")

    def _poll(self):
        for f, p in list(self.futures.items()):
            if f.done():
                del self.futures[f]
                if f.cancelled():
                    p.status = "idle"
                else:
                    p.status = f.result()
                    self._log(f"{p.name}: {STATUS_TEXT.get(p.status, p.status) or 'skipped'}")
        self._refresh()
        if not self.futures:
            self.poll.stop()
            self.pool.shutdown(wait=False)
            self._busy_ui(False)
            ok = sum(p.status in ("done", "exported") for p in self.points)
            failed = sum(p.status == "failed" for p in self.points)
            self._log(f"Finished: {ok} processed" + (f", {failed} failed" if failed else ""))

    def _refresh(self):
        for r in range(len(self.points)):
            self._set_row(r)

    def _busy_ui(self, busy):
        self.stop_btn.setVisible(busy)
        for b in (self.run_btn, self.check_btn, self.overlay_btn, self.export_btn,
                  self.template_combo, self.open_btn):
            b.setEnabled(not busy)
        if not busy:
            self.run_btn.setEnabled(self._template_ready())

    def _log(self, text):
        self.log.appendPlainText(text)

    # ---- overlay ----------------------------------------------------------------------

    def processed(self):
        return [p for p in self.points if p.spectrum and p.status in ("done", "exported")]

    def overlay(self):
        pts = self.processed()
        if not pts:
            return
        colours = series.gradient(len(pts))
        self.overlay_requested.emit([(p.spectrum, p.value or p.name, c)
                                     for p, c in zip(pts, colours)])


def _exists(path):
    if not path:
        return False
    return volume.first_plane(path) is not None if "%" in path else os.path.exists(path)
