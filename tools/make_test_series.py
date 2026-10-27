"""A titration-like series from one Bruker HSQC: copies whose 1H frequencies are
shifted by a little more each time (a uniform shift, not real titration
behaviour, but it makes overlays easy to check).

usage: python tools/make_test_series.py SOURCE_EXPERIMENT OUT_FOLDER [N] [STEP_PPM]
"""
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipedraw import bruker  # noqa: E402


def make(src, out, n=6, step_ppm=0.02):
    acqus = bruker.read_jcamp(os.path.join(src, "acqus"))
    td = int(acqus["TD"])
    sw = float(acqus["SW_h"])
    sfo1 = float(acqus["SFO1"])
    raw = np.fromfile(os.path.join(src, "ser"), "<i4").astype(np.float64)
    fids = (raw[0::2] + 1j * raw[1::2]).reshape(-1, td // 2)
    # Time zero is after the digital filter's group delay; shifting the
    # frequency around any other point would also change the phase.
    t = (np.arange(td // 2) - float(acqus.get("GRPDLY", 0.0))) / sw
    for i in range(n):
        d = os.path.join(out, str(i + 1))
        os.makedirs(d, exist_ok=True)
        for name in ("acqus", "acqu2s", "acqu", "acqu2", "pulseprogram"):
            if os.path.isfile(os.path.join(src, name)):
                shutil.copy(os.path.join(src, name), d)
        shifted = fids * np.exp(2j * np.pi * (i * step_ppm * sfo1) * t)
        inter = np.empty(raw.size)
        inter[0::2], inter[1::2] = shifted.real.ravel(), shifted.imag.ravel()
        np.round(inter).astype("<i4").tofile(os.path.join(d, "ser"))
    return [os.path.join(out, str(i + 1)) for i in range(n)]


if __name__ == "__main__":
    make(sys.argv[1], sys.argv[2], *(int(sys.argv[3]),) if len(sys.argv) > 3 else ())
