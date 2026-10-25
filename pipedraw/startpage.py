"""Start page shown when no spectrum is open."""
import os
import shutil
import subprocess

import nmrglue as ng
from PySide6 import QtCore, QtWidgets

from . import bruker, script, theme, volume
from .widgets import Card, CardHeader, Dot, button, clear_layout, hbox, icon, label, restyle, vbox

Qt = QtCore.Qt

SPECTRUM_EXTS = (".ft", ".ft1", ".ft2", ".ft3", ".ft4", ".fid", ".dat", ".pipe")


def describe(path):
    """(what it is, size text) for an NMRPipe file, from its header only."""
    try:
        dic = ng.fileio.pipe.fdata2dic(ng.fileio.pipe.get_fdata(path))
    except Exception:  # unreadable or not NMRPipe
        return None
    ndim = int(dic["FDDIMCOUNT"])
    order = [int(dic[f"FDDIMORDER{i}"]) for i in range(1, ndim + 1)]
    labels = [theme.nice_label(str(dic.get(f"FDF{d}LABEL", "")).strip() or f"F{d}") for d in order]
    if ndim >= 2 and order[0] == 1 and order[1] == 2:   # transposed: show direct first
        labels[0], labels[1] = labels[1], labels[0]
    freq = [bool(dic.get(f"FDF{d}FTFLAG", 0)) for d in order[:ndim]]
    kind = "spectrum" if all(freq) else ("time domain" if not any(freq) else "partly transformed")
    what = f"{ndim}D {kind} · " + " × ".join(labels[:ndim])
    sizes = [int(dic["FDSIZE"]), int(dic["FDSPECNUM"])]
    if ndim >= 2 and order[0] == 1 and order[1] == 2:
        sizes.reverse()
    if ndim >= 3:
        sizes.append(int(dic.get(f"FDF{order[2]}SIZE", 0)))
    return what, " × ".join(str(s) for s in sizes)


def _exists(path):
    return volume.first_plane(path) is not None if "%" in path else os.path.exists(path)


def _scan(folder, max_subfolders=12):
    """(shown name, path, (what, size)) for the NMRPipe data in ``folder`` and
    its subfolders. A plane series (test001.ft3, test002.ft3, …) is one entry."""
    entries = []

    def experiment(dirpath, shown):
        try:
            e = bruker.load_experiment(dirpath)
        except Exception:  # damaged parameter files
            return
        nuclei = " × ".join(theme.nice_label(d.nucleus) for d in e.dims)
        what = f"Bruker experiment · {e.pulprog}" + (" · NUS" if e.nus else "")
        entries.append((shown, dirpath, (what, f"{e.ndim}D · {nuclei}")))

    def scan(dirpath, prefix):
        if bruker.is_experiment(dirpath):
            experiment(dirpath, (prefix or os.path.basename(os.path.abspath(dirpath))) + "")
        try:
            names = sorted(os.listdir(dirpath))
        except OSError:
            return
        files = [n for n in names if n.lower().endswith(SPECTRUM_EXTS)
                 and os.path.isfile(os.path.join(dirpath, n))]
        series = {}
        for n in files:
            m = volume._SERIES.match(n)
            if m:
                series.setdefault((m.group(1), len(m.group(2)), m.group(3)), []).append(n)
        grouped = set()
        for (stem, width, ext), members in series.items():
            if len(members) < 2:
                continue
            info = describe(os.path.join(dirpath, members[0]))
            if info and info[0].startswith("3D"):
                template = f"{stem}%0{width}d{ext}"
                entries.append((prefix + template, os.path.join(dirpath, template),
                                (info[0], f"{info[1]} · {len(members)} planes")))
                grouped.update(members)
        for n in files:
            if n not in grouped:
                info = describe(os.path.join(dirpath, n))
                if info:
                    entries.append((prefix + n, os.path.join(dirpath, n), info))

    scan(folder, "")
    try:
        subs = [d for d in sorted(os.listdir(folder))
                if not d.startswith(".") and os.path.isdir(os.path.join(folder, d))]
    except OSError:
        subs = []
    for d in subs[:max_subfolders]:
        scan(os.path.join(folder, d), d + "/")
        # Bruker data often sits one level deeper (project/2D/4.hsqc).
        sub = os.path.join(folder, d)
        try:
            inner = sorted(n for n in os.listdir(sub) if not n.startswith("."))
        except OSError:
            continue
        for n in inner[:40]:
            path = os.path.join(sub, n)
            if os.path.isdir(path) and bruker.is_experiment(path):
                experiment(path, f"{d}/{n}/")
    return entries


class DropZone(QtWidgets.QFrame):
    dropped = QtCore.Signal(str)
    clicked = QtCore.Signal()          # choose a file
    folder_clicked = QtCore.Signal()   # choose an experiment folder

    def __init__(self):
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(180)
        ic = QtWidgets.QLabel()
        ic.setPixmap(icon("upload", theme.ACCENT, 24).pixmap(24, 24))
        ic.setFixedSize(48, 48)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background: {theme.ACCENT_SOFT}; border-radius: 12px;")
        t = label("Drop an experiment folder or a file", "h3")
        t.setStyleSheet("font-size: 15px;")
        t.setToolTip("A Bruker experiment (the folder with acqus), an NMRPipe FID or a spectrum")
        link = f"style='color:{theme.ACCENT}; font-weight:600'"
        s = QtWidgets.QLabel(f"<a href='folder' {link}>Choose folder…</a> ⇧⌘O"
                             f" &nbsp;·&nbsp; <a href='file' {link}>Choose file…</a> ⌘O")
        s.linkActivated.connect(lambda which: (self.folder_clicked if which == "folder"
                                               else self.clicked).emit())
        lay = vbox(None, ic, t, s, None, spacing=10)
        for w in (ic, t, s):
            lay.setAlignment(w, Qt.AlignHCenter)
        self.setLayout(lay)

    def mousePressEvent(self, ev):
        self.folder_clicked.emit()

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, ev):
        self._hover(False)

    def dropEvent(self, ev):
        self._hover(False)
        urls = ev.mimeData().urls()
        if urls:
            self.dropped.emit(urls[0].toLocalFile())

    def _hover(self, on):
        self.setProperty("hover", on)
        restyle(self)


class StartPage(QtWidgets.QWidget):
    open_requested = QtCore.Signal(str)
    browse_requested = QtCore.Signal()
    browse_folder_requested = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.folder = os.getcwd()
        outer = hbox(spacing=0, margins=(16, 40, 16, 16))
        page = QtWidgets.QWidget()
        page.setMaximumWidth(1120)
        outer.addStretch(1)
        outer.addWidget(page, 100)
        outer.addStretch(1)
        self.setLayout(outer)

        main = vbox(spacing=20)
        main.addWidget(label("Open data", "h1"))
        self.drop = DropZone()
        self.drop.dropped.connect(self._dropped)
        self.drop.clicked.connect(self.browse_requested)
        self.drop.folder_clicked.connect(self.browse_folder_requested)
        main.addWidget(self.drop)

        self.files_card = Card(margins=(0, 0, 0, 6), spacing=0)
        self.folder_lbl = label("", "mono")
        self.folder_lbl.setStyleSheet(f"color: {theme.MUTED};")
        self.files_card.body.addWidget(CardHeader(label("In this folder", "h3"), self.folder_lbl, None))
        self.files_grid = QtWidgets.QGridLayout()
        self.files_grid.setContentsMargins(16, 6, 16, 6)
        self.files_grid.setHorizontalSpacing(24)
        self.files_grid.setVerticalSpacing(10)
        self.files_card.body.addLayout(self.files_grid)
        main.addWidget(self.files_card)
        main.addStretch(1)

        side = vbox(spacing=16)
        self.recent_card = Card(spacing=8)
        self.recent_card.body.addWidget(label("Recent", "h3"))
        self.recent_list = vbox(spacing=6)
        self.recent_card.body.addLayout(self.recent_list)
        side.addWidget(self.recent_card)

        # Only shown when something is wrong.
        self.status_card = Card(spacing=8)
        self.pipe_dot = Dot(theme.WARN)
        self.pipe_lbl = label("nmrPipe not found")
        self.pipe_lbl.setToolTip("PipeDraw runs NMRPipe through csh, which reads ~/.cshrc. "
                                 "Set NMRPipe up there to convert and process.")
        self.status_card.body.addLayout(hbox(self.pipe_dot, self.pipe_lbl, None))
        self.status_card.hide()
        side.addWidget(self.status_card)
        side.addStretch(1)

        cols = hbox(spacing=20)
        cols.addLayout(main, 3)
        side_w = QtWidgets.QWidget()
        side_w.setLayout(side)
        side_w.setFixedWidth(320)
        cols.addWidget(side_w)
        page.setLayout(cols)
        QtCore.QTimer.singleShot(0, self._check_nmrpipe)

    def refresh(self, folder, recent):
        self.folder = folder
        self.folder_lbl.setText(os.path.basename(folder.rstrip("/")) + "/")
        grid = self.files_grid
        clear_layout(grid)
        for c, h in enumerate(("File", "What it is", "Size", "")):
            grid.addWidget(label(h, "field"), 0, c)
        script_path = script.find_script(folder)
        entries = _scan(folder)
        # The first processed spectrum gets the main Open button.
        primary = next((e[1] for e in entries if "spectrum" in e[2][0]), None)
        rows = 0
        if script_path:
            ps = script.parse_ps(open(script_path).read())
            dims = ", ".join(f"F{p.pipe_dim}" for p in ps)
            tag = label("✓ Loads with the spectrum")
            tag.setStyleSheet(f"background: {theme.PIVOT_SOFT}; color: {theme.PIVOT_TEXT}; "
                              "border-radius: 10px; padding: 3px 10px; font-size: 12px; "
                              "font-weight: 600;")
            entries.insert(0, (os.path.basename(script_path), None,
                               (f"Processing script · PS for {dims or 'none'}",
                                f"{len(open(script_path).read().splitlines())} lines"), tag))
        for entry in entries[:40]:
            shown, path, (what, size) = entry[:3]
            if path is None:
                action = entry[3]
            else:
                action = button("Convert" if os.path.isdir(path) else "Open",
                                kind="primary" if path == primary else None,
                                slot=lambda _=False, p=path: self.open_requested.emit(p))
            rows += 1
            for c, cell in enumerate((shown, what, size, action)):
                if isinstance(cell, str):
                    cell = label(cell, "mono" if c in (0, 2) else None)
                    if c == 0:
                        cell.setStyleSheet("font-weight: 600;")
                    elif c == 1:
                        cell.setWordWrap(True)
                grid.addWidget(cell, rows, c, Qt.AlignRight if c == 3 else Qt.AlignLeft)
        if rows == 0:
            grid.addWidget(label("No NMRPipe data here", "muted"), 1, 0, 1, 4)
        grid.setColumnStretch(1, 1)

        lay = self.recent_list
        clear_layout(lay)
        shown = [p for p in recent if _exists(p)][:6]
        for p in shown:
            b = button(kind="outline", slot=lambda _=False, p=p: self.open_requested.emit(p))
            name = label(os.path.join(os.path.basename(os.path.dirname(p)), os.path.basename(p)),
                         "mono")
            if "%" in p:
                name.setText(name.text() + "  (3D)")
            name.setStyleSheet("font-weight: 600;")
            name.setAttribute(Qt.WA_TransparentForMouseEvents)
            b.setLayout(hbox(name, None, margins=(10, 6, 10, 6)))
            b.setMinimumHeight(34)
            b.setToolTip(p)
            lay.addWidget(b)
        if not shown:
            lay.addWidget(label("None yet", "muted"))

    def _dropped(self, path):
        if os.path.isdir(path) and bruker.is_experiment(path):
            self.open_requested.emit(path)
        elif os.path.isdir(path):
            self.refresh(path, [])
        else:
            self.open_requested.emit(path)

    def _check_nmrpipe(self):
        found = None
        if shutil.which("csh"):
            r = subprocess.run(["csh", "-c", "which nmrPipe"], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip().startswith("/"):
                found = r.stdout.strip()
        self.status_card.setVisible(not found)
