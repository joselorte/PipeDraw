"""Offscreen: Auto phase on a copy of the HSQC processed with all phases at 0."""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from pipedraw import bruker, procgen, theme, volume
from pipedraw.app import PHASE, MainWindow

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


def wait_for(cond, limit):
    t = time.time()
    while not cond() and time.time() - t < limit:
        pump(1)
        time.sleep(0.05)
    pump(10)


work = os.path.join(tempfile.mkdtemp(), "4.hsqc")
os.makedirs(work)
for n in ("acqus", "acqu2s", "ser", "pulseprogram"):
    shutil.copy(os.path.join(ROOT, "testdata", "2D", "4.hsqc", n), work)
open(os.path.join(work, "fid.com"), "w").write(bruker.auto_fid_com(work)[0])
subprocess.run(["csh", "fid.com"], cwd=work, check=True, capture_output=True)
s = procgen.defaults(os.path.join(work, "test.fid"))
subprocess.run(["csh", "-c", procgen.command(s, os.path.join(work, "nmrproc.com"))], cwd=work,
               check=True, capture_output=True)
subprocess.run(["csh", "nmrproc.com"], cwd=work, check=True, capture_output=True)


def positive(path):
    r = volume.load(path).real
    big = np.abs(r) > np.abs(r).max() * 0.1
    return (r[big] > 0).mean()


print("before: strong points positive %.0f%%" % (100 * positive(os.path.join(work, "test.ft2"))))
win = MainWindow()
win.resize(1500, 950)
win.show()
win.open(os.path.join(work, "test.ft2"))
pump()
print("mode phase:", win.mode == PHASE, "| script:", os.path.basename(win.script_panel.path or ""))

win.dim_seg.set_index(0)
win._dim_changed(0)
win.auto_phase()                                   # 1H: NMRPipe, asynchronous
wait_for(lambda: win._ap_proc is None, 120)
print("1H:", win.auto_lbl.text(), "| total", np.round(win._total_dim(2), 1))
win.dim_seg.set_index(1)
win._dim_changed(1)
win.auto_phase()                                   # 15N: peak fit
pump()
print("15N:", win.auto_lbl.text(), "| total", np.round(win._total_dim(1), 1))
win.grab().save(os.path.join(out, "autophase_hsqc.png"))
win.write_ps(run=True)
wait_for(lambda: not win.script_panel.running, 120)
print("after Write PS and reprocess: strong points positive %.0f%%" % (100 * positive(os.path.join(work, "test.ft2"))),
      "| script PS:", [l.strip() for l in open(os.path.join(work, "nmrproc.com")) if "-fn PS" in l])
win.grab().save(os.path.join(out, "autophase_hsqc_after.png"))
