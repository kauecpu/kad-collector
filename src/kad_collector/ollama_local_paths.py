from __future__ import annotations

from pathlib import Path

from .canonical_classification import CanonicalClassificationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ARTIFACT_ROOT = REPOSITORY_ROOT / "data" / "benchmarks" / "local"


def validate_local_artifact_path(
    path: Path,
    *,
    label: str,
    artifact_root: Path = LOCAL_ARTIFACT_ROOT,
) -> Path:
    """Keep raw local-AI artifacts out of tracked repository paths."""

    resolved = path.expanduser().resolve()
    allowed_root = artifact_root.expanduser().resolve()
    if not resolved.is_relative_to(allowed_root):
        raise CanonicalClassificationError(
            f"{label} contém dados locais e deve ficar sob {allowed_root}"
        )
    return resolved
