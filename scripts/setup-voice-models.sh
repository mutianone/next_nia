#!/usr/bin/env bash
# Optional CPU models for single-microphone speaker review.
set -euo pipefail
cd "$(dirname "$0")/.."
python=backend/.venv/bin/python
models="${CRJ_VOICE_MODEL_DIR:-backend/data/voice-models}"
mkdir -p "$models"
work=$(mktemp -d "$models/.setup.XXXXXXXX")
trap 'rm -rf "$work"' EXIT
"$python" -m pip install --only-binary=:all: -r backend/requirements-voice.txt
verify() {
  "$python" - "$1" "$2" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys
with Path(sys.argv[1]).open('rb') as file:
    digest = sha256()
    for chunk in iter(lambda: file.read(1024*1024), b''):
        digest.update(chunk)
if digest.hexdigest() != sys.argv[2]:
    sys.exit('模型校验失败')
PY
}
base=https://github.com/k2-fsa/sherpa-onnx/releases/download
sense=sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09
embedding=3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx
if ! { test -f "$models/model.int8.onnx" && verify "$models/model.int8.onnx" 12ca1a2ae7ecf3e0019ef2822307ee0b5cadc9196569e379b4c4026f8205276d && test -f "$models/tokens.txt" && verify "$models/tokens.txt" f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc; }; then
  curl -fLsS --retry 2 "$base/asr-models/$sense.tar.bz2" -o "$work/sense.tar.bz2"
  verify "$work/sense.tar.bz2" 7305f7905bfcf77fa0b39388a313f3da35c68d971661a65475b56fb2162c8e63
  tar -xjf "$work/sense.tar.bz2" -C "$work" "$sense/model.int8.onnx" "$sense/tokens.txt"
  mv "$work/$sense/model.int8.onnx" "$work/$sense/tokens.txt" "$models/"
fi
if ! { test -f "$models/embedding.onnx" && verify "$models/embedding.onnx" 1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b; }; then
  curl -fLsS --retry 2 "$base/speaker-recongition-models/$embedding" -o "$work/embedding.onnx"
  verify "$work/embedding.onnx" 1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b
  mv "$work/embedding.onnx" "$models/embedding.onnx"
fi
echo '单麦克风辅助分人模型已就绪。'
