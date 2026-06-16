#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path

from jira_dsl_lib import (
    emit_frontmatter,
    load_issue_templates,
    load_json,
    normalize_issue_dsl,
    render_user_label,
)


def render_links_frontmatter(links: list[dict]) -> list[dict]:
    """연관이슈를 frontmatter용 리스트로 정규화한다."""
    result: list[dict] = []
    for link in links:
        result.append(
            {
                "direction": link.get("direction"),
                "relationship": link.get("relationship"),
                "key": link.get("key"),
                "summary": link.get("summary"),
            }
        )
    return result


def render_checklist_frontmatter(items: list[dict]) -> list[dict]:
    """체크리스트를 frontmatter용 리스트로 정규화한다."""
    result: list[dict] = []
    for item in items:
        result.append(
            {
                "id": item.get("id"),
                "name": item.get("name") or "",
                "checked": bool(item.get("checked", False)),
                "completed_date": item.get("completed_date"),
                "linked_issue_key": item.get("linked_issue_key"),
                "assignee_username": item.get("assignee_username"),
                "status_name": item.get("status_name"),
                "status_id": item.get("status_id"),
            }
        )
    return result


def render_forceable_value(
    value: str | None, field_name: str, md_force_fields: set[str]
) -> str | None:
    """메타데이터 값을 그대로 반환한다. `((force))`는 머지 우선순위 표시용으로만 사용한다."""
    if value in (None, "", "-"):
        return value
    return value


def render_frontmatter(issue_dsl: dict) -> str:
    """스킬 규격에 맞는 YAML frontmatter를 렌더링한다."""
    issue = issue_dsl["issue"]
    md_force_fields = set(issue_dsl.get("md_force_fields") or [])
    frontmatter: dict = {
        "title": render_forceable_value(
            issue.get("summary") or "Untitled", "summary", md_force_fields
        ),
        "jira": render_forceable_value(issue.get("key"), "key", md_force_fields),
        "url": render_forceable_value(issue.get("url"), "url", md_force_fields),
        "issue_type": render_forceable_value(
            issue.get("issue_type"), "issue_type", md_force_fields
        ),
        "status": render_forceable_value(
            issue.get("status"), "status", md_force_fields
        ),
        "priority": render_forceable_value(
            issue.get("priority"), "priority", md_force_fields
        ),
        "assignee": render_forceable_value(
            render_user_label(issue.get("assignee")), "assignee", md_force_fields
        ),
        "reporter": render_forceable_value(
            render_user_label(issue.get("reporter")), "reporter", md_force_fields
        ),
        "created_at": render_forceable_value(
            issue.get("created_at"), "created_at", md_force_fields
        ),
        "parent_key": render_forceable_value(
            issue.get("parent_key"), "parent_key", md_force_fields
        ),
        "end_date": render_forceable_value(
            issue.get("end_date"), "end_date", md_force_fields
        ),
        "due_date": render_forceable_value(
            issue.get("due_date"), "due_date", md_force_fields
        ),
        "labels": issue.get("labels") or [],
        "components": issue.get("components") or [],
        "links": render_links_frontmatter(issue.get("links") or []),
        "todo": render_checklist_frontmatter(issue_dsl["checklists"]["todo"]),
        "acceptance_criteria": render_checklist_frontmatter(
            issue_dsl["checklists"]["acceptance_criteria"]
        ),
    }
    return emit_frontmatter(frontmatter)


def _build_template_skeleton(issue_type: str | None) -> str:
    """issue_type에 맞는 빈 템플릿 스켈레톤을 반환한다.

    load_issue_templates()에서 section_order를 가져와 중앙 정의를 단일 소스로 사용한다.
    """
    if not issue_type:
        return ""
    templates = load_issue_templates()
    template = templates.get(issue_type)
    if not template:
        return ""
    sections = [f"## {name}" for name in template["section_order"]]
    return "\n\n".join(sections)


def render_document(issue_dsl: dict, *, is_new_file: bool = False) -> str:
    """DSL 객체를 frontmatter + description-only 마크다운으로 렌더링한다."""
    description = issue_dsl["description_markdown"].strip()
    parts = [render_frontmatter(issue_dsl)]
    if description:
        parts.extend(["", description])
    elif is_new_file:
        issue_type = (issue_dsl.get("issue") or {}).get("issue_type")
        skeleton = _build_template_skeleton(issue_type)
        if skeleton:
            parts.extend(["", skeleton])
    return "\n".join(parts).rstrip() + "\n"


def main() -> None:
    """CLI 진입점. Jira DSL JSON을 마크다운 파일로 변환한다."""
    parser = argparse.ArgumentParser(description="Render markdown from Jira DSL")
    parser.add_argument("--input", required=True, help="Merged Jira DSL JSON path")
    parser.add_argument("--output", required=True, help="Markdown file path")
    args = parser.parse_args()

    output_path = Path(args.output)
    is_new_file = not output_path.exists()
    issue_dsl = normalize_issue_dsl(load_json(args.input))
    document = render_document(issue_dsl, is_new_file=is_new_file)
    output_path.write_text(document, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
