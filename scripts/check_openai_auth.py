"""Read-only check using the application's effective OpenAI client; never generates text."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from time import monotonic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=15, help="GET timeout in seconds (1-60)")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 60:
        parser.error("--timeout must be between 1 and 60 seconds")
    from app.core.config import settings
    from app.services.content_engine import _get_openai_writer_client, _openai_error_details

    key = settings.OPENAI_API_KEY.strip()
    result = {"check": "GET /v1/models", "generation_requests": 0,
              "key_configured": bool(key), "key_fingerprint": sha256(key.encode()).hexdigest()[:12] if key else None,
              "proxy_configured": bool(settings.OPENAI_HTTPS_PROXY), "writer_model": settings.OPENAI_WRITER_MODEL}
    if not key:
        result.update(status="configuration_required", error_code="missing_openai_api_key")
        print(json.dumps(result, ensure_ascii=False))
        return 2
    start = monotonic()
    try:
        models = _get_openai_writer_client().with_options(timeout=args.timeout, max_retries=0).models.list()
        result.update(status="pass", http_status=200, writer_model_listed=any(item.id == settings.OPENAI_WRITER_MODEL for item in models.data))
        exit_code = 0
    except Exception as exc:
        status, code = _openai_error_details(exc)
        # Do not print exception bodies, API keys, headers, or proxy credentials.
        result.update(status="failed", http_status=status, error_code=code, error_type=type(exc).__name__)
        exit_code = 2
    result["elapsed_seconds"] = round(monotonic() - start, 2)
    print(json.dumps(result, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
