"""Network retry helpers for LLM calls."""
from __future__ import annotations

import httpx
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

network_retry = retry(
    retry=retry_if_exception_type(
        (
            httpx.RemoteProtocolError,
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.TransportError,
            ConnectionError,
        )
    ),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
