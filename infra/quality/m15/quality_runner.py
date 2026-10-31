"""m15 adds inspected, versioned QA capabilities without changing transport."""
import json
import sys
from pathlib import Path

import transport_quality_runner as transport


def main(request):
    result = transport.main(request)
    if result.get('ok') and request['operation'] == 'inspect_candidate':
        result['qa_capabilities_contract'] = 1
        result['qa_capabilities'] = json.loads(Path('/opt/quality/capabilities.json').read_text())
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(main(json.load(sys.stdin))))
    except Exception:
        print(json.dumps({'ok': False, 'error_code': 'quality_capability_contract_failed'}))
