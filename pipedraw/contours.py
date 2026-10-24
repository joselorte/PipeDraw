"""Contour lines as NaN-separated coordinate arrays, ready for plotting."""
import contourpy
import numpy as np


def levels(base: float, factor: float, count: int) -> np.ndarray:
    return base * factor ** np.arange(count)


def contour_paths(z: np.ndarray, lvls, xcoords: np.ndarray, ycoords: np.ndarray):
    """(x, y) arrays for all contours at ``lvls``, in axis coordinates.

    Lines are joined into one array with NaN breaks, so each sign of contour
    is drawn with a single curve item.
    """
    gen = contourpy.contour_generator(
        z=z, line_type=contourpy.LineType.ChunkCombinedNan, name="serial")
    xs, ys = [], []
    for lv in lvls:
        # ChunkCombinedNan: a tuple whose first item is a list of point
        # arrays, one per chunk (None where a chunk has no lines).
        for pts in gen.lines(lv)[0]:
            if pts is None:
                continue
            xs.append(pts[:, 0])
            ys.append(pts[:, 1])
            xs.append([np.nan])
            ys.append([np.nan])
    if not xs:
        return np.array([]), np.array([])
    xi = np.concatenate(xs)
    yi = np.concatenate(ys)
    # Coordinates are linear in index, so interpolate index -> ppm.
    return _to_coords(xi, xcoords), _to_coords(yi, ycoords)


def _to_coords(idx: np.ndarray, coords: np.ndarray) -> np.ndarray:
    if len(coords) < 2:
        return idx
    step = coords[1] - coords[0]
    return coords[0] + idx * step
