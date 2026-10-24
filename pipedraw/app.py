"""PipeDraw main window: header, spectrum view, phasing inspector, script workspace."""
import contextlib
import os
import time
from dataclasses import dataclass, field

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import bruker, contours, phase, script, theme, volume
from .scriptpanel import ScriptPanel
from .convertpage import ConvertPage
from .exportpage import ExportPage
from .processpage import ProcessPage
from .startpage import StartPage
from .widgets import (Card, CardHeader, Chip, Dot, FlowLayout, Segmented, StepBadge, Stepper,
                      button, clear_layout, hbox, icon, label, legend_item, restyle, two_line, vbox)

Qt = QtCore.Qt

AXIS_WIDTH = 56
AXIS_HEIGHT = 34
CONVERT, PROCESS, VIEW, PHASE, EXPORT, SCRIPT = 0, 1, 2, 3, 4, 5
CUBE_LIMIT = 64e6   # 3D data up to this many points are read whole (for planes in
                    # any orientation, projections and finding the strongest plane)


def _shape(src):
    return src.shape if isinstance(src, volume.Volume) else src.real.shape


@contextlib.contextmanager
def _busy():
    QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        yield
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()


@dataclass
class PhaseState:
    """Phasing for one dimension. p0 is referenced to the pivot."""
    p0: float = 0.0
    p1: float = 0.0
    pivot: float = 0.0          # full-width point index
    script_p0: float = 0.0      # values already in the processing script
    script_p1: float = 0.0
    traces: list = field(default_factory=list)  # indices along the other axis

    @property
    def is_identity(self) -> bool:
        return self.p0 == 0.0 and self.p1 == 0.0

    def key(self):
        return (self.p0, self.p1, self.pivot)


class TraceViewBox(pg.ViewBox):
    """View box for the phasing traces: the mouse wheel changes trace height."""
    wheel = QtCore.Signal(float)

    def wheelEvent(self, ev, axis=None):
        self.wheel.emit(ev.delta())
        ev.accept()


def _style_plot(plot, left=True, bottom=True):
    tick_font = theme.mono_font(11)
    for name in ("left", "bottom"):
        ax = plot.getAxis(name)
        ax.setPen(pg.mkPen(theme.BORDER_STRONG))
        ax.setTextPen(pg.mkPen(theme.MUTED))
        ax.setTickFont(tick_font)
    plot.getAxis("left").setWidth(AXIS_WIDTH)
    plot.getAxis("bottom").setHeight(AXIS_HEIGHT)
    if not left:
        _blank_axis(plot.getAxis("left"))
    if not bottom:
        _blank_axis(plot.getAxis("bottom"))
    plot.vb.setBorder(pg.mkPen(theme.BORDER_STRONG))


def _blank_axis(axis):
    """Keep the axis's space (so linked plots line up) but draw nothing."""
    axis.setStyle(showValues=False, tickLength=0)
    axis.setPen(None)
    axis.setLabel("")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setWindowTitle("PipeDraw")
        self.resize(1440, 920)
        self.settings = QtCore.QSettings()
        self.spec = None
        self.source = None               # Spectrum (2D) or Volume (3D)
        self.dim_state = {}              # pipe_dim -> PhaseState
        self.through = "z"               # 3D: the axis planes step along
        self.plane_index = 0
        self.proj_kind = None            # 3D: "skyline" or "sum" when showing a projection
        self._forced_view = False
        self._dims_text = ""
        self.active = 1                  # axis being phased: 1 = X, 0 = Y
        self.mode = PHASE
        self._base_cache = {}
        self.display = None
        self.cursor_idx = (0, 0)         # (iy, ix)
        self.base_level = 1.0
        self.trace_gain = 1.0            # vertical scale of the phasing traces
        self._trace_yrange = None
        self.fine = True
        self._placing_pivot = False

        self.watcher = QtCore.QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._file_changed)
        self.reload_timer = QtCore.QTimer(self, singleShot=True, interval=400)
        self.reload_timer.timeout.connect(self.reload)
        self.redraw_timer = QtCore.QTimer(self, singleShot=True, interval=60)
        self.redraw_timer.timeout.connect(self._redraw_after_phase)
        self.outline_timer = QtCore.QTimer(self, singleShot=True, interval=250)
        self.outline_timer.timeout.connect(self._push_script_context)
        self.flash_timer = QtCore.QTimer(self, singleShot=True)
        self.flash_timer.timeout.connect(lambda: self.msg_lbl.setText(""))

        ground = QtWidgets.QWidget()
        ground.setObjectName("ground")
        root = vbox(spacing=0)
        ground.setLayout(root)
        self.setCentralWidget(ground)

        self.script_panel = ScriptPanel()
        self._build_header(root)
        self.pages = QtWidgets.QStackedWidget()
        root.addWidget(self.pages, 1)
        self.start_page = StartPage()
        self.start_page.open_requested.connect(self.open)
        self.start_page.browse_requested.connect(self.open_dialog)
        self.pages.addWidget(self.start_page)
        self.pages.addWidget(self._build_workspace())
        self.pages.addWidget(self.script_panel)
        self.convert_page = ConvertPage()
        self.convert_page.view_requested.connect(self._view_converted)
        self.convert_page.open_requested.connect(self.open_experiment_dialog)
        self.pages.addWidget(self.convert_page)
        self.convert_page.process_requested.connect(self.start_processing)
        self.process_page = ProcessPage()
        self.process_page.process_requested.connect(self._run_processing)
        self.process_page.open_requested.connect(self._choose_fid_for_processing)
        self.process_page.log_requested.connect(lambda: self.set_mode(SCRIPT))
        self.pages.addWidget(self.process_page)
        self.export_page = ExportPage()
        self.pages.addWidget(self.export_page)
        self._after_process = False
        self._build_footer(root)
        self._build_menus()

        sp = self.script_panel
        sp.loaded.connect(self._script_changed)
        sp.changed.connect(self._script_changed)
        sp.saved_state.connect(self._update_script_state)
        sp.run_started.connect(self._run_started)
        sp.run_finished.connect(self._run_finished)

        if path:
            self.open(path)
        else:
            found = script.find_script(os.getcwd())
            if found:
                sp.load(found)
            self.start_page.refresh(os.getcwd(), self._recent())
        self._update_script_state()
        self._update_reload_indicator()
        self.set_mode(self.mode)

    @property
    def state(self):
        """Phase states of the displayed X (1) and Y (0) axes."""
        return {1: self.dim_state[self.spec.x.pipe_dim], 0: self.dim_state[self.spec.y.pipe_dim]}

    # ---- layout -------------------------------------------------------

    def _build_header(self, root):
        bar = QtWidgets.QFrame()
        bar.setObjectName("header")
        logo = QtWidgets.QLabel()
        logo.setPixmap(icon("logo", theme.ACCENT, 22).pixmap(22, 22))
        brand = label("PipeDraw", "h3")
        brand.setStyleSheet("font-size: 14px; font-weight: 700;")
        self.crumb_folder = label("", "muted")
        self.crumb_folder.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        self.crumb_file = label("No spectrum open", "h3")
        self.dims_pill = label("", "pill")
        self.dims_pill.hide()
        self.mode_seg = Segmented(["Convert", "Process", "View", "Phase", "Export", "Script"])
        self.mode_seg.set_index(PHASE)
        self.mode_seg.changed.connect(self.set_mode)
        for b, tip in zip(self.mode_seg.buttons, ("⌘1", "⌘2", "⌘3", "⌘4", "⌘5", "⌘6")):
            b.setToolTip(tip)

        self.reload_btn = QtWidgets.QPushButton()
        self.reload_btn.setFlat(True)
        self.reload_btn.setCursor(Qt.PointingHandCursor)
        self.reload_btn.setStyleSheet("QPushButton { border: 0; background: transparent; }")
        self.reload_btn.setToolTip("Reload the spectrum when its file changes (click to toggle)")
        self.reload_dot = Dot(theme.OK)
        self.reload_lbl = label("Auto-reload on")
        self.reload_lbl.setStyleSheet(f"font-size: 12px; color: {theme.TEXT2};")
        for w in (self.reload_dot, self.reload_lbl):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.reload_btn.setLayout(hbox(self.reload_dot, self.reload_lbl, spacing=6, margins=(4, 0, 4, 0)))
        self.reload_btn.setMinimumWidth(190)
        self.reload_btn.clicked.connect(self._toggle_auto_reload)
        self.auto_reload_on = True

        self.open_btn = button("Open…", icon=icon("folder", theme.TEXT), slot=self.open_dialog,
                               tip="Open a spectrum (⌘O)")
        self.stop_btn = button("Stop", icon=icon("stop", theme.TEXT, 12),
                               slot=self.script_panel.stop)
        self.stop_btn.hide()
        self.run_btn = button("Run script   ⌘↩", kind="primary", icon=icon("play", "#FFFFFF", 14),
                              slot=self.script_panel.run)
        for b in (self.open_btn, self.stop_btn, self.run_btn):
            b.setMinimumHeight(34)
        bar.setLayout(hbox(logo, brand, 8, self.crumb_folder, self.crumb_file, self.dims_pill, 8,
                           self.mode_seg, None, self.reload_btn, self.open_btn, self.stop_btn,
                           self.run_btn, spacing=8, margins=(16, 8, 16, 8)))
        root.addWidget(bar)

    def _build_workspace(self):
        page = QtWidgets.QWidget()
        lay = hbox(spacing=12, margins=(12, 12, 12, 12))
        page.setLayout(lay)

        left = QtWidgets.QSplitter(Qt.Vertical)
        left.setHandleWidth(12)
        left.setChildrenCollapsible(False)
        left.addWidget(self._build_spectrum_card())
        self.traces_card = self._build_traces_card()
        left.addWidget(self.traces_card)
        left.setSizes([620, 260])
        lay.addWidget(left, 1)

        self.inspector = QtWidgets.QScrollArea()
        self.inspector.setWidgetResizable(True)
        self.inspector.setFixedWidth(372)
        self.inspector.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        inner.setLayout(vbox(self._build_phase_card(), self._build_changes_card(),
                             self._build_run_card(), None, spacing=12, margins=(0, 0, 12, 0)))
        self.inspector.setWidget(inner)
        lay.addWidget(self.inspector)
        return page

    def _build_spectrum_card(self):
        card = Card(margins=(0, 0, 0, 0), spacing=0)
        self.level_edit = QtWidgets.QLineEdit()
        self.level_edit.setValidator(QtGui.QDoubleValidator(1e-30, 1e30, 6))
        self.level_edit.editingFinished.connect(self._level_typed)
        lvl = Stepper(self.level_edit, "Lower contour level (−)", "Raise contour level (+)",
                      lambda: self.step_level(-1), lambda: self.step_level(1), width=86)
        self.noise_lbl = label("", "muted")
        self.factor_spin = QtWidgets.QDoubleSpinBox(minimum=1.05, maximum=4.0, singleStep=0.05,
                                                    value=1.3)
        self.factor_spin.setFixedWidth(56)
        self.nlev_spin = QtWidgets.QSpinBox(minimum=1, maximum=40, value=12)
        self.nlev_spin.setFixedWidth(46)
        for w in (self.factor_spin, self.nlev_spin):
            w.setAlignment(Qt.AlignCenter)
            w.valueChanged.connect(self.draw_contours)
        self.neg_check = QtWidgets.QCheckBox("Negative", checked=True)
        self.neg_check.toggled.connect(self.draw_contours)
        legend = hbox(legend_item("+", theme.ACCENT), legend_item("−", theme.NEG), spacing=8)
        self.zoom_btn = button(icon=icon("box", theme.TEXT2), checkable=True,
                               tip="Drag a box to zoom (off: drag to pan)",
                               slot=self._zoom_mode_changed)
        self.zoom_btn.setProperty("kind", "toggle")
        self.zoom_btn.setFixedWidth(34)
        self.zoom_btn.setChecked(True)
        fit = button("Fit  F", slot=self.full_view, tip="Show the whole spectrum (F)")
        head = CardHeader(label("Lowest level", "field"), lvl, self.noise_lbl, 6,
                          label("Factor", "field"), self.factor_spin,
                          label("Levels", "field"), self.nlev_spin, 6,
                          self.neg_check, legend, None, self.zoom_btn, fit)
        card.body.addWidget(head)
        card.body.addWidget(self._build_plane_bar())

        glw = pg.GraphicsLayoutWidget()
        glw.setBackground(theme.CARD)
        glw.ci.setContentsMargins(6, 10, 10, 6)
        self.p2d = glw.addPlot(row=0, col=0)
        self.pv = glw.addPlot(row=0, col=1)     # column slice
        self.ph = glw.addPlot(row=1, col=0)     # row slice
        lay = glw.ci.layout
        lay.setColumnStretchFactor(0, 8)
        lay.setColumnStretchFactor(1, 1)
        lay.setRowStretchFactor(0, 6)
        lay.setRowStretchFactor(1, 1)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(6)

        p = self.p2d
        _style_plot(p)
        p.invertX(True)
        p.invertY(True)
        p.vb.setMouseMode(pg.ViewBox.RectMode)
        p.vb.setBackgroundColor("#FCFCFD")
        self.pos_curve = pg.PlotCurveItem(pen=pg.mkPen(theme.ACCENT, width=1.1), connect="finite")
        self.neg_curve = pg.PlotCurveItem(pen=pg.mkPen(theme.NEG, width=1.1), connect="finite")
        p.addItem(self.pos_curve)
        p.addItem(self.neg_curve)
        dash = pg.mkPen(theme.TEXT2, width=0.8, style=Qt.DashLine)
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=dash)
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=dash)
        p.addItem(self.vline, ignoreBounds=True)
        p.addItem(self.hline, ignoreBounds=True)
        self.pivot_line_2d = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen(theme.PIVOT, width=1.5, style=Qt.DotLine))
        p.addItem(self.pivot_line_2d, ignoreBounds=True)
        self.trace_lines = []

        self.ph.setXLink(p)
        self.ph.invertX(True)
        _style_plot(self.ph, left=False, bottom=False)
        self.ph.setMaximumHeight(110)
        self.hslice = self.ph.plot(pen=pg.mkPen(theme.TEXT, width=1.1))
        self.ph.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(theme.BORDER_STRONG)))

        self.pv.setYLink(p)
        self.pv.invertY(True)
        _style_plot(self.pv, left=False, bottom=False)
        self.pv.setMaximumWidth(130)
        self.vslice = self.pv.plot(pen=pg.mkPen(theme.TEXT, width=1.1))
        self.pv.addItem(pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen(theme.BORDER_STRONG)))

        self.mouse_proxy = pg.SignalProxy(p.scene().sigMouseMoved, rateLimit=60,
                                          slot=self._mouse_moved)
        p.scene().sigMouseClicked.connect(self._mouse_clicked)
        p.vb.sigRangeChanged.connect(self._sync_trace_xrange)
        card.body.addWidget(glw, 1)
        return card

    def _build_plane_bar(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("cardHeader")
        self.orient_seg = Segmented(["", "", ""])
        self.orient_seg.changed.connect(self.set_orientation)
        self.plane_spin = QtWidgets.QSpinBox(minimum=1, maximum=1)
        self.plane_spin.valueChanged.connect(lambda v: self.set_plane(v - 1))
        stepper = Stepper(self.plane_spin, "Previous plane (↓)", "Next plane (↑)",
                          lambda: self.set_plane(self.plane_index - 1),
                          lambda: self.set_plane(self.plane_index + 1), width=86)
        self.plane_slider = QtWidgets.QSlider(Qt.Horizontal, minimum=0, maximum=0)
        self.plane_slider.setMinimumWidth(140)
        self.plane_slider.valueChanged.connect(self.set_plane)
        self.plane_lbl = label("", "mono")
        self.proj_btn = button("Projection", kind="toggle", checkable=True,
                               tip="Show the projection along the stepping axis (P)")
        self.proj_btn.setMinimumHeight(30)
        self.proj_btn.toggled.connect(lambda _: self._projection_changed())
        self.proj_combo = QtWidgets.QComboBox()
        self.proj_combo.addItems(["Skyline", "Sum"])
        self.proj_combo.setToolTip("Skyline keeps the largest |intensity| (with its sign); "
                                   "Sum adds all planes")
        self.proj_combo.currentIndexChanged.connect(lambda _: self._projection_changed())
        bar.setLayout(hbox(label("Planes", "field"), self.orient_seg, 8, label("Plane", "field"),
                           stepper, self.plane_slider, self.plane_lbl, None, self.proj_btn,
                           self.proj_combo, spacing=10, margins=(14, 8, 14, 8)))
        bar.hide()
        self.plane_bar = bar
        return bar

    def _build_traces_card(self):
        card = Card(margins=(0, 0, 0, 0), spacing=0)
        self.traces_title = label("Phase traces", "h3")
        self.legend_box = QtWidgets.QWidget()
        self.legend_box.setLayout(hbox(spacing=14))
        self.gain_lbl = label("×1", "mono")
        gain = Stepper(self.gain_lbl, "Shorter traces", "Taller traces",
                       lambda: self.scale_traces(0.5), lambda: self.scale_traces(2.0), width=48)
        fit = button("Fit", slot=lambda: self.scale_traces(None), tip="Fit the traces to the panel")
        card.body.addWidget(CardHeader(self.traces_title, self.legend_box, None,
                                       label("Height", "field"), gain, fit,
                                       label("or scroll over the traces", "muted")))

        tvb = TraceViewBox()
        tvb.setMouseEnabled(x=False, y=False)
        tvb.wheel.connect(lambda d: self.scale_traces(1.25 if d > 0 else 0.8))
        self.ptrace = pg.PlotWidget(viewBox=tvb, background=theme.CARD)
        _style_plot(self.ptrace.getPlotItem(), left=False)
        self.ptrace.invertX(True)
        self.ptrace.setMenuEnabled(False)
        self.ptrace.setMinimumHeight(150)
        self.pivot_line = pg.InfiniteLine(
            angle=90, movable=True, pen=pg.mkPen(theme.PIVOT, width=2),
            hoverPen=pg.mkPen(theme.PIVOT, width=3),
            label="pivot · {value:.2f}", labelOpts={"position": 0.9, "color": "#FFFFFF",
                                                   "fill": theme.PIVOT, "movable": False})
        self.pivot_line.sigPositionChanged.connect(self._pivot_dragged)
        self.ptrace.addItem(self.pivot_line, ignoreBounds=True)
        self.trace_curves = []
        wrap = QtWidgets.QWidget()
        wrap.setLayout(vbox(self.ptrace, margins=(6, 6, 10, 6)))
        card.body.addWidget(wrap, 1)
        return card

    def _build_phase_card(self):
        card = Card(spacing=14)
        self.preview_check = QtWidgets.QCheckBox("Preview on 2D", checked=True)
        self.preview_check.toggled.connect(self._redraw_after_phase)
        card.body.addLayout(hbox(label("Phase", "h2"), None, self.preview_check))

        self.dim_seg = Segmented(["", ""], kind="dim")
        self.dim_titles = [two_line(b, "X", "direct") for b in self.dim_seg.buttons]
        self.dim_seg.changed.connect(self._dim_changed)
        self.phase_note = label("", "muted")
        self.phase_note.setWordWrap(True)
        self.phase_note.hide()
        card.body.addWidget(self.phase_note)
        self.phase_body = QtWidgets.QWidget()
        body = vbox(self.dim_seg, spacing=14)
        self.phase_body.setLayout(body)
        card.body.addWidget(self.phase_body)

        # Step 1: traces
        self.badges = [StepBadge(i) for i in (1, 2, 3)]
        self.chips_box = QtWidgets.QWidget()
        self.chips = FlowLayout(6)
        self.chips_box.setLayout(self.chips)
        hint = label("Click strong, isolated peaks near both ends of the spectrum.", "muted")
        hint.setWordWrap(True)
        clear = button("Clear all", kind="link", slot=self._clear_traces)
        s1 = vbox(hbox(label("Pick traces", "h3"), None, clear), hint, self.chips_box, spacing=6)

        # Step 2: pivot and P0
        self.pivot_chip = label("pivot –", "pivot")
        piv_btn = button("Set at cursor  V", slot=self.pivot_at_cursor,
                         tip="Put the pivot where the mouse is on the spectrum (V)")
        self.p0_slider = QtWidgets.QSlider(Qt.Horizontal, minimum=-1800, maximum=1800)
        self.p0_spin = self._angle_spin(-720, 720)
        s2 = vbox(label("Pivot and zero order", "h3"),
                  hbox(self.pivot_chip, piv_btn, None, spacing=8),
                  self._phase_row("P0", self.p0_slider, self.p0_spin), spacing=8)

        # Step 3: P1
        self.p1_slider = QtWidgets.QSlider(Qt.Horizontal, minimum=-3600, maximum=3600)
        self.p1_spin = self._angle_spin(-3600, 3600)
        self.fine_btn = button("fine 0.1°", kind="toggle", checkable=True,
                               slot=lambda: self.set_fine(True))
        self.coarse_btn = button("coarse 5°", kind="toggle", checkable=True,
                                 slot=lambda: self.set_fine(False))
        grp = QtWidgets.QButtonGroup(card)
        grp.addButton(self.fine_btn)
        grp.addButton(self.coarse_btn)
        self.fine_btn.setChecked(True)
        reset = button("Reset", kind="link", slot=self.reset_phase, tip="Set P0 and P1 back to 0")
        s3 = vbox(label("First order", "h3"), label("Fix peaks far from the pivot.", "muted"),
                  self._phase_row("P1", self.p1_slider, self.p1_spin),
                  hbox(label("Step", "muted"), self.fine_btn, self.coarse_btn, None, reset,
                       spacing=6), spacing=8)

        for badge, content in zip(self.badges, (s1, s2, s3)):
            row = hbox(spacing=10)
            row.addWidget(badge, 0, Qt.AlignTop)
            row.addLayout(content, 1)
            body.addLayout(row)

        self.p0_slider.valueChanged.connect(lambda v: self.p0_spin.setValue(v / 10))
        self.p1_slider.valueChanged.connect(lambda v: self.p1_spin.setValue(v / 10))
        self.p0_spin.valueChanged.connect(self._phase_edited)
        self.p1_spin.valueChanged.connect(self._phase_edited)
        self.set_fine(True)
        return card

    def _angle_spin(self, lo, hi):
        sp = QtWidgets.QDoubleSpinBox(minimum=lo, maximum=hi, decimals=1, suffix="°")
        sp.setFixedWidth(84)
        sp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return sp

    def _phase_row(self, name, slider, spin):
        lbl = label(name, "mono")
        lbl.setStyleSheet("font-weight: 700;")
        lbl.setFixedWidth(26)
        return hbox(lbl, slider, spin, spacing=8)

    def _build_changes_card(self):
        card = Card(spacing=10)
        self.changes_title = label("Changes to the script", "h2")
        open_script = button("Open script", kind="link", slot=lambda: self.set_mode(SCRIPT))
        card.body.addLayout(hbox(self.changes_title, None, open_script))
        self.changes_note = label("", "muted")
        self.changes_note.setWordWrap(True)
        card.body.addWidget(self.changes_note)

        self.changes_grid = QtWidgets.QGridLayout()
        self.changes_grid.setHorizontalSpacing(10)
        self.changes_grid.setVerticalSpacing(6)
        self.changes_grid.setColumnStretch(0, 1)
        self.change_rows = {}
        card.body.addLayout(self.changes_grid)

        self.write_run_btn = button("Write PS and reprocess", kind="primary",
                                    icon=icon("play", "#FFFFFF", 14),
                                    slot=lambda: self.write_ps(run=True),
                                    tip="Put the new values in the script, save, run and reload")
        self.write_run_btn.setProperty("kind", "primary")
        self.write_run_btn.setMinimumHeight(40)
        card.body.addWidget(self.write_run_btn)
        self.write_btn = button("Write only", slot=lambda: self.write_ps(run=False))
        copy = button("Copy PS line", icon=icon("copy", theme.TEXT), slot=self.copy_ps_line,
                      tip="Copy this dimension's PS line")
        card.body.addLayout(hbox(self.write_btn, copy, spacing=8))
        return card

    def _build_run_card(self):
        card = Card(margins=(14, 12, 14, 12), spacing=0)
        self.run_icon = QtWidgets.QLabel()
        self.run_icon.setFixedSize(28, 28)
        self.run_icon.setAlignment(Qt.AlignCenter)
        self.run_title = label("No runs yet", "h3")
        self.run_sub = label("Run the script from the top bar or with ⌘↩.", "muted")
        self.run_sub.setWordWrap(True)
        log = button("Log", kind="link", slot=lambda: self.set_mode(SCRIPT))
        row = hbox(self.run_icon, vbox(self.run_title, self.run_sub, spacing=2), None, log,
                   spacing=10)
        card.body.addLayout(row)
        self._set_run_icon(None)
        return card

    def _build_footer(self, root):
        bar = QtWidgets.QFrame()
        bar.setObjectName("footer")
        self.cursor_lbl = label("", "mono")
        self.cursor_lbl.setStyleSheet(f"color: {theme.TEXT2};")
        self.script_state_lbl = label("", "muted")
        self.msg_lbl = label("")
        self.msg_lbl.setStyleSheet(f"font-size: 12px; color: {theme.ACCENT_DARK}; font-weight: 600;")
        hint = label("F fit · V pivot · + / − levels · ⌘↩ run", "muted")
        bar.setLayout(hbox(self.cursor_lbl, 12, self.script_state_lbl, 12, self.msg_lbl, None,
                           hint, spacing=8, margins=(16, 6, 16, 6)))
        root.addWidget(bar)

    def _build_menus(self):
        m = self.menuBar().addMenu("&File")
        m.addAction("Open…", QtGui.QKeySequence.Open, self.open_dialog)
        m.addAction("Open Bruker experiment…", QtGui.QKeySequence("Ctrl+Shift+O"),
                    self.open_experiment_dialog)
        m.addAction("Reload", QtGui.QKeySequence("Ctrl+R"), self.reload)
        m.addSeparator()
        m.addAction("Open script…", self.script_panel.open_dialog)
        m.addAction("Save script", QtGui.QKeySequence.Save, self.script_panel.save)
        m.addAction("Run script", QtGui.QKeySequence("Ctrl+Return"), self.script_panel.run)
        m.addSeparator()
        m.addAction("Quit", QtGui.QKeySequence.Quit, self.close)
        m = self.menuBar().addMenu("&View")
        for i, name in enumerate(("Convert", "Process", "View", "Phase", "Export", "Script")):
            m.addAction(name, QtGui.QKeySequence(f"Ctrl+{i + 1}"),
                        lambda i=i: self.set_mode(i))
        m.addSeparator()
        m.addAction("Fit spectrum", QtGui.QKeySequence("Ctrl+0"), self.full_view)
        m.addAction("Header…", QtGui.QKeySequence("Ctrl+I"), self.show_header)

    # ---- modes and status ---------------------------------------------

    def set_mode(self, mode):
        self.mode = mode
        self.mode_seg.set_index(mode)
        if mode == SCRIPT:
            self.pages.setCurrentIndex(2)
        elif mode == CONVERT:
            self.pages.setCurrentIndex(3)
        elif mode == EXPORT:
            self.export_page.load(self.source)
            self.pages.setCurrentIndex(5)
        elif mode == PROCESS:
            if self.process_page.settings is None and self._is_fid(self.source):
                self.process_page.load(self.source.path, self.convert_page.exp)
            self.pages.setCurrentIndex(4)
        elif self.spec is None:
            self.start_page.refresh(self.start_page.folder, self._recent())
            self.pages.setCurrentIndex(0)
        else:
            self.pages.setCurrentIndex(1)
        self._update_crumb()
        phasing = mode == PHASE
        self.traces_card.setVisible(phasing)
        self.inspector.setVisible(phasing)
        for item in [self.pivot_line_2d] + self.trace_lines:
            item.setVisible(phasing)
        if self.spec:
            self._redraw_after_phase()

    def _update_crumb(self):
        """Top bar: the experiment while converting, else the open data."""
        exp = self.convert_page.exp
        if self.mode == CONVERT and exp:
            self.crumb_folder.setText(os.path.basename(os.path.dirname(exp.folder)) + " /")
            self.crumb_file.setText(os.path.basename(exp.folder))
            self.dims_pill.setText(f"{exp.ndim}D Bruker · {exp.pulprog}")
            self.dims_pill.show()
        elif self.source is not None:
            path = self.source.path
            self.crumb_folder.setText(os.path.basename(os.path.dirname(os.path.abspath(path))) + " /")
            self.crumb_file.setText(os.path.basename(path))
            self.dims_pill.setText(self._dims_text)
            self.dims_pill.show()
        else:
            self.crumb_folder.setText("")
            self.crumb_file.setText("No spectrum open")
            self.dims_pill.hide()

    def flash(self, text, ms=6000):
        self.msg_lbl.setText(text)
        self.flash_timer.start(ms)

    def _toggle_auto_reload(self):
        self.auto_reload_on = not self.auto_reload_on
        self._update_reload_indicator()

    def _update_reload_indicator(self):
        if self.script_panel.running:
            self.reload_dot.set_color(theme.WARN)
            self.reload_lbl.setText("Reload paused while running")
        elif self.auto_reload_on:
            self.reload_dot.set_color(theme.OK)
            self.reload_lbl.setText("Auto-reload on")
        else:
            self.reload_dot.set_color(theme.MUTED)
            self.reload_lbl.setText("Auto-reload off")

    def _update_script_state(self):
        sp = self.script_panel
        if sp.path:
            name = os.path.basename(sp.path)
            self.script_state_lbl.setText(f"{name} · {'unsaved changes' if sp.is_modified() else 'saved'}")
            self.run_btn.setText(f"Run {name}   ⌘↩")
            self.run_btn.setEnabled(not sp.running)
            self.run_btn.setToolTip(f"Save and run {sp.path}")
        else:
            self.script_state_lbl.setText("No processing script")
            self.run_btn.setText("Run script   ⌘↩")
            self.run_btn.setEnabled(False)
            self.run_btn.setToolTip("No nmrproc.com or proc.com found. Open one in the Script tab.")

    def _recent(self):
        val = self.settings.value("recent", [])
        return [val] if isinstance(val, str) else list(val or [])

    def _remember(self, path):
        path = os.path.abspath(path)
        recent = [p for p in self._recent() if p != path]
        self.settings.setValue("recent", [path] + recent[:9])

    # ---- file handling ------------------------------------------------

    def open_dialog(self):
        start = os.path.dirname(self.source.path) if self.source else self.start_page.folder
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open NMRPipe data", start,
            "NMRPipe (*.ft *.ft1 *.ft2 *.ft3 *.dat *.fid *.pipe);;All files (*)")
        if path:
            self.open(path)

    @property
    def is_3d(self):
        return isinstance(self.source, volume.Volume)

    def _source_axes(self):
        """Axes of the open data set, direct dimension first."""
        if self.source is None:
            return []
        if self.is_3d:
            return [self.source.axes[2], self.source.axes[1], self.source.axes[0]]
        return [self.source.x, self.source.y]

    def _axis(self, pipe_dim):
        return next(ax for ax in self._source_axes() if ax.pipe_dim == pipe_dim)

    def open_experiment_dialog(self):
        start = self.convert_page.exp.folder if self.convert_page.exp else self.start_page.folder
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Open Bruker experiment (the folder with acqus)", start)
        if folder:
            self.open(folder)

    def open_experiment(self, folder):
        if not self.convert_page.maybe_save():
            return
        try:
            self.convert_page.load(folder)
        except Exception as exc:  # unreadable parameter files
            QtWidgets.QMessageBox.critical(self, "Could not read the experiment",
                                           f"{folder}\n\n{exc}")
            return
        self._remember(folder)
        self.set_mode(CONVERT)

    def _is_fid(self, src):
        if src is None:
            return False
        axes = src.axes if isinstance(src, volume.Volume) else [src.x, src.y]
        return not any(a.is_freq for a in axes)

    def start_processing(self, fid_path):
        """Process step for a converted FID."""
        exp = self.convert_page.exp
        self.process_page.load(fid_path, exp)
        self.set_mode(PROCESS)

    def _choose_fid_for_processing(self):
        start = self.start_page.folder
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose an NMRPipe FID", start, "NMRPipe FID (*.fid *.dat);;All files (*)")
        if path:
            template = volume.series_template(path) if volume.read_header(path)["FDDIMCOUNT"] >= 3 else None
            self.start_processing(template or path)

    def _run_processing(self, script_path):
        sp = self.script_panel
        if sp.running or not sp.maybe_save():
            return
        sp.load(script_path)
        self._after_process = True
        self.process_page.set_running(
            True, "Processing… the spectrum opens for phasing when it finishes. "
                  "NUS reconstruction can take a few minutes.")
        sp.run()

    def _view_converted(self, path):
        self.open(path)
        self.set_mode(VIEW)

    def open(self, path, keep_view=False):
        if os.path.isdir(path):
            if bruker.is_experiment(path):
                self.open_experiment(path)
            else:
                self.start_page.refresh(path, self._recent())
                self.set_mode(VIEW if self.mode == CONVERT else self.mode)
            return
        try:
            src = volume.load(path)
        except Exception as exc:  # show any read error to the user
            QtWidgets.QMessageBox.critical(self, "Could not open", f"{path}\n\n{exc}")
            return
        old = self.source
        same = (keep_view and old is not None and type(src) is type(old)
                and _shape(src) == _shape(old))
        self.source = src
        if not same:
            self.spec = None     # the old view's axes no longer apply; rebuilt below
        if self.watcher.files():
            self.watcher.removePaths(self.watcher.files())
        watch = src.path
        if "%" in src.path:
            # nmrPipe writes the planes in order: the last one changes last.
            files = volume.plane_files(src.path)
            watch = files[-1] if files else None
        if watch:
            self.watcher.addPath(watch)
        self._remember(src.path)
        name = os.path.basename(src.path)
        self.setWindowTitle(f"PipeDraw — {name}")
        self.crumb_folder.setText(os.path.basename(os.path.dirname(os.path.abspath(src.path))) + " /")
        self.crumb_file.setText(name)
        axes = self._source_axes()
        pill = " × ".join(f"{theme.nice_label(a.label)} {a.size}" for a in axes)
        if any(a.is_freq and a.size != a.ftsize for a in axes):
            pill += " · EXT"
        if not any(a.is_freq for a in axes):
            pill += " · FID"
        elif not all(a.is_freq for a in axes):
            pill += " · partly transformed"
        self._dims_text = pill
        self.dims_pill.setText(pill)
        self.dims_pill.show()

        if not same:
            self.dim_state = {a.pipe_dim: PhaseState(pivot=a.offset + a.size // 2) for a in axes}
            self.through, self.proj_kind, self.plane_index = "z", None, 0
            self.proj_btn.blockSignals(True)
            self.proj_btn.setChecked(False)
            self.proj_btn.blockSignals(False)
            if self.is_3d and np.prod(src.shape) <= CUBE_LIMIT:
                with _busy():
                    self.plane_index = src.strongest_plane("z")
        self._setup_plane_bar()
        self._rebuild_changes_rows()
        self._find_script(src.path)
        self._sync_script_values()
        self._show_view(fit=not same, auto=not same)
        if self.mode == CONVERT and not keep_view:
            self.set_mode(VIEW)       # opening a file from Convert shows it
        elif self.mode != CONVERT:
            self.set_mode(self.mode)
        self.flash(f"{name}: {' × '.join(str(a.size) for a in axes)} points", 4000)

    # ---- 3D planes ------------------------------------------------------

    def _setup_plane_bar(self):
        self.plane_bar.setVisible(self.is_3d)
        if not self.is_3d:
            return
        src = self.source
        z, y, x = (theme.nice_label(a.label) for a in src.axes)
        for b, text, step in zip(self.orient_seg.buttons, (f"{x} × {y}", f"{x} × {z}", f"{y} × {z}"),
                                 (z, y, x)):
            b.setText(text)
            b.setToolTip(f"{text} planes, stepping through {step}")
        self.orient_seg.set_index("zyx".index(self.through))
        n = src.shape[volume.VIEWS[self.through][2]]
        for w in (self.plane_spin, self.plane_slider):
            w.blockSignals(True)
        self.plane_spin.setRange(1, n)
        self.plane_spin.setSuffix(f" / {n}")
        self.plane_slider.setRange(0, n - 1)
        self.plane_spin.setValue(self.plane_index + 1)
        self.plane_slider.setValue(self.plane_index)
        for w in (self.plane_spin, self.plane_slider):
            w.blockSignals(False)

    def _show_view(self, fit=False, auto=False):
        """Show the 2D data, or the current plane or projection of 3D data."""
        src = self.source
        if self.is_3d:
            with _busy():
                if self.proj_kind:
                    spec = src.projection(self.through, self.proj_kind)
                else:
                    spec = src.plane(self.through, self.plane_index)
        else:
            spec = src
        self.spec = spec
        self._base_cache.clear()
        xl, yl = theme.nice_label(spec.x.label), theme.nice_label(spec.y.label)
        self.p2d.setLabel("bottom", f"{xl} ({spec.x.unit})")
        self.p2d.setLabel("left", f"{yl} ({spec.y.unit})")
        for (t, s), ax in zip(self.dim_titles, (spec.x, spec.y)):
            t.setText(theme.nice_label(ax.label))
            s.setText(f"F{ax.pipe_dim} · {'direct' if ax.pipe_dim == 2 else 'indirect'}")
        # Frequency axes run high to low ppm; time axes run forwards.
        self.p2d.invertX(spec.x.is_freq)
        self.p2d.invertY(spec.y.is_freq)
        self.ph.invertX(spec.x.is_freq)
        self.pv.invertY(spec.y.is_freq)
        if fit:
            self.cursor_idx = (spec.y.size // 2, spec.x.size // 2)
            self._trace_yrange = None
        else:
            iy, ix = self.cursor_idx
            self.cursor_idx = (min(iy, spec.y.size - 1), min(ix, spec.x.size - 1))
        if auto:
            self.auto_level(draw=False)
        else:
            self.set_level(self.base_level, draw=False)
        self._update_phase_availability()
        self._load_phase_widgets()
        self._update_display()
        self.draw_contours()
        self._update_traces()
        if fit:
            self.full_view()
        self._update_slices()
        self._update_plane_label()

    def _update_plane_label(self):
        if not self.is_3d:
            return
        t_axis = self.source.view_axes(self.through)[2]
        name = theme.nice_label(t_axis.label)
        if self.proj_kind:
            self.plane_lbl.setText(f"{self.proj_kind.capitalize()} projection along {name}")
        else:
            self.plane_lbl.setText(f"{name} {t_axis.coords[self.plane_index]:.2f} {t_axis.unit}")

    def set_orientation(self, i):
        through = "zyx"[i]
        if not self.is_3d or through == self.through:
            return
        self.through = through
        for s in self.dim_state.values():
            s.traces.clear()
        self.plane_index = 0
        if np.prod(self.source.shape) <= CUBE_LIMIT:
            with _busy():
                self.plane_index = self.source.strongest_plane(through)
        self._setup_plane_bar()
        self._show_view(fit=True)

    def set_plane(self, k):
        if not self.is_3d:
            return
        n = self.source.shape[volume.VIEWS[self.through][2]]
        k = int(np.clip(k, 0, n - 1))
        if k == self.plane_index and not self.proj_kind:
            self._setup_plane_bar()
            return
        self.plane_index = k
        self._setup_plane_bar()
        if self.proj_kind:
            self.proj_btn.setChecked(False)   # stepping planes leaves the projection
        else:
            self._show_view()

    def _projection_changed(self):
        if not self.is_3d:
            return
        self.proj_kind = self.proj_combo.currentText().lower() if self.proj_btn.isChecked() else None
        self._show_view()

    def _update_phase_availability(self):
        spec = self.spec
        freq = {1: spec.x.is_freq, 0: spec.y.is_freq}
        any_freq = freq[1] or freq[0]
        for i, a in enumerate((1, 0)):
            self.dim_seg.buttons[i].setEnabled(freq[a])
        if any_freq and not freq[self.active]:
            self.active = 1 if freq[1] else 0
            self.dim_seg.set_index(0 if self.active == 1 else 1)
        if not any_freq:
            note = ("This is time-domain data (an FID). Phasing needs a Fourier-transformed "
                    "dimension: process the data, then open the spectrum to phase it.")
        elif self.proj_kind:
            note = "Phasing works on planes, not projections. Turn off Projection to phase."
        elif not (freq[0] and freq[1]):
            note = "Only the Fourier-transformed dimension can be phased."
        else:
            note = ""
        self.phase_note.setText(note)
        self.phase_note.setVisible(bool(note))
        self.phase_body.setEnabled(any_freq and not self.proj_kind)
        self.mode_seg.buttons[PHASE].setEnabled(any_freq)
        if not any_freq and self.mode == PHASE:
            self._forced_view = True
            self.set_mode(VIEW)
        elif any_freq and self._forced_view and self.mode == VIEW:
            self._forced_view = False
            self.set_mode(PHASE)

    # ---- reloading and running -----------------------------------------

    def _find_script(self, spectrum_path):
        """Load nmrproc.com / proc.com from the spectrum's folder or the
        folder PipeDraw was started in (also the parent, for ft/ folders)."""
        folder = os.path.dirname(os.path.abspath(spectrum_path))
        found = script.find_script(folder, os.getcwd(), os.path.dirname(folder))
        sp = self.script_panel
        if not found or (sp.path and os.path.samefile(found, sp.path)):
            return
        if sp.maybe_save():
            sp.load(found)

    def reload(self, after_run=False):
        if not self.source:
            return
        path = self.source.path
        pending = [d for d, s in self.dim_state.items() if not s.is_identity]
        if pending and (after_run or self.script_panel.path):
            # The script holds the PS values; corrections made on the old
            # data no longer apply to the reprocessed spectrum.
            for d in pending:
                self.dim_state[d].p0 = self.dim_state[d].p1 = 0.0
            self.flash("Spectrum reprocessed: phase corrections reset to 0.", 8000)
        elif pending:
            ans = QtWidgets.QMessageBox.question(
                self, "Reload",
                "The spectrum was reprocessed. Did you put the new PS values in "
                "your script?\n\nYes: they become the script values and the "
                "correction resets.\nNo: keep the current correction.")
            if ans == QtWidgets.QMessageBox.Yes:
                for d in pending:
                    s = self.dim_state[d]
                    s.script_p0, s.script_p1 = self._total_dim(d)
                    s.p0 = s.p1 = 0.0
        self.open(path, keep_view=True)

    def _file_changed(self, path):
        # nmrPipe may replace the file; watch the new one.
        if os.path.exists(path) and path not in self.watcher.files():
            self.watcher.addPath(path)
        # While our own run is writing the file, wait for it to finish.
        if self.auto_reload_on and not self.script_panel.running:
            self.reload_timer.start()

    def _run_started(self):
        self.run_btn.setEnabled(False)
        self.run_btn.setText("Running…")
        self.stop_btn.show()
        self._update_reload_indicator()
        self._set_run_icon("running")
        self.run_title.setText("Running…")
        self.run_sub.setText("The spectrum reloads when the run finishes.")

    def _run_finished(self, code):
        self.stop_btn.hide()
        self._update_script_state()
        self._update_reload_indicator()
        secs = self.script_panel.last_run[1] if self.script_panel.last_run else 0.0
        after_process, self._after_process = self._after_process, False
        if after_process:
            self.process_page.set_running(
                False, "" if code == 0 else f"Processing failed (exit {code}).")
        if code != 0:
            self._set_run_icon("failed")
            self.run_title.setText(f"Last run failed · exit {code}")
            self.run_sub.setText("Open the log to see nmrPipe's message.")
            self.flash(f"Script failed (exit code {code}). See the run log in the Script tab.", 10000)
            if self.spec:
                self._redraw_after_phase()
                self._draw_trace_curves()
            return
        out = script.output_file(self.script_panel.text())
        out_path = (os.path.normpath(os.path.join(os.path.dirname(self.script_panel.path), out))
                    if out else None)
        out_exists = out_path is not None and (
            volume.first_plane(out_path) is not None if "%" in out_path else os.path.exists(out_path))
        src_path = os.path.normpath(os.path.abspath(self.source.path)) if self.source else None
        if out_exists and out_path != src_path:
            self.open(out_path)               # a new spectrum, e.g. after Process
            if after_process:
                self.set_mode(PHASE if not self._is_fid(self.source) else VIEW)
        elif self.source:
            self.reload(after_run=True)
        self._set_run_icon("ok")
        self.run_title.setText(f"Last run finished · {secs:.1f} s")
        shown = os.path.basename(self.source.path) if self.source else "Output"
        self.run_sub.setText(f"{shown} reloaded at {time.strftime('%H:%M')}")
        self.flash("Script finished; spectrum reloaded.", 5000)

    def _set_run_icon(self, state):
        colors = {"ok": (theme.PIVOT_SOFT, theme.PIVOT, "check"),
                  "failed": ("#FDECEA", theme.ERROR, "info"),
                  "running": ("#FFF4E5", theme.WARN, "play"),
                  None: (theme.DIVIDER, theme.MUTED, "play")}
        bg, fg, name = colors[state]
        self.run_icon.setStyleSheet(f"background: {bg}; border-radius: 14px;")
        self.run_icon.setPixmap(icon(name, fg, 16).pixmap(16, 16))

    def closeEvent(self, ev):
        if self.script_panel.maybe_save() and self.convert_page.maybe_save():
            ev.accept()
        else:
            ev.ignore()

    # ---- drawing ------------------------------------------------------

    def auto_level(self, draw=True):
        if not self.spec:
            return
        self.set_level(8 * self.spec.noise_estimate(), draw=draw)

    def set_level(self, value, draw=True):
        self.base_level = max(float(value), 1e-30)
        self.level_edit.setText(f"{self.base_level:.4g}")
        if self.spec:
            self.noise_lbl.setText(f"{self.base_level / self.spec.noise_estimate():.1f} × noise")
        if draw:
            self.draw_contours()

    def step_level(self, direction):
        f = self.factor_spin.value()
        self.set_level(self.base_level * (f if direction > 0 else 1 / f))

    def _level_typed(self):
        try:
            self.set_level(float(self.level_edit.text()))
        except ValueError:
            self.set_level(self.base_level, draw=False)

    def draw_contours(self, *_):
        if self.display is None:
            return
        spec = self.spec
        lv = contours.levels(self.base_level, self.factor_spin.value(), self.nlev_spin.value())
        x, y = contours.contour_paths(self.display, lv, spec.x.coords, spec.y.coords)
        self.pos_curve.setData(x, y)
        if self.neg_check.isChecked():
            x, y = contours.contour_paths(self.display, -lv, spec.x.coords, spec.y.coords)
            self.neg_curve.setData(x, y)
        else:
            self.neg_curve.setData([], [])

    def full_view(self):
        if not self.spec:
            return
        xc, yc = self.spec.x.coords, self.spec.y.coords
        self.p2d.vb.setRange(xRange=(xc.min(), xc.max()), yRange=(yc.min(), yc.max()),
                             padding=0)

    def _zoom_mode_changed(self):
        self.p2d.vb.setMouseMode(pg.ViewBox.RectMode if self.zoom_btn.isChecked()
                                 else pg.ViewBox.PanMode)

    def _update_slices(self):
        if self.display is None:
            return
        iy, ix = self.cursor_idx
        spec = self.spec
        self.hslice.setData(spec.x.coords, self.display[iy])
        self.vslice.setData(self.display[:, ix], spec.y.coords)
        self.vline.setPos(spec.x.coords[ix])
        self.hline.setPos(spec.y.coords[iy])

    def _mouse_moved(self, evt):
        if not self.spec:
            return
        pos = evt[0]
        if not self.p2d.vb.sceneBoundingRect().contains(pos):
            return
        pt = self.p2d.vb.mapSceneToView(pos)
        ix = self.spec.x.index_of(pt.x())
        iy = self.spec.y.index_of(pt.y())
        self.cursor_idx = (iy, ix)
        self._update_slices()
        val = self.display[iy, ix]
        sx, sy = self.spec.x, self.spec.y
        self.cursor_lbl.setText(
            f"{theme.nice_label(sx.label)} {sx.coords[ix]:.3f} · "
            f"{theme.nice_label(sy.label)} {sy.coords[iy]:.2f} · {val:.3g}")

    def _mouse_clicked(self, ev):
        if not self.spec or self.mode != PHASE or ev.button() != Qt.LeftButton:
            return
        if not self.p2d.vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        pt = self.p2d.vb.mapSceneToView(ev.scenePos())
        if self.active == 1:   # phasing X: traces are rows
            idx = self.spec.y.index_of(pt.y())
        else:
            idx = self.spec.x.index_of(pt.x())
        traces = self.state[self.active].traces
        if idx not in traces:
            traces.append(idx)
            self._update_traces()

    def keyPressEvent(self, ev):
        key = ev.text()
        k = ev.key()
        if self.is_3d and k in (Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Down, Qt.Key_PageDown):
            self.set_plane(self.plane_index + (1 if k in (Qt.Key_Up, Qt.Key_PageUp) else -1))
        elif self.is_3d and key.lower() == "p":
            self.proj_btn.toggle()
        elif key in ("+", "="):
            self.step_level(1)
        elif key in ("-", "_"):
            self.step_level(-1)
        elif key.lower() == "v":
            self.pivot_at_cursor()
        elif key.lower() == "f":
            self.full_view()
        else:
            super().keyPressEvent(ev)

    # ---- phasing ------------------------------------------------------

    def _base_complex(self, axis):
        """Complex data along ``axis``, with the other axis already phased."""
        other = 1 - axis
        so = self.state[other]
        key = (axis, None if so.is_identity else so.key())
        if key not in self._base_cache:
            spec = self.spec
            if so.is_identity:
                c = phase.complex_along(spec, axis)
            else:
                real_o = phase.apply(phase.complex_along(spec, other), spec.axes[other],
                                     other, so.p0, so.p1, so.pivot)
                c = phase.analytic(real_o, axis)
            # Keep the cache small: one entry per axis.
            for k in [k for k in self._base_cache if k[0] == axis]:
                del self._base_cache[k]
            self._base_cache[key] = c
        return self._base_cache[key]

    def _update_display(self):
        spec = self.spec
        a = self.active
        sa, so = self.state[a], self.state[1 - a]
        if (self.mode != PHASE or not self.preview_check.isChecked()
                or (sa.is_identity and so.is_identity)):
            self.display = spec.real
        else:
            self.display = phase.apply(self._base_complex(a), spec.axes[a], a,
                                       sa.p0, sa.p1, sa.pivot)

    def _phased_traces(self):
        a = self.active
        s = self.state[a]
        ax = self.spec.axes[a]
        base = self._base_complex(a)
        out = []
        for t in s.traces:
            vec = base[t] if a == 1 else base[:, t]
            out.append(phase.apply(vec, ax, 1, s.p0, s.p1, s.pivot))
        return out

    def _trace_text(self, t):
        other = self.spec.axes[1 - self.active]
        return f"{other.coords[t]:.{1 if np.ptp(other.coords) > 20 else 2}f}"

    def _update_traces(self):
        if not self.spec:
            return
        a = self.active
        s = self.state[a]
        ax, other = self.spec.axes[a], self.spec.axes[1 - a]
        self.ptrace.invertX(ax.is_freq)

        self.chips.clear()
        legend = self.legend_box.layout()
        clear_layout(legend)
        for i, t in enumerate(s.traces):
            color = theme.TRACE_COLORS[i % len(theme.TRACE_COLORS)]
            chip = Chip(self._trace_text(t), color)
            chip.removed.connect(lambda t=t: self._remove_trace(t))
            self.chips.addWidget(chip)
            legend.addWidget(legend_item(f"{theme.nice_label(other.label)} {self._trace_text(t)}",
                                         color))
        self.chips_box.updateGeometry()

        for ln in self.trace_lines:
            self.p2d.removeItem(ln)
        self.trace_lines = []
        pen = pg.mkPen(theme.TRACE_MARK, width=1.5, style=Qt.DashLine)
        for i, t in enumerate(s.traces):
            ln = pg.InfiniteLine(pos=other.coords[t], angle=0 if a == 1 else 90, movable=False,
                                 pen=pen, label=f"trace {i + 1}",
                                 labelOpts={"position": 0.96, "color": "#FFFFFF",
                                            "fill": theme.TRACE_MARK, "movable": False})
            ln.setVisible(self.mode == PHASE)
            self.p2d.addItem(ln, ignoreBounds=True)
            self.trace_lines.append(ln)

        self.pivot_line_2d.setAngle(90 if a == 1 else 0)
        pcoord = ax.coords[int(np.clip(s.pivot - ax.offset, 0, ax.size - 1))]
        self.pivot_line_2d.setPos(pcoord)
        self._placing_pivot = True
        self.pivot_line.setPos(pcoord)
        self._placing_pivot = False
        self.pivot_chip.setText(f"pivot {pcoord:.2f} {ax.unit}")
        self.traces_title.setText(f"Phase traces · {theme.nice_label(ax.label)}")
        self.ptrace.setLabel("bottom", f"{theme.nice_label(ax.label)} ({ax.unit})")
        self._draw_trace_curves(reset_range=True)
        self._update_output()

    def _draw_trace_curves(self, reset_range=False):
        for c in self.trace_curves:
            self.ptrace.removeItem(c)
        self.trace_curves = []
        traces = self._phased_traces()
        if not traces:
            return
        ax = self.spec.axes[self.active]
        # Stack spacing and the Y range come from the unscaled traces, so
        # changing the height scales each trace about its own baseline.
        height = max(np.ptp(t) for t in traces) or 1.0
        step = 1.1 * height if len(traces) > 1 else 0.0
        for i, tr in enumerate(traces):
            color = theme.TRACE_COLORS[i % len(theme.TRACE_COLORS)]
            c = self.ptrace.plot(ax.coords, tr * self.trace_gain + i * step,
                                 pen=pg.mkPen(color, width=1.4))
            self.trace_curves.append(c)
        if reset_range or self._trace_yrange is None:
            lo = min(t.min() for t in traces)
            hi = max(t.max() + i * step for i, t in enumerate(traces))
            self._trace_yrange = (lo - 0.05 * height, hi + 0.12 * height)
        self.ptrace.setYRange(*self._trace_yrange, padding=0)
        if reset_range:
            self._sync_trace_xrange()

    def scale_traces(self, factor):
        """Multiply the trace height by ``factor``; None fits them to the panel."""
        if factor is None:
            self.trace_gain = 1.0
            self._trace_yrange = None
        else:
            self.trace_gain = float(np.clip(self.trace_gain * factor, 1 / 64, 4096))
        g = self.trace_gain
        self.gain_lbl.setText(f"×{g:.3g}" if g >= 1 else f"×1/{1 / g:.3g}")
        if self.spec:
            self._draw_trace_curves(reset_range=factor is None)

    def _sync_trace_xrange(self, *_):
        """Show the same region in the traces as in the 2D view."""
        if not self.spec:
            return
        xr, yr = self.p2d.vb.viewRange()
        self.ptrace.setXRange(*(xr if self.active == 1 else yr), padding=0)

    def set_fine(self, fine):
        self.fine = fine
        (self.fine_btn if fine else self.coarse_btn).setChecked(True)
        step = 0.1 if fine else 5.0
        for spin, slider in ((self.p0_spin, self.p0_slider), (self.p1_spin, self.p1_slider)):
            spin.setSingleStep(step)
            slider.setSingleStep(int(step * 10))
            slider.setPageStep(int(step * 100))

    def _load_phase_widgets(self):
        if not self.spec:
            return
        s = self.state[self.active]
        for w, val in ((self.p0_spin, s.p0), (self.p1_spin, s.p1)):
            w.blockSignals(True)
            w.setValue(val)
            w.blockSignals(False)
        self._sync_sliders()

    def _sync_sliders(self):
        for sl, sp in ((self.p0_slider, self.p0_spin), (self.p1_slider, self.p1_spin)):
            sl.blockSignals(True)
            sl.setValue(int(round(np.clip(sp.value() * 10, sl.minimum(), sl.maximum()))))
            sl.blockSignals(False)

    def _dim_changed(self, i):
        self.active = 1 if i == 0 else 0
        self._load_phase_widgets()
        if self.spec:
            self._update_traces()
            self._redraw_after_phase()

    def _phase_edited(self, *_):
        self._sync_sliders()
        if not self.spec:
            return
        s = self.state[self.active]
        s.p0, s.p1 = self.p0_spin.value(), self.p1_spin.value()
        self._draw_trace_curves()
        self._update_output()
        self.redraw_timer.start()

    def _script_value_typed(self, pipe_dim, which, value):
        """Script values typed by hand (only possible when no script is loaded)."""
        if self.script_panel.path or pipe_dim not in self.dim_state:
            return
        setattr(self.dim_state[pipe_dim], f"script_{which}", value)
        self._update_output()

    # ---- processing script ----------------------------------------------

    def _script_changed(self):
        """Script loaded or edited: re-read its PS values."""
        self._sync_script_values()
        self._load_phase_widgets()
        self._update_output()
        self._update_script_state()

    def _sync_script_values(self):
        if not self.script_panel.path or not self.source:
            return
        text = self.script_panel.text()
        for ax in self._source_axes():
            ps = script.ps_for_dim(text, ax.pipe_dim)
            s = self.dim_state[ax.pipe_dim]
            s.script_p0, s.script_p1 = (ps.p0, ps.p1) if ps else (0.0, 0.0)

    def _push_script_context(self):
        if not self.source:
            return
        labels = {ax.pipe_dim: theme.nice_label(ax.label) for ax in self._source_axes()}
        pending = {d: self._total_dim(d) for d, s in self.dim_state.items() if not s.is_identity}
        self.script_panel.set_context(labels, pending)

    def write_ps(self, run=False):
        """Put the new PS values into the script (and optionally run it)."""
        sp = self.script_panel
        if not self.source:
            return
        if not sp.path:
            QtWidgets.QMessageBox.information(
                self, "No script",
                "No nmrproc.com or proc.com was found next to this spectrum. "
                "Open one in the Script tab.")
            return
        changed = [d for d, s in self.dim_state.items() if not s.is_identity]
        text = sp.text()
        try:
            for d in changed:
                text = script.set_ps(text, d, *self._total_dim(d))
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Could not write PS", str(exc))
            return
        lines = []
        if changed:
            sp.set_text(text)
            for d in changed:
                self.dim_state[d].p0 = self.dim_state[d].p1 = 0.0
                ps = script.ps_for_dim(text, d)
                if ps:
                    lines.append((theme.nice_label(self._axis(d).label), ps.line + 1))
            self._sync_script_values()
            self._load_phase_widgets()
            self._push_script_context()
        if run:
            sp.run()
        else:
            sp.save()
            if lines:
                where = " and ".join(f"{n} (line {ln})" for n, ln in lines)
                sp.show_banner(f"New phase for {where} written to the script. Run to see it.")
            # The spectrum on screen is still the old processing.
            self._redraw_after_phase()
            self._draw_trace_curves()
            self._update_output()
            self.flash("PS written and saved. Press Run to reprocess.", 8000)

    def _redraw_after_phase(self, *_):
        if not self.spec:
            return
        self._update_display()
        self.draw_contours()
        self._update_slices()

    def _set_pivot(self, coord):
        """Move the pivot without changing the applied correction."""
        a = self.active
        s, ax = self.state[a], self.spec.axes[a]
        new = ax.offset + ax.index_of(coord)
        s.p0 = phase.wrap(s.p0 + s.p1 * (new - s.pivot) / ax.ftsize)
        s.pivot = new
        self._load_phase_widgets()
        self._update_traces()

    def _pivot_dragged(self, line):
        if self.spec and not self._placing_pivot:
            self._set_pivot(line.value())

    def pivot_at_cursor(self):
        if not self.spec:
            return
        iy, ix = self.cursor_idx
        ax = self.spec.axes[self.active]
        self._set_pivot(ax.coords[ix if self.active == 1 else iy])

    def reset_phase(self):
        if not self.spec:
            return
        s = self.state[self.active]
        s.p0 = s.p1 = 0.0
        self._load_phase_widgets()
        self._draw_trace_curves()
        self._update_output()
        self._redraw_after_phase()

    def _remove_trace(self, t):
        traces = self.state[self.active].traces
        if t in traces:
            traces.remove(t)
            self._update_traces()

    def _clear_traces(self):
        if self.spec:
            self.state[self.active].traces.clear()
            self._update_traces()

    def _total_dim(self, pipe_dim):
        """PS values for the script: script values plus the correction."""
        s, ax = self.dim_state[pipe_dim], self._axis(pipe_dim)
        d0, d1 = phase.to_pipe(s.p0, s.p1, s.pivot, ax.ftsize)
        return phase.wrap(s.script_p0 + d0), s.script_p1 + d1

    def _total(self, axis):
        """PS values for the displayed X (1) or Y (0) axis."""
        return self._total_dim(self.spec.axes[axis].pipe_dim)

    def _rebuild_changes_rows(self):
        grid = self.changes_grid
        clear_layout(grid)
        self.change_rows = {}
        for c, (h, align) in enumerate((("PS step", Qt.AlignLeft), ("In script", Qt.AlignRight),
                                        ("New", Qt.AlignRight))):
            grid.addWidget(label(h, "field"), 0, c, align)
        r = 1
        for i, ax in enumerate(self._source_axes()):
            if i:
                line = QtWidgets.QFrame()
                line.setFixedHeight(1)
                line.setStyleSheet(f"background: {theme.DIVIDER};")
                grid.addWidget(line, r, 0, 1, 3)
                r += 1
            for which in ("p0", "p1"):
                name = label("")
                old = QtWidgets.QDoubleSpinBox(minimum=-3600, maximum=3600, decimals=1)
                old.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                old.setFixedWidth(80)
                old.valueChanged.connect(
                    lambda v, d=ax.pipe_dim, w=which: self._script_value_typed(d, w, v))
                new = label("", "new")
                new.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                grid.addWidget(name, r, 0)
                grid.addWidget(old, r, 1, Qt.AlignRight)
                grid.addWidget(new, r, 2)
                self.change_rows[(ax.pipe_dim, which)] = (name, old, new)
                r += 1

    def _update_output(self):
        if not self.spec:
            return
        sp = self.script_panel
        has_script = bool(sp.path)
        text = sp.text() if has_script else ""
        name = os.path.basename(sp.path) if has_script else ""
        self.changes_title.setText(f"Changes to {name}" if has_script else "PS values")
        notes = []
        if not has_script:
            notes.append("No processing script found. Enter the PS values the file was "
                         "processed with; copy the new line into your script.")
        for ax in self._source_axes():
            d = ax.pipe_dim
            s = self.dim_state[d]
            ps = script.ps_for_dim(text, d) if has_script else None
            where = f"line {ps.line + 1}" if ps else ("new line" if has_script else f"F{d}")
            if has_script and not ps and ax.is_freq:
                notes.append(f"No PS step for F{d}; one will be added after its FT.")
            t0, t1 = self._total_dim(d)
            for which, old, new in (("p0", s.script_p0, t0), ("p1", s.script_p1, t1)):
                lbl, spin, new_lbl = self.change_rows[(d, which)]
                lbl.setText(f"{theme.nice_label(ax.label)} · {where} · −{which}")
                spin.blockSignals(True)
                spin.setValue(old)
                spin.blockSignals(False)
                if spin.isReadOnly() != has_script:
                    spin.setReadOnly(has_script)
                    restyle(spin)
                new_lbl.setText(script._fmt(new))
                changed = abs(new - old) > 0.05
                if new_lbl.property("changed") != changed:
                    new_lbl.setProperty("changed", changed)
                    restyle(new_lbl)
            if ax.is_freq and ax.size != ax.ftsize:
                notes.append(f"{theme.nice_label(ax.label)} was cut with EXT; the values "
                             "refer to the full width, as PS needs.")
        self.changes_note.setText(" ".join(notes))
        self.changes_note.setVisible(bool(notes))
        pending = any(not s.is_identity for s in self.dim_state.values())
        self.write_run_btn.setEnabled(has_script and pending and not sp.running)
        self.write_btn.setEnabled(has_script and pending)

        s = self.state[self.active]
        done = [bool(s.traces), bool(s.traces) and s.p0 != 0.0, s.p1 != 0.0]
        current = next((i for i, d in enumerate(done) if not d), None)
        for i, badge in enumerate(self.badges):
            badge.set_state("done" if done[i] else ("active" if i == current else "todo"))
        self.outline_timer.start()

    def copy_ps_line(self):
        if self.spec:
            QtWidgets.QApplication.clipboard().setText(phase.ps_line(*self._total(self.active)))
            self.flash("PS line copied", 3000)

    def show_header(self):
        if not self.source:
            return
        dic = self.source.dic
        rows = [f"File: {self.source.path}", f"Dimensions: {int(dic['FDDIMCOUNT'])}",
                f"Transposed: {int(dic['FDTRANSPOSED'])}", ""]
        for ax, name in zip(self._source_axes(), ("X", "Y", "Z")):
            f = f"FDF{ax.pipe_dim}"
            rows.append(f"{name} axis = F{ax.pipe_dim} ({ax.label})")
            for k in ("OBS", "SW", "ORIG", "CAR", "FTSIZE", "TDSIZE", "APOD",
                      "X1", "XN", "QUADFLAG", "FTFLAG", "P0", "P1"):
                if f + k in dic:
                    rows.append(f"  {k:<9}{dic[f + k]}")
            rows.append(f"  points   {ax.size}   range "
                        f"{ax.coords.max():.3f} … {ax.coords.min():.3f} {ax.unit}")
            rows.append("")
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Header")
        lay = QtWidgets.QVBoxLayout(dlg)
        txt = QtWidgets.QPlainTextEdit("\n".join(rows), readOnly=True)
        txt.setFont(theme.mono_font(12))
        lay.addWidget(txt)
        dlg.resize(520, 640)
        dlg.show()

def main(argv=None):
    import sys
    argv = sys.argv if argv is None else argv
    pg.setConfigOptions(background=theme.CARD, foreground=theme.MUTED, antialias=True)
    app = QtWidgets.QApplication(argv)
    app.setApplicationName("PipeDraw")
    app.setOrganizationName("PipeDraw")
    app.setFont(theme.ui_font(13))
    app.setStyleSheet(theme.stylesheet())
    win = MainWindow(argv[1] if len(argv) > 1 else None)
    win.show()
    return app.exec()
