from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import os
import re
import shutil
import uuid

from app.utils import (
    _collision_variants,
    json_write,
    json_write_exclusive,
    local_iso,
    sha256_file,
)


JOURNAL_SCHEMA = 1
SIDECAR_SUFFIX = ".lossydoctor-manifest.json"
_TERMINAL_STATES = {
    "COMPLETE",
    "RECOVERED_COMPLETE",
    "INTERRUPTED_BEFORE_OUTPUT",
    "ABORTED_COLLISION",
}


class PublicationRecoveryError(RuntimeError):
    """Una publicación interrumpida no puede resolverse sin intervención humana."""


def default_journal_root() -> Path:
    configured = os.environ.get("LOSSYDOCTOR_JOURNAL_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "state" / "publication_journal"


def _journal_path(journal_root: Path, transaction_id: str) -> Path:
    return journal_root / f"publication_{transaction_id}.json"


def _write_state(path: Path, record: dict, state: str, **detail) -> dict:
    updated = {**record, "state": state, "updated_at": local_iso(), **detail}
    json_write(path, updated)
    return updated


def _prepared_record(output: Path, output_sha256: str, manifest: dict) -> dict:
    transaction_id = uuid.uuid4().hex
    sidecar = Path(str(output) + SIDECAR_SUFFIX)
    final_manifest = deepcopy(manifest)
    final_manifest["output_path"] = str(output)
    final_manifest["output_sha256"] = output_sha256
    now = local_iso()
    return {
        "schema_version": JOURNAL_SCHEMA,
        "transaction_id": transaction_id,
        "state": "PREPARED",
        "created_at": now,
        "updated_at": now,
        "output_path": str(output),
        "sidecar_path": str(sidecar),
        "output_sha256": output_sha256,
        "manifest": final_manifest,
    }


def _is_collision_variant(desired: Path, candidate: Path) -> bool:
    if candidate.parent.resolve() != desired.parent.resolve() or candidate.suffix != desired.suffix:
        return False
    if candidate.name == desired.name:
        return True
    semantic = re.match(r"^(.*)\[([^\[\]]+)\]$", desired.stem)
    if semantic:
        pattern = rf"{re.escape(semantic.group(1))}\[{re.escape(semantic.group(2))} ([2-9][0-9]{{0,3}})\]"
    else:
        pattern = rf"{re.escape(desired.stem)} ([2-9][0-9]{{0,3}})"
    match = re.fullmatch(pattern, candidate.stem)
    return bool(match and int(match.group(1)) < 10000)


def _matching_publication(source: Path, desired: Path, manifest: dict) -> tuple[Path, Path, dict] | None:
    if not desired.parent.exists():
        return None
    expected_hash = sha256_file(source)
    for sidecar in sorted(desired.parent.glob(f"*{SIDECAR_SUFFIX}")):
        try:
            existing = json.loads(sidecar.read_text(encoding="utf-8"))
            output = Path(existing["output_path"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not _is_collision_variant(desired, output):
            continue
        try:
            exact_sidecar = sidecar.resolve() == Path(str(output) + SIDECAR_SUFFIX).resolve()
        except OSError:
            exact_sidecar = False
        if not exact_sidecar or not output.is_file() or existing.get("output_sha256") != expected_hash:
            continue
        if sha256_file(output) != expected_hash:
            continue
        expected_manifest = deepcopy(manifest)
        expected_manifest["output_path"] = str(output)
        expected_manifest["output_sha256"] = expected_hash
        if existing == expected_manifest:
            return output, sidecar, existing
    return None


def _prepare(source: Path, output: Path, manifest: dict, journal_root: Path) -> tuple[Path, dict]:
    record = _prepared_record(output, sha256_file(source), manifest)
    path = _journal_path(journal_root, record["transaction_id"])
    json_write_exclusive(path, record)
    return path, record


def _copy_exclusive(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, output.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())


def publish_with_manifest(
    source: Path,
    desired: Path,
    manifest: dict,
    journal_root: Path | None = None,
) -> tuple[Path, Path, dict]:
    """Publica la salida y su manifiesto sin sobrescribir, respaldado por un journal durable."""
    source = Path(source)
    desired = Path(desired)
    root = Path(journal_root) if journal_root is not None else default_journal_root()
    root.mkdir(parents=True, exist_ok=True)
    desired.parent.mkdir(parents=True, exist_ok=True)

    for output in _collision_variants(desired):
        sidecar = Path(str(output) + SIDECAR_SUFFIX)
        if output.exists() or sidecar.exists():
            continue
        journal_path, record = _prepare(source, output, manifest, root)
        try:
            _copy_exclusive(source, output)
        except FileExistsError:
            _write_state(journal_path, record, "ABORTED_COLLISION")
            continue

        if not output.is_file() or sha256_file(output) != record["output_sha256"]:
            _write_state(
                journal_path,
                record,
                "RECOVERY_BLOCKED",
                recovery_error="la copia publicada no coincide con el SHA-256 preparado",
            )
            raise PublicationRecoveryError(
                f"la copia publicada no coincide con el SHA-256 preparado; "
                f"no se creó el sidecar ni se modificó la salida dudosa: {output}"
            )

        json_write_exclusive(sidecar, record["manifest"])
        _write_state(journal_path, record, "COMPLETE", completed_at=local_iso())
        return output, sidecar, record["manifest"]

    raise RuntimeError("OUTPUT_COLLISION_EXHAUSTED")


def publish_or_preview_with_manifest(
    source: Path,
    desired: Path,
    manifest: dict,
    publish: bool,
    journal_root: Path | None = None,
) -> tuple[Path | None, Path | None, dict, str]:
    """Usa el mismo contrato de manifiesto para una publicación real o una vista previa de auditoría."""
    if publish:
        reused = _matching_publication(Path(source), Path(desired), manifest)
        if reused:
            return *reused, "REUSED"
        return *publish_with_manifest(source, desired, manifest, journal_root), "CREATED"
    preview = deepcopy(manifest)
    preview["output_path"] = None
    preview["output_sha256"] = sha256_file(source)
    preview["publication_status"] = "VERIFIED_NOT_PUBLISHED"
    return None, None, preview, "VERIFIED_NOT_PUBLISHED"


def combined_publication_status(outputs: list[dict]) -> str:
    statuses = {output.get("status") for output in outputs}
    if statuses == {"REUSED"}:
        return "REUSED"
    if statuses == {"CREATED"}:
        return "CREATED"
    if statuses == {"VERIFIED_NOT_PUBLISHED"}:
        return "VERIFIED_NOT_PUBLISHED"
    return "MIXED_CREATED_REUSED"


def _load_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PublicationRecoveryError(f"journal ilegible: {path} ({type(exc).__name__})") from exc
    if not isinstance(record, dict) or record.get("schema_version") != JOURNAL_SCHEMA:
        raise PublicationRecoveryError(f"journal con schema desconocido: {path}")
    return record


def _validated_paths(path: Path, record: dict) -> tuple[Path, Path, dict, str]:
    try:
        output = Path(record["output_path"])
        sidecar = Path(record["sidecar_path"])
        manifest = record["manifest"]
        expected_hash = record["output_sha256"]
    except (KeyError, TypeError) as exc:
        raise PublicationRecoveryError(f"journal incompleto: {path}") from exc
    if (
        not isinstance(manifest, dict)
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or sidecar != Path(str(output) + SIDECAR_SUFFIX)
        or manifest.get("output_path") != str(output)
        or manifest.get("output_sha256") != expected_hash
    ):
        raise PublicationRecoveryError(f"journal inconsistente: {path}")
    return output, sidecar, manifest, expected_hash


def _block(path: Path, record: dict, reason: str) -> None:
    _write_state(path, record, "RECOVERY_BLOCKED", recovery_error=reason)
    raise PublicationRecoveryError(f"{reason}; no se modificó la publicación: {path}")


def recover_interrupted_publications(journal_root: Path | None = None) -> list[dict]:
    """Resuelve publicaciones interrumpidas inequívocas y falla de forma cerrada en los demás casos."""
    root = Path(journal_root) if journal_root is not None else default_journal_root()
    if not root.exists():
        return []
    events: list[dict] = []
    for path in sorted(root.glob("publication_*.json")):
        record = _load_record(path)
        state = record.get("state")
        if state in _TERMINAL_STATES:
            continue
        if state not in {"PREPARED", "RECOVERY_BLOCKED"}:
            raise PublicationRecoveryError(f"estado de journal desconocido {state!r}: {path}")
        output, sidecar, manifest, expected_hash = _validated_paths(path, record)
        output_exists = output.exists()
        sidecar_exists = sidecar.exists()

        if not output_exists and not sidecar_exists:
            _write_state(path, record, "INTERRUPTED_BEFORE_OUTPUT", recovered_at=local_iso())
            events.append({"result": "INTERRUPTED_BEFORE_OUTPUT", "output_path": str(output)})
            continue
        if not output_exists and sidecar_exists:
            _block(path, record, "existe el sidecar pero falta la salida")
        if not output.is_file() or sha256_file(output) != expected_hash:
            _block(path, record, "la salida existente no coincide con el SHA-256 registrado")

        if sidecar_exists:
            try:
                actual_manifest = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                _block(path, record, "el sidecar existente no es JSON válido")
            if actual_manifest != manifest:
                _block(path, record, "el sidecar existente no coincide con el manifiesto registrado")
            result = "PUBLICATION_ALREADY_COMPLETE"
        else:
            try:
                json_write_exclusive(sidecar, manifest)
            except FileExistsError:
                try:
                    actual_manifest = json.loads(sidecar.read_text(encoding="utf-8"))
                except Exception:
                    _block(path, record, "apareció un sidecar ambiguo durante la recuperación")
                if actual_manifest != manifest:
                    _block(path, record, "apareció un sidecar diferente durante la recuperación")
            result = "SIDECAR_COMPLETED"

        _write_state(path, record, "RECOVERED_COMPLETE", recovered_at=local_iso())
        events.append({"result": result, "output_path": str(output), "sidecar_path": str(sidecar)})
    return events
