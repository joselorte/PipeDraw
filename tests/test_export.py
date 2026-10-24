"""Exports: Sparky written from PipeDraw's axes, .nv and single-file 3D via NMRPipe,
and (when NMRFx is installed) NMRFx reading the .nv with the same ppm scales."""
import os
import shutil
import subprocess
import sys

import nmrglue as ng
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipedraw import bruker, export, volume  # noqa: E402

NMRFXA = "/Applications/NMRFx Analyst 2.app/Contents/Resources/app/nmrfxa"
needs_pipe = pytest.mark.skipif(not bruker.have_nmrpipe(), reason="needs NMRPipe")


@pytest.fixture()
def data(tmp_path):
    shutil.copy(os.path.join(ROOT, "demo", "test.ft2"), tmp_path / "test.ft2")   # transposed 2D
    if os.path.isdir(os.path.join(ROOT, "demo3d", "ft")):
        shutil.copytree(os.path.join(ROOT, "demo3d", "ft"), tmp_path / "ft")
    return tmp_path


def test_ucsf_2d_standard_orientation(data):
    out = str(data / "hsqc.ucsf")
    report = export.write_ucsf(str(data / "test.ft2"), out)
    assert "same data" in report
    src = volume.load(str(data / "test.ft2"))
    dic, arr = ng.sparky.read(out)
    assert arr.shape == src.real.shape                  # 15N rows, 1H columns
    assert dic["w1"]["nucleus"] == "15N" and dic["w2"]["nucleus"] == "1H"
    np.testing.assert_allclose(ng.sparky.make_uc(dic, arr, 1).ppm_scale(), src.x.coords, atol=1e-6)


@pytest.mark.skipif(not os.path.isdir(os.path.join(ROOT, "demo3d", "ft")), reason="no demo3d")
def test_ucsf_3d(data):
    out = str(data / "hnca.ucsf")
    export.write_ucsf(str(data / "ft" / "test%03d.ft3"), out)
    dic, arr = ng.sparky.read(out)
    assert arr.shape == volume.load(str(data / "ft" / "test%03d.ft3")).shape


@needs_pipe
@pytest.mark.skipif(not os.path.isdir(os.path.join(ROOT, "demo3d", "ft")), reason="no demo3d")
def test_nv_and_single_file(data):
    folder = str(data)
    export.run_csh(export.nv_command("test.ft2", "hsqc.nv"), folder, str(data / "hsqc.nv"))
    export.run_csh(export.nv_command("ft/test%03d.ft3", "hnca.nv"), folder, str(data / "hnca.nv"))
    export.run_csh(export.single_file_command("ft/test%03d.ft3", "hnca3d.ft3"), folder,
                   str(data / "hnca3d.ft3"))
    one = volume.load(str(data / "hnca3d.ft3"))
    assert isinstance(one, volume.Volume)
    np.testing.assert_array_equal(one.cube(), volume.load(str(data / "ft" / "test%03d.ft3")).cube())
    if not os.path.exists(NMRFXA):
        return
    script = data / "axes.py"
    script.write_text(
        "import sys\nfrom org.nmrfx.processor.datasets import Dataset\n"
        "for f in sys.argv[1:]:\n"
        "    d = Dataset(f, '', False, False, True)\n"
        "    for i in range(d.getNDim()):\n"
        "        n = d.getSizeTotal(i)\n"
        "        print('AXIS %s %d %.4f %.4f' % (d.getLabel(i), n, d.pointToPPM(i, 0.0), "
        "d.pointToPPM(i, n - 1.0)))\n")
    r = subprocess.run([NMRFXA, str(script), str(data / "hnca.nv")], capture_output=True,
                       text=True, timeout=300)
    got = {p[1]: (int(p[2]), float(p[3]), float(p[4]))
           for p in (l.split() for l in r.stdout.splitlines() if l.startswith("AXIS"))}
    for ax in volume.load(str(data / "ft" / "test%03d.ft3")).axes:
        n, first, last = got[ax.label]
        assert n == ax.size
        assert first == pytest.approx(ax.coords[0], abs=1e-3)
        assert last == pytest.approx(ax.coords[-1], abs=1e-3)


def test_names_and_apps():
    assert export.default_name("/x/HNCA/ft/test%03d.ft3") == "HNCA"
    assert export.default_name("/x/4.hsqc/test.ft2") == "4.hsqc"
    assert export.default_name("/x/run/clic1_trosy.ft2") == "clic1_trosy"
    assert export.work_folder("/x/HNCA/ft/test%03d.ft3") == "/x/HNCA"
    assert isinstance(export.find_apps(), dict)
