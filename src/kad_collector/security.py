from __future__ import annotations

import gzip
import ipaddress
import socket
import time
from dataclasses import dataclass
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class UnsafeUrlError(ValueError):
    """URL fora dos hosts permitidos ou apontando para rede privada."""


class FetchError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_public_url(
    url: str, allowed_hosts: list[str], *, resolve_dns: bool = True
) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError(f"esquema nao permitido: {url}")
    if not host or host not in allowed_hosts:
        raise UnsafeUrlError(f"host nao permitido: {host or url}")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("credenciais embutidas na URL nao sao permitidas")

    addresses: set[str] = set()
    try:
        addresses.add(str(ipaddress.ip_address(host)))
    except ValueError:
        if resolve_dns:
            try:
                for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM):
                    addresses.add(str(item[4][0]))
            except socket.gaierror as exc:
                raise UnsafeUrlError(f"nao foi possivel resolver o host {host}") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeUrlError(f"endereco de rede nao publico bloqueado: {address}")
    return url


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass(frozen=True)
class HttpResult:
    url: str
    status_code: int
    headers: Message
    body: bytes


class SafeHttpClient:
    def __init__(self, user_agent: str, timeout: float, interval_seconds: float) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.interval_seconds = interval_seconds
        self._last_request_at = 0.0
        self._opener = build_opener(_NoRedirect())

    def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
        current_url = url
        for _ in range(6):
            validate_public_url(current_url, allowed_hosts)
            self._throttle()
            request = Request(
                current_url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.1",
                    "Accept-Encoding": "gzip",
                },
            )
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    body = self._read_limited(response, max_bytes)
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        body = gzip.decompress(body)
                        if len(body) > max_bytes:
                            raise FetchError("resposta descompactada excede o limite configurado")
                    return HttpResult(
                        url=response.geturl(),
                        status_code=response.status,
                        headers=response.headers,
                        body=body,
                    )
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    if not location:
                        raise FetchError(
                            "redirecionamento sem cabecalho Location", exc.code
                        ) from exc
                    current_url = urljoin(current_url, location)
                    continue
                raise FetchError(f"HTTP {exc.code} ao acessar {current_url}", exc.code) from exc
            except URLError as exc:
                raise FetchError(f"falha de rede ao acessar {current_url}: {exc.reason}") from exc
        raise FetchError("numero maximo de redirecionamentos excedido")

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _read_limited(response, max_bytes: int) -> bytes:  # type: ignore[no-untyped-def]
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise FetchError("cabecalho Content-Length invalido") from exc
            if declared_length > max_bytes:
                raise FetchError("resposta excede o limite configurado")
        body = bytes(response.read(max_bytes + 1))
        if len(body) > max_bytes:
            raise FetchError("resposta excede o limite configurado")
        return body
