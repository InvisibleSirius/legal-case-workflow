#!/usr/bin/env python3
"""验证案件目录、manifest、源文件哈希和材料引用。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


SCRIPT_VERSION = "1.0.0"
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
EXPECTED_WORK_FILES = (
    "01_manifest/CASE_INDEX.md",
    "03_evidence/FACT_MATRIX.md",
    "03_evidence/TIMELINE.md",
    "03_evidence/CONTRADICTIONS.md",
    "04_legal/ISSUES.md",
    "06_hearing/HEARING_OUTLINE.md",
    "99_audit/HANDOFF.md",
)
DERIVATIVE_NAME_PATTERNS = (
    re.compile(r"(?:^|[_-])ocr(?:[_-]|\.)", re.IGNORECASE),
    re.compile(r"(?:^|[_-])searchable(?:[_-]|\.)", re.IGNORECASE),
    re.compile(r"(?:^|[_-])page[-_]?\d+", re.IGNORECASE),
    re.compile(r"(?:^|[_-])slice[-_]?\d+", re.IGNORECASE),
)
SOURCE_ID_RE = re.compile(r"\bDOC-\d{3,}\b")
LOCATION_RE = re.compile(
    r"(?:PDF\s*第?\s*\d+\s*页|PDF\s*page\s*\d+|原页码\s*\d+|"
    r"截图|切片|坐标|y\d{3,}(?:[-–—_]y?\d{3,})?)",
    re.IGNORECASE,
)
UNVERIFIED_RE = re.compile(r"未提供|待核对|待核事项|待核事实|待人工|未核验")


class ValidationUsageError(RuntimeError):
    pass


def now_iso() -> str:
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
        raise ValidationUsageError(f"案件目录必须位于当前工作区 cases/ 内：{cases_root}")
    if resolved.name == "_template":
        raise ValidationUsageError("cases/_template 仅是模板，不能作为真实案件目录")


def issue(level: str, code: str, message: str, path: str | None = None, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"level": level, "code": code, "message": message}
    if path is not None:
        item["path"] = path
    item.update(extra)
    return item


def safe_manifest_path(case_dir: Path, relative: str) -> Path | None:
    candidate = (case_dir / relative).resolve()
    source_root = (case_dir / "00_source").resolve()
    if not is_relative_to(candidate, source_root) or candidate == source_root:
        return None
    return candidate


def load_manifest(path: Path, issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not path.exists():
        issues.append(issue("error", "manifest_missing", "缺少 01_manifest/manifest.json", str(path)))
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(issue("error", "manifest_invalid_json", f"manifest 无法解析：{exc}", str(path)))
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        issues.append(issue("error", "manifest_invalid_schema", "manifest 必须是对象且 files 必须是数组", str(path)))
        return None
    return data


def check_directories(case_dir: Path, issues: list[dict[str, Any]]) -> None:
    for relative in CASE_SUBDIRS:
        target = case_dir / relative
        if not target.is_dir():
            issues.append(issue("error", "required_directory_missing", f"缺少目录 {relative}/", relative))
    for relative in EXPECTED_WORK_FILES:
        if not (case_dir / relative).is_file():
            issues.append(issue("warning", "expected_work_file_missing", f"缺少建议的工作文件 {relative}", relative))


def check_manifest_records(case_dir: Path, manifest: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"manifest_files": 0, "hash_verified": 0, "hash_failed": 0, "conversion_failures": 0}
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, record in enumerate(manifest.get("files", [])):
        counts["manifest_files"] += 1
        label = f"files[{index}]"
        if not isinstance(record, dict):
            issues.append(issue("error", "manifest_record_invalid", f"{label} 不是对象"))
            continue
        material_id = str(record.get("material_id", ""))
        if not re.fullmatch(r"DOC-\d{3,}", material_id):
            issues.append(issue("error", "material_id_invalid", f"{label} 的材料编号无效：{material_id!r}"))
        elif material_id in seen_ids:
            issues.append(issue("error", "material_id_duplicate", f"材料编号重复：{material_id}"))
        seen_ids.add(material_id)

        digest = str(record.get("sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            issues.append(issue("error", "sha256_missing_or_invalid", f"{material_id or label} 缺少有效 SHA-256"))
        relative = str(record.get("stored_relative_path", ""))
        if relative in seen_paths:
            issues.append(issue("error", "stored_path_duplicate", f"manifest 中存储路径重复：{relative}"))
        seen_paths.add(relative)
        source_path = safe_manifest_path(case_dir, relative) if relative else None
        if source_path is None:
            issues.append(issue("error", "source_path_outside_raw", f"{material_id or label} 的存储路径不在 00_source/ 内：{relative!r}"))
        elif not source_path.is_file():
            issues.append(issue("error", "source_file_missing", f"manifest 指向的原始文件不存在：{relative}", relative))
        elif re.fullmatch(r"[0-9a-f]{64}", digest):
            try:
                actual = sha256_file(source_path)
                if actual != digest:
                    counts["hash_failed"] += 1
                    issues.append(
                        issue(
                            "error",
                            "sha256_mismatch",
                            f"原始文件哈希与 manifest 不一致：{relative}",
                            relative,
                            expected=digest,
                            actual=actual,
                        )
                    )
                else:
                    counts["hash_verified"] += 1
            except OSError as exc:
                counts["hash_failed"] += 1
                issues.append(issue("error", "source_read_failed", f"无法读取原始文件：{exc}", relative))

        for transformation in record.get("transformations", []) or []:
            if isinstance(transformation, dict) and transformation.get("status") == "failed":
                counts["conversion_failures"] += 1
                issues.append(
                    issue(
                        "warning",
                        "conversion_failed",
                        f"{material_id or label} 存在转换失败项：{transformation.get('type', 'unknown')}",
                        relative or None,
                    )
                )

    failures = manifest.get("failures", [])
    if isinstance(failures, list):
        for failure in failures:
            counts["conversion_failures"] += 1
            message = failure.get("message", "未记录错误信息") if isinstance(failure, dict) else str(failure)
            issues.append(issue("warning", "manifest_failure", f"manifest 记录失败项：{message}"))
    else:
        issues.append(issue("error", "manifest_failures_invalid", "manifest.failures 必须是数组"))
    return counts


def check_source_for_derivatives(case_dir: Path, issues: list[dict[str, Any]]) -> int:
    raw_root = case_dir / "00_source"
    suspicious = 0
    if not raw_root.is_dir():
        return suspicious
    for current, dirs, files in os.walk(raw_root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
        for name in files:
            path = current_path / name
            if path.is_symlink():
                issues.append(issue("warning", "source_symlink", "00_source 中存在符号链接，应人工确认", str(path.relative_to(case_dir))))
                continue
            if any(pattern.search(name) for pattern in DERIVATIVE_NAME_PATTERNS):
                suspicious += 1
                issues.append(
                    issue(
                        "warning",
                        "possible_derivative_in_source",
                        "00_source 中存在疑似 OCR、可搜索版、页面图或切片文件；需确认其是否为接收时原件",
                        str(path.relative_to(case_dir)),
                    )
                )
    return suspicious


def markdown_files(case_dir: Path) -> list[Path]:
    roots = [
        case_dir / "02_readable",
        case_dir / "03_evidence",
        case_dir / "04_legal",
        case_dir / "05_drafts",
        case_dir / "06_hearing",
        case_dir / "99_audit",
    ]
    excluded = {"validation-report.md", "validation-report.json"}
    results: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        results.extend(path for path in root.rglob("*.md") if path.name not in excluded and path.is_file())
    return sorted(results)


def check_markdown(case_dir: Path, issues: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"markdown_files": 0, "material_references": 0, "references_missing_location": 0, "unverified_fields": 0}
    for path in markdown_files(case_dir):
        counts["markdown_files"] += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            issues.append(issue("error", "markdown_unreadable", f"Markdown 无法读取：{exc}", str(path.relative_to(case_dir))))
            continue
        for line_number, line in enumerate(lines, start=1):
            matches = SOURCE_ID_RE.findall(line)
            counts["material_references"] += len(matches)
            if matches:
                start = max(0, line_number - 2)
                end = min(len(lines), line_number + 1)
                context = " ".join(lines[start:end])
                if not LOCATION_RE.search(context):
                    counts["references_missing_location"] += len(matches)
                    issues.append(
                        issue(
                            "warning",
                            "material_reference_missing_location",
                            f"材料引用 {', '.join(matches)} 附近未发现 PDF 页码或截图坐标",
                            str(path.relative_to(case_dir)),
                            line=line_number,
                        )
                    )
            unverified = UNVERIFIED_RE.findall(line)
            if unverified:
                counts["unverified_fields"] += len(unverified)
    return counts


def validate(case_dir: Path, *, enforce_workspace: bool = True) -> dict[str, Any]:
    case_dir = case_dir.expanduser().resolve()
    if enforce_workspace:
        validate_case_location(case_dir)
    if not case_dir.is_dir():
        raise ValidationUsageError(f"案件目录不存在：{case_dir}")
    issues: list[dict[str, Any]] = []
    check_directories(case_dir, issues)
    manifest = load_manifest(case_dir / "01_manifest" / "manifest.json", issues)
    counts: dict[str, Any] = {
        "manifest_files": 0,
        "hash_verified": 0,
        "hash_failed": 0,
        "conversion_failures": 0,
    }
    if manifest is not None:
        counts.update(check_manifest_records(case_dir, manifest, issues))
    counts["possible_derivatives_in_source"] = check_source_for_derivatives(case_dir, issues)
    counts.update(check_markdown(case_dir, issues))
    counts["errors"] = sum(item["level"] == "error" for item in issues)
    counts["warnings"] = sum(item["level"] == "warning" for item in issues)
    return {
        "schema_version": 1,
        "script_version": SCRIPT_VERSION,
        "generated_at": now_iso(),
        "case_dir": str(case_dir),
        "status": "pass" if counts["errors"] == 0 else "fail",
        "counts": counts,
        "issues": issues,
    }


def markdown_report(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# 案件目录验证报告",
        "",
        f"- 验证状态：{'通过' if report['status'] == 'pass' else '未通过'}",
        f"- 生成时间：{report['generated_at']}",
        f"- 案件目录：`{report['case_dir']}`",
        f"- manifest 文件数：{counts['manifest_files']}",
        f"- SHA-256 核验通过：{counts['hash_verified']}",
        f"- SHA-256 核验失败：{counts['hash_failed']}",
        f"- 转换失败项：{counts['conversion_failures']}",
        f"- 待核字段标记数：{counts['unverified_fields']}",
        f"- 材料引用缺少位置数：{counts['references_missing_location']}",
        f"- 错误：{counts['errors']}；警告：{counts['warnings']}",
        "",
        "## 问题清单",
        "",
    ]
    if not report["issues"]:
        lines.append("未发现结构性错误或警告。待核字段仍应按案件阶段人工处理。")
    else:
        for item in report["issues"]:
            location = f"（`{item['path']}`" if item.get("path") else "（"
            if item.get("line"):
                location += f"，第 {item['line']} 行"
            location += "）" if location != "（" else ""
            level = "错误" if item["level"] == "error" else "警告"
            lines.append(f"- [{level}] `{item['code']}` {item['message']} {location}".rstrip())
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 警告不会改变退出码，但必须在正式交付前人工判断。",
            "- 待核字段不是自动失败；它们代表尚未完成的人工作业范围。",
            "- 验证通过只说明结构、哈希和可机械检查项通过，不代表事实、OCR、法条或法律结论已经实质复核。",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(case_dir: Path, report: dict[str, Any], json_output: Path | None, markdown_output: Path | None) -> tuple[Path, Path]:
    audit = case_dir / "99_audit"
    audit.mkdir(parents=True, exist_ok=True)
    json_path = (json_output or audit / "validation-report.json").expanduser()
    md_path = (markdown_output or audit / "validation-report.md").expanduser()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path.resolve(), md_path.resolve()


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="legal-case-validate-selftest-") as temp_name:
        case_dir = Path(temp_name) / "workspace" / "cases" / "CASE-2099-001-selftest"
        for subdir in CASE_SUBDIRS:
            (case_dir / subdir).mkdir(parents=True)
        source = case_dir / "00_source" / "synthetic.txt"
        source.write_text("synthetic validation self-test\n", encoding="utf-8")
        digest = sha256_file(source)
        manifest = {
            "files": [
                {
                    "material_id": "DOC-001",
                    "stored_relative_path": "00_source/synthetic.txt",
                    "sha256": digest,
                    "transformations": [],
                }
            ],
            "failures": [],
        }
        (case_dir / "01_manifest" / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        for relative in EXPECTED_WORK_FILES:
            path = case_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("# 自测占位\n", encoding="utf-8")
        (case_dir / "03_evidence" / "FACT_MATRIX.md").write_text(
            "# 事实矩阵\n\nDOC-001，PDF 第 1 页：synthetic。\n", encoding="utf-8"
        )
        passed = validate(case_dir, enforce_workspace=False)
        assert passed["status"] == "pass"
        assert passed["counts"]["hash_verified"] == 1
        json_path, md_path = write_reports(case_dir, passed, None, None)
        assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "pass"
        assert "验证状态：通过" in md_path.read_text(encoding="utf-8")
        source.write_text("mutated synthetic self-test\n", encoding="utf-8")
        failed = validate(case_dir, enforce_workspace=False)
        assert failed["status"] == "fail"
        assert any(item["code"] == "sha256_mismatch" for item in failed["issues"])
    print(json.dumps({"self_test": "passed", "script": Path(__file__).name, "version": SCRIPT_VERSION}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验证案件目录、manifest、源文件哈希和材料引用")
    parser.add_argument("--case-dir", type=Path, help="当前工作区 cases/ 下的案件目录")
    parser.add_argument("--json-output", type=Path, help="可选的 JSON 报告路径；默认写入 99_audit/")
    parser.add_argument("--markdown-output", type=Path, help="可选的 Markdown 报告路径；默认写入 99_audit/")
    parser.add_argument("--self-test", action="store_true", help="只在临时目录运行程序生成的虚拟文件自测")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()
    if args.case_dir is None:
        print("错误：除 --self-test 外，必须提供 --case-dir", file=sys.stderr)
        return 2
    try:
        case_dir = args.case_dir.expanduser().resolve()
        report = validate(case_dir)
        json_path, md_path = write_reports(case_dir, report, args.json_output, args.markdown_output)
    except ValidationUsageError as exc:
        print(f"验证被安全停止：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"无法写入验证报告：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "errors": report["counts"]["errors"],
                "warnings": report["counts"]["warnings"],
                "json_report": str(json_path),
                "markdown_report": str(md_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
