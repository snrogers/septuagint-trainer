"""Audio synthesis: phonology in, cached WAV out.

Voice synthesis is optional. If the venv or the voice model is absent the rest
of the application carries on exactly as before and the UI simply hides its
play buttons -- the reading trainer should never depend on the audio extra
having been installed.

Clips are cached on disk keyed by the monotonic text and the voice, so a word
is synthesised once and replayed from cache after that. The cache is bounded:
past CACHE_MAX_BYTES the least recently played clips are deleted.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

from .monotonic import to_monotonic

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / ".venv-tts" / "bin" / "python"
VOICES_DIR = ROOT / "data" / "voices"
CACHE_DIR = ROOT / "data" / "audio"
WORKER = ROOT / "lxx" / "tts_worker.py"

# joy/medium, not rapunzelina/low: the "low" model rambles for several
# seconds when handed a single short word, which is most of what this app
# asks it for. The medium model returns clean 0.4-0.9s clips and is a
# higher sample rate besides.
DEFAULT_VOICE = "el_GR-joy-medium.onnx"

#: Every clip anyone asks for is kept, so on a public instance an unbounded
#: cache would grow toward a synthesis of the whole corpus -- the verses alone
#: come to roughly 10 GB -- and fill the volume it shares with the corpus.
CACHE_MAX_BYTES = 1 << 30

#: Eviction stops at this share of the cap, so the clips that follow do not
#: each set off another full scan of the cache.
CACHE_EVICT_TO = 0.9


def voice_path() -> Path | None:
    """The voice model to use, or None if no model has been downloaded."""
    preferred = VOICES_DIR / DEFAULT_VOICE
    if preferred.exists():
        return preferred
    models = sorted(VOICES_DIR.glob("*.onnx"))
    return models[0] if models else None


def available() -> bool:
    return VENV_PYTHON.exists() and voice_path() is not None and WORKER.exists()


def status() -> dict:
    """What the front end needs to decide whether to offer audio."""
    model = voice_path()
    return {
        "available": available(),
        "voice": model.name if model else None,
        "venv": VENV_PYTHON.exists(),
    }


class _Worker:
    """A resident synthesis subprocess, started on first use.

    Serialised behind a lock: the protocol is one request/response pair at a
    time, and the HTTP server is threaded.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        model = voice_path()
        if model is None:
            raise RuntimeError("no voice model installed")
        self._proc = subprocess.Popen(
            [str(VENV_PYTHON), str(WORKER), str(model)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=str(ROOT),
        )
        ready = self._proc.stdout.readline()
        if not ready:
            raise RuntimeError("synthesis worker failed to start")
        info = json.loads(ready)
        if not info.get("ok"):
            raise RuntimeError(info.get("error", "worker error"))
        return self._proc

    def synthesize(self, text: str, out: Path) -> None:
        with self._lock:
            proc = self._ensure()
            proc.stdin.write(json.dumps({"text": text, "out": str(out)}) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                # Worker died; drop it so the next call restarts it.
                self._proc = None
                raise RuntimeError("synthesis worker stopped")
            result = json.loads(line)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "synthesis failed"))

    def stop(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.stdin.close()
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
            self._proc = None


_worker = _Worker()


def audio_version() -> str:
    """Short hash identifying how clips are currently produced.

    Audio URLs embed this. A clip is immutable for a given voice and set of
    preprocessing rules, so it is worth caching hard in the browser -- but the
    URL has to change when those change, or an edit stays inaudible behind a
    year-long cache entry.
    """
    src = b"".join(
        (Path(__file__).parent / name).read_bytes()
        for name in ("monotonic.py", "tts.py")
    )
    model = voice_path()
    seed = src + (model.name.encode() if model else b"")
    return hashlib.sha1(seed).hexdigest()[:12]


#: Piper phonemizes Greek through espeak, which reads a bare one-character
#: word as the letter's NAME: handed the article ὁ it says "ómikron", not
#: "o". A trailing middle dot (U+00B7) suppresses that only for η and ο; the
#: other vowels keep spelling out no matter what punctuation surrounds them.
#: Each therefore gets a homophonic multi-character spelling instead --
#: verified against el_GR-joy-medium phoneme output:
#:
#:     α  -> αα   [ˈaː]        ι -> ει   [ˈi]
#:     ε  -> εε   [ˈeː]        υ -> ει   [ˈi]
#:     η  -> η·   [i]          ω -> ο·   [o]
#:     ο  -> ο·   [o]
#:
#: Consonants have no word reading at all, so a lone consonant letter keeps
#: its spoken name, which is the honest rendering of a bare letter.
_VOWEL_SUBSTITUTIONS = {
    "α": "αα",
    "ε": "εε",
    "η": "η\u00b7",
    "ι": "ει",
    "ο": "ο\u00b7",
    "υ": "ει",
    "ω": "ο\u00b7",
}


def _speakable(spoken: str) -> str:
    """Rewrite a one-vowel clip so espeak says the sound, not the letter."""
    return _VOWEL_SUBSTITUTIONS.get(spoken, spoken)


def clip_path(text: str) -> tuple[Path, str]:
    """Where this clip lives on disk, plus the text that will be spoken."""
    spoken = _speakable(to_monotonic(text).strip())
    key = hashlib.sha1(spoken.encode("utf-8")).hexdigest()
    # Shard by the first two hex characters: a full reading of the corpus can
    # produce tens of thousands of clips, too many for one directory.
    return CACHE_DIR / key[:2] / f"{key}.wav", spoken


def synthesize(text: str) -> Path:
    """Return a WAV for this text, synthesising it only on a cache miss."""
    if not available():
        raise RuntimeError(
            "audio not installed -- run ./setup-audio.sh to enable pronunciation"
        )

    path, spoken = clip_path(text)
    if not spoken:
        raise ValueError("nothing to pronounce")
    if path.exists() and path.stat().st_size > 44:
        try:
            path.touch()  # recently played clips are the last to be evicted
        except OSError:
            pass
        return path
    _worker.synthesize(spoken, path)
    _store(path)
    return path


_cache_lock = threading.Lock()
_cache_bytes: int | None = None  # counted from disk on the first store


def _clip_stats() -> list[tuple[float, int, Path]]:
    """(mtime, size, path) of every cached clip, oldest first."""
    stats = []
    for clip in CACHE_DIR.rglob("*.wav"):
        try:
            st = clip.stat()
        except OSError:  # deleted while the tree was being walked
            continue
        stats.append((st.st_mtime, st.st_size, clip))
    return sorted(stats)


def _store(path: Path) -> None:
    """Count a new clip, then evict the least recently played past the cap."""
    global _cache_bytes
    with _cache_lock:
        if _cache_bytes is None:
            _cache_bytes = sum(size for _, size, _ in _clip_stats())
        else:
            _cache_bytes += path.stat().st_size
        if _cache_bytes <= CACHE_MAX_BYTES:
            return
        # Recount while evicting: the running total drifts when two requests
        # synthesise the same clip at once.
        stats = _clip_stats()
        _cache_bytes = sum(size for _, size, _ in stats)
        for _, size, clip in stats:
            if _cache_bytes <= CACHE_MAX_BYTES * CACHE_EVICT_TO:
                break
            clip.unlink(missing_ok=True)
            _cache_bytes -= size


def cache_stats() -> dict:
    if not CACHE_DIR.exists():
        return {"clips": 0, "bytes": 0}
    clips = list(CACHE_DIR.rglob("*.wav"))
    return {"clips": len(clips), "bytes": sum(c.stat().st_size for c in clips)}


def shutdown() -> None:
    _worker.stop()


if __name__ == "__main__":  # small manual check
    text = sys.argv[1] if len(sys.argv) > 1 else "ἐν ἀρχῇ ἐποίησεν ὁ θεός"
    out = synthesize(text)
    print(f"{to_monotonic(text)}\n{out}  ({out.stat().st_size:,} bytes)")
