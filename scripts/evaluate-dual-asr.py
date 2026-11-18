"""Explicit live replay of the frozen public 12-clip corpus; no business DB writes.

PYTHONPATH=. backend/.venv/bin/python scripts/evaluate-dual-asr.py --settings-dir DIR
The output contains public test transcripts, never credentials or TOS signed URLs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from backend.app.config import SettingsStore
from backend.app.records_ai import transcribe_audio, RecordAIError
from backend.app.schemas import reveal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings-dir', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, default=Path('output/新增真人对话实测-20261118'))
    parser.add_argument('--output', type=Path, default=Path('output/双引擎核对实测-20261118'))
    args = parser.parse_args()
    config = SettingsStore(args.settings_dir).load().model_copy(deep=True)
    assert config.ai.enabled and config.ai.external_permissions.asr
    config.ai.speech.provider = 'dual'
    root = args.output
    (root/'data').mkdir(parents=True, exist_ok=True)
    os.environ['CRJ_VOLC_ASR_CACHE'] = str(root.resolve()/'tasks.sqlite3')
    samples = json.loads((args.corpus/'样本清单.json').read_text())
    (root/'样本清单.json').write_text(json.dumps(samples, ensure_ascii=False, indent=2))
    secrets = [reveal(config.ai.providers.siliconflow.api_key)] + [reveal(getattr(config.ai.speech.volcengine, k)) for k in ('api_key','tos_access_key','tos_secret_key')]
    for sample in samples:
        identifier = sample['id']; destination = root/'data'/f'{identifier}.json'
        if destination.exists():
            print(identifier, 'retained previous replay', flush=True); continue
        raw = (args.corpus/'audio'/f'{identifier}.wav').read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sample['sha256']
        print(identifier, 'START', flush=True); start = time.monotonic()
        try:
            result = transcribe_audio(raw, 'wav', 'audio/wav', config, speaker_count=2, request_key=f'dual-eval-20261118-{identifier}')
        except RecordAIError as error:
            print(identifier, error.status, error.message, flush=True); raise SystemExit(1)
        result = {**sample, **result, 'elapsed_seconds': round(time.monotonic()-start, 2)}
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        assert all(not secret or secret not in serialized for secret in secrets)
        assert 'X-Tos-Signature' not in serialized
        destination.write_text(serialized)
        print(identifier, result['elapsed_seconds'], 'seconds', len(result['segments']), 'review windows', list(result['raw_result']['errors']), flush=True)


if __name__ == '__main__':
    main()
