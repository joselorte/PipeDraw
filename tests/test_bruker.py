"""Bruker parameter reading, fid.com editing and checks; conversion of real data
in testdata/ when it is there (it is not in git)."""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipedraw import bruker, volume  # noqa: E402

HSQC = os.path.join(ROOT, "testdata", "2D", "4.hsqc")
HNCA = os.path.join(ROOT, "testdata", "3D", "HNCA")

FID_COM = """#!/bin/csh

bruk2pipe -verb -in ./ser \\
  -bad 0.0 -ext -aswap -AMX -decim 1600 -dspfvs 20 -grpdly 67.98  \\
  -xN              2048  -yN               200  \\
  -xT              1024  -yT               100  \\
  -xMODE            DQD  -yMODE  Echo-AntiEcho  \\
  -xSW        10000.000  -ySW         2500.000  \\
  -xOBS         700.130  -yOBS          70.950  \\
  -xCAR           4.700  -yCAR         118.000  \\
  -xLAB              HN  -yLAB             15N  \\
  -ndim               2  -aq2D         Complex  \\
| nmrPipe -fn MULT -c 1.0e+00 \\
  -out ./test.fid -ov
"""


def test_read_jcamp(tmp_path):
    p = tmp_path / "acqus"
    p.write_text("##TITLE= Parameter file\n##$TD= 2048\n##$SW_h= 11160.71\n"
                 "##$NUC1= <1H>\n##$P= (0..3)\n10 9.5 0\n2\n##END=\n")
    d = bruker.read_jcamp(str(p))
    assert d["TD"] == 2048 and d["SW_h"] == 11160.71 and d["NUC1"] == "1H"
    assert d["P"] == [10, 9.5, 0, 2]


def test_parse_and_edit_fid_com():
    fs = bruker.parse_fid_com(FID_COM)
    assert fs.ndim == 2 and fs.output == "./test.fid" and not fs.nus
    assert fs.dims["y"]["MODE"] == "Echo-AntiEcho" and fs.dims["x"]["CAR"] == "4.700"
    assert fs.options["decim"] == "1600" and "-aswap" in fs.flags
    new = bruker.set_option(FID_COM, "yCAR", "117.981")
    old_line = next(l for l in FID_COM.splitlines() if "yCAR" in l)
    new_line = next(l for l in new.splitlines() if "yCAR" in l)
    assert "117.981" in new_line and len(new_line) == len(old_line)
    assert bruker.parse_fid_com(new).dims["y"]["CAR"] == "117.981"
    assert bruker.set_option(FID_COM, "yMODE", "Complex").count("Complex") == 2
    with pytest.raises(KeyError):
        bruker.set_option(FID_COM, "zCAR", "1.0")


def test_water_reference():
    assert bruker.water_ppm(298.15) == pytest.approx(4.753, abs=1e-3)
    e = bruker.Experiment("x", "p", "", 8, 298.15, "ser", 0, dims=[
        bruker.Dim("1H", 2048, 1e4, 700.13, 700.13, 0, 0),
        bruker.Dim("15N", 128, 2e3, 70.95, 70.95, 6, 0)])
    h, n = bruker.water_referenced(e)
    sf0 = 700.13 / (1 + bruker.water_ppm(298.15) * 1e-6)
    assert h == pytest.approx(bruker.water_ppm(298.15), abs=1e-3)
    assert n == pytest.approx((70.95 / (sf0 * 0.101329118) - 1) * 1e6, abs=1e-3)


needs_hsqc = pytest.mark.skipif(not (os.path.isdir(HSQC) and bruker.have_nmrpipe()),
                                reason="needs testdata/2D/4.hsqc and NMRPipe")
needs_hnca = pytest.mark.skipif(not (os.path.isdir(HNCA) and bruker.have_nmrpipe()),
                                reason="needs testdata/3D/HNCA and NMRPipe")


def _copy_experiment(src, dst):
    os.makedirs(dst)
    for name in os.listdir(src):
        if os.path.isfile(os.path.join(src, name)) and name in (
                "acqus", "acqu2s", "acqu3s", "acqu", "acqu2", "acqu3", "ser", "fid",
                "pulseprogram", "nuslist", "audita.txt"):
            shutil.copy(os.path.join(src, name), dst)
    return dst


@needs_hsqc
def test_hsqc_matches_hand_made_fid_com(tmp_path):
    """bruker -AUTO agrees with the fid.com made for this data set by hand."""
    auto = bruker.parse_fid_com(bruker.auto_fid_com(HSQC)[0])
    own = bruker.parse_fid_com(open(os.path.join(HSQC, "fid.com")).read())
    for d in ("x", "y"):
        for f in ("N", "T", "MODE", "SW", "OBS", "CAR"):
            assert auto.dims[d][f] == own.dims[d][f], (d, f)
    exp = bruker.load_experiment(HSQC)
    assert bruker.checks(exp, auto) == [] and bruker.checks(exp, own) == []


@needs_hsqc
def test_hsqc_conversion_runs(tmp_path):
    work = _copy_experiment(HSQC, str(tmp_path / "hsqc"))
    text = bruker.auto_fid_com(work)[0]
    open(os.path.join(work, "fid.com"), "w").write(text)
    subprocess.run(["csh", "fid.com"], cwd=work, check=True, capture_output=True)
    new = volume.load(os.path.join(work, "test.fid"))
    old = volume.load(os.path.join(HSQC, "test.fid"))       # converted in 2018
    assert new.real.shape == old.real.shape and not new.x.is_freq
    # Same data apart from the intensity scale bruker -AUTO now applies.
    assert np.corrcoef(new.real.ravel(), old.real.ravel())[0, 1] > 0.9999


@needs_hnca
def test_nus_hnca_conversion(tmp_path):
    exp = bruker.load_experiment(HNCA)
    assert exp.nus and exp.nus_points == 750 and exp.ndim == 3
    text = bruker.auto_fid_com(HNCA)[0]
    fs = bruker.parse_fid_com(text)
    assert fs.nus and fs.output == "./fid/test%03d.fid"
    found = bruker.checks(exp, fs)
    assert [c.fix for c in found] == [("zMODE", "States-TPPI")]
    work = _copy_experiment(HNCA, str(tmp_path / "hnca"))
    open(os.path.join(work, "fid.com"), "w").write(bruker.set_option(text, "zMODE", "States-TPPI"))
    subprocess.run(["csh", "fid.com"], cwd=work, check=True, capture_output=True)
    v = volume.load(os.path.join(work, "fid", "test%03d.fid"))
    assert isinstance(v, volume.Volume) and not v.axes[2].is_freq
    assert os.path.isdir(os.path.join(work, "mask"))
