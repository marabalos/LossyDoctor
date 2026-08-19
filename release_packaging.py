from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parent
VERSION = "1.1.0"
CHECKSUM_FILE = "PACKAGE_SHA256SUMS.txt"

ROOT_FILES = (
    "LossyDoctor.bat",
    "LossyDoctorBootstrap.exe",
    "bootstrap_manifest.json",
    "config.toml",
    "requirements.txt",
    "README.md",
    "PRODUCT.md",
    "V1_BASELINE.md",
    "ROADMAP.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "TRADEMARKS.md",
    "THIRD_PARTY_NOTICES.md",
)

SOURCE_FILES = (
    "app/__init__.py",
    "app/aac_adts_preservation_hierarchy.py",
    "app/aac_adts_recovery.py",
    "app/aac_adts_repair.py",
    "app/aac_adts_timeline.py",
    "app/config.py",
    "app/derived_evidence.py",
    "app/discovery.py",
    "app/evidence_matrix.py",
    "app/external.py",
    "app/lossless_export.py",
    "app/main.py",
    "app/models.py",
    "app/mp4_aac_preservation_hierarchy.py",
    "app/mp4_aac_recovery.py",
    "app/mp4_aac_repair.py",
    "app/mp4_aac_timeline.py",
    "app/mp4_aac_timeline_export.py",
    "app/opus_preservation_hierarchy.py",
    "app/opus_recovery.py",
    "app/pipeline.py",
    "app/preservation_hierarchy.py",
    "app/publication.py",
    "app/repairs.py",
    "app/utils.py",
    "app/version.py",
    "app/vorbis_preservation_hierarchy.py",
    "app/vorbis_recovery.py",
    "app/wma_multi_region_recovery.py",
    "app/wma_preservation_hierarchy.py",
    "app/wma_recovery.py",
    "formats/__init__.py",
    "formats/aac_adts.py",
    "formats/asf_wma.py",
    "formats/identify.py",
    "formats/mp4_aac.py",
    "formats/mpeg.py",
    "formats/ogg_opus.py",
    "formats/ogg_vorbis.py",
    "policy/__init__.py",
    "policy/engine.py",
    "reporting/__init__.py",
    "reporting/collection_index.py",
    "reporting/json_report.py",
    "reporting/markdown_report.py",
    "bootstrap_src/main.go",
)


def release_files() -> tuple[str, ...]:
    """Allowlist completa; ningún archivo fuera de ella puede entrar al ZIP."""
    return tuple(sorted((*ROOT_FILES, *SOURCE_FILES, CHECKSUM_FILE)))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def build_bootstrap(go_executable: Path) -> None:
    env = os.environ.copy()
    cache_root = ROOT / "cache" / "go-build"
    cache_root.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "GOOS": "windows",
            "GOARCH": "amd64",
            "CGO_ENABLED": "0",
            "GOCACHE": str(cache_root),
            "GOMODCACHE": str(ROOT / "cache" / "go-mod"),
        }
    )
    subprocess.run(
        [
            str(go_executable),
            "build",
            "-trimpath",
            "-buildvcs=false",
            "-ldflags=-s -w -buildid=",
            "-o",
            str(ROOT / "LossyDoctorBootstrap.exe"),
            str(ROOT / "bootstrap_src" / "main.go"),
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def _checksum_text_from_worktree() -> str:
    # El archivo de checksums se excluye a sí mismo para evitar autorreferencia.
    rows = []
    for relative in release_files():
        if relative == CHECKSUM_FILE:
            continue
        rows.append(f"{sha256(ROOT / relative)}  {relative}")
    return "\n".join(rows) + "\n"


def refresh_checksums() -> None:
    """Regenera el inventario antes del commit; el ZIP nunca altera este archivo."""
    (ROOT / CHECKSUM_FILE).write_text(
        _checksum_text_from_worktree(), encoding="utf-8", newline="\n"
    )


def git_bytes(commit: str, relative: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=ROOT)


def _zip_datetime(commit_epoch: int) -> tuple[int, int, int, int, int, int]:
    from datetime import datetime, timezone

    value = datetime.fromtimestamp(commit_epoch, timezone.utc)
    year = max(1980, value.year)
    return year, value.month, value.day, value.hour, value.minute, value.second // 2 * 2


def build_zip(output_dir: Path) -> tuple[Path, str, tuple[str, ...]]:
    commit = git_value("rev-parse", "HEAD")
    commit_epoch = int(git_value("show", "-s", "--format=%ct", "HEAD"))
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"LossyDoctor_v{VERSION}_source_{commit[:8]}.zip"
    committed = {relative: git_bytes(commit, relative) for relative in release_files()}
    checksum_rows = committed[CHECKSUM_FILE].decode("utf-8").splitlines()
    expected = {name: digest for digest, name in (row.split("  ", 1) for row in checksum_rows)}
    actual = {
        relative: hashlib.sha256(data).hexdigest()
        for relative, data in committed.items()
        if relative != CHECKSUM_FILE
    }
    if expected != actual:
        raise RuntimeError("PACKAGE_SHA256SUMS.txt no coincide con el contenido comprometido")

    timestamp = _zip_datetime(commit_epoch)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in release_files():
            info = zipfile.ZipInfo(relative, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, committed[relative], compresslevel=9)

    digest = sha256(destination)
    sidecar = Path(str(destination) + ".sha256")
    sidecar.write_text(f"{digest}  {destination.name}\n", encoding="ascii", newline="\n")
    return destination, digest, release_files()


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye el ZIP reproducible de LossyDoctor V1.1.0")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "temp" / "release")
    parser.add_argument("--go", type=Path, help="go.exe fijado para recompilar el bootstrap")
    parser.add_argument("--refresh-checksums", action="store_true", help="regenera checksums del árbol de trabajo y termina")
    args = parser.parse_args()
    if args.refresh_checksums:
        refresh_checksums()
        print(f"CHECKSUMS={ROOT / CHECKSUM_FILE}")
        return 0
    if args.go:
        build_bootstrap(args.go.resolve())
    archive, digest, files = build_zip(args.output_dir.resolve())
    print(f"ZIP={archive}")
    print(f"SHA256={digest}")
    print(f"FILES={len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
