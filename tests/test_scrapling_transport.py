from __future__ import annotations

import unittest
from types import SimpleNamespace

from kad_collector.scrapling_transport import (
    PersistentScraplingSession,
    ScraplingSessionError,
)


class _FakeManager:
    """Minimal stand-in for a StealthySession instance/context manager."""

    def __init__(self, responses: list[object], *, fail_start: bool = False) -> None:
        self.responses = responses
        self.fail_start = fail_start
        self.entered = False
        self.exited = False

    def __enter__(self) -> _FakeManager:
        if self.fail_start:
            raise OSError("spawn UNKNOWN")
        self.entered = True
        return self

    def __exit__(self, *_args: object) -> None:
        self.exited = True

    def fetch(self, url: str, **_options: object) -> object:
        if not self.responses:
            raise AssertionError(f"nenhuma resposta enfileirada para {url}")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _factory(managers: list[_FakeManager]) -> tuple[object, list[dict[str, object]]]:
    calls: list[dict[str, object]] = []
    pending = list(managers)

    def factory(**options: object) -> _FakeManager:
        calls.append(options)
        return pending.pop(0)

    return factory, calls


_SHARED_PARAM_KEYS = ("real_chrome", "solve_cloudflare", "timeout", "block_webrtc", "hide_canvas")


class PersistentScraplingSessionTests(unittest.TestCase):
    def test_headless_success_needs_no_retry(self) -> None:
        response = SimpleNamespace(url="https://x.test", status=200, headers={}, body=b"ok")
        manager = _FakeManager([response])
        factory, calls = _factory([manager])

        session = PersistentScraplingSession(
            user_agent="KADCollector/Test", timeout_seconds=30, session_factory=factory
        )
        result = session.fetch("https://x.test")
        session.close()

        self.assertIs(result, response)
        self.assertTrue(session.headless)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["headless"], True)
        self.assertIs(calls[0]["real_chrome"], True)
        self.assertIs(calls[0]["solve_cloudflare"], True)
        self.assertIs(calls[0]["block_webrtc"], True)
        self.assertIs(calls[0]["hide_canvas"], True)
        self.assertEqual(calls[0]["timeout"], 90_000)

    def test_403_from_headless_retries_headful_and_returns_result(self) -> None:
        blocked = SimpleNamespace(url="https://x.test", status=403, headers={}, body=b"denied")
        ok = SimpleNamespace(url="https://x.test", status=200, headers={}, body=b"ok")
        headless_manager = _FakeManager([blocked])
        headful_manager = _FakeManager([ok])
        factory, calls = _factory([headless_manager, headful_manager])

        session = PersistentScraplingSession(
            user_agent="KADCollector/Test", timeout_seconds=30, session_factory=factory
        )
        result = session.fetch("https://x.test")
        session.close()

        self.assertIs(result, ok)
        self.assertFalse(session.headless)
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0]["headless"], True)
        self.assertIs(calls[1]["headless"], False)
        for key in _SHARED_PARAM_KEYS:
            self.assertEqual(calls[0][key], calls[1][key])
        self.assertTrue(headless_manager.exited)

    def test_captcha_marker_in_body_triggers_headful_retry(self) -> None:
        blocked = SimpleNamespace(
            url="https://x.test",
            status=200,
            headers={},
            body=b"<html><title>Just a moment...</title>"
            b'<div class="cf-turnstile"></div></html>',
        )
        ok = SimpleNamespace(url="https://x.test", status=200, headers={}, body=b"ok")
        factory, calls = _factory([_FakeManager([blocked]), _FakeManager([ok])])

        session = PersistentScraplingSession(
            user_agent="KADCollector/Test", timeout_seconds=30, session_factory=factory
        )
        result = session.fetch("https://x.test")
        session.close()

        self.assertIs(result, ok)
        self.assertEqual(len(calls), 2)

    def test_headless_launch_failure_falls_back_to_headful(self) -> None:
        ok = SimpleNamespace(url="https://x.test", status=200, headers={}, body=b"ok")
        failing_manager = _FakeManager([], fail_start=True)
        working_manager = _FakeManager([ok])
        factory, calls = _factory([failing_manager, working_manager])

        session = PersistentScraplingSession(
            user_agent="KADCollector/Test", timeout_seconds=30, session_factory=factory
        )
        result = session.fetch("https://x.test")
        session.close()

        self.assertIs(result, ok)
        self.assertFalse(session.headless)
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0]["headless"], True)
        self.assertIs(calls[1]["headless"], False)

    def test_both_attempts_still_blocked_returns_second_response_without_raising(self) -> None:
        blocked_headless = SimpleNamespace(
            url="https://x.test", status=403, headers={}, body=b"denied"
        )
        blocked_headful = SimpleNamespace(
            url="https://x.test", status=403, headers={}, body=b"denied"
        )
        factory, calls = _factory(
            [_FakeManager([blocked_headless]), _FakeManager([blocked_headful])]
        )

        session = PersistentScraplingSession(
            user_agent="KADCollector/Test", timeout_seconds=30, session_factory=factory
        )
        result = session.fetch("https://x.test")
        session.close()

        # nenhuma das duas tentativas levantou excecao; cabe a camada de cima
        # (CollectionHttpClient) decidir o que fazer com um 403 persistente.
        self.assertEqual(result.status, 403)
        self.assertFalse(session.headless)
        self.assertEqual(len(calls), 2)

    def test_both_attempts_raising_reports_clear_error(self) -> None:
        factory, calls = _factory(
            [
                _FakeManager([OSError("spawn UNKNOWN")]),
                _FakeManager([OSError("spawn UNKNOWN")]),
            ]
        )

        session = PersistentScraplingSession(
            user_agent="KADCollector/Test", timeout_seconds=30, session_factory=factory
        )
        with self.assertRaisesRegex(ScraplingSessionError, "headless=False"):
            session.fetch("https://x.test")
        session.close()
        self.assertEqual(len(calls), 2)

    def test_timeout_floor_is_ninety_seconds(self) -> None:
        session = PersistentScraplingSession(
            user_agent="KADCollector/Test", timeout_seconds=10, session_factory=lambda **_o: None
        )
        self.assertEqual(session.timeout_ms, 90_000)

        session_longer = PersistentScraplingSession(
            user_agent="KADCollector/Test", timeout_seconds=120, session_factory=lambda **_o: None
        )
        self.assertEqual(session_longer.timeout_ms, 120_000)

    def test_bypass_disabled_uses_thirty_second_timeout_with_no_floor(self) -> None:
        session = PersistentScraplingSession(
            user_agent="KADCollector/Test",
            timeout_seconds=30,
            session_factory=lambda **_o: None,
            solve_cloudflare=False,
        )
        self.assertEqual(session.timeout_ms, 30_000)

    def test_bypass_disabled_sends_solve_cloudflare_false_and_stays_headless(self) -> None:
        response = SimpleNamespace(url="https://x.test", status=200, headers={}, body=b"ok")
        manager = _FakeManager([response])
        factory, calls = _factory([manager])

        session = PersistentScraplingSession(
            user_agent="KADCollector/Test",
            timeout_seconds=30,
            session_factory=factory,
            solve_cloudflare=False,
        )
        result = session.fetch("https://x.test")
        session.close()

        self.assertIs(result, response)
        self.assertTrue(session.headless)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["solve_cloudflare"], False)
        self.assertEqual(calls[0]["timeout"], 30_000)

    def test_bypass_disabled_does_not_retry_headful_when_blocked(self) -> None:
        blocked = SimpleNamespace(url="https://x.test", status=403, headers={}, body=b"denied")
        manager = _FakeManager([blocked])
        factory, calls = _factory([manager])

        session = PersistentScraplingSession(
            user_agent="KADCollector/Test",
            timeout_seconds=30,
            session_factory=factory,
            solve_cloudflare=False,
        )
        result = session.fetch("https://x.test")
        session.close()

        # sem bypass, a resposta bloqueada eh devolvida como esta (sem checagem
        # de _looks_blocked nem segunda tentativa headful) -- mais rapido, e a
        # camada de cima (CollectionHttpClient) decide o que fazer com o 403.
        self.assertIs(result, blocked)
        self.assertTrue(session.headless)
        self.assertEqual(len(calls), 1)

    def test_bypass_disabled_launch_failure_does_not_fall_back_to_headful(self) -> None:
        factory, calls = _factory([_FakeManager([], fail_start=True)])

        session = PersistentScraplingSession(
            user_agent="KADCollector/Test",
            timeout_seconds=30,
            session_factory=factory,
            solve_cloudflare=False,
        )
        with self.assertRaisesRegex(ScraplingSessionError, "headless"):
            session.fetch("https://x.test")
        session.close()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
