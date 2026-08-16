from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kad_collector.guided_test import run_guided_test, select_review_item
from kad_collector.json_utils import write_json
from kad_collector.models import ReviewQueue, ReviewQueueItem
from kad_collector.static_parser import FuvestStaticExtractor


def queue_item(status: str, name: str) -> ReviewQueueItem:
    return ReviewQueueItem(
        batch_id=f"batch-{name}",
        source_id="fuvest_vestibular_teste",
        source_title=f"Prova {name}",
        batch_path=f"data/reviewed/{name}.json",
        session_path=f"data/reviews/{name}.json",
        status=status,  # type: ignore[arg-type]
        question_count=90,
        matched_answers=90 if status == "ready" else 0,
        missing_answers=0 if status == "ready" else 90,
    )


class GuidedTestTests(unittest.TestCase):
    def test_ready_item_has_priority_over_exception(self) -> None:
        queue = ReviewQueue(
            created_at=datetime.now(UTC),
            extraction_manifest="data/extracted/test.json",
            items=[queue_item("exception", "exception"), queue_item("ready", "ready")],
        )

        selected = select_review_item(queue)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.status, "ready")  # type: ignore[union-attr]

    def test_guided_flow_uses_local_extractor_and_opens_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue_path = root / "queue.json"
            output_path = root / "result.json"
            state_path = root / "state.json"
            config_path = root / "sources.toml"
            queue = ReviewQueue(
                created_at=datetime.now(UTC),
                extraction_manifest=str(root / "extraction.json"),
                items=[
                    queue_item("exception", "exception"),
                    queue_item("ready", "ready"),
                ],
            )
            write_json(queue_path, queue.model_dump(mode="json"))
            report = SimpleNamespace(review_queue_path=str(queue_path))

            def fake_automatic(**_kwargs: object) -> tuple[object, Path]:
                self.assertNotIn("OPENAI_API_KEY", os.environ)
                return report, output_path

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("kad_collector.guided_test.getpass.getpass") as getpass_mock,
                patch(
                    "kad_collector.guided_test.run_automatic",
                    side_effect=fake_automatic,
                ) as automatic,
                patch(
                    "kad_collector.guided_test.available_review_port",
                    return_value=8877,
                ),
                patch("kad_collector.guided_test.serve_review_application") as review,
            ):
                _report, selected = run_guided_test(
                    config_path=config_path,
                    state_path=state_path,
                    output_path=output_path,
                )
                self.assertNotIn("OPENAI_API_KEY", os.environ)

            getpass_mock.assert_not_called()
            call_arguments = automatic.call_args.kwargs
            self.assertEqual(call_arguments["config_path"], config_path)
            self.assertEqual(call_arguments["state_path"], state_path)
            self.assertEqual(call_arguments["output_path"], output_path)
            self.assertIsNone(call_arguments["model"])
            self.assertEqual(call_arguments["max_chars"], 500_000)
            self.assertEqual(call_arguments["overlap_chars"], 0)
            self.assertIsInstance(call_arguments["extractor"], FuvestStaticExtractor)
            review.assert_called_once_with(
                Path("data/reviewed/ready.json"),
                session_path=Path("data/reviews/ready.json"),
                output_path=None,
                port=8877,
                open_browser=True,
            )
            self.assertIsNotNone(selected)
            self.assertEqual(selected.status, "ready")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
