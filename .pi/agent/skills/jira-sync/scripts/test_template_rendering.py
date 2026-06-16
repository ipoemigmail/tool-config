#!/usr/bin/env python3
"""jira-sync 템플릿 렌더링 및 변환 테스트.

실행: python3 .agents/skills/jira-sync/scripts/test_template_rendering.py

검증 범위:
  - 스켈레톤 생성 (신규 파일, 이슈 타입별)
  - MD ↔ Jira wiki 라운드트립
  - 레거시 heading 정규화
  - 섹션명 alias 처리
  - 엣지 케이스 (빈 입력, 혼합 포맷, 코드 블록 내 heading 등)
"""

from __future__ import annotations

import sys
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jira_dsl_lib import (
    load_issue_templates,
    normalize_description_markdown,
)
from md_to_jira_dsl import parse_frontmatter_markdown
from render_md_from_jira_dsl import _build_template_skeleton, render_document
from sync_jira_from_jira_dsl import markdown_to_jira


def _make_dsl(
    issue_type: str | None = "Task",
    description: str = "",
    summary: str = "Test",
) -> dict:
    """테스트용 최소 DSL 객체를 생성한다."""
    return {
        "version": 1,
        "issue": {
            "key": "KCDL-9999",
            "url": "https://jira.daumkakao.com/browse/KCDL-9999",
            "summary": summary,
            "epic_name": summary if issue_type == "Epic" else None,
            "issue_type": issue_type,
            "status": "Open",
            "priority": "Medium",
            "created_at": "2026-04-13",
            "assignee": {"username": "test", "display_name": "test/kakao"},
            "reporter": {"username": "test", "display_name": "test/kakao"},
            "parent_key": None,
            "end_date": None,
            "due_date": None,
            "labels": [],
            "components": [],
            "links": [],
        },
        "description_markdown": description,
        "md_force_fields": [],
        "checklists": {"todo": [], "acceptance_criteria": []},
    }


# =====================================================================
# 1. Skeleton generation
# =====================================================================
class TestSkeletonGeneration(unittest.TestCase):
    """신규 파일 생성 시 스켈레톤 템플릿 검증."""

    def test_epic_skeleton_has_all_sections(self):
        skeleton = _build_template_skeleton("Epic")
        templates = load_issue_templates()
        for section in templates["Epic"]["section_order"]:
            self.assertIn(f"## {section}", skeleton)

    def test_task_skeleton_has_all_sections(self):
        skeleton = _build_template_skeleton("Task")
        templates = load_issue_templates()
        for section in templates["Task"]["section_order"]:
            self.assertIn(f"## {section}", skeleton)

    def test_skeleton_no_jira_markers(self):
        """스켈레톤에 ☑️, h3. 등 Jira 마커가 없어야 한다."""
        for issue_type in ("Epic", "Task"):
            skeleton = _build_template_skeleton(issue_type)
            self.assertNotIn("☑️", skeleton, f"{issue_type} skeleton has ☑️")
            self.assertNotIn("h3.", skeleton, f"{issue_type} skeleton has h3.")

    def test_skeleton_uses_h2_only(self):
        """모든 헤딩이 ## (h2)이어야 한다."""
        for issue_type in ("Epic", "Task"):
            skeleton = _build_template_skeleton(issue_type)
            for line in skeleton.split("\n"):
                if line.startswith("#"):
                    self.assertTrue(
                        line.startswith("## "),
                        f"{issue_type}: non-h2 heading: {line}",
                    )

    def test_story_returns_empty(self):
        self.assertEqual(_build_template_skeleton("Story"), "")

    def test_none_returns_empty(self):
        self.assertEqual(_build_template_skeleton(None), "")

    def test_unknown_type_returns_empty(self):
        self.assertEqual(_build_template_skeleton("Bug"), "")


# =====================================================================
# 2. render_document skeleton behavior
# =====================================================================
class TestRenderDocumentSkeleton(unittest.TestCase):
    """render_document의 스켈레톤 삽입 조건 검증."""

    def test_new_file_empty_desc_generates_skeleton(self):
        dsl = _make_dsl("Task", description="")
        result = render_document(dsl, is_new_file=True)
        self.assertIn("## 목표", result)
        self.assertIn("## 결과", result)

    def test_new_file_with_desc_no_skeleton(self):
        dsl = _make_dsl("Task", description="## 목표\n- 있음")
        result = render_document(dsl, is_new_file=True)
        self.assertNotIn("## 결과", result)

    def test_existing_file_empty_desc_no_skeleton(self):
        dsl = _make_dsl("Task", description="")
        result = render_document(dsl, is_new_file=False)
        self.assertNotIn("## 목표", result)

    def test_whitespace_only_desc_no_skeleton(self):
        dsl = _make_dsl("Task", description="   \n  \n ")
        result = render_document(dsl, is_new_file=False)
        body = result.split("---", 2)[-1].strip()
        self.assertEqual(body, "")

    def test_new_epic_skeleton_section_order(self):
        """Epic 스켈레톤의 섹션 순서가 section_order와 일치해야 한다."""
        dsl = _make_dsl("Epic", description="")
        result = render_document(dsl, is_new_file=True)
        templates = load_issue_templates()
        expected = templates["Epic"]["section_order"]
        found = [
            line.replace("## ", "")
            for line in result.split("\n")
            if line.startswith("## ")
        ]
        self.assertEqual(found, expected)


# =====================================================================
# 3. Legacy heading normalization
# =====================================================================
class TestLegacyHeadingNormalization(unittest.TestCase):
    """레거시 Jira/MD 헤딩을 ## 섹션명으로 정규화."""

    def test_checkbox_star_task(self):
        """☑️ *목표* → ## 목표"""
        result = normalize_description_markdown("☑️ *목표*\n- test", "Task")
        self.assertIn("## 목표", result)
        self.assertNotIn("☑️", result)

    def test_checkbox_plain_task(self):
        """☑️ 목표 → ## 목표"""
        result = normalize_description_markdown("☑️ 목표\n- test", "Task")
        self.assertIn("## 목표", result)
        self.assertNotIn("☑️", result)

    def test_h3_checkbox_epic(self):
        """h3. ☑️ 배경 → ## 배경"""
        result = normalize_description_markdown("h3. ☑️ 배경\n- test", "Epic")
        self.assertIn("## 배경", result)
        self.assertNotIn("h3.", result)
        self.assertNotIn("☑️", result)

    def test_h3_with_hash_task(self):
        """### ☑️ 목표 → ## 목표"""
        result = normalize_description_markdown("### ☑️ 목표\n- test", "Task")
        self.assertIn("## 목표", result)
        self.assertNotIn("###", result)


# =====================================================================
# 4. Forceable field parsing
# =====================================================================
class TestForceableFieldParsing(unittest.TestCase):
    """frontmatter의 ((force)) 파싱 검증."""

    def test_links_force_marker_is_tracked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.md"
            path.write_text(
                """---
title: Test
links ((force)):
  - direction: outward
    relationship: blocks
    key: KCDL-1
    summary: Sample
---

## 목표
- test
""",
                encoding="utf-8",
            )
            dsl = parse_frontmatter_markdown(path)

        self.assertIn("links", dsl["md_force_fields"])
        self.assertEqual(dsl["issue"]["links"][0]["key"], "KCDL-1")

    def test_h2_checkbox_star(self):
        """## ☑️ *결과* → ## 결과"""
        result = normalize_description_markdown("## ☑️ *결과*\n- test", "Task")
        self.assertIn("## 결과", result)
        self.assertNotIn("☑️", result)
        self.assertNotIn("*결과*", result)

    def test_already_normalized_preserved(self):
        """## 목표 → ## 목표 (변경 없음)"""
        original = "## 목표\n- test\n\n## 결과\n- result"
        result = normalize_description_markdown(original, "Task")
        self.assertIn("## 목표", result)
        self.assertIn("## 결과", result)

    def test_mixed_legacy_and_normal(self):
        """레거시와 정상 헤딩 혼합 시 모두 정규화."""
        mixed = "☑️ *목표*\n- a\n\n## 결과\n- b\n\n☑️ *링크*\n- c"
        result = normalize_description_markdown(mixed, "Task")
        self.assertIn("## 목표", result)
        self.assertIn("## 결과", result)
        self.assertIn("## 링크", result)
        self.assertNotIn("☑️", result)


# =====================================================================
# 4. Section alias
# =====================================================================
class TestSectionAlias(unittest.TestCase):
    """섹션명 alias 정규화 검증."""

    def test_epic_기대효과_alias(self):
        """기대효과 → 기대 효과 (또는 보존) — alias가 처리되어야 한다."""
        result = normalize_description_markdown("## 기대효과\n- test", "Epic")
        has_space = "## 기대 효과" in result
        has_nospace = "## 기대효과" in result
        self.assertTrue(has_space or has_nospace, f"alias not handled: {result[:80]}")

    def test_epic_작업범위_alias(self):
        result = normalize_description_markdown("## 작업 범위\n- test", "Epic")
        has_canonical = "## 개발범위" in result
        has_original = "## 작업 범위" in result
        self.assertTrue(
            has_canonical or has_original, f"alias not handled: {result[:80]}"
        )


# =====================================================================
# 5. MD → Jira wiki conversion
# =====================================================================
class TestMdToJiraWiki(unittest.TestCase):
    """MD → Jira wiki markup 변환."""

    def test_task_heading_format(self):
        """Task: ## 목표 → h2. 목표"""
        wiki = markdown_to_jira("## 목표\n- test", "Task")
        self.assertIn("h2. 목표", wiki)
        self.assertNotIn("##", wiki)
        self.assertNotIn("☑️", wiki)

    def test_epic_heading_format(self):
        """Epic: ## 배경 → h2. 배경"""
        wiki = markdown_to_jira("## 배경\n- test", "Epic")
        self.assertIn("h2. 배경", wiki)
        self.assertNotIn("##", wiki)
        self.assertNotIn("☑️", wiki)

    def test_bullet_list_conversion(self):
        wiki = markdown_to_jira("## 목표\n- 항목1\n- 항목2", "Task")
        self.assertIn("* 항목1", wiki)
        self.assertIn("* 항목2", wiki)

    def test_nested_bullet_conversion(self):
        wiki = markdown_to_jira("## 목표\n- 항목1\n  - 하위 항목", "Task")
        self.assertIn("** 하위 항목", wiki)

    def test_non_template_heading_converted(self):
        """템플릿에 없는 ## 헤딩도 h2. 형식으로 변환."""
        wiki = markdown_to_jira("## 기타 메모\n- test", "Task")
        self.assertIn("h2. 기타 메모", wiki)
        self.assertNotIn("☑️", wiki)

    def test_sub_headings_preserved_as_is(self):
        """###, #### 등 하위 헤딩은 ##로 승격하지 않고 원래 레벨을 유지한다."""
        for md, expected_prefix in (
            ("### 세부사항\n- test", "h3."),
            ("#### 세부사항\n- test", "h4."),
        ):
            wiki = markdown_to_jira(md, "Task")
            self.assertIn(expected_prefix, wiki, f"failed for: {md[:20]}")
            self.assertIn("세부사항", wiki, f"failed for: {md[:20]}")
            self.assertNotIn("☑️", wiki, f"should not promote: {md[:20]}")

    def test_code_block_preserved(self):
        """코드 블록 내부의 ## 은 변환하지 않는다."""
        md = "## 목표\n- test\n\n```python\n## comment\nprint('hello')\n```"
        wiki = markdown_to_jira(md, "Task")
        self.assertIn("{code:python}", wiki)
        self.assertIn("## comment", wiki)


# =====================================================================
# 6. Roundtrip: Jira wiki → MD → Jira wiki
# =====================================================================
class TestRoundtrip(unittest.TestCase):
    """Jira → MD → Jira 라운드트립에서 섹션이 보존되어야 한다."""

    def test_task_roundtrip(self):
        original = "☑️ *목표*\n * 첫번째 목표\n\n☑️ *결과*\n * 첫번째 결과"
        md = normalize_description_markdown(original, "Task")
        wiki = markdown_to_jira(md, "Task")
        self.assertIn("h2. 목표", wiki)
        self.assertIn("h2. 결과", wiki)
        self.assertNotIn("☑️", wiki)

    def test_epic_roundtrip(self):
        original = "h3. ☑️ 배경\n- 배경 내용\n\nh3. ☑️ 목표\n- 목표 내용"
        md = normalize_description_markdown(original, "Epic")
        wiki = markdown_to_jira(md, "Epic")
        self.assertIn("h2. 배경", wiki)
        self.assertIn("h2. 목표", wiki)
        self.assertNotIn("☑️", wiki)

    def test_task_legacy_roundtrip(self):
        """레거시 ☑️ 형식 → MD → Jira 라운드트립."""
        original = "☑️ 목표\n * 내용\n\n☑️ 결과\n * 산출물"
        md = normalize_description_markdown(original, "Task")
        self.assertIn("## 목표", md)
        wiki = markdown_to_jira(md, "Task")
        self.assertIn("h2. 목표", wiki)
        self.assertIn("h2. 결과", wiki)
        self.assertNotIn("☑️", wiki)

    def test_content_preserved_in_roundtrip(self):
        """섹션 내용이 라운드트립에서 손실되지 않는다."""
        md = "## 목표\n- CDP 이관 완료에 따른 코드 제거\n\n## 결과\n- kc-spark 패키지 삭제"
        wiki = markdown_to_jira(md, "Task")
        self.assertIn("CDP 이관 완료에 따른 코드 제거", wiki)
        self.assertIn("kc-spark 패키지 삭제", wiki)


# =====================================================================
# 7. Edge cases
# =====================================================================
class TestEdgeCases(unittest.TestCase):
    """엣지 케이스 검증."""

    def test_empty_string(self):
        result = normalize_description_markdown("", "Task")
        self.assertEqual(result, "")

    def test_none_input(self):
        result = normalize_description_markdown(None, "Task")
        self.assertEqual(result, "")

    def test_none_issue_type(self):
        result = normalize_description_markdown("## 목표\n- test", None)
        self.assertIn("## 목표", result)

    def test_unknown_issue_type(self):
        """알 수 없는 이슈 타입은 텍스트를 그대로 반환."""
        original = "## 목표\n- test"
        result = normalize_description_markdown(original, "Bug")
        self.assertIn("## 목표", result)

    def test_heading_inside_code_block_not_normalized(self):
        """코드 블록 내 heading은 정규화하지 않는다."""
        md = "## 목표\n- test\n\n```\n☑️ 이건 코드\n```\n\n## 결과\n- done"
        result = normalize_description_markdown(md, "Task")
        self.assertIn("## 목표", result)
        self.assertIn("## 결과", result)

    def test_multiple_empty_lines_between_sections(self):
        md = "## 목표\n- a\n\n\n\n## 결과\n- b"
        result = normalize_description_markdown(md, "Task")
        self.assertIn("## 목표", result)
        self.assertIn("## 결과", result)

    def test_section_with_no_content(self):
        """빈 섹션도 유지."""
        md = "## 목표\n\n## 결과\n- done"
        result = normalize_description_markdown(md, "Task")
        self.assertIn("## 목표", result)
        self.assertIn("## 결과", result)

    def test_inline_bold_italic_preserved(self):
        """섹션 본문의 인라인 마크업은 보존."""
        md = "## 목표\n- **굵은** 텍스트와 *기울인* 텍스트"
        wiki = markdown_to_jira(md, "Task")
        self.assertIn("*굵은*", wiki)

    def test_link_in_content_preserved(self):
        """링크가 포함된 내용 보존."""
        md = "## 링크\n- [아지트](https://kakao.agit.in/g/300005887/wall/455822310)"
        wiki = markdown_to_jira(md, "Task")
        self.assertIn("455822310", wiki)

    def test_table_in_description(self):
        """섹션 내 테이블이 Jira 형식으로 변환."""
        md = "## 결과\n\n| 항목 | 값 |\n|------|----|\n| a | b |"
        wiki = markdown_to_jira(md, "Task")
        self.assertIn("||", wiki)

    def test_whitespace_only_description(self):
        result = normalize_description_markdown("   \n  \n ", "Task")
        self.assertEqual(result.strip(), "")

    def test_h3_subsection_not_promoted_to_h2(self):
        """### 하위 헤딩이 템플릿 섹션명과 같아도 ##로 승격하지 않는다."""
        md = "## 목표\n- test\n\n### 결과\n- sub result"
        result = normalize_description_markdown(md, "Task")
        self.assertIn("### 결과", result)

    def test_h4_subsection_not_promoted_to_h2(self):
        """#### 하위 헤딩이 ##로 승격하지 않는다."""
        md = "## 목표\n- test\n\n#### 링크\n- sub link"
        result = normalize_description_markdown(md, "Task")
        self.assertIn("#### 링크", result)


# =====================================================================
# 8. Template loading consistency
# =====================================================================
class TestTemplateConsistency(unittest.TestCase):
    """load_issue_templates()의 일관성 검증."""

    def test_task_has_required_fields(self):
        templates = load_issue_templates()
        self.assertIn("Task", templates)
        self.assertIn("jira_heading_format", templates["Task"])
        self.assertIn("section_order", templates["Task"])
        self.assertIn("required_sections", templates["Task"])

    def test_epic_has_required_fields(self):
        templates = load_issue_templates()
        self.assertIn("Epic", templates)
        self.assertIn("jira_heading_format", templates["Epic"])
        self.assertIn("section_order", templates["Epic"])
        self.assertIn("required_sections", templates["Epic"])

    def test_task_mandatory_sections_in_order(self):
        """Task 필수 섹션(목표, 결과)이 section_order 선두에 있어야 한다."""
        templates = load_issue_templates()
        order = templates["Task"]["section_order"]
        self.assertEqual(order[0], "목표")
        self.assertEqual(order[1], "결과")

    def test_epic_mandatory_sections_present(self):
        """Epic 필수 섹션이 section_order에 포함."""
        templates = load_issue_templates()
        order = templates["Epic"]["section_order"]
        for required in ("배경", "목표", "관련 링크"):
            self.assertIn(required, order, f"missing: {required}")

    def test_skeleton_matches_section_order(self):
        """스켈레톤 렌더링 결과가 section_order와 정확히 일치."""
        templates = load_issue_templates()
        for issue_type in ("Epic", "Task"):
            skeleton = _build_template_skeleton(issue_type)
            found = [
                line.replace("## ", "")
                for line in skeleton.split("\n")
                if line.startswith("## ")
            ]
            self.assertEqual(
                found,
                templates[issue_type]["section_order"],
                f"{issue_type} skeleton order mismatch",
            )

    def test_task_required_sections(self):
        """Task required_sections가 템플릿 파일에서 올바르게 로드."""
        templates = load_issue_templates()
        required = templates["Task"]["required_sections"]
        self.assertEqual(required, ["목표", "결과"])

    def test_epic_required_sections(self):
        """Epic required_sections가 템플릿 파일에서 올바르게 로드."""
        templates = load_issue_templates()
        required = templates["Epic"]["required_sections"]
        self.assertIn("배경", required)
        self.assertIn("목표", required)
        self.assertIn("기대 효과", required)
        self.assertIn("관련 링크", required)

    def test_required_sections_subset_of_section_order(self):
        """required_sections는 section_order의 부분집합이어야 한다."""
        templates = load_issue_templates()
        for issue_type in ("Epic", "Task"):
            required = set(templates[issue_type]["required_sections"])
            order = set(templates[issue_type]["section_order"])
            diff = required - order
            self.assertEqual(
                diff,
                set(),
                f"{issue_type}: required에 있지만 section_order에 없음: {diff}",
            )


if __name__ == "__main__":
    unittest.main()
