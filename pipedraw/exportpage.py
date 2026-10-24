"""Export step: write the spectrum for other programs and open it there."""
import os

from PySide6 import QtCore, QtWidgets

from . import export, theme, volume
from .widgets import Card, button, clear_layout, hbox, icon, label, vbox

Qt = QtCore.Qt

FORMATS = (
    ("ucsf", "Sparky (.ucsf)", ".ucsf",
     "For Sparky, POKY and CCPN. Written by PipeDraw and read back to check the data "
     "and ppm scales; Sparky's own pipe2ucsf is used instead when it is installed."),
    ("nv", "NMRView (.nv)", ".nv",
     "For NMRViewJ and NMRFx. Written by NMRPipe's pipe2xyz -nv."),
    ("single", "Single-file NMRPipe (3D)", ".ft3",
     "The plane series as one NMRPipe file, for programs that want a 3D spectrum in "
     "one file. Written by xyz2pipe | pipe2xyz."),
)


class ExportPage(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self.folder = None
        self.is_3d = False
        self.rows = {}
        self.apps = export.find_apps()

        self.stack = QtWidgets.QStackedLayout(self)
        empty = QtWidgets.QWidget()
        card = Card(margins=(28, 28, 28, 28), spacing=10)
        card.setMaximumWidth(520)
        card.body.addWidget(label("Export a spectrum", "h2"))
        note = label("Open or process a spectrum first. Export writes it as Sparky and "
                     "NMRView files and opens it in NMRFx, NMRViewJ or CCPN.", "muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        empty.setLayout(vbox(None, hbox(None, card, None), None, margins=(16, 16, 16, 16)))
        self.stack.addWidget(empty)

        page = QtWidgets.QWidget()
        outer = hbox(spacing=0, margins=(16, 16, 16, 16))
        inner = QtWidgets.QWidget()
        inner.setMaximumWidth(980)
        outer.addStretch(1)
        outer.addWidget(inner, 100)
        outer.addStretch(1)
        page.setLayout(outer)
        col = vbox(spacing=12)
        inner.setLayout(col)

        self.spec_card = Card(spacing=6)
        self.spec_title = label("", "h2")
        self.spec_sub = label("", "muted")
        self.spec_sub.setWordWrap(True)
        self.spec_card.body.addWidget(self.spec_title)
        self.spec_card.body.addWidget(self.spec_sub)
        col.addWidget(self.spec_card)

        files = Card(spacing=12)
        files.body.addWidget(label("Files", "h2"))
        self.folder_lbl = label("", "muted")
        files.body.addWidget(self.folder_lbl)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        r = 0
        for key, title, ext, desc in FORMATS:
            name = QtWidgets.QLineEdit()
            name.setMinimumWidth(220)
            status = label("", "muted")
            status.setWordWrap(True)
            go = button("Export", slot=lambda _=False, k=key: self.export(k))
            d = label(desc, "muted")
            d.setWordWrap(True)
            grid.addWidget(label(title, "h3"), r, 0)
            grid.addWidget(name, r, 1)
            grid.addWidget(go, r, 2)
            grid.addWidget(d, r + 1, 0, 1, 3)
            grid.addWidget(status, r + 2, 0, 1, 3)
            spacer = QtWidgets.QFrame()
            spacer.setFixedHeight(8)
            grid.addWidget(spacer, r + 3, 0)
            self.rows[key] = {"name": name, "status": status, "button": go, "ext": ext,
                              "widgets": [grid.itemAtPosition(r, 0).widget(), name, go, d, status]}
            r += 4
        grid.setColumnStretch(1, 1)
        files.body.addLayout(grid)
        self.all_btn = button("Export all", kind="primary", slot=self.export_all)
        files.body.addWidget(self.all_btn, 0, Qt.AlignLeft)
        col.addWidget(files)

        apps = Card(spacing=10)
        apps.body.addWidget(label("Open in", "h2"))
        self.apps_box = vbox(spacing=10)
        apps.body.addLayout(self.apps_box)
        col.addWidget(apps)
        col.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.stack.addWidget(scroll)

    # ---- loading --------------------------------------------------------

    def load(self, source):
        """Show export options for the open data (Spectrum or Volume)."""
        if source is None:
            self.stack.setCurrentIndex(0)
            return
        axes = (list(reversed(source.axes)) if isinstance(source, volume.Volume)
                else [source.x, source.y])
        if not all(a.is_freq for a in axes):
            self.path = None
            self.stack.setCurrentIndex(0)
            return
        if source.path == self.path:
            self._refresh_status()
            self.stack.setCurrentIndex(1)
            return
        self.path = source.path
        self.is_3d = isinstance(source, volume.Volume)
        self.folder = export.work_folder(source.path)
        base = export.default_name(source.path)
        nuclei = " × ".join(theme.nice_label(a.label) for a in axes)
        self.spec_title.setText(f"{len(axes)}D spectrum · {nuclei}")
        self.spec_sub.setText(os.path.relpath(source.path, os.path.dirname(self.folder)))
        self.folder_lbl.setText(f"Written to {self.folder}")
        for key, row in self.rows.items():
            row["name"].setText(base + ("3d" if key == "single" else "") + row["ext"])
            row["status"].setText("")
            row["status"].setStyleSheet("")
            row["status"].setProperty("fresh", False)
            for w in row["widgets"]:
                w.setVisible(key != "single" or self.is_3d)
        self._refresh_status()
        self._fill_apps()
        self.stack.setCurrentIndex(1)

    def out_path(self, key):
        return os.path.join(self.folder, self.rows[key]["name"].text().strip())

    def _refresh_status(self):
        for key, row in self.rows.items():
            p = self.out_path(key)
            if os.path.exists(p) and not row["status"].property("fresh"):
                row["status"].setText(f"{os.path.basename(p)} exists "
                                      f"({os.path.getsize(p) / 1e6:.1f} MB). Export to replace it.")

    # ---- exporting ----------------------------------------------------------

    def export(self, key) -> bool:
        if not self.path:
            return False
        row = self.rows[key]
        out = self.out_path(key)
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if key == "ucsf":
                report = export.write_ucsf(self.path, out)
            elif key == "nv":
                export.run_csh(export.nv_command(self._rel(self.path), self._rel(out)),
                               self.folder, out)
                report = "Written with pipe2xyz -nv."
            else:
                export.run_csh(export.single_file_command(self._rel(self.path), self._rel(out)),
                               self.folder, out)
                volume.load(out)   # check it reads as one 3D file
                report = "Written with xyz2pipe | pipe2xyz; reads as one 3D file."
        except Exception as exc:  # show the tool's message
            QtWidgets.QApplication.restoreOverrideCursor()
            row["status"].setText(f"Failed: {exc}")
            row["status"].setStyleSheet(f"color: {theme.ERROR};")
            return False
        QtWidgets.QApplication.restoreOverrideCursor()
        row["status"].setStyleSheet(f"color: {theme.PIVOT_TEXT};")
        row["status"].setText(f"✓ {os.path.basename(out)} · {os.path.getsize(out) / 1e6:.1f} MB · {report}")
        row["status"].setProperty("fresh", True)
        self._fill_apps()
        return True

    def export_all(self):
        for key in self.rows:
            if key != "single" or self.is_3d:
                self.export(key)

    def _rel(self, path):
        return os.path.relpath(os.path.abspath(path), self.folder)

    # ---- other programs ---------------------------------------------------------

    def _candidates(self, formats):
        """Files this program can read, best first, that exist."""
        found = []
        for fmt in formats:
            if fmt == "pipe":
                p = self.out_path("single") if self.is_3d else self.path
            else:
                key = "ucsf" if fmt == ".ucsf" else "nv"
                p = self.out_path(key)
            if p and os.path.exists(p):
                found.append(p)
        return found

    def _fill_apps(self):
        clear_layout(self.apps_box)
        if not self.apps:
            self.apps_box.addWidget(label("NMRFx, NMRViewJ and CCPN weren't found in "
                                          "/Applications.", "muted"))
            return
        for name, (app, version) in self.apps.items():
            info = export.APPS[name]
            files = self._candidates(info["formats"])
            combo = QtWidgets.QComboBox()
            for f in files:
                combo.addItem(os.path.basename(f), f)
            go = button("Open", icon=icon("play", theme.TEXT, 12), slot=lambda _=False, n=name, c=combo:
                        self._open(n, c.currentData()))
            go.setEnabled(bool(files))
            wants = " or ".join(f if f != "pipe" else "NMRPipe" for f in info["formats"])
            if not files:
                hint = f"Export a {wants} file first."
            elif files and any(f.endswith(info["direct"]) for f in files if info["direct"]):
                hint = "Opens the file directly."
            else:
                hint = ("Starts the program and shows the file in Finder (its path is copied): "
                        "drag it onto the program's window, or use its Open command.")
            text = label(hint, "muted")
            text.setWordWrap(True)
            title = label(f"{name}", "h3")
            ver = label(version, "muted")
            self.apps_box.addLayout(hbox(vbox(hbox(title, ver, None, spacing=6), text, spacing=2),
                                         combo, go, spacing=10))

    def _open(self, name, file_path):
        if not file_path:
            return
        app, _ = self.apps[name]
        direct = file_path.endswith(export.APPS[name]["direct"]) if export.APPS[name]["direct"] else False
        if not direct:
            QtWidgets.QApplication.clipboard().setText(file_path)
        export.open_in(app, file_path, direct)
