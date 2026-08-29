from __future__ import annotations

from collections.abc import Callable, Mapping
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


class PersistentScraplingSession:
    """Own one browser session for an entire collection source."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float,
        session_factory: ScraplingSessionFactory | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_ms = max(1, int(timeout_seconds * 1000))
        self._session_factory = session_factory or _default_session_factory
        self._manager: ScraplingSession | None = None
        self._session: ScraplingSession | None = None

    @property
    def started(self) -> bool:
        return self._session is not None

    def start(self) -> PersistentScraplingSession:
        if self._session is not None:
            return self
        manager: ScraplingSession | None = None
        try:
            manager = self._session_factory(
                headless=True,
                real_chrome=True,
                solve_cloudflare=False,
                timeout=self.timeout_ms,
                useragent=self.user_agent,
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
            raise ScraplingSessionError(
                "nao foi possivel iniciar a sessao persistente do Scrapling: "
                f"{exc}{cleanup_detail}"
            ) from exc
        self._manager = manager
        self._session = session
        return self

    def fetch(
        self,
        url: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> ScraplingResponse:
        self.start()
        assert self._session is not None
        try:
            return self._session.fetch(
                url,
                google_search=False,
                extra_headers=dict(extra_headers or {}),
                timeout=self.timeout_ms,
            )
        except Exception as exc:
            raise ScraplingSessionError(f"falha do Scrapling ao carregar {url}: {exc}") from exc

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
