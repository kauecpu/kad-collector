from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from .editorial_taxonomy import EditorialTaxonomy
from .json_utils import read_json, write_json, write_json_lines
from .models import DownloadManifest, LocalReviewSession, StrictModel
from .operator_review import OperatorReviewBatch, build_review_batches
from .structured_questions import StructuredQuestion, StructuredQuestionPackage

CONSOLIDATED_REVIEW_VERSION = "1.0"


class CorpusPackageSpec(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    label: str = Field(min_length=2)
    package_path: str
    manifest_paths: list[str] = Field(min_length=1)
    origin: str = Field(min_length=2)
    period: str = Field(min_length=2)
    expected_questions: int = Field(ge=1)
    expected_accepted: int = Field(ge=0)
    expected_quarantined: int = Field(ge=0)
    expected_rejected: int = Field(default=0, ge=0)
    expected_structured_years: list[int] = Field(default_factory=list)
    expected_quarantine_reasons: dict[str, int] = Field(default_factory=dict)
    sample_years: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def expected_states_must_match_total(self) -> CorpusPackageSpec:
        total = self.expected_accepted + self.expected_quarantined + self.expected_rejected
        if total != self.expected_questions:
            raise ValueError(f"totais esperados de {self.id} não fecham: {total}")
        return self


class ConsolidatedCorpusSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    corpus_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    base_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    branch: str = Field(min_length=2)
    packages: list[CorpusPackageSpec] = Field(min_length=1)


class PackageInventory(StrictModel):
    id: str
    label: str
    package_path: str
    package_file_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_hash_policy: Literal["current", "legacy-with-manifest"]
    parser_version: str
    manifest_paths: list[str]
    manifest_sha256s: list[str]
    origin: str
    period: str
    documents: int = Field(ge=0)
    exams: int = Field(ge=0)
    answer_keys: int = Field(ge=0)
    documents_used_by_package: int = Field(ge=0)
    unused_documents: int = Field(ge=0)
    structured_exams: int = Field(ge=0)
    manifest_years: list[int]
    structured_years: list[int]
    expected_structured_years: list[int]
    quarantine_reasons: dict[str, int]
    expected_quarantine_reasons: dict[str, int]
    questions: int = Field(ge=0)
    accepted: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    rejected: int = Field(ge=0)
    integrity: Literal["verified"] = "verified"


class InventoryCounts(StrictModel):
    documents: int = Field(ge=0)
    exams: int = Field(ge=0)
    answer_keys: int = Field(ge=0)
    questions_extracted: int = Field(ge=0)
    structurally_accepted: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    rejected: int = Field(ge=0)
    annulled: int = Field(ge=0)
    ready_for_review: int = Field(ge=0)
    classified: int = Field(ge=0)
    editorial_decisions: int = Field(ge=0)
    ready_for_export: int = Field(ge=0)
    exported: int = Field(ge=0)


class ClassificationInventory(StrictModel):
    taxonomy_version: str
    trace_path: str
    deterministic: int = Field(ge=0)
    qwen: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    qwen_calls: int = Field(ge=0)
    sample_size: int = Field(ge=0)
    minimum_precision: float = Field(default=0.95, ge=0, le=1)
    precision_by_field: dict[str, float | None]
    accepted_without_correction: bool


class ConsolidatedReviewIndex(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    corpus_id: str
    base_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    branch: str
    created_at: datetime
    packages: list[PackageInventory]
    batches: list[OperatorReviewBatch]
    total: InventoryCounts
    homologation_sample: InventoryCounts
    open_batch: InventoryCounts
    open_batch_id: str | None
    classification: ClassificationInventory
    grouped: dict[str, list[dict[str, Any]]]
    quarantine_reasons: dict[str, int]
    quarantine_exclusive_combinations: dict[str, int]
    lineage_warnings: list[str]
    duplicate_stable_ids: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _structured_content_sha256(
    package: StructuredQuestionPackage, *, include_manifest_hashes: bool = False
) -> str:
    content = package.model_dump(mode="json")
    content.pop("content_sha256", None)
    if not include_manifest_hashes:
        content.pop("input_manifest_sha256s", None)
    for traces in content["page_extraction"].values():
        for trace in traces:
            trace.pop("duration_ms", None)
    content["qwen"].pop("available", None)
    for decision in content["qwen"]["calls"]:
        decision.pop("duration_ms", None)
    for exam_metric in content["exams"]:
        exam_metric.pop("duration_ms", None)
    content["metrics"].pop("duration_ms", None)
    return _canonical_sha256(content)


def _resolve(spec_path: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = spec_path.parent / candidate
    return candidate.resolve()


def _document_year(document: Any) -> int | None:
    for key in ("year", "ano", "ano_publicacao"):
        value = document.metadata.get(key)
        if value is not None and str(value).isdigit():
            return int(value)
    return None


def _load_package(
    spec_path: Path, package_spec: CorpusPackageSpec
) -> tuple[StructuredQuestionPackage, DownloadManifest, PackageInventory]:
    package_path = _resolve(spec_path, package_spec.package_path)
    package = StructuredQuestionPackage.model_validate(read_json(package_path))
    semantic_sha = _structured_content_sha256(package)
    legacy_semantic_sha = _structured_content_sha256(
        package, include_manifest_hashes=True
    )
    if semantic_sha == package.content_sha256:
        content_hash_policy: Literal["current", "legacy-with-manifest"] = "current"
    elif legacy_semantic_sha == package.content_sha256:
        content_hash_policy = "legacy-with-manifest"
    else:
        raise ValueError(f"hash semântico inválido no pacote {package_spec.id}")

    manifest_paths = [_resolve(spec_path, value) for value in package_spec.manifest_paths]
    manifest_hashes = [_file_sha256(path) for path in manifest_paths]
    if set(manifest_hashes) != set(package.input_manifest_sha256s):
        raise ValueError(
            f"manifestos de {package_spec.id} não correspondem aos hashes do pacote"
        )
    manifests = [DownloadManifest.model_validate(read_json(path)) for path in manifest_paths]
    documents_by_sha: dict[str, Any] = {}
    for manifest in manifests:
        for document in manifest.documents:
            existing = documents_by_sha.get(document.sha256)
            if existing is not None and existing != document:
                raise ValueError(f"documento {document.sha256} diverge entre manifestos")
            documents_by_sha[document.sha256] = document
    for document in documents_by_sha.values():
        local_path = Path(document.local_path)
        if not local_path.is_absolute():
            local_path = spec_path.parent.parent / local_path
        local_path = local_path.resolve()
        if not local_path.is_file():
            raise ValueError(f"documento local ausente: {document.local_path}")
        if _file_sha256(local_path) != document.sha256:
            raise ValueError(f"documento local corrompido: {document.local_path}")

    actual = (
        len(package.accepted),
        len(package.quarantined),
        len(package.rejected),
    )
    expected = (
        package_spec.expected_accepted,
        package_spec.expected_quarantined,
        package_spec.expected_rejected,
    )
    if actual != expected:
        raise ValueError(
            f"totais de {package_spec.id} divergiram: esperado {expected}, encontrado {actual}"
        )
    package_questions = [question for _state, question in _all_questions(package)]
    used_document_sha256s = {
        digest
        for question in package_questions
        for digest in (question.exam_sha256, question.answer_key_sha256)
    }
    unknown_document_sha256s = used_document_sha256s - set(documents_by_sha)
    if unknown_document_sha256s:
        raise ValueError(
            f"{package_spec.id} referencia documentos ausentes: "
            + ", ".join(sorted(unknown_document_sha256s))
        )
    manifest_years = sorted(
        {
            year
            for document in documents_by_sha.values()
            if (year := _document_year(document)) is not None
        }
    )
    structured_years = sorted({question.year for question in package_questions})
    package_quarantine_reasons: Counter[str] = Counter(
        reason for question in package.quarantined for reason in question.validation_reasons
    )

    first = manifests[0]
    merged = first.model_copy(
        update={
            "created_at": max(item.created_at for item in manifests),
            "documents": sorted(documents_by_sha.values(), key=lambda item: item.sha256),
            "references": [value for item in manifests for value in item.references],
            "filtered_out_documents": sum(
                item.filtered_out_documents for item in manifests
            ),
            "duplicate_documents": sum(item.duplicate_documents for item in manifests),
            "failures": [value for item in manifests for value in item.failures],
            "warnings": [value for item in manifests for value in item.warnings],
            "telemetry": [value for item in manifests for value in item.telemetry],
        }
    )
    inventory = PackageInventory(
        id=package_spec.id,
        label=package_spec.label,
        package_path=str(package_path),
        package_file_sha256=_file_sha256(package_path),
        package_content_sha256=package.content_sha256,
        content_hash_policy=content_hash_policy,
        parser_version=package.parser_version,
        manifest_paths=[str(path) for path in manifest_paths],
        manifest_sha256s=manifest_hashes,
        origin=package_spec.origin,
        period=package_spec.period,
        documents=len(merged.documents),
        exams=sum(item.document_type == "exam" for item in merged.documents),
        answer_keys=sum(item.document_type == "answer_key" for item in merged.documents),
        documents_used_by_package=len(used_document_sha256s),
        unused_documents=len(documents_by_sha) - len(used_document_sha256s),
        structured_exams=len({question.exam_sha256 for question in package_questions}),
        manifest_years=manifest_years,
        structured_years=structured_years,
        expected_structured_years=package_spec.expected_structured_years,
        quarantine_reasons=dict(package_quarantine_reasons),
        expected_quarantine_reasons=package_spec.expected_quarantine_reasons,
        questions=sum(actual),
        accepted=actual[0],
        quarantined=actual[1],
        rejected=actual[2],
    )
    return package, merged, inventory


def _all_questions(
    package: StructuredQuestionPackage,
) -> list[tuple[str, StructuredQuestion]]:
    return [
        *(("accepted", item) for item in package.accepted),
        *(("quarantined", item) for item in package.quarantined),
        *(("rejected", item) for item in package.rejected),
    ]


def _session_state(
    entries: list[OperatorReviewBatch],
) -> dict[str, tuple[bool, bool, bool]]:
    result: dict[str, tuple[bool, bool, bool]] = {}
    for entry in entries:
        if entry.session_path is None:
            continue
        session = LocalReviewSession.model_validate(read_json(Path(entry.session_path)))
        decisions = {item.question_number: item for item in session.decisions}
        for question in session.batch.questions:
            stable_id = question.source_stable_id
            if stable_id is None:
                continue
            decision = decisions[question.number]
            classified = all(
                value is not None
                for value in (
                    question.discipline,
                    question.matter,
                    question.subject,
                    question.level,
                )
            )
            exportable = (
                decision.status == "approved"
                and classified
                and not question.editorial_blocks
                and question.answer_status == "matched"
            )
            result[stable_id] = (classified, exportable, decision.status != "pending")
    return result


def _counts(
    rows: list[tuple[str, StructuredQuestion]],
    *,
    documents: int = 0,
    exams: int = 0,
    answer_keys: int = 0,
    session_state: dict[str, tuple[bool, bool, bool]] | None = None,
) -> InventoryCounts:
    session_state = session_state or {}
    decisions = 0
    classified = 0
    exportable = 0
    for _state, question in rows:
        current = session_state.get(question.stable_id)
        if current is None:
            continue
        classified += int(current[0])
        exportable += int(current[1])
        decisions += int(current[2])
    return InventoryCounts(
        documents=documents,
        exams=exams,
        answer_keys=answer_keys,
        questions_extracted=len(rows),
        structurally_accepted=sum(state == "accepted" for state, _item in rows),
        quarantined=sum(state == "quarantined" for state, _item in rows),
        rejected=sum(state == "rejected" for state, _item in rows),
        annulled=sum(
            item.answer_association.original_answer == "X" for _state, item in rows
        ),
        ready_for_review=sum(state != "rejected" for state, _item in rows),
        classified=classified,
        editorial_decisions=decisions,
        ready_for_export=exportable,
        exported=0,
    )


def _group_rows(
    rows: list[tuple[str, StructuredQuestion]], key: str
) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[str, StructuredQuestion]]] = defaultdict(list)
    for state, question in rows:
        value = getattr(question, key)
        grouped[str(value) if value not in {None, ""} else "(não informado)"].append(
            (state, question)
        )
    output: list[dict[str, Any]] = []
    for value, items in sorted(grouped.items()):
        output.append(
            {
                "value": value,
                "questions": len(items),
                "accepted": sum(state == "accepted" for state, _item in items),
                "quarantined": sum(state == "quarantined" for state, _item in items),
                "rejected": sum(state == "rejected" for state, _item in items),
                "annulled": sum(
                    item.answer_association.original_answer == "X"
                    for _state, item in items
                ),
            }
        )
    return output


def _portable_path(value: str | Path) -> str:
    path = Path(value)
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _versionable_payload(index: ConsolidatedReviewIndex) -> dict[str, Any]:
    payload = index.model_dump(mode="json")
    for package in payload["packages"]:
        package["package_path"] = _portable_path(package["package_path"])
        package["manifest_paths"] = [
            _portable_path(value) for value in package["manifest_paths"]
        ]
    for batch in payload["batches"]:
        for field in ("batch_path", "session_path", "exceptions_path"):
            if batch[field] is not None:
                batch[field] = _portable_path(batch[field])
    payload["classification"]["trace_path"] = _portable_path(
        payload["classification"]["trace_path"]
    )
    return payload


def _write_markdown(index: ConsolidatedReviewIndex, path: Path, *, spec_path: Path) -> None:
    lines = [
        "# Inventário consolidado da revisão editorial",
        "",
        f"- Especificação: `{_portable_path(spec_path)}`",
        f"- Identificador: `{index.corpus_id}`",
        f"- Base: `{index.base_commit}` (PR #99 integrado).",
        f"- Branch: `{index.branch}`.",
        f"- Hash do inventário: `{index.content_sha256}`",
        "",
        "## Números principais",
        "",
        "| Indicador | Total |",
        "|---|---:|",
        f"| Acervo total estruturado | {index.total.questions_extracted} |",
        f"| Acervo colocado na revisão | {index.total.ready_for_review} |",
        f"| Acervo apto para exportação | {index.total.ready_for_export} |",
        "",
        (
            "Nenhuma aprovação foi criada automaticamente. `publicationStatus` "
            "permanece `draft` no fluxo de exportação controlada."
        ),
        "",
        "## Pacotes e integridade",
        "",
        (
            "| Pacote | Período | Documentos | Usados | Não usados | Provas "
            "estruturadas | Questões | Aceitas | Quarentena | Integridade |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in index.packages:
        lines.append(
            f"| {item.label} | {item.period} | {item.documents} | "
            f"{item.documents_used_by_package} | {item.unused_documents} | "
            f"{item.structured_exams} | {item.questions} | {item.accepted} | "
            f"{item.quarantined} | {item.integrity} |"
        )
    lines.extend(["", "### Caminhos e hashes", ""])
    for item in index.packages:
        lines.append(f"#### {item.label}")
        lines.append("")
        lines.extend(
            [
                f"- Pacote: `{_portable_path(item.package_path)}`; "
                f"arquivo `{item.package_file_sha256}`; conteúdo "
                f"`{item.package_content_sha256}` ({item.content_hash_policy}).",
                f"- Anos no manifesto: {item.manifest_years}; anos estruturados: "
                f"{item.structured_years}; esperados: {item.expected_structured_years}.",
            ]
        )
        for manifest_path, manifest_sha in zip(
            item.manifest_paths, item.manifest_sha256s, strict=True
        ):
            lines.append(
                f"- Manifesto: `{_portable_path(manifest_path)}`; SHA-256 "
                f"`{manifest_sha}`."
            )
        lines.append("")
    lines.extend(
        [
            "",
            "## Escopos",
            "",
            (
                "| Escopo | Questões | Aceitas | Quarentena | Anuladas | "
                "Classificadas | Decisões | Aptas para exportação |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for label, counts in (
        ("Acervo total", index.total),
        ("Amostra homologada anteriormente", index.homologation_sample),
        ("Lote aberto", index.open_batch),
    ):
        lines.append(
            f"| {label} | {counts.questions_extracted} | {counts.structurally_accepted} | "
            f"{counts.quarantined} | {counts.annulled} | {counts.classified} | "
            f"{counts.editorial_decisions} | {counts.ready_for_export} |"
        )
    lines.extend(["", "## Quarentena", "", "### Por motivo", ""])
    for reason, count in sorted(index.quarantine_reasons.items()):
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "### Combinações exclusivas", ""])
    for reason, count in sorted(index.quarantine_exclusive_combinations.items()):
        lines.append(f"- {reason}: {count}")
    lines.extend(
        [
            "",
            "## Classificação editorial",
            "",
            f"- Taxonomia canônica: `{index.classification.taxonomy_version}`.",
            f"- Regras determinísticas aceitas: {index.classification.deterministic}.",
            f"- Classificações Qwen aceitas: {index.classification.qwen}.",
            f"- Pendentes por falta de catálogo seguro: {index.classification.unresolved}.",
            f"- Chamadas ao Qwen: {index.classification.qwen_calls}.",
            (
                "- Precisão da amostra: não medida; nenhuma classificação foi liberada "
                "sem correção humana."
            ),
            f"- Rastro local: `{_portable_path(index.classification.trace_path)}`.",
        ]
    )
    lines.extend(["", "## Alertas de linhagem", ""])
    if index.lineage_warnings:
        lines.extend(f"- {warning}" for warning in index.lineage_warnings)
    else:
        lines.append("- Nenhuma divergência entre os anos esperados e os estruturados.")
    for dimension, values in index.grouped.items():
        lines.extend(
            [
                "",
                f"## Por {dimension}",
                "",
                "| Valor | Questões | Aceitas | Quarentena | Rejeitadas | Anuladas |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for value in values:
            lines.append(
                f"| {value['value']} | {value['questions']} | {value['accepted']} | "
                f"{value['quarantined']} | {value['rejected']} | {value['annulled']} |"
            )
    lines.extend(
        [
            "",
            "## Limitações e ação humana",
            "",
            f"- {index.total.ready_for_review} questões aguardam revisão humana explícita.",
            (
                "- A classificação editorial ainda não foi aceita sem correção: os "
                "catálogos canônicos atuais não cobrem integralmente PF e Banco do Brasil."
            ),
            (
                "- O Qwen não é chamado sem opções canônicas suficientes; indisponibilidade "
                "do Ollama não impede a criação da fila."
            ),
            (
                "- As categorias históricas da quarentena são comparadas com os motivos "
                "efetivamente presentes nos pacotes, sem apagar divergências."
            ),
            (
                "- Nenhum PDF, sessão completa, conteúdo de prova, credencial ou dado foi "
                "incluído neste relatório."
            ),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_consolidated_review(
    spec_path: Path,
    output_dir: Path,
    *,
    report_json_path: Path | None = None,
    report_markdown_path: Path | None = None,
) -> tuple[ConsolidatedReviewIndex, Path]:
    spec_path = spec_path.resolve()
    output_dir = output_dir.resolve()
    spec = ConsolidatedCorpusSpec.model_validate(read_json(spec_path))
    review_root = output_dir / "review"
    package_inventories: list[PackageInventory] = []
    lineage_warnings: list[str] = []
    entries: list[OperatorReviewBatch] = []
    rows: list[tuple[str, StructuredQuestion]] = []
    sample_rows: list[tuple[str, StructuredQuestion]] = []
    all_documents: dict[str, str] = {}
    stable_ids: Counter[str] = Counter()
    created_at: datetime | None = None

    for package_spec in spec.packages:
        package, manifest, package_inventory = _load_package(spec_path, package_spec)
        package_inventories.append(package_inventory)
        if (
            package_spec.expected_structured_years
            and package_inventory.structured_years
            != sorted(set(package_spec.expected_structured_years))
        ):
            lineage_warnings.append(
                f"{package_spec.label}: anos estruturados "
                f"{package_inventory.structured_years} diferem dos esperados "
                f"{sorted(set(package_spec.expected_structured_years))}."
            )
        if package_inventory.unused_documents:
            lineage_warnings.append(
                f"{package_spec.label}: {package_inventory.unused_documents} documento(s) "
                "do manifesto não têm questão no pacote estruturado."
            )
        for reason, expected_count in package_spec.expected_quarantine_reasons.items():
            actual_count = package_inventory.quarantine_reasons.get(reason, 0)
            if actual_count != expected_count:
                lineage_warnings.append(
                    f"{package_spec.label}: a referência de quarentena para '{reason}' "
                    f"é {expected_count}, mas o pacote final registra {actual_count}."
                )
        unexpected_reasons = (
            set(package_inventory.quarantine_reasons)
            - set(package_spec.expected_quarantine_reasons)
            if package_spec.expected_quarantine_reasons
            else set()
        )
        for reason in sorted(unexpected_reasons):
            lineage_warnings.append(
                f"{package_spec.label}: o pacote final registra "
                f"{package_inventory.quarantine_reasons[reason]} ocorrência(s) de "
                f"'{reason}', categoria ausente na referência fornecida."
            )
        created_at = max(created_at, manifest.created_at) if created_at else manifest.created_at
        for document in manifest.documents:
            previous = all_documents.get(document.sha256)
            if previous is not None and previous != document.document_type:
                raise ValueError(f"tipo divergente para documento {document.sha256}")
            all_documents[document.sha256] = document.document_type
        package_rows = _all_questions(package)
        rows.extend(package_rows)
        sample_rows.extend(
            (state, question)
            for state, question in package_rows
            if question.year in package_spec.sample_years
        )
        stable_ids.update(question.stable_id for _state, question in package_rows)
        new_entries = build_review_batches(
            review_root,
            run_id=f"consolidated:{spec.corpus_id}:{package_spec.id}",
            manifest=manifest,
            package=package,
        )
        entries.extend(new_entries)

    duplicate_ids = sum(count - 1 for count in stable_ids.values() if count > 1)
    if duplicate_ids:
        raise ValueError(f"o acervo contém {duplicate_ids} IDs estáveis duplicados")
    if len({entry.exam_sha256 for entry in entries}) != len(entries):
        raise ValueError("uma prova apareceu em mais de um pacote consolidado")
    entries.sort(key=lambda item: item.batch_id)
    session_state = _session_state(entries)
    total = _counts(
        rows,
        documents=len(all_documents),
        exams=sum(value == "exam" for value in all_documents.values()),
        answer_keys=sum(value == "answer_key" for value in all_documents.values()),
        session_state=session_state,
    )
    sample = _counts(sample_rows, session_state=session_state)
    sample_ids = {question.stable_id for _state, question in sample_rows}
    sample_decisions = 0
    for entry in entries:
        if entry.session_path is None:
            continue
        session = LocalReviewSession.model_validate(read_json(Path(entry.session_path)))
        for review_question, decision in zip(
            session.batch.questions, session.decisions, strict=True
        ):
            if (
                review_question.source_stable_id in sample_ids
                and decision.status != "pending"
            ):
                sample_decisions += 1
    sample = sample.model_copy(update={"editorial_decisions": sample_decisions})

    open_entry = next((item for item in entries if item.session_path is not None), None)
    open_rows: list[tuple[str, StructuredQuestion]] = []
    if open_entry is not None:
        exam_sha = open_entry.exam_sha256
        open_rows = [(state, item) for state, item in rows if item.exam_sha256 == exam_sha]
    open_counts = _counts(open_rows, session_state=session_state)

    taxonomy = EditorialTaxonomy.load_default()
    classification_trace_path = output_dir / "classification" / "traces.jsonl"
    classification_traces = [
        {
            "stable_id": question.stable_id,
            "method": "unresolved",
            "taxonomy_version": taxonomy.version,
            "model": None,
            "input_sha256": _canonical_sha256(
                {
                    "statement": question.statement,
                    "alternatives": question.alternatives,
                }
            ),
            "original_output": None,
            "accepted_classification": {
                "discipline": None,
                "matter": None,
                "subject": None,
                "level": None,
            },
            "confidence": 0,
            "duration_ms": 0,
            "accepted": False,
            "error": "catalog_not_available_for_corpus",
        }
        for _state, question in rows
    ]
    write_json_lines(classification_trace_path, classification_traces)
    classification = ClassificationInventory(
        taxonomy_version=taxonomy.version,
        trace_path=str(classification_trace_path),
        deterministic=0,
        qwen=0,
        unresolved=len(rows),
        qwen_calls=0,
        sample_size=0,
        precision_by_field={
            "discipline": None,
            "matter": None,
            "subject": None,
            "level": None,
        },
        accepted_without_correction=False,
    )

    reasons: Counter[str] = Counter()
    combinations: Counter[str] = Counter()
    for state, structured_question in rows:
        if state != "quarantined":
            continue
        normalized = sorted(set(structured_question.validation_reasons)) or [
            "sem motivo registrado"
        ]
        reasons.update(normalized)
        combinations[" + ".join(normalized)] += 1

    grouped = {
        "órgão": _group_rows(rows, "organization"),
        "banca": _group_rows(rows, "board"),
        "concurso": _group_rows(rows, "contest"),
        "ano": _group_rows(rows, "year"),
        "cargo": _group_rows(rows, "role"),
        "estado": [
            {
                "value": state,
                "questions": sum(item_state == state for item_state, _item in rows),
                "accepted": sum(item_state == "accepted" for item_state, _item in rows)
                if state == "accepted"
                else 0,
                "quarantined": sum(
                    item_state == "quarantined" for item_state, _item in rows
                )
                if state == "quarantined"
                else 0,
                "rejected": sum(item_state == "rejected" for item_state, _item in rows)
                if state == "rejected"
                else 0,
                "annulled": sum(
                    item.answer_association.original_answer == "X"
                    for item_state, item in rows
                    if item_state == state
                ),
            }
            for state in ("accepted", "quarantined", "rejected")
        ],
    }
    fingerprint = {
        "corpus_id": spec.corpus_id,
        "base_commit": spec.base_commit,
        "packages": [
            {
                "id": item.id,
                "content_sha256": item.package_content_sha256,
                "manifest_sha256s": item.manifest_sha256s,
            }
            for item in package_inventories
        ],
        "batches": [
            {
                "batch_id": item.batch_id,
                "exam_sha256": item.exam_sha256,
                "accepted": item.accepted,
                "quarantined": item.quarantined,
                "rejected": item.rejected,
            }
            for item in entries
        ],
        "total": total.model_dump(mode="json"),
        "classification": classification.model_dump(mode="json", exclude={"trace_path"}),
    }
    assert created_at is not None
    index = ConsolidatedReviewIndex(
        corpus_id=spec.corpus_id,
        base_commit=spec.base_commit,
        branch=spec.branch,
        created_at=created_at,
        packages=package_inventories,
        batches=entries,
        total=total,
        homologation_sample=sample,
        open_batch=open_counts,
        open_batch_id=open_entry.batch_id if open_entry else None,
        classification=classification,
        grouped=grouped,
        quarantine_reasons=dict(reasons),
        quarantine_exclusive_combinations=dict(combinations),
        lineage_warnings=lineage_warnings,
        duplicate_stable_ids=duplicate_ids,
        content_sha256=_canonical_sha256(fingerprint),
    )
    index_path = review_root / "index.json"
    write_json(index_path, index.model_dump(mode="json"))
    if report_json_path is not None:
        write_json(report_json_path, _versionable_payload(index))
    if report_markdown_path is not None:
        _write_markdown(index, report_markdown_path, spec_path=spec_path)
    return index, index_path
