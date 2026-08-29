#!/usr/bin/env python3
"""Build a deterministic cross-agent Skill ZIP with SKILL.md at archive root."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
import zipfile
from pathlib import Path


ROOT_FILES = ("SKILL.md", "VERSION")
RESOURCE_DIRS = ("agents", "assets", "references", "scripts")
EXCLUDED_PARTS = {".git", "__pycache__", "dist"}
EXCLUDED_NAMES = {".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def should_include(path: Path) -> bool:
    return (
        path.name not in EXCLUDED_NAMES
        and not any(part in EXCLUDED_PARTS for part in path.parts)
        and path.suffix not in EXCLUDED_SUFFIXES
    )


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in ROOT_FILES:
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少必需文件：{path}")
        files.append(path)

    for name in RESOURCE_DIRS:
        directory = root / name
        if not directory.is_dir():
            continue
        files.extend(path for path in directory.rglob("*") if path.is_file() and should_include(path))

    return sorted(set(files), key=lambda path: path.relative_to(root).as_posix())


def zip_info(archive_name: str, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(archive_name, FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = 0o755 if executable else 0o644
    info.external_attr = mode << 16
    info.create_system = 3
    return info


def build_package(root: Path, output: Path) -> Path:
    files = collect_files(root)
    manifest_lines: list[str] = []
    payloads: list[tuple[str, bytes, bool]] = []

    for path in files:
        archive_name = path.relative_to(root).as_posix()
        data = path.read_bytes()
        executable = path.suffix in {".py", ".sh"}
        payloads.append((archive_name, data, executable))
        manifest_lines.append(f"{sha256_bytes(data)}  {archive_name}")

    manifest = ("\n".join(manifest_lines) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for archive_name, data, executable in payloads:
            archive.writestr(zip_info(archive_name, executable), data)
        archive.writestr(zip_info("MANIFEST.sha256"), manifest)
    return output


def validate_package(package: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        required = {"SKILL.md", "VERSION", "MANIFEST.sha256"}
        missing = required.difference(names)
        if missing:
            raise ValueError(f"压缩包缺少：{', '.join(sorted(missing))}")
        if any(name.startswith("legal-case-workflow/") for name in names):
            raise ValueError("SKILL.md 必须位于压缩包根目录，不能再套仓库目录")

        manifest_entries = {}
        for line in archive.read("MANIFEST.sha256").decode("utf-8").splitlines():
            digest, name = line.split("  ", 1)
            manifest_entries[name] = digest
        for name, digest in manifest_entries.items():
            actual = sha256_bytes(archive.read(name))
            if actual != digest:
                raise ValueError(f"哈希不一致：{name}")


def self_test(root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="legal-skill-package-") as temp_dir:
        package = Path(temp_dir) / "skill.zip"
        build_package(root, package)
        validate_package(package)
    print("package_skill.py self-test: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="打包 Codex、Kimi Code、WorkBuddy 通用 Skill ZIP")
    parser.add_argument("--output", type=Path, help="输出 ZIP 路径")
    parser.add_argument("--self-test", action="store_true", help="在临时目录执行打包与完整性测试")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    if args.self_test:
        self_test(root)
        return 0

    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    output = args.output or root / "dist" / f"legal-case-workflow-v{version}-universal.zip"
    package = build_package(root, output.resolve())
    validate_package(package)
    print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
