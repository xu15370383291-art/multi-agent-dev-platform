"""Versioned m14: console budgets apply to logs, not complete controller evidence."""
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import legacy_quality_runner as legacy

ROOT = Path('/workspace')
CONTRACT = 'quality-result-file-v1'
RESULT = 'quality-artifacts/execution-result.json'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


def write_once(path, raw):
    if path.is_symlink() or path.exists() or path.parent.is_symlink():
        raise ValueError('quality_result_path_invalid')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(request):
    limit = request['result_bytes']
    if type(limit) is not int or limit <= 0:
        raise ValueError('quality_result_file_limit')
    Path('/tmp/qa-result-limit.json').write_text(json.dumps(limit))
    original = legacy.command

    def command(args, timeout, log_limit):
        process = original(args, timeout, log_limit)
        if args != ['node', '/opt/quality/browser.cjs'] or not process.get('ok'):
            return process
        try:
            proof = json.loads(process['stdout'].strip().splitlines()[-1])
            path = ROOT / 'quality-artifacts/browser-result.json'
            if (
                proof.get('contract') != 'browser-result-file-v1'
                or proof.get('path') != 'quality-artifacts/browser-result.json'
                or type(proof.get('bytes')) is not int
                or not 0 < proof['bytes'] <= limit
                or path.is_symlink() or path.parent.is_symlink() or not path.is_file()
                or path.stat().st_size != proof['bytes']
            ):
                raise ValueError('invalid descriptor')
            with path.open('rb') as stream:
                raw = stream.read(limit + 1)
            if len(raw) != proof['bytes'] or hashlib.sha256(raw).hexdigest() != proof.get('sha256'):
                raise ValueError('invalid digest')
            output = json.loads(raw)
            asset = hashlib.sha256(canonical(request['arguments']['tests'])).hexdigest()
            if output.get('executed_candidate_sha') != request['arguments']['expected_sha'] or output.get('executed_asset_hash') != asset:
                raise ValueError('invalid version')
            return {**process, 'stdout': raw.decode('utf-8')}
        except (ValueError, OSError, KeyError, TypeError):
            return {**process, 'ok': False, 'error_code': 'quality_result_artifact_incomplete', 'stdout': ''}

    legacy.command = command
    try:
        result = legacy.main(request)
    finally:
        legacy.command = original
    if result.get('ok') and request['operation'] == 'inspect_candidate':
        result.update(assertion_trace_contract=1, execution_environment_contract=1,
                      execution_environment=json.loads(Path('/opt/quality/environment.json').read_text()),
                      runtime_observation_contract=1, result_transport_contract=1)
    if request['operation'] != 'run_tests':
        return result
    if result.get('ok') and any(result.get(key) != 1 for key in ('assertion_trace_contract', 'execution_environment_contract', 'runtime_observation_contract')):
        result = {'ok': False, 'error_code': 'quality_result_contract_required'}
    raw = canonical(result)
    if len(raw) > limit:
        return {'ok': False, 'error_code': 'quality_result_file_limit'}
    (ROOT / 'quality-artifacts').mkdir(exist_ok=True)
    write_once(ROOT / RESULT, raw)
    return {'quality_result_transport': {
        'contract': CONTRACT, 'path': RESULT, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'candidate_sha': request['arguments']['expected_sha'],
        'asset_hash': hashlib.sha256(canonical(request['arguments']['tests'])).hexdigest(),
    }}


if __name__ == '__main__':
    try:
        print(json.dumps(main(json.load(sys.stdin))))
    except Exception:
        print(json.dumps({'ok': False, 'error_code': 'quality_result_artifact_incomplete'}))
