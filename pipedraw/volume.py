"""3D NMRPipe data: planes in any orientation and projections, as 2D spectra.

A 3D data set is either one stream file or a plane series such as
``ft/test%03d.ft3``. Opening any one plane of a series opens the whole series.
"""
import glob
import os
import re
from typing import Optional

import nmrglue as ng
import numpy as np

from .spectrum import Spectrum, _make_axis

_SERIES = re.compile(r"^(.*?)(\d+)(\.[^./]+)$")

# through-axis -> (horizontal array axis, vertical array axis, stepping array axis)
# Array axes of the cube: 0 = Z, 1 = Y, 2 = X.
VIEWS = {"z": (2, 1, 0), "y": (2, 0, 1), "x": (1, 0, 2)}


def read_header(path: str) -> dict:
    return ng.fileio.pipe.fdata2dic(ng.fileio.pipe.get_fdata(path))


def series_template(path: str) -> Optional[str]:
    """``ft/test001.ft3`` -> ``ft/test%03d.ft3`` when sibling planes exist."""
    folder, name = os.path.split(path)
    m = _SERIES.match(name)
    if not m:
        return None
    prefix, digits, ext = m.groups()
    siblings = glob.glob(os.path.join(folder, glob.escape(prefix) + "[0-9]" * len(digits)
                                      + glob.escape(ext)))
    if len(siblings) < 2:
        return None
    return os.path.join(folder, f"{prefix}%0{len(digits)}d{ext}")


def plane_files(template: str) -> list:
    """The files of a plane series, in order."""
    pattern = re.sub(r"%0(\d+)d", lambda m: "[0-9]" * int(m.group(1)), glob.escape(template))
    return sorted(glob.glob(pattern))


def first_plane(template: str) -> Optional[str]:
    files = plane_files(template)
    return files[0] if files else None


def _is_single_plane(path: str, dic: dict) -> bool:
    """A plane file of a series (rather than a whole 3D stream file)."""
    if int(dic["FDDIMCOUNT"]) < 3:
        return False
    points = int(dic["FDSIZE"]) * int(dic["FDSPECNUM"])
    if dic.get("FDF2QUADFLAG", 1) == 0 and int(dic["FDDIMORDER1"]) == 2:
        points *= 2
    return os.path.getsize(path) <= 2048 + 4 * points


def load(path: str):
    """A Spectrum (2D) or a Volume (3D) for ``path``."""
    if "%" not in path:
        dic = read_header(path)
        ndim = int(dic["FDDIMCOUNT"])
        if ndim == 2:
            return Spectrum.load(path)
        if ndim == 3 and _is_single_plane(path, dic):
            template = series_template(path)
            if template is None:
                raise ValueError(f"{path} is one plane of a 3D series, but no other planes "
                                 "were found next to it.")
            path = template
    dic, data = ng.pipe.read_lowmem(path)
    if data.ndim == 2:
        return Spectrum.load(path)
    if data.ndim != 3:
        raise ValueError(f"{path} has {data.ndim} dimensions; PipeDraw shows 2D and 3D data.")
    return Volume(path, dic, data)


class Volume:
    """A 3D data set. ``axes[i]`` describes cube array axis i (0 = Z, 1 = Y, 2 = X)."""
    ndim = 3

    def __init__(self, path, dic, data):
        self.path = path
        self.dic = dic
        self.data = data
        self.shape = tuple(int(s) for s in data.shape)
        order = [int(dic[f"FDDIMORDER{i}"]) for i in (1, 2, 3)]
        # Array axis -1 is FDDIMORDER1, -2 is FDDIMORDER2, -3 is FDDIMORDER3.
        self.axes = [_make_axis(dic, None, order[2], self.shape[0]),
                     _make_axis(dic, None, order[1], self.shape[1]),
                     _make_axis(dic, None, order[0], self.shape[2])]
        self._cube = None
        self._proj = {}

    # ---- data access ------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._cube is not None

    def cube(self) -> np.ndarray:
        """The whole data set in memory (read once)."""
        if self._cube is None:
            self._cube = np.ascontiguousarray(np.real(self.data[:, :, :]), dtype=np.float32)
        return self._cube

    def view_axes(self, through: str):
        """(horizontal Axis, vertical Axis, stepping Axis) for planes ``through``."""
        h, v, t = VIEWS[through]
        return self.axes[h], self.axes[v], self.axes[t]

    def plane(self, through: str, k: int) -> Spectrum:
        h, v, t = VIEWS[through]
        k = int(np.clip(k, 0, self.shape[t] - 1))
        if t == 0 and self._cube is None:
            arr = np.real(np.asarray(self.data[k]))
        else:
            arr = np.take(self.cube(), k, axis=t)
        return self._as_spectrum(arr, h, v)

    def projection(self, through: str, kind: str = "skyline") -> Spectrum:
        """Projection along the stepping axis: skyline (largest |I|, sign kept) or sum."""
        h, v, t = VIEWS[through]
        key = (t, kind)
        if key not in self._proj:
            cube = self.cube()
            if kind == "sum":
                arr = cube.sum(axis=t, dtype=np.float64)
            else:
                idx = np.expand_dims(np.argmax(np.abs(cube), axis=t), t)
                arr = np.take_along_axis(cube, idx, axis=t).squeeze(t)
            self._proj[key] = arr
        return self._as_spectrum(self._proj[key], h, v)

    def strongest_plane(self, through: str) -> int:
        """Index of the plane with the largest signal (needs the cube)."""
        t = VIEWS[through][2]
        other = tuple(a for a in range(3) if a != t)
        return int(np.argmax(np.abs(self.cube()).max(axis=other)))

    def _as_spectrum(self, arr, h, v) -> Spectrum:
        # The two remaining array axes keep their order; put vertical first.
        if v > h:
            arr = arr.T
        real = np.ascontiguousarray(arr, dtype=np.float64)
        return Spectrum(self.path, self.dic, real, None, None, [self.axes[v], self.axes[h]])
