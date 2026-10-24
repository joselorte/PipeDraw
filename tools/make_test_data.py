"""Generate a synthetic 1H-15N HSQC in NMRPipe format for testing PipeDraw.

Writes into ``testdata/``:
  test.fid          2D time-domain data (States in F1)
  correct.ft2       processed with the right phases
  misphased.ft2     processed with deliberate phase errors in both dimensions
  misphased_ext.ft2 as misphased.ft2, with the direct dimension extracted (EXT -left)

The phase errors are listed in ERRORS: the correction PipeDraw should find is
the negative of each value.
"""
import os
import sys

import nmrglue as ng
import numpy as np

OUT = sys.argv[1] if len(sys.argv) > 1 else "testdata"

N2, N1 = 512, 128            # complex points: direct (1H), indirect (15N)
SW2, SW1 = 8000.0, 2000.0    # Hz
OBS2, OBS1 = 600.0, 60.8     # MHz
CAR2, CAR1 = 4.77, 118.0     # ppm

# (1H ppm, 15N ppm, amplitude)
rng = np.random.default_rng(1)
PEAKS = [(rng.uniform(6.8, 10.0), rng.uniform(105, 130), rng.uniform(0.5, 1.0))
         for _ in range(60)]
PEAKS += [(7.6, 112.5, -0.7), (6.9, 112.5, -0.7)]  # negative NH2-like pair

# Applied when processing "misphased": (p0, p1) per dimension.
ERRORS = {"x": (40.0, -60.0), "y": (25.0, 0.0)}


def build_fid():
    t2 = np.arange(N2) / SW2
    t1 = np.arange(N1) / SW1
    fid = np.zeros((2 * N1, N2), dtype=np.complex64)
    for h, n, a in PEAKS:
        f2 = (h - CAR2) * OBS2
        f1 = (n - CAR1) * OBS1
        direct = np.exp(2j * np.pi * f2 * t2 - t2 / 0.03)
        ind = np.exp(-t1 / 0.05)
        fid[0::2] += a * np.outer(np.cos(2 * np.pi * f1 * t1) * ind, direct)
        fid[1::2] += a * np.outer(np.sin(2 * np.pi * f1 * t1) * ind, direct)
    fid += (rng.normal(size=fid.shape) + 1j * rng.normal(size=fid.shape)) * 0.02
    return fid


def make_dic():
    udic = {
        "ndim": 2,
        0: dict(sw=SW1, complex=True, obs=OBS1, car=CAR1 * OBS1, size=2 * N1,
                label="15N", encoding="states", time=True, freq=False),
        1: dict(sw=SW2, complex=True, obs=OBS2, car=CAR2 * OBS2, size=N2,
                label="1H", encoding="direct", time=True, freq=False),
    }
    return ng.pipe.create_dic(udic)


def process(dic, data, err_x=(0, 0), err_y=(0, 0), ext=False):
    dic, data = ng.pipe_proc.sp(dic, data, off=0.5, end=0.98, pow=2, c=0.5)
    dic, data = ng.pipe_proc.zf(dic, data, auto=True)
    dic, data = ng.pipe_proc.ft(dic, data, auto=True)
    dic, data = ng.pipe_proc.ps(dic, data, p0=err_x[0], p1=err_x[1])
    dic, data = ng.pipe_proc.di(dic, data)
    if ext:
        dic, data = ng.pipe_proc.ext(dic, data, left=True, sw=True)
    dic, data = ng.pipe_proc.tp(dic, data)
    dic, data = ng.pipe_proc.sp(dic, data, off=0.5, end=0.98, pow=2, c=0.5)
    dic, data = ng.pipe_proc.zf(dic, data, auto=True)
    dic, data = ng.pipe_proc.ft(dic, data, auto=True)
    dic, data = ng.pipe_proc.ps(dic, data, p0=err_y[0], p1=err_y[1])
    dic, data = ng.pipe_proc.di(dic, data)
    dic, data = ng.pipe_proc.tp(dic, data)
    return dic, data


def main():
    os.makedirs(OUT, exist_ok=True)
    dic, fid = make_dic(), build_fid()
    ng.pipe.write(os.path.join(OUT, "test.fid"), dic, fid, overwrite=True)

    d, s = process(dict(dic), fid.copy())
    ng.pipe.write(os.path.join(OUT, "correct.ft2"), d, s.astype(np.float32), overwrite=True)

    d, s = process(dict(dic), fid.copy(), ERRORS["x"], ERRORS["y"])
    ng.pipe.write(os.path.join(OUT, "misphased.ft2"), d, s.astype(np.float32), overwrite=True)

    d, s = process(dict(dic), fid.copy(), ERRORS["x"], ERRORS["y"], ext=True)
    ng.pipe.write(os.path.join(OUT, "misphased_ext.ft2"), d, s.astype(np.float32), overwrite=True)

    d, s = process(dict(dic), fid.copy(), ext=True)
    ng.pipe.write(os.path.join(OUT, "correct_ext.ft2"), d, s.astype(np.float32), overwrite=True)
    print("wrote test data to", OUT)


if __name__ == "__main__":
    main()
