"""Reading and editing the PS (phase) steps of NMRPipe processing scripts.

Each ``-fn PS`` line is assigned to a dimension by following the pipeline:
a pipeline starts with X in front (``nmrPipe -in`` or ``xyz2pipe -x``), and
``TP``/``YTP``/``XY2YX`` swap X and Y, ``ZTP`` swaps X and Z. Dimensions are
named by NMRPipe header number: X (direct) = F2, Y = F1, Z = F3.
"""
import os
import re
from dataclasses import dataclass
from typing import Optional

SCRIPT_NAMES = ("nmrproc.com", "proc.com", "ft2.com", "proc2d.com")
DIM_OF = {"x": 2, "y": 1, "z": 3}

_PIPE_START = re.compile(r"^\s*(nmrPipe|xyz2pipe|\w+2pipe)\b.*-in\b")
_FN = re.compile(r"-fn\s+(\w+)")
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


@dataclass
class PSLine:
    line: int        # 0-based line number in the script
    pipe_dim: int    # NMRPipe dimension number (2 = direct in 2D, 1 = indirect)
    p0: float
    p1: float


def find_script(*folders) -> Optional[str]:
    """First processing script found in ``folders`` (in order), or None."""
    for folder in folders:
        if not folder:
            continue
        for name in SCRIPT_NAMES:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    return None


def _code(line: str) -> str:
    """The line without a trailing comment."""
    return line.split("#", 1)[0]


TRANSPOSES = ("TP", "YTP", "XY2YX", "ZTP")


def _walk(text: str, transposes=False):
    """Yield (line number, code, function name, dimension in front) per -fn.

    Transpose steps are only yielded when ``transposes`` is True.
    """
    front = "x"
    for i, raw in enumerate(text.splitlines()):
        code = _code(raw)
        if _PIPE_START.match(code):
            front = "x"
            if code.lstrip().startswith("xyz2pipe"):
                for flag, dim in (("-y", "y"), ("-z", "z"), ("-a", "a")):
                    if re.search(rf"\s{flag}\b", code):
                        front = dim
        for fn in _FN.findall(code):
            fn = fn.upper()
            if fn in TRANSPOSES:
                if transposes:
                    yield i, code, fn, front
                if fn == "ZTP":
                    front = {"x": "z", "z": "x"}.get(front, front)
                else:
                    front = {"x": "y", "y": "x"}.get(front, front)
            else:
                yield i, code, fn, front


@dataclass
class Step:
    """One entry of the pipeline outline."""
    line: int
    kind: str                 # "in", "out", "fn" or "transpose"
    name: str                 # function name or file name
    pipe_dim: Optional[int]   # dimension a processing step acts on
    summary: str


def steps(text: str) -> list:
    """The script as an outline: input, processing steps per dimension, output."""
    out = []
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        code = _code(raw)
        m = re.search(r"-in\s+(\S+)", code)
        if m and _PIPE_START.match(code):
            out.append(Step(i, "in", m.group(1), None, "input"))
    for i, code, fn, front in _walk(text, transposes=True):
        if fn in TRANSPOSES:
            out.append(Step(i, "transpose", fn, None, "transpose"))
        else:
            out.append(Step(i, "fn", fn, DIM_OF.get(front), _summary(fn, code)))
    for i, raw in enumerate(lines):
        m = re.search(r"-out\s+(\S+)", _code(raw))
        if m:
            out.append(Step(i, "out", m.group(1), None, "output"))
    return sorted(out, key=lambda s: (s.line, {"in": 0, "fn": 1, "transpose": 1, "out": 2}[s.kind]))


def _summary(fn: str, code: str) -> str:
    args = re.split(r"-fn\s+\w+", code, maxsplit=1)[-1].replace("\\", " ").split()
    flags = " ".join(args)
    if fn == "PS":
        return f"{_fmt(_arg(code, '-p0'))} / {_fmt(_arg(code, '-p1'))}"
    if fn == "SP":
        return f"sine bell · off {_arg(code, '-off'):g}" if "-off" in flags else "sine bell"
    if fn == "ZF":
        return "auto" if "-auto" in flags else (flags or "double")
    if fn == "FT":
        if "-inv" in flags:
            return "inverse"
        return "real" if "-real" in flags else ("auto" if "-auto" in flags else "complex")
    if fn == "EXT":
        if "-left" in flags:
            return "left half"
        if "-right" in flags:
            return "right half"
        return flags or "region"
    if fn == "SOL":
        return "solvent filter"
    if fn == "POLY":
        return "baseline"
    return flags[:28] + ("…" if len(flags) > 28 else "")


def parse_ps(text: str) -> list:
    """All PS steps in the script, with the dimension each one acts on."""
    return [PSLine(i, DIM_OF[front], _arg(code, "-p0"), _arg(code, "-p1"))
            for i, code, fn, front in _walk(text) if fn == "PS" and front in DIM_OF]


def ps_for_dim(text: str, pipe_dim: int) -> Optional[PSLine]:
    """The first PS step acting on ``pipe_dim``."""
    for ps in parse_ps(text):
        if ps.pipe_dim == pipe_dim:
            return ps
    return None


def set_ps(text: str, pipe_dim: int, p0: float, p1: float) -> str:
    """Script text with the PS values for ``pipe_dim`` replaced (or a PS step added)."""
    lines = text.splitlines(keepends=True)
    ps = ps_for_dim(text, pipe_dim)
    if ps is not None:
        lines[ps.line] = _rewrite(lines[ps.line], p0, p1)
        return "".join(lines)

    ft = _ft_line(text, pipe_dim)
    if ft is None:
        raise ValueError(f"No FT step found for F{pipe_dim}; add a PS step by hand.")
    template = lines[ft]
    indent = re.match(r"^(\s*\|?\s*nmrPipe\s+)", template)
    head = indent.group(1) if indent else "| nmrPipe  "
    new = f"{head}-fn PS -p0 {_fmt(p0)} -p1 {_fmt(p1)} -di"
    width = len(template.rstrip("\r\n").rstrip().rstrip("\\").rstrip())
    new = new.ljust(max(width, len(new))) + " \\\n"
    lines.insert(ft + 1, new)
    return "".join(lines)


def output_file(text: str) -> Optional[str]:
    """The last ``-out`` file named in the script."""
    outs = re.findall(r"-out\s+(\S+)", "\n".join(_code(l) for l in text.splitlines()))
    return outs[-1] if outs else None


def _ft_line(text: str, pipe_dim: int) -> Optional[int]:
    """Line of the FT step for ``pipe_dim``."""
    for i, _, fn, front in _walk(text):
        if fn == "FT" and DIM_OF.get(front) == pipe_dim:
            return i
    return None


def _arg(code: str, flag: str) -> float:
    m = re.search(rf"{flag}\s+({_NUM})", code)
    return float(m.group(1)) if m else 0.0


def _fmt(v: float) -> str:
    text = f"{v:.1f}"
    return "0.0" if text == "-0.0" else text


def _rewrite(line: str, p0: float, p1: float) -> str:
    """Replace -p0/-p1 values, keeping the trailing backslash in its column."""
    end = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\r\n")
    cont = body.rstrip().endswith("\\")
    code = body.rstrip().rstrip("\\").rstrip() if cont else body.rstrip()
    width = len(body.rstrip()) - 1 if cont else 0

    if re.search(r"-p0\s+" + _NUM, code):
        code = re.sub(r"(-p0\s+)" + _NUM, lambda m: m.group(1) + _fmt(p0), code, count=1)
    else:
        code = re.sub(r"(-fn\s+PS)", lambda m: f"{m.group(1)} -p0 {_fmt(p0)}", code, count=1)
    if re.search(r"-p1\s+" + _NUM, code):
        code = re.sub(r"(-p1\s+)" + _NUM, lambda m: m.group(1) + _fmt(p1), code, count=1)
    else:
        code = re.sub(r"(-p0\s+" + _NUM + ")", lambda m: f"{m.group(1)} -p1 {_fmt(p1)}",
                      code, count=1)
    if cont:
        code = code.ljust(width) + "\\" if len(code) < width else code + " \\"
    return code + end
