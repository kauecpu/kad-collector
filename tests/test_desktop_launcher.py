from __future__ import annotations

import unittest
from unittest.mock import patch

import desktop_launcher


class DesktopLauncherTests(unittest.TestCase):
    def test_bootstrap_initializes_multiprocessing_before_app(self) -> None:
        calls: list[str] = []

        with (
            patch.object(
                desktop_launcher.multiprocessing,
                "freeze_support",
                side_effect=lambda: calls.append("freeze_support"),
            ),
            patch(
                "kad_collector.browser_runtime.configure_playwright_browsers_path",
                side_effect=lambda: calls.append("browser_path"),
            ),
            patch(
                "kad_collector.desktop_app.main",
                side_effect=lambda: calls.append("main") or 0,
            ),
        ):
            result = desktop_launcher._bootstrap()

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["freeze_support", "browser_path", "main"])


if __name__ == "__main__":
    unittest.main()
