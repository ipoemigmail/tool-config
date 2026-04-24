#!/usr/bin/env python3

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CHECKLIST_STATUS_NAME_TO_ID = {
    "in progress": "inProgress",
    "done": "done",
    "to do": "toDo",
    "todo": "toDo",
    "none": "none",
}

CHECKLIST_STATUS_ID_TO_NAME = {
    "inProgress": "In Progress",
    "done": "Done",
    "toDo": "To Do",
    "none": None,
}

FORCE_SUFFIX_PATTERN = re.compile(r"\s*\(\(force\)\)\s*$", re.IGNORECASE)

FORCEABLE_ISSUE_FIELDS = {
    "key",
    "url",
    "summary",
    "epic_name",
    "issue_type",
    "status",
    "priority",
    "created_at",
    "assignee",
    "reporter",
    "parent_key",
    "end_date",
    "due_date",
    "labels",
    "components",
    "links",
    "description_markdown",
}

DEFAULT_JIRA_BASE_URL = "https://jira.daumkakao.com"

KCDL_ISSUE_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "reference" / "kcdl_issue_templates.md"
)

DEFAULT_ISSUE_TEMPLATES = {
    "Task": {
        "jira_heading_format": "h2. {name}",
        "section_order": ["목표", "결과", "링크", "배경", "요구사항", "작업 노트"],
        "required_sections": ["목표", "결과"],
    },
    "Epic": {
        "jira_heading_format": "h2. {name}",
        "section_order": [
            "배경",
            "목표",
            "기대 효과",
            "개발범위",
            "요청 정보",
            "연동 정보",
            "관련 링크",
            "완료보고",
        ],
        "required_sections": ["배경", "목표", "기대 효과", "관련 링크"],
    },
}

EPIC_SECTION_ALIASES = {
    "기대 효과": "기대효과",
    "기대효과": "기대효과",
    "작업 범위": "개발범위",
    "작업범위": "개발범위",
    "개발 범위": "개발범위",
    "개발범위": "개발범위",
}


def _normalize_section_key(value: str | None) -> str | None:
    """섹션명을 비교 가능한 키로 정규화한다."""
    collapsed = collapse_whitespace(value)
    return collapsed.casefold() if collapsed else None


def _split_issue_template_sections(text: str) -> dict[str, list[str]]:
    """템플릿 참조 문서에서 이슈 타입별 블록을 분리한다."""
    sections: dict[str, list[str]] = {}
    current_issue_type: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if current_issue_type is not None:
            sections[current_issue_type] = buffer[:]
        buffer = []

    for line in text.splitlines():
        stripped = line.strip()
        issue_type_match = re.match(r"^##\s+(Task|Epic)(?:\s+\(.*\))?\s*$", stripped)
        if issue_type_match:
            flush()
            current_issue_type = issue_type_match.group(1)
            continue
        if stripped.startswith("## "):
            flush()
            current_issue_type = None
            continue
        if current_issue_type is not None:
            buffer.append(line)

    flush()
    return sections


def _extract_code_block(lines: list[str], heading: str) -> list[str]:
    """주어진 소제목 아래 첫 번째 fenced code block을 추출한다."""
    in_target = False
    in_code_block = False
    block: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not in_target:
            if stripped == heading or stripped.startswith(f"{heading} "):
                in_target = True
            continue
        if not in_code_block:
            if stripped == "```":
                in_code_block = True
                continue
            if stripped.startswith("### ") and stripped != heading:
                break
            continue
        if stripped == "```":
            break
        block.append(line)

    return block


def _template_section_keys(template: dict[str, Any]) -> set[str]:
    """템플릿 섹션명을 비교용 키 집합으로 반환한다."""
    keys: set[str] = set()
    for name in template.get("section_order", []):
        normalized = _normalize_section_key(name)
        if normalized is not None:
            keys.add(normalized)
            canonical = _canonical_epic_section_name(normalized)
            canonical_key = _normalize_section_key(canonical)
            if canonical_key is not None:
                keys.add(canonical_key)
    return keys


def _canonical_epic_section_name(section_name: str | None) -> str | None:
    """Epic 섹션의 흔한 별칭을 템플릿 기준 이름으로 통일한다."""
    collapsed = collapse_whitespace(section_name)
    if not collapsed:
        return None
    return EPIC_SECTION_ALIASES.get(collapsed, collapsed)


def _extract_heading_format(lines: list[str], default: dict[str, Any]) -> str:
    """템플릿 블록에서 Jira 헤딩 포맷을 추출한다."""
    for line in lines:
        match = re.match(r"^- Jira wiki 형식:\s*`(.+?)`\s*$", line.strip())
        if not match:
            continue
        format_text = match.group(1).strip()
        if "섹션명" in format_text:
            return format_text.replace("섹션명", "{name}")
    return default["jira_heading_format"]


def _build_heading_regex(format_text: str) -> re.Pattern[str]:
    """`{name}` 플레이스홀더를 가진 포맷 문자열을 정규식으로 변환한다."""
    pattern = re.escape(format_text).replace(re.escape("{name}"), r"(?P<name>.+?)")
    return re.compile(rf"^{pattern}\s*$")


def _extract_section_order(
    lines: list[str], heading_format: str, default: dict[str, Any]
) -> list[str]:
    """템플릿 코드 블록에서 섹션 순서를 추출한다."""
    heading_regex = _build_heading_regex(heading_format)
    section_order: list[str] = []
    for line in _extract_code_block(lines, "### Jira wiki markup"):
        match = heading_regex.match(line.strip())
        if not match:
            continue
        section_name = collapse_whitespace(match.group("name"))
        if section_name:
            section_order.append(section_name)
    return section_order or list(default["section_order"])


def _extract_required_sections(lines: list[str], default: dict[str, Any]) -> list[str]:
    """템플릿 섹션에서 required_sections를 추출한다."""
    for line in lines:
        match = re.match(r"^-\s*required_sections:\s*(.+)$", line.strip())
        if match:
            return [s.strip() for s in match.group(1).split(",") if s.strip()]
    return list(default.get("required_sections", []))


@lru_cache(maxsize=1)
def load_issue_templates() -> dict[str, dict[str, Any]]:
    """이슈 타입별 Description 템플릿을 참조 문서에서 로드한다."""
    templates = {
        issue_type: {
            "jira_heading_format": config["jira_heading_format"],
            "section_order": list(config["section_order"]),
            "required_sections": list(config.get("required_sections", [])),
        }
        for issue_type, config in DEFAULT_ISSUE_TEMPLATES.items()
    }

    try:
        text = KCDL_ISSUE_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError:
        return templates

    for issue_type, lines in _split_issue_template_sections(text).items():
        default = templates.get(issue_type)
        if not default:
            continue
        heading_format = _extract_heading_format(lines, default)
        section_order = _extract_section_order(lines, heading_format, default)
        required_sections = _extract_required_sections(lines, default)
        templates[issue_type] = {
            "jira_heading_format": heading_format,
            "section_order": section_order,
            "required_sections": required_sections,
        }
    return templates


def render_issue_template_heading(text: str, issue_type: str | None) -> str | None:
    """이슈 타입 템플릿에 맞는 Jira 섹션 헤딩을 생성한다."""
    normalized_issue_type = collapse_whitespace(issue_type)
    if not normalized_issue_type:
        return None
    template = load_issue_templates().get(normalized_issue_type)
    if not template:
        return None
    return template["jira_heading_format"].format(name=text)


def _normalize_section_name_from_heading(value: str | None) -> str | None:
    """Jira/Markdown 헤딩에서 섹션명을 원문 그대로 추출한다."""
    if value is None:
        return None
    section_name = collapse_whitespace(jira_inline_to_markdown(value))
    section_name = re.sub(r"^☑️\s*", "", section_name or "").strip()
    section_name = re.sub(r"^\*(?P<name>.+)\*$", r"\g<name>", section_name).strip()
    return collapse_whitespace(section_name)


def parse_issue_template_heading(line: str, issue_type: str | None) -> str | None:
    """Jira 섹션 헤딩을 Markdown 섹션명으로 변환한다."""
    normalized_issue_type = collapse_whitespace(issue_type)
    if not normalized_issue_type:
        return None
    template = load_issue_templates().get(normalized_issue_type)
    if not template:
        return None
    stripped = line.strip()
    match = _build_heading_regex(template["jira_heading_format"]).match(stripped)
    if match:
        return _normalize_section_name_from_heading(match.group("name"))

    for pattern in (
        r"^h[1-6]\.\s+☑️\s+(?P<name>.+?)\s*$",
        r"^#{1,6}\s+☑️\s+(?P<name>.+?)\s*$",
        r"^☑️\s+(?P<name>.+?)\s*$",
    ):
        fallback = re.match(pattern, stripped)
        if fallback:
            return _normalize_section_name_from_heading(fallback.group("name"))
    return None


def parse_markdown_template_heading(line: str, issue_type: str | None) -> str | None:
    """레거시/변형 markdown 템플릿 헤딩을 `## 섹션명` 기준으로 정규화한다."""
    normalized_issue_type = collapse_whitespace(issue_type)
    if not normalized_issue_type:
        return None
    template = load_issue_templates().get(normalized_issue_type)
    if not template:
        return None
    heading = re.match(r"^#{2}\s+(?P<name>.+?)\s*$", line.strip())
    if not heading:
        return None

    section_name = _normalize_section_name_from_heading(heading.group("name"))
    if section_name:
        return section_name
    return None


def _parse_markdown_sections(
    text: str,
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """본문을 preamble과 top-level 섹션 목록으로 분리한다."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_level: int | None = None
    current_lines: list[str] = []

    for line in text.split("\n"):
        match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line.strip())
        if match:
            level = len(match.group(1))
            title = collapse_whitespace(match.group(2))
            if title and (current_level is None or level <= current_level):
                if current_title is None:
                    preamble = current_lines[:]
                else:
                    sections.append((current_title, current_lines[:]))
                current_title = title
                current_level = level
                current_lines = []
                continue
        current_lines.append(line)

    if current_title is None:
        return preamble or current_lines, []
    sections.append((current_title, current_lines[:]))
    return preamble, sections


_LEGACY_HEADING_PATTERNS = [
    re.compile(r"^h[1-6]\.\s+☑️\s+(?P<name>.+?)\s*$"),
    re.compile(r"^#{1,6}\s+☑️\s+(?P<name>.+?)\s*$"),
    re.compile(r"^☑️\s+(?P<name>.+?)\s*$"),
]


def _normalize_template_headings_to_markdown(text: str, issue_type: str | None) -> str:
    """레거시 Jira/MD 템플릿 헤딩(☑️ 기반)만 ## 섹션명으로 변환한다."""
    normalized_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        for pattern in _LEGACY_HEADING_PATTERNS:
            m = pattern.match(stripped)
            if m:
                section_name = _normalize_section_name_from_heading(m.group("name"))
                if section_name:
                    normalized_lines.append(f"## {section_name}")
                    break
        else:
            normalized_lines.append(line)
    return "\n".join(normalized_lines)


def normalize_description_markdown(
    value: str | None, issue_type: str | None = None
) -> str:
    """이슈 타입 템플릿에 맞춰 Description 헤딩만 정규화하고 순서는 유지한다."""
    text = normalize_markdown(value)
    normalized_issue_type = collapse_whitespace(issue_type)
    if not text or not normalized_issue_type:
        return text

    template = load_issue_templates().get(normalized_issue_type)
    if not template:
        return text

    return _normalize_template_headings_to_markdown(text, normalized_issue_type).strip()


# --- Frontmatter helpers ---

_YAML_SPECIAL_LITERALS = {"null", "true", "false", "yes", "no", "on", "off"}


def _needs_quoting(value: str) -> bool:
    """YAML frontmatter에서 문자열 값을 따옴표로 감싸야 하는지 판별한다."""
    if not value:
        return True
    if value.lower() in _YAML_SPECIAL_LITERALS:
        return True
    if value[0] in "-[{*&!|>'\"%@`? ":
        return True
    if "#" in value or ": " in value:
        return True
    if re.match(r"^-?\d+$", value):
        return True
    return False


def _format_scalar(value: Any) -> str:
    """파이썬 값을 YAML 스칼라 문자열로 포맷한다."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if _needs_quoting(value):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        return value
    return str(value)


def _emit_list_item(item: dict[str, Any]) -> list[str]:
    """딕셔너리 리스트 항목을 YAML 줄 목록으로 변환한다."""
    lines: list[str] = []
    is_first = True
    for k, v in item.items():
        if v is None:
            continue
        prefix = "  - " if is_first else "    "
        lines.append(f"{prefix}{k}: {_format_scalar(v)}")
        is_first = False
    return lines


def emit_frontmatter(data: dict[str, Any]) -> str:
    """딕셔너리를 YAML frontmatter 문자열로 변환한다."""
    lines: list[str] = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in value:
                    if isinstance(item, dict):
                        lines.extend(_emit_list_item(item))
                    else:
                        lines.append(f"  - {_format_scalar(item)}")
        else:
            lines.append(f"{key}: {_format_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _parse_scalar_value(value: str) -> Any:
    """YAML 스칼라 문자열을 파이썬 값으로 변환한다."""
    if not value or value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value[0] in "[{":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    try:
        return int(value)
    except ValueError:
        return value


def _parse_multiline_json_value(
    lines: list[str], start_index: int, first_value: str
) -> tuple[Any, int] | None:
    """top-level `key: [` / `key: {` 값을 JSON 컬렉션으로 파싱한다."""
    json_lines = [first_value]
    index = start_index + 1

    while index < len(lines):
        child_line = lines[index].rstrip()
        if child_line.strip() and _indent_level(child_line) == 0:
            break
        if child_line.strip():
            json_lines.append(child_line.strip())
        index += 1
        try:
            return json.loads("\n".join(json_lines)), index
        except json.JSONDecodeError:
            continue
    return None


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """마크다운 텍스트를 frontmatter 문자열과 본문으로 분리한다."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_text = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :]).lstrip("\n")
            return fm_text, body
    return None, text


def _indent_level(line: str) -> int:
    """앞쪽 공백 개수를 반환한다."""
    return len(line) - len(line.lstrip(" "))


def _parse_frontmatter_list(block_lines: list[str]) -> list[Any]:
    """들여쓰기된 YAML 리스트 블록을 파싱한다."""
    items: list[Any] = []
    current_item: dict[str, Any] | None = None

    for line in block_lines:
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current_item is not None:
                items.append(current_item)
                current_item = None
            rest = stripped[2:].strip()
            if ": " in rest:
                key, value = rest.split(": ", 1)
                current_item = {key.strip(): _parse_scalar_value(value.strip())}
            elif rest:
                items.append(_parse_scalar_value(rest))
            continue

        if current_item is not None and ": " in stripped:
            key, value = stripped.split(": ", 1)
            current_item[key.strip()] = _parse_scalar_value(value.strip())

    if current_item is not None:
        items.append(current_item)

    return items


def _parse_frontmatter_mapping(block_lines: list[str]) -> dict[str, Any]:
    """들여쓰기된 YAML 매핑 블록을 파싱한다."""
    mapping: dict[str, Any] = {}
    for line in block_lines:
        stripped = line.strip()
        if not stripped or ": " not in stripped:
            continue
        key, value = stripped.split(": ", 1)
        mapping[key.strip()] = _parse_scalar_value(value.strip())
    return mapping


def parse_frontmatter_yaml(fm_text: str) -> dict[str, Any]:
    """간단한 YAML frontmatter 텍스트를 딕셔너리로 파싱한다."""
    result: dict[str, Any] = {}

    lines = fm_text.split("\n")
    index = 0
    while index < len(lines):
        raw_line = lines[index].rstrip()
        if not raw_line.strip():
            index += 1
            continue
        if _indent_level(raw_line) != 0:
            index += 1
            continue

        stripped = raw_line.strip()
        if ": " in stripped:
            key, value = stripped.split(": ", 1)
            stripped_value = value.strip()
            parsed_multiline = None
            if stripped_value in {"[", "{"}:
                parsed_multiline = _parse_multiline_json_value(
                    lines, index, stripped_value
                )
            if parsed_multiline is not None:
                parsed_value, next_index = parsed_multiline
                result[key.strip()] = parsed_value
                index = next_index
                continue
            result[key.strip()] = _parse_scalar_value(stripped_value)
            index += 1
            continue

        if not stripped.endswith(":"):
            index += 1
            continue

        key = stripped[:-1].strip()
        block_lines: list[str] = []
        index += 1
        while index < len(lines):
            child_line = lines[index].rstrip()
            if child_line.strip() and _indent_level(child_line) == 0:
                break
            block_lines.append(child_line)
            index += 1

        meaningful_lines = [line for line in block_lines if line.strip()]
        if not meaningful_lines:
            result[key] = {}
            continue

        if meaningful_lines[0].lstrip().startswith("- "):
            result[key] = _parse_frontmatter_list(block_lines)
        else:
            result[key] = _parse_frontmatter_mapping(block_lines)

    return result


def load_json(path: str) -> Any:
    """JSON 파일을 읽어 파이썬 객체로 반환한다."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str, data: Any) -> None:
    """파이썬 객체를 JSON 파일로 저장한다."""
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def collapse_whitespace(value: str | None) -> str | None:
    """연속 공백을 하나로 합치고 양끝 공백을 제거한다."""
    if value is None:
        return None
    collapsed = re.sub(r"\s+", " ", str(value)).strip()
    return collapsed or None


def build_issue_browse_url(
    issue_key: str | None, base_url: str = DEFAULT_JIRA_BASE_URL
) -> str | None:
    """이슈 키가 있으면 Jira browse 전체 URL을 생성한다."""
    key = collapse_whitespace(issue_key)
    normalized_base_url = collapse_whitespace(base_url)
    if not key or not normalized_base_url:
        return None
    return f"{normalized_base_url.rstrip('/')}/browse/{key}"


def normalize_markdown(value: str | None) -> str:
    """마크다운 텍스트의 줄바꿈을 정규화하고 양끝 공백을 제거한다."""
    if value is None:
        return ""
    text = value.replace("\r\n", "\n").strip()
    return text


def strip_force_suffix(value: Any) -> tuple[Any, bool]:
    """문자열 끝의 `(force)` 접미사를 제거하고 force 여부를 반환한다."""
    if not isinstance(value, str):
        return value, False
    stripped = value.strip()
    if not stripped:
        return value, False
    if not FORCE_SUFFIX_PATTERN.search(stripped):
        return value, False
    cleaned = FORCE_SUFFIX_PATTERN.sub("", stripped).strip()
    return cleaned, True


def extract_force_from_keys(
    mapping: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    """dict의 키에서 ``((force))`` 접미사를 제거하고 force된 키 집합을 반환한다.

    Returns:
        (cleaned_dict, forced_keys) – *forced_keys* 는 접미사를 제거한 뒤의 키 이름 집합.
    """
    cleaned: dict[str, Any] = {}
    forced_keys: set[str] = set()
    for key, value in mapping.items():
        clean_key, is_forced = strip_force_suffix(key)
        if is_forced:
            clean_key = clean_key.strip() if isinstance(clean_key, str) else clean_key
            forced_keys.add(clean_key)
        cleaned[clean_key if is_forced else key] = value
    return cleaned, forced_keys


def normalize_force_fields(value: Any) -> list[str]:
    """MD 우선 머지 대상으로 지정된 이슈 필드 목록을 정규화한다."""
    if value in (None, "", []):
        return []
    if not isinstance(value, list):
        raise ValueError("md_force_fields must be an array")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        field_name = collapse_whitespace(item)
        if not field_name or field_name not in FORCEABLE_ISSUE_FIELDS:
            continue
        if field_name in seen:
            continue
        normalized.append(field_name)
        seen.add(field_name)
    return normalized


def jira_inline_to_markdown(text: str) -> str:
    """Jira 인라인 마크업(@멘션, 링크, 코드)을 마크다운으로 변환한다."""
    text = re.sub(r"\[~([A-Za-z0-9._-]+)\]", r"@\1", text)
    text = re.sub(r"\[([^\]|]+)\|([^\]]+)\]", r"[\1](\2)", text)
    text = re.sub(r"\{\{([^{}]+)\}\}", r"`\1`", text)
    return text


def _convert_jira_table_header(line: str) -> str:
    """Jira 테이블 헤더 행(||)을 Markdown 테이블 헤더로 변환한다."""
    cells = [c.strip() for c in line.strip().strip("|").split("||")]
    cells = [c for c in cells if c]
    if not cells:
        return ""
    header = "| " + " | ".join(jira_inline_to_markdown(c) for c in cells) + " |"
    separator = "| " + " | ".join("---" for _ in cells) + " |"
    return f"{header}\n{separator}"


def _is_jira_table_header(line: str) -> bool:
    """Jira 테이블 헤더 행(||)인지 판별한다."""
    stripped = line.strip()
    return stripped.startswith("||") and "||" in stripped[2:]


def jira_markup_to_markdown(value: str | None, issue_type: str | None = None) -> str:
    """Jira wiki 마크업 전체를 마크다운으로 변환한다."""
    if value is None:
        return ""

    lines = value.replace("\r\n", "\n").split("\n")
    output: list[str] = []
    in_code_block = False
    code_language = ""

    for line in lines:
        stripped = line.strip()
        code_match = re.match(r"^\{code(?::([^}]+))?\}$", stripped)
        if code_match:
            if in_code_block:
                output.append("```")
                in_code_block = False
                code_language = ""
            else:
                code_language = (code_match.group(1) or "").strip()
                output.append(f"```{code_language}".rstrip())
                in_code_block = True
            continue

        if in_code_block:
            output.append(line)
            continue

        template_heading = parse_issue_template_heading(stripped, issue_type)
        if template_heading:
            output.append(f"## {template_heading}")
            continue

        heading = re.match(r"^h([1-6])\.\s+(.*)$", stripped)
        bullet = re.match(r"^(\*+)\s+(.*)$", stripped)
        ordered = re.match(r"^(#+)\s+(.*)$", stripped)

        if heading:
            level = int(heading.group(1))
            output.append(f"{'#' * level} {jira_inline_to_markdown(heading.group(2))}")
            continue

        if bullet:
            indent = "  " * (len(bullet.group(1)) - 1)
            output.append(f"{indent}- {jira_inline_to_markdown(bullet.group(2))}")
            continue

        if ordered:
            indent = "  " * (len(ordered.group(1)) - 1)
            output.append(f"{indent}1. {jira_inline_to_markdown(ordered.group(2))}")
            continue

        if _is_jira_table_header(stripped):
            converted = _convert_jira_table_header(stripped)
            if converted:
                output.append(converted)
                continue

        output.append(jira_inline_to_markdown(line))

    return normalize_description_markdown("\n".join(output), issue_type)


def normalize_user(value: Any) -> dict[str, str | None] | None:
    """다양한 형태의 사용자 값을 username/display_name 딕셔너리로 정규화한다."""
    if not value:
        return None
    if isinstance(value, str):
        return {"username": collapse_whitespace(value), "display_name": None}
    if not isinstance(value, dict):
        raise ValueError(f"Invalid user value: {value!r}")
    username = collapse_whitespace(value.get("username") or value.get("name"))
    display_name = collapse_whitespace(
        value.get("display_name") or value.get("displayName")
    )
    if not username and not display_name:
        return None
    return {"username": username, "display_name": display_name}


def normalize_link(value: Any) -> dict[str, str | None]:
    """연관이슈 링크 값을 direction/relationship/key/summary 딕셔너리로 정규화한다."""
    if not isinstance(value, dict):
        raise ValueError(f"Invalid link value: {value!r}")
    return {
        "direction": collapse_whitespace(value.get("direction")),
        "relationship": collapse_whitespace(value.get("relationship")),
        "key": collapse_whitespace(value.get("key")),
        "summary": collapse_whitespace(value.get("summary")),
    }


def normalize_status_fields(item: dict[str, Any]) -> tuple[str | None, str | None]:
    """체크리스트 항목의 status_name과 status_id를 상호 보완하여 정규화한다."""
    status_name = collapse_whitespace(item.get("status_name"))
    status_id = collapse_whitespace(item.get("status_id"))

    if status_name and not status_id:
        status_id = CHECKLIST_STATUS_NAME_TO_ID.get(status_name.lower())
    if status_id and not status_name:
        status_name = CHECKLIST_STATUS_ID_TO_NAME.get(status_id, status_id)
    if status_id == "none":
        status_name = None
    return status_name, status_id


def normalize_checklist_item(value: Any) -> dict[str, Any]:
    """체크리스트 항목 딕셔너리를 DSL 표준 형식으로 정규화한다."""
    if not isinstance(value, dict):
        raise ValueError(f"Invalid checklist item: {value!r}")
    status_name, status_id = normalize_status_fields(value)
    raw_id = value.get("id")
    item_id = int(raw_id) if raw_id is not None else None
    return {
        "id": item_id,
        "name": collapse_whitespace(value.get("name")) or "",
        "checked": bool(value.get("checked", False)),
        "completed_date": collapse_whitespace(value.get("completed_date")),
        "linked_issue_key": collapse_whitespace(
            value.get("linked_issue_key") or value.get("ticket_key")
        ),
        "assignee_username": collapse_whitespace(value.get("assignee_username")),
        "status_name": status_name,
        "status_id": status_id,
    }


def normalize_checklist(items: Any) -> list[dict[str, Any]]:
    """체크리스트 배열의 각 항목을 정규화하고 이름이 비어있는 항목을 제거한다."""
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError("Checklist must be an array")
    normalized = [normalize_checklist_item(item) for item in items]
    return [item for item in normalized if item["name"]]


def normalize_string_list(value: Any) -> list[str]:
    """문자열 배열을 정규화한다. 빈 문자열과 중복을 제거한다."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Expected an array of strings")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = collapse_whitespace(item)
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def normalize_issue(issue: Any) -> dict[str, Any]:
    """이슈 딕셔너리를 DSL 표준 형식으로 정규화한다."""
    if not isinstance(issue, dict):
        raise ValueError("issue must be an object")
    issue_key = collapse_whitespace(issue.get("key"))
    summary = collapse_whitespace(issue.get("summary")) or ""
    epic_name = collapse_whitespace(issue.get("epic_name"))
    issue_type = collapse_whitespace(issue.get("issue_type"))
    issue_url = collapse_whitespace(issue.get("url")) or build_issue_browse_url(
        issue_key
    )
    if issue_type == "Epic" and not epic_name:
        epic_name = summary or None
    return {
        "key": issue_key,
        "url": issue_url,
        "summary": summary,
        "epic_name": epic_name,
        "issue_type": issue_type,
        "status": collapse_whitespace(issue.get("status")),
        "priority": collapse_whitespace(issue.get("priority")),
        "created_at": collapse_whitespace(issue.get("created_at")),
        "assignee": normalize_user(issue.get("assignee")),
        "reporter": normalize_user(issue.get("reporter")),
        "parent_key": collapse_whitespace(issue.get("parent_key")),
        "end_date": collapse_whitespace(issue.get("end_date")),
        "due_date": collapse_whitespace(issue.get("due_date")),
        "labels": normalize_string_list(issue.get("labels")),
        "components": normalize_string_list(issue.get("components")),
        "links": [normalize_link(link) for link in issue.get("links", [])],
    }


def normalize_issue_dsl(data: Any) -> dict[str, Any]:
    """전체 DSL 객체를 검증하고 정규화한다."""
    if not isinstance(data, dict):
        raise ValueError("DSL root must be an object")
    checklists = data.get("checklists") or {}
    issue = normalize_issue(data.get("issue") or {})
    issue_dsl = {
        "version": 1,
        "issue": issue,
        "description_markdown": normalize_description_markdown(
            data.get("description_markdown"), issue.get("issue_type")
        ),
        "md_force_fields": normalize_force_fields(data.get("md_force_fields")),
        "checklists": {
            "todo": normalize_checklist(checklists.get("todo")),
            "acceptance_criteria": normalize_checklist(
                checklists.get("acceptance_criteria")
            ),
        },
    }
    validate_issue_dsl(issue_dsl)
    return issue_dsl


def has_meaningful_md_content(issue_dsl: dict[str, Any]) -> bool:
    """MD에서 파싱한 DSL에 실질적인 내용이 있는지 확인한다."""
    issue = issue_dsl["issue"]
    metadata_keys = (
        "epic_name",
        "issue_type",
        "status",
        "priority",
        "created_at",
        "assignee",
        "reporter",
        "parent_key",
    )
    if any(issue.get(key) for key in metadata_keys):
        return True
    if issue.get("links"):
        return True
    if issue.get("labels"):
        return True
    if issue.get("components"):
        return True
    if issue_dsl.get("description_markdown"):
        return True
    if issue_dsl["checklists"]["todo"]:
        return True
    if issue_dsl["checklists"]["acceptance_criteria"]:
        return True
    return False


def validate_issue_dsl(data: dict[str, Any]) -> None:
    """DSL 객체의 필수 필드와 구조를 검증한다."""
    if data.get("version") != 1:
        raise ValueError("DSL version must be 1")
    if not data["issue"]["summary"] and not data["issue"]["key"]:
        raise ValueError("issue.summary or issue.key is required")
    for section in ("todo", "acceptance_criteria"):
        if section not in data["checklists"]:
            raise ValueError(f"Missing checklist section: {section}")


def preserve_optional_fields(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *,
    field_names: tuple[str, ...] = ("completed_date", "linked_issue_key"),
) -> dict[str, Any]:
    """primary 항목에 없는 선택 필드를 secondary에서 보존한다."""
    merged = dict(primary)
    for field_name in field_names:
        if not merged.get(field_name) and secondary.get(field_name):
            merged[field_name] = secondary.get(field_name)
    return merged


def merge_text(md_value: str | None, jira_value: str | None) -> str | None:
    """두 텍스트 값 중 비어있지 않은 첫 번째 값을 반환한다."""
    return collapse_whitespace(md_value) or collapse_whitespace(jira_value)


def merge_optional(md_value: Any, jira_value: Any) -> Any:
    """두 값 중 비어있지 않은 첫 번째 값을 반환한다."""
    return md_value if md_value not in (None, "", []) else jira_value


def checklist_key(item: dict[str, Any]) -> int | None:
    """체크리스트 항목의 머지 키(id)를 반환한다."""
    return item.get("id")


def merge_checklist_section(
    md_items: list[dict[str, Any]], jira_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """id 기준으로 MD와 Jira 체크리스트를 머지한다. Jira가 우선이다."""
    jira_map: dict[int, dict[str, Any]] = {}
    for item in jira_items:
        item_id = checklist_key(item)
        if item_id is not None:
            jira_map[item_id] = item

    md_map: dict[int, dict[str, Any]] = {}
    for item in md_items:
        item_id = checklist_key(item)
        if item_id is not None:
            md_map[item_id] = item
        # id가 없는 항목은 머지 결과에서 삭제한다

    merged_items: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    # Jira order is primary; Jira wins for all matched items
    for item in jira_items:
        item_id = checklist_key(item)
        if item_id is not None:
            merged = item.copy()
            # Preserve linked_issue_key / completed_date from MD if Jira lacks them
            md_item = md_map.get(item_id)
            if md_item:
                merged = preserve_optional_fields(merged, md_item)
            merged_items.append(merged)
            seen_ids.add(item_id)

    # MD items with id not present in Jira (edge case)
    for item_id, item in md_map.items():
        if item_id not in seen_ids:
            merged_items.append(item.copy())

    return merged_items


def render_user_label(user: dict[str, str | None] | None) -> str:
    """사용자 딕셔너리를 표시용 문자열로 변환한다."""
    if not user:
        return "-"
    if user.get("display_name"):
        return user["display_name"] or "-"
    if user.get("username"):
        return user["username"] or "-"
    return "-"


def checklist_item_to_jira(
    item: dict[str, Any],
    item_id: int,
    valid_statuses: set[str] | None = None,
) -> dict[str, Any]:
    """DSL 체크리스트 항목을 Jira API 페이로드 형식으로 변환한다."""
    if valid_statuses is None:
        valid_statuses = {"inProgress"}
    payload: dict[str, Any] = {
        "name": item["name"],
        "checked": item["checked"],
        "mandatory": True,
        "id": item.get("id") or item_id,
        "rank": item_id - 1,
        "assigneeIds": [item["assignee_username"]]
        if item.get("assignee_username")
        else [],
        "isHeader": False,
        "statusId": "none",
    }
    # checked 항목은 status를 강제하지 않는다 (statusId: "none")
    if not item["checked"]:
        status_name, status_id = normalize_status_fields(item)
        if status_id and status_id != "none" and status_id in valid_statuses:
            payload["status"] = {"name": status_name, "id": status_id}
            payload["statusId"] = status_id
    # linked_issue_key가 있으면 linkedIssueKey로 전달한다
    linked_key = item.get("linked_issue_key")
    if linked_key:
        payload["linkedIssueKey"] = linked_key
    return payload
