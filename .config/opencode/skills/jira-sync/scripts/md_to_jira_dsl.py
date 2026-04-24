#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from jira_dsl_lib import (
    extract_force_from_keys,
    normalize_issue_dsl,
    parse_frontmatter_yaml,
    strip_force_suffix,
    split_frontmatter,
    write_json,
)

SECTION_NAMES = {
    "Parent",
    "Description",
    "ToDo",
    "AcceptanceCriteria",
    "연관이슈",
}

METADATA_LABELS = {
    "Jira": "key",
    "URL": "url",
    "유형": "issue_type",
    "상태": "status",
    "우선순위": "priority",
    "담당자": "assignee",
    "보고자": "reporter",
    "생성일": "created_at",
    "종료일": "end_date",
    "End Date": "end_date",
    "마감일": "due_date",
    "Due Date": "due_date",
    "Parent": "parent_key",
}


def collapse_whitespace(value: str | None) -> str | None:
    """연속 공백을 하나로 합치고 양끝 공백을 제거한다."""
    if value is None:
        return None
    collapsed = re.sub(r"\s+", " ", str(value)).strip()
    return collapsed or None


def parse_title_line(line: str) -> str:
    """마크다운 제목 줄에서 summary만 파싱한다."""
    match = re.match(r"^#\s+(.*?)(?:\s+\[[A-Z]+-\d+\])?\s*$", line.strip())
    if not match:
        return ""
    return match.group(1).strip()


def parse_user_label(value: Any) -> dict[str, str | None] | None:
    """사용자 표시 텍스트에서 username과 display_name을 파싱한다."""
    if value is None:
        return None
    text = collapse_whitespace(str(value))
    if not text or text == "-":
        return None
    match = re.match(r"^([^\s()\[\]|]+)", text)
    username = match.group(1) if match else None
    return {"username": username, "display_name": text}


def extract_issue_key(value: Any) -> str | None:
    """문자열에서 Jira 이슈 키를 추출한다."""
    if value is None:
        return None
    match = re.search(r"\b([A-Z]+-\d+)\b", str(value))
    return match.group(1) if match else None


def strip_markdown_link(value: str) -> str:
    """[label](url) 형태면 label만 반환한다."""
    match = re.match(r"^\[([^\]]+)\]\([^)]*\)$", value.strip())
    if match:
        return match.group(1).strip()
    return value.strip()


def strip_description_force_marker(text: str) -> tuple[str, bool]:
    """본문 상단의 ``((force))`` 마커를 제거하고 force 여부를 반환한다."""
    stripped = text.lstrip()
    if not stripped.startswith("((force))"):
        return text, False

    remainder = stripped[len("((force))") :]
    if remainder.startswith("\r\n"):
        remainder = remainder[2:]
    elif remainder.startswith("\n"):
        remainder = remainder[1:]
    return remainder.lstrip("\n"), True


def build_empty_dsl(issue_key: str | None, summary: str = "") -> dict[str, Any]:
    """최소한의 빈 DSL 객체를 생성한다."""
    return normalize_issue_dsl(
        {
            "version": 1,
            "issue": {
                "key": issue_key,
                "url": None,
                "summary": summary,
                "epic_name": None,
                "issue_type": None,
                "status": None,
                "priority": None,
                "created_at": None,
                "assignee": None,
                "reporter": None,
                "parent_key": None,
                "end_date": None,
                "due_date": None,
                "labels": [],
                "components": [],
                "links": [],
            },
            "description_markdown": "",
            "md_force_fields": [],
            "checklists": {"todo": [], "acceptance_criteria": []},
        }
    )


def normalize_fm_checklist(items: Any) -> list[dict[str, Any]]:
    """frontmatter에서 파싱된 체크리스트 항목을 DSL 형식으로 변환한다."""
    if not items or not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": item.get("id"),
                "name": collapse_whitespace(item.get("name")) or "",
                "checked": bool(item.get("checked", False)),
                "completed_date": collapse_whitespace(item.get("completed_date")),
                "linked_issue_key": collapse_whitespace(item.get("linked_issue_key")),
                "assignee_username": collapse_whitespace(item.get("assignee_username")),
                "status_name": collapse_whitespace(item.get("status_name")),
                "status_id": collapse_whitespace(item.get("status_id")),
            }
        )
    return [item for item in result if item["name"]]


def normalize_fm_string_list(items: Any) -> list[str]:
    """frontmatter에서 파싱된 문자열 배열을 정규화한다."""
    if not items or not isinstance(items, list):
        return []
    result = []
    for item in items:
        text = collapse_whitespace(str(item)) if item is not None else None
        if text:
            result.append(text)
    return result


def normalize_fm_links(items: Any) -> list[dict[str, str | None]]:
    """frontmatter에서 파싱된 연관이슈 링크를 DSL 형식으로 변환한다."""
    if not items or not isinstance(items, list):
        return []
    return [
        {
            "direction": collapse_whitespace(item.get("direction")),
            "relationship": collapse_whitespace(item.get("relationship")),
            "key": collapse_whitespace(item.get("key")),
            "summary": collapse_whitespace(item.get("summary")),
        }
        for item in items
        if isinstance(item, dict)
    ]


def parse_frontmatter_markdown(path: Path) -> dict[str, Any]:
    """frontmatter 기반 문서를 기존 방식대로 파싱한다."""
    text = path.read_text(encoding="utf-8")
    fm_text, body = split_frontmatter(text)
    if fm_text is None:
        raise ValueError("frontmatter is required")

    fm_raw = parse_frontmatter_yaml(fm_text)
    fm, fm_forced = extract_force_from_keys(fm_raw)

    issue_meta_raw = fm.get("issue") if isinstance(fm.get("issue"), dict) else {}
    if issue_meta_raw:
        issue_meta, issue_forced = extract_force_from_keys(issue_meta_raw)
        fm["issue"] = issue_meta
    else:
        issue_meta = {}
        issue_forced: set[str] = set()

    all_key_forced = fm_forced | issue_forced
    forced_fields: set[str] = set()

    def fm_issue_value(field_name: str, *aliases: str) -> Any:
        for key in (field_name, *aliases):
            if key in issue_meta and issue_meta.get(key) not in (None, ""):
                return issue_meta.get(key)
        for key in (field_name, *aliases):
            if fm.get(key) not in (None, ""):
                return fm.get(key)
        return None

    def fm_issue_value_with_force(field_name: str, *aliases: str) -> Any:
        value = fm_issue_value(field_name, *aliases)
        for key in (field_name, *aliases):
            if key in all_key_forced:
                forced_fields.add(field_name)
                break
        return value

    body_lines = body.splitlines()

    # frontmatter의 summary/title을 우선 사용한다.
    # frontmatter에 title이 없을 때만 본문 첫 H1을 summary로 사용한다.
    summary_value = fm_issue_value_with_force("summary", "title")
    summary = None
    desc_start = 0
    if not collapse_whitespace(summary_value):
        for i, line in enumerate(body_lines):
            stripped = line.strip()
            if not stripped:
                continue
            parsed_summary = parse_title_line(stripped)
            if parsed_summary:
                summary = parsed_summary
                desc_start = i + 1
            break

    description, description_forced = strip_description_force_marker(
        "\n".join(body_lines[desc_start:]).strip()
    )
    if description_forced:
        forced_fields.add("description_markdown")
    fm_created_at = fm_issue_value_with_force("created_at")
    if fm_created_at is not None:
        fm_created_at = str(fm_created_at)
    fm_end_date = fm_issue_value_with_force("end_date")
    if fm_end_date is not None:
        fm_end_date = str(fm_end_date)
    fm_due_date = fm_issue_value_with_force("due_date")
    if fm_due_date is not None:
        fm_due_date = str(fm_due_date)

    issue = {
        "key": collapse_whitespace(
            fm_issue_value_with_force("key", "jira", "IssueKey")
        ),
        "url": collapse_whitespace(
            fm_issue_value_with_force("url", "jira_url", "issue_url")
        ),
        "summary": summary or collapse_whitespace(summary_value) or "",
        "epic_name": collapse_whitespace(fm_issue_value_with_force("epic_name")),
        "issue_type": collapse_whitespace(fm_issue_value_with_force("issue_type")),
        "status": collapse_whitespace(fm_issue_value_with_force("status")),
        "priority": collapse_whitespace(fm_issue_value_with_force("priority")),
        "assignee": parse_user_label(fm_issue_value_with_force("assignee")),
        "reporter": parse_user_label(fm_issue_value_with_force("reporter")),
        "created_at": collapse_whitespace(fm_created_at),
        "parent_key": collapse_whitespace(fm_issue_value_with_force("parent_key")),
        "end_date": collapse_whitespace(fm_end_date),
        "due_date": collapse_whitespace(fm_due_date),
        "labels": normalize_fm_string_list(fm_issue_value_with_force("labels")),
        "components": normalize_fm_string_list(fm_issue_value_with_force("components")),
        "links": normalize_fm_links(fm_issue_value_with_force("links")),
    }

    if issue.get("issue_type") == "Epic" and not issue.get("epic_name"):
        issue["epic_name"] = issue["summary"] or None

    issue_dsl = {
        "version": 1,
        "issue": issue,
        "description_markdown": description,
        "md_force_fields": sorted(forced_fields),
        "checklists": {
            "todo": normalize_fm_checklist(fm.get("todo")),
            "acceptance_criteria": normalize_fm_checklist(
                fm.get("acceptance_criteria")
            ),
        },
    }
    return normalize_issue_dsl(issue_dsl)


def find_title(lines: list[str]) -> tuple[int | None, str | None]:
    """문서에서 첫 h1 제목과 위치를 찾는다."""
    for index, line in enumerate(lines):
        summary = parse_title_line(line)
        if summary:
            return index, summary
    return None, None


def parse_metadata_table(
    lines: list[str], start_index: int
) -> tuple[dict[str, Any], set[str], int]:
    """제목 직후의 메타데이터 테이블을 파싱한다."""
    issue: dict[str, Any] = {}
    forced_fields: set[str] = set()
    index = start_index
    while index < len(lines) and not lines[index].strip():
        index += 1

    if index + 1 >= len(lines):
        return issue, forced_fields, index
    if not re.match(r"^\|\s*항목\s*\|\s*내용\s*\|\s*$", lines[index].strip()):
        return issue, forced_fields, index
    if not re.match(r"^\|[\-\s|:]+\|\s*$", lines[index + 1].strip()):
        return issue, forced_fields, index

    index += 2
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped.startswith("|"):
            break
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 2:
            label = cells[0]
            label, is_forced = strip_force_suffix(label)
            label = label.strip() if isinstance(label, str) else label
            raw_value = " | ".join(cells[1:]).strip()
            raw_value = strip_markdown_link(str(raw_value))
            field_name = METADATA_LABELS.get(label)
            if field_name == "key":
                issue["key"] = extract_issue_key(raw_value)
            elif field_name in {"assignee", "reporter"}:
                issue[field_name] = parse_user_label(raw_value)
            elif field_name:
                issue[field_name] = collapse_whitespace(raw_value)
            if field_name and is_forced:
                forced_fields.add(field_name)
        index += 1

    return issue, forced_fields, index


def parse_sections(lines: list[str], start_index: int) -> dict[str, list[str]]:
    """문서의 h2 섹션을 수집한다."""
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if current_section is not None:
            sections[current_section] = buffer[:]
        buffer = []

    for line in lines[start_index:]:
        match = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if match:
            heading = collapse_whitespace(match.group(1))
            if heading in SECTION_NAMES:
                flush()
                current_section = heading
                continue
        if current_section is not None:
            buffer.append(line)

    flush()
    return sections


def parse_parent_section(lines: list[str]) -> str | None:
    """Parent 섹션에서 상위 이슈 키를 추출한다."""
    for line in lines:
        issue_key = extract_issue_key(line)
        if issue_key:
            return issue_key
    return None


def parse_related_issue_line(line: str) -> dict[str, str | None] | None:
    """연관이슈 섹션 한 줄을 DSL 링크 객체로 변환한다."""
    stripped = line.strip()
    if not stripped.startswith("-"):
        return None
    parts = [part.strip() for part in stripped[1:].split("|")]
    if len(parts) < 3:
        return None
    direction = collapse_whitespace(parts[0])
    relationship = collapse_whitespace(parts[1])
    key = extract_issue_key(parts[2])
    summary = collapse_whitespace(" | ".join(parts[3:])) if len(parts) > 3 else None
    return {
        "direction": direction,
        "relationship": relationship,
        "key": key,
        "summary": summary,
    }


def parse_related_issues(lines: list[str]) -> list[dict[str, str | None]]:
    """연관이슈 섹션을 DSL 링크 배열로 변환한다."""
    parsed = []
    for line in lines:
        link = parse_related_issue_line(line)
        if link and any(link.values()):
            parsed.append(link)
    return parsed


def parse_checklist_line(line: str) -> dict[str, Any] | None:
    """[ ]*(id=1)... 형식의 체크리스트 한 줄을 파싱한다."""
    match = re.match(
        r"^\[(?P<checked>[Xx ])\]\*?(?P<attrs>(?:\([^)]*\))*)(?:\s*)(?P<name>.+?)\s*$",
        line.strip(),
    )
    if not match:
        return None

    item: dict[str, Any] = {
        "id": None,
        "name": collapse_whitespace(match.group("name")) or "",
        "checked": match.group("checked").upper() == "X",
        "completed_date": None,
        "linked_issue_key": None,
        "assignee_username": None,
        "status_name": None,
        "status_id": None,
    }
    for key, value in re.findall(r"\(([^=()]+)=([^)]*)\)", match.group("attrs")):
        attr_key = key.strip()
        attr_value = collapse_whitespace(value)
        if attr_key == "id" and attr_value:
            try:
                item["id"] = int(attr_value)
            except ValueError:
                pass
        elif attr_key == "status":
            item["status_id"] = attr_value
        elif attr_key == "assignee":
            item["assignee_username"] = attr_value
        elif attr_key == "linkedIssueKey":
            item["linked_issue_key"] = attr_value
        elif attr_key == "completedDate":
            item["completed_date"] = attr_value
    return item if item["name"] else None


def parse_checklist_section(lines: list[str]) -> list[dict[str, Any]]:
    """체크리스트 섹션을 DSL 배열로 변환한다."""
    items = []
    for line in lines:
        item = parse_checklist_line(line)
        if item:
            items.append(item)
    return items


def parse_section_markdown(path: Path) -> dict[str, Any]:
    """현행 KCDL 문서 템플릿(표 + 섹션) 기반 마크다운을 파싱한다."""
    lines = path.read_text(encoding="utf-8").splitlines()
    title_index, summary = find_title(lines)
    if title_index is None or summary is None:
        return build_empty_dsl(None, "")

    issue_from_table, forced_fields, next_index = parse_metadata_table(
        lines, title_index + 1
    )
    sections = parse_sections(lines, next_index)

    issue = {
        "key": issue_from_table.get("key"),
        "summary": summary,
        "epic_name": None,
        "issue_type": issue_from_table.get("issue_type"),
        "status": issue_from_table.get("status"),
        "priority": issue_from_table.get("priority"),
        "assignee": issue_from_table.get("assignee"),
        "reporter": issue_from_table.get("reporter"),
        "created_at": issue_from_table.get("created_at"),
        "parent_key": parse_parent_section(sections.get("Parent", []))
        or issue_from_table.get("parent_key"),
        "end_date": issue_from_table.get("end_date"),
        "due_date": issue_from_table.get("due_date"),
        "labels": [],
        "components": [],
        "links": parse_related_issues(sections.get("연관이슈", [])),
    }

    if issue.get("issue_type") == "Epic":
        issue["epic_name"] = summary

    description_markdown, description_forced = strip_description_force_marker(
        "\n".join(sections.get("Description", [])).strip()
    )
    if description_forced:
        forced_fields.add("description_markdown")
    issue_dsl = {
        "version": 1,
        "issue": issue,
        "description_markdown": description_markdown,
        "md_force_fields": sorted(forced_fields),
        "checklists": {
            "todo": parse_checklist_section(sections.get("ToDo", [])),
            "acceptance_criteria": parse_checklist_section(
                sections.get("AcceptanceCriteria", [])
            ),
        },
    }
    return normalize_issue_dsl(issue_dsl)


def parse_markdown(path: Path) -> dict[str, Any]:
    """마크다운 파일을 읽어 Jira DSL 객체로 변환한다."""
    text = path.read_text(encoding="utf-8")
    fm_text, _body = split_frontmatter(text)
    if fm_text is not None:
        return parse_frontmatter_markdown(path)
    return parse_section_markdown(path)


def main() -> None:
    """CLI 진입점. 마크다운 파일을 Jira DSL JSON으로 변환한다."""
    parser = argparse.ArgumentParser(
        description="Convert markdown issue doc to Jira DSL"
    )
    parser.add_argument("--input", help="Markdown file path")
    parser.add_argument("--output", required=True, help="Output Jira DSL JSON path")
    parser.add_argument(
        "--issue-key",
        help="Issue key used when bootstrapping a missing markdown file",
    )
    parser.add_argument(
        "--summary",
        default="",
        help="Optional summary used when bootstrapping a missing markdown file",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Emit a minimal Jira DSL instead of failing when input file is missing",
    )
    args = parser.parse_args()

    if args.input:
        input_path = Path(args.input)
        if input_path.exists():
            issue_dsl = parse_markdown(input_path)
            write_json(args.output, issue_dsl)
            print(args.output)
            return
        if not args.allow_missing:
            raise SystemExit(f"Markdown file not found: {input_path}")

    if not args.allow_missing:
        raise SystemExit("--input is required unless --allow-missing is used")

    issue_dsl = build_empty_dsl(args.issue_key, args.summary)
    write_json(args.output, issue_dsl)
    print(args.output)


if __name__ == "__main__":
    main()
