"""Automatic phase correction.

Two methods, chosen by what works on real data:

* Direct dimension: NMRPipe's ``basicAutoPhase.com`` on the FID. It phases
  while the true complex data still exist (before EXT and -di), so it isn't
  hurt by the approximate imaginary part PipeDraw has to rebuild for a
  processed spectrum.
* Indirect dimensions (and the direct one without an FID): the phase at each
  strong peak's maximum. At the centre of a peak the dispersive part is zero,
  so the angle of the complex value there is that peak's phase error; the
  maxima are found on the magnitude, which doesn't depend on phase. P0 (and
  optionally P1) is the weighted fit of those angles against position.
  Entropy (ACME) and peak-area scores were tried first and were biased by
  noise, negative peaks and overlapping dispersive tails.
"""
import re
import shlex
from typing import Optional

import numpy as np
from scipy import optimize

from . import phase


def pick_traces(real: np.ndarray, axis: int, count: int = 12, spacing: int = 3) -> list:
    """Indices (along the other axis) of the strongest, well-separated traces,
    strongest first.

    ``axis`` is the dimension being phased: 1 = rows (X), 0 = columns (Y)."""
    strength = np.abs(real).max(axis=axis)
    chosen = []
    for i in np.argsort(strength)[::-1]:
        if all(abs(int(i) - j) >= spacing for j in chosen):
            chosen.append(int(i))
        if len(chosen) == count:
            break
    return chosen


def peak_points(vec: np.ndarray, rel: float = 0.3) -> np.ndarray:
    """Indices of the strong peak maxima (local maxima of the magnitude) in ``vec``.

    Weak maxima are left out: at their centre, the dispersive tail of a nearby
    strong peak can outweigh their own signal and spoil their phase."""
    m = np.abs(vec)
    noise = 1.4826 * np.median(np.abs(m - np.median(m)))
    keep = (m > 6 * noise) & (m >= rel * m.max())
    i = np.arange(1, len(m) - 1)
    is_max = (m[i] >= m[i - 1]) & (m[i] > m[i + 1]) & keep[i]
    return i[is_max]


def from_peaks(vectors, ax, pivot: float, fit_p1: bool = False, p1_limit: float = 360.0):
    """Correction (P0 at the pivot, P1) for complex 1D ``vectors`` along ``ax``.

    Returns (p0, p1, agreement, peaks): agreement runs from 0 (the peaks
    disagree) to 1 (all peaks point the same way after correction)."""
    xs, angles, weights = [], [], []
    for v in vectors:
        v = np.asarray(v)
        top = np.abs(v).max()
        if top == 0:
            continue
        v = v / top
        for i in peak_points(v):
            xs.append((ax.offset + i - pivot) / ax.ftsize)
            angles.append(np.angle(v[i]))
            weights.append(np.abs(v[i]) ** 2)
    if not xs:
        raise ValueError("No clear peaks in the traces.")
    x, ang, w = np.array(xs), np.array(angles), np.array(weights)

    def resultant(p1_deg, k):
        # k = 1: peaks taken as mostly one sign (pins down P1); k = 2: doubled
        # angles, so a peak and its 180°-flipped version agree.
        return np.sum(w * np.exp(1j * k * (ang + np.deg2rad(p1_deg) * x)))

    p1 = 0.0
    if fit_p1:
        grid = np.arange(-p1_limit, p1_limit + 0.5, 1.0)
        p1 = float(grid[np.argmax([abs(resultant(g, 1)) for g in grid])])
        p1 = float(optimize.minimize_scalar(lambda g: -abs(resultant(g, 1)),
                                            bounds=(p1 - 1, p1 + 1), method="bounded").x)
        p0 = -np.angle(resultant(p1, 1))
    else:
        p0 = -np.angle(resultant(0.0, 2)) / 2.0
    # Of the two answers 180° apart, keep the one with mostly positive peaks.
    if np.sum(np.sqrt(w) * np.cos(ang + p0 + np.deg2rad(p1) * x)) < 0:
        p0 += np.pi
    agreement = float(abs(resultant(p1, 2)) / w.sum())
    return phase.wrap(float(np.rad2deg(p0))), p1, agreement, len(x)


# ---- NMRPipe's autophaser for the direct dimension -----------------------------

def script_fid(text: str) -> Optional[str]:
    """The FID a processing script starts from (first ``-in``)."""
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        m = re.search(r"^\s*(?:nmrPipe|xyz2pipe)\b.*-in\s+(\S+)", code)
        if m:
            return m.group(1)
    return None


def nmrpipe_command(fid: str, x_p1: float, y_p0: float, y_p1: float, y_ft: str = "None") -> str:
    """basicAutoPhase.com for the X-axis zero order, given the other phases."""
    return ("basicAutoPhase.com -in " + shlex.quote(fid)
            + f" -apxP1 {x_p1:.1f} -apyP0 {y_p0:.1f} -apyP1 {y_p1:.1f}"
            + f" -apyFTARG {y_ft} -apOrd 0")


def parse_nmrpipe(output: str) -> float:
    """The phase basicAutoPhase.com prints (its last number)."""
    nums = re.findall(r"[-+]?\d+\.\d+", output)
    if not nums:
        raise ValueError("basicAutoPhase.com gave no phase:\n" + output[-500:])
    return float(nums[-1])
