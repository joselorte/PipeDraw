"""Drive PipeDraw offscreen and save screenshots of each screen."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from pipedraw import phase, theme
from pipedraw.app import PHASE, SCRIPT, VIEW, MainWindow

out = sys.argv[1]
# Keep the user's real recent-files list untouched.
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, tempfile.mkdtemp())
pg.setConfigOptions(background=theme.CARD, foreground=theme.MUTED, antialias=True)
app = QtWidgets.QApplication([])
app.setOrganizationName("PipeDraw")
app.setApplicationName("PipeDraw")
app.setFont(theme.ui_font(13))
app.setStyleSheet(theme.stylesheet())


def shot(win, name):
    for _ in range(5):
        app.processEvents()
    win.grab().save(os.path.join(out, name))


os.chdir(os.path.join(ROOT, "demo"))
win = MainWindow()
win.show()
shot(win, "a_start.png")

win.open("test.ft2")
spec = win.spec
iy, ix = np.unravel_index(np.argmax(np.abs(spec.real)), spec.real.shape)
win.state[1].traces += [int(iy), int(np.argsort(np.abs(spec.real).max(1))[-8])]
win._set_pivot(spec.x.coords[ix])
win._update_traces()
s = win.state[1]
win.p1_spin.setValue(60.0)
win.p0_spin.setValue(round(-40.0 + 60.0 * s.pivot / spec.x.ftsize, 1))
win._redraw_after_phase()
win.scale_traces(2.0)
shot(win, "b_phase.png")
print("X totals:", win._total(1), "script:", (s.script_p0, s.script_p1))

win.dim_seg.set_index(1)
win._dim_changed(1)
win.state[0].traces += [int(ix)]
win._update_traces()
win.p0_spin.setValue(-25.0)
win._redraw_after_phase()
shot(win, "c_phase_y.png")
print("Y totals:", win._total(0))

win._push_script_context()
win.set_mode(SCRIPT)
shot(win, "d_script.png")
win.set_mode(VIEW)
shot(win, "e_view.png")
win.set_mode(PHASE)
win.script_panel.editor.document().setModified(False)
