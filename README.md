# Crossing at the Streetlight — a digital street music performance

An original piece in **G major, 6/8 at 100 bpm**, 86.4 seconds, one continuous take.
It was **performed entirely by software** — a deterministic synthesizer and a
frame-by-frame video renderer — in the spirit of digital street music: a program
on the corner, playing for anyone who stops. There is no human performance,
no sample audio, and no AI audio model anywhere in this project.

## The no-filter signal chain

The audio path contains **only** these stages, in this order — and nothing else:

1. **Oscillators**: pure sine, triangle, additive partials (fundamental + 2nd
   partial for the lead; root + major third + octave for the pad; root +
   triangle octave for the bass), and one FM voice (sine modulator on a sine
   carrier with an exponentially decaying index) for the bell.
   Kick is a pitch-drop oscillator (92 Hz → 48 Hz). Snare is a decaying
   white-noise burst. Hat is a decaying white-noise burst.
2. **Gain envelopes** (attack/release, tremolo = amplitude modulation) —
   gain as a function of time, which is not filtering.
3. **Mixing**: a linear sum of per-voice signals, each multiplied by a
   fixed, hand-chosen voice gain. The gains are chosen to leave headroom
   below full scale, so the mix stays inside the 16-bit range and the
   integer conversion never clamps a sample (enforced by an assertion in the
   code, not by a processor — there is no limiter, clipper or ceiling).
4. **One noise gate** on the drum bus (hard threshold, fixed range) — the
   single gate the rules allow.

There is **no low-pass, high-pass, band processing, EQ, reverb, delay,
compression, limiting, loudness normalization, resampling, or dithering**
anywhere in `src/`. Timbre comes only from waveform choice, additive partials,
and FM. The shipped `verify.sh` greps the source for exactly the forbidden
filter terms and fails on any executable hit (see "How the verification works"
below — the blacklist is one check, not the whole proof).

## What you are hearing

The piece is in **6/8 at 100 bpm**; each 1.80 s bar is divided into 18 equal
steps of 0.10 s, each an **eighth-note triplet** subdivision (an eighth holds
three triplet steps — it is a triplet grid, not a sixteenth-note grid). Form,
and the highest note the **lead melody** actually reaches in each section, as
read from the rendered bars of the score data that drives the audio:

- **Intro** (bars 1–8): pad and drums build up; the lead enters with the theme.
- **Theme** (bars 9–20): the melody — a clear G-major line, peaking at A5.
- **Variation** (bars 21–32): same harmony, a busier melody with a bell
  counter-line; the lead again peaks at A5, while the bell reaches A6.
- **Bridge** (bars 33–40): the lead continues at its peak register (A5).
- **Outro** (bars 41–48): the line thins out and ends on a G (a G5–E5–D5
  figure over a sustained G pad); the bell rings a final G6.

In short the melody stays in the A5 range across the theme, variation and
bridge (A5 is the top of the rendered lead line), and the piece closes on G.
These descriptions are generated from the same rendered score data that drives
the audio, so the text, the video labels and the recording agree.

## How to re-render and verify

Prerequisites: `python3` with **`numpy` and `Pillow` (PIL)**, **`ffmpeg`**
(ffmpeg + ffprobe), and a DejaVu font (standard on most Linux systems).

### The two verifiers

There are **two** verification entry points, for two different scopes, and this
package ships the public one:

- **Public — `verify.sh` (this directory).** Self-contained: it only needs the
  files published here (the media, `src/`, `REFERENCE.md`, `SHA256SUMS`). It
  does not need any build artifact, runs `sha256sum -c` on the shipped media and
  source, re-renders the audio into a **temporary directory** and compares it to
  the **reference WAV hash**, checks the FLAC sample-identity and the MP4,
  enforces the filter gate, and leak-scans the whole package. Run it from a
  fresh copy of this directory:

  ```sh
  bash verify.sh
  ```

- **Working tree — the repo's top-level `verify.sh`.** In addition to the above
  it checks the claim contract and the claim image (a real video frame), which
  live in the working tree and are not part of the public package.

`verify.sh` is the self-contained verifier **shipped in this package**. Run it
from this directory:

```sh
bash verify.sh
```

It checks, **without network and writing only to a temp directory**:
- a fresh re-render of the audio is byte-identical to the **reference WAV hash
  recorded in `REFERENCE.md`**;
- the MP4 has one video + one audio stream and a duration in the 45–90 s window;
- `src/` contains no forbidden filter code — the grep gate is written so that an
  **executable** forbidden line makes the script **fail** (and it demonstrates
  this against an injected copy before trusting the clean source);
- the FLAC is sample-identical to the reference render, and the AAC stream in
  the MP4 agrees with the render within lossy tolerance;
- the shipped media and source checksums (`SHA256SUMS`) are valid;
- this package (including the scanner itself) contains no personal data or host
  paths.

It does **not** rely on the blacklist alone to establish "no clipping": the
source's own headroom assertion is what guarantees the mix stays in range, and
`verify.sh` re-runs the render so that assertion is exercised on every check.

### Re-rendering the audio and checking it against the public reference

```sh
# 1) render the audio (deterministic) — synth.py creates the out/ dir itself
python3 src/synth.py out/performance.wav

# 2) encode the freshly rendered WAV to FLAC exactly as the shipped one
ffmpeg -y -i out/performance.wav -c:a flac out/performance.flac -loglevel error

# 3) the shipped public audio is sample-identical to your render:
python3 - <<'PY'
import subprocess
def flac_pcm(p):
    return subprocess.check_output(["ffmpeg","-v","error","-i",p,"-f","f64le","-"],
                                   stderr=subprocess.DEVNULL)
a = flac_pcm("performance.flac"); b = flac_pcm("out/performance.flac")
print("public FLAC sample-identical to fresh render:", a == b, "bytes:", len(a))
PY

# 4) compare the fresh render to the reference WAV hash in REFERENCE.md
#    (exits 0 on a match, nonzero on a mismatch):
REF=$(awk '$2=="performance.wav"{print $1}' REFERENCE.md)
ACT=$(sha256sum out/performance.wav | awk '{print $1}')
[ "$REF" = "$ACT" ] && echo "reference match: $ACT" || { echo "reference MISMATCH: $ACT != $REF" >&2; exit 1; }

# 5) the video, if you want to re-mux it (optional):
python3 src/video.py --wav out/performance.wav --video out/performance.mp4
```

The render is **bit-for-bit reproducible** on the same toolchain: same source
+ same numpy build → same WAV bytes, so `sha256sum out/performance.wav` matches
the **reference WAV sha256 recorded in `REFERENCE.md`**. The public package
ships the FLAC because the WAV is large; the FLAC is the identical samples, so
`performance.flac` is the stable public audio reference. A different numpy build
or platform can in principle produce last-bit differences in `sin()`; the
composition, structure and durations, and the build-machine hash, are exactly
reproducible.

## Files

| File | What |
|---|---|
| `performance.mp4` | the 86.4 s performance video (H.264 + AAC, one take) |
| `performance.flac` | lossless copy of the exact same audio (identical samples) |
| `src/score.py` | the complete composition as data (bars, notes, velocities) |
| `src/synth.py` | the deterministic synthesizer (the full signal chain above) |
| `src/video.py` | the live-take video renderer |
| `REFERENCE.md` | the reference WAV sha256 + a runnable hash comparison |
| `SHA256SUMS` | checksums for the shipped media and source |
| `verify.sh` | the self-contained public verifier described above |

## Licensing

- Code: **MIT License** (see `LICENSE`).
- Recording and video: **CC BY 4.0** (see `LICENSE`) — share and adapt with
  credit; credit line: *"Crossing at the Streetlight", a digital street music
  performance by software, CC BY 4.0*.
