"""Observe trusted successful-assertion progress without accumulating console logs."""
import hashlib
import json
import re
from pathlib import Path

PROGRESS = Path('/tmp/qa-verified-progress.json')


def signature(base):
    try:
        raw = PROGRESS.read_bytes()
        if len(raw) > 256:
            return base
        value = json.loads(raw)
        if set(value) != {'case', 'assertion'} or type(value['case']) is not int or value['case'] < 0:
            return base
        if not isinstance(value['assertion'], str) or not re.fullmatch('[a-f0-9]{64}', value['assertion']):
            return base
        return hashlib.sha256((base + json.dumps(value, sort_keys=True)).encode()).hexdigest()
    except (OSError, ValueError, TypeError):
        return base


def install(runner):
    original = runner.activity_signature
    runner.activity_signature = lambda: signature(original())
