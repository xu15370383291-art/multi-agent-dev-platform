"""Keep connection strings/provider values out of operator command tracebacks."""

import json
import re
import sys


def entry(main):
    try:
        main()
    except KeyboardInterrupt:
        print(
            json.dumps(
                {"error": "operation_interrupted", "message": "操作已中断，保留现场；请查看状态再重试。"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(130) from None
    except Exception as exc:  # noqa: BLE001 -- no raw connection/provider exception in console
        code = (
            str(exc)
            if isinstance(exc, ValueError) and re.fullmatch(r"[a-z0-9_:]{1,140}", str(exc))
            else "operation_failed:" + type(exc).__name__
        )
        print(
            json.dumps(
                {
                    "error": code,
                    "message": "操作未完成，数据未被自动重置。请核对依赖、端口、运行状态及维护说明；不要反复重建数据库。",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
