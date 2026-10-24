"""Processing-script generation (basicFT2/3.com) and SMILE phase handling."""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipedraw import bruker, procgen, script, volume  # noqa: E402

HSQC = os.path.join(ROOT, "testdata", "2D", "4.hsqc")

SMILE_COM = """#!/bin/csh
xyz2pipe -in fid/test%03d.fid -x -verb \\
| nmrPipe -fn FT \\
| nmrPipe -fn PS -p0 22.0 -p1 0.0 -di \\
| pipe2xyz -out tmp/test%05d.ft1 -z -ov

xyz2pipe -in tmp/test%05d.ft1 -x -verb \\
| nusPipe -fn SMILE -nDim 3 -maxIter 2048 -report 1 -sample nuslist \\
                    -xApod SP -xQ1 0.50 -xQ2 0.95 -xQ3 1 -xELB 0.0 -xGLB 0.0 \\
                    -xT 100 -xP0 90.0 -xP1 0.0 \\
                    -yT 200 -yP0 0.0 -yP1 0.0 \\
| pipe2xyz -out ft1/test%05d.ft1 -x -ov

xyz2pipe -in ft1/test%05d.ft1 -x -verb \\
| nmrPipe -fn FT \\
| nmrPipe -fn PS -p0 0.0 -p1 0.0 -di \\
| nmrPipe -fn TP \\
| nmrPipe -fn ZTP \\
| pipe2xyz -out ft2/test%05d.ft2 -x -ov

xyz2pipe -in ft2/test%05d.ft2 -z -verb \\
| nmrPipe -fn FT \\
| nmrPipe -fn PS -p0 0.0 -p1 0.0 -di \\
| pipe2xyz -out ft/test%03d.ft3 -z -ov
"""


def test_smile_phases_are_its_arguments():
    ps = {p.pipe_dim: p for p in script.parse_ps(SMILE_COM)}
    assert sorted(ps) == [1, 2, 3]
    assert (ps[2].p0, ps[2].smile_axis) == (22.0, None)
    assert (ps[1].p0, ps[1].smile_axis) == (90.0, "x")
    assert ps[3].smile_axis == "y"
    new = script.set_ps(script.set_ps(SMILE_COM, 1, -88.5, 2.0), 3, 15.0, 0.0)
    ps = {p.pipe_dim: p for p in script.parse_ps(new)}
    assert (ps[1].p0, ps[1].p1, ps[3].p0) == (-88.5, 2.0, 15.0)
    # The PS steps after reconstruction are untouched.
    assert new.count("-fn PS -p0 0.0 -p1 0.0 -di") == 2


def test_arguments_from_settings():
    s = procgen.Settings(ndim=2, fid="test.fid", out="test.ft2",
                         dims={"x": procgen.DimSettings(q3="2", p0=22.0),
                               "y": procgen.DimSettings(ft="alt", p0=188.6, lp=True)},
                         solvent=True, ext_x1="11ppm", ext_xn="5.5ppm", baseline=True)
    a = procgen.arguments(s)
    pairs = dict(zip(a[::2], a[1::2]))
    assert pairs["-xP0"] == "22.0" and pairs["-yP0"] == "188.6" and pairs["-yFTARG"] == "alt"
    assert pairs["-yLP"] == "fb,ord=8" and pairs["-xSOL"] == "SOL"
    assert pairs["-xEXTX1"] == "11ppm" and pairs["-xBASEARG"] == "POLY,auto"
    cmd = procgen.command(s, "/tmp/x y/nmr.com")
    assert cmd.startswith("basicFT2.com ") and "'/tmp/x y/nmr.com'" in cmd


@pytest.mark.skipif(not (os.path.isdir(HSQC) and bruker.have_nmrpipe()),
                    reason="needs testdata/2D/4.hsqc and NMRPipe")
def test_hsqc_generate_and_process(tmp_path):
    work = tmp_path / "hsqc"
    work.mkdir()
    for n in ("acqus", "acqu2s", "ser", "pulseprogram"):
        shutil.copy(os.path.join(HSQC, n), work)
    shutil.copy(os.path.join(HSQC, "nmrproc.com"), work)       # its phases are reused
    (work / "fid.com").write_text(bruker.auto_fid_com(str(work))[0])
    subprocess.run(["csh", "fid.com"], cwd=work, check=True, capture_output=True)

    s = procgen.defaults(str(work / "test.fid"), bruker.load_experiment(str(work)))
    assert (s.dims["x"].p0, s.dims["y"].p0) == (22.0, 188.6)
    assert s.solvent and s.ext_x1 == "3%"        # basicFT's amide defaults (label HN)
    gen = tmp_path / "gen.com"
    subprocess.run(["csh", "-c", procgen.command(s, str(gen))], cwd=work, check=True,
                   capture_output=True)
    text = gen.read_text()
    assert [(p.pipe_dim, p.p0) for p in script.parse_ps(text)] == [(2, 22.0), (1, 188.6)]
    (work / "nmrproc.com").write_text(text)
    subprocess.run(["csh", "nmrproc.com"], cwd=work, check=True, capture_output=True)
    spec = volume.load(str(work / "test.ft2"))
    assert spec.x.is_freq and spec.y.is_freq and spec.x.label == "HN"
    # With the phases from the hand-made script, peaks come out positive.
    strongest = spec.real.flat[np.argmax(np.abs(spec.real))]
    assert strongest > 0
