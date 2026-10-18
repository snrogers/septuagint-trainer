#!/usr/bin/env bash
# Install the optional pronunciation feature: a neural TTS voice.
#
# This is the only part of the project with dependencies, which is why it is
# kept in its own virtualenv and its own script. Without it the reading
# trainer works exactly as before, minus the play buttons.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv-tts"
VOICE_DIR="data/voices"
VOICE="el_GR-joy-medium"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/el/el_GR/joy/medium"

echo "Installing pronunciation support."
echo

# ── python environment ────────────────────────────────────────────────────
if [ ! -x "$VENV/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "Creating $VENV with uv..."
    uv venv "$VENV" --python 3.12
    uv pip install --python "$VENV/bin/python" piper-tts
  else
    echo "Creating $VENV with venv/pip..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install piper-tts
  fi
else
  echo "  $VENV already present"
fi

# ── voice model ───────────────────────────────────────────────────────────
mkdir -p "$VOICE_DIR"
for ext in onnx onnx.json; do
  target="$VOICE_DIR/$VOICE.$ext"
  if [ -s "$target" ]; then
    echo "  have $(basename "$target")"
  else
    echo "  downloading $(basename "$target")..."
    curl -fsSL -o "$target" "$VOICE_BASE/$VOICE.$ext"
  fi
done

# ── check ─────────────────────────────────────────────────────────────────
echo
python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from lxx import tts
st = tts.status()
if not st["available"]:
    print("Audio still unavailable:", st)
    raise SystemExit(1)
path = tts.synthesize("ἐν ἀρχῇ ἐποίησεν ὁ θεός", "reconstructed")
print(f"Pronunciation ready — test clip {path.stat().st_size:,} bytes")
print(f"Voice: {st['voice']}")
tts.shutdown()
PY

echo
echo "Done. Restart the app and pick a pronunciation from the Read toolbar."
