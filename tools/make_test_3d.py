"""Synthetic 3D HNCO-like data set (1H × 15N × 13C) as an NMRPipe plane series.

Writes ``<out>/fid/test%03d.fid`` and ``<out>/nmrproc.com``, a two-pass
NMRPipe script in the style of NMRPipe's own example. The script's F3 (13C)
PS step is deliberately wrong by ERROR_Z; run it with csh to make ft/test%03d.ft3.
"""
import os
import sys

import nmrglue as ng
import numpy as np

OUT = sys.argv[1] if len(sys.argv) > 1 else "demo3d"
NX, NY, NZ = 256, 32, 32          # complex points: 1H, 15N, 13C
SW = {"x": 8000.0, "y": 2000.0, "z": 3000.0}
OBS = {"x": 600.0, "y": 60.8, "z": 150.9}
CAR = {"x": 4.77, "y": 118.0, "z": 176.0}
ERROR_Z = (30.0, 0.0)

rng = np.random.default_rng(3)
PEAKS = [(rng.uniform(7.0, 9.8), rng.uniform(106, 130), rng.uniform(172, 180), rng.uniform(0.6, 1.0))
         for _ in range(25)]

SCRIPT = """#!/bin/csh

xyz2pipe -in fid/test%03d.fid -x -verb              \\
| nmrPipe  -fn SP -off 0.5 -end 0.98 -pow 2 -c 0.5  \\
| nmrPipe  -fn ZF -auto                             \\
| nmrPipe  -fn FT                                   \\
| nmrPipe  -fn PS -p0 0.0 -p1 0.0 -di               \\
| nmrPipe  -fn EXT -left -sw                        \\
| nmrPipe  -fn TP                                   \\
| nmrPipe  -fn SP -off 0.5 -end 0.98 -pow 1 -c 0.5  \\
| nmrPipe  -fn ZF -auto                             \\
| nmrPipe  -fn FT                                   \\
| nmrPipe  -fn PS -p0 0.0 -p1 0.0 -di               \\
| pipe2xyz -out ft/test%03d.ft2 -y

xyz2pipe -in ft/test%03d.ft2 -z -verb               \\
| nmrPipe  -fn SP -off 0.5 -end 0.98 -pow 1 -c 0.5  \\
| nmrPipe  -fn ZF -auto                             \\
| nmrPipe  -fn FT                                   \\
| nmrPipe  -fn PS -p0 {p0} -p1 {p1} -di             \\
| pipe2xyz -out ft/test%03d.ft3 -z
"""


def main():
    tx = np.arange(NX) / SW["x"]
    ty = np.arange(NY) / SW["y"]
    tz = np.arange(NZ) / SW["z"]
    fid = np.zeros((2 * NZ, 2 * NY, NX), dtype=np.complex64)
    for h, n, c, a in PEAKS:
        fx = np.exp(2j * np.pi * (h - CAR["x"]) * OBS["x"] * tx - tx / 0.03)
        wy = 2 * np.pi * (n - CAR["y"]) * OBS["y"] * ty
        wz = 2 * np.pi * (c - CAR["z"]) * OBS["z"] * tz
        dy = np.exp(-ty / 0.04)
        dz = np.exp(-tz / 0.03)
        for iz, fz in ((0, np.cos), (1, np.sin)):
            for iy, fy in ((0, np.cos), (1, np.sin)):
                block = a * np.outer(fz(wz) * dz, fy(wy) * dy)
                fid[iz::2, iy::2, :] += (block[:, :, None] * fx[None, None, :]).astype(np.complex64)
    fid += ((rng.normal(size=fid.shape) + 1j * rng.normal(size=fid.shape)) * 0.01).astype(np.complex64)

    udic = {"ndim": 3}
    for i, (key, label, n, enc) in enumerate((("z", "13C", 2 * NZ, "states"),
                                              ("y", "15N", 2 * NY, "states"),
                                              ("x", "1H", NX, "direct"))):
        udic[i] = dict(sw=SW[key], complex=True, obs=OBS[key], car=CAR[key] * OBS[key],
                       size=n, label=label, encoding=enc, time=True, freq=False)
    dic = ng.pipe.create_dic(udic)
    os.makedirs(os.path.join(OUT, "fid"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "ft"), exist_ok=True)
    ng.pipe.write(os.path.join(OUT, "fid", "test%03d.fid"), dic, fid, overwrite=True)
    with open(os.path.join(OUT, "nmrproc.com"), "w") as fh:
        fh.write(SCRIPT.format(p0=ERROR_Z[0], p1=ERROR_Z[1]))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
