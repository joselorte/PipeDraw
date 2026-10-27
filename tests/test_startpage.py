"""Start page listing: every experiment in a large project, in natural order."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipedraw import startpage  # noqa: E402

ACQUS = ("##TITLE= Parameter file\n##$TD= 1024\n##$SW_h= 8000.0\n##$SFO1= 600.1\n"
         "##$BF1= 600.1\n##$NUC1= <1H>\n##$PULPROG= <hsqc>\n##$NS= 8\n##$TE= 298.0\n##END=\n")
ACQU2S = ("##TITLE= Parameter file\n##$TD= 128\n##$SW_h= 2000.0\n##$SFO1= 60.8\n"
          "##$BF1= 60.8\n##$NUC1= <15N>\n##$FnMODE= 6\n##END=\n")


def make_project(root, count):
    for i in range(1, count + 1):
        d = root / str(i)
        d.mkdir()
        (d / "acqus").write_text(ACQUS)
        (d / "acqu2s").write_text(ACQU2S)
        (d / "ser").write_bytes(b"\0" * 64)


def test_all_experiments_listed_in_natural_order(tmp_path):
    make_project(tmp_path, 60)
    shown = [e[0] for e in startpage._scan(str(tmp_path))]
    assert shown == [f"{i}/" for i in range(1, 61)]


def test_experiments_one_level_down(tmp_path):
    (tmp_path / "2D").mkdir()
    make_project(tmp_path / "2D", 25)
    shown = [e[0] for e in startpage._scan(str(tmp_path))]
    assert shown[:3] == ["2D/1/", "2D/2/", "2D/3/"] and len(shown) == 25
