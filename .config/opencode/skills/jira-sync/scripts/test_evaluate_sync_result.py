#!/usr/bin/env python3
"""evaluate_sync_result.py의 단위 테스트.

실행: python3 .agents/skills/jira-sync/scripts/test_evaluate_sync_result.py

검증 범위:
  - Jira 마커 잔존 검출
  - 헤딩 레벨 검증
  - Bullet list 형식 검증
  - 필수 섹션 누락 검출
  - 섹션 순서 검증
  - Frontmatter-DSL 정합성
  - Description-DSL 비교
  - 코드 블록 면제
  - 엣지 케이스 (빈 본문, 알 수 없는 이슈 타입 등)
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from evaluate_sync_result import (
    check_bullet_list_format,
    check_description_vs_dsl,
    check_frontmatter_vs_dsl,
    check_heading_level,
    check_no_jira_markers_in_md,
    check_section_order,
    check_template_sections,
    evaluate_md_file,
)


# =====================================================================
# 1. Jira markers
# =====================================================================
class TestJiraMarkers(unittest.TestCase):
    """MD에 Jira 마커가 남아있으면 error."""

    def test_clean_body_no_issues(self):
        body = "## 목표\n- 테스트\n\n## 결과\n- 완료"
        self.assertEqual(check_no_jira_markers_in_md(body), [])

    def test_checkbox_emoji_detected(self):
        body = "☑️ 목표\n- test"
        issues = check_no_jira_markers_in_md(body)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "error")
        self.assertEqual(issues[0]["line"], 1)

    def test_h3_checkbox_detected(self):
        body = "h3. ☑️ 배경\n- test"
        issues = check_no_jira_markers_in_md(body)
        self.assertEqual(len(issues), 1)

    def test_italic_checkbox_detected(self):
        body = "☑️ *작업노트*\n- test"
        issues = check_no_jira_markers_in_md(body)
        self.assertEqual(len(issues), 1)

    def test_multiple_markers(self):
        body = "☑️ 목표\n- a\n\n☑️ 결과\n- b\n\n☑️ 링크\n- c"
        issues = check_no_jira_markers_in_md(body)
        self.assertEqual(len(issues), 3)

    def test_checkbox_in_code_block_still_detected(self):
        """코드 블록 내 ☑️도 현재는 검출됨 (보수적 검출)."""
        body = "```\n☑️ 이건 코드\n```"
        issues = check_no_jira_markers_in_md(body)
        # 현재 구현은 코드 블록을 구분하지 않음 — 보수적으로 검출
        self.assertEqual(len(issues), 1)

    def test_empty_body(self):
        self.assertEqual(check_no_jira_markers_in_md(""), [])


# =====================================================================
# 2. Heading level
# =====================================================================
class TestHeadingLevel(unittest.TestCase):
    """섹션 헤딩이 ## (h2)이 아닌 레벨이면 warning."""

    def test_h2_no_issues(self):
        body = "## 목표\n- test\n\n## 결과\n- done"
        self.assertEqual(check_heading_level(body), [])

    def test_h1_warning(self):
        body = "# 제목\n- test"
        issues = check_heading_level(body)
        self.assertEqual(len(issues), 1)
        self.assertIn("h1", issues[0]["message"])

    def test_h3_warning(self):
        body = "### 하위 섹션\n- test"
        issues = check_heading_level(body)
        self.assertEqual(len(issues), 1)
        self.assertIn("h3", issues[0]["message"])

    def test_h4_h5_h6(self):
        body = "#### h4\n- a\n##### h5\n- b\n###### h6\n- c"
        issues = check_heading_level(body)
        self.assertEqual(len(issues), 3)

    def test_heading_in_code_block_skipped(self):
        """코드 블록 내 #은 무시."""
        body = "## 목표\n- test\n\n```python\n# comment\n### not a heading\n```"
        issues = check_heading_level(body)
        self.assertEqual(len(issues), 0)

    def test_mixed_h2_and_h3(self):
        body = "## 목표\n- a\n\n### 세부\n- b\n\n## 결과\n- c"
        issues = check_heading_level(body)
        self.assertEqual(len(issues), 1)

    def test_empty_body(self):
        self.assertEqual(check_heading_level(""), [])


# =====================================================================
# 3. Bullet list format
# =====================================================================
class TestBulletListFormat(unittest.TestCase):
    """섹션 본문이 bullet list가 아닌 평문이면 warning."""

    def test_bullet_list_no_issues(self):
        body = "## 목표\n- 항목1\n- 항목2"
        self.assertEqual(check_bullet_list_format(body), [])

    def test_plain_text_warning(self):
        body = "## 목표\n이건 평문입니다"
        issues = check_bullet_list_format(body)
        self.assertEqual(len(issues), 1)
        self.assertIn("목표", issues[0]["message"])

    def test_numbered_list_no_warning(self):
        body = "## 목표\n1. 첫번째\n2. 두번째"
        self.assertEqual(check_bullet_list_format(body), [])

    def test_nested_bullet_no_warning(self):
        body = "## 목표\n- 항목\n  - 하위 항목"
        self.assertEqual(check_bullet_list_format(body), [])

    def test_mixed_bullet_and_plain_no_warning(self):
        """bullet이 하나라도 있으면 해당 섹션은 경고 안 함."""
        body = "## 목표\n- 항목\n부연 설명 평문"
        self.assertEqual(check_bullet_list_format(body), [])

    def test_table_not_flagged(self):
        """테이블은 평문으로 취급하지 않음."""
        body = "## 결과\n| 항목 | 값 |\n|------|----|\n| a | b |"
        self.assertEqual(check_bullet_list_format(body), [])

    def test_empty_section_no_warning(self):
        """빈 섹션은 경고 안 함."""
        body = "## 목표\n\n## 결과\n- done"
        self.assertEqual(check_bullet_list_format(body), [])

    def test_multiple_plain_sections(self):
        body = "## 목표\n평문1\n\n## 결과\n평문2"
        issues = check_bullet_list_format(body)
        self.assertEqual(len(issues), 2)

    def test_last_section_plain(self):
        """마지막 섹션이 평문인 경우도 검출."""
        body = "## 목표\n- ok\n\n## 결과\n이건 평문"
        issues = check_bullet_list_format(body)
        self.assertEqual(len(issues), 1)
        self.assertIn("결과", issues[0]["message"])

    def test_code_block_in_section_not_flagged(self):
        """코드 블록은 평문으로 취급하지 않음."""
        body = "## 목표\n- 항목\n\n```bash\necho hello\n```"
        self.assertEqual(check_bullet_list_format(body), [])

    def test_asterisk_bullet(self):
        """* 로 시작하는 bullet도 인식."""
        body = "## 목표\n* 항목1\n* 항목2"
        self.assertEqual(check_bullet_list_format(body), [])


# =====================================================================
# 4. Template sections (required)
# =====================================================================
class TestTemplateSections(unittest.TestCase):
    """필수 섹션 누락 검출."""

    def test_task_all_required_present(self):
        body = "## 목표\n- a\n\n## 결과\n- b"
        self.assertEqual(check_template_sections(body, "Task"), [])

    def test_task_missing_result(self):
        body = "## 목표\n- a"
        issues = check_template_sections(body, "Task")
        self.assertEqual(len(issues), 1)
        self.assertIn("결과", issues[0]["message"])

    def test_task_missing_all(self):
        body = "## 링크\n- url"
        issues = check_template_sections(body, "Task")
        self.assertEqual(len(issues), 2)

    def test_epic_all_required_present(self):
        body = "## 배경\n- a\n\n## 목표\n- b\n\n## 기대 효과\n- c\n\n## 관련 링크\n- d"
        self.assertEqual(check_template_sections(body, "Epic"), [])

    def test_epic_missing_기대효과(self):
        body = "## 배경\n- a\n\n## 목표\n- b\n\n## 관련 링크\n- c"
        issues = check_template_sections(body, "Epic")
        self.assertEqual(len(issues), 1)
        self.assertIn("기대 효과", issues[0]["message"])

    def test_none_issue_type_no_check(self):
        self.assertEqual(check_template_sections("", None), [])

    def test_unknown_issue_type_no_check(self):
        self.assertEqual(check_template_sections("## 목표\n- a", "Bug"), [])

    def test_empty_body_all_missing(self):
        issues = check_template_sections("", "Task")
        self.assertEqual(len(issues), 2)


# =====================================================================
# 5. Section order
# =====================================================================
class TestSectionOrder(unittest.TestCase):
    """섹션 순서가 템플릿과 일치하는지."""

    def test_correct_order_no_issues(self):
        body = "## 목표\n- a\n\n## 결과\n- b\n\n## 링크\n- c"
        self.assertEqual(check_section_order(body, "Task"), [])

    def test_wrong_order(self):
        body = "## 결과\n- b\n\n## 목표\n- a"
        issues = check_section_order(body, "Task")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "info")

    def test_partial_sections_correct_order(self):
        """일부 섹션만 있어도 상대 순서가 맞으면 OK."""
        body = "## 목표\n- a\n\n## 링크\n- c"
        self.assertEqual(check_section_order(body, "Task"), [])

    def test_extra_non_template_sections_ignored(self):
        """템플릿에 없는 섹션은 순서 검사에서 무시."""
        body = "## 목표\n- a\n\n## 커스텀 섹션\n- x\n\n## 결과\n- b"
        self.assertEqual(check_section_order(body, "Task"), [])

    def test_none_issue_type(self):
        self.assertEqual(check_section_order("## 목표\n- a", None), [])

    def test_epic_correct_order(self):
        body = "## 배경\n- a\n\n## 목표\n- b\n\n## 관련 링크\n- c\n\n## 완료보고\n- d"
        self.assertEqual(check_section_order(body, "Epic"), [])

    def test_epic_wrong_order(self):
        body = "## 목표\n- b\n\n## 배경\n- a"
        issues = check_section_order(body, "Epic")
        self.assertEqual(len(issues), 1)


# =====================================================================
# 6. Frontmatter vs DSL
# =====================================================================
class TestFrontmatterVsDsl(unittest.TestCase):
    """frontmatter와 DSL 메타데이터 비교."""

    def _make_dsl(self, **overrides) -> dict:
        base = {
            "issue": {
                "key": "KCDL-1234",
                "summary": "Test Task",
                "issue_type": "Task",
                "status": "Open",
                "priority": "Medium",
                "parent_key": "KCDL-1000",
                "links": [
                    {
                        "direction": "outward",
                        "relationship": "blocks",
                        "key": "KCDL-9999",
                        "summary": "Blocked by sample",
                    }
                ],
            }
        }
        base["issue"].update(overrides)
        return base

    def test_all_match(self):
        fm = {
            "title": "Test Task",
            "jira": "KCDL-1234",
            "issue_type": "Task",
            "status": "Open",
            "priority": "Medium",
            "parent_key": "KCDL-1000",
            "links": [
                {
                    "direction": "outward",
                    "relationship": "blocks",
                    "key": "KCDL-9999",
                    "summary": "Blocked by sample",
                }
            ],
        }
        self.assertEqual(check_frontmatter_vs_dsl(fm, self._make_dsl()), [])

    def test_title_mismatch(self):
        fm = {"title": "Wrong Title", "jira": "KCDL-1234"}
        issues = check_frontmatter_vs_dsl(fm, self._make_dsl())
        self.assertTrue(any("title" in i["message"] for i in issues))

    def test_status_mismatch(self):
        fm = {"title": "Test Task", "status": "In Progress"}
        issues = check_frontmatter_vs_dsl(fm, self._make_dsl())
        self.assertTrue(any("status" in i["message"] for i in issues))

    def test_null_values_ignored(self):
        """frontmatter가 null/None/- 이면 무시."""
        fm = {"title": "Test Task", "status": "null", "priority": "-"}
        self.assertEqual(check_frontmatter_vs_dsl(fm, self._make_dsl()), [])

    def test_dsl_none_field_ignored(self):
        """DSL에 None인 필드는 비교 안 함."""
        fm = {"title": "Test Task", "parent_key": "KCDL-9999"}
        dsl = self._make_dsl(parent_key=None)
        self.assertEqual(check_frontmatter_vs_dsl(fm, dsl), [])

    def test_missing_frontmatter_field_ignored(self):
        """frontmatter에 필드 자체가 없으면 비교 안 함."""
        fm = {"title": "Test Task"}
        self.assertEqual(check_frontmatter_vs_dsl(fm, self._make_dsl()), [])

    def test_links_mismatch_detected(self):
        fm = {
            "title": "Test Task",
            "links": [
                {
                    "direction": "outward",
                    "relationship": "relates to",
                    "key": "KCDL-8888",
                    "summary": "Different link",
                }
            ],
        }
        issues = check_frontmatter_vs_dsl(fm, self._make_dsl())
        self.assertTrue(any("links" in i["message"] for i in issues))

    def test_empty_dsl_issue(self):
        self.assertEqual(check_frontmatter_vs_dsl({}, {"issue": {}}), [])


# =====================================================================
# 7. Description vs DSL
# =====================================================================
class TestDescriptionVsDsl(unittest.TestCase):
    """MD 본문과 DSL description 비교."""

    def test_both_empty(self):
        self.assertEqual(check_description_vs_dsl("", {"description_markdown": ""}), [])

    def test_both_have_headings(self):
        body = "## 목표\n- test"
        dsl = {"description_markdown": "## 목표\n- test"}
        self.assertEqual(check_description_vs_dsl(body, dsl), [])

    def test_dsl_has_headings_body_empty(self):
        """DSL에 헤딩이 있지만 body가 비어있으면 — 둘 다 비어있지 않은 조건 통과 못함."""
        dsl = {"description_markdown": "## 목표\n- test"}
        self.assertEqual(check_description_vs_dsl("", dsl), [])

    def test_dsl_has_headings_body_has_no_headings(self):
        body = "그냥 평문"
        dsl = {"description_markdown": "## 목표\n- test"}
        issues = check_description_vs_dsl(body, dsl)
        self.assertEqual(len(issues), 1)
        self.assertIn("섹션 헤딩", issues[0]["message"])

    def test_dsl_no_headings_no_issue(self):
        body = "평문"
        dsl = {"description_markdown": "평문"}
        self.assertEqual(check_description_vs_dsl(body, dsl), [])


# =====================================================================
# 8. evaluate_md_file integration
# =====================================================================
class TestEvaluateMdFile(unittest.TestCase):
    """evaluate_md_file 통합 테스트."""

    def _write_md(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        )
        f.write(content)
        f.close()
        return f.name

    def _write_dsl(self, dsl: dict) -> str:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(dsl, f, ensure_ascii=False)
        f.close()
        return f.name

    def test_perfect_task_passes(self):
        md = "---\ntitle: Test\njira: KCDL-1234\nissue_type: Task\nstatus: Open\npriority: Medium\n---\n\n## 목표\n- 목표 내용\n\n## 결과\n- 결과 내용\n\n## 링크\n- url\n"
        result = evaluate_md_file(self._write_md(md))
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["errors"], 0)

    def test_jira_marker_fails(self):
        md = "---\ntitle: Test\nissue_type: Task\n---\n\n☑️ *목표*\n- test\n"
        result = evaluate_md_file(self._write_md(md))
        self.assertEqual(result["result"], "FAIL")
        self.assertGreater(result["errors"], 0)

    def test_with_dsl_match(self):
        md = "---\ntitle: Test Task\njira: KCDL-1234\nissue_type: Task\nstatus: Open\npriority: Medium\n---\n\n## 목표\n- a\n\n## 결과\n- b\n"
        dsl = {
            "version": 1,
            "issue": {
                "key": "KCDL-1234",
                "summary": "Test Task",
                "issue_type": "Task",
                "status": "Open",
                "priority": "Medium",
                "assignee": None,
                "reporter": None,
                "parent_key": None,
                "end_date": None,
                "due_date": None,
                "labels": [],
                "components": [],
                "links": [],
                "created_at": None,
                "url": None,
            },
            "description_markdown": "## 목표\n- a\n\n## 결과\n- b",
            "md_force_fields": [],
            "checklists": {"todo": [], "acceptance_criteria": []},
        }
        result = evaluate_md_file(self._write_md(md), self._write_dsl(dsl))
        self.assertEqual(result["result"], "PASS")

    def test_with_dsl_mismatch(self):
        md = "---\ntitle: Wrong Title\njira: KCDL-1234\nissue_type: Task\nstatus: Open\npriority: Medium\n---\n\n## 목표\n- a\n\n## 결과\n- b\n"
        dsl = {
            "version": 1,
            "issue": {
                "key": "KCDL-1234",
                "summary": "Correct Title",
                "issue_type": "Task",
                "status": "Open",
                "priority": "Medium",
                "assignee": None,
                "reporter": None,
                "parent_key": None,
                "end_date": None,
                "due_date": None,
                "labels": [],
                "components": [],
                "links": [],
                "created_at": None,
                "url": None,
            },
            "description_markdown": "## 목표\n- a\n\n## 결과\n- b",
            "md_force_fields": [],
            "checklists": {"todo": [], "acceptance_criteria": []},
        }
        result = evaluate_md_file(self._write_md(md), self._write_dsl(dsl))
        self.assertEqual(result["result"], "FAIL")

    def test_no_frontmatter(self):
        md = "## 목표\n- test\n"
        result = evaluate_md_file(self._write_md(md))
        self.assertIsNone(result["issue_type"])

    def test_epic_missing_required(self):
        md = "---\ntitle: Test Epic\nissue_type: Epic\n---\n\n## 목표\n- a\n"
        result = evaluate_md_file(self._write_md(md))
        warnings = [i for i in result["issues"] if i["check"] == "template_sections"]
        # 배경, 기대 효과, 관련 링크 누락
        self.assertGreaterEqual(len(warnings), 3)

    def test_pass_fail_only_on_errors(self):
        """warning만 있어도 PASS."""
        md = "---\ntitle: Test\nissue_type: Task\n---\n\n## 목표\n평문 (warning)\n\n## 결과\n- ok\n"
        result = evaluate_md_file(self._write_md(md))
        self.assertEqual(result["result"], "PASS")
        self.assertGreater(result["warnings"], 0)

    def test_empty_file(self):
        md = "---\ntitle: Empty\nissue_type: Task\n---\n"
        result = evaluate_md_file(self._write_md(md))
        # 필수 섹션 누락 warning만, error 없음
        self.assertEqual(result["result"], "PASS")
        self.assertGreater(result["warnings"], 0)


if __name__ == "__main__":
    unittest.main()
