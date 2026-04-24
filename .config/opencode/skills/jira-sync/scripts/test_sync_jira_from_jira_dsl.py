#!/usr/bin/env python3
"""sync_jira_from_jira_dsl.py의 단위 테스트."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync_jira_from_jira_dsl as sync


class TestSyncIssueLinks(unittest.TestCase):
    def test_replace_deletes_links_not_in_md(self):
        calls: list[tuple[str, str, dict | None]] = []

        def fake_request_json(method: str, url: str, token: str, payload=None):
            calls.append((method, url, payload))
            if method == "GET" and "fields=issuelinks" in url:
                return {
                    "fields": {
                        "issuelinks": [
                            {
                                "id": "101",
                                "type": {
                                    "name": "blocks",
                                    "outward": "blocks",
                                    "inward": "is blocked by",
                                },
                                "outwardIssue": {"key": "KCDL-1"},
                            },
                            {
                                "id": "102",
                                "type": {
                                    "name": "relates to",
                                    "outward": "relates to",
                                    "inward": "relates to",
                                },
                                "outwardIssue": {"key": "KCDL-2"},
                            },
                        ]
                    }
                }
            if method == "GET" and "issueLinkType" in url:
                return {
                    "issueLinkTypes": [
                        {
                            "name": "blocks",
                            "outward": "blocks",
                            "inward": "is blocked by",
                        },
                        {
                            "name": "relates to",
                            "outward": "relates to",
                            "inward": "relates to",
                        },
                    ]
                }
            return {}

        with patch.object(sync, "request_json", side_effect=fake_request_json):
            sync.sync_issue_links(
                "https://jira.example",
                "token",
                "KCDL-10",
                [
                    {
                        "direction": "outward",
                        "relationship": "blocks",
                        "key": "KCDL-1",
                        "summary": None,
                    }
                ],
                replace=True,
            )

        deletes = [call for call in calls if call[0] == "DELETE"]
        posts = [call for call in calls if call[0] == "POST"]
        self.assertEqual(len(deletes), 1)
        self.assertIn("issueLink/102", deletes[0][1])
        self.assertEqual(posts, [])

    def test_replace_adds_missing_links(self):
        calls: list[tuple[str, str, dict | None]] = []

        def fake_request_json(method: str, url: str, token: str, payload=None):
            calls.append((method, url, payload))
            if method == "GET" and "fields=issuelinks" in url:
                return {"fields": {"issuelinks": []}}
            if method == "GET" and "issueLinkType" in url:
                return {
                    "issueLinkTypes": [
                        {
                            "name": "blocks",
                            "outward": "blocks",
                            "inward": "is blocked by",
                        }
                    ]
                }
            return {}

        with patch.object(sync, "request_json", side_effect=fake_request_json):
            sync.sync_issue_links(
                "https://jira.example",
                "token",
                "KCDL-10",
                [
                    {
                        "direction": "outward",
                        "relationship": "blocks",
                        "key": "KCDL-1",
                        "summary": None,
                    }
                ],
                replace=True,
            )

        posts = [call for call in calls if call[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertIn("issueLink", posts[0][1])


if __name__ == "__main__":
    unittest.main()
