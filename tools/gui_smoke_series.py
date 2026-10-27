"""Offscreen: the Series tab on a shifted copy series of the HSQC."""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from pipedraw import bruker, procgen, theme
from pipedraw.app import SERIES, VIEW, MainWindow
import make_test_series

out = sys.argv[1]
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, tempfile.mkdtemp())
pg.setConfigOptions(background=theme.CARD, foreground=theme.MUTED, antialias=True)
app = QtWidgets.QApplication([])
app.setOrganizationName("PipeDraw")
app.setApplicationName("PipeDraw")
app.setFont(theme.ui_font(13))
app.setStyleSheet(theme.stylesheet())


def pump(n=5):
    for _ in range(n):
        app.processEvents()


def wait_idle(page, limit=600):
    t = time.time()
    while page.busy and time.time() - t < limit:
        pump(1)
        time.sleep(0.05)
    pump(10)
    return time.time() - t


proj = os.path.join(tempfile.mkdtemp(), "titration")
points = make_test_series.make(os.path.join(ROOT, "testdata", "2D", "4.hsqc"), proj, 6)
t = points[0]                                           # template, set up as a user would
open(os.path.join(t, "fid.com"), "w").write(bruker.auto_fid_com(t)[0])
subprocess.run(["csh", "fid.com"], cwd=t, check=True, capture_output=True)
s = procgen.defaults(os.path.join(t, "test.fid"))
s.dims["x"].p0, s.dims["y"].p0, s.dims["y"].p1 = 13.0, 188.6, -10.0
subprocess.run(["csh", "-c", procgen.command(s, os.path.join(t, "nmrproc.com"))], cwd=t,
               check=True, capture_output=True)
subprocess.run(["csh", "nmrproc.com"], cwd=t, check=True, capture_output=True)

os.chdir(proj)
win = MainWindow()
win.resize(1500, 950)
win.show()
pump()
print("start page offers a series:", win.start_page.series_btn.isVisible())
win.start_page._series()
sp = win.series_page
pump()
print("series tab:", win.mode == SERIES, "| points:", [p.name for p in sp.points],
      "| template:", os.path.basename(sp.template), sp.template_state.text())
for r, v in enumerate(["0 eq", "0.25 eq", "0.5 eq", "1 eq", "2 eq", "4 eq"]):
    sp.table.item(r, 3).setText(v)
sp.jobs.setValue(3)
sp.run()
secs = wait_idle(sp)
print(f"run: {secs:.1f} s |", [(p.name, p.status) for p in sp.points])
win.grab().save(os.path.join(out, "series_run.png"))
sp.check_phases()
secs = wait_idle(sp)
print(f"phase check: {secs:.1f} s |", [(p.name, {k: round(v, 1) for k, v in p.phase_dev.items()}) for p in sp.points])
pump()
win.grab().save(os.path.join(out, "series_checked.png"))
sp.overlay()
pump(10)
print("overlay:", win.mode == VIEW, "| curves:", len(win.overlay_curves), "|", win.series_lbl.text())
win.full_view()
win.p2d.vb.setRange(xRange=(8.6, 7.6), yRange=(126, 116), padding=0)
pump(10)
win.grab().save(os.path.join(out, "series_overlay.png"))
win.series_step(1)
pump()
print("stepped:", win.series_lbl.text(), "| source:", os.path.relpath(win.source.path, proj))
sp.export_all()
wait_idle(sp)
print("exported:", sorted(f for f in os.listdir(points[3]) if f.endswith((".ucsf", ".nv"))),
      "| series.com:", os.path.exists(os.path.join(proj, "series.com")))
