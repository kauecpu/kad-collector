from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from importlib import import_module
from typing import Protocol, cast


class ScraplingResponse(Protocol):
    url: str
    status: int
    headers: Mapping[str, object]
    body: bytes


class ScraplingSession(Protocol):
    def __enter__(self) -> ScraplingSession: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> object: ...

    def fetch(self, url: str, **options: object) -> ScraplingResponse: ...


ScraplingSessionFactory = Callable[..., ScraplingSession]


class ScraplingUnavailableError(RuntimeError):
    """Scrapling or its browser runtime is not available."""


class ScraplingSessionError(RuntimeError):
    """A page could not be loaded by the persistent Scrapling session."""


def _default_session_factory(**options: object) -> ScraplingSession:
    try:
        module = import_module("scrapling.fetchers")
    except (ImportError, ModuleNotFoundError) as exc:
        raise ScraplingUnavailableError(
            'Scrapling indisponivel; instale o extra browser e execute "scrapling install"'
        ) from exc
    factory = cast(ScraplingSessionFactory, module.StealthySession)
    return factory(**options)


_CHALLENGE_BODY_MARKERS = (
    b"cf-turnstile",
    b"captcha",
    b"just a moment",
    b"checking your browser",
    b"cloudflare ray id",
    b"cf-chl-",
)
_PUBLIC_PDF_LINK = re.compile(
    rb"(?:href|data-url)\s*=\s*['\"](?!javascript:|data:)[^'\"]+"
    rb"\.pdf(?:[?#][^'\"]*)?['\"]",
    re.IGNORECASE,
)


def _looks_blocked(response: ScraplingResponse) -> bool:
    """Heuristic for a Cloudflare/captcha challenge still present in the response."""
    try:
        status = int(response.status)
    except (TypeError, ValueError):
        status = 0
    if status == 403:
        return True
    body = bytes(response.body)[:1_000_000].lower()
    return not _PUBLIC_PDF_LINK.search(body) and any(
        marker in body for marker in _CHALLENGE_BODY_MARKERS
    )


class PersistentScraplingSession:
    """Own one browser session for an entire collection source.

    The session starts in headless mode. If the headless browser fails to
    start, or the first fetch still comes back blocked (HTTP 403 or a
    Cloudflare/captcha challenge in the body), the session is restarted in
    headful mode (``headless=False``) and the fetch is retried once. Every
    other launch parameter (``real_chrome``, ``solve_cloudflare``, the
    timeout, ``block_webrtc`` and ``hide_canvas``) stays identical between
    both attempts -- only ``headless`` changes.

    This entire bypass strategy (``solve_cloudflare``, the headless->headful
    retry, and the 90s timeout floor) is controlled by a single
    ``solve_cloudflare`` flag -- this mirrors the "Usar bypass Cloudflare"
    toggle on the desktop app's Coletar screen. When it is ``False`` the
    session does a single headless fetch with no retry, using the timeout
    as configured (no 90s floor) for a faster, non-bypassing collection.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float,
        session_factory: ScraplingSessionFactory | None = None,
        solve_cloudflare: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self._solve_cloudflare = solve_cloudflare
        self.timeout_ms = (
            max(90_000, int(timeout_seconds * 1000))
            if solve_cloudflare
            else int(timeout_seconds * 1000)
        )
        self._session_factory = session_factory or _default_session_factory
        self._manager: ScraplingSession | None = None
        self._session: ScraplingSession | None = None
        self._headless = True

    @property
    def started(self) -> bool:
        return self._session is not None

    @property
    def headless(self) -> bool:
        return self._headless

    def _launch(self, *, headless: bool) -> tuple[ScraplingSession, ScraplingSession]:
        manager: ScraplingSession | None = None
        try:
            manager = self._session_factory(
                headless=headless,
                real_chrome=True,
                solve_cloudflare=self._solve_cloudflare,
                timeout=self.timeout_ms,
                block_webrtc=True,
                hide_canvas=True,
                useragent=self.user_agent,
                # O cliente externo já controla as tentativas; limitar o
                # retry interno evita multiplicar uma navegação bloqueada.
                retries=1,
            )
            session = manager.__enter__()
        except ScraplingUnavailableError:
            raise
        except Exception as exc:
            cleanup_detail = ""
            if manager is not None:
                try:
                    manager.__exit__(type(exc), exc, exc.__traceback__)
                except Exception as cleanup_exc:
                    cleanup_detail = f"; falha adicional ao limpar a sessao: {cleanup_exc}"
            modo = "headless" if headless else "headful (headless=False)"
            raise ScraplingSessionError(
                f"nao foi possivel iniciar a sessao persistente do Scrapling em modo {modo}: "
                f"{exc}{cleanup_detail}"
            ) from exc
        return manager, session

    def start(self) -> PersistentScraplingSession:
        if self._session is not None:
            return self
        try:
            self._manager, self._session = self._launch(headless=True)
            self._headless = True
        except ScraplingUnavailableError:
            raise
        except ScraplingSessionError:
            if not self._solve_cloudflare:
                # bypass desativado: sem fallback para headful, propaga o erro.
                raise
            # headless=True falhou ao iniciar: tenta novamente sem headless antes
            # de desistir, mantendo os demais parametros identicos.
            self._manager, self._session = self._launch(headless=False)
            self._headless = False
        return self

    def _restart(self, *, headless: bool) -> None:
        manager = self._manager
        self._manager = None
        self._session = None
        if manager is not None:
            with suppress(Exception):
                manager.__exit__(None, None, None)
        self._manager, self._session = self._launch(headless=headless)
        self._headless = headless

    def fetch(
        self,
        url: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> ScraplingResponse:
        self.start()
        headers = dict(extra_headers or {})

        def _do_fetch() -> ScraplingResponse:
            assert self._session is not None
            return self._session.fetch(
                url,
                google_search=False,
                extra_headers=headers,
                timeout=self.timeout_ms,
            )

        if not self._solve_cloudflare:
            # bypass desativado: uma unica tentativa, sem checagem de bloqueio
            # nem fallback headful (comportamento mais rapido).
            try:
                return _do_fetch()
            except Exception as exc:
                raise ScraplingSessionError(
                    f"falha do Scrapling ao carregar {url}: {exc}"
                ) from exc

        try:
            response = _do_fetch()
        except Exception as exc:
            # Falha de rede/driver não é evidência de desafio. Reabrir uma
            # segunda sessão headful aqui apenas duplica uma chamada que pode
            # estar bloqueada indefinidamente no driver.
            raise ScraplingSessionError(
                f"falha do Scrapling ao carregar {url}: {exc}"
            ) from exc
        else:
            if not _looks_blocked(response):
                return response
            first_failure = f"status {response.status} / desafio ainda presente na resposta"

        if not self._headless:
            # ja estavamos em modo headful (headless=False) e ainda assim falhou ou
            # continua bloqueado; nao ha mais nada para tentar.
            raise ScraplingSessionError(
                f"falha do Scrapling ao carregar {url} mesmo com headless=False: {first_failure}"
            )

        # headless=True falhou ou ainda mostra 403/captcha: tenta novamente sem headless,
        # mantendo os demais parametros (real_chrome, solve_cloudflare, timeout,
        # block_webrtc, hide_canvas) identicos.
        self._restart(headless=False)
        try:
            return _do_fetch()
        except Exception as exc:
            raise ScraplingSessionError(
                f"falha do Scrapling ao carregar {url} mesmo com headless=False: {exc}"
            ) from exc

    def close(self) -> None:
        manager = self._manager
        self._manager = None
        self._session = None
        if manager is None:
            return
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            raise ScraplingSessionError(f"falha ao encerrar a sessao do Scrapling: {exc}") from exc

    def __enter__(self) -> PersistentScraplingSession:
        return self.start()

    def __exit__(self, *_args: object) -> None:
        self.close()
