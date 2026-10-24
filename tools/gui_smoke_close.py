"""Offscreen: start on Convert, process an experiment, Close, open the next one."""
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
from pipedraw.app import CONVERT, PHASE, MainWindow

out = sys.argv[1]
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, tempfile.mkdtemp())
pg.setConfigOptions(background=theme.CARD, foreground=theme.MUTED, antialias=True)
app = QtWidgets.QApplication([])
app.setOrganizationName("PipeDraw")
app.setApplicationName("PipeDraw")
app.setFont(theme.ui_font(13))
app.setStyleSheet(theme.stylesheet())
KEEP = ("acqus", "acqu2s", "acqu3s", "acqu", "acqu2", "acqu3", "ser", "pulseprogram", "nuslist")


def pump(n=5):
    for _ in range(n):
        app.processEvents()


def wait_for(cond, limit):
    t = time.time()
    while not cond() and time.time() - t < limit:
        pump(1)
        time.sleep(0.05)
    pump(10)


project = tempfile.mkdtemp()
for name, src in (("4.hsqc", "testdata/2D/4.hsqc"), ("HNCA", "testdata/3D/HNCA")):
    os.makedirs(os.path.join(project, name))
    for n in KEEP:
        if os.path.isfile(os.path.join(ROOT, src, n)):
            shutil.copy(os.path.join(ROOT, src, n), os.path.join(project, name))
os.chdir(project)

win = MainWindow()
win.resize(1500, 950)
win.show()
pump()
print("start: mode Convert:", win.mode == CONVERT, "| overview shown:", win.pages.currentIndex() == 0,
      "| close button:", win.close_btn.isVisible())
win.grab().save(os.path.join(out, "close_a_start.png"))

win.open(os.path.join(project, "4.hsqc"))
cp = win.convert_page
cp.convert()
wait_for(lambda: not cp.runner.running and cp.status_lbl.text() != "Converting", 120)
cp._process()
pp = win.process_page
wait_for(lambda: pp.proc is None and pp.generated, 60)
pp.process()
wait_for(lambda: not win.script_panel.running and not win._after_process, 120)
print("processed: mode Phase:", win.mode == PHASE, "| close button:", win.close_btn.isVisible())

win.close_dataset()
pump()
print("closed: source:", win.source, "| experiment:", cp.exp, "| process:", pp.settings,
      "| script:", win.script_panel.path, "| mode Convert:", win.mode == CONVERT,
      "| overview:", win.pages.currentIndex() == 0, "| folder:", os.path.basename(win.start_page.folder) == os.path.basename(project),
      "| close button:", win.close_btn.isVisible())
win.grab().save(os.path.join(out, "close_b_after.png"))

win.open(os.path.join(project, "HNCA"))
pump()
print("next: convert page:", win.pages.currentIndex() == 3, "| experiment:", cp.exp.pulprog,
      "| crumb:", win.crumb_file.text())
