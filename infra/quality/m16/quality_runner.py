"""m16: setup checkpoints plus unchanged strict runtime-binding witnesses."""
import json
import sys
import capability_quality_runner as legacy


def main(request):
    result=legacy.main(request)
    if result.get('ok') and request['operation']=='inspect_candidate':
        result['qa_capabilities_contract']=2
        result['runtime_setup_contract']=1
    return result


if __name__=='__main__':
    try:
        print(json.dumps(main(json.load(sys.stdin))))
    except Exception:
        print(json.dumps({'ok':False,'error_code':'quality_setup_contract_failed'}))
