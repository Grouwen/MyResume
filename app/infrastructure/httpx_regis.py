from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class HttpxSpec:
    name: str
    timeout: float = 60.0
    verify: bool = True
    proxy: str | None = None
    max_connections: int = 100
    max_keepalive: int = 20
    http2: bool = False


class HttpxRegistry:
    """
    HTTPX 连接池注册表
    """
    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}

    def create(self, spec: HttpxSpec) -> httpx.AsyncClient:
        old = self._clients.get(spec.name)
        if old is not None and not old.is_closed:
            return old
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(spec.timeout, connect=10.0),
            verify=spec.verify,
            proxy=spec.proxy,
            http2=spec.http2,
            limits=httpx.Limits(
                max_connections=spec.max_connections,
                max_keepalive_connections=spec.max_keepalive,
            ),
        )
        self._clients[spec.name] = client
        return client

    def get(self, name: str = "default") -> httpx.AsyncClient:
        client = self._clients.get(name)
        if client is None or client.is_closed:
            client = self.create(HttpxSpec(name=name))
        return client

    async def aclose(self):
        for client in list(self._clients.values()):
            await client.aclose()
        self._clients.clear()


def init_httpx_regis(*specs: HttpxSpec) -> HttpxRegistry:
    registry = HttpxRegistry()
    for spec in specs:
        registry.create(spec)
    return registry