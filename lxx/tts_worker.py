"""Synthesis worker. Runs inside .venv-tts, which has piper and onnxruntime.

The main application stays dependency-free; it speaks to this process over a
JSON-lines protocol on stdin/stdout. Keeping it resident matters because
loading the ONNX model costs about a second, while synthesising a word from an
already-loaded model costs a few tens of milliseconds.

Text goes in as monotonic Greek and the voice phonemizes it itself, with its
own default synthesis settings. An earlier version generated IPA by hand to
offer a choice of historical pronunciations; it sounded wrong, because the
voice only ever trained on modern Greek phonemes and on its own conventions
for stress and palatalisation. Letting it do the job it was trained for is
both simpler and correct.

Protocol -- one JSON object per line:

    in   {"text": "εν αρχή εποίησεν", "out": "/path/file.wav"}
    out  {"ok": true, "samples": 28160}
    out  {"ok": false, "error": "..."}
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np
from piper import PiperVoice

#: Amplitude below which a sample counts as silence when trimming the ends.
SILENCE_FLOOR = 0.02
#: Padding left either side of the speech, so clips do not sound clipped off.
PAD_SECONDS = 0.04
#: Target peak, so a single word and a whole verse play back at one level.
TARGET_PEAK = 0.89


def polish(audio: np.ndarray, rate: int) -> np.ndarray:
    """Trim silent margins and even out the level.

    Only the margins: the audio itself is left exactly as the voice produced
    it. Raw clips carry a variable lead-in and tail, which is distracting when
    playing one word at a time.
    """
    if audio.size == 0:
        return audio

    loud = np.flatnonzero(np.abs(audio) >= SILENCE_FLOOR)
    if loud.size:
        pad = int(PAD_SECONDS * rate)
        audio = audio[max(0, int(loud[0]) - pad):min(audio.size, int(loud[-1]) + pad)]

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1e-6:
        audio = audio * (TARGET_PEAK / peak)
    return audio


def write_wav(path: Path, audio: np.ndarray, rate: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = polish(np.asarray(audio, dtype=np.float32), rate)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    # Write to a temporary name and rename, so a reader never sees a partial
    # file if synthesis is interrupted.
    tmp = path.with_suffix(".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)
    return len(pcm)


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: tts_worker.py MODEL"}),
              flush=True)
        return 2

    voice = PiperVoice.load(sys.argv[1])
    rate = voice.config.sample_rate
    print(json.dumps({"ok": True, "ready": True, "sample_rate": rate}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            chunks = list(voice.synthesize(req["text"]))
            if not chunks:
                raise ValueError("voice produced no audio")
            audio = np.concatenate([c.audio_float_array for c in chunks])
            n = write_wav(Path(req["out"]), audio, rate)
            print(json.dumps({"ok": True, "samples": n}), flush=True)
        except Exception as exc:  # never let one bad request kill the worker
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
                  flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
