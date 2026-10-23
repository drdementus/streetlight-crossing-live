"""Live-take video renderer for "Crossing at the Streetlight".

Renders 1280x720 @ 30 fps as one continuous take: a nighttime street scene,
a scrolling 18-step sequencer bar (one step per eighth-note triplet
subdivision), a console showing the performance EVENT TRACE (the notes and
drum hits that fired, in order), activity-estimate meters (event velocities
plus the whole-mix RMS -- labelled as estimates, not per-stem measurements),
and section headers. Everything is drawn with PIL and written raw-RGB to
ffmpeg, which muxes it with the untouched WAV.

Deterministic: no wall clock, no RNG in drawing; meters come from the same
mix array the WAV uses. Same inputs -> same frames.

Usage:
  python3 src/video.py --wav OUT.wav --video OUT.mp4 [--frames N]
"""

import argparse
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score
import synth

W, H = 1280, 720
FPS = 30

# Palette (night street under a sodium-vapor lamp)
BG = (10, 12, 20)
SKY = (13, 16, 28)
ROAD = (16, 18, 26)
LAMP = (255, 190, 90)
LAMP_SOFT = (255, 160, 60)
GRID = (38, 44, 66)
PANEL = (18, 22, 36)
PANEL_EDGE = (52, 60, 92)
TEXT = (210, 216, 235)
DIM = (120, 128, 155)
CYAN = (90, 210, 230)
AMBER = (255, 190, 90)
GREEN = (110, 220, 140)
RED = (235, 90, 80)

FONT_BIG = 44
FONT_MED = 26
FONT_SML = 19
FONT_TINY = 15


def load_fonts():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    path = None
    for c in candidates:
        if os.path.exists(c):
            path = c
            break
    if path is None:
        raise SystemExit("no DejaVu font found on this machine")

    def f(size):
        return ImageFont.truetype(path, size)

    return {
        "big": f(FONT_BIG),
        "med": f(FONT_MED),
        "sml": f(FONT_SML),
        "tiny": f(FONT_TINY),
    }


class FrameRenderer:
    def __init__(self, mix, events):
        self.mix = mix
        self.events = events  # sorted (t, voice, label, vel)
        self.fonts = load_fonts()
        self.dur = score.DURATION_S
        # events sorted already; keep parallel arrays for fast scans
        self.et = np.array([e[0] for e in events], dtype=np.float64)
        self.n_events = len(events)
        # skyline silhouette (static, deterministic)
        self.skyline = self._make_skyline()

    def _make_skyline(self):
        pts = []
        x = 0
        heights = [120, 180, 90, 220, 150, 260, 110, 190, 140, 240, 100, 170, 130, 210, 95, 160, 120, 230]
        i = 0
        while x < W:
            h = heights[i % len(heights)]
            w = 40 + (i * 37) % 50
            x2 = min(W, x + w)
            y = H - 190
            pts.append((x, y, x2, y - h))
            x = x2
            i += 1
        return pts

    def voice_energy(self, t):
        """Energy per voice in a 0.6 s window ending at t, from the event list."""
        lo, hi = t - 0.6, t
        sel = (self.et >= lo) & (self.et <= hi)
        idxs = np.where(sel)[0]
        out = {"lead": 0.0, "bass": 0.0, "pad": 0.0, "bell": 0.0, "drum": 0.0}
        for i in idxs:
            _, voice, _, vel = self.events[i]
            if voice in ("lead", "bass", "pad", "bell"):
                out[voice] += vel
            else:
                out["drum"] += vel
        return out

    def meter_value(self, t, voice, level=0.35):
        """dB-scale meter: combine live energy + actual audio RMS over 0.25 s."""
        i = int(t * synth.SR)
        seg = self.mix[max(0, i - int(0.25 * synth.SR)):i]
        if len(seg):
            rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
        else:
            rms = 0.0
        e = self.voice_energy(t)
        val = min(1.0, rms / (level * 0.5) * (0.35 + 0.65 * min(1.0, e[voice] / 2.5)))
        return val

    def current_note(self, t, voice):
        """Most recent event for voice at time t (label only, for the console)."""
        idxs = np.where(self.et <= t)[0]
        if len(idxs) == 0:
            return None
        best = None
        for i in idxs[::-1]:
            if self.events[i][1] == voice:
                best = self.events[i]
                break
        return best

    def section_at(self, t):
        bar = int(t / score.SECONDS_PER_BAR)
        bar = min(bar, score.TOTAL_BARS - 1)
        section, idx = score.section_of_bar(bar)
        return section, idx, bar

    def render_frame(self, t):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        section, idx, bar = self.section_at(t)

        # --- sky gradient (banded, cheap) ---
        for i in range(0, 300, 6):
            f = i / 300.0
            c = (
                int(SKY[0] + 4 * f),
                int(SKY[1] + 4 * f),
                int(SKY[2] + 7 * f),
            )
            d.line([(0, i), (W, i)], fill=c)
        # stars (static, deterministic)
        for i in range(40):
            sx = (i * 173 + 31) % W
            sy = (i * 97 + 13) % 180
            d.point((sx, sy), fill=(90, 95, 120))

        # skyline
        for (x1, y1, x2, y2) in self.skyline:
            d.rectangle([x1, y2, x2, y1], fill=(22, 26, 42))

        # streetlight pole + glow
        px = 1050
        d.rectangle([px - 4, 180, px + 4, H - 150], fill=(45, 50, 70))
        d.rectangle([px - 26, 168, px + 26, 184], fill=(45, 50, 70))
        glow_r = 60 + int(6 * math.sin(2 * math.pi * 0.5 * t))
        for r in range(glow_r, 0, -2):
            a = int(26 * (1 - r / glow_r))
            c = (LAMP[0] // 3, LAMP[1] // 3, LAMP[2] // 3)
            d.ellipse([px - r, 176 - r, px + r, 176 + r], outline=c)
        d.ellipse([px - 10, 166, px + 10, 186], fill=LAMP)

        # road
        d.rectangle([0, H - 150, W, H], fill=ROAD)
        d.line([(0, H - 75), (W, H - 75)], fill=(60, 64, 88), width=3)
        # lane dashes
        for i in range(0, W, 90):
            d.line([(i, H - 75), (i + 40, H - 75)], fill=(90, 96, 130), width=3)

        # --- header ---
        d.rectangle([0, 0, W, 58], fill=(8, 10, 18))
        d.text((16, 10), "CROSSING AT THE STREETLIGHT", font=self.fonts["med"], fill=TEXT)
        d.text((W - 210, 14), "6/8  100 BPM  G MAJOR", font=self.fonts["sml"], fill=DIM)
        live = int(0.6 + 0.4 * math.sin(2 * math.pi * 2.0 * t))
        d.ellipse([W - 168, 20, W - 148, 40], fill=(40, 40, 55))
        d.ellipse([W - 166, 22, W - 150, 38], fill=(RED if live > 0.5 else (90, 60, 60)))
        d.text((W - 138, 16), "LIVE", font=self.fonts["med"], fill=RED if live > 0.5 else DIM)

        # section banner
        banner = "SECTION: " + section + "   BAR %d/%d" % (bar + 1, score.TOTAL_BARS)
        d.text((16, 66), banner, font=self.fonts["sml"], fill=CYAN)

        # --- step sequencer panel ---
        panel = (16, 110, W - 16, 330)
        d.rectangle(panel, fill=PANEL, outline=PANEL_EDGE)
        d.text((30, 120), "STEP SEQUENCER", font=self.fonts["sml"], fill=DIM)
        # 18 steps across
        x0, x1 = 40, W - 40
        step_w = (x1 - x0) / 18.0
        eighth = t % 0.6
        s16_pos = t / score.TRIPLET_S % 18.0
        # collect active step highlights from events in the current bar
        bar_start = bar * score.SECONDS_PER_BAR
        cur_events = [e for e in self.events if bar_start <= e[0] < bar_start + score.SECONDS_PER_BAR]
        active = {}  # s16 -> (label, vel)
        for (et, voice, label, vel) in cur_events:
            if et <= t:
                s16 = int(round((et - bar_start) / score.TRIPLET_S))
                if s16 not in active or vel > active[s16][1]:
                    active[s16] = (label, vel)
        for s in range(18):
            cx = x0 + s * step_w
            rect = [cx + 2, 160, cx + step_w - 2, 240]
            if s in active:
                label, vel = active[s]
                color = GREEN if label == "K" else (AMBER if label in ("S", "H") else CYAN)
                d.rectangle(rect, fill=(30, 40, 40), outline=color)
                d.text((cx + step_w / 2 - 10, 250), label, font=self.fonts["sml"], fill=color)
            else:
                d.rectangle(rect, fill=(20, 24, 38), outline=GRID)
        # playhead
        phx = x0 + (s16_pos / 18.0) * (x1 - x0)
        d.line([(phx, 150), (phx, 250)], fill=LAMP, width=3)
        d.polygon([(phx - 6, 144), (phx + 6, 144), (phx, 156)], fill=LAMP)
        # note readouts
        line = []
        for voice, colr in (("lead", CYAN), ("bass", AMBER), ("bell", GREEN)):
            e = self.current_note(t, voice)
            if e is not None:
                line.append("%s=%s" % (voice[:2].upper(), e[2]))
        if line:
            d.text((30, 285), "NOW  " + "   ".join(line), font=self.fonts["med"], fill=TEXT)

        # --- console panel: the performance event trace (notes/hits that fired) ---
        cpanel = (16, 344, 700, H - 24)
        d.rectangle(cpanel, fill=PANEL, outline=PANEL_EDGE)
        d.text((30, 352), "PERFORMANCE EVENT TRACE (most recent)", font=self.fonts["sml"], fill=DIM)
        # last 9 events as a trace of (voice, note, velocity)
        upto = np.searchsorted(self.et, t, side="right")
        start = max(0, upto - 9)
        y = 384
        for i in range(upto - 1, start - 1, -1):
            (et, voice, label, vel) = self.events[i]
            age = t - et
            alpha_col = TEXT if age < 1.0 else DIM
            if voice == "lead":
                code = 'lead   note=%-4s vel=%.2f' % (label, vel)
            elif voice == "bass":
                code = 'bass   note=%-4s vel=%.2f' % (label, vel)
            elif voice == "pad":
                code = 'pad    note=%-4s (sustain)' % (label,)
            elif voice == "bell":
                code = 'bell   note=%-4s vel=%.2f' % (label, vel)
            elif voice == "kick":
                code = 'kick   vel=%.2f' % vel
            elif voice == "snare":
                code = 'snare  vel=%.2f' % vel
            else:
                code = 'hat    vel=%.2f' % vel
            d.text((36, y), code, font=self.fonts["tiny"], fill=alpha_col)
            y += 21

        # --- meters panel (activity estimates, not per-stem measurements) ---
        mpanel = (720, 344, W - 16, H - 24)
        d.rectangle(mpanel, fill=PANEL, outline=PANEL_EDGE)
        d.text((734, 352), "ACTIVITY (estimates)", font=self.fonts["sml"], fill=DIM)
        mlabels = [("LEAD", "lead", CYAN), ("BASS", "bass", AMBER),
                   ("PAD", "pad", (170, 150, 220)), ("BELL", "bell", GREEN),
                   ("DRUM", "drum", RED), ("MIX", None, TEXT)]
        y = 392
        for name, key, colr in mlabels:
            d.text((734, y), name, font=self.fonts["tiny"], fill=DIM)
            if key is None:
                seg = self.mix[max(0, int(t * synth.SR) - int(0.25 * synth.SR)):int(t * synth.SR)]
                rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))) if len(seg) else 0.0
                v = min(1.0, rms / 0.22)
            else:
                v = self.meter_value(t, key)
            mx0, mx1 = 800, 1230
            d.rectangle([mx0, y + 2, mx1, y + 18], outline=GRID, fill=(12, 14, 24))
            fw = int((mx1 - mx0 - 2) * v)
            d.rectangle([mx0 + 1, y + 3, mx0 + 1 + fw, y + 17], fill=colr)
            y += 34

        # --- footer: honest disclosure + clock ---
        d.text((16, H - 18), "performed by software, one continuous take  -  no audio filters",
               font=self.fonts["tiny"], fill=DIM)
        mm = int(t // 60)
        ss = int(t % 60)
        d.text((W - 150, H - 18), "%02d:%02d" % (mm, ss), font=self.fonts["sml"], fill=TEXT)

        return img


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--wav", required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--frames", type=int, default=None)
    args = p.parse_args(argv)

    dur = score.DURATION_S
    n_frames = args.frames or int(dur * FPS)

    import wave
    with wave.open(args.wav, "rb") as w:
        assert w.getframerate() == synth.SR
        assert w.getnchannels() == 2
        raw = w.readframes(w.getnframes())
    import numpy as np
    stereo = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
    mono = stereo[:, 0].astype(np.float32) / 32768.0

    _, events = synth.render(dur)  # deterministic event list (drives console + meters)
    # mix for meters: re-render to be safe (identical to the wav)
    mix, _ = synth.render(dur)

    fr = FrameRenderer(mix, events)

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", "%dx%d" % (W, H), "-pix_fmt", "rgb24", "-r", str(FPS),
        "-i", "-",
        "-i", args.wav,
        "-c:v", "libx264", "-preset", "medium", "-b:v", "1200k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-metadata", "title=Crossing at the Streetlight (digital street music)",
        "-metadata", "artist=digital street performer (autonomous software)",
        args.video,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n_frames):
        t = min(i / FPS, dur - 1e-6)
        img = fr.render_frame(t)
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    rc = proc.wait()
    if rc != 0:
        raise SystemExit("ffmpeg exited %d" % rc)
    print("wrote %s: %d frames, %.2f s" % (args.video, n_frames, n_frames / FPS))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
