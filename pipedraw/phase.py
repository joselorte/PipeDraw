"""Phase correction in NMRPipe's convention.

NMRPipe's ``PS`` multiplies point i (0-based, of the full width N at the time
PS runs) by exp(1j * (p0 + p1 * i / N)), angles in degrees. Interactive phasing
is easier around a pivot, so the viewer works with (p0 at pivot, p1) and
converts with ``to_pipe``. Phase is linear, so a correction found on an
already-phased spectrum simply adds to the values in the script.
"""
import numpy as np
import scipy.signal

from .spectrum import Axis, Spectrum


def analytic(real: np.ndarray, axis: int) -> np.ndarray:
    """Complex data rebuilt from the real part by Hilbert transform.

    Matches NMRPipe's spectra: imaginary = +H(real). Exact for full-width,
    zero-filled data; approximate near the edges after EXT.
    """
    return scipy.signal.hilbert(real, axis=axis)


def complex_along(spec: Spectrum, axis: int) -> np.ndarray:
    """Complex data for phasing along ``axis`` (1 = X/direct, 0 = Y/indirect)."""
    imag = spec.imag_x if axis == 1 else spec.imag_y
    if imag is not None:
        return spec.real + 1j * imag
    return analytic(spec.real, axis)


def phase_vector(ax: Axis, p0_pivot: float, p1: float, pivot_full: float) -> np.ndarray:
    """exp(i*phi) for each point of ``ax``; pivot given as a full-width index."""
    idx = ax.offset + np.arange(ax.size)
    phi = np.deg2rad(p0_pivot + p1 * (idx - pivot_full) / ax.ftsize)
    return np.exp(1j * phi)


def apply(cdata: np.ndarray, ax: Axis, axis: int, p0_pivot: float, p1: float,
          pivot_full: float) -> np.ndarray:
    """Real part of ``cdata`` after phasing along ``axis``."""
    vec = phase_vector(ax, p0_pivot, p1, pivot_full)
    if cdata.ndim == 2 and axis == 0:
        vec = vec[:, None]
    return np.real(cdata * vec)


def to_pipe(p0_pivot: float, p1: float, pivot_full: float, ftsize: int):
    """(p0, p1) as NMRPipe's PS wants them, from pivot-referenced values."""
    return wrap(p0_pivot - p1 * pivot_full / ftsize), p1


def wrap(angle: float) -> float:
    """Angle in (-180, 180]."""
    a = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def ps_line(p0: float, p1: float) -> str:
    p0, p1 = (f"{v:.1f}" for v in (p0, p1))
    p0, p1 = ("0.0" if v == "-0.0" else v for v in (p0, p1))
    return f"| nmrPipe -fn PS -p0 {p0} -p1 {p1} -di \\"
