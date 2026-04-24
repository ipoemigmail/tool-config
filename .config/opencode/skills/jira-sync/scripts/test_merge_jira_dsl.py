#!/usr/bin/env python3
"""merge_jira_dsl.py의 단위 테스트."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from merge_jira_dsl import merge_dsl


def _make_dsl(*, links: list[dict], md_force_fields: list[str] | None = None) -> dict:
    return {
        "version": 1,
        "issue": {
            "key": "KCDL-1",
            "url": "https://jira.daumkakao.com/browse/KCDL-1",
            "summary": "Sample",
            "epic_name": None,
            "issue_type": "Task",
            "status": "Open",
            "priority": "Medium",
            "created_at": None,
            "assignee": None,
            "reporter": None,
            "parent_key": None,
            "end_date": None,
            "due_date": None,
            "labels": [],
            "components": [],
            "links": links,
        },
        "description_markdown": "",
        "md_force_fields": md_force_fields or [],
        "checklists": {"todo": [], "acceptance_criteria": []},
    }


class TestLinkMerge(unittest.TestCase):
    def test_links_append_md_only_after_jira(self):
        """SKILL.md 규칙: Jira 기준으로 취하고, MD에만 있는 링크(key 기준)는 뒤에 추가."""
        md_only = {
            "direction": "outward",
            "relationship": "blocks",
            "key": "KCDL-2",
            "summary": "MD link",
        }
        jira_only = {
            "direction": "inward",
            "relationship": "is blocked by",
            "key": "KCDL-3",
            "summary": "Jira link",
        }
        md = _make_dsl(links=[md_only])
        jira = _make_dsl(links=[jira_only])

        merged = merge_dsl(md, jira)

        self.assertEqual(merged["issue"]["links"], [jira_only, md_only])

    def test_links_same_key_jira_wins(self):
        """같은 key는 Jira 값이 우선한다."""
        md_link = {
            "direction": "outward",
            "relationship": "relates to",
            "key": "KCDL-2",
            "summary": "MD side",
        }
        jira_link = {
            "direction": "inward",
            "relationship": "is blocked by",
            "key": "KCDL-2",
            "summary": "Jira side",
        }
        md = _make_dsl(links=[md_link])
        jira = _make_dsl(links=[jira_link])

        merged = merge_dsl(md, jira)

        self.assertEqual(merged["issue"]["links"], [jira_link])

    def test_links_force_uses_md_value(self):
        md = _make_dsl(
            links=[
                {
                    "direction": "outward",
                    "relationship": "blocks",
                    "key": "KCDL-2",
                    "summary": "MD link",
                }
            ],
            md_force_fields=["links"],
        )
        jira = _make_dsl(
            links=[
                {
                    "direction": "inward",
                    "relationship": "is blocked by",
                    "key": "KCDL-3",
                    "summary": "Jira link",
                }
            ]
        )

        merged = merge_dsl(md, jira)

        self.assertEqual(merged["issue"]["links"], md["issue"]["links"])


if __name__ == "__main__":
    unittest.main()
