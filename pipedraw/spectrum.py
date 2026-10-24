"""Loading 2D NMRPipe spectra and describing their axes."""
from dataclasses import dataclass
from typing import Optional

import nmrglue as ng
import numpy as np


@dataclass
class Axis:
    label: str
    size: int            # points in the file along this axis
    is_freq: bool        # frequency domain (ppm) or time domain (points)
    ftsize: int          # full width when PS was applied (before EXT)
    offset: int          # index of point 0 of this file in that full width
    coords: np.ndarray   # ppm (or point index) for each point
    pipe_dim: int        # 1-4: which NMRPipe dimension (F1, F2...) this is

    def index_of(self, value: float) -> int:
        """Nearest point index for a coordinate value."""
        return int(np.clip(np.argmin(np.abs(self.coords - value)), 0, self.size - 1))

    @property
    def unit(self) -> str:
        return "ppm" if self.is_freq else "pts"


class Spectrum:
    """A 2D NMRPipe spectrum.

    ``real`` is the real part, shape (ny, nx): axis 0 is Y (indirect), axis 1
    is X (direct). ``imag_x`` / ``imag_y`` hold stored imaginaries when the
    file kept them (no ``-di``), otherwise None and phasing falls back to a
    Hilbert transform.
    """

    def __init__(self, path: str, dic: dict, real: np.ndarray,
                 imag_x: Optional[np.ndarray], imag_y: Optional[np.ndarray],
                 axes: list):
        self.path = path
        self.dic = dic
        self.real = real
        self.imag_x = imag_x
        self.imag_y = imag_y
        self.axes = axes   # [y_axis, x_axis], matching array axes

    @property
    def x(self) -> Axis:
        return self.axes[1]

    @property
    def y(self) -> Axis:
        return self.axes[0]

    @classmethod
    def load(cls, path: str) -> "Spectrum":
        dic, data = ng.pipe.read(path)
        ndim = int(dic["FDDIMCOUNT"])
        if data.ndim != 2 or ndim != 2:
            raise ValueError(
                f"{path} is a {ndim}D file; this prototype handles 2D spectra only.")

        # Array axis -1 is FDDIMORDER1, axis 0 is FDDIMORDER2.
        xdim = int(dic["FDDIMORDER1"])
        ydim = int(dic["FDDIMORDER2"])
        y_complex = dic[f"FDF{ydim}QUADFLAG"] == 0 and data.shape[0] % 2 == 0

        if y_complex:
            re_rows, im_rows = data[0::2], data[1::2]
        else:
            re_rows, im_rows = data, None

        real = np.ascontiguousarray(np.real(re_rows), dtype=np.float64)
        imag_x = np.imag(re_rows).astype(np.float64) if np.iscomplexobj(re_rows) else None
        imag_y = np.real(im_rows).astype(np.float64) if im_rows is not None else None

        x_axis = _make_axis(dic, real, xdim, real.shape[1])
        y_axis = _make_axis(dic, real, ydim, real.shape[0])

        # Scripts that end without a final TP write the data transposed
        # (F1 along rows). Show the direct dimension (F2) on X, as nmrDraw does.
        if xdim == 1 and ydim == 2:
            real = np.ascontiguousarray(real.T)
            imag_x, imag_y = (None if imag_y is None else imag_y.T,
                              None if imag_x is None else imag_x.T)
            x_axis, y_axis = y_axis, x_axis
        return cls(path, dic, real, imag_x, imag_y, [y_axis, x_axis])

    def noise_estimate(self) -> float:
        """Robust noise level (scaled median absolute deviation)."""
        sample = self.real[::max(1, self.real.shape[0] // 64),
                           ::max(1, self.real.shape[1] // 256)]
        mad = np.median(np.abs(sample - np.median(sample)))
        return float(mad * 1.4826) or float(np.abs(self.real).max() * 1e-3) or 1.0


def _make_axis(dic: dict, real: np.ndarray, pipe_dim: int, size: int) -> Axis:
    f = f"FDF{pipe_dim}"
    label = str(dic.get(f + "LABEL", "")).strip() or f"F{pipe_dim}"
    is_freq = bool(dic.get(f + "FTFLAG", 0))
    ftsize = int(dic.get(f + "FTSIZE", 0)) or size
    x1 = int(dic.get(f + "X1", 0))
    offset = x1 - 1 if x1 > 0 else 0
    if is_freq:
        coords = _ppm_scale(dic, pipe_dim, size)
    else:
        coords = np.arange(size, dtype=float)
        ftsize, offset = size, 0
    return Axis(label, size, is_freq, ftsize, offset, coords, pipe_dim)


def _ppm_scale(dic: dict, pipe_dim: int, size: int) -> np.ndarray:
    """ppm for each point, from the header (same maths as nmrglue's make_uc)."""
    f = f"FDF{pipe_dim}"
    sw = float(dic[f + "SW"]) or 1.0
    obs = float(dic[f + "OBS"]) or 1.0
    orig = float(dic[f + "ORIG"])
    full = float(size)
    # ORIG is the frequency (Hz) of the last point.
    hz = orig + sw * (full - 1 - np.arange(size)) / full
    return hz / obs
