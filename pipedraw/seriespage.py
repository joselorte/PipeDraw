"""Series tab: process a set of similar experiments (e.g. a titration) in one lot."""
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6 import QtCore, QtGui, QtWidgets

from . import bruker, pseudo, series, theme, volume
from .widgets import Card, CardHeader, InfoTip, button, hbox, heading, icon, label, vbox

Qt = QtCore.Qt

COLS = ("", "Experiment", "Type", "Value", "Check", "Status", "Phase vs template")
C_INC, C_NAME, C_TYPE, C_VALUE, C_CHECK, C_STATUS, C_DEV = range(len(COLS))
# Phase differences from the template above this are flagged. The checks
# scatter by a few degrees between identical-phase points (NMRPipe's
# autophaser works in 1° steps over a region the peaks move through).
PHASE_WARN = 8.0
_BOX_STYLE = "QCheckBox::indicator { width: 18px; height: 18px; }"
STATUS_TEXT = {"idle": "", "queued": "Queued", "running": "Running…", "done": "✓ Processed",
               "failed": "Failed", "exported": "✓ Exported",
               "export_failed": "Processed · export failed"}
# Statuses of a point with a processed spectrum.
PROCESSED = ("done", "exported", "export_failed")
EXPORT_KINDS = (("ucsf", "Sparky"), ("nv", "NMRView"))


class SeriesPage(QtWidgets.QWidget):
    open_template = QtCore.Signal(str)          # experiment folder
    view_point = QtCore.Signal(str)             # spectrum path
    overlay_requested = QtCore.Signal(list)     # [(path, label, colour)]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder = None
        self.points = []
        self._custom_selection = False   # the user picked points by hand
        self._last_ticked = None         # row of the last checkbox clicked (for shift-click)
        self.boxes = []
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
        self.export_btn = button("Export again", slot=self.export_all,
                                 tip="Write the ticked formats again for every ticked, "
                                     "processed point (e.g. after typing titrant amounts)")
        # Each point is exported as soon as it is processed, in the ticked formats.
        settings = QtCore.QSettings()
        self.export_boxes = {}
        for key, name in EXPORT_KINDS:
            box = QtWidgets.QCheckBox(name)
            box.setChecked(settings.value(f"series/export_{key}", True, type=bool))
            box.toggled.connect(lambda on, k=key: QtCore.QSettings().setValue(f"series/export_{k}", on))
            self.export_boxes[key] = box
        export_tip = InfoTip("Run series exports each point when it is processed, named by "
                             "experiment and titrant (e.g. 12_0.5eq.ucsf), into the series "
                             "folder's sparky/ and nv/ folders.")
        top.body.addLayout(hbox(self.run_btn, self.stop_btn, 12, label("Then export", "field"),
                                self.export_boxes["ucsf"], self.export_boxes["nv"], export_tip,
                                None, self.check_btn, self.overlay_btn, self.export_btn,
                                spacing=8))
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
        hh.setSectionResizeMode(C_CHECK, QtWidgets.QHeaderView.Stretch)
        self.table.setStyleSheet(f"QTableWidget {{ border: 0; background: {theme.CARD}; }}"
                                 f"QHeaderView::section {{ background: {theme.CARD}; border: 0;"
                                 f" border-bottom: 1px solid {theme.DIVIDER}; padding: 6px;"
                                 f" font-weight: 600; color: {theme.TEXT2}; }}")
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.installEventFilter(self)
        hh.setSectionResizeMode(C_INC, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(C_INC, 40)
        self.summary = label("", "muted")
        table_card.body.addWidget(CardHeader(label("Experiments", "h3"), self.summary, None,
                                             InfoTip("Tick the points to process. Type the "
                                                     "titrant amount (it names exported "
                                                     "files). Double-click a processed "
                                                     "point to open it.")))
        self.range_edit = QtWidgets.QLineEdit()
        self.range_edit.setPlaceholderText("e.g. 10-21, 25")
        self.range_edit.setFixedWidth(160)
        self.range_edit.setToolTip("Experiments to tick, by number; Enter applies")
        self.range_edit.returnPressed.connect(self.select_range)
        self.hide_others = QtWidgets.QCheckBox("Hide other types")
        self.hide_others.setToolTip("Hide experiments with a different pulse program, "
                                    "dimensions or nuclei from the template")
        self.hide_others.toggled.connect(self._hide_toggled)
        bar = QtWidgets.QFrame()
        bar.setObjectName("cardHeader")
        # Above the checkbox column: tick or untick every shown experiment.
        self.all_box = QtWidgets.QCheckBox()
        self.all_box.setTristate(True)
        self.all_box.setToolTip("Tick or untick all (shown) experiments")
        self.all_box.setStyleSheet(_BOX_STYLE)
        self.all_box.clicked.connect(self._all_clicked)
        bar.setLayout(hbox(self.all_box, 8, label("Select", "field"),
                           button("Same as template", slot=self.select_same,
                                  tip="Tick experiments of the template's type"),
                           12, self.range_edit, button("Tick", slot=self.select_range),
                           None, self.hide_others, spacing=8, margins=(12, 6, 14, 6)))
        table_card.body.addWidget(bar)
        table_card.body.addWidget(self.table, 1)
        root.addWidget(table_card, 1)

        self.log = QtWidgets.QPlainTextEdit(readOnly=True)
        self.log.setObjectName("log")
        self.log.setMaximumBlockCount(5000)
        self.log.setFixedHeight(130)
        self.log.setPlaceholderText("Run output appears here.")
        root.addWidget(self.log)

    # ---- choosing the experiments ------------------------------------------------

    def note(self, text):
        self._log(text)

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
        planes = pseudo.read_values(self.folder)     # delays etc. of a split experiment
        for p in self.points:
            p.value = planes.get(p.name, "")
        self._custom_selection = False
        self.table.setRowCount(0)
        self.boxes = []
        for p in self.points:
            try:
                p.pulprog = bruker.load_experiment(p.folder).pulprog
            except Exception:  # unreadable parameters: still listed
                p.pulprog = "?"
            # Points processed earlier (in another session, or by series.com).
            proc = os.path.join(p.folder, "nmrproc.com")
            if os.path.isfile(proc):
                out = series.output_path(p.folder, open(proc).read())
                if _exists(out):
                    p.spectrum, p.status = out, "done"
        self.template_check = None
        shown = os.path.basename(self.folder) + "/"
        if planes:
            shown = os.path.basename(os.path.dirname(self.folder)) + "/" + shown
        self.folder_lbl.setText(shown)
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
            other = bruker.load_experiment(p.folder)
            p.same = p.folder == t or series.same_kind(texp, other)
            p.issues = [] if p.folder == t else series.compare(texp, other)
            p.phase_dev = {}
            if not self._custom_selection:
                p.include = p.same
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
        if len(self.boxes) != len(self.points):
            # Checkboxes are made once per folder; refreshes only update them.
            self.boxes = []
            for r in range(len(self.points)):
                box = QtWidgets.QCheckBox()
                box.setStyleSheet(_BOX_STYLE)
                box.setToolTip("Shift-click to tick or untick a run of experiments")
                box.clicked.connect(lambda checked, r=r: self._box_clicked(r, checked))
                under = QtWidgets.QTableWidgetItem()
                under.setFlags(Qt.NoItemFlags)       # nothing to select or draw under the box
                self.table.setItem(r, C_INC, under)
                cell = QtWidgets.QWidget()
                cell.setLayout(hbox(None, box, None))
                self.table.setCellWidget(r, C_INC, cell)
                self.boxes.append(box)
        for r, p in enumerate(self.points):
            self.boxes[r].setChecked(p.include)
            name = QtWidgets.QTableWidgetItem(p.name + ("  · template" if p.folder == self.template else ""))
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            name.setFont(theme.mono_font(12))
            self.table.setItem(r, C_NAME, name)
            kind = QtWidgets.QTableWidgetItem(p.pulprog)
            kind.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if not p.same:
                kind.setForeground(QtGui.QColor(theme.MUTED))
            self.table.setItem(r, C_TYPE, kind)
            val = QtWidgets.QTableWidgetItem(p.value)
            val.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self.table.setItem(r, C_VALUE, val)
            self._set_row(r)
        self.table.blockSignals(False)
        self._apply_hidden()
        self._update_summary()

    def _update_summary(self):
        n = len(self.points)
        k = sum(p.include for p in self.points)
        self.summary.setText(f"{k} of {n} selected")
        shown = [p for r, p in enumerate(self.points) if not self.table.isRowHidden(r)]
        ticked = sum(p.include for p in shown)
        self.all_box.setCheckState(Qt.Unchecked if ticked == 0 else
                                   Qt.Checked if ticked == len(shown) else Qt.PartiallyChecked)

    def _set_include(self, rows, value):
        for r in rows:
            self.points[r].include = value
            self.boxes[r].setChecked(value)
        self._custom_selection = True
        self._update_summary()

    def _box_clicked(self, r, checked):
        """A checkbox click; with Shift, the run from the last click gets the same state."""
        shift = QtWidgets.QApplication.keyboardModifiers() & Qt.ShiftModifier
        if shift and self._last_ticked is not None and self._last_ticked != r:
            lo, hi = sorted((self._last_ticked, r))
            rows = [i for i in range(lo, hi + 1) if not self.table.isRowHidden(i)]
        else:
            rows = [r]
        self._last_ticked = r
        self._set_include(rows, checked)

    def _all_clicked(self, *_):
        shown = [r for r in range(len(self.points)) if not self.table.isRowHidden(r)]
        # From "all ticked" untick; from none or some, tick them all.
        value = not all(self.points[r].include for r in shown)
        self._set_include(shown, value)

    def eventFilter(self, obj, ev):
        # Space toggles the selected rows.
        if obj is self.table and ev.type() == QtCore.QEvent.KeyPress and ev.key() == Qt.Key_Space:
            rows = sorted({i.row() for i in self.table.selectedIndexes()})
            if rows:
                self._set_include(rows, not self.points[rows[0]].include)
                return True
        return super().eventFilter(obj, ev)

    def _hide_toggled(self, hide):
        if hide:
            # Nothing hidden may stay ticked: it would be processed unseen.
            for r, p in enumerate(self.points):
                if not p.same and p.include:
                    p.include = False
                    if r < len(self.boxes):
                        self.boxes[r].setChecked(False)
        self._apply_hidden()

    def _apply_hidden(self):
        hide = self.hide_others.isChecked()
        for r, p in enumerate(self.points):
            self.table.setRowHidden(r, hide and not p.same)
        self._update_summary()

    def _select(self, keep):
        for p in self.points:
            p.include = bool(keep(p))
        self._custom_selection = True
        self._fill_table()

    def select_same(self):
        self._select(lambda p: p.same)
        self._custom_selection = False

    def select_range(self):
        text = self.range_edit.text()
        if not text.strip():
            return
        try:
            picked = series.parse_selection(text, [p.name for p in self.points])
        except ValueError as exc:
            self.range_edit.setToolTip(str(exc))
            self.range_edit.setStyleSheet(f"border-color: {theme.ERROR};")
            return
        self.range_edit.setStyleSheet("")
        self.range_edit.setToolTip("Experiments to tick, by number; Enter applies")
        self._select(lambda p: p.name in picked)

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
        if p.status in ("failed", "export_failed"):
            status.setForeground(QtGui.QColor(theme.ERROR))
        dev = QtWidgets.QTableWidgetItem(self._dev_text(p))
        if any(abs(v) > PHASE_WARN for v in p.phase_dev.values()):
            dev.setForeground(QtGui.QColor(theme.WARN))
            dev.setToolTip(f"More than {PHASE_WARN:.0f}° from the template: check this point's phase.")
        for c, item in ((C_CHECK, check), (C_STATUS, status), (C_DEV, dev)):
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(r, c, item)
        self.table.blockSignals(blocked)

    def _dev_text(self, p):
        names = {2: "¹H", 1: "F1", 3: "F3"}
        if p.folder == self.template and p.phase_dev:
            return "reference"
        return "  ".join(f"{names.get(d, d)} {v:+.1f}°" for d, v in sorted(p.phase_dev.items()))

    def _item_changed(self, item):
        if item.column() == C_VALUE:
            self.points[item.row()].value = item.text().strip()

    def _double_clicked(self, r, c):
        p = self.points[r]
        if c not in (C_VALUE, C_INC) and p.spectrum:
            self.view_point.emit(p.spectrum)

    def included(self):
        return [p for p in self.points if p.include]

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
        with open(os.path.join(self.export_dest(), "series.com"), "w") as fh:
            fh.write(series.series_script(points))
        water = self.water.isChecked()
        for p in points:
            p.status, p.log, p.phase_dev = "queued", "", {}
        kinds = self.export_kinds()
        self._start(points, lambda p: self._process_point(p, t, edits, proc, water, kinds))

    def export_kinds(self):
        return tuple(k for k, box in self.export_boxes.items() if box.isChecked())

    def _process_point(self, p, template, edits, proc, water, kinds=()):
        if p.folder != template:
            series.write_point(p, series.fid_com_for(p.folder, edits, water), proc)
        code, out = self._run(series.run_command(), p.folder)
        p.log = out
        p.spectrum = series.output_path(p.folder, proc)
        if not (code == 0 and _exists(p.spectrum)):
            return "failed"
        if not kinds or self.stop_event.is_set():
            return "done"
        return self._export_point(p, kinds)

    def check_phases(self):
        if self.busy:
            return
        done = [p for p in self.points if p.spectrum and p.status in PROCESSED]
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
        kinds = self.export_kinds()
        done = [p for p in self.processed() if p.include]
        if not kinds or not done:
            self._log("Nothing to export: tick a format and processed experiments.")
            return
        for p in done:
            p.status = "queued"
        self._log("Exporting " + " and ".join(n for k, n in EXPORT_KINDS if k in kinds)
                  + " files…")
        self._start(done, lambda p: self._export_point(p, kinds))

    def export_dest(self):
        """The series folder, which gets sparky/ and nv/ folders of exports."""
        return self.folder

    def _export_point(self, p, kinds):
        """Export one processed point; a failed export leaves it processed."""
        try:
            files = series.export_point(p, kinds, self.export_dest())
        except Exception as exc:  # NMRPipe's message, kept in the tooltip
            p.log = (p.log + "\n" if p.log else "") + f"Export failed: {exc}"
            return "export_failed"
        p.log = (p.log + "\n" if p.log else "") + "Exported:\n" + "\n".join(files)
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
            ok = sum(p.status in PROCESSED for p in self.points)
            failed = sum(p.status == "failed" for p in self.points)
            exported = sum(p.status == "exported" for p in self.points)
            bad = sum(p.status == "export_failed" for p in self.points)
            self._log(f"Finished: {ok} processed" + (f", {exported} exported" if exported else "")
                      + (f", {failed} failed" if failed else "")
                      + (f", {bad} not exported (see their status)" if bad else ""))
            if exported:
                kinds = self.export_kinds() or tuple(series.EXPORT_FOLDERS)
                self._log("Exported files are in " + " and ".join(
                    os.path.join(self.export_dest(), series.EXPORT_FOLDERS[k]) + "/"
                    for k in kinds))

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
        return [p for p in self.points if p.spectrum and p.status in PROCESSED]

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
