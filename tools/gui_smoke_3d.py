"""Offscreen: 3D planes, orientation, projection, 13C phasing + write & run; 2D FID view."""
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from pipedraw import theme, volume
from pipedraw.app import PHASE, VIEW, MainWindow

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


def shot(win, name):
    pump()
    win.grab().save(os.path.join(out, name))


work = os.path.join(tempfile.mkdtemp(), "demo3d")
shutil.copytree(os.path.join(ROOT, "demo3d"), work)
os.chdir(work)
win = MainWindow()
win.show()
shot(win, "f_start_3d.png")

win.open("ft/test001.ft3")
print("source:", type(win.source).__name__, win.source.path, "plane", win.plane_index + 1,
      "dims in table:", sorted({k[0] for k in win.change_rows}))
print("script:", os.path.basename(win.script_panel.path or "none"),
      {d: (s.script_p0, s.script_p1) for d, s in win.dim_state.items()})
shot(win, "g_3d_xy.png")

win.orient_seg.set_index(1)
win.set_orientation(1)                     # 1H x 13C planes
spec = win.spec
print("XZ plane:", spec.x.label, spec.y.label, "plane", win.plane_index + 1)
iy, ix = np.unravel_index(np.argmax(np.abs(spec.real)), spec.real.shape)
win.dim_seg.set_index(1)
win._dim_changed(1)                        # phase the vertical (13C) axis
win.state[0].traces += [int(ix)]
win._update_traces()
win.p0_spin.setValue(-30.0)
win._redraw_after_phase()
shot(win, "h_3d_phase_13c.png")
print("13C total:", win._total_dim(3))

win.proj_btn.setChecked(True)
shot(win, "i_3d_projection.png")
print("phase body enabled in projection:", win.phase_body.isEnabled())
win.proj_btn.setChecked(False)

win.write_ps(run=True)
t = time.time()
while (win.script_panel.running or time.time() - t < 1.0) and time.time() - t < 60:
    pump(1)
    time.sleep(0.05)
pump(20)
print("after run:", [l.strip() for l in open("nmrproc.com") if "-fn PS" in l][-1],
      "13C delta:", win.dim_state[3].p0)
shot(win, "j_3d_after_run.png")

win.open(os.path.join(ROOT, "demo", "test.fid"))
print("FID mode:", "VIEW" if win.mode == VIEW else win.mode, "phase enabled:",
      win.mode_seg.buttons[PHASE].isEnabled(), "x inverted:", win.p2d.vb.state["xInverted"])
shot(win, "k_fid.png")
win.open(os.path.join(ROOT, "demo", "test.ft2"))
print("back to spectrum mode:", "PHASE" if win.mode == PHASE else win.mode)
win.script_panel.editor.document().setModified(False)
