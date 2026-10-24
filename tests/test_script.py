"""PS parsing/rewriting, plus an end-to-end check with real nmrPipe when installed."""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipedraw import phase, script  # noqa: E402
from pipedraw.spectrum import Spectrum  # noqa: E402

FT2_COM = """#!/bin/csh

nmrPipe -in fid/test001.fid \\
| nmrPipe  -fn SOL                                    \\
| nmrPipe  -fn SP -off 0.5 -end 0.98 -pow 2 -c 0.5    \\
| nmrPipe  -fn ZF -auto                               \\
| nmrPipe  -fn FT                                     \\
| nmrPipe  -fn PS -p0 43  -p1 0.0 -di                 \\
| nmrPipe  -fn EXT -left -sw -verb                    \\
| nmrPipe  -fn TP                                     \\
| nmrPipe  -fn SP -off 0.5 -end 0.98 -pow 1 -c 1.0    \\
| nmrPipe  -fn ZF -auto                               \\
| nmrPipe  -fn FT                                     \\
| nmrPipe  -fn PS -p0 -135 -p1 180 -di                \\
   -verb -ov -out test.ft2
"""

NMRPROC_3D = """#!/bin/csh
xyz2pipe -in fid/test%03d.fid -x  -verb             \\
| nmrPipe  -fn FT                                   \\
| nmrPipe  -fn PS -p0 43  -p1 0.0 -di               \\
| nmrPipe  -fn TP                                   \\
| nmrPipe  -fn FT                                   \\
| nmrPipe  -fn PS -p0 -135 -p1 180 -di              \\
| pipe2xyz -out ft/test%03d.ft2 -y

xyz2pipe -in ft/test%03d.ft2 -z -verb               \\
| nmrPipe  -fn FT                                   \\
| nmrPipe  -fn PS -p0 0.0 -p1 0.0 -di               \\
| pipe2xyz -out ft/test%03d.ft3 -z
"""


def test_parse_2d():
    ps = script.parse_ps(FT2_COM)
    assert [(p.pipe_dim, p.p0, p.p1) for p in ps] == [(2, 43, 0), (1, -135, 180)]
    assert script.output_file(FT2_COM) == "test.ft2"


def test_parse_3d_dims():
    assert [(p.pipe_dim, p.p0) for p in script.parse_ps(NMRPROC_3D)] == [
        (2, 43), (1, -135), (3, 0)]


def test_rewrite_keeps_layout():
    new = script.set_ps(FT2_COM, 1, -120.5, 175.0)
    old_lines, new_lines = FT2_COM.splitlines(), new.splitlines()
    i = script.ps_for_dim(FT2_COM, 1).line
    assert new_lines[i].startswith("| nmrPipe  -fn PS -p0 -120.5 -p1 175.0 -di")
    assert new_lines[i].index("\\") == old_lines[i].index("\\")
    assert [l for j, l in enumerate(new_lines) if j != i] == \
        [l for j, l in enumerate(old_lines) if j != i]
    assert script.ps_for_dim(new, 1).p0 == -120.5


def test_insert_missing_ps():
    text = FT2_COM.replace("| nmrPipe  -fn PS -p0 -135 -p1 180 -di                \\\n", "")
    assert script.ps_for_dim(text, 1) is None
    new = script.set_ps(text, 1, 10.0, -5.0)
    ps = script.ps_for_dim(new, 1)
    assert (ps.p0, ps.p1) == (10.0, -5.0)
    assert new.splitlines()[ps.line - 1].strip().startswith("| nmrPipe  -fn FT")
    assert new.splitlines()[ps.line].rstrip().endswith("\\")


def test_find_script(tmp_path):
    assert script.find_script(str(tmp_path)) is None
    (tmp_path / "proc.com").write_text("x")
    (tmp_path / "nmrproc.com").write_text("x")
    assert script.find_script(str(tmp_path)).endswith("nmrproc.com")


def _have_nmrpipe():
    return shutil.which("csh") and subprocess.run(
        ["csh", "-c", "which nmrPipe"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _have_nmrpipe(), reason="nmrPipe not installed")
def test_real_nmrpipe_round_trip(tmp_path):
    """Misphase with nmrPipe, correct in PipeDraw, write PS back, rerun: matches."""
    shutil.copy(os.path.join(ROOT, "testdata", "test.fid"), tmp_path)
    body = FT2_COM.replace("fid/test001.fid", "test.fid").replace("-fn SOL", "-fn NULL")
    good = script.set_ps(script.set_ps(body, 2, 0, 0), 1, 0, 0).replace("test.ft2", "good.ft2")
    bad = script.set_ps(script.set_ps(body, 2, 40, -60), 1, 25, 0)
    for name, text in (("good.com", good), ("nmrproc.com", bad)):
        (tmp_path / name).write_text(text)
        subprocess.run(["csh", name], cwd=tmp_path, check=True, capture_output=True)

    spec = Spectrum.load(str(tmp_path / "test.ft2"))
    assert spec.x.pipe_dim == 2 and spec.x.ftsize > spec.x.size   # EXT applied
    # Corrections found interactively, around a pivot as a user would set them.
    totals = {}
    for axis, (d0, d1) in ((1, (-40, 60)), (0, (-25, 0))):
        ax = spec.axes[axis]
        pivot = ax.offset + ax.size // 3
        p0_pivot = d0 + d1 * pivot / ax.ftsize
        ps = script.ps_for_dim(bad, ax.pipe_dim)
        c0, c1 = phase.to_pipe(p0_pivot, d1, pivot, ax.ftsize)
        totals[ax.pipe_dim] = (phase.wrap(ps.p0 + c0), ps.p1 + c1)
    fixed = bad
    for dim, (p0, p1) in totals.items():
        fixed = script.set_ps(fixed, dim, p0, p1)
    (tmp_path / "nmrproc.com").write_text(fixed)
    subprocess.run(["csh", "nmrproc.com"], cwd=tmp_path, check=True, capture_output=True)

    a = Spectrum.load(str(tmp_path / "test.ft2")).real
    b = Spectrum.load(str(tmp_path / "good.ft2")).real
    assert np.corrcoef(a.ravel(), b.ravel())[0, 1] > 0.99999
