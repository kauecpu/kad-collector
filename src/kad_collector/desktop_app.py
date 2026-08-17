from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path
from typing import Any

from .desktop_server import DesktopApplication, _resource_bytes, start_desktop_server


class DesktopBridge:
    def __init__(self) -> None:
        self._window: Any = None

    def bind_window(self, window: Any) -> None:
        self._window = window

    def _require_window(self) -> Any:
        if self._window is None:
            raise RuntimeError("janela do aplicativo ainda não está pronta")
        return self._window

    def choose_pdfs(self) -> list[str]:
        selected = self._require_window().create_file_dialog(
            10,
            allow_multiple=True,
            file_types=("Documentos PDF (*.pdf)",),
        )
        return list(selected or [])

    def choose_folder(self) -> list[str]:
        selected = self._require_window().create_file_dialog(20)
        return list(selected or [])

    def choose_export_folder(self) -> str | None:
        selected = self._require_window().create_file_dialog(20)
        return selected[0] if selected else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aplicativo desktop local do KAD Collector")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--browser", action="store_true", help="abre a interface no navegador")
    parser.add_argument("--smoke-test", action="store_true")
    return parser


def _smoke_test(application: DesktopApplication) -> int:
    application.bootstrap()
    for resource_name in ("desktop_ui.html", "desktop_styles.css", "desktop_app.js"):
        if not _resource_bytes(resource_name):
            raise RuntimeError(f"recurso desktop vazio: {resource_name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    application = DesktopApplication(args.data_dir)
    if args.smoke_test:
        return _smoke_test(application)
    server, server_thread, url = start_desktop_server(application, port=args.port)
    del server_thread
    try:
        if args.browser:
            webbrowser.open(url)
            threading.Event().wait()
            return 0
        try:
            import webview
        except ImportError:
            webbrowser.open(url)
            threading.Event().wait()
            return 0
        bridge = DesktopBridge()
        window = webview.create_window(
            "KAD Collector",
            url,
            js_api=bridge,
            width=1440,
            height=900,
            min_size=(1024, 700),
            background_color="#100c20",
        )
        bridge.bind_window(window)
        webview.start(debug=False)
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
