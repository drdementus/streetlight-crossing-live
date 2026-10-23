"""Deterministic software performance engine for "Crossing at the Streetlight".

Signal chain (the ENTIRE audio path -- nothing else exists in this program):
  oscillators (sine / triangle / FM sine-on-sine / additive partials)
    + time-varying gain envelopes (attack / release / tremolo)
    + per-voice gain mix (linear sums with FIXED, predetermined voice gains)
    + one noise gate on the drum bus (the single permitted gate)
  The fixed voice gains are chosen to leave headroom below full scale, so the
  pre-quantization mix always stays inside the PCM range and the 16-bit
  integer conversion never changes (clamps) a sample. That is enforced by an
  assertion, not by a processor: there is no limiter, no clipper, no
  measured-peak normalization and no constant ceiling in this code.
  There is no band-shaping, EQ, spatial effect, feedback effect, dynamic
  processing, loudness stage, rate conversion, or dither stage anywhere in
  the code. Timbre comes entirely from waveform choice, additive partials,
  and FM.

Pitch: scientific pitch notation, A4 = 440 Hz, equal temperament. Explicit
chromatic offsets C=0 D=2 E=4 F=5 G=7 A=9 B=11 (semitones), +1 per sharp,
+12 per octave.

Determinism: numpy + one fixed legacy RNG seed (20261018). No wall clock, no
entropy, no threads. Same input -> same bytes.

Usage: python3 src/synth.py OUT.wav [--duration N]
"""

import math
import os
import sys

import numpy as np

import score

SR = 48000
C4_HZ = 261.6255653005986
TWO_PI = 2.0 * np.pi

# Explicit chromatic offset (semitones from C) for each natural note letter.
NOTE_OFFSETS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_freq(name):
    """Name -> Hz. Scientific pitch notation, A4 = 440, equal temperament.

    letter + optional '#' + octave digit. Uses the explicit chromatic
    offsets C=0 D=2 E=4 F=5 G=7 A=9 B=11, +1 per sharp, +12 per octave.
    """
    letter = name[0]
    sharp = "#" in name
    base = NOTE_OFFSETS[letter]
    octave = int(name[-1])
    semis = 12 * (octave - 4) + base + (1 if sharp else 0)
    return C4_HZ * (2.0 ** (semis / 12.0))


def env_ad(t, dur, attack):
    """Attack-decay gain with a 40 ms anti-click release at the note's end."""
    a = max(0.003, min(attack, dur))
    g = np.minimum(1.0, t / a)
    release = min(0.04, dur * 0.5)
    m = dur - t < release
    g = np.where(m, g * (dur - t) / release, g)
    return g


def make_leadsynth(freq, dur, vel, attack=0.006):
    """Lead voice: fundamental sine + a quiet 2nd partial (additive timbre)."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = np.sin(TWO_PI * freq * t) + 0.30 * np.sin(TWO_PI * 2.0 * freq * t)
    return vel * env_ad(t, dur, attack) * s


def make_pad(freq, dur, vel, attack=0.18):
    """Pad voice: additive triad (root, major 3rd, octave) + slow tremolo.
    Tremolo is amplitude modulation (gain over time) -- a mixing-domain
    operation, not a band-shaping operation."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = (
        0.62 * np.sin(TWO_PI * freq * t)
        + 0.24 * np.sin(TWO_PI * freq * 1.2599210498948732 * t)
        + 0.14 * np.sin(TWO_PI * 2.0 * freq * t)
    )
    trem = 1.0 - 0.14 + 0.14 * (0.5 + 0.5 * np.sin(TWO_PI * 0.28 * t))
    return vel * env_ad(t, dur, attack) * trem * s


def make_bass(freq, dur, vel, attack=0.005):
    """Bass voice: sine root + triangle octave (additive pluck character)."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = np.sin(TWO_PI * freq * t) + 0.35 * (2.0 / np.pi * np.arcsin(np.sin(TWO_PI * 2.0 * freq * t)))
    return vel * env_ad(t, dur, attack) * s


def make_bell(freq, dur, vel):
    """Bell voice: FM, sine modulator on sine carrier, exponentially decaying
    index and level. No filters involved."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    decay = np.exp(-5.0 * t / dur)
    idx = 3.6 * decay
    mod = np.sin(TWO_PI * 4.6 * freq * t)
    carrier = np.sin(TWO_PI * freq * t + idx * mod)
    return vel * decay * carrier


def make_kick(vel):
    """Kick: pitch drop 92 Hz -> 48 Hz over 150 ms, 250 ms exponential tail."""
    dur = 0.25
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = np.where(t < 0.15, 48.0 + (92.0 - 48.0) * ((0.15 - t) / 0.15) ** 2, 48.0)
    phase = np.cumsum(TWO_PI * f) / SR
    return vel * np.exp(-14.0 * t) * np.sin(phase)


def make_noise_hit(vel, dur, decay, white):
    """White-noise burst with exponential decay (snare noise / hat)."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    return vel * white[:n] * np.exp(-decay * t)


def noise_gate(buf, threshold=0.010, range_db=14.0):
    """The single permitted gate: hard threshold on the drum bus.

    Below threshold the bus is attenuated by a fixed ratio; above it, unity.
    A plain threshold gate -- no dynamics beyond the cut itself.
    """
    ratio = 10.0 ** (-range_db / 20.0)
    return np.where(np.abs(buf) >= threshold, buf, buf * ratio)


# Fixed, predetermined voice gains. Chosen by hand to leave headroom below
# full scale so the pre-quantization mix stays inside the PCM range. They are
# constants -- never derived from the measured peak of this take.
GAIN_LEAD = 0.26
GAIN_BASS = 0.28
GAIN_PAD = 0.13
GAIN_BELL = 0.09
GAIN_DRUM = 0.34

# The pre-quantization mix must stay strictly inside the PCM range so the
# 16-bit integer conversion changes no sample. This is a design bound,
# enforced by an assertion -- not a limiter that reacts to the signal.
MIX_PEAK_LIMIT = 0.98


def render(duration):
    """Render the whole piece into one mono float32 array. Deterministic.

    Returns (mix, events) where events = sorted list of
    (time_s, voice, label, velocity) -- the same list drives the video.

    The returned mix is guaranteed (by assertion) to stay within the PCM
    range, so downstream 16-bit conversion is a no-op clamp.
    """
    rng = np.random.RandomState(20261018)
    total = int(duration * SR)
    headroom = int(0.25 * SR)  # room for a note whose tail crosses the end boundary
    buf_len = total + headroom
    white = rng.uniform(-1.0, 1.0, buf_len)
    lead = np.zeros(buf_len, dtype=np.float32)
    bass = np.zeros(buf_len, dtype=np.float32)
    pad = np.zeros(buf_len, dtype=np.float32)
    bell = np.zeros(buf_len, dtype=np.float32)
    drum = np.zeros(buf_len, dtype=np.float32)
    events = []

    step = score.TRIPLET_S

    def place(buf, samples, at):
        i0 = int(at * SR)
        n = min(len(samples), len(buf) - i0)
        buf[i0:i0 + n] += samples[:n]

    for bar in range(score.TOTAL_BARS):
        section, idx = score.section_of_bar(bar)
        bar_t = bar * score.SECONDS_PER_BAR
        key = section.lower()

        if idx < len(score.LEAD[key]):
            for s16, d16, note, vel in score.LEAD[key][idx]:
                t0 = bar_t + s16 * step
                place(lead, make_leadsynth(note_freq(note), d16 * step, vel).astype(np.float32), t0)
                events.append((t0, "lead", note, vel))

        if idx < len(score.BASS[key]):
            for s16, d16, note, vel in score.BASS[key][idx]:
                t0 = bar_t + s16 * step
                place(bass, make_bass(note_freq(note), d16 * step, vel).astype(np.float32), t0)
                events.append((t0, "bass", note, vel))

        if idx < len(score.PAD[key]):
            for note, s16, d16, vel in (score.PAD[key][idx] or []):
                t0 = bar_t + s16 * step
                place(pad, make_pad(note_freq(note), d16 * step, vel).astype(np.float32), t0)
                events.append((t0, "pad", note, vel))

        if idx < len(score.BELL[key]):
            for s16, d16, note, vel in (score.BELL[key][idx] or []):
                t0 = bar_t + s16 * step
                place(bell, make_bell(note_freq(note), d16 * step, vel).astype(np.float32), t0)
                events.append((t0, "bell", note, vel))

        if idx < len(score.DRUMS[key]):
            pat = score.DRUMS[key][idx]
            eighth = 3.0 * step
            for slot in sorted(pat.get("K", {})):
                t0 = bar_t + slot * eighth
                place(drum, make_kick(pat["K"][slot]).astype(np.float32), t0)
                events.append((t0, "kick", "K", pat["K"][slot]))
            for slot in sorted(pat.get("S", {})):
                t0 = bar_t + slot * eighth
                place(drum, make_noise_hit(pat["S"][slot], 0.11, 42.0, white).astype(np.float32), t0)
                events.append((t0, "snare", "S", pat["S"][slot]))
            for slot in sorted(pat.get("H", {})):
                t0 = bar_t + slot * eighth
                place(drum, make_noise_hit(pat["H"][slot], 0.045, 75.0, white).astype(np.float32), t0)
                events.append((t0, "hat", "H", pat["H"][slot]))

    events.sort(key=lambda e: e[0])

    lead, bass, pad, bell, drum = (v[:total] for v in (lead, bass, pad, bell, drum))
    drum = noise_gate(drum)
    mix = (
        lead * GAIN_LEAD
        + bass * GAIN_BASS
        + pad * GAIN_PAD
        + bell * GAIN_BELL
        + drum * GAIN_DRUM
    )
    mix = mix.astype(np.float32)

    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    assert peak < MIX_PEAK_LIMIT, (
        "pre-quantization mix peak %.6f exceeds the headroom bound %.3f -- "
        "lower the fixed voice gains (GAIN_*); do NOT add a limiter/clipper"
        % (peak, MIX_PEAK_LIMIT)
    )
    return mix, events


def write_wav(path, mix):
    """Stereo 16-bit WAV; both channels identical (no stereo processing).

    The mix is guaranteed (see render) to stay inside the PCM range, so the
    clamp below is a no-op: we assert it changes zero samples.
    """
    scaled = np.round(mix.astype(np.float64) * 32767.0)
    assert scaled.size == 0 or (float(scaled.min()) >= -32768 and float(scaled.max()) <= 32767), (
        "integer conversion would clamp a sample (min %.1f max %.1f)"
        % (float(scaled.min()), float(scaled.max()))
    )
    i16 = np.clip(scaled, -32768, 32767).astype("<i2")
    stereo = np.ascontiguousarray(np.stack([i16, i16], axis=1))
    header = (
        b"RIFF" + np.uint32(36 + stereo.nbytes).tobytes() + b"WAVE"
        + b"fmt " + np.uint32(16).tobytes()
        + np.uint16(1).tobytes()                      # PCM
        + np.uint16(2).tobytes()                      # 2 channels
        + np.uint32(SR).tobytes()                     # sample rate
        + np.uint32(SR * 4).tobytes()                 # byte rate
        + np.uint16(4).tobytes()                      # block align
        + np.uint16(16).tobytes()                     # bits per sample
        + b"data" + np.uint32(stereo.nbytes).tobytes()
    )
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as f:
        f.write(header)
        f.write(stereo.tobytes())
    return len(mix)


def main(argv):
    args = list(argv)
    if not args:
        print("usage: synth.py OUT.wav [--duration N]", file=sys.stderr)
        return 2
    out = args[0]
    dur = score.DURATION_S
    i = 1
    while i < len(args):
        if args[i] == "--duration" and i + 1 < len(args):
            dur = float(args[i + 1])
            i += 2
        else:
            i += 1
    mix, events = render(dur)
    n = write_wav(out, mix)
    peak = float(np.max(np.abs(mix)))
    rms = float(math.sqrt(np.mean(mix.astype(np.float64) ** 2)))
    print("rendered %s: %d samples (%.2f s), peak=%.4f rms=%.4f, %d events"
          % (out, n, n / SR, peak, rms, len(events)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
