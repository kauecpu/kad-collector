import multiprocessing


def _bootstrap() -> int:
    """Start the desktop app after PyInstaller's multiprocessing bootstrap.

    PyInstaller starts multiprocessing workers by re-executing the frozen
    executable with private arguments. ``freeze_support`` must run before any
    application startup work, otherwise the worker can fall through into the
    desktop launcher instead of running the browser probe.
    """

    multiprocessing.freeze_support()
    from kad_collector.browser_runtime import configure_playwright_browsers_path
    from kad_collector.desktop_app import main

    # O .exe empacotado roda a partir de uma pasta temporaria. Configure o
    # cache real do Chromium antes de qualquer chamada ao Patchright.
    configure_playwright_browsers_path()
    return main()

if __name__ == "__main__":
    raise SystemExit(_bootstrap())
