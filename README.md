# PipeDraw (prototype)

A native macOS viewer for 2D NMRPipe spectra with interactive phasing, as a
stand-in for nmrDraw. It reads `.ft2` (and other 2D NMRPipe files), draws
contours in ppm, and turns interactive phase adjustments into `-fn PS` lines
for your processing script.

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

## Run

From a folder with your data and processing script:

```bash
cd /path/to/nmrdata
pipedraw test.ft2
```

Run `pipedraw` with no file and it opens on the **Convert** tab, showing the folder you started in. It lists Bruker experiments (with **Convert** buttons), NMRPipe FIDs and spectra, including those one level down, plus recent items, and checks that nmrPipe can be found. You can drop an experiment folder or a file on it.

**Close** in the top bar (or File > Close dataset, ⌘W) forgets the open experiment, FID, spectrum and script and returns to Convert, listing the closed experiment's neighbours so you can start on the next one.

The top bar has three modes (⌘1–3):

- **View**: the spectrum on its own, with contour controls and row/column slices.
- **Phase**: adds the phasing traces and the phasing panel.
- **Script**: the processing script (`nmrproc.com`, `proc.com`, `ft2.com` or
  `proc2d.com`, found next to the spectrum, in the starting folder, or one level up).
  A **Pipeline** outline lists each step by dimension; click one to jump to its
  line. The editor highlights NMRPipe syntax and tags each PS line with its
  dimension. The run log below it shows progress and has a **Stop** button.

**Run** in the top bar (⌘↩) saves the script and runs it with `csh` in its folder,
so nmrPipe comes from your `~/.cshrc`. Auto-reload pauses while it runs, and the
spectrum reloads when it finishes. Click the auto-reload indicator to turn it off.

## Converting Bruker data

Open a Bruker experiment folder (the one with `acqus` and `fid` or `ser`):
`pipedraw path/to/12`, **File > Open Bruker experiment…** (⇧⌘O), or **Convert** next
to an experiment on the start page. The **Convert** tab shows:

- **The experiment**: pulse program, nuclei, scans, temperature, data size and,
  for non-uniform sampling, how many points were sampled.
- **fid.com**: the folder's existing `fid.com`, or, if there is none, one made
  by NMRPipe's own converter (`bruker -AUTO`, run in a scratch folder). **Generate
  from acqus** makes a fresh one. Nothing is written until **Save** or **Convert**;
  the first save over an existing `fid.com` keeps the old one as `fid.com.bak`.
- **Conversion parameters**: N, T, mode, sweep width, frequency, carrier and label for
  each dimension, plus the digital-filter settings. Editing a field rewrites that
  value in the `bruk2pipe` line; you can also edit the script directly.
- **Checks against acqus**: modes against FnMODE, point counts, sweep widths,
  frequencies and digital-filter values, each with a one-click fix. For example,
  `bruker -AUTO` writes `Complex` for States-TPPI dimensions, which then need
  `FT -alt` when processing.
- **Referencing**: carriers with ¹H on water at the sample temperature
  (7.83 − T/96.9 ppm) and the other nuclei from the IUPAC ratios. **Use water
  referencing** applies them. This assumes the ¹H carrier was set on water.
- **NUS**: `fid.com` expands the sampled points onto the full grid (with
  `nusExpand.tcl`) and writes a sampling mask; reconstruction belongs to processing.

**Convert** saves and runs `fid.com` in the experiment folder; **View FID** then
opens the result (2D or 3D) to check it.

## Processing: FID → nmrproc.com → spectrum

After converting, **Next: Process** (or the **Process** tab with an FID open)
shows the processing settings for each dimension:

- window (sine bell or Lorentz-to-Gauss), line broadening, first-point scaling;
- zero filling, FT options (`-alt` is preselected when States-TPPI data were
  converted as Complex), phases;
- for the direct dimension: the region to keep, solvent filter, baseline;
- for indirect dimensions of uniformly sampled data: linear prediction.

On the right is the `nmrproc.com` that NMRPipe's own `basicFT2.com` /
`basicFT3.com` writes for these settings, updated as you change them. Defaults
follow basicFT's, including its amide defaults for HN-detected data (solvent
filter, baseline, keep 3–47%). If the folder already has an `nmrproc.com`, its
phases are used as the starting point, and saving keeps the old script as
`nmrproc.com.bak`.

**NUS data** (a `nuslist` in the folder) is reconstructed with SMILE. Its
indirect phases are SMILE arguments (`-xP0`, `-yP0`), and the Phase step writes
them there.

**Process** saves the script, runs it, and opens the spectrum in **Phase** with
the script loaded, so **Write PS and reprocess** updates it in place.

## Export

The **Export** tab writes the open spectrum for other programs, next to the
data (for a plane series in `ft/`, in the folder above). File names start from
the experiment folder's name and can be edited.

- **Sparky (.ucsf)**, for Sparky, POKY and CCPN. If Sparky's `pipe2ucsf` is
  installed it is used. Otherwise PipeDraw writes the file with nmrglue from its
  own axes. Either way the file is read back and the data and ppm scales checked.
- **NMRView (.nv)**, for NMRViewJ and NMRFx: NMRPipe's `pipe2xyz -nv`.
- **Single-file NMRPipe (3D)**: the plane series as one file (`xyz2pipe | pipe2xyz`).

**Open in** lists NMRFx Analyst, NMRViewJ and CcpNmr Analysis when they are in
/Applications, with the exported files each can read. NMRViewJ opens a `.nv`
file directly. NMRFx and CCPN don't accept files from macOS, so PipeDraw starts
them, shows the file in Finder and copies its path: drag the file onto the
program's window.

## 3D data and FIDs

**3D data** opens the way nmrDraw shows it: one plane at a time. Open a plane series
by any one of its planes (`pipedraw ft/test001.ft3`) or by its template
(`pipedraw 'ft/test%03d.ft3'`); single-file 3D data works too. A **Planes** bar
appears above the spectrum:

- Pick the plane orientation (for example ¹H × ¹⁵N, ¹H × ¹³C or ¹⁵N × ¹³C). PipeDraw
  starts on the plane with the strongest signal.
- Step through planes with − / +, the slider, or the ↑ / ↓ (Page Up / Down) keys.
  The label shows the plane's position in ppm.
- **Projection** (or **P**) shows the projection along the stepping axis:
  **Skyline** keeps the largest |intensity| with its sign; **Sum** adds all planes.

Phasing works on planes. Each dimension keeps its own correction, so you can phase
¹H on an ¹H × ¹⁵N plane, switch to ¹H × ¹³C planes to phase ¹³C, then write all
of them at once. The ¹³C (F3) PS step is found in the script's second
`xyz2pipe -z` pass. Projections can't be phased.

**FIDs and partly processed data** open too (`pipedraw test.fid`). Time-domain
axes are shown in points, running forwards, with row and column slices. FIDs open
in View mode, because phasing needs a Fourier-transformed dimension. For a
partly transformed file, only the transformed dimension can be phased.

## Phasing workflow

The phasing panel walks through three numbered steps:

1. **Pick traces**: choose the dimension (¹H direct or ¹⁵N indirect), then click
   the spectrum through strong, isolated peaks. Each trace appears as a chip you
   can remove, and as a coloured line in the traces panel. Change trace height with
   the mouse wheel over the traces or the Height − / + / Fit buttons.
2. **Pivot and zero order**: drag the green pivot onto a large peak, or hover over
   it and press **V**. Fix that peak with **P0**.
3. **First order**: fix peaks far from the pivot with **P1**. **fine 0.1°** and
   **coarse 5°** set the step size for the sliders and arrow keys.

**Changes to nmrproc.com** lists each PS value as it is in the script next to the
new value. **Write PS and reprocess** puts the new values into the right lines,
keeping their layout, then saves, runs and reloads. **Write only** saves without
running; the script then shows a banner with **Undo change**. If a dimension has
no PS step, one is added after its FT.

PS lines are matched to dimensions by following the script's TP steps. P1 and the
pivot follow NMRPipe's convention (P1 runs across the full spectral width at the
time PS runs), so values stay right after `EXT`. This was checked against
nmrPipe 13 on Apple Silicon: a misphased spectrum, corrected in PipeDraw and
reprocessed, matches the correctly processed one exactly.

`demo/` holds a deliberately misphased HSQC with its `nmrproc.com` to try this on.

## Keys

`+` / `-` raise or lower the lowest contour, `F` full view, `V` pivot at cursor,
`Cmd-R` reload, `Cmd-I` header, `Cmd-Return` run script, `Cmd-S` save script. Drag a box to zoom; right-click for pyqtgraph's view menu.

## Limitations of this prototype

- 4D data isn't supported.
- Conversion covers Bruker data; Varian/Agilent and JEOL come later.
- Sparky files written by nmrglue put the carrier at point n/2. NMRFx reads them
  one point (0.013 ppm ¹H, 0.13 ppm ¹⁵N) off from NMRPipe's scale. Check a known
  peak in Sparky/POKY, or install POKY so pipe2ucsf is used.
- On short indirect dimensions (few points), the rebuilt imaginary part is rough,
  so the phased 2D preview can show streaks at low contour levels. The PS values
  are still right; the reprocessed spectrum is clean.
- If the file was processed with `-di`, the imaginary part is rebuilt by Hilbert
  transform (as nmrDraw does). That is exact in the middle of the spectrum, but can
  leave faint artifacts near the edges, especially after `EXT`. For an exact
  preview, leave out `-di` in the dimension you are phasing.

## Tests

```bash
.venv/bin/python tools/make_test_data.py   # synthetic HSQC, correct and misphased
.venv/bin/python tools/make_test_3d.py demo3d   # synthetic 3D set (then: cd demo3d; csh nmrproc.com)
.venv/bin/python -m pytest -q tests
```
