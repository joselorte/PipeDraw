"""Pseudo-3D experiments (T1, T2, CEST, relaxation dispersion) and interleaved
HetNOEs: 2D planes recorded together in one ``ser`` file.

Splitting writes each plane as an ordinary 2D Bruker experiment in the
experiment's ``split/`` folder, as TopSpin's ``split`` does. The planes are then
a series like any other: convert and phase one, process the rest with its recipe.
The original experiment is not changed.
"""
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Optional

from . import bruker

SPLIT_FOLDER = "split"
PLANES_FILE = "planes.txt"          # plane folder name <tab> its delay/irradiation/...
MARKER = "pipedraw_plane"           # in each plane, so it isn't taken for interleaved
_SKIP = ("ser", "acqu3", "acqu3s", PLANES_FILE, MARKER)


@dataclass
class Layout:
    planes: int
    rows: int               # FIDs per plane, as Bruker counts TD of the 2D
    record: int             # bytes per FID, with Bruker's padding
    plane_inner: bool       # True: the planes cycle within each row of the 2D
    interleaved: bool       # a 2D's TD holds the planes (HetNOE), no acqu3s
    values: list = field(default_factory=list)      # one label per plane, or empty
    missing: int = 0        # FIDs not recorded (a stopped experiment)
    note: str = ""          # what's needed to analyse the planes, e.g. the CPMG time


def _read(folder, name):
    try:
        with open(os.path.join(folder, name), errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _is_plane(folder, acqu2s_text):
    return (os.path.exists(os.path.join(folder, MARKER))
            or "bin/split" in acqu2s_text)       # TopSpin's split notes itself here


def _record_bytes(acqus, size, fids):
    """Bytes per FID. Bruker pads each FID to 1 kB; newer files may not."""
    raw = int(acqus.get("TD", 0)) * (8 if int(acqus.get("DTYPA", 0)) == 2 else 4)
    padded = int(math.ceil(raw / 1024) * 1024)
    if raw and size % padded and size == raw * fids:
        return raw
    return padded


def layout(folder: str) -> Optional[Layout]:
    """How the planes lie in ``ser``, or None if this isn't a set of planes."""
    ser = os.path.join(folder, "ser")
    if not os.path.isfile(ser) or not os.path.isfile(os.path.join(folder, "acqu2s")):
        return None
    acqu2s_text = _read(folder, "acqu2s")
    if _is_plane(folder, acqu2s_text):
        return None
    acqus = bruker.read_jcamp(os.path.join(folder, "acqus"))
    acqu2 = bruker.read_jcamp(os.path.join(folder, "acqu2s"))
    rows = int(acqu2.get("TD", 0))
    program = _read(folder, "pulseprogram")
    acqu3_path = os.path.join(folder, "acqu3s")
    interleaved = False
    if os.path.isfile(acqu3_path):
        acqu3 = bruker.read_jcamp(acqu3_path)
        planes = int(acqu3.get("TD", 0))
        if planes < 2 or int(acqu3.get("FnMODE", 0)) != 1:  # QF: nothing to Fourier transform
            return None
        plane_inner = int(acqus.get("AQSEQ", 0)) == 1
    elif re.search(r"interleaved", program, re.I) and rows >= 4 and rows % 2 == 0:
        interleaved, planes, plane_inner = True, 2, True
        rows //= 2
    else:
        return None
    size = os.path.getsize(ser)
    record = _record_bytes(acqus, size, planes * rows)
    if not record or not rows:
        return None
    fids = size // record
    missing = max(0, planes * rows - fids)
    if missing:
        if plane_inner:
            rows = fids // planes
            rows -= rows % 2         # whole complex pairs only
        else:
            planes = fids // rows
    return Layout(planes, rows, record, plane_inner, interleaved,
                  plane_values(folder, planes, program), missing,
                  note=_cpmg_note(folder, program))


def is_nus(folder: str) -> bool:
    return os.path.isfile(os.path.join(folder, "nuslist"))


# ---- what each plane is ---------------------------------------------------------

def plane_values(folder: str, planes: int, program: str = None) -> list:
    """A label for each plane from the list the pulse program uses, or []."""
    program = _read(folder, "pulseprogram") if program is None else program
    if planes == 2 and re.search(r"\bNOE\b|NONOE", program.split("prosol")[0]):
        return _noe_labels(folder, program)
    for pattern, name, fmt in (
            (r"<\$VDLIST>", "vdlist", _delay), (r"<\$VCLIST>", "vclist", _loops),
            (r"<\$FQ3LIST>", "fq3list", None), (r"<\$FQ2LIST>", "fq2list", None),
            (r"<\$FQ1LIST>", "fq1list", None)):
        if not re.search(pattern, program):
            continue
        text = _read(folder, name)
        if not text.strip():
            continue
        if name == "vdlist" and re.search(r"field.strength", program, re.I):
            block = _cpmg_block(folder, program)
            fmt = lambda t: _cpmg_frequency(t, block)
        elif name == "vclist":
            per_loop = _loop_time(folder, program)
            if per_loop:
                fmt = lambda t: f"{int(t) * per_loop * 1e3:.6g} ms"
        lines = [t.strip() for t in text.splitlines() if t.strip()]
        unit = ""
        if name.startswith("fq") and re.match(r"[a-zA-Z]", lines[0]):
            unit = " " + lines.pop(0).split()[-1]       # e.g. "bf ppm"
        fmt = fmt or (lambda t, u=unit: f"{t}{u}")
        return [fmt(t.split()[0]) for t in lines[:planes]] if len(lines) >= planes else []
    return []


def _delay(text: str) -> str:
    """A vdlist entry (seconds, or with Bruker's m/u/s suffix) in ms."""
    m = re.fullmatch(r"([0-9.eE+-]+)\s*([mus]?)", text)
    if not m:
        return text
    scale = {"": 1e3, "s": 1e3, "m": 1.0, "u": 1e-3}[m.group(2)]
    return f"{float(m.group(1)) * scale:.6g} ms"


def _loops(text: str) -> str:
    return f"{text} loop" + ("" if text == "1" else "s")


_BRANCH = re.compile(r'if\s+"\(?l(\d+)\)?\s*%\s*2\s*==\s*([01])"\s*\{(.*?)\}\s*else\s*\{(.*?)\}',
                     re.S)


def _delays(folder: str) -> list:
    """The D array of acqus, in seconds."""
    return bruker.read_jcamp(os.path.join(folder, "acqus")).get("D") or []


def _loop_time(folder: str, program: str) -> Optional[float]:
    """Seconds per vclist loop of a T2 experiment, or None if the pulse program
    doesn't say. Bruker's programs define it, e.g. "d31=(p30*16+d21*32)", and
    TopSpin stores its value in acqus."""
    m = re.search(r'"d(\d+)=\(p\d+\*\d+\+d\d+\*\d+\)"', program)
    if not m:
        return None
    delays = _delays(folder)
    number = int(m.group(1))
    return float(delays[number]) if number < len(delays) and delays[number] else None


def _cpmg_block(folder: str, program: str) -> Optional[float]:
    """Seconds of CPMG per block, if the program sets its echo count from a
    field in the list: "COUNTER=d21*RF_field*2"."""
    m = re.search(r"COUNTER\s*=\s*d(\d+)\s*\*\s*\w+\s*\*\s*2", program)
    if not m:
        return None
    delays = _delays(folder)
    number = int(m.group(1))
    return float(delays[number]) if number < len(delays) and delays[number] else None


def _cpmg_frequency(text: str, block: Optional[float]) -> str:
    """The frequency applied for a list entry. The echo count is a whole number,
    so a frequency between two counts is rounded down, as the spectrometer did."""
    try:
        wanted = float(text)
    except ValueError:
        return text
    if not block or not wanted:
        return f"{wanted:g} Hz"
    return f"{math.floor(block * wanted * 2 + 1e-9) / (2 * block):.6g} Hz"


def _cpmg_note(folder: str, program: str) -> str:
    """The constant relaxation time: the program runs one CPMG block on each
    side of its central refocusing element."""
    block = _cpmg_block(folder, program)
    if not block or len(re.findall(r"lo to \d+ times COUNTER\b", program)) != 2:
        return ""
    return (f"Constant CPMG time {2 * block * 1e3:.6g} ms: "
            "R2,eff = ln(I0 / I) / T, with the 0 Hz plane as I0.")


def _noe_labels(folder: str, program: str) -> list:
    """["no NOE", "NOE"] in acquisition order, read from the pulse program: a
    test on a loop counter's parity chooses between plain relaxation and a
    saturation loop. The first plane takes the counter's starting value."""
    for m in _BRANCH.finditer(program):
        counter, parity, first, other = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        if ("lo to" in first) == ("lo to" in other):
            continue
        start = re.search(rf'"l{counter}\s*=\s*(\d+)"', program)
        if start:
            start = int(start.group(1))
        else:
            values = bruker.read_jcamp(os.path.join(folder, "acqus")).get("L") or []
            if counter >= len(values):
                return []
            start = int(values[counter])
        saturates = "lo to" in (first if start % 2 == parity else other)
        return ["NOE", "no NOE"] if saturates else ["no NOE", "NOE"]
    return []


def default_template(lay: Layout) -> int:
    """The plane (0-based) with most signal: the shortest delay or the reference."""
    if "no NOE" in lay.values:
        return lay.values.index("no NOE")
    if "0 Hz" in lay.values:
        return lay.values.index("0 Hz")
    numbers = []
    for v in lay.values:
        m = re.match(r"([0-9.]+) (ms|loops?)$", v)
        if not m:
            return 0
        numbers.append(float(m.group(1)))
    return numbers.index(min(numbers)) if numbers else 0


def summary(lay: Layout) -> str:
    kind = "Interleaved" if lay.interleaved else "Pseudo-3D"
    return f"{kind} · {lay.planes} planes"


# ---- splitting ------------------------------------------------------------------

def split_folder(folder: str) -> str:
    return os.path.join(folder, SPLIT_FOLDER)


def is_split(folder: str) -> bool:
    return os.path.isfile(os.path.join(split_folder(folder), PLANES_FILE))


def plane_names(n: int) -> list:
    width = len(str(n))
    return [str(i + 1).zfill(width) for i in range(n)]


def _set_param(text: str, key: str, value) -> str:
    return re.sub(rf"^(##\${key}=).*$", rf"\g<1> {value}", text, count=1, flags=re.M)


def split(folder: str) -> list:
    """Write each plane as a 2D experiment in ``split/``; returns their folders.
    Written to a temporary folder first, so a failure leaves nothing half done."""
    folder = os.path.abspath(folder)
    if is_nus(folder):
        raise ValueError("Splitting non-uniformly sampled pseudo-3D data isn't supported yet.")
    lay = layout(folder)
    if lay is None:
        raise ValueError(f"{folder} is not a pseudo-3D or interleaved experiment.")
    if lay.rows < 2 or lay.planes < 2:
        raise ValueError("Too little of this experiment was recorded to split it.")
    dest = split_folder(folder)
    if os.path.exists(dest):
        raise FileExistsError(f"{dest} already exists.")
    tmp = dest + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    names = plane_names(lay.planes)
    try:
        os.makedirs(tmp)
        extra = [n for n in os.listdir(folder)
                 if os.path.isfile(os.path.join(folder, n)) and n not in _SKIP]
        for i, name in enumerate(names):
            plane = os.path.join(tmp, name)
            os.makedirs(plane)
            for n in extra:
                shutil.copy2(os.path.join(folder, n), os.path.join(plane, n))
            open(os.path.join(plane, MARKER), "w").close()
            _write_parameters(plane, lay)
        _write_planes(os.path.join(folder, "ser"), tmp, names, lay)
        with open(os.path.join(tmp, PLANES_FILE), "w") as fh:
            if lay.note:
                fh.write(f"# {lay.note}\n")
            for i, name in enumerate(names):
                fh.write(f"{name}\t{lay.values[i] if lay.values else ''}\n")
        os.rename(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return [os.path.join(dest, n) for n in names]


def _write_parameters(plane, lay):
    for name, edits in (("acqus", {"PARMODE": 1, "AQSEQ": 0}), ("acqu", {"PARMODE": 1, "AQSEQ": 0}),
                        ("acqu2s", {"TD": lay.rows}), ("acqu2", {"TD": lay.rows})):
        path = os.path.join(plane, name)
        if not os.path.isfile(path):
            continue
        with open(path, errors="surrogateescape") as fh:
            text = fh.read()
        for key, value in edits.items():
            text = _set_param(text, key, value)
        with open(path, "w", errors="surrogateescape") as fh:
            fh.write(text)


def _write_planes(ser, tmp, names, lay):
    outs = [open(os.path.join(tmp, n, "ser"), "wb") for n in names]
    try:
        with open(ser, "rb") as src:
            if lay.plane_inner:
                # Rows in order, each with one FID of every plane.
                for _ in range(lay.rows):
                    for out in outs:
                        out.write(src.read(lay.record))
            else:
                for out in outs:
                    for _ in range(lay.rows):
                        out.write(src.read(lay.record))
    finally:
        for out in outs:
            out.close()


def read_values(folder: str) -> dict:
    """Plane folder name -> label, from a ``split/`` folder (empty if there's none)."""
    out = {}
    for line in _read(folder, PLANES_FILE).splitlines():
        name, _, value = line.partition("\t")
        if name and not name.startswith("#"):
            out[name] = value.strip()
    return out
