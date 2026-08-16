from __future__ import annotations

import getpass
import os
import socket
from pathlib import Path

from .automation import run_automatic
from .json_utils import read_json
from .models import AutomationReport, ReviewQueue, ReviewQueueItem
from .review_server import serve_review_application
from .static_parser import FuvestStaticExtractor


def select_review_item(queue: ReviewQueue) -> ReviewQueueItem | None:
    if not queue.items:
        return None
    return next((item for item in queue.items if item.status == "ready"), queue.items[0])


def available_review_port(preferred_port: int = 8765) -> int:
    if not 1 <= preferred_port <= 65535:
        raise ValueError("a porta deve estar entre 1 e 65535")
    for candidate in (preferred_port, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return int(probe.getsockname()[1])
    raise OSError("nao foi possivel localizar uma porta local para a revisao")


def run_guided_test(
    *,
    config_path: Path = Path("config/sources.test.toml"),
    state_path: Path = Path("data/state/teste-guiado.json"),
    output_path: Path = Path("data/results/teste-guiado.json"),
    model: str | None = None,
    preferred_port: int = 8765,
) -> tuple[AutomationReport, ReviewQueueItem | None]:
    temporary_key = False
    use_openai = model is not None
    if use_openai and not os.environ.get("OPENAI_API_KEY", "").strip():
        key = getpass.getpass(
            "Chave da API OpenAI (entrada oculta; nao sera salva): "
        ).strip()
        if not key:
            raise ValueError("a chave da API OpenAI e necessaria para estruturar as questoes")
        os.environ["OPENAI_API_KEY"] = key
        temporary_key = True

    try:
        extractor = None if use_openai else FuvestStaticExtractor()
        selected_model = model or FuvestStaticExtractor.model
        print("\nIniciando teste reduzido: FUVEST 2026 V1 + gabarito.")
        if use_openai:
            print(f"Modelo de extracao por API: {selected_model}.")
        else:
            print(f"Extracao local sem API: {selected_model}.")
        print("Nenhuma conexao com KAD ou Supabase sera aberta.\n")
        report, written_path = run_automatic(
            config_path=config_path,
            state_path=state_path,
            output_path=output_path,
            model=model,
            max_chars=500_000,
            overlap_chars=0,
            extractor=extractor,
        )
        print(f"Resultado do teste: {written_path}")
        if not report.review_queue_path:
            raise ValueError("a execucao terminou sem gerar uma fila de revisao")

        queue_path = Path(report.review_queue_path)
        queue = ReviewQueue.model_validate(read_json(queue_path))
        item = select_review_item(queue)
        if item is None:
            processing_failure = next(
                (
                    warning
                    for warning in report.result.warnings
                    if "processamento automatico falhou" in warning
                ),
                None,
            )
            if processing_failure:
                raise RuntimeError(processing_failure)
            print("Nenhum lote esta pendente. Novidades futuras aparecerao na proxima rodada.")
            return report, None

        port = available_review_port(preferred_port)
        print(
            f"Abrindo revisao de {item.question_count} questoes "
            f"({item.status}) em http://127.0.0.1:{port}"
        )
        print("Ao terminar, volte a esta janela e pressione Ctrl+C.\n")
        serve_review_application(
            Path(item.batch_path),
            session_path=Path(item.session_path),
            output_path=None,
            port=port,
            open_browser=True,
        )
        return report, item
    finally:
        if temporary_key:
            os.environ.pop("OPENAI_API_KEY", None)
