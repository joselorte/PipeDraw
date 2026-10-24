"""Offscreen: Convert page on copies of testdata/2D/4.hsqc and testdata/3D/HNCA."""
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
from pipedraw.app import CONVERT, VIEW, MainWindow

out = sys.argv[1]
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, tempfile.mkdtemp())
pg.setConfigOptions(background=theme.CARD, foreground=theme.MUTED, antialias=True)
app = QtWidgets.QApplication([])
app.setOrganizationName("PipeDraw")
app.setApplicationName("PipeDraw")
app.setFont(theme.ui_font(13))
app.setStyleSheet(theme.stylesheet())
KEEP = ("acqus", "acqu2s", "acqu3s", "acqu", "acqu2", "acqu3", "ser", "pulseprogram",
        "nuslist", "audita.txt")


def pump(n=5):
    for _ in range(n):
        app.processEvents()


def copy(src):
    dst = os.path.join(tempfile.mkdtemp(), os.path.basename(src))
    os.makedirs(dst)
    for n in KEEP:
        if os.path.isfile(os.path.join(src, n)):
            shutil.copy(os.path.join(src, n), dst)
    return dst


def wait(page):
    t = time.time()
    while (page.runner.running or time.time() - t < 0.5) and time.time() - t < 120:
        pump(1)
        time.sleep(0.05)
    pump(10)


win = MainWindow()
win.resize(1500, 950)
win.show()
cp = win.convert_page

hsqc = copy(os.path.join(ROOT, "testdata", "2D", "4.hsqc"))
win.open(hsqc)
pump()
print("mode convert:", win.mode == CONVERT, "| state:", cp.state_lbl.text(),
      "| checks:", cp.check_list.count(), "| yCAR field:", cp.fields["yCAR"].text())
win.grab().save(os.path.join(out, "conv_a_hsqc.png"))
cp.convert()
wait(cp)
print("hsqc converted:", cp.status_lbl.text(), os.path.exists(os.path.join(hsqc, "test.fid")))
cp._view()
pump()
print("viewing:", win.mode == VIEW, os.path.basename(win.source.path), win.spec.real.shape)
win.grab().save(os.path.join(out, "conv_b_hsqc_fid.png"))

hnca = copy(os.path.join(ROOT, "testdata", "3D", "HNCA"))
win.open(hnca)
pump()
print("hnca checks text:", [cp.check_list.itemAt(i).layout().itemAt(1).widget().text()[:60]
                            for i in range(cp.check_list.count()) if cp.check_list.itemAt(i).layout()])
win.grab().save(os.path.join(out, "conv_c_hnca.png"))
cp._edit("zMODE", "States-TPPI")           # the check's fix button
pump()
print("after fix zMODE:", cp.fid_script.dims["z"]["MODE"], "| checks left:",
      sum(1 for i in range(cp.check_list.count()) if cp.check_list.itemAt(i).layout()))
cp.use_water_referencing()
pump()
print("water-referenced CARs:", [cp.fid_script.dims[d]["CAR"] for d in "xyz"])
cp.convert()
wait(cp)
print("hnca converted:", cp.status_lbl.text(), "| output:", cp.output_path())
win.grab().save(os.path.join(out, "conv_d_hnca_done.png"))
cp._view()
pump()
print("viewing 3D FID:", type(win.source).__name__, win.mode == VIEW)
