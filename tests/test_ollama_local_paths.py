from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kad_collector.canonical_classification import CanonicalClassificationError
from kad_collector.ollama_local_paths import (
    LOCAL_ARTIFACT_ROOT,
    REPOSITORY_ROOT,
    validate_local_artifact_path,
)


class OllamaLocalPathTests(unittest.TestCase):
    def test_rejects_raw_artifact_in_tracked_repository_path(self) -> None:
        with self.assertRaisesRegex(CanonicalClassificationError, "deve ficar sob"):
            validate_local_artifact_path(
                REPOSITORY_ROOT / "docs" / "raw-ollama.json",
                label="artefato de teste",
            )

    def test_allows_ignored_local_root_and_explicit_test_root(self) -> None:
        self.assertEqual(
            validate_local_artifact_path(
                LOCAL_ARTIFACT_ROOT / "ollama" / "checkpoint.json",
                label="checkpoint",
            ),
            (LOCAL_ARTIFACT_ROOT / "ollama" / "checkpoint.json").resolve(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary) / "checkpoint.json"
            with self.assertRaises(CanonicalClassificationError):
                validate_local_artifact_path(external, label="checkpoint")
            self.assertEqual(
                validate_local_artifact_path(
                    external,
                    label="checkpoint",
                    artifact_root=Path(temporary),
                ),
                external.resolve(),
            )


if __name__ == "__main__":
    unittest.main()
