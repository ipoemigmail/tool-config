#!/usr/bin/env python3

from __future__ import annotations

import argparse

from jira_dsl_lib import (
    collapse_whitespace,
    has_meaningful_md_content,
    merge_checklist_section,
    merge_text,
    normalize_issue_dsl,
    render_user_label,
    write_json,
    load_json,
)


def _merge_links(md_links: list[dict], jira_links: list[dict]) -> list[dict]:
    """links 머지: Jira 기준으로 취하고, MD에만 있는 링크(key 기준)는 뒤에 추가한다.

    같은 `key`는 Jira 우선. `key`가 없는 MD 링크는 무시한다.
    """

    def norm_key(item: dict) -> str:
        return (collapse_whitespace(item.get("key")) or "").casefold()

    jira_keys = {norm_key(l) for l in jira_links if norm_key(l)}
    merged = list(jira_links)
    seen_md: set[str] = set()
    for md_link in md_links:
        key = norm_key(md_link)
        if not key or key in jira_keys or key in seen_md:
            continue
        merged.append(md_link)
        seen_md.add(key)
    return merged


def merge_issue(md_dsl: dict, jira_dsl: dict, prefer_jira_bootstrap: bool) -> dict:
    """MD와 Jira의 이슈 메타데이터를 우선순위 규칙에 따라 머지한다."""
    md_issue = md_dsl["issue"]
    jira_issue = jira_dsl["issue"]
    md_force_fields = set(md_dsl.get("md_force_fields") or [])

    def merge_issue_text(field_name: str, *, prefer_md: bool = False) -> str | None:
        if field_name in md_force_fields or prefer_md:
            return merge_text(md_issue.get(field_name), jira_issue.get(field_name))
        jira_val = collapse_whitespace(jira_issue.get(field_name))
        return jira_val if jira_val is not None else None

    def merge_issue_optional(field_name: str) -> dict | None:
        if field_name in md_force_fields:
            return md_issue.get(field_name)
        return (
            jira_issue.get(field_name)
            if jira_issue.get(field_name) not in (None, "", [])
            else None
        )

    def merge_issue_list(field_name: str) -> list[dict] | list[str]:
        if field_name in md_force_fields:
            return md_issue.get(field_name) or []
        if field_name == "links":
            return _merge_links(
                md_issue.get("links") or [], jira_issue.get("links") or []
            )
        return jira_issue.get(field_name) or []

    issue_type = merge_issue_text("issue_type")
    if prefer_jira_bootstrap and "summary" not in md_force_fields:
        summary = merge_text(jira_issue.get("summary"), md_issue.get("summary")) or ""
    else:
        summary = merge_text(md_issue.get("summary"), jira_issue.get("summary")) or ""
    merged_issue = {
        "key": merge_issue_text("key"),
        "url": merge_issue_text("url"),
        "summary": summary,
        "epic_name": None,
        "issue_type": issue_type,
        "status": merge_issue_text("status"),
        "priority": merge_issue_text("priority"),
        "created_at": merge_issue_text("created_at"),
        "assignee": merge_issue_optional("assignee"),
        "reporter": merge_issue_optional("reporter"),
        "parent_key": merge_issue_text("parent_key"),
        "end_date": merge_issue_text("end_date"),
        "due_date": merge_issue_text("due_date"),
        "labels": merge_issue_list("labels"),
        "components": merge_issue_list("components"),
        "links": merge_issue_list("links"),
    }
    if issue_type == "Epic":
        if prefer_jira_bootstrap:
            merged_issue["epic_name"] = (
                merge_text(jira_issue.get("epic_name"), md_issue.get("epic_name"))
                or summary
                or None
            )
        else:
            merged_issue["epic_name"] = (
                merge_text(md_issue.get("epic_name"), jira_issue.get("epic_name"))
                or summary
                or None
            )
        if "epic_name" in md_force_fields:
            merged_issue["epic_name"] = (
                merge_text(md_issue.get("epic_name"), jira_issue.get("epic_name"))
                or summary
                or None
            )
    return merged_issue


def merge_dsl(md_dsl: dict, jira_dsl: dict) -> dict:
    """MD DSL과 Jira DSL을 머지하여 하나의 통합 DSL을 생성한다."""
    prefer_jira_bootstrap = not has_meaningful_md_content(md_dsl)
    md_force_fields = set(md_dsl.get("md_force_fields") or [])
    merged_issue = merge_issue(md_dsl, jira_dsl, prefer_jira_bootstrap)
    if "description_markdown" in md_force_fields:
        description_markdown = (
            md_dsl["description_markdown"] or jira_dsl["description_markdown"]
        )
    else:
        description_markdown = (
            jira_dsl["description_markdown"] or md_dsl["description_markdown"]
        )
    return {
        "version": 1,
        "issue": merged_issue,
        "description_markdown": description_markdown,
        "md_force_fields": sorted(md_force_fields),
        "checklists": {
            "todo": merge_checklist_section(
                md_dsl["checklists"]["todo"],
                jira_dsl["checklists"]["todo"],
            ),
            "acceptance_criteria": merge_checklist_section(
                md_dsl["checklists"]["acceptance_criteria"],
                jira_dsl["checklists"]["acceptance_criteria"],
            ),
        },
    }


def print_summary(merged_dsl: dict) -> None:
    """머지 결과의 핵심 정보를 표준 출력에 출력한다."""
    issue = merged_dsl["issue"]
    print(f"key={issue.get('key') or '-'}")
    print(f"summary={issue['summary']}")
    print(f"issue_type={issue.get('issue_type') or '-'}")
    print(f"assignee={render_user_label(issue.get('assignee'))}")
    print(f"todo_count={len(merged_dsl['checklists']['todo'])}")
    print(
        "acceptance_criteria_count="
        f"{len(merged_dsl['checklists']['acceptance_criteria'])}"
    )


def main() -> None:
    """CLI 진입점. MD DSL과 Jira DSL을 머지하여 JSON으로 저장한다."""
    parser = argparse.ArgumentParser(description="Merge MD DSL and Jira DSL")
    parser.add_argument("--md", required=True, help="MD-derived Jira DSL JSON path")
    parser.add_argument("--jira", required=True, help="Jira-derived Jira DSL JSON path")
    parser.add_argument("--output", required=True, help="Merged Jira DSL JSON path")
    args = parser.parse_args()

    md_dsl = normalize_issue_dsl(load_json(args.md))
    jira_dsl = normalize_issue_dsl(load_json(args.jira))
    merged_dsl = normalize_issue_dsl(merge_dsl(md_dsl, jira_dsl))
    write_json(args.output, merged_dsl)
    print_summary(merged_dsl)


if __name__ == "__main__":
    main()
