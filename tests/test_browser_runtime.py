from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kad_collector.browser_runtime import (
    BROWSER_STARTUP_TIMEOUT_SECONDS,
    INSTALL_HINT,
    BrowserRuntimeError,
    check_patchright_chromium,
    configure_playwright_browsers_path,
)


class BrowserRuntimeTests(unittest.TestCase):
    def test_check_passes_silently_when_chromium_launches(self) -> None:
        calls: list[str] = []

        def probe() -> None:
            calls.append("launched")

        check_patchright_chromium(launch_probe=probe)

        self.assertEqual(calls, ["launched"])

    def test_spawn_unknown_is_captured_and_reported_legibly(self) -> None:
        def probe() -> None:
            # Reproduz o erro de baixo nivel que o Node.js (usado pelo driver
            # do Patchright) lanca no Windows quando o binario do Chromium
            # nao foi instalado ou esta corrompido.
            raise OSError("spawn UNKNOWN")

        with self.assertRaises(BrowserRuntimeError) as ctx:
            check_patchright_chromium(launch_probe=probe)

        message = str(ctx.exception)
        self.assertIn("spawn UNKNOWN", message)
        self.assertIn(INSTALL_HINT, message)

    def test_missing_patchright_package_reports_install_hint(self) -> None:
        def probe() -> None:
            raise ImportError("No module named 'patchright'")

        with self.assertRaises(BrowserRuntimeError) as ctx:
            check_patchright_chromium(launch_probe=probe)

        self.assertIn(INSTALL_HINT, str(ctx.exception))

    def test_real_patchright_probe_either_starts_or_fails_legibly(self) -> None:
        """Integration-style check: exercises the real Patchright launch path.

        Environments without a downloaded Chromium (or without Patchright
        at all) must not blow up with a raw, low-level exception -- any
        failure has to come back as a single, readable BrowserRuntimeError.
        """
        try:
            check_patchright_chromium()
        except BrowserRuntimeError as exc:
            self.assertIn(INSTALL_HINT, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.fail(
                "uma falha ao iniciar o Patchright deve virar BrowserRuntimeError, "
                f"nao {type(exc).__name__}: {exc}"
            )

    def test_default_probe_uses_process_watchdog(self) -> None:
        with patch("kad_collector.browser_runtime._run_default_probe_with_timeout") as watchdog:
            check_patchright_chromium()

        watchdog.assert_called_once_with(BROWSER_STARTUP_TIMEOUT_SECONDS)


class ConfigurePlaywrightBrowsersPathTests(unittest.TestCase):
    def test_points_playwright_at_the_users_local_app_data_cache(self) -> None:
        local_app_data = os.path.join("C:", "Users", "alguem", "AppData", "Local")
        environ = {"LOCALAPPDATA": local_app_data}

        result = configure_playwright_browsers_path(environ=environ)

        expected = os.path.join(local_app_data, "ms-playwright")
        self.assertEqual(result, expected)
        self.assertEqual(environ["PLAYWRIGHT_BROWSERS_PATH"], expected)

    def test_does_nothing_without_local_app_data(self) -> None:
        environ: dict[str, str] = {}

        result = configure_playwright_browsers_path(environ=environ)

        self.assertIsNone(result)
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", environ)

    def test_overwrites_any_previously_configured_path(self) -> None:
        local_app_data = os.path.join("C:", "Users", "alguem", "AppData", "Local")
        environ = {
            "LOCALAPPDATA": local_app_data,
            "PLAYWRIGHT_BROWSERS_PATH": os.path.join("C:", "tmp", "pyinstaller"),
        }

        configure_playwright_browsers_path(environ=environ)

        self.assertEqual(
            environ["PLAYWRIGHT_BROWSERS_PATH"],
            os.path.join(local_app_data, "ms-playwright"),
        )


if __name__ == "__main__":
    unittest.main()
