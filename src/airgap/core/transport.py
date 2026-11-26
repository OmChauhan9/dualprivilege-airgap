"""The guard's default transport: HTTP(S) that connects only to public addresses.

The address check runs inside `connect_tcp`, on the very address the socket then connects to, for
the first request and every redirect hop alike. There is no separate check that a DNS answer could
change after (rebinding). TLS still verifies the host name: httpcore passes the origin's name as
SNI, while the TCP connection goes to the checked address.

Built on the public APIs of httpcore, which httpx requires and pins; no package is added.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import typing
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager

import httpcore
import httpx

from airgap.core.fetcher import AddressRefused
from airgap.core.policy import is_public_address

# Every address a host resolves to, for (host, port). Injectable so tests never touch DNS.
Resolver = Callable[[str, int], Sequence[str]]


def _system_resolver(host: str, port: int) -> Sequence[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _resolve_within(resolve: Resolver, host: str, port: int, timeout: float | None) -> list[str]:
    """`resolve` on a worker thread, given at most `timeout` seconds: getaddrinfo cannot be
    interrupted, so a lookup that hangs is abandoned, not waited for."""
    box: list[Sequence[str] | BaseException] = []

    def work() -> None:
        try:
            box.append(resolve(host, port))
        except BaseException as exc:  # noqa: BLE001 - handed back to the caller's thread
            box.append(exc)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout)
    if not box:
        raise httpcore.ConnectTimeout("the host name lookup took too long")
    if isinstance(box[0], BaseException):
        raise httpcore.ConnectError("the host could not be resolved") from box[0]
    return list(box[0])


class _PublicOnlyBackend(httpcore.NetworkBackend):
    """Resolves the host, refuses it if any answer is not public, then connects to an answer it
    checked. A host in `private_hosts` (exact match, never a subdomain) skips the address check."""

    def __init__(
        self,
        private_hosts: frozenset[str],
        resolve: Resolver = _system_resolver,
        inner: httpcore.NetworkBackend | None = None,
    ) -> None:
        self._private_hosts = private_hosts
        self._resolve = resolve
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        name = host.lower().rstrip(".")
        found = _resolve_within(self._resolve, name, port, timeout)
        try:
            answers = [ipaddress.ip_address(a.split("%", 1)[0]) for a in found]
        except ValueError as exc:
            raise httpcore.ConnectError("the host could not be resolved") from exc
        exempt = name in self._private_hosts
        if not answers or not (exempt or all(is_public_address(a) for a in answers)):
            raise AddressRefused(name)
        error: Exception | None = None
        for addr in answers:
            try:
                return self._inner.connect_tcp(
                    str(addr), port, timeout, local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
        assert error is not None
        raise error

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        raise AddressRefused("a unix socket")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


# httpcore's exceptions as httpx names them, so callers handle one family.
_AS_HTTPX: tuple[tuple[type[Exception], type[httpx.TransportError]], ...] = (
    (httpcore.ConnectTimeout, httpx.ConnectTimeout),
    (httpcore.ReadTimeout, httpx.ReadTimeout),
    (httpcore.WriteTimeout, httpx.WriteTimeout),
    (httpcore.PoolTimeout, httpx.PoolTimeout),
    (httpcore.ConnectError, httpx.ConnectError),
    (httpcore.ReadError, httpx.ReadError),
    (httpcore.WriteError, httpx.WriteError),
    (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
    (httpcore.LocalProtocolError, httpx.LocalProtocolError),
)


@contextmanager
def _httpx_errors() -> Iterator[None]:
    try:
        yield
    except AddressRefused:
        raise
    except Exception as exc:
        for theirs, ours in _AS_HTTPX:
            if isinstance(exc, theirs):
                raise ours(str(exc)) from exc
        raise


class _Body(httpx.SyncByteStream):
    def __init__(self, stream: Iterable[bytes]) -> None:
        self._stream = stream

    def __iter__(self) -> Iterator[bytes]:
        with _httpx_errors():
            yield from self._stream

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if close is not None:
            close()


class PublicTransport(httpx.BaseTransport):
    """An httpx transport over `httpcore.ConnectionPool` with the public-address backend.

    No proxy, no environment settings, no retries, certificate verification on.
    """

    def __init__(self, private_hosts: Iterable[str] = (), resolve: Resolver = _system_resolver):
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=_PublicOnlyBackend(frozenset(private_hosts), resolve),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(request.stream, httpx.SyncByteStream)
        url = request.url
        core = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=url.raw_scheme, host=url.raw_host, port=url.port, target=url.raw_path
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with _httpx_errors():
            resp = self._pool.handle_request(core)
        assert isinstance(resp.stream, Iterable)
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=_Body(resp.stream),
            extensions=resp.extensions,
        )

    def close(self) -> None:
        self._pool.close()
