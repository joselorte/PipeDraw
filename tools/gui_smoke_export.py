"""Offscreen: the Export tab on a copy of demo3d (writes files, opens no programs)."""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from pipedraw import theme
from pipedraw.app import EXPORT, MainWindow

out = sys.argv[1]
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, tempfile.mkdtemp())
pg.setConfigOptions(background=theme.CARD, foreground=theme.MUTED, antialias=True)
app = QtWidgets.QApplication([])
app.setOrganizationName("PipeDraw")
app.setApplicationName("PipeDraw")
app.setFont(theme.ui_font(13))
app.setStyleSheet(theme.stylesheet())

work = os.path.join(tempfile.mkdtemp(), "HNCA")
shutil.copytree(os.path.join(ROOT, "demo3d", "ft"), os.path.join(work, "ft"))
win = MainWindow()
win.resize(1500, 950)
win.show()
win.open(os.path.join(work, "ft", "test001.ft3"))
win.set_mode(EXPORT)
ep = win.export_page
ep.export_all()
for _ in range(10):
    app.processEvents()
print({k: r["status"].text()[:90] for k, r in ep.rows.items()})
print(sorted(os.listdir(work)))
win.grab().save(os.path.join(out, "export_tab.png"))
