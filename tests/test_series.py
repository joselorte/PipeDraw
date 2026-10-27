"""Series processing: checks against the template, replaying its recipe, phase check."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from pipedraw import bruker, procgen, series  # noqa: E402
import make_test_series  # noqa: E402
from test_startpage import ACQU2S, ACQUS  # noqa: E402

HSQC = os.path.join(ROOT, "testdata", "2D", "4.hsqc")


def _exp(tmp_path, name, acqus=ACQUS, acqu2s=ACQU2S):
    d = tmp_path / name
    d.mkdir()
    (d / "acqus").write_text(acqus)
    (d / "acqu2s").write_text(acqu2s)
    (d / "ser").write_bytes(b"\0" * 64)
    return bruker.load_experiment(str(d))


def test_compare_flags_differences(tmp_path):
    t = _exp(tmp_path, "1")
    assert series.compare(t, _exp(tmp_path, "2")) == []
    warm = _exp(tmp_path, "3", ACQUS.replace("##$TE= 298.0", "##$TE= 303.0"))
    assert series.compare(t, warm) == ["temperature 303.0 K (template 298.0)"]
    fewer = _exp(tmp_path, "4", acqu2s=ACQU2S.replace("##$TD= 128", "##$TD= 96"))
    assert series.compare(t, fewer) == ["¹⁵N points 96 (template 128)"]
    other = _exp(tmp_path, "5", ACQUS.replace("<hsqc>", "<trosy>"))
    assert series.compare(t, other)[0].startswith("pulse program trosy")


def test_names_script_gradient(tmp_path):
    p = series.Point(str(tmp_path / "12"), value="0.5 eq")
    assert p.tag == "12_0.5eq" and series.Point(str(tmp_path / "3")).tag == "3"
    text = series.series_script([series.Point(str(tmp_path / n)) for n in ("2", "10")])
    assert "foreach exp (2 10)" in text and "csh fid.com && csh nmrproc.com" in text
    g = series.gradient(3)
    assert g[0] == "#2557d6" and g[-1] == "#d9730d" and len(set(g)) == 3
    assert sorted(["10", "2", "1"], key=series.natural_key) == ["1", "2", "10"]


@pytest.mark.skipif(not (os.path.isdir(HSQC) and bruker.have_nmrpipe()),
                    reason="needs testdata/2D/4.hsqc and NMRPipe")
def test_series_end_to_end(tmp_path):
    """Template edits carry over, every point processes, and points with the
    template's phase check out within a few degrees of it."""
    folders = make_test_series.make(HSQC, str(tmp_path / "titration"), 3)
    t = folders[0]
    fid = bruker.set_option(bruker.auto_fid_com(t)[0], "yCAR", "118.026")
    open(os.path.join(t, "fid.com"), "w").write(fid)
    subprocess.run(["csh", "fid.com"], cwd=t, check=True, capture_output=True)
    s = procgen.defaults(os.path.join(t, "test.fid"))
    s.dims["x"].p0, s.dims["y"].p0, s.dims["y"].p1 = 13.0, 188.6, -10.0
    subprocess.run(["csh", "-c", procgen.command(s, os.path.join(t, "nmrproc.com"))], cwd=t,
                   check=True, capture_output=True)
    subprocess.run(["csh", "nmrproc.com"], cwd=t, check=True, capture_output=True)

    edits = series.template_edits(t)
    assert edits == {"yCAR": "118.026"}
    proc = open(os.path.join(t, "nmrproc.com")).read()
    tp = series.Point(t, spectrum=series.output_path(t, proc))
    tcheck = series.phase_check(tp)
    for f in folders[1:]:
        p = series.Point(f)
        series.write_point(p, series.fid_com_for(f, edits), proc)
        subprocess.run(["csh", "-c", series.run_command()], cwd=f, check=True,
                       capture_output=True)
        p.spectrum = series.output_path(f, proc)
        assert os.path.exists(p.spectrum)
        assert bruker.parse_fid_com(open(os.path.join(f, "fid.com")).read()).dims["y"]["CAR"] == "118.026"
        dev = series.relative(series.phase_check(p), tcheck)
        assert all(abs(v) < 8 for v in dev.values()), dev
        files = series.export_point(p)
        assert [os.path.basename(x) for x in files] == [f"{p.name}.ucsf", f"{p.name}.nv"]


def test_same_kind(tmp_path):
    t = _exp(tmp_path, "1")
    assert series.same_kind(t, _exp(tmp_path, "2", ACQUS.replace("##$TE= 298.0", "##$TE= 303.0")))
    assert not series.same_kind(t, _exp(tmp_path, "3", ACQUS.replace("<hsqc>", "<trosy>")))
    assert not series.same_kind(t, _exp(tmp_path, "4", acqu2s=ACQU2S.replace("<15N>", "<13C>")))


def test_parse_selection():
    names = ["1", "2", "3", "10", "11", "12", "25", "ref"]
    assert series.parse_selection("2-11, 25 ref", names) == {"2", "3", "10", "11", "25", "ref"}
    assert series.parse_selection("12–10", names) == {"10", "11", "12"}
    with pytest.raises(ValueError):
        series.parse_selection("99", names)
