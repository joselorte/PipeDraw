import os

import pytest

from pipedraw import pseudo, startpage

ACQUS = "##$TD= 512\n##$DTYPA= 0\n##$AQSEQ= {aqseq}\n##$PARMODE= 2\n##$PULPROG= <t1>\n##$NUC1= <1H>\n"
ACQU2S = "##$TD= {rows}\n##$NUC1= <15N>\n##$FnMODE= 6\n"
ACQU3S = "##$TD= {planes}\n##$NUC1= <1H>\n##$FnMODE= 1\n"
RECORD = 2048      # 512 points of 4 bytes, already a whole number of kB


def _write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def _fid(plane, row):
    return bytes([plane, row]) * (RECORD // 2)


def _exp(tmp_path, planes=3, rows=4, aqseq=1, program="define list<delay> vd=<$VDLIST>\n",
         vdlist="0.01\n1\n0.2\n", third=True, fids=None):
    d = tmp_path / "exp"
    d.mkdir()
    _write(d / "acqus", ACQUS.format(aqseq=aqseq))
    _write(d / "acqu2s", ACQU2S.format(rows=rows))
    if third:
        _write(d / "acqu3s", ACQU3S.format(planes=planes))
    _write(d / "pulseprogram", program)
    if vdlist:
        _write(d / "vdlist", vdlist)
    if aqseq == 1:
        data = [_fid(p, r) for r in range(rows) for p in range(planes)]
    else:
        data = [_fid(p, r) for p in range(planes) for r in range(rows)]
    (d / "ser").write_bytes(b"".join(data[:fids]))
    return str(d)


@pytest.mark.parametrize("aqseq", (0, 1))
def test_split_follows_acquisition_order(tmp_path, aqseq):
    d = _exp(tmp_path, aqseq=aqseq)
    original = open(os.path.join(d, "ser"), "rb").read()
    folders = pseudo.split(d)
    assert [os.path.basename(f) for f in folders] == ["1", "2", "3"]
    for p, f in enumerate(folders):
        want = b"".join(_fid(p, r) for r in range(4))
        assert open(os.path.join(f, "ser"), "rb").read() == want
        assert not os.path.exists(os.path.join(f, "acqu3s"))
        assert "##$PARMODE= 1" in open(os.path.join(f, "acqus")).read()
        assert pseudo.layout(f) is None     # a plane isn't split again
    assert open(os.path.join(d, "ser"), "rb").read() == original
    assert pseudo.is_split(d)
    with pytest.raises(FileExistsError):
        pseudo.split(d)


def test_values_and_template_come_from_the_lists(tmp_path):
    d = _exp(tmp_path, vdlist="1\n10m\n0.2\n")
    lay = pseudo.layout(d)
    assert lay.values == ["1000 ms", "10 ms", "200 ms"]
    assert pseudo.default_template(lay) == 1
    pseudo.split(d)
    assert pseudo.read_values(pseudo.split_folder(d)) == {"1": "1000 ms", "2": "10 ms",
                                                          "3": "200 ms"}


def _add_delays(folder, **values):
    delays = ["0"] * 64
    for key, value in values.items():
        delays[int(key[1:])] = str(value)
    with open(os.path.join(folder, "acqus"), "a") as fh:
        fh.write("##$D= (0..63)\n" + " ".join(delays) + "\n")


def test_t2_delays_from_the_loop_length(tmp_path):
    d = _exp(tmp_path, vdlist=None, program=('define list<loopcounter> c=<$VCLIST>\n'
                                             '"d31=(p30*16+d21*32)"\n'))
    _write(os.path.join(d, "vclist"), "1\n5\n2\n")
    _add_delays(d, d31=0.01696)
    lay = pseudo.layout(d)
    assert lay.values == ["16.96 ms", "84.8 ms", "33.92 ms"]
    assert pseudo.default_template(lay) == 0


def test_cpmg_frequencies_and_constant_time(tmp_path):
    program = ('define list<delay> RF_field=<$VDLIST>\n;values are field-strength in Hz\n'
               '"if (RF_field == 0) { COUNTER=0; } else { COUNTER=d21*RF_field*2; }"\n'
               '6 DELTA3\nlo to 6 times COUNTER\n7 DELTA3\nlo to 7 times COUNTER\n')
    d = _exp(tmp_path, program=program, vdlist="100\n0\n60\n")
    _add_delays(d, d21=0.02)
    lay = pseudo.layout(d)
    assert lay.values == ["100 Hz", "0 Hz", "50 Hz"]    # 60 Hz needs 2.4 echoes: 2 were run
    assert pseudo.default_template(lay) == 1
    assert lay.note.startswith("Constant CPMG time 40 ms")
    pseudo.split(d)
    assert len(pseudo.read_values(pseudo.split_folder(d))) == 3


def test_interleaved_noe(tmp_path):
    program = (";recording NONOE and NOE interleaved\nprosol\n\"l0=1\"\n"
               'if "l0 %2 == 1"\n{\nd1\n}\nelse\n{\n4 p0\nlo to 4 times l4\n}\n')
    d = _exp(tmp_path, planes=1, rows=8, third=False, vdlist=None, program=program)
    lay = pseudo.layout(d)
    assert (lay.planes, lay.rows, lay.interleaved) == (2, 8 // 2, True)
    assert lay.values == ["no NOE", "NOE"]
    assert pseudo.default_template(lay) == 0
    folders = pseudo.split(d)
    assert "##$TD= 4" in open(os.path.join(folders[0], "acqu2s")).read()


def test_noe_label_order_uses_the_starting_counter(tmp_path):
    program = (";recording NONOE and NOE as pseudo 3D\nprosol\n"
               'if "l11 %2 == 0"\n{\nd1\n}\nelse\n{\n4 p0\nlo to 4 times l4\n}\n')
    d = _exp(tmp_path, planes=2, vdlist=None, program=program)
    with open(os.path.join(d, "acqus"), "a") as fh:
        fh.write("##$L= (0..31)\n" + " ".join(["1"] * 32) + "\n")
    assert pseudo.layout(d).values == ["NOE", "no NOE"]


def test_stopped_experiment_keeps_whole_rows(tmp_path):
    d = _exp(tmp_path, fids=7)      # 2 whole rows and a bit, of 3 planes
    lay = pseudo.layout(d)
    assert (lay.rows, lay.missing) == (2, 5)
    folders = pseudo.split(d)
    assert os.path.getsize(os.path.join(folders[0], "ser")) == 2 * RECORD
    assert "##$TD= 2" in open(os.path.join(folders[0], "acqu2s")).read()


def test_real_3d_and_nus_are_not_split(tmp_path):
    d = _exp(tmp_path)
    _write(os.path.join(d, "acqu3s"), "##$TD= 64\n##$NUC1= <13C>\n##$FnMODE= 5\n")
    assert pseudo.layout(d) is None
    other = tmp_path / "nus"
    other.mkdir()
    n = _exp(other)
    _write(os.path.join(n, "nuslist"), "0\n")
    with pytest.raises(ValueError):
        pseudo.split(n)


def test_tree_marks_planes_and_lists_split(tmp_path):
    d = _exp(tmp_path)
    node = startpage.experiment_node(d)
    assert node.planes == 3 and node.status == "planes" and "Pseudo-3D · 3 planes" in node.what
    pseudo.split(d)
    node = startpage.experiment_node(d)
    assert node.status == "split"
    kids = startpage.experiment_contents(d)
    assert [(k.kind, k.name, k.experiments) for k in kids] == [("folder", "split", 3)]
    assert len(startpage.list_folder(pseudo.split_folder(d))) == 3
