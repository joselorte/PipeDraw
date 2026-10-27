"""Series of similar experiments (e.g. a titration), processed with one recipe.

One experiment is the template: converted, processed and phased as usual.
Every other point gets a fresh fid.com from its own acqus (NMRPipe's
``bruker -AUTO``) with the template's edits replayed on top (modes, carriers,
labels), and a copy of the template's nmrproc.com, so all points are processed
identically. Before running, each point's acquisition is compared with the
template's, since a changed TD, sweep width or temperature breaks the overlay.
"""
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

from . import autophase, bruker, export, phase, script, volume

# fid.com settings the user typically edits by hand and that should carry over.
REPLAY = ("MODE", "CAR", "LAB")


@dataclass
class Point:
    folder: str
    value: str = ""                 # titrant amount, free text (e.g. "0.5 eq")
    issues: list = field(default_factory=list)
    status: str = "idle"            # idle, running, done, failed
    spectrum: Optional[str] = None
    phase_dev: dict = field(default_factory=dict)   # pipe_dim -> degrees
    log: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.folder.rstrip("/"))

    @property
    def tag(self) -> str:
        """File-name friendly name: experiment plus titrant value."""
        v = re.sub(r"[^A-Za-z0-9.+-]+", "", self.value.replace(" ", ""))
        return f"{self.name}_{v}" if v else self.name


# ---- consistency -------------------------------------------------------------

def compare(template: bruker.Experiment, other: bruker.Experiment) -> list:
    """Ways ``other`` was acquired differently from ``template``."""
    issues = []
    if other.pulprog != template.pulprog:
        issues.append(f"pulse program {other.pulprog} (template {template.pulprog})")
    if other.ndim != template.ndim:
        issues.append(f"{other.ndim}D (template {template.ndim}D)")
        return issues
    for i, (a, b) in enumerate(zip(template.dims, other.dims)):
        nuc = bruker_label(a.nucleus)
        if b.nucleus != a.nucleus:
            issues.append(f"{bruker_label(b.nucleus)} (template {nuc})")
            continue
        td_a = a.nus_td if template.nus and i else a.td
        td_b = b.nus_td if other.nus and i else b.td
        if td_a != td_b:
            issues.append(f"{nuc} points {td_b} (template {td_a})")
        if abs(a.sw_hz - b.sw_hz) > 0.01:
            issues.append(f"{nuc} sweep width {b.sw_hz:.1f} Hz (template {a.sw_hz:.1f})")
        if abs(a.sfo1 - b.sfo1) > 1e-4:
            issues.append(f"{nuc} carrier {b.sfo1:.4f} MHz (template {a.sfo1:.4f})")
    if abs(template.temperature - other.temperature) > 0.5:
        issues.append(f"temperature {other.temperature:.1f} K "
                      f"(template {template.temperature:.1f})")
    if template.nus != other.nus:
        issues.append("NUS" if other.nus else "uniformly sampled (template NUS)")
    elif template.nus and template.nus_points != other.nus_points:
        issues.append(f"{other.nus_points} NUS points (template {template.nus_points})")
    return issues


def bruker_label(nucleus: str) -> str:
    from .theme import nice_label
    return nice_label(nucleus)


# ---- the template's recipe ------------------------------------------------------

def template_edits(template_folder: str) -> dict:
    """Edits in the template's fid.com relative to NMRPipe's own: option -> value."""
    own = bruker.parse_fid_com(open(os.path.join(template_folder, "fid.com")).read())
    auto = bruker.parse_fid_com(bruker.auto_fid_com(template_folder)[0])
    edits = {}
    for d in own.dim_names:
        for f in REPLAY:
            if own.dims[d].get(f) and own.dims[d].get(f) != auto.dims.get(d, {}).get(f):
                edits[f"{d}{f}"] = own.dims[d][f]
    return edits


def fid_com_for(folder: str, edits: dict, water: bool = False) -> str:
    """fid.com for one point: NMRPipe's own, with the template's edits."""
    text = bruker.auto_fid_com(folder)[0]
    for option, value in edits.items():
        try:
            text = bruker.set_option(text, option, value)
        except KeyError:
            pass
    if water:
        exp = bruker.load_experiment(folder)
        fs = bruker.parse_fid_com(text)
        refs = bruker.water_referenced(exp)
        for d, dim in bruker.match_dims(exp, fs).items():
            ref = refs[exp.dims.index(dim)]
            if ref is not None:
                text = bruker.set_option(text, f"{d}CAR", f"{ref:.3f}")
    return text


def write_point(point: Point, fid_text: str, proc_text: str):
    """Write fid.com and nmrproc.com into a point's folder (old ones kept as .bak)."""
    for name, text in (("fid.com", fid_text), ("nmrproc.com", proc_text)):
        path = os.path.join(point.folder, name)
        if os.path.isfile(path):
            old = open(path).read()
            if old != text and not os.path.exists(path + ".bak"):
                with open(path + ".bak", "w") as fh:
                    fh.write(old)
        with open(path, "w") as fh:
            fh.write(text)


def output_path(folder: str, proc_text: str) -> Optional[str]:
    out = script.output_file(proc_text)
    return os.path.normpath(os.path.join(folder, out)) if out else None


def series_script(points: list) -> str:
    """A csh loop that reruns the whole series with NMRPipe alone."""
    common = os.path.commonpath([p.folder for p in points])
    names = " ".join(shlex.quote(os.path.relpath(p.folder, common)) for p in points)
    return ("#!/bin/csh\n"
            "# Written by PipeDraw: convert and process every point of the series.\n"
            f"foreach exp ({names})\n"
            "  echo \"== $exp\"\n"
            "  (cd $exp && csh fid.com && csh nmrproc.com)\n"
            "end\n")


def run_command() -> str:
    return "csh fid.com && csh nmrproc.com"


# ---- after processing ---------------------------------------------------------------

def phase_check(point: Point) -> dict:
    """The correction each dimension seems to need, in degrees (signed).

    ¹H: NMRPipe's autophaser on the FID, against the script's value.
    Indirect: the peak-maximum correction the processed spectrum would still
    need. Both methods have a bias of several degrees, so compare a point
    with the template (``relative``), not with zero."""
    proc = open(os.path.join(point.folder, "nmrproc.com")).read()
    out = {}
    fid = autophase.script_fid(proc)
    x = script.ps_for_dim(proc, 2)
    y = script.ps_for_dim(proc, 1)
    if fid and x is not None:
        import subprocess
        cmd = autophase.nmrpipe_command(fid, x.p1, y.p0 if y else 0.0, y.p1 if y else 0.0)
        r = subprocess.run(["csh", "-c", cmd], cwd=point.folder, capture_output=True,
                           text=True, timeout=300)
        try:
            out[2] = phase.wrap(autophase.parse_nmrpipe(r.stdout + r.stderr) - x.p0)
        except ValueError:
            pass
    if point.spectrum:
        data = volume.load(point.spectrum)
        if isinstance(data, volume.Volume):
            planes = [(data.plane("z", data.strongest_plane("z")), 0),
                      (data.plane("y", data.strongest_plane("y")), 0)]
        else:
            planes = [(data, 0)]
        for spec, axis in planes:
            ax = spec.axes[axis]
            if not ax.is_freq or ax.pipe_dim in out:
                continue
            c = phase.analytic(spec.real, 0)
            cols = autophase.pick_traces(spec.real, 0)
            try:
                p0, _, _, _ = autophase.from_peaks([c[:, j] for j in cols], ax,
                                                   ax.offset + ax.size // 2)
            except ValueError:
                continue
            out[ax.pipe_dim] = phase.wrap(p0)
    return out


def relative(point_check: dict, template_check: dict) -> dict:
    """Phase differences from the template (degrees, signed), per dimension."""
    return {d: phase.wrap(v - template_check[d]) for d, v in point_check.items()
            if d in template_check}


def export_point(point: Point, kinds=("ucsf", "nv")) -> list:
    """Write Sparky/NMRView files for one processed point; returns the paths."""
    if not point.spectrum:
        return []
    folder = export.work_folder(point.spectrum)
    written = []
    rel = os.path.relpath(point.spectrum, folder)
    if "ucsf" in kinds:
        out = os.path.join(folder, point.tag + ".ucsf")
        export.write_ucsf(point.spectrum, out)
        written.append(out)
    if "nv" in kinds:
        out = os.path.join(folder, point.tag + ".nv")
        export.run_csh(export.nv_command(rel, os.path.basename(out)), folder, out)
        written.append(out)
    return written


def gradient(n: int) -> list:
    """n colours from blue (first point) to orange (last), via purple."""
    stops = [(0x25, 0x57, 0xD6), (0x7C, 0x3A, 0xED), (0xD9, 0x73, 0x0D)]
    out = []
    for i in range(n):
        t = 0.0 if n == 1 else i / (n - 1)
        k = min(int(t * 2), 1)
        f = t * 2 - k
        a, b = stops[k], stops[k + 1]
        out.append("#%02x%02x%02x" % tuple(int(round(a[j] + (b[j] - a[j]) * f)) for j in range(3)))
    return out


def natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]
