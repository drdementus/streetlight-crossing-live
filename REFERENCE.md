# Reference values

This file records the reference hash for the audio, so a re-render can be
checked against the build machine's exact output.

## Reference WAV sha256

The composition renders to a 48 kHz / stereo / 16-bit WAV. On the build
machine (python 3.11.15, numpy 2.4.3, ffmpeg 8.0.1) the render is byte-for-byte
reproducible and hashes to:

```
d58e2cb580265d52f95112b63eda12132acd62bdb314f95d7c6246fe9525410e  performance.wav
```

A different numpy build or platform can in principle change the last bits of
`sin()`, so a re-render on other toolchains may differ by a few low-order bits
while staying the same composition. This hash is the build-machine reference.

## How to compare

From the root of this package (after `python3 src/synth.py out/performance.wav`
has written a fresh render into a temporary `out/`), run:

```
REF=$(awk '$2=="performance.wav"{print $1}' REFERENCE.md)
ACT=$(sha256sum out/performance.wav | awk '{print $1}')
[ "$REF" = "$ACT" ] && echo "reference match: $ACT" || { echo "reference MISMATCH: $ACT != $REF" >&2; exit 1; }
```

That command exits 0 on a match and nonzero on a mismatch. `verify.sh`
performs the same comparison: it re-renders the audio and fails (nonzero
exit) if the rendered hash differs from the reference above. Proving that
the comparison also fails when the recorded value is wrong is part of the
**private regression scope** (the private checker, which is not part of this
public package), not something the public `verify.sh` demonstrates.
