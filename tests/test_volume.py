"""3D planes and projections, FID loading, and a 3D phasing round trip with nmrPipe."""
import os
import shutil
import subprocess
import sys

import nmrglue as ng
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from pipedraw import phase, script, volume  # noqa: E402
from pipedraw.spectrum import Spectrum  # noqa: E402
import make_test_3d  # noqa: E402


def _have_nmrpipe():
    return shutil.which("csh") and subprocess.run(
        ["csh", "-c", "which nmrPipe"], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    """demo-style 3D set processed by nmrPipe, with a misphased 13C dimension."""
    if not _have_nmrpipe():
        pytest.skip("nmrPipe not installed")
    d = tmp_path_factory.mktemp("hnco")
    make_test_3d.OUT = str(d)
    make_test_3d.main()
    subprocess.run(["csh", "nmrproc.com"], cwd=d, check=True, capture_output=True)
    return d


def test_single_plane_opens_series(processed):
    v = volume.load(str(processed / "ft" / "test001.ft3"))
    assert isinstance(v, volume.Volume)
    assert v.path.endswith("test%03d.ft3")
    assert [a.pipe_dim for a in v.axes] == [3, 1, 2]       # Z, Y, X
    assert [a.label for a in v.axes] == ["13C", "15N", "1H"]
    assert v.axes[2].ftsize > v.axes[2].size                # EXT on 1H


def test_planes_and_projections_match_full_read(processed):
    v = volume.load(str(processed / "ft" / "test%03d.ft3"))
    _, full = ng.pipe.read(str(processed / "ft" / "test%03d.ft3"))
    for through, (h, vert, t) in volume.VIEWS.items():
        k = v.strongest_plane(through)
        ref = np.take(full, k, axis=t)
        ref = ref.T if vert > h else ref
        s = v.plane(through, k)
        np.testing.assert_allclose(s.real, ref, rtol=1e-6)
        assert (s.x, s.y) == (v.axes[h], v.axes[vert])
        sky = v.projection(through, "skyline").real
        np.testing.assert_allclose(np.abs(sky).max(), np.abs(full).max(), rtol=1e-6)
        total = v.projection(through, "sum").real
        np.testing.assert_allclose(total, (full.sum(axis=t).T if vert > h else full.sum(axis=t)),
                                   rtol=1e-4, atol=1e-2)


def test_2d_fid_loads_as_time_domain():
    s = volume.load(os.path.join(ROOT, "demo", "test.fid"))
    assert isinstance(s, Spectrum)
    assert not s.x.is_freq and not s.y.is_freq
    assert s.x.unit == "pts" and s.x.coords[0] == 0


def test_3d_phase_round_trip(processed):
    """Phase 13C on a 1H×13C plane, write PS for F3, rerun: matches the right phase."""
    text = (processed / "nmrproc.com").read_text()
    ps = script.ps_for_dim(text, 3)
    assert (ps.p0, ps.p1) == make_test_3d.ERROR_Z

    good = script.set_ps(text, 3, 0.0, 0.0).replace("test%03d.ft3", "good%03d.ft3")
    (processed / "good.com").write_text(good)
    subprocess.run(["csh", "good.com"], cwd=processed, check=True, capture_output=True)

    v = volume.load(str(processed / "ft" / "test%03d.ft3"))
    k = v.strongest_plane("y")
    plane = v.plane("y", k)                     # X = 1H, Y = 13C
    assert plane.y.pipe_dim == 3
    ax = plane.y
    pivot = ax.offset + ax.size // 3
    p0_pivot = -make_test_3d.ERROR_Z[0]          # what a user dials in at the pivot (P1 = 0)
    fixed = phase.apply(phase.analytic(plane.real, 0), ax, 0, p0_pivot, 0.0, pivot)
    good_plane = volume.load(str(processed / "ft" / "good%03d.ft3")).plane("y", k).real
    assert np.corrcoef(fixed.ravel(), good_plane.ravel())[0, 1] > 0.99

    d0, d1 = phase.to_pipe(p0_pivot, 0.0, pivot, ax.ftsize)
    new = script.set_ps(text, 3, phase.wrap(ps.p0 + d0), ps.p1 + d1)
    (processed / "nmrproc.com").write_text(new)
    subprocess.run(["csh", "nmrproc.com"], cwd=processed, check=True, capture_output=True)
    a = volume.load(str(processed / "ft" / "test%03d.ft3")).cube()
    b = volume.load(str(processed / "ft" / "good%03d.ft3")).cube()
    assert np.corrcoef(a.ravel(), b.ravel())[0, 1] > 0.99999
