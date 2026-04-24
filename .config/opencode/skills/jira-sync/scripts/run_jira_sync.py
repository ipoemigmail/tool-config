#!/usr/bin/env python3

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent

TASK_STATUS_FILENAME_PREFIX = {
    "holding": "⏸️",
    "in progress": "🔄",
    "resolved": "⏹️",
    "open": "🆕",
    "closed": "☑️",
    "reopened": "⏏️",
}

TASKS_DIR_NAME = ".tasks"

sys.path.insert(0, str(SCRIPT_DIR))

from jira_dsl_lib import normalize_markdown, parse_frontmatter_yaml, split_frontmatter
from sync_jira_from_jira_dsl import markdown_to_jira


@dataclass
class IssuePlan:
    key: str | None
    summary: str | None
    md_path: Path
    source_md_path: Path | None
    raw_path: Path | None
    md_dsl_path: Path
    jira_dsl_path: Path
    merged_dsl_path: Path
    preview_path: Path
    exists_locally: bool
    create_on_apply: bool
    parent_epic_key: str | None = None


@dataclass(frozen=True)
class MdPathPlan:
    source_path: Path | None
    target_path: Path


def collapse_whitespace(value: str | None) -> str | None:
    """연속 공백을 하나로 합치고 양끝 공백을 제거한다."""
    if value is None:
        return None
    collapsed = re.sub(r"\s+", " ", str(value)).strip()
    return collapsed or None


def extract_issue_key(value: str | None) -> str | None:
    """문자열에서 Jira 이슈 키(예: KCDL-1234)를 추출한다."""
    if not value:
        return None
    match = re.search(r"\b([A-Z]+-\d+)\b", value)
    return match.group(1) if match else None


def parse_markdown_summary_and_issue_key(path: Path) -> tuple[str | None, str | None]:
    """마크다운 파일에서 summary와 issue key를 파싱한다.

    summary 우선순위:
    1. frontmatter `title`
    2. 본문 첫 번째 `# ` H1 헤딩 (frontmatter title이 없을 때만 fallback)
    """
    if not path.exists():
        return None, None
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        return None, None

    start = 0
    fm_issue_key: str | None = None
    fm_title: str | None = None
    fm_text, _ = split_frontmatter(text)
    if fm_text is not None:
        fm = parse_frontmatter_yaml(fm_text)
        raw_issue = fm.get("issue")
        issue_meta: dict = raw_issue if isinstance(raw_issue, dict) else {}
        fm_issue_key = extract_issue_key(
            str(issue_meta.get("key") or fm.get("jira") or fm.get("IssueKey") or "")
        )
        fm_title = collapse_whitespace(fm.get("title"))
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break

    # frontmatter title을 우선 사용
    if fm_title:
        return fm_title, fm_issue_key

    # frontmatter title이 없으면 본문 첫 # H1 헤딩에서 추출
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^#\s+(.*?)(?:\s+\[[A-Z]+-\d+\])?\s*$", stripped)
        if match:
            return collapse_whitespace(match.group(1)), fm_issue_key
        break
    return None, fm_issue_key


def issue_output_paths(
    preview_dir: Path, key: str | None, slug: str
) -> tuple[Path, Path, Path, Path, Path]:
    """이슈 키와 슬러그로 중간 산출물 파일 경로들을 생성한다."""
    base = key or slug or "issue"
    return (
        preview_dir / f"{base}.raw.json",
        preview_dir / f"{base}.md.dsl.json",
        preview_dir / f"{base}.jira.dsl.json",
        preview_dir / f"{base}.merged.dsl.json",
        preview_dir / f"{base}.preview.md",
    )


def sanitize_filename(summary: str | None) -> str:
    """summary 문자열을 안전한 파일/디렉토리명으로 변환한다."""
    text = collapse_whitespace(summary) or "Untitled"
    text = re.sub(r'[\\/:*?"<>|]+', "-", text)
    return text.strip(" .") or "Untitled"


def task_status_filename_prefix(status: str | None) -> str:
    """Task 상태에 따라 파일명 이모지 접두사를 반환한다."""
    normalized = collapse_whitespace(status)
    if not normalized:
        return "*️⃣"
    return TASK_STATUS_FILENAME_PREFIX.get(normalized.casefold(), "*️⃣")


def build_task_filename(
    issue_key: str | None,
    summary: str | None,
    status: str | None = None,
) -> str:
    """Task markdown 파일명을 상태 이모지 규칙에 맞게 생성한다.

    형식: <이모지> (<ISSUE_KEY>) <제목>.md
    예: 🆕 (KCDL-5500) [Sandbox] DB 에서 샤딩 정보를 가져올수 있는 API 추가.md
    """
    prefix = task_status_filename_prefix(status)

    if collapse_whitespace(issue_key):
        base_name = f"({issue_key}) {sanitize_filename(summary)}.md"
    else:
        base_name = f"{sanitize_filename(summary)}.md"

    return f"{prefix} {base_name}" if prefix else base_name


def run_subprocess(command: list[str], *, env: dict[str, str] | None = None) -> None:
    """외부 명령을 서브프로세스로 실행한다."""
    subprocess.run(command, check=True, env=env)


def curl_fetch(
    url: str, output_path: Path, token: str, extra_args: list[str] | None = None
) -> None:
    """curl로 URL 내용을 파일에 저장한다."""
    command = [
        "curl",
        "-sS",
        "-H",
        f"Authorization: Bearer {token}",
        "-H",
        "Content-Type: application/json",
    ]
    if extra_args:
        command.extend(extra_args)
    command.extend([url, "-o", str(output_path)])
    run_subprocess(command)


def load_json(path: Path) -> Any:
    """JSON 파일을 읽어 파이썬 객체로 반환한다."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """파이썬 객체를 JSON 파일로 저장한다."""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build_empty_dsl(issue_key: str | None, summary: str = "") -> dict[str, Any]:
    """최소한의 빈 DSL 객체를 생성한다."""
    return {
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
        "checklists": {"todo": [], "acceptance_criteria": []},
    }


def compare_issue_fields(
    jira_dsl: dict[str, Any], merged_dsl: dict[str, Any]
) -> list[str]:
    """Jira DSL과 머지 DSL을 비교하여 변경된 필드 목록을 반환한다."""
    changes = []
    jira_issue = jira_dsl.get("issue", {})
    merged_issue = merged_dsl.get("issue", {})
    if jira_dsl.get("issue") != merged_dsl.get("issue"):
        for field in merged_issue:
            if field == "links":
                if normalize_links_for_compare(
                    jira_issue.get(field)
                ) != normalize_links_for_compare(merged_issue.get(field)):
                    changes.append("issue.links")
                continue
            if jira_issue.get(field) != merged_issue.get(field):
                changes.append(f"issue.{field}")
    jira_description = render_description_for_compare(
        jira_dsl.get("description_markdown"), jira_issue.get("issue_type")
    )
    merged_description = render_description_for_compare(
        merged_dsl.get("description_markdown"), merged_issue.get("issue_type")
    )
    if jira_description != merged_description:
        changes.append("description_markdown")
    jira_checklists = jira_dsl.get("checklists", {})
    merged_checklists = merged_dsl.get("checklists", {})
    if jira_checklists.get("todo") != merged_checklists.get("todo"):
        changes.append("checklists.todo")
    if jira_checklists.get("acceptance_criteria") != merged_checklists.get(
        "acceptance_criteria"
    ):
        changes.append("checklists.acceptance_criteria")
    return changes


def _change_field_is_forced(change_name: str, md_force_fields: set[str]) -> bool:
    """변경 필드가 md_force_fields에 포함되는지 확인한다.

    compare_issue_fields 결과(issue.summary 등)를 md_force_fields(summary 등)와 매핑한다.
    ((force)) 가 지정된 필드만 Jira 동기화 대상으로 표시하기 위해 사용한다.
    """
    if change_name in md_force_fields:
        return True
    if change_name.startswith("issue."):
        return change_name[len("issue.") :] in md_force_fields
    return False


def normalize_description_for_compare(value: Any) -> str:
    """ordered list 번호 차이를 무시할 수 있도록 description을 비교용으로 정규화한다."""
    text = normalize_markdown(value)
    lines = []
    for line in text.split("\n"):
        if re.match(r"^\|(?:\s*:?-+:?\s*\|)+\s*$", line.strip()):
            continue
        if line.strip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            lines.append("|" + "|".join(cells) + "|")
            continue
        match = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        if match:
            lines.append(f"{match.group(1)}1. {match.group(2)}")
            continue
        lines.append(line)
    collapsed: list[str] = []
    for line in lines:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)
    return "\n".join(collapsed)


def render_description_for_compare(value: Any, issue_type: Any) -> str:
    """비교 전 description을 Jira markup 기준으로 정규화한다."""
    normalized = normalize_description_for_compare(value)
    return markdown_to_jira(normalized, collapse_whitespace(issue_type) or "Task")


def normalize_links_for_compare(
    links: Any,
) -> list[tuple[str, str, str]]:
    """링크를 (key, direction, relationship) 튜플 리스트로 정규화하여 비교한다."""
    normalized: list[tuple[str, str, str]] = []
    if not isinstance(links, list):
        return []
    seen: set[str] = set()
    for link in links:
        if not isinstance(link, dict):
            continue
        key = (collapse_whitespace(link.get("key")) or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        direction = (collapse_whitespace(link.get("direction")) or "").casefold()
        relationship = (collapse_whitespace(link.get("relationship")) or "").casefold()
        normalized.append((key, direction, relationship))
    return sorted(normalized)


def compare_md_to_merged_dsl(
    md_dsl: dict[str, Any], merged_dsl: dict[str, Any]
) -> list[str]:
    """MD DSL과 merged DSL을 비교하여 변경된 필드 목록을 반환한다."""
    changes: list[str] = []
    md_issue = md_dsl.get("issue", {})
    merged_issue = merged_dsl.get("issue", {})

    for field in merged_issue:
        if field == "links":
            if normalize_links_for_compare(
                md_issue.get(field)
            ) != normalize_links_for_compare(merged_issue.get(field)):
                changes.append(f"issue.{field}")
            continue
        if md_issue.get(field) != merged_issue.get(field):
            changes.append(f"issue.{field}")

    md_description = normalize_description_for_compare(
        md_dsl.get("description_markdown")
    )
    merged_description = normalize_description_for_compare(
        merged_dsl.get("description_markdown")
    )
    if md_description != merged_description:
        changes.append("description_markdown")

    md_checklists = md_dsl.get("checklists", {})
    merged_checklists = merged_dsl.get("checklists", {})
    if md_checklists.get("todo") != merged_checklists.get("todo"):
        changes.append("checklists.todo")
    if md_checklists.get("acceptance_criteria") != merged_checklists.get(
        "acceptance_criteria"
    ):
        changes.append("checklists.acceptance_criteria")

    return changes


def compare_local_markdown(
    source_md_path: Path | None,
    target_md_path: Path,
    preview_text: str,
    *,
    exists_locally: bool,
) -> tuple[str, list[str]]:
    """로컬 markdown 변경 유형과 세부 필드를 계산한다."""
    if not exists_locally:
        return "new", ["filename", "content"]

    changes: list[str] = []
    if source_md_path is not None and source_md_path != target_md_path:
        changes.append("filename")

    local_text = (source_md_path or target_md_path).read_text(encoding="utf-8")
    if local_text != preview_text:
        changes.append("content")

    if not changes:
        return "same", []
    if changes == ["filename"]:
        return "rename", changes
    if changes == ["content"]:
        return "content", changes
    return "rename+content", changes


def strip_force_markers_from_md(source_path: Path | None) -> bool:
    """source MD에서 ``((force))`` 마커를 물리적으로 제거한다.

    처리 대상:
    - frontmatter 키 접미사 (예: ``status ((force)): In Progress`` -> ``status: In Progress``)
    - 본문 상단의 ``((force))`` 단독 라인 (frontmatter 직후)

    반환값: 파일이 수정되었으면 True.
    """
    if source_path is None or not source_path.exists():
        return False

    original = source_path.read_text(encoding="utf-8")
    fm_text, body = split_frontmatter(original)
    modified = False

    if fm_text is not None:
        new_fm_lines: list[str] = []
        for line in fm_text.split("\n"):
            new_line = re.sub(
                r"^(\s*)([^\s:]+)\s*\(\(force\)\)\s*:",
                r"\1\2:",
                line,
            )
            if new_line != line:
                modified = True
            new_fm_lines.append(new_line)
        fm_text = "\n".join(new_fm_lines)

        body_lstripped = body.lstrip("\n")
        if body_lstripped.startswith("((force))"):
            remainder = body_lstripped[len("((force))") :]
            if remainder.startswith("\r\n"):
                remainder = remainder[2:]
            elif remainder.startswith("\n"):
                remainder = remainder[1:]
            body = remainder.lstrip("\n")
            modified = True

        if modified:
            # 렌더러는 frontmatter와 description 사이에 빈 줄 하나를 유지한다.
            body_content = body.lstrip("\n")
            if body_content:
                new_text = f"---\n{fm_text}\n---\n\n{body_content}"
            else:
                new_text = f"---\n{fm_text}\n---\n"
            source_path.write_text(new_text, encoding="utf-8")
            return True
        return False

    # frontmatter 없는 문서: 본문 최상단 ((force)) 만 처리한다.
    stripped = original.lstrip()
    if stripped.startswith("((force))"):
        remainder = stripped[len("((force))") :]
        if remainder.startswith("\r\n"):
            remainder = remainder[2:]
        elif remainder.startswith("\n"):
            remainder = remainder[1:]
        leading_len = len(original) - len(stripped)
        new_text = original[:leading_len] + remainder.lstrip("\n")
        if new_text != original:
            source_path.write_text(new_text, encoding="utf-8")
            return True
    return False


def render_markdown(plan: IssuePlan, output_path: Path) -> None:
    """머지된 DSL을 마크다운 파일로 렌더링한다."""
    run_subprocess(
        [
            sys.executable,
            str(SCRIPT_DIR / "render_md_from_jira_dsl.py"),
            "--input",
            str(plan.merged_dsl_path),
            "--output",
            str(output_path),
        ]
    )


def sync_jira(plan: IssuePlan, project_key: str) -> None:
    """머지된 DSL을 Jira에 반영한다."""
    command = [
        sys.executable,
        str(SCRIPT_DIR / "sync_jira_from_jira_dsl.py"),
        "--input",
        str(plan.merged_dsl_path),
        "--apply",
    ]
    if plan.create_on_apply:
        command.extend(["--create", "--project-key", project_key])
    run_subprocess(command)


def process_issue(plan: IssuePlan) -> dict[str, Any]:
    """단일 이슈의 전체 동기화 파이프라인(MD→DSL, Jira→DSL, 머지, 프리뷰)을 실행한다."""
    md_command = [
        sys.executable,
        str(SCRIPT_DIR / "md_to_jira_dsl.py"),
        "--output",
        str(plan.md_dsl_path),
    ]
    if plan.exists_locally:
        md_command.extend(["--input", str(plan.source_md_path or plan.md_path)])
    else:
        md_command.extend(["--issue-key", plan.key or "", "--allow-missing"])
    run_subprocess(md_command)

    if plan.raw_path and plan.raw_path.exists():
        run_subprocess(
            [
                sys.executable,
                str(SCRIPT_DIR / "jira_raw_to_jira_dsl.py"),
                "--input",
                str(plan.raw_path),
                "--output",
                str(plan.jira_dsl_path),
            ]
        )
    else:
        empty_dsl = build_empty_dsl(plan.key, plan.summary or "")
        if plan.parent_epic_key:
            empty_dsl["issue"]["parent_key"] = plan.parent_epic_key
            if not empty_dsl["issue"]["issue_type"]:
                empty_dsl["issue"]["issue_type"] = "Task"
        write_json(plan.jira_dsl_path, empty_dsl)

    run_subprocess(
        [
            sys.executable,
            str(SCRIPT_DIR / "merge_jira_dsl.py"),
            "--md",
            str(plan.md_dsl_path),
            "--jira",
            str(plan.jira_dsl_path),
            "--output",
            str(plan.merged_dsl_path),
        ]
    )
    render_markdown(plan, plan.preview_path)

    md_dsl = load_json(plan.md_dsl_path)
    merged_dsl = load_json(plan.merged_dsl_path)
    jira_dsl = load_json(plan.jira_dsl_path)
    preview_text = plan.preview_path.read_text(encoding="utf-8")
    all_jira_changes = compare_issue_fields(jira_dsl, merged_dsl)
    md_force_fields = set(merged_dsl.get("md_force_fields") or [])
    fields_to_sync_to_jira = [
        f for f in all_jira_changes if _change_field_is_forced(f, md_force_fields)
    ]
    # ((force))가 있지만 Jira 에 반영할 변경이 없으면 마커를 소스 MD 에서 제거한다.
    if (
        md_force_fields
        and not fields_to_sync_to_jira
        and plan.source_md_path is not None
    ):
        strip_force_markers_from_md(plan.source_md_path)
    fields_to_write_to_md = compare_md_to_merged_dsl(md_dsl, merged_dsl)
    issue = merged_dsl.get("issue", {})
    if (
        collapse_whitespace(issue.get("issue_type")) == "Task"
        and plan.md_path.parent.name == TASKS_DIR_NAME
        and issue.get("key")
    ):
        plan.md_path = plan.md_path.parent / build_task_filename(
            issue.get("key"),
            issue.get("summary"),
            issue.get("status"),
        )
    local_change, _ = compare_local_markdown(
        plan.source_md_path,
        plan.md_path,
        preview_text,
        exists_locally=plan.exists_locally,
    )
    if fields_to_write_to_md:
        local_change = f"{local_change}({','.join(sorted(fields_to_write_to_md))})"
    return {
        "key": issue.get("key"),
        "summary": issue.get("summary"),
        "issue_type": issue.get("issue_type"),
        "source_md_path": str(plan.source_md_path) if plan.source_md_path else None,
        "md_path": str(plan.md_path),
        "preview_path": str(plan.preview_path),
        "merged_dsl_path": str(plan.merged_dsl_path),
        "local_change": local_change,
        "fields_to_write_to_md": fields_to_write_to_md,
        "fields_to_sync_to_jira": fields_to_sync_to_jira,
        "create_on_apply": plan.create_on_apply,
    }


def collect_local_task_files(
    task_dir: Path,
) -> tuple[dict[str, Path], dict[str, Path]]:
    """.tasks/ 디렉토리의 로컬 MD 파일들을 수집한다."""
    by_key: dict[str, Path] = {}
    by_summary: dict[str, Path] = {}
    for path in sorted(task_dir.glob("*.md")):
        summary, key = parse_markdown_summary_and_issue_key(path)
        if key:
            by_key[key] = path
        if summary:
            by_summary[summary.casefold()] = path
    return by_key, by_summary


def issue_key_sort_key(issue_key: str) -> tuple[str, int, str]:
    """Jira 이슈 키를 프로젝트/번호 기준으로 정렬 가능하게 변환한다."""
    match = re.match(r"^([A-Z]+)-(\d+)$", issue_key)
    if not match:
        return (issue_key, sys.maxsize, issue_key)
    return (match.group(1), int(match.group(2)), issue_key)


def build_child_target_path(
    task_dir: Path,
    issue_key: str | None,
    summary: str | None,
    status: str | None = None,
) -> Path:
    """child 이슈의 목표 markdown 경로를 계산한다."""
    filename = build_task_filename(issue_key, summary, status)
    return task_dir / filename


def plan_child_paths(
    task_dir: Path,
    child_issues: list[dict[str, Any]],
) -> dict[str, MdPathPlan]:
    """child 이슈마다 로컬 MD 파일의 원본/목표 경로를 계획한다."""
    by_key, by_summary = collect_local_task_files(task_dir)
    planned: dict[str, MdPathPlan] = {}
    new_issue_jobs: list[tuple[str, str | None, str | None]] = []
    for issue in child_issues:
        key: str | None = issue.get("key")
        if not key:
            continue
        fields = issue.get("fields") or {}
        summary = collapse_whitespace(fields.get("summary"))
        status = collapse_whitespace((fields.get("status") or {}).get("name"))
        source_path = by_key.get(key)
        if source_path is None and summary and summary.casefold() in by_summary:
            source_path = by_summary[summary.casefold()]
        if source_path is not None:
            target_path = build_child_target_path(task_dir, key, summary, status)
            planned[key] = MdPathPlan(source_path=source_path, target_path=target_path)
            continue
        new_issue_jobs.append((key, summary, status))

    for key, summary, status in sorted(
        new_issue_jobs, key=lambda item: issue_key_sort_key(item[0])
    ):
        target_path = build_child_target_path(task_dir, key, summary, status)
        planned[key] = MdPathPlan(source_path=None, target_path=target_path)
    return planned


def cleanup_renamed_markdown_files(plans: list[IssuePlan]) -> None:
    """rename 대상이 된 기존 markdown 원본 파일을 안전하게 정리한다."""
    target_paths = {plan.md_path for plan in plans}
    for plan in plans:
        source_path = plan.source_md_path
        if source_path is None or source_path == plan.md_path:
            continue
        if source_path in target_paths:
            continue
        if source_path.exists():
            source_path.unlink()


def build_issue_plan(
    preview_dir: Path,
    key: str | None,
    summary: str | None,
    md_path: Path,
    raw_path: Path | None,
    *,
    source_md_path: Path | None = None,
    create_on_apply: bool = False,
    parent_epic_key: str | None = None,
) -> IssuePlan:
    """이슈 하나에 대한 동기화 계획(IssuePlan)을 생성한다."""
    raw_output, md_dsl_output, jira_dsl_output, merged_output, preview_output = (
        issue_output_paths(
            preview_dir,
            key,
            sanitize_filename(summary),
        )
    )
    if raw_path is None:
        raw_path = raw_output if key else None
    local_source = source_md_path if source_md_path is not None else md_path
    return IssuePlan(
        key=key,
        summary=summary,
        md_path=md_path,
        source_md_path=local_source if local_source.exists() else None,
        raw_path=raw_path,
        md_dsl_path=md_dsl_output,
        jira_dsl_path=jira_dsl_output,
        merged_dsl_path=merged_output,
        preview_path=preview_output,
        exists_locally=local_source.exists(),
        create_on_apply=create_on_apply,
        parent_epic_key=parent_epic_key,
    )


def main() -> None:
    """CLI 진입점. Epic 단위 이슈 동기화를 오케스트레이션한다."""
    parser = argparse.ArgumentParser(
        description="Preview or apply Jira sync for a markdown issue bundle"
    )
    parser.add_argument(
        "--md", required=True, help="Root EPIC.md or task markdown path"
    )
    parser.add_argument(
        "--preview-dir",
        default="/tmp",
        help="Directory used for raw JSON, DSL, preview markdown, and summary files",
    )
    parser.add_argument(
        "--base-url",
        default="https://jira.daumkakao.com",
        help="Jira base URL",
    )
    parser.add_argument(
        "--project-key",
        default="KCDL",
        help="Jira project key used when creating issues",
    )
    parser.add_argument(
        "--token-env",
        default="JIRA_API_TOKEN",
        help="Environment variable storing Jira API token",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=min(8, os.cpu_count() or 4),
        help="Maximum worker count used for child issue fetch and processing",
    )
    parser.add_argument(
        "--write-md",
        action="store_true",
        help="Write merged previews back to markdown files",
    )
    parser.add_argument(
        "--apply-jira",
        action="store_true",
        help="Apply merged Jira DSL back to Jira and rewrite markdown from merged DSL",
    )
    parser.add_argument(
        "--summary-output",
        help="Optional path for the generated sync summary JSON",
    )
    args = parser.parse_args()

    write_md = args.write_md or args.apply_jira

    md_path = Path(args.md).resolve()
    preview_dir = Path(args.preview_dir).resolve()
    preview_dir.mkdir(parents=True, exist_ok=True)

    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"Missing Jira token env: {args.token_env}")

    main_summary, main_key = parse_markdown_summary_and_issue_key(md_path)
    main_plan = build_issue_plan(
        preview_dir,
        main_key,
        main_summary,
        md_path,
        None,
        create_on_apply=main_key is None,
    )

    if main_key and main_plan.raw_path:
        curl_fetch(
            f"{args.base_url}/rest/api/2/issue/{main_key}", main_plan.raw_path, token
        )

    child_issues: list[dict[str, Any]] = []
    if main_key and main_plan.raw_path and main_plan.raw_path.exists():
        main_raw = load_json(main_plan.raw_path)
        issue_type = collapse_whitespace(
            ((main_raw.get("fields") or {}).get("issuetype") or {}).get("name")
        )
        if issue_type == "Epic":
            child_search_path = preview_dir / f"{main_key}.children.raw.json"
            curl_fetch(
                f"{args.base_url}/rest/api/2/search",
                child_search_path,
                token,
                extra_args=[
                    "-G",
                    "--data-urlencode",
                    f"jql='Epic Link' = {main_key} OR parent = {main_key}",
                    "--data-urlencode",
                    "maxResults=200",
                ],
            )
            child_issues = load_json(child_search_path).get("issues", [])

    task_dir = md_path.parent / TASKS_DIR_NAME
    child_plans: list[IssuePlan] = []
    planned_md_paths: set[Path] = set()
    if child_issues:
        planned_paths = plan_child_paths(task_dir, child_issues)
        planned_md_paths = {
            path
            for path_plan in planned_paths.values()
            for path in (path_plan.source_path, path_plan.target_path)
            if path is not None
        }
        raw_fetch_jobs: list[tuple[str, Path]] = []
        for issue in child_issues:
            key: str | None = issue.get("key")
            if not key:
                continue
            summary = collapse_whitespace((issue.get("fields") or {}).get("summary"))
            path_plan = planned_paths[key]
            plan = build_issue_plan(
                preview_dir,
                key,
                summary,
                path_plan.target_path,
                None,
                source_md_path=path_plan.source_path,
            )
            child_plans.append(plan)
            if plan.raw_path:
                raw_fetch_jobs.append((key, plan.raw_path))

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.max_workers
        ) as executor:
            futures = [
                executor.submit(
                    curl_fetch,
                    f"{args.base_url}/rest/api/2/issue/{key}",
                    raw_path,
                    token,
                )
                for key, raw_path in raw_fetch_jobs
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()

    if task_dir.exists():
        local_only_raw_jobs: list[tuple[str, Path]] = []
        for md_file in sorted(task_dir.glob("*.md")):
            if md_file in planned_md_paths:
                continue
            file_summary, file_key = parse_markdown_summary_and_issue_key(md_file)
            plan = build_issue_plan(
                preview_dir,
                file_key,
                file_summary,
                md_file,
                None,
                source_md_path=md_file,
                create_on_apply=file_key is None,
                parent_epic_key=main_key,
            )
            child_plans.append(plan)
            if file_key and plan.raw_path:
                local_only_raw_jobs.append((file_key, plan.raw_path))

        if local_only_raw_jobs:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=args.max_workers
            ) as executor:
                futures = [
                    executor.submit(
                        curl_fetch,
                        f"{args.base_url}/rest/api/2/issue/{key}",
                        raw_path,
                        token,
                    )
                    for key, raw_path in local_only_raw_jobs
                ]
                for future in concurrent.futures.as_completed(futures):
                    future.result()

    all_plans = [main_plan, *child_plans]
    summaries: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.max_workers
    ) as executor:
        process_futures = {
            executor.submit(process_issue, plan): plan for plan in all_plans
        }
        for done in concurrent.futures.as_completed(process_futures):
            summaries.append(done.result())
    summaries.sort(key=lambda item: (item.get("md_path", ""), item.get("key", "")))

    if args.apply_jira:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.max_workers
        ) as executor:
            futures = [
                executor.submit(sync_jira, plan, args.project_key) for plan in all_plans
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()
        # Jira create/transition이 merged DSL의 key/status를 바꿀 수 있으므로
        # 갱신된 merged DSL을 다시 읽어 task key/이모지/파일명을 재계산한다.
        summary_by_preview: dict[str, dict[str, Any]] = {
            s["preview_path"]: s for s in summaries if s.get("preview_path")
        }
        refreshed_issues: list[tuple[IssuePlan, dict[str, Any]]] = []
        for plan in all_plans:
            if not plan.merged_dsl_path.exists():
                continue
            merged_dsl = load_json(plan.merged_dsl_path)
            issue = merged_dsl.get("issue", {})
            plan.key = issue.get("key") or plan.key
            plan.summary = issue.get("summary") or plan.summary
            refreshed_issues.append((plan, issue))
            if plan.key:
                verify_path = plan.merged_dsl_path.parent / f"{plan.key}.verify.json"
                if verify_path.exists():
                    verify_data = load_json(verify_path)
                    summary_item = summary_by_preview.get(str(plan.preview_path))
                    if summary_item is not None:
                        summary_item["jira_verify"] = verify_data

        for plan, issue in refreshed_issues:
            if (
                collapse_whitespace(issue.get("issue_type")) == "Task"
                and plan.md_path.parent.name == TASKS_DIR_NAME
                and issue.get("key")
            ):
                plan.md_path = build_child_target_path(
                    plan.md_path.parent,
                    issue.get("key"),
                    issue.get("summary"),
                    issue.get("status"),
                )
            summary_item = summary_by_preview.get(str(plan.preview_path))
            if summary_item is not None:
                summary_item["key"] = plan.key
                summary_item["summary"] = plan.summary
                summary_item["md_path"] = str(plan.md_path)
                preview_text = plan.preview_path.read_text(encoding="utf-8")
                local_change, _ = compare_local_markdown(
                    plan.source_md_path,
                    plan.md_path,
                    preview_text,
                    exists_locally=plan.exists_locally,
                )
                md_dsl = load_json(plan.md_dsl_path)
                merged_dsl = load_json(plan.merged_dsl_path)
                fields_to_write_to_md = compare_md_to_merged_dsl(md_dsl, merged_dsl)
                if fields_to_write_to_md:
                    local_change = (
                        f"{local_change}({','.join(sorted(fields_to_write_to_md))})"
                    )
                summary_item["local_change"] = local_change
                summary_item["fields_to_write_to_md"] = fields_to_write_to_md

    if write_md:
        for plan in all_plans:
            plan.md_path.parent.mkdir(parents=True, exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.max_workers
        ) as executor:
            futures = [
                executor.submit(render_markdown, plan, plan.md_path)
                for plan in all_plans
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()
        cleanup_renamed_markdown_files(all_plans)

    summary_output = (
        Path(args.summary_output).resolve()
        if args.summary_output
        else preview_dir / "jira_sync_summary.json"
    )
    summary_data: dict[str, Any] = {
        "root_md": str(md_path),
        "write_md": write_md,
        "apply_jira": args.apply_jira,
        "max_workers": args.max_workers,
        "dir_renamed": False,
        "issues": summaries,
    }
    write_json(summary_output, summary_data)

    print(summary_output)
    for item in summaries:
        local_change = item["local_change"]
        fields_to_write_to_md = item.get("fields_to_write_to_md") or []
        if fields_to_write_to_md and "(" not in local_change:
            local_change = f"{local_change}({','.join(sorted(fields_to_write_to_md))})"
        jira_sync = ",".join(sorted(item["fields_to_sync_to_jira"])) or "none"
        verify_info = ""
        jira_verify = item.get("jira_verify")
        if jira_verify:
            if jira_verify.get("all_match"):
                verify_info = "\tverify=OK"
            else:
                mismatches = jira_verify.get("mismatches") or []
                verify_info = f"\tverify=MISMATCH({','.join(mismatches)})"
        print(
            f"{item['key'] or '-'}\tmd={local_change}\tjira={jira_sync}"
            f"{verify_info}\tpreview={item['preview_path']}"
        )


if __name__ == "__main__":
    main()
