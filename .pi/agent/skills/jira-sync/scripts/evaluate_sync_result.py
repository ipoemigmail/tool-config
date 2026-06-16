#!/usr/bin/env python3
"""jira-sync 실행 결과를 평가한다.

sync 실행 후 생성된 MD 파일과 merged DSL을 비교하여
템플릿 준수, 데이터 정합성, 포맷 규칙 위반을 검출한다.

실행:
  # jira_sync_summary.json 기반 전체 평가
  python3 .agents/skills/jira-sync/scripts/evaluate_sync_result.py \
    --summary /tmp/jira_sync_summary.json

  # 개별 MD 파일 + DSL 평가
  python3 .agents/skills/jira-sync/scripts/evaluate_sync_result.py \
    --md /path/to/task.md --dsl /tmp/task.merged.dsl.json

출력: JSON 리포트 → stdout 또는 --output 파일
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from jira_dsl_lib import (
    load_issue_templates,
    load_json,
    normalize_issue_dsl,
    parse_frontmatter_yaml,
    split_frontmatter,
)


# =====================================================================
# Evaluation checks
# =====================================================================


def check_no_jira_markers_in_md(body: str) -> list[dict]:
    """MD 본문에 Jira 마커(☑️, h3. ☑️)가 남아있으면 위반."""
    issues = []
    for i, line in enumerate(body.split("\n"), 1):
        if "☑️" in line:
            issues.append(
                {
                    "check": "no_jira_markers",
                    "severity": "error",
                    "line": i,
                    "message": f"Jira 마커 ☑️가 MD에 남아있음: {line.strip()[:80]}",
                }
            )
    return issues


def check_heading_level(body: str) -> list[dict]:
    """섹션 헤딩이 ## (h2)이 아닌 레벨을 사용하면 위반."""
    issues = []
    in_code_block = False
    for i, line in enumerate(body.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        match = re.match(r"^(#{1,6})\s+", line)
        if match:
            level = len(match.group(1))
            if level != 2:
                issues.append(
                    {
                        "check": "heading_level",
                        "severity": "warning",
                        "line": i,
                        "message": f"h{level} 헤딩 사용 (h2 권장): {line.strip()[:80]}",
                    }
                )
    return issues


def check_bullet_list_format(body: str) -> list[dict]:
    """섹션 본문이 bullet list가 아닌 평문이면 경고."""
    issues = []
    in_code_block = False
    current_section: str | None = None
    section_has_content = False
    section_has_bullet = False
    section_has_plain = False
    section_start_line = 0

    for i, line in enumerate(body.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if re.match(r"^##\s+", line):
            if current_section and section_has_plain and not section_has_bullet:
                issues.append(
                    {
                        "check": "bullet_list_format",
                        "severity": "warning",
                        "line": section_start_line,
                        "message": f"'{current_section}' 섹션 본문이 bullet list가 아닌 평문",
                    }
                )
            current_section = stripped.replace("## ", "")
            section_has_content = False
            section_has_bullet = False
            section_has_plain = False
            section_start_line = i
            continue

        if current_section and stripped:
            section_has_content = True
            if re.match(r"^[-*]\s", stripped) or re.match(r"^\d+\.\s", stripped):
                section_has_bullet = True
            elif re.match(r"^\s+[-*]\s", line):
                section_has_bullet = True
            elif not stripped.startswith("|") and not stripped.startswith("```"):
                section_has_plain = True

    if current_section and section_has_plain and not section_has_bullet:
        issues.append(
            {
                "check": "bullet_list_format",
                "severity": "warning",
                "line": section_start_line,
                "message": f"'{current_section}' 섹션 본문이 bullet list가 아닌 평문",
            }
        )
    return issues


def check_template_sections(body: str, issue_type: str | None) -> list[dict]:
    """필수 섹션이 존재하는지 확인. load_issue_templates()에서 required_sections를 가져온다."""
    if not issue_type:
        return []
    templates = load_issue_templates()
    template = templates.get(issue_type)
    if not template:
        return []

    required = template.get("required_sections", [])
    if not required:
        return []

    found_sections = set()
    for line in body.split("\n"):
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            found_sections.add(match.group(1).strip())

    issues = []
    for section in required:
        if section not in found_sections:
            issues.append(
                {
                    "check": "template_sections",
                    "severity": "warning",
                    "line": 0,
                    "message": f"필수 섹션 '{section}'이 누락됨 (issue_type={issue_type})",
                }
            )
    return issues


def check_section_order(body: str, issue_type: str | None) -> list[dict]:
    """섹션 순서가 템플릿 순서와 일치하는지 확인."""
    if not issue_type:
        return []
    templates = load_issue_templates()
    template = templates.get(issue_type)
    if not template:
        return []

    template_order = template["section_order"]
    found_sections = []
    for line in body.split("\n"):
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            found_sections.append(match.group(1).strip())

    # 템플릿에 있는 섹션만 필터링하여 순서 비교
    filtered = [s for s in found_sections if s in template_order]
    expected = [s for s in template_order if s in filtered]

    if filtered != expected:
        return [
            {
                "check": "section_order",
                "severity": "info",
                "line": 0,
                "message": f"섹션 순서가 템플릿과 다름: {filtered} (기대: {expected})",
            }
        ]
    return []


def check_frontmatter_vs_dsl(frontmatter: dict, issue_dsl: dict) -> list[dict]:
    """frontmatter 메타데이터가 merged DSL과 일치하는지 확인."""
    issues = []
    issue = issue_dsl.get("issue") or {}

    field_map = {
        "title": ("summary", issue.get("summary")),
        "jira": ("key", issue.get("key")),
        "issue_type": ("issue_type", issue.get("issue_type")),
        "status": ("status", issue.get("status")),
        "priority": ("priority", issue.get("priority")),
        "parent_key": ("parent_key", issue.get("parent_key")),
        "due_date": ("due_date", issue.get("due_date")),
        "links": ("links", issue.get("links")),
    }

    for fm_field, (dsl_field, expected) in field_map.items():
        actual = frontmatter.get(fm_field)
        if expected is None:
            continue
        if isinstance(actual, (list, dict)) or isinstance(expected, (list, dict)):
            if actual in (None, [], {}):
                continue
            if actual != expected:
                issues.append(
                    {
                        "check": "frontmatter_vs_dsl",
                        "severity": "error",
                        "line": 0,
                        "message": f"frontmatter '{fm_field}'={actual} != DSL '{dsl_field}'={expected}",
                    }
                )
            continue
        actual_str = str(actual).strip() if actual else None
        expected_str = str(expected).strip()
        if actual_str and expected_str and actual_str != expected_str:
            # null/None 은 무시
            if actual_str.lower() in ("null", "none", "-", '"-"'):
                continue
            issues.append(
                {
                    "check": "frontmatter_vs_dsl",
                    "severity": "error",
                    "line": 0,
                    "message": f"frontmatter '{fm_field}'={actual_str} != DSL '{dsl_field}'={expected_str}",
                }
            )
    return issues


def check_description_vs_dsl(body: str, issue_dsl: dict) -> list[dict]:
    """MD 본문의 내용이 DSL description과 대략 일치하는지 확인."""
    dsl_desc = (issue_dsl.get("description_markdown") or "").strip()
    body_stripped = body.strip()

    if not dsl_desc and not body_stripped:
        return []
    if not dsl_desc or not body_stripped:
        return []

    # 핵심 단어 기반 비교: DSL의 첫 번째 섹션 제목이 MD에 있는지
    dsl_headings = re.findall(r"^##\s+(.+)$", dsl_desc, re.MULTILINE)
    body_headings = re.findall(r"^##\s+(.+)$", body_stripped, re.MULTILINE)

    if dsl_headings and not body_headings:
        return [
            {
                "check": "description_vs_dsl",
                "severity": "warning",
                "line": 0,
                "message": "DSL에 섹션 헤딩이 있으나 MD 본문에 없음",
            }
        ]
    return []


# =====================================================================
# Evaluate single file
# =====================================================================


def evaluate_md_file(md_path: str, dsl_path: str | None = None) -> dict:
    """MD 파일을 평가하고 결과를 반환한다."""
    md_text = Path(md_path).read_text(encoding="utf-8")
    fm_text, body = split_frontmatter(md_text)
    frontmatter = parse_frontmatter_yaml(fm_text) if fm_text else {}
    issue_type = frontmatter.get("issue_type")

    all_issues: list[dict] = []
    all_issues.extend(check_no_jira_markers_in_md(body))
    all_issues.extend(check_heading_level(body))
    all_issues.extend(check_bullet_list_format(body))
    all_issues.extend(check_template_sections(body, issue_type))
    all_issues.extend(check_section_order(body, issue_type))

    if dsl_path:
        issue_dsl = normalize_issue_dsl(load_json(dsl_path))
        all_issues.extend(check_frontmatter_vs_dsl(frontmatter, issue_dsl))
        all_issues.extend(check_description_vs_dsl(body, issue_dsl))

    errors = [i for i in all_issues if i["severity"] == "error"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]
    infos = [i for i in all_issues if i["severity"] == "info"]

    return {
        "md_path": md_path,
        "dsl_path": dsl_path,
        "issue_type": issue_type,
        "result": "PASS" if not errors else "FAIL",
        "errors": len(errors),
        "warnings": len(warnings),
        "infos": len(infos),
        "issues": all_issues,
    }


# =====================================================================
# Evaluate from summary
# =====================================================================


def evaluate_from_summary(summary_path: str) -> dict:
    """jira_sync_summary.json을 읽고 모든 이슈를 평가한다."""
    summary = load_json(summary_path)
    results = []
    total_errors = 0
    total_warnings = 0

    for item in summary.get("issues", []):
        md_path = item.get("md_path") or item.get("preview_path")
        dsl_path = item.get("merged_dsl_path")

        if not md_path or not Path(md_path).exists():
            # preview 파일로 대체
            md_path = item.get("preview_path")
        if not md_path or not Path(md_path).exists():
            continue

        result = evaluate_md_file(
            md_path,
            dsl_path if dsl_path and Path(dsl_path).exists() else None,
        )
        result["key"] = item.get("key")
        result["summary"] = item.get("summary")
        results.append(result)
        total_errors += result["errors"]
        total_warnings += result["warnings"]

    return {
        "summary_path": summary_path,
        "total_issues_evaluated": len(results),
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "result": "PASS" if total_errors == 0 else "FAIL",
        "evaluations": results,
    }


# =====================================================================
# CLI
# =====================================================================


def print_report(report: dict) -> None:
    """평가 결과를 콘솔에 출력한다."""
    if "evaluations" in report:
        # summary mode
        total = report["total_issues_evaluated"]
        errors = report["total_errors"]
        warnings = report["total_warnings"]
        result = report["result"]
        print(f"=== jira-sync evaluation ===")
        print(f"결과: {result} ({total}건 평가, {errors} errors, {warnings} warnings)")
        print()
        for ev in report["evaluations"]:
            if not ev["issues"]:
                continue
            key = ev.get("key") or "-"
            print(f"[{key}] {ev.get('summary', '')}")
            for issue in ev["issues"]:
                severity = issue["severity"].upper()
                line = f"L{issue['line']}" if issue["line"] else ""
                print(f"  {severity:7s} {line:5s} {issue['message']}")
            print()
    else:
        # single file mode
        result = report["result"]
        print(f"=== {report['md_path']} ===")
        print(
            f"결과: {result} ({report['errors']} errors, {report['warnings']} warnings)"
        )
        for issue in report["issues"]:
            severity = issue["severity"].upper()
            line = f"L{issue['line']}" if issue["line"] else ""
            print(f"  {severity:7s} {line:5s} {issue['message']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="jira-sync 실행 결과 평가")
    parser.add_argument("--summary", help="jira_sync_summary.json 경로")
    parser.add_argument("--md", help="개별 MD 파일 경로")
    parser.add_argument("--dsl", help="개별 merged DSL 파일 경로")
    parser.add_argument("--output", help="JSON 리포트 출력 경로")
    args = parser.parse_args()

    if args.summary:
        report = evaluate_from_summary(args.summary)
    elif args.md:
        report = evaluate_md_file(args.md, args.dsl)
    else:
        parser.error("--summary 또는 --md 중 하나를 지정하세요")
        return

    print_report(report)

    if args.output:
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n리포트: {args.output}")

    sys.exit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
