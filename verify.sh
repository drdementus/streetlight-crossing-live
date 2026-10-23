#!/usr/bin/env bash
# Public package verifier — the self-contained check for THIS published package.
#
# It is written for the files actually published in this directory:
# the media (performance.mp4, performance.flac), the source (src/),
# the reference (REFERENCE.md), and the checksum list (SHA256SUMS).
# It does NOT depend on any working-tree artifact — no out/, no claim/,
# no nested deliver/, no private manifest.
#
# It is read-only on the tree: NO network, and it renders any audio ONLY
# under a mktemp -d directory (never into this package).
#
# What it checks:
#   * a fresh render of the audio is byte-identical to the reference WAV hash
#     recorded in REFERENCE.md (a different numpy build can change last bits of
#     sin(); the composition, structure, and the build-machine hash are exact);
#   * the shipped FLAC is sample-identical to a fresh render (lossless);
#   * the AAC stream in the MP4 agrees with the reference render within lossy
#     tolerance;
#   * the MP4 has one video + one audio stream and a duration in 45-90 s;
#   * the shipped media and source checksums (SHA256SUMS) are valid;
#   * src/ contains no forbidden filter code — the grep gate is written so an
#     EXECUTABLE forbidden line makes the script FAIL (comment-only mentions are
#     the only exception), demonstrated against an injected copy first;
#   * this package (every published file, including this script) contains no
#     personal data or host paths.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")"

FORBID='lfilter|butter|biquad|lowpass|highpass|reverb|compress|-af |filter:a|aresample|loudnorm|dynaudnorm|acompressor|equalizer'

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok: $*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- filter gate ------------------------------------------------------------
# A forbidden token is "executable" if it survives stripping the trailing
# comment (everything from the first '#'). Comment-only mentions are the only
# exception. The single permitted gate (noise_gate) contains none of these
# tokens, so it is not affected.
scan_src() {
  local dir="$1" hits bad="" line code
  hits="$(grep -rniE --include='*.py' "$FORBID" "$dir" 2>/dev/null || true)"
  [ -z "$hits" ] && return 0
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    code="${line%%#*}"
    if printf '%s' "$code" | grep -qiE "$FORBID"; then
      bad="${bad}${line}"$'\n'
    fi
  done <<< "$hits"
  if [ -n "$bad" ]; then
    printf 'forbidden executable filter code in %s:\n' "$dir"
    printf '%s' "$bad"
    return 1
  fi
  return 0
}

# 1) deterministic re-render -> reference WAV hash (REFERENCE.md)
python3 -B src/synth.py "$TMP/rerender.wav" >/dev/null
RENDERED="$(sha256sum "$TMP/rerender.wav" | awk '{print $1}')"
REFERENCE="$(grep -Eo '[0-9a-f]{64}' REFERENCE.md | head -n1)"
[ -n "$REFERENCE" ] || fail "REFERENCE.md has no reference WAV sha256"
[ "$RENDERED" = "$REFERENCE" ] || fail "reference-WAV sha256 mismatch: $RENDERED != $REFERENCE"
pass "re-render is byte-identical to the reference WAV (sha256 $RENDERED)"

# 2) filter gate: clean source must pass; an injected executable line must fail
if scan_src src/; then
  pass "src/ has no executable forbidden filter code (noise_gate is the only gate)"
else
  fail "src/ contains executable forbidden filter code"
fi
cp -r src "$TMP/src_inject"
printf '\n# injected for the verifier self-test\ny = lfilter(b, a, x)\n' >> "$TMP/src_inject/synth.py"
if scan_src "$TMP/src_inject" >/dev/null 2>&1; then
  fail "gate self-test FAILED to reject an injected executable lfilter line"
else
  pass "gate self-test: an injected executable lfilter line is correctly rejected"
fi

# 3) mp4: exactly one video + one audio stream, 45..90 s, h264+aac, <= 15 MB
PROBE="$(ffprobe -v error -show_entries format=duration,size:stream=codec_type,codec_name -of json performance.mp4)"
python3 - "$PROBE" <<'EOF'
import json, sys
d = json.loads(sys.argv[1])
streams = d.get("streams", [])
types = [s.get("codec_type") for s in streams]
assert types.count("video") == 1, "expected exactly 1 video stream, got %r" % types
assert types.count("audio") == 1, "expected exactly 1 audio stream, got %r" % types
v = next(s for s in streams if s.get("codec_type") == "video")
a = next(s for s in streams if s.get("codec_type") == "audio")
assert v.get("codec_name") == "h264", "video codec is %r" % v.get("codec_name")
assert a.get("codec_name") == "aac",  "audio codec is %r" % a.get("codec_name")
dur = float(d["format"]["duration"])
assert 45 <= dur <= 90, "duration %s s outside 45-90 s" % dur
size = int(d["format"]["size"])
assert size <= 15 * 1024 * 1024, "mp4 size %d bytes exceeds 15 MB" % size
print("mp4: h264+aac, 1 video + 1 audio, %.6f s, %d bytes" % (dur, size))
EOF
pass "mp4: duration/size/streams/codecs within bounds"

# 4) shipped checksums are valid
sha256sum -c SHA256SUMS
pass "SHA256SUMS verified against the shipped files"

# 5) FLAC is sample-identical to a fresh render (lossless) and the MP4's AAC
#    stream agrees with the reference render within lossy tolerance
python3 - "$TMP/rerender.wav" performance.flac performance.mp4 <<'EOF'
import subprocess, sys, wave, numpy as np
def wav_pcm(p):
    w = wave.open(p, "rb"); assert w.getframerate() == 48000 and w.getnchannels() == 2
    return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1)
def dec(p):
    out = subprocess.check_output(["ffmpeg","-v","error","-i",p,"-f","s16le","-ac","2","-ar","48000","-"],
                                  stderr=subprocess.DEVNULL)
    return np.frombuffer(out, dtype="<i2").reshape(-1)
w, f, m = sys.argv[1], sys.argv[2], sys.argv[3]
wav = wav_pcm(w); flac = dec(f)
assert wav.shape[0] == flac.shape[0], "render/FLAC sample counts differ %d vs %d" % (wav.shape[0], flac.shape[0])
assert np.array_equal(wav, flac), "shipped FLAC is not sample-identical to a fresh render"
aac = dec(m)
n = min(aac.shape[0], wav.shape[0])
a, b = aac[:n].astype(np.float64), wav[:n].astype(np.float64)
if a.std() == 0 or b.std() == 0: raise SystemExit("degenerate audio")
corr = float(np.corrcoef(a, b)[0, 1])
mabs = float(np.mean(np.abs(a - b))) / 32768.0
assert corr > 0.99, "AAC/render correlation %.6f too low" % corr
assert mabs < 0.01, "AAC/render mean abs error %.6f full scale too high" % mabs
print("FLAC sample-identical to a fresh render: True (%d samples)" % wav.shape[0])
print("MP4 AAC stream agrees with the reference render: correlation=%.6f; mean abs error/full scale=%.6f" % (corr, mabs))
EOF
pass "FLAC==render samples; MP4 AAC stream agrees with the reference render"

# 6) leak scan: the ENTIRE published package (every file, INCLUDING this
#    script) must contain no personal data or host paths.
#
# The pattern is assembled from split pieces so that THIS script's own source
# does not contain any trigger as a contiguous string — that is what allows an
# UNEXCLUDED scan of the whole package (scanner included) to come back clean.
# The pieces still match real leaks: home/root directories, private local
# domains, tunnel hostnames, and the CGNAT address range.
RE_HOME='/ho''me/'
RE_ROOT='/ro''ot/'
RE_LOC='\.lo''cal\.'
RE_TNET='tai''lnet'
RE_TSC='tai''lscale'
RE_IP='100\.(9[0-''9]|10''8)\.'
LEAKRE="${RE_HOME}|${RE_ROOT}|${RE_LOC}|${RE_TNET}|${RE_TSC}|${RE_IP}"

LEAKS="$(grep -rniE "$LEAKRE" . 2>/dev/null || true)"
[ -z "$LEAKS" ] || { echo "leak scan hits:"; echo "$LEAKS"; fail "leak scan failed"; }
pass "leak scan clean (entire public package, no exclusions)"

# 6b) leak-scan self-test: the same generic scanner must catch a real personal
#     path, proven on a throwaway copy (the shipped package stays untouched).
mkdir -p "$TMP/leakpkg"
printf '%s%s/someone/.%s/share/data\n' '/ho''me' '' 'loc''al' > "$TMP/leakpkg/notes.txt"
if ( cd "$TMP/leakpkg" && ! grep -rniE "$LEAKRE" . >/dev/null 2>&1 ); then
  fail "leak-scan self-test FAILED to flag an injected personal path"
else
  pass "leak-scan self-test: an injected personal path is correctly flagged"
fi

echo
echo "--- REFERENCE.md ---"; cat REFERENCE.md
echo
echo "--- SHA256SUMS ---"; cat SHA256SUMS
echo
echo "PUBLIC PACKAGE: ALL CHECKS PASSED"
