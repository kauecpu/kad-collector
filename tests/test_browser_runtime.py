from __future__ import annotations

import unittest

from kad_collector.browser_runtime import (
    INSTALL_HINT,
    BrowserRuntimeError,
    check_patchright_chromium,
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


if __name__ == "__main__":
    unittest.main()
