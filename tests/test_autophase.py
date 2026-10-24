"""Automatic phasing: the peak-maximum fit and NMRPipe's autophaser."""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipedraw import autophase, bruker, phase, procgen, spectrum, volume  # noqa: E402

HSQC = os.path.join(ROOT, "testdata", "2D", "4.hsqc")


def _angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def _indirect(spec_real, ax):
    c = phase.analytic(spec_real, 0)
    cols = autophase.pick_traces(spec_real, 0)
    pivot = ax.offset + ax.size // 2
    p0, p1, agree, n = autophase.from_peaks([c[:, j] for j in cols], ax, pivot)
    return phase.to_pipe(p0, p1, pivot, ax.ftsize)[0], agree, n


def test_synthetic_15n():
    """Synthetic HSQC with a 25° 15N error (1H corrected first)."""
    s = spectrum.Spectrum.load(os.path.join(ROOT, "testdata", "misphased.ft2"))
    fixed_x = phase.apply(phase.complex_along(s, 1), s.x, 1, -40, 60, 0.0)
    p0, agree, n = _indirect(fixed_x, s.y)
    assert _angle_diff(p0, -25) < 8 and agree > 0.8 and n >= 5


@pytest.mark.skipif(not os.path.isdir(os.path.join(ROOT, "demo3d", "ft")), reason="no demo3d")
def test_synthetic_13c_plane():
    v = volume.load(os.path.join(ROOT, "demo3d", "ft", "test%03d.ft3"))
    plane = v.plane("y", v.strongest_plane("y"))
    p0, _, _ = _indirect(plane.real, plane.y)
    assert _angle_diff(p0, -30) < 8


def test_negative_peaks_do_not_matter():
    """Flipping the sign of some traces leaves the zero-order fit unchanged."""
    s = spectrum.Spectrum.load(os.path.join(ROOT, "testdata", "misphased.ft2"))
    fixed_x = phase.apply(phase.complex_along(s, 1), s.x, 1, -40, 60, 0.0)
    c = phase.analytic(fixed_x, 0)
    cols = autophase.pick_traces(fixed_x, 0)
    vecs = [c[:, j] * (-1 if k % 2 else 1) for k, j in enumerate(cols)]
    p0a = autophase.from_peaks([c[:, j] for j in cols], s.y, 0.0)[0]
    p0b = autophase.from_peaks(vecs, s.y, 0.0)[0]
    assert _angle_diff(p0a, p0b) < 1e-6 or _angle_diff(p0a, p0b + 180) < 1e-6


def test_script_helpers():
    assert autophase.script_fid("#!/bin/csh\nnmrPipe -in ./test.fid \\\n| nmrPipe -fn FT \\\n") == "./test.fid"
    assert autophase.script_fid("xyz2pipe -in fid/test%03d.fid -x -verb \\\n") == "fid/test%03d.fid"
    assert autophase.parse_nmrpipe("Auto-Phase ...\n-162.00\n") == -162.0
    cmd = autophase.nmrpipe_command("fid/test%03d.fid", 0.0, 188.6, -10.0)
    assert "-apyP0 188.6" in cmd and "-apyP1 -10.0" in cmd


@pytest.mark.skipif(not (os.path.isdir(HSQC) and bruker.have_nmrpipe()),
                    reason="needs testdata/2D/4.hsqc and NMRPipe")
def test_hsqc_against_hand_phasing(tmp_path):
    """Your 2018 phases: 1H 22°, 15N 188.6° (P1 -10°). Auto lands within 15°."""
    for n in ("acqus", "acqu2s", "ser", "pulseprogram"):
        shutil.copy(os.path.join(HSQC, n), tmp_path)
    (tmp_path / "fid.com").write_text(bruker.auto_fid_com(str(tmp_path))[0])
    subprocess.run(["csh", "fid.com"], cwd=tmp_path, check=True, capture_output=True)
    r = subprocess.run(["csh", "-c", autophase.nmrpipe_command("test.fid", 0.0, 188.6, -10.0)],
                       cwd=tmp_path, capture_output=True, text=True)
    assert _angle_diff(autophase.parse_nmrpipe(r.stdout), 22.0) < 15

    s = procgen.defaults(str(tmp_path / "test.fid"))
    s.dims["x"].p0 = 22.0
    subprocess.run(["csh", "-c", procgen.command(s, str(tmp_path / "p.com"))], cwd=tmp_path,
                   check=True, capture_output=True)
    subprocess.run(["csh", "p.com"], cwd=tmp_path, check=True, capture_output=True)
    h = spectrum.Spectrum.load(str(tmp_path / "test.ft2"))
    p0, agree, _ = _indirect(h.real, h.y)
    hand_at_centre = 188.6 - 10.0 * (h.y.size // 2) / h.y.ftsize
    centre = p0 + 0.0
    assert _angle_diff(centre, hand_at_centre) < 15
