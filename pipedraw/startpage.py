"""Start page shown when no spectrum is open."""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

import nmrglue as ng
from PySide6 import QtCore, QtGui, QtWidgets

from . import bruker, pseudo, script, theme, volume
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


def _natural(name):
    """Sort key putting experiment 2 before 10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


# ---- what a folder holds ------------------------------------------------------

SCRIPTS = ("fid.com", "nmrproc.com", "proc.com", "ft2.com", "proc2d.com", "series.com")
EXPORTS = (".ucsf", ".nv")


@dataclass
class Node:
    """One row of the folder tree."""
    kind: str                   # folder, experiment, file, script, export
    name: str
    path: str
    what: str = ""
    size: str = ""
    status: str = ""            # experiments: processed, converted, raw
    open_path: Optional[str] = None     # what Open opens (a processed spectrum, a file)
    experiments: int = 0        # folders: experiments directly inside
    planes: int = 0             # experiments: 2D planes to split out of one file


def _listdir(path):
    try:
        return sorted((n for n in os.listdir(path) if not n.startswith(".")), key=_natural)
    except OSError:
        return []


def has_data(path, depth=3, budget=None):
    """Whether NMR data (a Bruker experiment or an NMRPipe file) is in ``path``
    or within ``depth`` folders below it. Looks at a bounded number of entries."""
    budget = budget if budget is not None else [3000]
    if bruker.is_experiment(path):
        return True
    for n in _listdir(path):
        budget[0] -= 1
        if budget[0] < 0:
            return False
        full = os.path.join(path, n)
        if os.path.isdir(full):
            if depth > 0 and has_data(full, depth - 1, budget):
                return True
        elif n.lower().endswith(SPECTRUM_EXTS):
            return True
    return False


def _output(folder, script_name):
    path = os.path.join(folder, script_name)
    if not os.path.isfile(path):
        return None
    try:
        out = script.output_file(open(path).read())
    except OSError:
        return None
    out = out and os.path.normpath(os.path.join(folder, out))
    return out if out and _exists(out) else None


def experiment_node(path, name=None) -> Node:
    """A Bruker experiment: its type and how far it has been processed."""
    name = name or os.path.basename(os.path.abspath(path))
    node = Node("experiment", name, path)
    try:
        e = bruker.load_experiment(path)
        node.what = e.pulprog + (" · NUS" if e.nus else "")
        node.size = f"{e.ndim}D · " + " × ".join(theme.nice_label(d.nucleus) for d in e.dims)
        lay = pseudo.layout(path)
        if lay:
            node.planes = lay.planes
            node.what = f"{e.pulprog} · {pseudo.summary(lay)}"
            node.size = "2D · " + " × ".join(theme.nice_label(d.nucleus) for d in e.dims[:2])
    except Exception:  # damaged parameter files: still listed
        node.what = "Bruker experiment (parameters unreadable)"
    if node.planes:
        node.status = "split" if pseudo.is_split(path) else "planes"
        return node
    spectrum = next((o for o in (_output(path, n) for n in SCRIPTS[1:5]) if o), None)
    if spectrum:
        node.status, node.open_path = "processed", spectrum
    elif _output(path, "fid.com"):
        node.status = "converted"
    else:
        node.status = "raw"
    return node


def pipe_files(dirpath, prefix=""):
    """NMRPipe files in one folder. A plane series (test001.ft3, test002.ft3, …)
    is one entry."""
    entries = []
    files = [n for n in _listdir(dirpath) if n.lower().endswith(SPECTRUM_EXTS)
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
            path = os.path.join(dirpath, template)
            entries.append(Node("file", prefix + template, path, info[0],
                                f"{info[1]} · {len(members)} planes", open_path=path))
            grouped.update(members)
    for n in files:
        if n not in grouped:
            info = describe(os.path.join(dirpath, n))
            if info:
                path = os.path.join(dirpath, n)
                entries.append(Node("file", prefix + n, path, *info, open_path=path))
    return entries


def experiment_contents(path):
    """What PipeDraw and NMRPipe made in an experiment: scripts, FIDs and
    spectra (also in fid/, ft/ and the like), and exported files."""
    nodes = []
    for n in SCRIPTS:
        if os.path.isfile(os.path.join(path, n)):
            nodes.append(Node("script", n, os.path.join(path, n), "Script"))
    nodes += pipe_files(path)
    for n in _listdir(path):
        sub = os.path.join(path, n)
        if n == pseudo.SPLIT_FOLDER and pseudo.is_split(path):
            planes = len(pseudo.read_values(sub))
            nodes.append(Node("folder", n, sub, f"{planes} planes, as 2D experiments",
                              experiments=planes))
        elif os.path.isdir(sub) and n not in ("pdata",):
            nodes += pipe_files(sub, n + "/")
    for n in _listdir(path):
        if n.lower().endswith(EXPORTS):
            size = os.path.getsize(os.path.join(path, n)) / 1e6
            nodes.append(Node("export", n, os.path.join(path, n),
                              "Sparky" if n.lower().endswith(".ucsf") else "NMRView",
                              f"{size:.1f} MB"))
    return nodes


def list_folder(folder):
    """The rows under ``folder``: experiments, folders that hold NMR data
    (expandable), and NMRPipe files, in natural order."""
    nodes, files = [], []
    for n in _listdir(folder):
        path = os.path.join(folder, n)
        if not os.path.isdir(path):
            continue
        if bruker.is_experiment(path):
            nodes.append(experiment_node(path))
        elif has_data(path):
            inside = sum(bruker.is_experiment(os.path.join(path, m)) for m in _listdir(path))
            nodes.append(Node("folder", n + "/", path, experiments=inside,
                              what=f"{inside} experiment{'s' if inside != 1 else ''}"
                              if inside else "Folder"))
    files = pipe_files(folder)
    return nodes + files


class DropZone(QtWidgets.QFrame):
    dropped = QtCore.Signal(str)
    clicked = QtCore.Signal()          # choose a file
    folder_clicked = QtCore.Signal()   # choose an experiment folder

    def __init__(self):
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(140)
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


STATUS = {"processed": ("✓ Processed", "PIVOT_TEXT"), "converted": ("Converted", "ACCENT"),
          "raw": ("Not converted", "MUTED"), "planes": ("Not split", "MUTED"),
          "split": ("Split", "ACCENT")}
COLS = ("Name", "What it is", "Dimensions", "Status", "")
C_NAME, C_WHAT, C_SIZE, C_STATUS, C_ACTION = range(len(COLS))


class FolderTree(QtWidgets.QTreeWidget):
    """The folder's NMR data as a tree. Folders and experiments expand to show
    what is inside them; their contents are read when first expanded."""
    open_requested = QtCore.Signal(str)
    series_requested = QtCore.Signal(str)
    split_requested = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.setColumnCount(len(COLS))
        self.setHeaderLabels(COLS)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.setIndentation(18)
        self.setMinimumHeight(560)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        hh = self.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(C_NAME, QtWidgets.QHeaderView.Interactive)
        hh.setSectionResizeMode(C_WHAT, QtWidgets.QHeaderView.Stretch)
        for c in (C_SIZE, C_STATUS, C_ACTION):
            hh.setSectionResizeMode(c, QtWidgets.QHeaderView.ResizeToContents)
        self.setColumnWidth(C_NAME, 230)
        self.setStyleSheet(
            f"QTreeWidget {{ border: 0; background: {theme.CARD}; }}"
            f"QTreeWidget::item {{ height: 32px; border-bottom: 1px solid {theme.DIVIDER}; }}"
            f"QTreeWidget::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}"
            f"QHeaderView::section {{ background: {theme.CARD}; border: 0;"
            f" border-bottom: 1px solid {theme.DIVIDER}; padding: 6px;"
            f" font-weight: 600; color: {theme.TEXT2}; }}")
        self.itemExpanded.connect(self._expanded)
        self.itemDoubleClicked.connect(self._double_clicked)

    def show_folder(self, folder, extra=()):
        """List ``folder``. ``extra`` rows (e.g. its processing script) go first."""
        self.clear()
        nodes = list(extra) + list_folder(folder)
        self._add(self.invisibleRootItem(), nodes)
        # Data often sits one level down (project/2D/4.hsqc): open those folders.
        folders = [self.topLevelItem(i) for i in range(self.topLevelItemCount())
                   if self.topLevelItem(i).data(0, Qt.UserRole).kind == "folder"]
        if folders and len(folders) <= 3 and len(folders) == len(nodes) - len(extra):
            for item in folders:
                item.setExpanded(True)
        return nodes

    def _add(self, parent, nodes):
        for node in nodes:
            item = QtWidgets.QTreeWidgetItem(parent)
            item.setData(0, Qt.UserRole, node)
            item.setText(C_NAME, node.name)
            item.setText(C_WHAT, node.what)
            item.setText(C_SIZE, node.size)
            item.setToolTip(C_NAME, node.path)
            item.setFont(C_NAME, theme.mono_font(12))
            if node.kind in ("folder", "experiment"):
                f = item.font(C_NAME)
                f.setBold(True)
                item.setFont(C_NAME, f)
                if node.status not in ("raw", "planes"):    # untouched: nothing to show
                    item.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ShowIndicator)
            if node.kind == "folder":
                item.setIcon(C_NAME, icon("folder", theme.MUTED, 14))
            if node.kind in ("script", "export"):
                for c in (C_NAME, C_WHAT, C_SIZE):
                    item.setForeground(c, QtGui.QColor(theme.MUTED))
            if node.status:
                text, colour = STATUS[node.status]
                item.setText(C_STATUS, text)
                item.setForeground(C_STATUS, QtGui.QColor(getattr(theme, colour)))
            if node.kind == "script" and getattr(node, "tag", None):
                self.setItemWidget(item, C_STATUS, node.tag)
            actions = self._actions(node)
            if actions:
                cell = QtWidgets.QWidget()
                cell.setLayout(hbox(None, *actions, spacing=6, margins=(0, 0, 6, 0)))
                self.setItemWidget(item, C_ACTION, cell)

    def _actions(self, node):
        def small(text, slot, primary=False, tip=None):
            b = button(text, kind="toggle", slot=lambda _=False: slot(), tip=tip)
            if primary:
                b.setStyleSheet(f"background: {theme.ACCENT}; color: white; border: 0;"
                                " font-weight: 600;")
            return b
        out = []
        if node.kind == "folder" and node.experiments >= 2:
            out.append(small("Series…", lambda: self.series_requested.emit(node.path),
                             tip="Process these experiments (e.g. a titration) with one "
                                 "experiment's recipe"))
        elif node.kind == "experiment" and node.planes:
            done = node.status == "split"
            out.append(small("Series…" if done else "Split…",
                             lambda: self.split_requested.emit(node.path), primary=True,
                             tip="Process the planes in the Series tab" if done else
                                 "Write each plane as a 2D experiment in split/ and process "
                                 "them in the Series tab. The original is not changed."))
        elif node.kind == "experiment":
            out.append(small("Convert", lambda: self.open_requested.emit(node.path),
                             primary=not node.open_path,
                             tip="Convert to NMRPipe (fid.com), then process"))
            if node.open_path:
                out.append(small("Open", lambda: self.open_requested.emit(node.open_path),
                                 primary=True, tip=os.path.relpath(node.open_path, node.path)))
        elif node.kind == "file":
            out.append(small("Open", lambda: self.open_requested.emit(node.open_path)))
        return out

    def _expanded(self, item):
        node = item.data(0, Qt.UserRole)
        if item.childCount() or node.kind not in ("folder", "experiment"):
            return
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            kids = (experiment_contents(node.path) if node.kind == "experiment"
                    else list_folder(node.path))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if kids:
            self._add(item, kids)
        else:
            item.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.DontShowIndicator)

    def _double_clicked(self, item, _column):
        node = item.data(0, Qt.UserRole)
        if node.kind == "experiment" and node.planes:
            self.split_requested.emit(node.path)
        elif node.kind == "experiment":
            self.open_requested.emit(node.open_path or node.path)
        elif node.kind == "file":
            self.open_requested.emit(node.open_path)

    def filter(self, text):
        """Show rows whose name, type or dimensions contain ``text`` (and their
        folders). Only rows already read are searched."""
        text = text.strip().lower()

        def visit(item):
            node = item.data(0, Qt.UserRole)
            hit = not text or any(text in v.lower() for v in (node.name, node.what, node.size))
            kids = [visit(item.child(i)) for i in range(item.childCount())]
            show = hit or any(kids)
            item.setHidden(not show)
            return show
        for i in range(self.topLevelItemCount()):
            visit(self.topLevelItem(i))


class StartPage(QtWidgets.QWidget):
    open_requested = QtCore.Signal(str)
    browse_requested = QtCore.Signal()
    browse_folder_requested = QtCore.Signal()
    series_requested = QtCore.Signal(str)     # folder holding a series of experiments
    split_requested = QtCore.Signal(str)      # pseudo-3D experiment to split into 2D planes

    def __init__(self):
        super().__init__()
        self.folder = os.getcwd()
        outer = hbox(spacing=0, margins=(16, 40, 16, 16))
        page = QtWidgets.QWidget()
        page.setMaximumWidth(1120)
        outer.addStretch(1)
        outer.addWidget(page, 100)
        outer.addStretch(1)
        content = QtWidgets.QWidget()
        content.setLayout(outer)
        # Long folders scroll instead of running off the window.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setLayout(vbox(scroll))

        main = vbox(spacing=20)
        main.addWidget(label("Open data", "h1"))
        self.drop = DropZone()
        self.drop.dropped.connect(self._dropped)
        self.drop.clicked.connect(self.browse_requested)
        self.drop.folder_clicked.connect(self.browse_folder_requested)
        main.addWidget(self.drop)

        self.files_card = Card(margins=(0, 0, 0, 0), spacing=0)
        self.folder_lbl = label("", "mono")
        self.folder_lbl.setStyleSheet(f"color: {theme.MUTED};")
        self.up_btn = button("", icon=icon("up", theme.TEXT, 14), slot=self._up,
                             tip="Parent folder")
        self.up_btn.setFixedWidth(32)
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter (name, pulse program, nucleus)")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setFixedWidth(250)
        self.series_btn = button("Process as a series…", slot=self._series,
                                 tip="Process these experiments (e.g. a titration) with one "
                                     "experiment's recipe")
        self.series_btn.hide()
        self.files_card.body.addWidget(CardHeader(self.up_btn, label("In this folder", "h3"),
                                                  self.folder_lbl, None, self.filter_edit,
                                                  self.series_btn))
        self.tree = FolderTree()
        self.tree.open_requested.connect(self.open_requested)
        self.tree.series_requested.connect(self.series_requested)
        self.tree.split_requested.connect(self.split_requested)
        self.filter_edit.textChanged.connect(self.tree.filter)
        self.files_card.body.addWidget(self.tree)
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
        self._recent = recent
        self.folder_lbl.setToolTip(folder)
        self.up_btn.setEnabled(os.path.dirname(os.path.abspath(folder)) != os.path.abspath(folder))
        extra = []
        script_path = script.find_script(folder)
        if script_path and os.path.dirname(os.path.abspath(script_path)) == os.path.abspath(folder):
            text = open(script_path).read()
            dims = ", ".join(f"F{p.pipe_dim}" for p in script.parse_ps(text))
            node = Node("script", os.path.basename(script_path), script_path,
                        f"Processing script · PS for {dims or 'none'}",
                        f"{len(text.splitlines())} lines")
            tag = label("✓ Loads with the spectrum")
            tag.setStyleSheet(f"background: {theme.PIVOT_SOFT}; color: {theme.PIVOT_TEXT}; "
                              "border-radius: 10px; padding: 3px 10px; font-size: 12px; "
                              "font-weight: 600;")
            node.tag = tag
            extra.append(node)
        nodes = self.tree.show_folder(folder, extra)
        self.filter_edit.clear()
        if not nodes:
            item = QtWidgets.QTreeWidgetItem(self.tree, ["No NMR data here"])
            item.setData(0, Qt.UserRole, Node("none", "", folder))
            item.setForeground(0, QtGui.QColor(theme.MUTED))
        # Offer a series where this folder holds several experiments.
        n_exp = sum(n.kind == "experiment" for n in nodes)
        self._series_folder = folder if n_exp >= 2 else None
        self.series_btn.setVisible(self._series_folder is not None)

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

    def _up(self):
        self.refresh(os.path.dirname(os.path.abspath(self.folder)), getattr(self, "_recent", []))

    def _series(self):
        if self._series_folder:
            self.series_requested.emit(self._series_folder)

    def _dropped(self, path):
        if os.path.isdir(path) and bruker.is_experiment(path):
            self.open_requested.emit(path)
        elif os.path.isdir(path):
            self.refresh(path, getattr(self, "_recent", []))
        else:
            self.open_requested.emit(path)

    def _check_nmrpipe(self):
        found = None
        if shutil.which("csh"):
            r = subprocess.run(["csh", "-c", "which nmrPipe"], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip().startswith("/"):
                found = r.stdout.strip()
        self.status_card.setVisible(not found)
