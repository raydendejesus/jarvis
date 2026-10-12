"""
One shared HTTP client for the hot request paths (the brain, the voice engine).

Building a new client per request meant a new connection, and a freshly built
TLS context, on every single turn; reusing one keeps the connection open so a
reply doesn't pay that cost each time.
"""
import httpx

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=120,
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=300),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
