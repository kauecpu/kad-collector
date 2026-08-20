from pathlib import Path

from reportlab.pdfgen import canvas

from kad_collector.document_contract import DeclaredDocumentType, NormalizedDocument
from kad_collector.semantic_identity import (
    ExamSemanticIdentity,
    SemanticEvidence,
    SemanticField,
)


def normalized_document(
    path: Path,
    *,
    sha256: str = "a" * 64,
    declared_type: DeclaredDocumentType = "exam",
    metadata: dict[str, str | int] | None = None,
    title: str = "prova.pdf",
) -> NormalizedDocument:
    return NormalizedDocument(
        local_path=str(path),
        sha256=sha256,
        size_bytes=100,
        declared_type=declared_type,
        title=title,
        entry_method="direct_import",
        metadata=metadata or {},
    )


def identity(*, board: str | None, concurso: str | None, year: int | None) -> ExamSemanticIdentity:
    def field(name: str, value: str | int | None) -> SemanticField:
        if value is None:
            return SemanticField.unknown(f"{name} ausente no fixture")
        return SemanticField.from_evidence(name, (SemanticEvidence.metadata(name, value),))

    def unknown(name: str) -> SemanticField:
        return SemanticField.unknown(f"{name} ausente no fixture")

    return ExamSemanticIdentity(
        board=field("board", board), concurso=field("concurso", concurso),
        organization=unknown("organization"), year=field("year", year),
        roles=unknown("roles"), stage=unknown("stage"), turns=unknown("turns"),
        variants=unknown("variants"),
    )


def write_text_pdf(path: Path, lines: list[str], *, author: str = "fixture") -> None:
    document = canvas.Canvas(str(path))
    document.setAuthor(author)
    y = 800
    for line in lines:
        document.drawString(54, y, line)
        y -= 22
    document.showPage()
    document.save()
