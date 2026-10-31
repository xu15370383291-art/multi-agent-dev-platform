"""Read projections only: never persist secret-shaped diagnostics to browser output."""

import re

TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}|\bAKIA[A-Z0-9]{16}\b|(?i:Bearer)\s+[A-Za-z0-9._~+/-]{12,}")
PRIVATE = {
    "api_key",
    "apikey",
    "authorization",
    "credential_ref",
    "reasoning_content",
    "raw_response",
    "provider_response",
}


def public_value(value):
    if isinstance(value, dict):
        return {
            key: "[已隐藏]" if str(key).lower() in PRIVATE else public_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    if isinstance(value, str):
        return TOKEN.sub("[已隐藏]", value)
    return value
