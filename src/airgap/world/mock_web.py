"""In-process web: a host -> path map served through httpx.MockTransport.

Real HTTP semantics (redirects, headers, status) with no sockets, so every test is hermetic.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from airgap.world.sites import Response


class MockWeb:
    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], Response] = {}
        # Every URL actually requested, redirect hops included: ground truth for P5.
        self.requests: list[str] = []

    def add(self, url: str, response: Response | str) -> MockWeb:
        parts = urlsplit(url)
        resp = Response(body=response) if isinstance(response, str) else response
        self._routes[(parts.hostname or "", parts.path or "/")] = resp
        return self

    def snapshot(self) -> tuple[tuple[str, str, Response], ...]:
        """Every route and what it serves, for comparing two worlds."""
        return tuple((host, path, resp) for (host, path), resp in sorted(self._routes.items()))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        route = self._routes.get((request.url.host, request.url.path))
        if route is None:
            return httpx.Response(404, text="not found")
        headers = {"content-type": route.content_type}
        if route.location:
            headers["location"] = route.location
        return httpx.Response(route.status, text=route.body, headers=headers)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle), follow_redirects=True)
