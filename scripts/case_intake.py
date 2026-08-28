#!/usr/bin/env python3
"""本地、非覆盖、可增量的法律案件材料入库工具。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Iterable
import zipfile


SCRIPT_VERSION = "1.0.0"
PARAMS_VERSION = "2026-08-24"
CASE_SUBDIRS = (
    "00_source",
    "01_manifest",
    "02_readable",
    "03_evidence",
    "04_legal",
    "05_drafts",
    "06_hearing",
    "99_audit",
)
TEMPLATE_MAP = {
    "CASE_INDEX.md": "01_manifest/CASE_INDEX.md",
    "FACT_MATRIX.md": "03_evidence/FACT_MATRIX.md",
    "TIMELINE.md": "03_evidence/TIMELINE.md",
    "ISSUES.md": "04_legal/ISSUES.md",
    "CONTRADICTIONS.md": "03_evidence/CONTRADICTIONS.md",
    "HEARING_OUTLINE.md": "06_hearing/HEARING_OUTLINE.md",
    "HANDOFF.md": "99_audit/HANDOFF.md",
}


class IntakeError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_case_location(case_dir: Path) -> None:
    workspace = Path(__file__).resolve().parents[4]
    cases_root = (workspace / "cases").resolve()
    resolved = case_dir.resolve()
    if not is_relative_to(resolved, cases_root) or resolved == cases_root:
        raise IntakeError(f"案件目录必须位于当前工作区 cases/ 内：{cases_root}")
    if resolved.name == "_template":
        raise IntakeError("cases/_template 仅是模板，不能作为真实案件目录")


def command_version(name: str) -> dict[str, Any]:
    executable = shutil.which(name)
    if not executable:
        return {"available": False, "version": None}
    for args in ([executable, "--version"], [executable, "-v"]):
        try:
            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=8,
                check=False,
            )
            line = (result.stdout or "").strip().splitlines()
            if line:
                return {"available": True, "version": line[0][:240]}
        except (OSError, subprocess.SubprocessError):
            continue
    return {"available": True, "version": "unknown"}


def python_capability(module_name: str) -> dict[str, Any]:
    if importlib.util.find_spec(module_name) is None:
        return {"available": False, "version": None}
    try:
        module = __import__(module_name)
        version = getattr(module, "__version__", "unknown")
    except Exception:
        version = "import_error"
    return {"available": True, "version": str(version)}


def detect_capabilities() -> dict[str, dict[str, Any]]:
    return {
        "pypdf": python_capability("pypdf"),
        "Pillow": python_capability("PIL"),
        "pdfinfo": command_version("pdfinfo"),
        "pdftotext": command_version("pdftotext"),
        "ocrmypdf": command_version("ocrmypdf"),
    }


def safe_member_name(raw_name: str) -> Path:
    normalized = raw_name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        raise IntakeError(f"压缩包包含不安全路径：{raw_name!r}")
    if pure.parts[0].endswith(":"):
        raise IntakeError(f"压缩包包含盘符路径：{raw_name!r}")
    return Path(*pure.parts)


def extract_zip_safely(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        planned: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in bundle.infolist():
            rel = safe_member_name(info.filename.rstrip("/")) if info.filename.rstrip("/") else None
            if rel is None:
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise IntakeError(f"压缩包包含符号链接：{info.filename!r}")
            target = (destination / rel).resolve()
            if not is_relative_to(target, destination.resolve()):
                raise IntakeError(f"压缩包路径越界：{info.filename!r}")
            planned.append((info, target))
        for info, target in planned:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def extract_tar_safely(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as bundle:
        planned: list[tuple[tarfile.TarInfo, Path]] = []
        for member in bundle.getmembers():
            rel = safe_member_name(member.name.rstrip("/")) if member.name.rstrip("/") else None
            if rel is None:
                continue
            if member.issym() or member.islnk() or member.isdev():
                raise IntakeError(f"压缩包包含链接或设备文件：{member.name!r}")
            if not (member.isdir() or member.isfile()):
                raise IntakeError(f"压缩包包含不支持的成员：{member.name!r}")
            target = (destination / rel).resolve()
            if not is_relative_to(target, destination.resolve()):
                raise IntakeError(f"压缩包路径越界：{member.name!r}")
            planned.append((member, target))
        for member, target in planned:
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = bundle.extractfile(member)
            if extracted is None:
                raise IntakeError(f"无法读取压缩成员：{member.name!r}")
            with extracted, target.open("xb") as output:
                shutil.copyfileobj(extracted, output)


def archive_kind(path: Path) -> str | None:
    if zipfile.is_zipfile(path):
        return "zip"
    try:
        if tarfile.is_tarfile(path):
            return "tar"
    except OSError:
        pass
    return None


def iter_regular_files(root: Path) -> Iterable[tuple[Path, Path]]:
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(
            name for name in dirs if not (current_path / name).is_symlink()
        )
        for name in sorted(files):
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path, path.relative_to(root)


def collect_source_files(source: Path, temporary_root: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    failures: list[str] = []
    if source.is_symlink():
        raise IntakeError("源路径不能是符号链接")
    if source.is_dir():
        return list(iter_regular_files(source)), failures
    if not source.is_file():
        raise IntakeError(f"材料来源不存在或不是普通文件：{source}")
    kind = archive_kind(source)
    if kind:
        extracted = temporary_root / "extracted"
        extracted.mkdir(parents=True)
        if kind == "zip":
            extract_zip_safely(source, extracted)
        else:
            extract_tar_safely(source, extracted)
        return list(iter_regular_files(extracted)), failures
    suffixes = "".join(source.suffixes).lower()
    if suffixes.endswith((".rar", ".7z")):
        raise IntakeError("该压缩格式需要本机可选工具，当前脚本不会自动安装或不安全调用")
    return [(source, Path(source.name))], failures


def sniff_mime(path: Path) -> str:
    try:
        head = path.read_bytes()[:32]
    except OSError:
        head = b""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def image_dimensions_stdlib(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as handle:
        head = handle.read(32)
        if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
            return struct.unpack(">II", head[16:24])
        if head.startswith((b"GIF87a", b"GIF89a")) and len(head) >= 10:
            return struct.unpack("<HH", head[6:10])
        if not head.startswith(b"\xff\xd8"):
            return None
        handle.seek(2)
        while True:
            marker_start = handle.read(1)
            if not marker_start:
                return None
            if marker_start != b"\xff":
                continue
            marker = handle.read(1)
            while marker == b"\xff":
                marker = handle.read(1)
            if marker in (b"\xd8", b"\xd9"):
                continue
            length_raw = handle.read(2)
            if len(length_raw) != 2:
                return None
            length = struct.unpack(">H", length_raw)[0]
            if length < 2:
                return None
            if marker and marker[0] in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                data = handle.read(length - 2)
                if len(data) < 5:
                    return None
                height, width = struct.unpack(">HH", data[1:5])
                return width, height
            handle.seek(length - 2, os.SEEK_CUR)


def inspect_image(path: Path, capabilities: dict[str, Any]) -> dict[str, Any]:
    width = height = None
    method = "unavailable"
    if capabilities["Pillow"]["available"]:
        try:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
            method = "Pillow"
        except Exception:
            pass
    if width is None:
        try:
            dimensions = image_dimensions_stdlib(path)
            if dimensions:
                width, height = dimensions
                method = "stdlib_header"
        except (OSError, ValueError, struct.error):
            pass
    result: dict[str, Any] = {
        "width": width,
        "height": height,
        "dimension_detection": method,
        "long_image": None,
    }
    if width and height:
        result["long_image"] = height >= 3000 or height / max(width, 1) >= 3.0
    return result


def inspect_pdf(path: Path, capabilities: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "page_count": None,
        "text_layer_status": "capability_unavailable",
        "sampled_pages": [],
        "sample_character_counts": [],
        "inspection_method": None,
    }
    if capabilities["pypdf"]["available"]:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            count = len(reader.pages)
            sample_indexes = sorted(set(index for index in (0, count // 2, count - 1) if 0 <= index < count))
            char_counts = []
            for index in sample_indexes:
                text = reader.pages[index].extract_text() or ""
                char_counts.append(len(text.strip()))
            result.update(
                {
                    "page_count": count,
                    "text_layer_status": "present" if any(value >= 20 for value in char_counts) else "not_detected",
                    "sampled_pages": [index + 1 for index in sample_indexes],
                    "sample_character_counts": char_counts,
                    "inspection_method": "pypdf",
                }
            )
            return result
        except Exception as exc:
            result["pypdf_error"] = f"{type(exc).__name__}: {exc}"[:500]
    if capabilities["pdfinfo"]["available"]:
        try:
            completed = subprocess.run(
                ["pdfinfo", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            for line in completed.stdout.splitlines():
                if line.lower().startswith("pages:"):
                    result["page_count"] = int(line.split(":", 1)[1].strip())
                    break
            result["inspection_method"] = "pdfinfo"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result["pdfinfo_error"] = f"{type(exc).__name__}: {exc}"[:500]
    if capabilities["pdftotext"]["available"]:
        try:
            completed = subprocess.run(
                ["pdftotext", "-f", "1", "-l", "3", str(path), "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
            if completed.returncode == 0:
                count = len(completed.stdout.decode("utf-8", errors="replace").strip())
                result["text_layer_status"] = "present" if count >= 20 else "not_detected"
                result["sampled_pages"] = [1, 2, 3]
                result["sample_character_counts"] = [count]
                result["inspection_method"] = (result["inspection_method"] or "") + "+pdftotext"
        except (OSError, subprocess.SubprocessError) as exc:
            result["pdftotext_error"] = f"{type(exc).__name__}: {exc}"[:500]
    return result


def material_number(records: list[dict[str, Any]]) -> str:
    highest = 0
    for record in records:
        value = str(record.get("material_id", ""))
        if value.startswith("DOC-") and value[4:].isdigit():
            highest = max(highest, int(value[4:]))
    return f"DOC-{highest + 1:03d}"


def collision_safe_destination(source_root: Path, logical: Path, digest: str) -> tuple[Path, str]:
    candidate = source_root / logical
    if not candidate.exists():
        return candidate, "new"
    if candidate.is_file() and sha256_file(candidate) == digest:
        return candidate, "unchanged"
    stem = candidate.stem
    suffix = candidate.suffix
    replacement = candidate.with_name(f"{stem}__{digest[:12]}{suffix}")
    serial = 1
    while replacement.exists():
        if replacement.is_file() and sha256_file(replacement) == digest:
            return replacement, "unchanged"
        replacement = candidate.with_name(f"{stem}__{digest[:12]}-{serial}{suffix}")
        serial += 1
    return replacement, "renamed_conflict"


def cache_key(digest: str, capabilities: dict[str, Any], ocr_requested: bool) -> str:
    payload = {
        "source_sha256": digest,
        "script_version": SCRIPT_VERSION,
        "params_version": PARAMS_VERSION,
        "ocr_requested": ocr_requested,
        "ocr_tool_version": capabilities["ocrmypdf"].get("version"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def write_page_markdown(material_id: str, pages: list[str], output: Path, method: str) -> dict[str, Any]:
    lines = [f"# {material_id} 分页文本", "", "> 仅供检索，引用前须回看原始 PDF 页面。", ""]
    for index, text in enumerate(pages, start=1):
        lines.extend(
            [
                f"## {material_id}｜PDF 第 {index} 页",
                "",
                text.strip() or "[本页未提取到文本]",
                "",
            ]
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return {
        "status": "success",
        "output": str(output.name),
        "method": method,
        "page_sections": len(pages),
    }


def extract_pdf_markdown(
    path: Path,
    material_id: str,
    output: Path,
    capabilities: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if capabilities["pypdf"]["available"]:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages = [(page.extract_text() or "") for page in reader.pages]
            return write_page_markdown(material_id, pages, output, "pypdf")
        except Exception as exc:
            errors.append(f"pypdf: {type(exc).__name__}: {exc}"[:500])
    if capabilities["pdftotext"]["available"]:
        try:
            completed = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=600,
                check=False,
            )
            if completed.returncode != 0:
                errors.append(
                    "pdftotext: "
                    + completed.stderr.decode("utf-8", errors="replace")[-500:]
                )
            else:
                extracted = completed.stdout.decode("utf-8", errors="replace")
                pages = extracted.split("\f")
                if pages and not pages[-1].strip():
                    pages.pop()
                if not pages:
                    pages = [""]
                return write_page_markdown(material_id, pages, output, "pdftotext")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"pdftotext: {type(exc).__name__}: {exc}"[:500])
    if not capabilities["pypdf"]["available"] and not capabilities["pdftotext"]["available"]:
        return {
            "status": "capability_unavailable",
            "required_any_of": ["pypdf", "pdftotext"],
        }
    return {"status": "failed", "error": " | ".join(errors)[:1000]}


def run_ocr(path: Path, material_id: str, readable_dir: Path) -> dict[str, Any]:
    output = readable_dir / f"{material_id}_ocr.pdf"
    try:
        completed = subprocess.run(
            ["ocrmypdf", "--skip-text", "--output-type", "pdf", str(path), str(output)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3600,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
    if completed.returncode != 0 or not output.exists():
        return {
            "status": "failed",
            "exit_code": completed.returncode,
            "error": completed.stderr.decode("utf-8", errors="replace")[-1000:],
        }
    return {"status": "success", "output": output.name, "sha256": sha256_file(output)}


def copy_templates(case_dir: Path) -> None:
    template_root = Path(__file__).resolve().parents[1] / "assets" / "templates"
    for template_name, relative_target in TEMPLATE_MAP.items():
        # CASE_INDEX 由 manifest 确定性生成；首次入库时不先复制空表，避免产生无意义备份。
        if template_name == "CASE_INDEX.md":
            continue
        target = case_dir / relative_target
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_root / template_name, target)


def write_case_index(path: Path, records: list[dict[str, Any]]) -> None:
    if path.exists():
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.backup-{timestamp}")
        counter = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.backup-{timestamp}-{counter}")
            counter += 1
        shutil.copy2(path, backup)
    lines = [
        "# 案件材料索引",
        "",
        "> 本表由入库脚本根据 manifest 生成。简要内容、关联争点和视觉复核状态需人工补充；引用前必须回看原始材料。",
        "",
        "| 材料编号 | 原文件名 | 文件类型 | 页数或切片数 | 简要内容 | 文本层状态 | OCR 状态 | 视觉复核状态 | 关联争点 | 备注 |",
        "|---|---|---|---:|---|---|---|---|---|---|",
    ]
    for record in records:
        pdf = record.get("pdf") or {}
        image = record.get("image") or {}
        pages = pdf.get("page_count")
        count = str(pages) if pages is not None else ("1" if image.get("width") else "待核对")
        transformations = record.get("transformations") or []
        ocr_states = [item.get("status") for item in transformations if item.get("type") == "ocr"]
        ocr_status = ocr_states[-1] if ocr_states else "未执行"
        note = "超长图片" if image.get("long_image") else ""
        values = [
            record.get("material_id", ""),
            record.get("original_filename", ""),
            record.get("mime_type", ""),
            count,
            "待核对",
            pdf.get("text_layer_status", "不适用"),
            ocr_status,
            "待核对",
            "",
            note,
        ]
        escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
        lines.append("| " + " | ".join(escaped) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntakeError(f"现有 manifest 无法解析，拒绝覆盖：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise IntakeError("现有 manifest 结构无效，拒绝覆盖")
    return data


def perform_intake(
    source: Path,
    case_dir: Path,
    *,
    dry_run: bool,
    incremental: bool,
    ocr_requested: bool,
    enforce_workspace: bool = True,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    case_dir = case_dir.expanduser().resolve()
    if enforce_workspace:
        validate_case_location(case_dir)
    capabilities = detect_capabilities()
    manifest_path = case_dir / "01_manifest" / "manifest.json"
    existing = load_manifest(manifest_path)
    if existing and not incremental:
        raise IntakeError("案件已有 manifest；如为新增材料，请显式使用 --incremental")
    records = list(existing.get("files", [])) if existing else []
    failures = list(existing.get("failures", [])) if existing else []
    run_summary: dict[str, Any] = {
        "started_at": utc_now(),
        "source": str(source),
        "case_dir": str(case_dir),
        "dry_run": dry_run,
        "incremental": incremental,
        "ocr_requested": ocr_requested,
        "new": 0,
        "unchanged": 0,
        "renamed_conflicts": 0,
        "failed": 0,
        "planned_files": [],
    }
    with tempfile.TemporaryDirectory(prefix="legal-case-intake-") as temp_name:
        candidates, collection_failures = collect_source_files(source, Path(temp_name))
        failures.extend(
            {"stage": "collect", "message": message, "recorded_at": utc_now()}
            for message in collection_failures
        )
        for source_file, logical_path in candidates:
            try:
                digest = sha256_file(source_file)
                target, disposition = collision_safe_destination(case_dir / "00_source", logical_path, digest)
                plan = {
                    "source_relative_path": logical_path.as_posix(),
                    "target_relative_path": target.relative_to(case_dir).as_posix(),
                    "sha256": digest,
                    "disposition": disposition,
                }
                run_summary["planned_files"].append(plan)
                if disposition == "unchanged":
                    run_summary["unchanged"] += 1
                    continue
                if dry_run:
                    run_summary["new"] += 1
                    if disposition == "renamed_conflict":
                        run_summary["renamed_conflicts"] += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
                if sha256_file(target) != digest:
                    raise IntakeError("复制后 SHA-256 不一致")
                material_id = material_number(records)
                stat = target.stat()
                mime = sniff_mime(target)
                record: dict[str, Any] = {
                    "material_id": material_id,
                    "original_filename": logical_path.name,
                    "source_relative_path": logical_path.as_posix(),
                    "stored_relative_path": target.relative_to(case_dir).as_posix(),
                    "size_bytes": stat.st_size,
                    "sha256": digest,
                    "extension": target.suffix.lower(),
                    "mime_type": mime,
                    "modified_time": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).replace(microsecond=0).isoformat(),
                    "intake_time": utc_now(),
                    "status": "ingested",
                    "collision_handling": disposition,
                    "cache_key": cache_key(digest, capabilities, ocr_requested),
                    "transformations": [],
                }
                if mime == "application/pdf":
                    record["pdf"] = inspect_pdf(target, capabilities)
                    if record["pdf"]["text_layer_status"] == "present":
                        readable = case_dir / "02_readable" / f"{material_id}_pages.md"
                        transformation = extract_pdf_markdown(target, material_id, readable, capabilities)
                        transformation["type"] = "text_extraction"
                        record["transformations"].append(transformation)
                    if ocr_requested and record["pdf"]["text_layer_status"] != "present":
                        if capabilities["ocrmypdf"]["available"]:
                            transformation = run_ocr(target, material_id, case_dir / "02_readable")
                            transformation["type"] = "ocr"
                        else:
                            transformation = {
                                "type": "ocr",
                                "status": "capability_unavailable",
                                "tool": "ocrmypdf",
                            }
                        record["transformations"].append(transformation)
                elif mime.startswith("image/"):
                    record["image"] = inspect_image(target, capabilities)
                records.append(record)
                run_summary["new"] += 1
                if disposition == "renamed_conflict":
                    run_summary["renamed_conflicts"] += 1
            except Exception as exc:
                failure = {
                    "stage": "ingest",
                    "source_relative_path": logical_path.as_posix(),
                    "message": f"{type(exc).__name__}: {exc}"[:1000],
                    "recorded_at": utc_now(),
                }
                failures.append(failure)
                run_summary["failed"] += 1

    run_summary["completed_at"] = utc_now()
    if dry_run:
        run_summary["capabilities"] = capabilities
        return run_summary

    for subdir in CASE_SUBDIRS:
        (case_dir / subdir).mkdir(parents=True, exist_ok=True)
    copy_templates(case_dir)
    manifest = {
        "schema_version": 1,
        "script_version": SCRIPT_VERSION,
        "params_version": PARAMS_VERSION,
        "case_directory": case_dir.name,
        "generated_at": utc_now(),
        "capabilities": capabilities,
        "files": records,
        "failures": failures,
        "runs": list(existing.get("runs", [])) + [run_summary] if existing else [run_summary],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    write_case_index(case_dir / "01_manifest" / "CASE_INDEX.md", records)
    handoff = case_dir / "99_audit" / "HANDOFF.md"
    with handoff.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n## 入库运行记录 {run_summary['completed_at']}\n\n"
            f"- 新增：{run_summary['new']}\n"
            f"- 哈希未变、跳过：{run_summary['unchanged']}\n"
            f"- 同名冲突改用安全衍生名：{run_summary['renamed_conflicts']}\n"
            f"- 失败：{run_summary['failed']}\n"
            "- 关键字段与可读化质量：待人工视觉复核。\n"
        )
    run_summary["manifest"] = str(manifest_path)
    return run_summary


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="legal-case-intake-selftest-") as temp_name:
        root = Path(temp_name)
        source = root / "synthetic-source"
        source.mkdir()
        (source / "note.txt").write_text("synthetic legal-case-workflow self-test\n", encoding="utf-8")
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000040802000000"
            "c66b3d7e0000000c49444154789c6360606060000000050001a5f64540"
            "0000000049454e44ae426082"
        )
        (source / "long.png").write_bytes(png)
        synthetic_pages = root / "synthetic-pages.md"
        page_result = write_page_markdown(
            "DOC-999", ["page one", "page two"], synthetic_pages, "selftest"
        )
        assert page_result["status"] == "success" and page_result["page_sections"] == 2
        page_text = synthetic_pages.read_text(encoding="utf-8")
        assert "DOC-999｜PDF 第 1 页" in page_text and "DOC-999｜PDF 第 2 页" in page_text
        case_dir = root / "workspace" / "cases" / "CASE-2099-001-selftest"
        result = perform_intake(
            source,
            case_dir,
            dry_run=False,
            incremental=False,
            ocr_requested=False,
            enforce_workspace=False,
        )
        manifest = json.loads((case_dir / "01_manifest" / "manifest.json").read_text(encoding="utf-8"))
        assert result["failed"] == 0
        assert len(manifest["files"]) == 2
        assert all(len(item["sha256"]) == 64 for item in manifest["files"])
        repeat = perform_intake(
            source,
            case_dir,
            dry_run=False,
            incremental=True,
            ocr_requested=False,
            enforce_workspace=False,
        )
        assert repeat["new"] == 0 and repeat["unchanged"] == 2
        dry_case = root / "workspace" / "cases" / "CASE-2099-002-dry"
        dry = perform_intake(
            source,
            dry_case,
            dry_run=True,
            incremental=False,
            ocr_requested=False,
            enforce_workspace=False,
        )
        assert dry["new"] == 2 and not dry_case.exists()
        unsafe = root / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as bundle:
            bundle.writestr("../escape.txt", "blocked")
        try:
            perform_intake(
                unsafe,
                root / "workspace" / "cases" / "CASE-2099-003-unsafe",
                dry_run=True,
                incremental=False,
                ocr_requested=False,
                enforce_workspace=False,
            )
        except IntakeError:
            pass
        else:
            raise AssertionError("路径穿越压缩包未被拒绝")
        assert not (root / "escape.txt").exists()
    print(json.dumps({"self_test": "passed", "script": Path(__file__).name, "version": SCRIPT_VERSION}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地、非覆盖、可增量的案件材料入库")
    parser.add_argument("--source", type=Path, help="源目录、单个文件或 ZIP/TAR 压缩包")
    parser.add_argument("--case-dir", type=Path, help="当前工作区 cases/ 下的目标案件目录")
    parser.add_argument("--dry-run", action="store_true", help="仅输出计划，不写入案件目录")
    parser.add_argument("--incremental", action="store_true", help="基于现有 manifest 只处理新增或变化文件")
    parser.add_argument("--ocr", action="store_true", help="仅在本机已有 ocrmypdf 时尝试 OCR；绝不自动安装")
    parser.add_argument("--self-test", action="store_true", help="只在临时目录运行程序生成的虚拟文件自测")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()
    if args.source is None or args.case_dir is None:
        print("错误：除 --self-test 外，必须同时提供 --source 和 --case-dir", file=sys.stderr)
        return 2
    try:
        result = perform_intake(
            args.source,
            args.case_dir,
            dry_run=args.dry_run,
            incremental=args.incremental,
            ocr_requested=args.ocr,
        )
    except IntakeError as exc:
        print(f"入库被安全停止：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
