"""Bruker experiments: acquisition parameters, fid.com generation and editing.

The conversion script itself comes from NMRPipe's own ``bruker -AUTO``,
which knows the vendor details (digital filter, quadrature modes, NUS
expansion). This module reads the Bruker parameters for a summary and
cross-checks, and reads and edits the ``bruk2pipe`` options in fid.com.
"""
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

# Bruker FnMODE values (acquNs) and the bruk2pipe MODE they correspond to.
FNMODE = {0: ("undefined", None), 1: ("QF", "Real"), 2: ("QSEQ", "Sequential"),
          3: ("TPPI", "TPPI"), 4: ("States", "Complex"), 5: ("States-TPPI", "States-TPPI"),
          6: ("Echo-AntiEcho", "Echo-AntiEcho")}
MODES = ["DQD", "Complex", "States", "States-TPPI", "TPPI", "Echo-AntiEcho",
         "Rance-Kay", "Real", "Sequential"]
DIMS = ("x", "y", "z", "a")
DIM_FIELDS = ("N", "T", "MODE", "SW", "OBS", "CAR", "LAB")

# IUPAC frequency ratios relative to 1H in DSS (Markley et al. 1998).
XI = {"13C": 0.251449530, "15N": 0.101329118, "31P": 0.404808636, "2H": 0.153506088}


# ---- Bruker parameter files ---------------------------------------------

def read_jcamp(path: str) -> dict:
    """Parameters of a Bruker acqus-style file: ``##$KEY= value``."""
    params = {}
    key, buf = None, []

    def flush():
        if key is not None:
            params[key] = _value(" ".join(buf).strip())

    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("##"):
                flush()
                key, buf = None, []
                m = re.match(r"##\$?([^=]+)=\s*(.*)$", line)
                if m:
                    key, buf = m.group(1).strip(), [m.group(2)]
            elif line.startswith("$$"):
                continue
            elif key is not None:
                buf.append(line)
    flush()
    return params


def _value(text: str):
    if text.startswith("<") and text.endswith(">"):
        return text[1:-1]
    m = re.match(r"^\((\d+)\.\.(\d+)\)\s*(.*)$", text, re.S)
    if m:   # array: (0..15) v0 v1 ...
        return [_scalar(v) for v in m.group(3).split()]
    return _scalar(text)


def _scalar(text: str):
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def is_experiment(folder: str) -> bool:
    return (os.path.isfile(os.path.join(folder, "acqus"))
            and any(os.path.isfile(os.path.join(folder, f)) for f in ("ser", "fid")))


@dataclass
class Dim:
    nucleus: str
    td: int            # points as Bruker counts them (real + imaginary)
    sw_hz: float
    sfo1: float        # MHz, carrier frequency
    bf1: float         # MHz, basic frequency
    fnmode: int
    nus_td: int

    @property
    def mode_name(self) -> str:
        return FNMODE.get(self.fnmode, (str(self.fnmode), None))[0]


@dataclass
class Experiment:
    folder: str
    pulprog: str
    title: str
    ns: int
    temperature: float          # K
    data_file: str              # "ser" or "fid"
    data_bytes: int
    dims: list = field(default_factory=list)   # direct dimension first
    nus_points: Optional[int] = None           # sampled points in nuslist

    @property
    def ndim(self) -> int:
        return len(self.dims)

    @property
    def nus(self) -> bool:
        return self.nus_points is not None

    @property
    def nus_fraction(self) -> Optional[float]:
        if not self.nus:
            return None
        grid = 1
        for d in self.dims[1:]:
            grid *= max(1, d.nus_td // 2)
        return self.nus_points / grid if grid else None


def load_experiment(folder: str) -> Experiment:
    acqus = read_jcamp(os.path.join(folder, "acqus"))
    dims = [_dim(acqus)]
    for n in (2, 3, 4):
        path = os.path.join(folder, f"acqu{n}s")
        if not os.path.isfile(path):
            break
        p = read_jcamp(path)
        if int(p.get("TD", 0)) <= 1 and n > 2 and int(p.get("NusTD", 0)) <= 1:
            break
        dims.append(_dim(p))
    data_file = "ser" if os.path.isfile(os.path.join(folder, "ser")) else "fid"
    title = ""
    for t in (os.path.join(folder, "pdata", "1", "title"),):
        if os.path.isfile(t):
            title = open(t, errors="replace").read().strip().splitlines()[0:1]
            title = title[0] if title else ""
    nus_points = None
    nuslist = os.path.join(folder, "nuslist")
    if os.path.isfile(nuslist):
        nus_points = sum(1 for line in open(nuslist) if line.strip())
    return Experiment(folder=os.path.abspath(folder), pulprog=str(acqus.get("PULPROG", "")),
                      title=title, ns=int(acqus.get("NS", 0)),
                      temperature=float(acqus.get("TE", 0.0)), data_file=data_file,
                      data_bytes=os.path.getsize(os.path.join(folder, data_file)),
                      dims=dims, nus_points=nus_points)


def _dim(p: dict) -> Dim:
    return Dim(nucleus=str(p.get("NUC1", "")), td=int(p.get("TD", 0)),
               sw_hz=float(p.get("SW_h", 0.0)), sfo1=float(p.get("SFO1", 0.0)),
               bf1=float(p.get("BF1", 0.0)), fnmode=int(p.get("FnMODE", 0)),
               nus_td=int(p.get("NusTD", 0)))


# ---- referencing ----------------------------------------------------------

def water_ppm(temperature_k: float) -> float:
    """1H shift of water relative to DSS: 7.83 − T/96.9 (Wishart et al. 1995)."""
    return 7.83 - temperature_k / 96.9


def water_referenced(exp: Experiment) -> list:
    """Carrier (ppm) for each dimension, with 1H set to water at the sample
    temperature and the other nuclei from the IUPAC ratios. Assumes the 1H
    carrier sits on water, as is usual for protein experiments. None where a
    nucleus has no known ratio."""
    h = exp.dims[0]
    h_car = water_ppm(exp.temperature)
    sf0_h = h.sfo1 / (1 + h_car * 1e-6)    # MHz of 0 ppm 1H
    out = []
    for d in exp.dims:
        nuc = d.nucleus
        if nuc == "1H":
            out.append(round((d.sfo1 / sf0_h - 1) * 1e6, 3))
        elif nuc in XI:
            out.append(round((d.sfo1 / (sf0_h * XI[nuc]) - 1) * 1e6, 3))
        else:
            out.append(None)
    return out


# ---- generating fid.com with NMRPipe ---------------------------------------

def auto_fid_com(folder: str) -> tuple:
    """(fid.com text, log) from NMRPipe's ``bruker -AUTO``, run in a scratch
    folder so the experiment folder is not touched."""
    with tempfile.TemporaryDirectory(prefix="pipedraw-bruker-") as tmp:
        for name in os.listdir(folder):
            src = os.path.join(folder, name)
            if os.path.isfile(src):
                os.symlink(os.path.abspath(src), os.path.join(tmp, name))
        r = subprocess.run(["csh", "-c", "bruker -AUTO"], cwd=tmp, capture_output=True,
                           text=True, timeout=120)
        log = (r.stdout or "") + (r.stderr or "")
        path = os.path.join(tmp, "fid.com")
        if r.returncode != 0 or not os.path.isfile(path):
            raise RuntimeError("bruker -AUTO did not write fid.com:\n" + log[-2000:])
        with open(path) as fh:
            return fh.read(), log


def have_nmrpipe() -> bool:
    return bool(shutil.which("csh")) and subprocess.run(
        ["csh", "-c", "which bruk2pipe"], capture_output=True).returncode == 0


# ---- reading and editing fid.com -------------------------------------------

_CONVERTER = re.compile(r"\b(bruk2pipe|var2pipe|delta2pipe)\b")


def _block(text: str):
    """(start, end) character offsets of the converter command (with its
    continuation lines), or None."""
    m = _CONVERTER.search(text)
    if not m:
        return None
    start = text.rfind("\n", 0, m.start()) + 1
    end = start
    for line in text[start:].splitlines(keepends=True):
        end += len(line)
        if not line.rstrip().endswith("\\"):
            break
    return start, end


@dataclass
class FidScript:
    ndim: int
    dims: dict              # "x"/"y"/... -> {"N": "2048", "MODE": "DQD", ...} (strings)
    options: dict           # decim, dspfvs, grpdly, bad, aq2D -> string
    flags: list             # switches such as -ext -aswap -AMX
    output: Optional[str]   # where the converted FID goes
    scale: Optional[float]  # intensity scale from MULT, if any
    nus: bool

    @property
    def dim_names(self):
        return [d for d in DIMS[:self.ndim]]


def parse_fid_com(text: str) -> Optional[FidScript]:
    blk = _block(text)
    if blk is None:
        return None
    code = text[blk[0]:blk[1]]
    args = code.replace("\\", " ").split()
    values = {}
    flags = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-") and i + 1 < len(args) and not _is_flag(args[i + 1]):
            values[a[1:]] = args[i + 1]
            i += 2
        else:
            if a.startswith("-"):
                flags.append(a)
            i += 1
    ndim = int(_num(values.get("ndim", "2")) or 2)
    dims = {d: {f: values.get(f"{d}{f}", "") for f in DIM_FIELDS} for d in DIMS[:ndim]}
    options = {k: values[k] for k in ("decim", "dspfvs", "grpdly", "bad", "aq2D") if k in values}
    outs = re.findall(r"-out\s+(\S+)", text)
    pipe_out = [o for o in outs if "mask" not in o]
    scale = re.search(r"-fn\s+MULT\s+-c\s+(\S+)", text)
    return FidScript(ndim=ndim, dims=dims, options=options, flags=flags,
                     output=pipe_out[-1] if pipe_out else None,
                     scale=float(scale.group(1)) if scale else None,
                     nus="nusExpand" in text)


def _is_flag(token: str) -> bool:
    return token.startswith("-") and not re.match(r"^-\d", token)


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def set_option(text: str, option: str, value: str) -> str:
    """Change ``-option value`` in the converter command, keeping its column
    alignment. Raises KeyError if the option isn't there."""
    blk = _block(text)
    if blk is None:
        raise KeyError(option)
    code = text[blk[0]:blk[1]]
    rx = re.compile(rf"(?<!\S)(-{re.escape(option)})(\s+)(\S+)")
    m = rx.search(code)
    if not m:
        raise KeyError(option)
    width = len(m.group(2)) + len(m.group(3))
    value = str(value)
    new = m.group(1) + (" " * max(1, width - len(value)) + value)
    code = code[:m.start()] + new + code[m.end():]
    return text[:blk[0]] + code + text[blk[1]:]


def format_value(field_name: str, value: float) -> str:
    """Numbers as bruker's GUI writes them."""
    if field_name in ("N", "T"):
        return str(int(round(value)))
    return f"{value:.3f}"


# ---- cross-checks between fid.com and the Bruker parameters -----------------

@dataclass
class Check:
    level: str                 # "ok", "warn"
    text: str                  # short, shown
    fix: Optional[tuple] = None   # (option, value) that would resolve it
    fix_label: str = ""
    detail: str = ""           # longer explanation, for a tooltip


def match_dims(exp: Experiment, fs: FidScript) -> dict:
    """fid.com dimension name -> Bruker Dim, matched by spectrometer frequency."""
    out = {}
    free = list(range(exp.ndim))
    for d in fs.dim_names:
        obs = _num(fs.dims[d].get("OBS"))
        if obs is None or not free:
            continue
        best = min(free, key=lambda i: abs(exp.dims[i].sfo1 - obs))
        out[d] = exp.dims[best]
        free.remove(best)
    return out


def checks(exp: Experiment, fs: FidScript) -> list:
    """Things in fid.com that disagree with the Bruker parameters."""
    found = []
    acqus = read_jcamp(os.path.join(exp.folder, "acqus"))
    for opt, key in (("decim", "DECIM"), ("dspfvs", "DSPFVS"), ("grpdly", "GRPDLY")):
        want = acqus.get(key)
        have = _num(fs.options.get(opt))
        if want is not None and have is not None and float(want) >= 0 \
                and abs(have - float(want)) > 1e-3:
            found.append(Check("warn", f"-{opt} {fs.options[opt]}, acqus {key} {want}",
                               (opt, str(want)), f"Use {want}"))
    for d, dim in match_dims(exp, fs).items():
        name = d.upper()
        p = fs.dims[d]
        sw, obs = _num(p.get("SW")), _num(p.get("OBS"))
        if sw is not None and abs(sw - dim.sw_hz) > 0.01:
            found.append(Check("warn", f"{name} sweep width {p['SW']}, acquired {dim.sw_hz:.3f} Hz",
                               (f"{d}SW", f"{dim.sw_hz:.3f}"), f"Use {dim.sw_hz:.3f}"))
        if obs is not None and abs(obs - dim.sfo1) > 0.001:
            found.append(Check("warn", f"{name} frequency {p['OBS']}, acquired {dim.sfo1:.3f} MHz",
                               (f"{d}OBS", f"{dim.sfo1:.3f}"), f"Use {dim.sfo1:.3f}"))
        if d == "x":
            continue
        n = _num(p.get("N"))
        want_n = dim.nus_td if exp.nus and dim.nus_td else dim.td
        if n is not None and want_n and int(n) != want_n:
            found.append(Check("warn", f"{name} points {int(n)}, acquired {want_n}",
                               (f"{d}N", str(want_n)), f"Use {want_n}"))
        expected = FNMODE.get(dim.fnmode, (None, None))[1]
        mode = p.get("MODE", "")
        same = mode == expected or {mode, expected} <= {"Complex", "States"}
        if expected and not same:
            hint = (" Kept as Complex, the data need FT -alt when processing."
                    if expected == "States-TPPI" and mode == "Complex" else "")
            found.append(Check("warn", f"{name} mode {mode}, acquired {dim.mode_name}",
                               (f"{d}MODE", expected), f"Use {expected}",
                               detail=f"Bruker FnMODE {dim.fnmode} ({dim.mode_name}).{hint}"))
    return found
