#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from jira_dsl_lib import (
    checklist_item_to_jira,
    collapse_whitespace,
    load_json,
    normalize_description_markdown,
    normalize_issue_dsl,
)
from jira_raw_to_jira_dsl import build_issue_dsl

TODO_FIELD_ID = "customfield_11250"
AC_FIELD_ID = "customfield_11251"


def heading_to_jira(level: int, text: str, issue_type: str) -> str:
    """마크다운 제목을 Jira wiki 마크업으로 변환한다."""
    converted = convert_inline_markup(text)
    return f"h{level}. {converted}"


def convert_table_row(line: str) -> str:
    """마크다운 테이블 본문 행을 Jira wiki 형식으로 변환한다."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return "| " + " | ".join(convert_inline_markup(c) for c in cells) + " |"


def convert_table_header(line: str) -> str:
    """마크다운 테이블 헤더 행을 Jira wiki 형식으로 변환한다."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return "|| " + " || ".join(convert_inline_markup(c) for c in cells) + " ||"


def is_table_separator(line: str) -> bool:
    """마크다운 테이블 구분선 행인지 판별한다."""
    return bool(re.match(r"^\|[\s:]*[-]+[\s:]*(\|[\s:]*[-]+[\s:]*)*\|?\s*$", line))


def indentation_depth(prefix: str) -> int:
    """마크다운 리스트 들여쓰기를 Jira 리스트 depth로 변환한다."""
    expanded = prefix.expandtabs(2)
    return max(0, len(expanded) // 2)


def markdown_to_jira(markdown_text: str, issue_type: str = "Task") -> str:
    """마크다운 텍스트 전체를 Jira wiki 마크업으로 변환한다."""
    markdown_text = normalize_description_markdown(markdown_text, issue_type)
    lines = markdown_text.replace("\r\n", "\n").split("\n")
    output = []
    in_code_block = False
    table_state = 0  # 0: not in table, 1: header seen, 2: body
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                output.append("{code}")
            else:
                lang = stripped[3:].strip()
                output.append(f"{{code:{lang}}}" if lang else "{code}")
            in_code_block = not in_code_block
            table_state = 0
            continue
        if in_code_block:
            output.append(line)
            continue
        # table handling
        if stripped.startswith("|") and "|" in stripped[1:]:
            if is_table_separator(stripped):
                table_state = 2
                continue
            if table_state == 0:
                output.append(convert_table_header(stripped))
                table_state = 1
                continue
            output.append(convert_table_row(stripped))
            continue
        table_state = 0
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        numbered = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        bullet = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if heading:
            level = min(len(heading.group(1)), 6)
            output.append(heading_to_jira(level, heading.group(2), issue_type))
            continue
        if numbered:
            depth = indentation_depth(numbered.group(1)) + 1
            output.append(f"{'#' * depth} {convert_inline_markup(numbered.group(2))}")
            continue
        if bullet:
            depth = indentation_depth(bullet.group(1)) + 1
            output.append(f"{'*' * depth} {convert_inline_markup(bullet.group(2))}")
            continue
        output.append(convert_inline_markup(line))
    return "\n".join(output).strip()


def build_default_description_markdown(issue_type: str | None) -> str:
    """새 이슈 생성을 위한 기본 Description 스켈레톤을 반환한다."""
    normalized = collapse_whitespace(issue_type) or "Task"
    if normalized == "Epic":
        return "\n\n".join(
            [
                "## 배경\n\n- ",
                "## 목표\n\n- ",
                "## 기대효과\n\n- ",
                "## 요청 정보\n\n- 요청일:\n- 요청자:\n- 요청 조직:",
                "## 완료보고\n\n- TBD",
                "## 관련 링크\n\n- ",
            ]
        )
    return "\n\n".join(
        [
            "## 목표\n\n- ",
            "## 결과\n\n- ",
            "## 링크\n\n- ",
        ]
    )


def convert_inline_markup(text: str) -> str:
    """마크다운 인라인 마크업(코드, 볼드, 링크, 멘션)을 Jira 형식으로 변환한다."""
    text = re.sub(r"`([^`]+)`", r"{{\1}}", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"[\1|\2]", text)
    text = re.sub(r"(^|\s)@([A-Za-z0-9._-]+)", r"\1[~\2]", text)
    return text


def update_dsl_status(issue_dsl: dict, resolved_status: str, dsl_path: str) -> None:
    """transition이 해소한 실제 status가 DSL과 다르면 DSL 파일을 갱신한다.

    스크립트가 transition 정의에서 사전에 결정한 resolved status를 기록하므로,
    Jira 측 워크플로 버그와 무관하게 의도된 상태가 보존된다.
    """
    current = issue_dsl["issue"].get("status")
    if current == resolved_status:
        return
    issue_dsl["issue"]["status"] = resolved_status
    with open(dsl_path, "w", encoding="utf-8") as f:
        json.dump(issue_dsl, f, ensure_ascii=False, indent=2)
        f.write("\n")


def build_fields(issue_dsl: dict) -> dict:
    """DSL 객체에서 Jira 이슈 업데이트용 필드 딕셔너리를 생성한다."""
    issue = issue_dsl["issue"]
    issue_type = issue.get("issue_type") or "Task"
    assignee = issue.get("assignee") or {}
    fields = {
        "summary": issue["summary"],
        "description": markdown_to_jira(issue_dsl["description_markdown"], issue_type),
        TODO_FIELD_ID: [
            checklist_item_to_jira(item, index)
            for index, item in enumerate(issue_dsl["checklists"]["todo"], start=1)
        ],
        AC_FIELD_ID: [
            checklist_item_to_jira(item, index)
            for index, item in enumerate(
                issue_dsl["checklists"]["acceptance_criteria"],
                start=1,
            )
        ],
    }
    if issue.get("issue_type") == "Epic":
        fields["customfield_10351"] = issue.get("epic_name") or issue["summary"]
    if issue.get("priority"):
        fields["priority"] = {"name": issue["priority"]}
    assignee_username = assignee.get("username")
    if assignee_username:
        fields["assignee"] = {"name": assignee_username}
    # labels 반영
    labels = issue.get("labels")
    if labels:
        fields["labels"] = labels
    # components 반영
    components = issue.get("components")
    if components:
        fields["components"] = [{"name": name} for name in components]
    # end_date 반영 (customfield_11551 = End date)
    end_date = issue.get("end_date")
    if end_date:
        fields["customfield_11551"] = end_date
    # due_date 반영 (duedate = Jira 표준 마감일 필드)
    due_date = issue.get("due_date")
    if due_date:
        fields["duedate"] = due_date
    # parent_key 반영: Task/Story는 Epic Link, 그 외는 Parent Link
    parent_key = issue.get("parent_key")
    if parent_key:
        if issue_type in {"Task", "Story"}:
            fields["customfield_10350"] = parent_key
        else:
            fields["customfield_12287"] = parent_key
    return fields


def build_create_fields(issue_dsl: dict, project_key: str) -> dict:
    """DSL 객체에서 Jira 이슈 생성용 필드 딕셔너리를 생성한다."""
    issue = issue_dsl["issue"]
    issue_type = issue.get("issue_type") or "Task"
    assignee = issue.get("assignee") or {}
    fields = {
        "project": {"key": project_key},
        "summary": issue["summary"],
        "issuetype": {"name": issue_type},
        "description": markdown_to_jira(issue_dsl["description_markdown"], issue_type),
    }
    if issue.get("issue_type") == "Epic":
        fields["customfield_10351"] = issue.get("epic_name") or issue["summary"]
    if issue.get("priority"):
        fields["priority"] = {"name": issue["priority"]}
    assignee_username = assignee.get("username")
    if assignee_username:
        fields["assignee"] = {"name": assignee_username}
    # labels 반영
    labels = issue.get("labels")
    if labels:
        fields["labels"] = labels
    # components 반영
    components = issue.get("components")
    if components:
        fields["components"] = [{"name": name} for name in components]
    # end_date 반영 (customfield_11551 = End date)
    end_date = issue.get("end_date")
    if end_date:
        fields["customfield_11551"] = end_date
    # due_date 반영 (duedate = Jira 표준 마감일 필드)
    due_date = issue.get("due_date")
    if due_date:
        fields["duedate"] = due_date
    if issue.get("parent_key"):
        if issue.get("issue_type") in {"Task", "Story"}:
            fields["customfield_10350"] = issue["parent_key"]
        else:
            fields["customfield_12287"] = issue["parent_key"]
    return fields


def request_json(
    method: str, url: str, token: str, payload: dict | None = None
) -> dict:
    """Jira REST API에 JSON 요청을 보내고 응답을 반환한다."""
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode("utf-8").strip()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Jira API error ({error.code}): {body}") from error


def fetch_issue_raw(base_url: str, token: str, issue_key: str) -> dict:
    """생성 직후 Jira 이슈 원본을 다시 조회한다."""
    url = f"{base_url}/rest/api/2/issue/{issue_key}"
    return request_json("GET", url, token)


def update_issue(base_url: str, token: str, issue_key: str, fields: dict) -> None:
    """기존 Jira 이슈의 필드를 업데이트한다."""
    url = f"{base_url}/rest/api/2/issue/{issue_key}"
    request_json("PUT", url, token, {"fields": fields})


def fetch_current_status(base_url: str, token: str, issue_key: str) -> str | None:
    """현재 Jira 이슈의 status name을 조회한다."""
    url = f"{base_url}/rest/api/2/issue/{issue_key}?fields=status"
    response = request_json("GET", url, token)
    fields = response.get("fields") or {}
    status = fields.get("status") or {}
    return status.get("name")


def fetch_transitions(base_url: str, token: str, issue_key: str) -> list[dict]:
    """Jira 이슈에서 사용 가능한 transition 목록을 조회한다."""
    url = f"{base_url}/rest/api/2/issue/{issue_key}/transitions"
    response = request_json("GET", url, token)
    return response.get("transitions") or []


def transition_issue(
    base_url: str, token: str, issue_key: str, desired_status: str | None
) -> str | None:
    """이슈 상태를 transition API로 변경한다.

    transition 정의의 ``to.name`` (스크립트가 사전에 해석한 resolved status)을
    반환한다. transition이 불필요하거나 실패하면 ``None``을 반환한다.
    Jira에 실제 POST하기 **전에** resolved status를 결정하므로, Jira 측
    워크플로 버그가 있어도 반환값은 스크립트의 의도를 정확히 반영한다.
    """
    if not desired_status:
        return None
    current_status = fetch_current_status(base_url, token, issue_key)
    if not current_status:
        return None
    if current_status.casefold() == desired_status.casefold():
        return None

    transitions = fetch_transitions(base_url, token, issue_key)
    target_transition = None
    # 1차: target status name으로 매칭
    for t in transitions:
        to_status = (t.get("to") or {}).get("name") or ""
        if to_status.casefold() == desired_status.casefold():
            target_transition = t
            break
    # 2차: transition name으로 fallback 매칭 (예: "Review" → "In Review")
    if not target_transition:
        for t in transitions:
            if (t.get("name") or "").casefold() == desired_status.casefold():
                target_transition = t
                break
    # 3차: "Open" → "Reopened" fallback (Open은 초기 상태로 transition 불가)
    if not target_transition and desired_status.casefold() == "open":
        for t in transitions:
            to_status = (t.get("to") or {}).get("name") or ""
            if to_status.casefold() == "reopened":
                target_transition = t
                break

    if not target_transition:
        print(
            f"WARNING: No transition found to '{desired_status}' for {issue_key} "
            f"(current: {current_status})",
            file=sys.stderr,
        )
        return None

    # resolved status는 Jira POST 전에 transition 정의에서 결정한다
    resolved_status = (target_transition.get("to") or {}).get("name") or desired_status

    url = f"{base_url}/rest/api/2/issue/{issue_key}/transitions"
    request_json("POST", url, token, {"transition": {"id": target_transition["id"]}})
    return resolved_status


def _casefold_eq(a: str | None, b: str | None) -> bool:
    """대소문자 무시 비교. 둘 다 None이면 True."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return a.casefold() == b.casefold()


def verify_jira_sync(
    base_url: str, token: str, issue_key: str, issue_dsl: dict, dsl_path: str
) -> dict:
    """Jira 반영 후 실제 상태를 조회하여 merged DSL의 의도와 비교 검증한다.

    검증 결과를 ``{issue_key}.verify.json`` 파일로 저장하고 반환한다.
    mismatch가 있으면 stderr에 WARNING을 출력한다.
    """
    url = f"{base_url}/rest/api/2/issue/{issue_key}?fields=status,summary,priority,assignee"
    response = request_json("GET", url, token)
    jira_fields = response.get("fields") or {}
    issue = issue_dsl["issue"]

    verifications: dict[str, dict] = {}

    # status
    expected_status = issue.get("status")
    actual_status = (jira_fields.get("status") or {}).get("name")
    verifications["status"] = {
        "expected": expected_status,
        "actual": actual_status,
        "match": _casefold_eq(expected_status, actual_status),
    }

    # summary
    expected_summary = issue.get("summary")
    actual_summary = jira_fields.get("summary")
    verifications["summary"] = {
        "expected": expected_summary,
        "actual": actual_summary,
        "match": expected_summary == actual_summary,
    }

    # priority
    expected_priority = issue.get("priority")
    actual_priority = (jira_fields.get("priority") or {}).get("name")
    verifications["priority"] = {
        "expected": expected_priority,
        "actual": actual_priority,
        "match": _casefold_eq(expected_priority, actual_priority),
    }

    # assignee
    expected_assignee = (issue.get("assignee") or {}).get("username")
    actual_assignee = (jira_fields.get("assignee") or {}).get("name")
    verifications["assignee"] = {
        "expected": expected_assignee,
        "actual": actual_assignee,
        "match": expected_assignee == actual_assignee,
    }

    mismatches = {k: v for k, v in verifications.items() if not v["match"]}
    result = {
        "issue_key": issue_key,
        "all_match": len(mismatches) == 0,
        "fields": verifications,
        "mismatches": list(mismatches.keys()),
    }

    # Write verify file next to merged DSL
    verify_path = os.path.join(os.path.dirname(dsl_path), f"{issue_key}.verify.json")
    with open(verify_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    for field_name, info in mismatches.items():
        print(
            f"VERIFY WARNING: {issue_key}.{field_name} mismatch — "
            f"expected: {info['expected']}, actual: {info['actual']}",
            file=sys.stderr,
        )

    return result


def jira_link_to_dsl(link: dict) -> dict[str, str | None] | None:
    """Jira 이슈 링크 객체를 DSL 형식으로 정규화한다.

    direction/relationship은 "현재 이슈"의 관점(actor/receiver)으로 기록한다.
    (jira_raw_to_jira_dsl.jira_link_to_dsl 과 동일 컨벤션)
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
        }
    if "inwardIssue" in link:
        issue = link.get("inwardIssue") or {}
        return {
            "direction": "inward",
            "relationship": collapse_whitespace(
                link_type.get("inward") or link_type.get("name")
            ),
            "key": collapse_whitespace(issue.get("key")),
        }
    return None


def link_signature(link: dict[str, str | None]) -> tuple[str, str, str]:
    """연관 이슈 링크 비교용 시그니처를 생성한다."""
    return (
        (collapse_whitespace(link.get("direction")) or "").casefold(),
        (collapse_whitespace(link.get("relationship")) or "").casefold(),
        (collapse_whitespace(link.get("key")) or "").casefold(),
    )


def fetch_issue_link_records(
    base_url: str, token: str, issue_key: str
) -> list[dict[str, str | None]]:
    """현재 Jira 이슈의 링크 목록을 DSL 형식으로 조회한다."""
    url = f"{base_url}/rest/api/2/issue/{issue_key}?fields=issuelinks"
    response = request_json("GET", url, token)
    fields = response.get("fields") or {}
    links = []
    for raw_link in fields.get("issuelinks") or []:
        parsed = jira_link_to_dsl(raw_link)
        if parsed and parsed.get("key"):
            parsed["id"] = collapse_whitespace(raw_link.get("id"))
            links.append(parsed)
    return links


def fetch_issue_links(
    base_url: str, token: str, issue_key: str
) -> list[dict[str, str | None]]:
    """현재 Jira 이슈의 링크 목록을 DSL 형식으로 조회한다."""
    records = fetch_issue_link_records(base_url, token, issue_key)
    return [{k: v for k, v in record.items() if k != "id"} for record in records]


def fetch_link_type_lookup(
    base_url: str, token: str
) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """relationship 문자열을 Jira link type 이름으로 해석하기 위한 lookup을 만든다."""
    url = f"{base_url}/rest/api/2/issueLinkType"
    response = request_json("GET", url, token)
    directional: dict[tuple[str, str], str] = {}
    fallback: dict[str, str] = {}
    for raw_type in response.get("issueLinkTypes") or []:
        type_name = collapse_whitespace(raw_type.get("name"))
        if not type_name:
            continue
        fallback.setdefault(type_name.casefold(), type_name)
        outward = collapse_whitespace(raw_type.get("outward"))
        inward = collapse_whitespace(raw_type.get("inward"))
        if outward:
            directional[("outward", outward.casefold())] = type_name
            fallback.setdefault(outward.casefold(), type_name)
        if inward:
            directional[("inward", inward.casefold())] = type_name
            fallback.setdefault(inward.casefold(), type_name)
    return directional, fallback


def resolve_link_type_name(
    link: dict[str, str | None],
    directional_lookup: dict[tuple[str, str], str],
    fallback_lookup: dict[str, str],
) -> str | None:
    """DSL 링크 정보로 Jira link type 이름을 찾는다."""
    direction = collapse_whitespace(link.get("direction")) or "outward"
    relationship = collapse_whitespace(link.get("relationship"))
    if not relationship:
        return None
    return directional_lookup.get(
        (direction.casefold(), relationship.casefold())
    ) or fallback_lookup.get(relationship.casefold())


def build_issue_link_payload(
    issue_key: str, link: dict[str, str | None], type_name: str
) -> dict | None:
    """단일 링크 생성용 Jira payload를 구성한다.

    Jira REST API 컨벤션: POST ``inwardIssue=A, outwardIssue=B`` 는 "A → B" (A가 actor).
    DSL direction은 현재 이슈의 관점:
    - direction == "outward": 현재가 actor → POST ``inwardIssue=current, outwardIssue=target``
    - direction == "inward": 현재가 receiver → POST ``inwardIssue=target, outwardIssue=current``
    """
    target_key = collapse_whitespace(link.get("key"))
    if not target_key:
        return None
    direction = (collapse_whitespace(link.get("direction")) or "outward").casefold()
    payload = {"type": {"name": type_name}}
    if direction == "outward":
        payload["inwardIssue"] = {"key": issue_key}
        payload["outwardIssue"] = {"key": target_key}
        return payload
    payload["inwardIssue"] = {"key": target_key}
    payload["outwardIssue"] = {"key": issue_key}
    return payload


def delete_issue_link(base_url: str, token: str, link_id: str) -> None:
    """Jira issue link를 삭제한다."""
    url = f"{base_url}/rest/api/2/issueLink/{link_id}"
    request_json("DELETE", url, token)


def sync_issue_links(
    base_url: str,
    token: str,
    issue_key: str,
    desired_links: list[dict[str, str | None]],
    *,
    replace: bool = False,
) -> None:
    """머지된 DSL 기준으로 Jira 연관 이슈 링크를 동기화한다."""
    existing_records = fetch_issue_link_records(base_url, token, issue_key)
    existing_signatures = {link_signature(link) for link in existing_records}
    desired_signatures = {link_signature(link) for link in desired_links}

    if replace:
        for link in existing_records:
            signature = link_signature(link)
            if signature in desired_signatures:
                continue
            link_id = collapse_whitespace(link.get("id"))
            if link_id:
                delete_issue_link(base_url, token, link_id)

    if not desired_links:
        return

    directional_lookup, fallback_lookup = fetch_link_type_lookup(base_url, token)
    seen_desired: set[tuple[str, str, str]] = set()
    for link in desired_links:
        signature = link_signature(link)
        if signature in existing_signatures or signature in seen_desired:
            continue
        type_name = resolve_link_type_name(link, directional_lookup, fallback_lookup)
        if not type_name:
            raise SystemExit(f"Unknown Jira link relationship: {link!r}")
        payload = build_issue_link_payload(issue_key, link, type_name)
        if not payload:
            continue
        request_json("POST", f"{base_url}/rest/api/2/issueLink", token, payload)
        seen_desired.add(signature)


def create_issue(base_url: str, token: str, fields: dict) -> str:
    """새 Jira 이슈를 생성하고 생성된 이슈 키를 반환한다."""
    url = f"{base_url}/rest/api/2/issue"
    response = request_json("POST", url, token, {"fields": fields})
    key = response.get("key")
    if not key:
        raise SystemExit("Jira create response did not include issue key")
    return key


def persist_created_issue_identity(
    issue_dsl: dict, issue_key: str, base_url: str, token: str, dsl_path: str
) -> None:
    """생성된 이슈의 Jira 값을 다시 읽어 DSL을 백필한다."""
    local_description = issue_dsl.get("description_markdown")
    local_force_fields = issue_dsl.get("md_force_fields")
    hydrated = build_issue_dsl(fetch_issue_raw(base_url, token, issue_key))
    hydrated["description_markdown"] = local_description
    if local_force_fields is not None:
        hydrated["md_force_fields"] = local_force_fields
    issue_dsl.clear()
    issue_dsl.update(hydrated)
    with open(dsl_path, "w", encoding="utf-8") as f:
        json.dump(issue_dsl, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> None:
    """CLI 진입점. 머지된 DSL을 Jira에 반영하거나 페이로드를 출력한다."""
    parser = argparse.ArgumentParser(description="Sync Jira issue from merged Jira DSL")
    parser.add_argument("--input", required=True, help="Merged Jira DSL JSON path")
    parser.add_argument(
        "--base-url",
        default="https://jira.daumkakao.com",
        help="Jira base URL",
    )
    parser.add_argument(
        "--project-key",
        default="KCDL",
        help="Jira project key for issue creation",
    )
    parser.add_argument(
        "--token-env",
        default="JIRA_API_TOKEN",
        help="Environment variable that stores Jira API token",
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help="Create issue when issue.key is missing",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the Jira API request instead of printing payload",
    )
    args = parser.parse_args()

    issue_dsl = normalize_issue_dsl(load_json(args.input))
    issue = issue_dsl["issue"]
    force_fields = set(issue_dsl.get("md_force_fields") or [])
    replace_links = "links" in force_fields
    fields = build_fields(issue_dsl)

    if not args.apply:
        print(json.dumps({"fields": fields}, ensure_ascii=False, indent=2))
        return

    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"Missing Jira token env: {args.token_env}")

    issue_key = issue.get("key")
    if issue_key:
        update_issue(args.base_url, token, issue_key, fields)
        resolved_status = transition_issue(
            args.base_url, token, issue_key, issue.get("status")
        )
        if resolved_status:
            update_dsl_status(issue_dsl, resolved_status, args.input)
        sync_issue_links(
            args.base_url,
            token,
            issue_key,
            issue.get("links") or [],
            replace=replace_links,
        )
        verify_jira_sync(args.base_url, token, issue_key, issue_dsl, args.input)
        print(issue_key)
        return

    if not args.create:
        raise SystemExit(
            "issue.key is missing; rerun with --create to create a new issue"
        )

    if not collapse_whitespace(issue_dsl.get("description_markdown")):
        issue_dsl["description_markdown"] = build_default_description_markdown(
            issue.get("issue_type")
        )
        fields = build_fields(issue_dsl)

    create_fields = build_create_fields(issue_dsl, args.project_key)
    issue_key = create_issue(args.base_url, token, create_fields)
    persist_created_issue_identity(
        issue_dsl, issue_key, args.base_url, token, args.input
    )

    # Epic Link(customfield_10350)는 create payload에 포함해도 Jira가 무시하는
    # 경우가 있으므로 생성 후 별도 PUT으로 설정한다.
    parent_key = issue.get("parent_key")
    if parent_key and issue.get("issue_type") in {"Task", "Story"}:
        update_issue(args.base_url, token, issue_key, {"customfield_10350": parent_key})

    checklist_fields = {
        key: value
        for key, value in fields.items()
        if key in {TODO_FIELD_ID, AC_FIELD_ID}
    }
    if checklist_fields:
        update_issue(args.base_url, token, issue_key, checklist_fields)
    resolved_status = transition_issue(
        args.base_url, token, issue_key, issue.get("status")
    )
    if resolved_status:
        update_dsl_status(issue_dsl, resolved_status, args.input)
    sync_issue_links(
        args.base_url,
        token,
        issue_key,
        issue.get("links") or [],
        replace=replace_links,
    )
    verify_jira_sync(args.base_url, token, issue_key, issue_dsl, args.input)
    print(issue_key)


if __name__ == "__main__":
    main()
