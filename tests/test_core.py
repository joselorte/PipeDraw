"""Checks the loader and the phase maths against nmrglue's NMRPipe emulation.

Run ``python tools/make_test_data.py`` first.
"""
import os
import sys

import nmrglue as ng
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from pipedraw import contours, phase  # noqa: E402
from pipedraw.spectrum import Spectrum  # noqa: E402
import make_test_data as mtd  # noqa: E402

DATA = os.path.join(ROOT, "testdata")


def load(name):
    return Spectrum.load(os.path.join(DATA, name))


def corr(a, b):
    return np.corrcoef(a.ravel(), b.ravel())[0, 1]


def test_axes_match_nmrglue():
    for name in ["correct.ft2", "misphased_ext.ft2"]:
        spec = load(name)
        dic, data = ng.pipe.read(os.path.join(DATA, name))
        for i, ax in enumerate(spec.axes):
            uc = ng.pipe.make_uc(dic, data, i)
            np.testing.assert_allclose(ax.coords, uc.ppm_scale(), atol=1e-6)


def test_ext_header():
    spec = load("misphased_ext.ft2")
    assert spec.x.size == 512 and spec.x.ftsize == 1024 and spec.x.offset == 0
    assert spec.y.offset == 0 and spec.y.ftsize == spec.y.size


def test_pivot_conversion_is_identity():
    ax = load("correct.ft2").x
    pivot = 300.0
    p0p, p1 = 33.0, -75.0
    a = phase.phase_vector(ax, p0p, p1, pivot)
    p0, p1b = phase.to_pipe(p0p, p1, pivot, ax.ftsize)
    b = phase.phase_vector(ax, p0, p1b, 0.0)
    np.testing.assert_allclose(a, b, atol=1e-9)


@pytest.mark.parametrize("name, ref", [("misphased.ft2", "correct.ft2"),
                                       ("misphased_ext.ft2", "correct_ext.ft2")])
def test_correction_recovers_correct_spectrum(name, ref):
    bad, good = load(name), load(ref)
    ex0, ex1 = mtd.ERRORS["x"]
    ey0, _ = mtd.ERRORS["y"]
    assert corr(bad.real, good.real) < 0.9

    # Correct X then Y with the negated errors (pivot at 0 = NMRPipe values).
    cx = phase.complex_along(bad, 1)
    fixed = phase.apply(cx, bad.x, 1, -ex0, -ex1, 0.0)
    fixed = phase.apply(phase.analytic(fixed, 0), bad.y, 0, -ey0, 0.0, 0.0)
    threshold = 0.995 if name == "misphased.ft2" else 0.99  # HT is approximate after EXT
    assert corr(fixed, good.real) > threshold


def test_stored_y_imaginaries_are_used(tmp_path):
    """F1 processed without -di: the file holds interleaved real/imag rows."""
    dic, fid = mtd.make_dic(), mtd.build_fid()
    d, s = ng.pipe_proc.sp(dic, fid, off=0.5, end=0.98, pow=2, c=0.5)
    d, s = ng.pipe_proc.zf(d, s, auto=True)
    d, s = ng.pipe_proc.ft(d, s, auto=True)
    d, s = ng.pipe_proc.di(d, s)
    d, s = ng.pipe_proc.tp(d, s)
    d, s = ng.pipe_proc.sp(d, s, off=0.5, end=0.98, pow=2, c=0.5)
    d, s = ng.pipe_proc.zf(d, s, auto=True)
    d, s = ng.pipe_proc.ft(d, s, auto=True)
    d, s = ng.pipe_proc.tp(d, s)
    path = tmp_path / "yimag.ft2"
    ng.pipe.write(str(path), d, s, overwrite=True)
    spec = Spectrum.load(str(path))
    assert spec.imag_y is not None and spec.imag_x is None
    assert spec.real.shape == (256, 1024)
    assert corr(spec.real, load("correct.ft2").real) > 0.9999
    # Stored imaginaries agree with the Hilbert reconstruction.
    assert corr(spec.imag_y, phase.analytic(spec.real, 0).imag) > 0.99


def test_contours_in_ppm():
    spec = load("correct.ft2")
    noise = spec.noise_estimate()
    x, y = contours.contour_paths(spec.real, contours.levels(10 * noise, 1.4, 6),
                                  spec.x.coords, spec.y.coords)
    finite = np.isfinite(x)
    assert finite.sum() > 100
    assert spec.x.coords.min() <= x[finite].min() and x[finite].max() <= spec.x.coords.max()
    assert spec.y.coords.min() <= y[finite].min() and y[finite].max() <= spec.y.coords.max()
