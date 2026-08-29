from kad_collector.browser_runtime import configure_playwright_browsers_path

# Precisa rodar antes de qualquer importacao/chamada ao Patchright: o .exe
# empacotado (PyInstaller) roda a partir de uma pasta temporaria, entao sem
# isso o Patchright procuraria o Chromium ali em vez do cache real do
# usuario em %LOCALAPPDATA%\ms-playwright (onde "python -m patchright
# install chromium" de fato instala).
configure_playwright_browsers_path()

from kad_collector.desktop_app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
