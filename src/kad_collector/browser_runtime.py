"""Startup checks for the Patchright/Chromium browser runtime.

The desktop executable (built via ``KADCollector.spec`` with PyInstaller)
bundles the Patchright *library*, but not the Chromium binary that Patchright
downloads separately into a local cache. When that cache is missing or
corrupted, launching the browser fails deep inside a collection run with
low-level, hard-to-read errors (for example ``spawn UNKNOWN`` on Windows,
raised by the Node.js driver process Patchright uses to control Chromium).

``check_patchright_chromium`` turns any such failure into a single, clear,
actionable error instead of letting the .exe crash silently or with a
traceback the end user cannot act on.
"""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable, MutableMapping
from multiprocessing.connection import Connection

INSTALL_HINT = "python -m patchright install chromium"
BROWSER_STARTUP_TIMEOUT_SECONDS = 20.0


class BrowserRuntimeError(RuntimeError):
    """Patchright's Chromium runtime is missing or could not be started."""


def configure_playwright_browsers_path(
    *, environ: MutableMapping[str, str] | None = None
) -> str | None:
    """Point Playwright/Patchright at the browsers the user already installed.

    The packaged .exe (PyInstaller) runs from a temporary extraction folder.
    Without this, Patchright/Playwright would default to looking for Chromium
    relative to that temporary folder instead of the real per-user cache under
    ``%LOCALAPPDATA%\\ms-playwright``, which is where
    ``python -m patchright install chromium`` actually installs it. Call this
    once, as early as possible during the desktop app's startup -- before
    anything imports or calls Patchright/Playwright.

    Returns the browsers path that was set, or ``None`` when ``LOCALAPPDATA``
    is not available (e.g. running outside Windows) and nothing was changed.
    """

    env = environ if environ is not None else os.environ
    local_app_data = env.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    browsers_path = os.path.join(local_app_data, "ms-playwright")
    env["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path
    return browsers_path


def _default_launch_probe() -> None:
    from patchright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        browser.close()


def _launch_probe_worker(channel: Connection) -> None:
    """Run the browser probe in a child process that can be terminated."""

    try:
        _default_launch_probe()
    except BaseException as exc:  # pragma: no cover - executed in child process
        channel.send(("error", type(exc).__name__, str(exc)))
    else:
        channel.send(("ok", "", ""))
    finally:
        channel.close()


def _run_default_probe_with_timeout(timeout_seconds: float) -> None:
    """Run Patchright preflight with a hard process-level timeout."""

    multiprocessing.freeze_support()
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_launch_probe_worker, args=(sender,))
    try:
        process.start()
    except Exception:
        receiver.close()
        sender.close()
        raise
    sender.close()
    try:
        if not receiver.poll(timeout_seconds):
            process.terminate()
            process.join(timeout=2)
            raise BrowserRuntimeError(
                "tempo esgotado ao iniciar o Chromium do Patchright "
                f"({timeout_seconds:.0f}s); o processo foi encerrado. "
                f'Rode "{INSTALL_HINT}" e tente novamente.'
            )
        result, error_type, detail = receiver.recv()
    finally:
        receiver.close()
        if process.is_alive():
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        else:
            process.join(timeout=2)
    if result == "error":
        raise BrowserRuntimeError(
            "Nao foi possivel iniciar o Chromium do Patchright "
            f"({error_type}: {detail}). Rode \"{INSTALL_HINT}\" e tente novamente."
        )


def check_patchright_chromium(*, launch_probe: Callable[[], None] | None = None) -> None:
    """Raise :class:`BrowserRuntimeError` if Chromium cannot be launched.

    Call this once, early, before starting any collection run that needs a
    browser (``page_transport="scrapling"`` sources) so a missing or broken
    install is reported clearly instead of failing partway through a run.

    ``launch_probe`` is injectable so tests can exercise this function
    without spawning a real browser (or can force specific failures, such
    as a Windows ``spawn UNKNOWN`` error) while still verifying that any
    such failure is captured and re-raised legibly.
    """

    probe = launch_probe
    try:
        if probe is None:
            _run_default_probe_with_timeout(BROWSER_STARTUP_TIMEOUT_SECONDS)
        else:
            probe()
    except BrowserRuntimeError:
        raise
    except ImportError as exc:
        raise BrowserRuntimeError(
            "Patchright nao esta instalado. Instale o extra 'browser' do "
            'KAD Collector ("pip install kad-collector[browser]") e depois rode '
            f'"{INSTALL_HINT}".'
        ) from exc
    except Exception as exc:  # noqa: BLE001 - qualquer falha de spawn vira mensagem clara
        raise BrowserRuntimeError(
            "Nao foi possivel iniciar o Chromium do Patchright "
            f'("{exc}"). Rode "{INSTALL_HINT}" e tente novamente.'
        ) from exc
