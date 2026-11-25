"""The guard's default transport: HTTP(S) that connects only to public addresses.

The address check runs inside `connect_tcp`, on the very address the socket then connects to, for
the first request and every redirect hop alike. There is no separate check that a DNS answer could
change after (rebinding). TLS still verifies the host name: httpcore passes the origin's name as
SNI, while the TCP connection goes to the checked address.

Built on httpcore, which httpx requires and pins; no package is added.
"""

from __future__ import annotations

import ipaddress
import socket
import typing
from collections.abc import Callable, Iterable, Sequence

import httpcore
import httpx

from airgap.core.fetcher import AddressRefused
from airgap.core.policy import is_public_address

# Every address a host resolves to, for (host, port). Injectable so tests never touch DNS.
Resolver = Callable[[str, int], Sequence[str]]


def _system_resolver(host: str, port: int) -> Sequence[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


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
        try:
            answers = [ipaddress.ip_address(a.split("%", 1)[0]) for a in self._resolve(name, port)]
        except (OSError, ValueError) as exc:
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


class PublicTransport(httpx.HTTPTransport):
    """`httpx.HTTPTransport` whose connection pool uses `_PublicOnlyBackend`.

    No proxy, no environment settings, no retries, certificate verification on.
    """

    def __init__(self, private_hosts: Iterable[str] = (), resolve: Resolver = _system_resolver):
        super().__init__(trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=_PublicOnlyBackend(frozenset(private_hosts), resolve),
        )
