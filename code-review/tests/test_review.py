"""Unit tests for code-review/review.py.

Run from the repo root:

    python3 -m unittest discover -s code-review/tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review  # noqa: E402


class SummarizeCITests(unittest.TestCase):
    def test_unknown_when_no_checks(self):
        summary, failing = review.summarize_ci([])
        self.assertIn("unknown", summary)
        self.assertFalse(failing)

    def test_green_when_all_pass(self):
        summary, failing = review.summarize_ci(
            [{"name": "lint", "state": "SUCCESS", "bucket": "pass"}]
        )
        self.assertIn("green", summary)
        self.assertFalse(failing)

    def test_red_when_any_failing(self):
        summary, failing = review.summarize_ci(
            [
                {"name": "lint", "state": "SUCCESS", "bucket": "pass"},
                {"name": "test", "state": "FAILURE", "bucket": "fail"},
            ]
        )
        self.assertTrue(failing)
        self.assertIn("test", summary)

    def test_pending_when_in_progress(self):
        summary, failing = review.summarize_ci(
            [{"name": "build", "state": "IN_PROGRESS", "bucket": "pending"}]
        )
        self.assertFalse(failing)
        self.assertIn("pending", summary)


class NormalizeReviewTests(unittest.TestCase):
    def _minimal(self, **overrides) -> dict:
        base = {
            "verdict": "approve",
            "summary": "looks good",
            "blockers": [],
            "major": [],
            "minor": [],
            "praise": [],
            "out_of_scope": [],
            "questions": [],
        }
        base.update(overrides)
        return base

    def test_accepts_minimal_valid_payload(self):
        out = review.normalize_review(self._minimal())
        self.assertEqual(out["verdict"], "approve")
        self.assertEqual(out["blockers"], [])

    def test_rejects_invalid_verdict(self):
        with self.assertRaises(review.ReviewError):
            review.normalize_review(self._minimal(verdict="lgtm"))

    def test_coerces_finding_fields_to_strings(self):
        out = review.normalize_review(
            self._minimal(
                blockers=[
                    {
                        "location": "a.py:1",
                        "description": "boom",
                        "why_it_matters": "crashes on None",
                        "confidence": "high",
                        "suggested_fix": "fix it",
                    }
                ]
            )
        )
        self.assertEqual(out["blockers"][0]["location"], "a.py:1")
        self.assertEqual(out["blockers"][0]["suggested_fix"], "fix it")
        self.assertEqual(out["blockers"][0]["why_it_matters"], "crashes on None")
        self.assertEqual(out["blockers"][0]["confidence"], "high")

    def test_missing_or_invalid_confidence_defaults_to_low(self):
        out = review.normalize_review(
            self._minimal(
                major=[
                    {"location": "a.py:1", "description": "x"},
                    {"location": "b.py:2", "description": "y", "confidence": "bogus"},
                ]
            )
        )
        self.assertEqual(out["major"][0]["confidence"], "low")
        self.assertEqual(out["major"][1]["confidence"], "low")

    def test_drops_blank_strings_in_lists(self):
        out = review.normalize_review(self._minimal(praise=["nice", "  ", ""]))
        self.assertEqual(out["praise"], ["nice"])

    def test_rejects_non_list_blockers(self):
        with self.assertRaises(review.ReviewError):
            review.normalize_review(self._minimal(blockers="not a list"))


class HasBlockersTests(unittest.TestCase):
    def test_blocker_via_verdict(self):
        review_obj = {
            "verdict": "request_changes",
            "blockers": [],
            "major": [],
            "minor": [],
            "praise": [],
            "out_of_scope": [],
            "questions": [],
            "summary": "",
        }
        self.assertTrue(review.has_blockers(review_obj))

    def test_blocker_via_findings(self):
        review_obj = {
            "verdict": "approve_with_comments",
            "blockers": [{"location": "x:1", "description": "y", "suggested_fix": ""}],
            "major": [],
            "minor": [],
            "praise": [],
            "out_of_scope": [],
            "questions": [],
            "summary": "",
        }
        self.assertTrue(review.has_blockers(review_obj))

    def test_no_blockers(self):
        review_obj = {
            "verdict": "approve",
            "blockers": [],
            "major": [],
            "minor": [],
            "praise": [],
            "out_of_scope": [],
            "questions": [],
            "summary": "",
        }
        self.assertFalse(review.has_blockers(review_obj))


class RenderMarkdownTests(unittest.TestCase):
    def test_renders_headline_and_findings(self):
        meta = {
            "number": 42,
            "title": "Add widget",
            "author": {"login": "alice"},
            "baseRefName": "main",
            "headRefName": "feat/widget",
            "additions": 10,
            "deletions": 2,
            "changedFiles": 3,
            "isDraft": False,
        }
        rev = {
            "verdict": "request_changes",
            "summary": "Has issues.",
            "blockers": [
                {"location": "src/a.py:10", "description": "NPE", "suggested_fix": "guard None"}
            ],
            "major": [],
            "minor": [],
            "praise": ["clean diff"],
            "out_of_scope": [],
            "questions": [],
        }
        md = review.render_markdown(meta, rev, "green — all checks passing", False)
        self.assertIn("PR #42: Add widget", md)
        self.assertIn("@alice", md)
        self.assertIn("❌ Request changes", md)
        self.assertIn("### Blockers", md)
        self.assertIn("`src/a.py:10`", md)
        self.assertIn("### Praise", md)
        self.assertNotIn("### Major issues", md)

    def test_truncated_diff_warning(self):
        meta = {
            "number": 1,
            "title": "t",
            "author": {"login": "x"},
            "baseRefName": "main",
            "headRefName": "h",
            "additions": 0,
            "deletions": 0,
            "changedFiles": 0,
            "isDraft": False,
        }
        rev = {
            "verdict": "approve",
            "summary": "",
            "blockers": [],
            "major": [],
            "minor": [],
            "praise": [],
            "out_of_scope": [],
            "questions": [],
        }
        md = review.render_markdown(meta, rev, "green", True)
        self.assertIn("Diff was truncated", md)


class CommentBodyTests(unittest.TestCase):
    def test_includes_marker(self):
        body = review.comment_body("hello")
        self.assertTrue(body.startswith(review.COMMENT_MARKER))
        self.assertIn("hello", body)


class CompactMetaTests(unittest.TestCase):
    def _full_meta(self, **overrides) -> dict:
        base = {
            "number": 42,
            "title": "Add widget",
            "author": {"login": "alice", "name": "Alice Smith", "id": 123},
            "baseRefName": "main",
            "headRefName": "feat/widget",
            "additions": 10,
            "deletions": 2,
            "changedFiles": 3,
            "isDraft": False,
            "labels": [{"name": "bug", "color": "red", "description": "a bug"}],
            "body": "Fixes the widget",
            "reviewDecision": "APPROVED",
            "mergeable": "MERGEABLE",
        }
        base.update(overrides)
        return base

    def test_contains_essential_fields(self):
        compact = review._compact_meta(self._full_meta())
        self.assertEqual(compact["number"], 42)
        self.assertEqual(compact["author"], "alice")
        self.assertEqual(compact["base"], "main")
        self.assertEqual(compact["head"], "feat/widget")
        self.assertEqual(compact["labels"], ["bug"])
        self.assertEqual(compact["draft"], False)

    def test_omits_verbose_github_fields(self):
        compact = review._compact_meta(self._full_meta())
        self.assertNotIn("reviewDecision", compact)
        self.assertNotIn("mergeable", compact)

    def test_body_truncated_at_500_chars(self):
        long_body = "x" * 600
        compact = review._compact_meta(self._full_meta(body=long_body))
        self.assertEqual(len(compact["body"]), 501)  # 500 chars + ellipsis
        self.assertTrue(compact["body"].endswith("…"))

    def test_body_not_truncated_when_short(self):
        compact = review._compact_meta(self._full_meta(body="short"))
        self.assertEqual(compact["body"], "short")

    def test_missing_author_falls_back_to_unknown(self):
        compact = review._compact_meta(self._full_meta(author=None))
        self.assertEqual(compact["author"], "unknown")

    def test_empty_labels(self):
        compact = review._compact_meta(self._full_meta(labels=[]))
        self.assertEqual(compact["labels"], [])


class WriteGithubOutputTests(unittest.TestCase):
    def test_writes_verdict_and_has_blockers(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            tmp_path = f.name
        try:
            os.environ["GITHUB_OUTPUT"] = tmp_path
            review.write_github_output("approve", False)
            content = Path(tmp_path).read_text()
            self.assertIn("verdict=approve\n", content)
            self.assertIn("has_blockers=false\n", content)
        finally:
            del os.environ["GITHUB_OUTPUT"]
            os.unlink(tmp_path)

    def test_writes_has_blockers_true(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            tmp_path = f.name
        try:
            os.environ["GITHUB_OUTPUT"] = tmp_path
            review.write_github_output("request_changes", True)
            content = Path(tmp_path).read_text()
            self.assertIn("has_blockers=true\n", content)
        finally:
            del os.environ["GITHUB_OUTPUT"]
            os.unlink(tmp_path)

    def test_no_op_when_env_unset(self):
        os.environ.pop("GITHUB_OUTPUT", None)
        review.write_github_output("approve", False)  # should not raise


class FilterFindingsTests(unittest.TestCase):
    def _finding(self, confidence: str, loc: str = "a.py:1") -> dict:
        return {
            "location": loc,
            "description": "d",
            "why_it_matters": "w",
            "confidence": confidence,
            "suggested_fix": "",
        }

    def _review(self, **overrides) -> dict:
        base = {
            "verdict": "approve_with_comments",
            "summary": "",
            "blockers": [],
            "major": [],
            "minor": [],
            "praise": [],
            "out_of_scope": [],
            "questions": [],
        }
        base.update(overrides)
        return base

    def test_drops_low_confidence_major(self):
        rev = self._review(major=[self._finding("low"), self._finding("medium")])
        out = review.filter_findings(rev, min_confidence="medium", include_nits=False)
        self.assertEqual(len(out["major"]), 1)
        self.assertEqual(out["major"][0]["confidence"], "medium")

    def test_high_threshold_drops_medium_major(self):
        rev = self._review(major=[self._finding("medium"), self._finding("high")])
        out = review.filter_findings(rev, min_confidence="high", include_nits=False)
        self.assertEqual(len(out["major"]), 1)
        self.assertEqual(out["major"][0]["confidence"], "high")

    def test_minor_dropped_when_nits_off(self):
        rev = self._review(minor=[self._finding("high")])
        out = review.filter_findings(rev, min_confidence="medium", include_nits=False)
        self.assertEqual(out["minor"], [])

    def test_minor_kept_high_only_when_nits_on(self):
        rev = self._review(minor=[self._finding("high"), self._finding("medium")])
        out = review.filter_findings(rev, min_confidence="medium", include_nits=True)
        self.assertEqual(len(out["minor"]), 1)
        self.assertEqual(out["minor"][0]["confidence"], "high")

    def test_blockers_always_kept(self):
        rev = self._review(blockers=[self._finding("low")])
        out = review.filter_findings(rev, min_confidence="high", include_nits=False)
        self.assertEqual(len(out["blockers"]), 1)


class DeriveVerdictTests(unittest.TestCase):
    def _review(self, **overrides) -> dict:
        base = {
            "verdict": "approve_with_comments",
            "blockers": [],
            "major": [],
            "minor": [],
        }
        base.update(overrides)
        return base

    def test_blocker_forces_request_changes(self):
        rev = self._review(blockers=[{"location": "a:1"}])
        self.assertEqual(review.derive_verdict(rev, ci_failing=False), "request_changes")

    def test_ci_failing_forces_request_changes(self):
        rev = self._review()
        self.assertEqual(review.derive_verdict(rev, ci_failing=True), "request_changes")

    def test_major_only_is_approve_with_comments(self):
        rev = self._review(major=[{"location": "a:1"}])
        self.assertEqual(review.derive_verdict(rev, ci_failing=False), "approve_with_comments")

    def test_empty_is_approve(self):
        rev = self._review()
        self.assertEqual(review.derive_verdict(rev, ci_failing=False), "approve")

    def test_cannot_review_is_preserved(self):
        rev = self._review(verdict="cannot_review")
        self.assertEqual(review.derive_verdict(rev, ci_failing=True), "cannot_review")


if __name__ == "__main__":
    unittest.main()
