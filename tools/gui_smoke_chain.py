"""Offscreen: Convert -> Process -> Phase on copies of the Bruker test data."""
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from pipedraw import theme
from pipedraw.app import PHASE, PROCESS, MainWindow

out = sys.argv[1]
which = sys.argv[2:] or ["hsqc", "hnca"]
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, tempfile.mkdtemp())
pg.setConfigOptions(background=theme.CARD, foreground=theme.MUTED, antialias=True)
app = QtWidgets.QApplication([])
app.setOrganizationName("PipeDraw")
app.setApplicationName("PipeDraw")
app.setFont(theme.ui_font(13))
app.setStyleSheet(theme.stylesheet())
KEEP = ("acqus", "acqu2s", "acqu3s", "acqu", "acqu2", "acqu3", "ser", "pulseprogram", "nuslist")
SOURCES = {"hsqc": "testdata/2D/4.hsqc", "hnca": "testdata/3D/HNCA"}


def pump(n=5):
    for _ in range(n):
        app.processEvents()


def wait_for(cond, limit):
    t = time.time()
    while not cond() and time.time() - t < limit:
        pump(1)
        time.sleep(0.05)
    pump(10)
    return time.time() - t


win = MainWindow()
win.resize(1500, 950)
win.show()
for name in which:
    src = os.path.join(ROOT, SOURCES[name])
    work = os.path.join(tempfile.mkdtemp(), os.path.basename(src))
    os.makedirs(work)
    for n in KEEP:
        if os.path.isfile(os.path.join(src, n)):
            shutil.copy(os.path.join(src, n), work)
    win.open(work)
    cp = win.convert_page
    pump()
    for chk_fix in [("zMODE", "States-TPPI")] if name == "hnca" else []:
        cp._edit(*chk_fix)
    cp.convert()
    wait_for(lambda: not cp.runner.running and cp.status_lbl.text() != "Converting", 300)
    print(name, "converted:", cp.status_lbl.text(), "| next button:", cp.next_btn.isVisible())
    cp._process()
    pp = win.process_page
    wait_for(lambda: pp.proc is None and pp.generated, 60)
    print(name, "process tab:", win.mode == PROCESS, "| preview lines:", len(pp.generated.splitlines()),
          "| out:", pp.settings.out, "| nus:", pp.settings.nus)
    win.grab().save(os.path.join(out, f"chain_{name}_process.png"))
    pp.process()
    secs = wait_for(lambda: not win.script_panel.running and not win._after_process, 900)
    print(name, f"processed in {secs:.0f} s | mode phase:", win.mode == PHASE,
          "| source:", os.path.relpath(win.source.path, work), "| script:",
          os.path.basename(win.script_panel.path or ""), "| PS dims:",
          sorted(win.dim_state), "| run:", win.run_title.text())
    win.grab().save(os.path.join(out, f"chain_{name}_phase.png"))
