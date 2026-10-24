"""Offscreen check of the script loop: open from the data folder, phase, write PS & run."""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

import shutil
import subprocess

from pipedraw import script
from pipedraw.app import MainWindow
from pipedraw.spectrum import Spectrum

out = sys.argv[1]
# Work on a copy of demo/, with a correctly processed reference beside it.
work = os.path.join(tempfile.mkdtemp(), "demo")
shutil.copytree(os.path.join(ROOT, "demo"), work)
os.chdir(work)
proc = open("nmrproc.com").read()
good = script.set_ps(script.set_ps(proc, 2, 0, 0), 1, 0, 0).replace("test.ft2", "good.ft2")
open("good.com", "w").write(good)
subprocess.run(["csh", "good.com"], check=True, capture_output=True)
subprocess.run(["csh", "nmrproc.com"], check=True, capture_output=True)
pg.setConfigOptions(background="w", foreground="#374151", antialias=False)
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, tempfile.mkdtemp())
app = QtWidgets.QApplication([])
app.setOrganizationName("PipeDraw")
app.setApplicationName("PipeDraw")
win = MainWindow("test.ft2")
win.show()
app.processEvents()
sp = win.script_panel
print("script:", os.path.basename(sp.path or "none"))
print("X:", win.spec.x.label, "Y:", win.spec.y.label)
print("script PS X/Y:", (win.state[1].script_p0, win.state[1].script_p1),
      (win.state[0].script_p0, win.state[0].script_p1))

spec = win.spec
iy, ix = np.unravel_index(np.argmax(np.abs(spec.real)), spec.real.shape)
win.state[1].traces += [int(iy)]
win._set_pivot(spec.x.coords[ix])
win._update_traces()
s = win.state[1]
win.p1_spin.setValue(60.0)
win.p0_spin.setValue(round(-40.0 + 60.0 * s.pivot / spec.x.ftsize, 1))
win.scale_traces(4.0)
win.dim_seg.set_index(1); win._dim_changed(1)
win.state[0].traces += [int(ix)]
win._update_traces()
win.p0_spin.setValue(-25.0)
win.dim_seg.set_index(0); win._dim_changed(0)
app.processEvents()
print("new PS totals:", win._total(1), win._total(0))
win.grab().save(os.path.join(out, "4_before_run.png"))

win.write_ps(run=True)
t = time.time()
while (sp.running or time.time() - t < 1.0) and time.time() - t < 30:
    app.processEvents()
    time.sleep(0.05)
for _ in range(20):
    app.processEvents()
    time.sleep(0.05)
print("log tail:", sp.log.toPlainText().strip().splitlines()[-1])
ps_lines = [l.strip() for l in open("nmrproc.com") if "-fn PS" in l]
print("script now:", ps_lines)
a = Spectrum.load("test.ft2").real
b = Spectrum.load("good.ft2").real
print("reprocessed vs correctly processed r =", np.corrcoef(a.ravel(), b.ravel())[0, 1])
print("deltas after run:", win.state[1].p0, win.state[1].p1, win.state[0].p0)
win.grab().save(os.path.join(out, "5_after_run.png"))
