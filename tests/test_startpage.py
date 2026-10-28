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
    nodes = startpage.list_folder(str(tmp_path))
    assert [n.name for n in nodes] == [str(i) for i in range(1, 61)]
    assert {n.kind for n in nodes} == {"experiment"}
    assert nodes[0].size == "2D · ¹H × ¹⁵N" and nodes[0].status == "raw"


def test_folders_expand_to_experiments(tmp_path):
    (tmp_path / "2D").mkdir()
    (tmp_path / "empty").mkdir()
    make_project(tmp_path / "2D", 25)
    nodes = startpage.list_folder(str(tmp_path))
    assert [(n.kind, n.name, n.experiments) for n in nodes] == [("folder", "2D/", 25)]
    assert len(startpage.list_folder(nodes[0].path)) == 25


def test_processed_experiment_and_its_contents(tmp_path):
    make_project(tmp_path, 1)
    exp = tmp_path / "1"
    (exp / "fid.com").write_text("bruk2pipe -in ./ser | nmrPipe -out ./test.fid -ov\n")
    (exp / "nmrproc.com").write_text("nmrPipe -in test.fid | nmrPipe -out test.ft2 -ov\n")
    (exp / "test.fid").write_bytes(b"")
    node = startpage.experiment_node(str(exp))
    assert node.status == "converted" and node.open_path is None
    (exp / "test.ft2").write_bytes(b"")
    (exp / "1_0.5eq.ucsf").write_bytes(b"\0" * 10)
    node = startpage.experiment_node(str(exp))
    assert node.status == "processed" and node.open_path == str(exp / "test.ft2")
    kinds = [(n.kind, n.name) for n in startpage.experiment_contents(str(exp))]
    assert ("script", "nmrproc.com") in kinds and ("export", "1_0.5eq.ucsf") in kinds
