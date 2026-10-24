"""Export: Sparky .ucsf, NMRView .nv, single-file 3D; opening in other programs."""
import glob
import os
import plistlib
import shlex
import shutil
import subprocess

import nmrglue as ng
import numpy as np

from . import volume


def work_folder(path: str) -> str:
    """Folder for exported files: the spectrum's, or for a plane series in
    ft/, the folder above."""
    folder = os.path.dirname(os.path.abspath(path))
    return os.path.dirname(folder) if "%" in path else folder


def default_name(path: str) -> str:
    """Base name for exports: the spectrum's stem, or the folder's name when
    the stem is NMRPipe's generic ``test``."""
    stem = os.path.splitext(os.path.basename(path))[0].replace("%03d", "")
    if stem in ("test", "test%03d", "") or "%" in path:
        return os.path.basename(work_folder(path))
    return stem


def _axes_and_data(src):
    if isinstance(src, volume.Volume):
        return list(src.axes), src.cube()
    return [src.y, src.x], src.real


# ---- Sparky -------------------------------------------------------------

def have_pipe2ucsf() -> bool:
    return bool(shutil.which("csh")) and subprocess.run(
        ["csh", "-c", "which pipe2ucsf"], capture_output=True).returncode == 0


def write_ucsf(src_path: str, out_path: str) -> str:
    """Write a Sparky file and read it back to check it. Returns a short report.

    Uses Sparky's own pipe2ucsf when it is installed; otherwise writes the
    file from PipeDraw's axes with nmrglue (carrier at point n/2, as nmrglue
    defines the Sparky header)."""
    src = volume.load(src_path)
    axes, data = _axes_and_data(src)
    if os.path.exists(out_path):
        os.remove(out_path)
    if have_pipe2ucsf() and "%" not in src_path:
        folder = os.path.dirname(os.path.abspath(out_path))
        cmd = f"pipe2ucsf {shlex.quote(os.path.abspath(src_path))} {shlex.quote(out_path)}"
        r = subprocess.run(["csh", "-c", cmd], cwd=folder, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stdout + r.stderr)
        how = "pipe2ucsf"
    else:
        udic = {"ndim": len(axes)}
        for i, ax in enumerate(axes):
            obs = float(src.dic[f"FDF{ax.pipe_dim}OBS"])
            step = abs(ax.coords[0] - ax.coords[1])
            udic[i] = dict(size=ax.size, complex=False, encoding="states", sw=step * ax.size * obs,
                           obs=obs, car=ax.coords[ax.size // 2] * obs, label=ax.label,
                           time=False, freq=True)
        ng.sparky.write(out_path, ng.sparky.create_dic(udic), data.astype(np.float32),
                        overwrite=True)
        how = "nmrglue"
    # Read back: same data, same ppm scales.
    rdic, rdata = ng.sparky.read(out_path)
    if rdata.shape != data.shape or not np.allclose(rdata, data, rtol=1e-5, atol=1e-6 * np.abs(data).max()):
        raise RuntimeError("The Sparky file doesn't read back with the same data.")
    worst = 0.0
    for i, ax in enumerate(axes):
        scale = ng.sparky.make_uc(rdic, rdata, i).ppm_scale()
        step = abs(ax.coords[1] - ax.coords[0])
        worst = max(worst, float(np.max(np.abs(scale - ax.coords))) / step)
    return f"Written with {how}; read back: same data, ppm scales within {worst:.2f} points."


# ---- NMRPipe tools ---------------------------------------------------------

def nv_command(src_path: str, out_path: str) -> str:
    q = shlex.quote
    if "%" in src_path:
        return f"xyz2pipe -in {q(src_path)} | pipe2xyz -nv -out {q(out_path)}"
    return f"pipe2xyz -nv -in {q(src_path)} -out {q(out_path)}"


def single_file_command(template: str, out_path: str) -> str:
    return f"xyz2pipe -in {shlex.quote(template)} | pipe2xyz -out {shlex.quote(out_path)}"


def run_csh(command: str, cwd: str, out_path: str) -> str:
    """Run an NMRPipe command line; returns its output. Overwrites ``out_path``."""
    if os.path.exists(out_path):
        os.remove(out_path)
    r = subprocess.run(["csh", "-c", command], cwd=cwd, capture_output=True, text=True,
                       timeout=600)
    log = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(log[-2000:] or f"{command} failed")
    return log


# ---- other programs ----------------------------------------------------------

APPS = {
    "NMRFx Analyst": {"bundles": ["NMRFx Analyst*.app"], "direct": (),
                      "formats": (".nv", ".ucsf", "pipe")},
    "NMRViewJ": {"bundles": ["NMRViewJ*.app"], "direct": (".nv",), "formats": (".nv",)},
    "CcpNmr Analysis": {"bundles": ["CcpNmrAnalysis*.app"], "direct": (),
                        "formats": (".ucsf", ".nv", "pipe")},
}


def _version(app: str) -> tuple:
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as fh:
            v = plistlib.load(fh).get("CFBundleShortVersionString", "0")
    except (OSError, ValueError):
        v = "0"
    return tuple(int(p) if p.isdigit() else 0 for p in v.replace("-", ".").split("."))


def find_apps() -> dict:
    """Installed programs: name -> (app path, version text). Newest wins."""
    found = {}
    roots = ["/Applications", os.path.expanduser("~/Applications")]
    for name, info in APPS.items():
        candidates = [p for root in roots for pat in info["bundles"]
                      for p in glob.glob(os.path.join(root, pat))]
        if candidates:
            best = max(candidates, key=_version)
            found[name] = (best, ".".join(str(n) for n in _version(best)))
    return found


def open_in(app_path: str, file_path: str, direct: bool):
    """Open ``file_path`` in the program. Programs that don't accept files
    from macOS are started, and the file is shown in Finder to drag in."""
    if direct:
        subprocess.Popen(["open", "-a", app_path, file_path])
    else:
        subprocess.Popen(["open", "-a", app_path])
        subprocess.Popen(["open", "-R", file_path])
