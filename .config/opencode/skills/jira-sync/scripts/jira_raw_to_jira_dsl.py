#!/usr/bin/env python3

from __future__ import annotations

import argparse
from typing import Any

from jira_dsl_lib import (
    build_issue_browse_url,
    collapse_whitespace,
    jira_markup_to_markdown,
    normalize_issue_dsl,
    write_json,
    load_json,
)


TODO_FIELD_ID = "customfield_11250"
AC_FIELD_ID = "customfield_11251"


def field_name(value: Any) -> str | None:
    """Jira 필드 값에서 표시 이름을 추출한다."""
    if isinstance(value, dict):
        return collapse_whitespace(value.get("name") or value.get("value"))
    return collapse_whitespace(value)


def jira_user_to_dsl(user: Any) -> dict[str, str | None] | None:
    """Jira 사용자 객체를 DSL의 username/display_name 형식으로 변환한다."""
    if not isinstance(user, dict):
        return None
    username = collapse_whitespace(user.get("name") or user.get("username"))
    display_name = collapse_whitespace(user.get("displayName"))
    if not username and not display_name:
        return None
    return {"username": username, "display_name": display_name}


def created_at_to_date(value: Any) -> str | None:
    """Jira 생성일 타임스탬프에서 날짜 부분(YYYY-MM-DD)만 추출한다."""
    text = collapse_whitespace(value)
    if not text:
        return None
    return text[:10]


def extract_parent_key(fields: dict[str, Any], issue_type: str | None) -> str | None:
    """이슈 필드에서 상위 이슈 키를 추출한다."""
    parent = fields.get("parent")
    parent_key = None
    if isinstance(parent, dict):
        parent_key = collapse_whitespace(parent.get("key"))

    if issue_type in {"Task", "Story"}:
        candidates = [
            fields.get("customfield_10350"),
            fields.get("customfield_12287"),
            parent_key,
        ]
    else:
        candidates = [
            fields.get("customfield_12287"),
            fields.get("customfield_10350"),
            parent_key,
        ]

    for candidate in candidates:
        text = collapse_whitespace(candidate)
        if text:
            return text
    return None


def extract_link_summary(issue: dict[str, Any]) -> str | None:
    """연결된 이슈 객체에서 summary를 추출한다."""
    fields = issue.get("fields")
    if isinstance(fields, dict):
        return collapse_whitespace(fields.get("summary"))
    return None


def jira_link_to_dsl(link: Any) -> dict[str, str | None] | None:
    """Jira 이슈 링크 객체를 DSL 형식으로 변환한다.

    DSL의 direction/relationship은 "현재 이슈"의 관점(actor/receiver)으로 표기한다.
    Jira REST API 컨벤션: POST ``inwardIssue=A, outwardIssue=B`` 는 "A → B" 를 만든다
    (A가 actor, B가 receiver; A의 페이지에는 outward description이 표시됨).
    GET 응답에서 각 링크 항목의 필드명은 상대 이슈가 link graph에서 차지하는 slot을 의미한다.
    - ``outwardIssue: Y`` = 링크가 current → Y (current가 actor = outward-semantic-side)
      → direction="outward", relationship=link_type.outward (current 페이지의 표기)
    - ``inwardIssue: Y`` = 링크가 Y → current (current가 receiver = inward-semantic-side)
      → direction="inward", relationship=link_type.inward
    """
    if not isinstance(link, dict):
        return None
    link_type = link.get("type") or {}
    if "outwardIssue" in link:
        issue = link.get("outwardIssue") or {}
        return {
            "direction": "outward",
            "relationship": collapse_whitespace(
                link_type.get("outward") or link_type.get("name")
            ),
            "key": collapse_whitespace(issue.get("key")),
            "summary": extract_link_summary(issue),
        }
    if "inwardIssue" in link:
        issue = link.get("inwardIssue") or {}
        return {
            "direction": "inward",
            "relationship": collapse_whitespace(
                link_type.get("inward") or link_type.get("name")
            ),
            "key": collapse_whitespace(issue.get("key")),
            "summary": extract_link_summary(issue),
        }
    return None


def extract_labels(fields: dict[str, Any]) -> list[str]:
    """Jira 이슈 필드에서 labels 배열을 추출한다."""
    raw = fields.get("labels")
    if not isinstance(raw, list):
        return []
    return [collapse_whitespace(label) for label in raw if collapse_whitespace(label)]


def extract_components(fields: dict[str, Any]) -> list[str]:
    """Jira 이슈 필드에서 components 이름 배열을 추출한다."""
    raw = fields.get("components")
    if not isinstance(raw, list):
        return []
    names = []
    for comp in raw:
        if isinstance(comp, dict):
            name = collapse_whitespace(comp.get("name"))
        else:
            name = collapse_whitespace(comp)
        if name:
            names.append(name)
    return names


def jira_checklist_item_to_dsl(item: Any) -> dict[str, Any] | None:
    """Jira 체크리스트 항목을 DSL 형식으로 변환한다."""
    if not isinstance(item, dict):
        return None
    assignee_ids = item.get("assigneeIds")
    assignee_username = None
    if isinstance(assignee_ids, list) and assignee_ids:
        assignee_username = collapse_whitespace(assignee_ids[0])
    raw_status = item.get("status")
    status = raw_status if isinstance(raw_status, dict) else {}
    raw_id = item.get("id")
    item_id = int(raw_id) if raw_id is not None else None
    return {
        "id": item_id,
        "name": collapse_whitespace(item.get("name")) or "",
        "checked": bool(item.get("checked", False)),
        "completed_date": None,
        "linked_issue_key": collapse_whitespace(
            item.get("linkedIssueKey") or item.get("ticketKey")
        ),
        "assignee_username": assignee_username,
        "status_name": collapse_whitespace(status.get("name")),
        "status_id": collapse_whitespace(item.get("statusId") or status.get("id")),
    }


def parse_checklist(items: Any) -> list[dict[str, Any]]:
    """Jira 체크리스트 배열을 파싱하여 DSL 항목 목록으로 반환한다."""
    if not isinstance(items, list):
        return []
    parsed = []
    for item in items:
        parsed_item = jira_checklist_item_to_dsl(item)
        if parsed_item and parsed_item["name"]:
            parsed.append(parsed_item)
    return parsed


def build_issue_dsl(raw_issue: dict[str, Any]) -> dict[str, Any]:
    """Jira raw 이슈 JSON을 완전한 DSL 객체로 변환한다."""
    fields = raw_issue.get("fields") or {}
    issue_type = field_name(fields.get("issuetype"))
    summary = collapse_whitespace(fields.get("summary")) or ""
    links = []
    for link in fields.get("issuelinks") or []:
        parsed_link = jira_link_to_dsl(link)
        if parsed_link:
            links.append(parsed_link)

    dsl = {
        "version": 1,
        "issue": {
            "key": collapse_whitespace(raw_issue.get("key")),
            "url": build_issue_browse_url(raw_issue.get("key")),
            "summary": summary,
            "epic_name": collapse_whitespace(fields.get("customfield_10351")),
            "issue_type": issue_type,
            "status": field_name(fields.get("status")),
            "priority": field_name(fields.get("priority")),
            "created_at": created_at_to_date(fields.get("created")),
            "assignee": jira_user_to_dsl(fields.get("assignee")),
            "reporter": jira_user_to_dsl(fields.get("reporter")),
            "parent_key": extract_parent_key(fields, issue_type),
            "end_date": created_at_to_date(fields.get("customfield_11551")),
            "due_date": created_at_to_date(fields.get("duedate")),
            "labels": extract_labels(fields),
            "components": extract_components(fields),
            "links": links,
        },
        "description_markdown": jira_markup_to_markdown(
            fields.get("description"), issue_type
        ),
        "checklists": {
            "todo": parse_checklist(fields.get(TODO_FIELD_ID)),
            "acceptance_criteria": parse_checklist(fields.get(AC_FIELD_ID)),
        },
    }
    return normalize_issue_dsl(dsl)


def main() -> None:
    """CLI 진입점. Jira raw JSON을 Jira DSL JSON으로 변환한다."""
    parser = argparse.ArgumentParser(
        description="Convert Jira raw issue JSON to Jira DSL"
    )
    parser.add_argument("--input", required=True, help="Jira raw issue JSON path")
    parser.add_argument("--output", required=True, help="Output Jira DSL JSON path")
    args = parser.parse_args()

    raw_issue = load_json(args.input)
    issue_dsl = build_issue_dsl(raw_issue)
    write_json(args.output, issue_dsl)
    print(args.output)


if __name__ == "__main__":
    main()
