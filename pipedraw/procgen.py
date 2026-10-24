"""Processing settings -> nmrproc.com, written by NMRPipe's basicFT2.com /
basicFT3.com (with ``-noexec``), so the script is explicit NMRPipe commands."""
import os
import shlex
from dataclasses import dataclass, field
from typing import Optional

from . import bruker, script, volume

WINDOWS = {"SP": ("Sine bell", ("off", "end", "pow")),
           "GM": ("Lorentz-to-Gauss", ("g1 (Hz)", "g2 (Hz)", "g3"))}
ZERO_FILLS = {"zf=1,auto": "×2, then power of 2", "zf=2,auto": "×4, then power of 2",
              "zf=3,auto": "×8, then power of 2"}
FT_ARGS = {"None": "Standard", "alt": "Alternate signs (-alt)", "neg": "Reverse (-neg)",
           "alt,neg": "-alt -neg"}


@dataclass
class DimSettings:
    window: str = "SP"
    q1: str = "0.50"
    q2: str = "0.95"
    q3: str = "1"
    lb: str = "0.0"          # exponential line broadening, Hz
    c1: str = "Auto"         # first-point scale
    zf: str = "zf=1,auto"
    ft: str = "None"
    p0: float = 0.0
    p1: float = 0.0
    lp: bool = False         # forward-backward linear prediction (indirect only)


@dataclass
class Settings:
    ndim: int
    fid: str                  # FID path relative to the folder (test.fid, fid/test%03d.fid)
    out: str                  # spectrum path relative to the folder
    dims: dict = field(default_factory=dict)     # "x"/"y"/"z" -> DimSettings
    solvent: bool = False     # SOL on the direct dimension
    ext_x1: str = "0%"
    ext_xn: str = "100%"
    baseline: bool = False    # POLY -auto on the direct dimension
    nus_sample: Optional[str] = None   # sampling schedule -> SMILE reconstruction

    @property
    def nus(self) -> bool:
        return self.nus_sample is not None


def work_folder(fid_path: str) -> str:
    """Where processing runs: the FID's folder, or for a plane series in fid/,
    the folder above."""
    folder = os.path.dirname(os.path.abspath(fid_path))
    return os.path.dirname(folder) if "%" in fid_path else folder


def defaults(fid_path: str, exp: Optional[bruker.Experiment] = None) -> Settings:
    """Settings like basicFT's own defaults, plus what the experiment tells us."""
    data = volume.load(fid_path)
    ndim = 3 if isinstance(data, volume.Volume) else 2
    folder = work_folder(fid_path)
    rel = os.path.relpath(fid_path, folder)
    out = "ft/test%03d.ft3" if ndim == 3 else os.path.splitext(rel)[0] + ".ft2"
    s = Settings(ndim=ndim, fid=rel, out=out)
    for d in "xyz"[:ndim]:
        s.dims[d] = DimSettings(q3="2" if d == "x" else "1")
    x_label = (data.axes[2] if ndim == 3 else data.x).label
    if x_label.upper().startswith("HN"):
        # basicFT's defaults for amide-detected data.
        s.solvent, s.baseline, s.ext_x1, s.ext_xn = True, True, "3%", "47%"
    nuslist = os.path.join(folder, "nuslist")
    if os.path.isfile(nuslist):
        s.nus_sample = "nuslist"
        s.out = "ft/test%03d.ft3" if ndim == 3 else s.out
    if exp is not None:
        fid_com = os.path.join(exp.folder, "fid.com")
        fs = bruker.parse_fid_com(open(fid_com).read()) if os.path.isfile(fid_com) else None
        if fs is not None:
            for chk in bruker.checks(exp, fs):
                # Complex-mode States-TPPI data needs FT -alt.
                if chk.fix and chk.fix[0].endswith("MODE") and chk.fix[1] == "States-TPPI":
                    d = chk.fix[0][0]
                    if d in s.dims:
                        s.dims[d].ft = "alt"
    # Keep the phases of an existing script for the same data.
    existing = script.find_script(folder)
    if existing:
        text = open(existing).read()
        for d, pipe_dim in (("x", 2), ("y", 1), ("z", 3)):
            ps = script.ps_for_dim(text, pipe_dim)
            if ps and d in s.dims:
                s.dims[d].p0, s.dims[d].p1 = ps.p0, ps.p1
    return s


def arguments(s: Settings) -> list:
    """basicFT2.com / basicFT3.com command-line options for ``s``."""
    args = ["-in", s.fid, "-out", s.out]
    for d, ds in s.dims.items():
        args += [f"-{d}APOD", ds.window, f"-{d}Q1", ds.q1, f"-{d}Q2", ds.q2, f"-{d}Q3", ds.q3,
                 f"-{d}ELB", ds.lb, f"-{d}C1", ds.c1, f"-{d}ZFARG", ds.zf,
                 f"-{d}FTARG", ds.ft, f"-{d}P0", script._fmt(ds.p0), f"-{d}P1", script._fmt(ds.p1)]
        if d != "x" and ds.lp and not s.nus:
            args += [f"-{d}LP", "fb,ord=8"]
    args += ["-xSOL", "SOL" if s.solvent else "NULL", "-xEXTX1", s.ext_x1, "-xEXTXN", s.ext_xn,
             "-xBASEARG", "POLY,auto" if s.baseline else "NULL"]
    if s.nus:
        args += ["-nus", "-smile", "-sample", s.nus_sample]
    return args


def command(s: Settings, script_path: str) -> str:
    """The csh command that writes the script without processing."""
    prog = "basicFT3.com" if s.ndim == 3 else "basicFT2.com"
    return " ".join([prog] + [shlex.quote(a) for a in arguments(s)]
                    + ["-noexec", "-script", shlex.quote(script_path)])
