from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from kad_collector.editorial_export import EDITORIAL_IMPORT_V2_FINGERPRINT


class EditorialContractV2Tests(unittest.TestCase):
    def test_declared_fingerprint_matches_canonical_schema(self) -> None:
        contracts = Path(__file__).parents[1] / "contracts"
        schema = json.loads(
            (contracts / "editorial-question-import-v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        canonical = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        declared = (
            contracts / "editorial-question-import-v2.sha256"
        ).read_text(encoding="utf-8").strip()

        self.assertEqual(actual, EDITORIAL_IMPORT_V2_FINGERPRINT)
        self.assertEqual(declared, EDITORIAL_IMPORT_V2_FINGERPRINT)


if __name__ == "__main__":
    unittest.main()
