#!/usr/bin/env python3
"""AI PR code review — fetch diff, call LiteLLM with JSON schema, post idempotent comment."""

from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COMMENT_MARKER = "<!-- innogando-ai-code-review -->"
GH_TIMEOUT_SECONDS = 60
LLM_TIMEOUT_SECONDS = 300
LLM_MAX_ATTEMPTS = 3
LLM_BACKOFF_BASE = 2.0
RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}

VALID_VERDICTS = {
    "approve",
    "approve_with_comments",
    "request_changes",
    "cannot_review",
}

# Ordered weakest → strongest. Findings below the configured minimum are dropped.
CONFIDENCE_LEVELS = ("low", "medium", "high")
CONFIDENCE_RANK = {level: rank for rank, level in enumerate(CONFIDENCE_LEVELS)}
DEFAULT_MIN_CONFIDENCE = "medium"

VERDICT_RENDER = {
    "approve": "✅ Approve — no blocking issues",
    "approve_with_comments": "⚠️ Approve with comments — non-blocking findings only",
    "request_changes": "❌ Request changes — at least one blocker",
    "cannot_review": "🚧 Cannot review — needs more context",
}

CONTEXT_FILES: list[tuple[str, Path]] = [
    ("AGENTS.md", Path("AGENTS.md")),
    ("code-review-generic", Path("ai/shared/skills/code-review-generic/SKILL.md")),
    ("code-review (repo-specific)", Path("ai/skills/code-review/SKILL.md")),
    ("conventions", Path("ai/context/conventions.md")),
    ("gotchas", Path("ai/context/gotchas.md")),
    ("secret-patterns", Path("ai/shared/policies/secret-patterns.md")),
    (
        "write-conventional-commit",
        Path("ai/shared/skills/write-conventional-commit/SKILL.md"),
    ),
]


log = logging.getLogger("code-review")


class ReviewError(Exception):
    """Fatal review error — surfaces as job failure with a clean message."""


@dataclass(frozen=True)
class Config:
    pr_number: str
    repo: str
    action_path: Path
    litellm_api_key: str
    litellm_base_url: str
    litellm_model: str
    max_diff_lines: int
    max_diff_skip_multiplier: int
    skip_draft: bool
    fail_on_blockers: bool
    min_confidence: str
    include_nits: bool

    @classmethod
    def from_env(cls) -> "Config":
        pr_number = _env("PR_NUMBER")
        repo = _env("GITHUB_REPOSITORY")
        api_key = _env("LITELLM_API_KEY")
        if not pr_number:
            raise ReviewError("PR_NUMBER is required")
        if not repo:
            raise ReviewError("GITHUB_REPOSITORY is required")
        if not api_key:
            raise ReviewError("LITELLM_API_KEY is required")
        try:
            max_lines = int(_env("MAX_DIFF_LINES", "2000") or "2000")
        except ValueError as exc:
            raise ReviewError(f"MAX_DIFF_LINES must be an integer: {exc}") from exc
        try:
            skip_multiplier = int(_env("MAX_DIFF_SKIP_MULTIPLIER", "3") or "3")
        except ValueError as exc:
            raise ReviewError(f"MAX_DIFF_SKIP_MULTIPLIER must be an integer: {exc}") from exc
        min_confidence = _env("MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE).lower() or DEFAULT_MIN_CONFIDENCE
        if min_confidence not in CONFIDENCE_RANK:
            raise ReviewError(
                f"MIN_CONFIDENCE must be one of {', '.join(CONFIDENCE_LEVELS)}: got {min_confidence!r}"
            )
        return cls(
            pr_number=pr_number,
            repo=repo,
            action_path=Path(_env("GITHUB_ACTION_PATH", ".")),
            litellm_api_key=api_key,
            litellm_base_url=_env("LITELLM_BASE_URL"),
            litellm_model=_env("LITELLM_MODEL"),
            max_diff_lines=max_lines,
            max_diff_skip_multiplier=skip_multiplier,
            skip_draft=_truthy("SKIP_DRAFT", default=True),
            fail_on_blockers=_truthy("FAIL_ON_BLOCKERS", default=True),
            min_confidence=min_confidence,
            include_nits=_truthy("INCLUDE_NITS", default=False),
        )


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _truthy(name: str, *, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def run_gh(args: list[str], *, input_text: str | None = None) -> str:
    cmd = ["gh", *args]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewError(f"`gh {' '.join(args[:2])}` timed out after {GH_TIMEOUT_SECONDS}s") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise ReviewError(f"`gh {' '.join(args[:2])}` failed: {stderr}") from exc
    return result.stdout


def read_optional(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def load_system_prompt(action_path: Path) -> str:
    prompt_path = action_path / "system-prompt.md"
    if not prompt_path.is_file():
        raise ReviewError(f"system prompt not found at {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def build_context() -> str:
    sections: list[str] = []
    for label, rel_path in CONTEXT_FILES:
        content = read_optional(rel_path)
        if content:
            log.info("Context file loaded: %s (%s)", label, rel_path)
            sections.append(f"## {label}\n\n{content}")
        else:
            log.debug("Context file not found (skipping): %s (%s)", label, rel_path)
    if not sections:
        log.warning(
            "No context files found — checked %d paths (cwd=%s). "
            "Check that the action runs from the repository root.",
            len(CONTEXT_FILES),
            Path.cwd(),
        )
        return "_No repository context files found._"
    return "\n\n".join(sections)


def fetch_pr_metadata(pr_number: str) -> dict:
    raw = run_gh(
        [
            "pr",
            "view",
            pr_number,
            "--json",
            "number,title,body,labels,author,headRefName,baseRefName,"
            "additions,deletions,changedFiles,isDraft,mergeable,reviewDecision",
        ]
    )
    return json.loads(raw)


def fetch_pr_diff(pr_number: str) -> str:
    return run_gh(["pr", "diff", pr_number])


def fetch_pr_checks(pr_number: str) -> list[dict]:
    try:
        raw = run_gh(["pr", "checks", pr_number, "--json", "name,state,bucket"])
    except ReviewError as exc:
        log.warning("Could not fetch PR checks: %s", exc)
        return []
    return json.loads(raw) if raw.strip() else []


def summarize_ci(checks: list[dict]) -> tuple[str, bool]:
    if not checks:
        return "unknown — no checks reported", False

    failing_states = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED"}
    pending_states = {"PENDING", "IN_PROGRESS", "QUEUED"}

    failing = [c["name"] for c in checks if c.get("state") in failing_states or c.get("bucket") == "fail"]
    pending = [c["name"] for c in checks if c.get("state") in pending_states or c.get("bucket") == "pending"]

    if failing:
        return f"red — failing: {', '.join(failing)}", True
    if pending:
        return f"pending — {', '.join(pending)}", False
    return "green — all checks passing", False


def _compact_meta(meta: dict) -> dict:
    body = meta.get("body") or ""
    return {
        "number": meta.get("number"),
        "title": meta.get("title", ""),
        "author": (meta.get("author") or {}).get("login", "unknown"),
        "base": meta.get("baseRefName", ""),
        "head": meta.get("headRefName", ""),
        "additions": meta.get("additions", 0),
        "deletions": meta.get("deletions", 0),
        "changed_files": meta.get("changedFiles", 0),
        "labels": [lb["name"] for lb in (meta.get("labels") or []) if lb.get("name")],
        "draft": meta.get("isDraft", False),
        "body": body[:500] + ("…" if len(body) > 500 else ""),
    }


def build_user_message(meta: dict, diff: str, ci_summary: str, repo_context: str) -> str:
    parts = [
        "# Pull request metadata",
        json.dumps(_compact_meta(meta), indent=2),
        "",
        f"# CI status\n\n{ci_summary}",
        "",
        "# Repository context",
        repo_context,
        "",
        "# Diff",
        f"```diff\n{diff}\n```",
    ]
    return "\n".join(parts)


def call_litellm(cfg: Config, system_prompt: str, user_message: str) -> dict:
    url = f"{cfg.litellm_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": cfg.litellm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {cfg.litellm_api_key}",
        "Content-Type": "application/json",
    }
    body = _post_with_retries(url, payload, headers)
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReviewError(f"unexpected LiteLLM response shape: {body}") from exc
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"LiteLLM returned non-JSON content: {content[:500]}") from exc


def _post_with_retries(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = exc
            if exc.code not in RETRYABLE_HTTP_STATUSES or attempt == LLM_MAX_ATTEMPTS:
                raise ReviewError(f"LiteLLM HTTP {exc.code}: {detail[:500]}") from exc
            log.warning("LiteLLM HTTP %s on attempt %d/%d: %s", exc.code, attempt, LLM_MAX_ATTEMPTS, detail[:200])
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == LLM_MAX_ATTEMPTS:
                raise ReviewError(f"LiteLLM unreachable after {attempt} attempts: {exc}") from exc
            log.warning("LiteLLM transport error on attempt %d/%d: %s", attempt, LLM_MAX_ATTEMPTS, exc)
        if attempt < LLM_MAX_ATTEMPTS:
            sleep_for = LLM_BACKOFF_BASE ** attempt + random.uniform(0, 0.5)
            time.sleep(sleep_for)
    raise ReviewError(f"LiteLLM call failed: {last_error}")


def normalize_review(raw: dict) -> dict:
    """Validate and normalize the LLM output into a known shape."""
    verdict = str(raw.get("verdict", "")).strip().lower()
    if verdict not in VALID_VERDICTS:
        raise ReviewError(f"invalid verdict from LLM: {verdict!r}")

    def _findings(key: str) -> list[dict]:
        items = raw.get(key) or []
        if not isinstance(items, list):
            raise ReviewError(f"field {key!r} must be a list, got {type(items).__name__}")
        out: list[dict] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            confidence = str(entry.get("confidence", "")).strip().lower()
            if confidence not in CONFIDENCE_RANK:
                # Unknown/missing confidence is treated as the weakest tier so it
                # is dropped by the filter unless the model asserts otherwise.
                confidence = "low"
            out.append(
                {
                    "location": str(entry.get("location", "")).strip(),
                    "description": str(entry.get("description", "")).strip(),
                    "why_it_matters": str(entry.get("why_it_matters", "")).strip(),
                    "confidence": confidence,
                    "suggested_fix": str(entry.get("suggested_fix", "")).strip(),
                }
            )
        return out

    def _strings(key: str) -> list[str]:
        items = raw.get(key) or []
        if not isinstance(items, list):
            return []
        return [str(x).strip() for x in items if str(x).strip()]

    return {
        "verdict": verdict,
        "summary": str(raw.get("summary", "")).strip(),
        "blockers": _findings("blockers"),
        "major": _findings("major"),
        "minor": _findings("minor"),
        "praise": _strings("praise"),
        "out_of_scope": _strings("out_of_scope"),
        "questions": _strings("questions"),
    }


def filter_findings(review: dict, *, min_confidence: str, include_nits: bool) -> dict:
    """Drop low-value findings so only confident, consequential issues remain.

    - Blockers are always kept (the prompt requires them to be demonstrable), but a
      low-confidence blocker is logged so we can spot prompt drift.
    - Major findings must clear ``min_confidence``.
    - Minor findings are dropped entirely unless ``include_nits`` is set, and even then
      require high confidence.
    """
    threshold = CONFIDENCE_RANK[min_confidence]

    def _clears(finding: dict, floor: int) -> bool:
        return CONFIDENCE_RANK.get(finding.get("confidence", "low"), 0) >= floor

    kept = dict(review)

    weak_blockers = [b for b in review["blockers"] if not _clears(b, CONFIDENCE_RANK["medium"])]
    if weak_blockers:
        log.warning("%d blocker(s) arrived below medium confidence — keeping but flagging", len(weak_blockers))
    kept["blockers"] = review["blockers"]

    kept["major"] = [m for m in review["major"] if _clears(m, threshold)]

    if include_nits:
        kept["minor"] = [m for m in review["minor"] if _clears(m, CONFIDENCE_RANK["high"])]
    else:
        kept["minor"] = []

    dropped = (
        (len(review["major"]) - len(kept["major"]))
        + (len(review["minor"]) - len(kept["minor"]))
    )
    if dropped:
        log.info(
            "Filtered out %d low-confidence/nit finding(s) (min_confidence=%s, include_nits=%s)",
            dropped,
            min_confidence,
            include_nits,
        )
    return kept


def derive_verdict(review: dict, *, ci_failing: bool) -> str:
    """Recompute the verdict from the filtered findings so it can't drift from what's shown.

    Preserves ``cannot_review`` (a context problem, not a findings problem). Otherwise only
    blockers or failing CI justify ``request_changes`` — major/minor are advisory.
    """
    if review["verdict"] == "cannot_review":
        return "cannot_review"
    if ci_failing or review["blockers"]:
        return "request_changes"
    if review["major"] or review["minor"]:
        return "approve_with_comments"
    return "approve"


def render_markdown(meta: dict, review: dict, ci_summary: str, diff_truncated: bool) -> str:
    author = (meta.get("author") or {}).get("login") or "unknown"
    title = meta.get("title", "")
    number = meta.get("number", "?")
    base = meta.get("baseRefName", "?")
    head = meta.get("headRefName", "?")
    additions = meta.get("additions", 0)
    deletions = meta.get("deletions", 0)
    files = meta.get("changedFiles", 0)
    draft = "yes" if meta.get("isDraft") else "no"

    lines: list[str] = [
        f"## Review — PR #{number}: {title}",
        "",
        f"**Author:** @{author} · **Base:** `{base}` ← **Head:** `{head}`  ",
        f"**Stats:** +{additions}/-{deletions} across {files} file(s) · Draft: {draft}  ",
        f"**CI:** {ci_summary}",
        "",
        f"**Verdict:** {VERDICT_RENDER[review['verdict']]}",
    ]
    if review["summary"]:
        lines.extend(["", review["summary"]])
    if diff_truncated:
        lines.extend(["", "> ⚠️ Diff was truncated — review may be incomplete."])

    has_any_finding = any(
        review[key] for key in ("blockers", "major", "minor", "out_of_scope", "questions")
    )
    if review["verdict"] == "approve" and not has_any_finding:
        lines.extend(["", "Nothing to flag — looks good to merge. ✨"])

    def _render_findings(heading: str, items: list[dict]) -> None:
        if not items:
            return
        lines.extend(["", f"### {heading}"])
        for it in items:
            loc = it["location"] or "_(no location)_"
            bullet = f"- `{loc}` — {it['description']}"
            if it.get("why_it_matters"):
                bullet += f" — {it['why_it_matters']}"
            if it["suggested_fix"]:
                bullet += f" _Suggested fix:_ {it['suggested_fix']}"
            lines.append(bullet)

    def _render_strings(heading: str, items: list[str]) -> None:
        if not items:
            return
        lines.extend(["", f"### {heading}"])
        for s in items:
            lines.append(f"- {s}")

    _render_findings("Blockers", review["blockers"])
    _render_findings("Major issues", review["major"])
    _render_findings("Minor issues / nits", review["minor"])
    _render_strings("Praise", review["praise"])
    _render_strings("Out of scope (won't block merge)", review["out_of_scope"])
    _render_strings("Questions for the author", review["questions"])

    return "\n".join(lines).rstrip() + "\n"


def comment_body(report: str) -> str:
    return f"{COMMENT_MARKER}\n\n{report.strip()}\n"


def find_existing_comment(repo: str, pr_number: str) -> int | None:
    raw = run_gh([
        "api",
        f"repos/{repo}/issues/{pr_number}/comments",
        "--paginate",
        "--jq",
        f'.[] | select(.body | contains("{COMMENT_MARKER}")) | .id',
    ])
    ids = [line.strip() for line in raw.splitlines() if line.strip()]
    return int(ids[-1]) if ids else None


def post_or_update_comment(repo: str, pr_number: str, body: str) -> None:
    existing_id = find_existing_comment(repo, pr_number)
    if existing_id:
        payload = json.dumps({"body": body})
        run_gh(
            ["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{existing_id}", "--input", "-"],
            input_text=payload,
        )
        log.info("Updated existing review comment id=%s", existing_id)
    else:
        run_gh(["pr", "comment", pr_number, "--body-file", "-"], input_text=body)
        log.info("Posted new review comment")


def post_skip_comment(repo: str, pr_number: str, reason: str) -> str:
    report = f"## Review — PR #{pr_number}: skipped\n\n**Verdict:** 🚧 Cannot review — {reason}\n"
    post_or_update_comment(repo, pr_number, comment_body(report))
    return report


def write_step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown)
            fh.write("\n")
    except OSError as exc:
        log.warning("Could not write step summary: %s", exc)


def write_github_output(verdict: str, blockers: bool) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"verdict={verdict}\n")
            fh.write(f"has_blockers={'true' if blockers else 'false'}\n")
    except OSError as exc:
        log.warning("Could not write GITHUB_OUTPUT: %s", exc)


def has_blockers(review: dict) -> bool:
    return review["verdict"] == "request_changes" or bool(review["blockers"])


def run(cfg: Config) -> int:
    meta = fetch_pr_metadata(cfg.pr_number)

    if cfg.skip_draft and meta.get("isDraft"):
        print(f"::notice::Code review skipped — PR #{cfg.pr_number} is a draft", file=sys.stderr)
        log.info("Skipping draft PR #%s", cfg.pr_number)
        return 0

    diff = fetch_pr_diff(cfg.pr_number)
    diff_lines = len(diff.splitlines()) if diff else 0
    diff_truncated = False

    skip_threshold = cfg.max_diff_lines * cfg.max_diff_skip_multiplier
    if diff_lines > skip_threshold:
        reason = (
            f"diff exceeds {skip_threshold} lines ({diff_lines} lines — "
            f"{cfg.max_diff_skip_multiplier}× the review limit). "
            "Split the PR or raise max_diff_lines."
        )
        print(f"::notice::Code review skipped — {reason}", file=sys.stderr)
        markdown = post_skip_comment(cfg.repo, cfg.pr_number, reason)
        write_step_summary(markdown)
        return 0

    if diff_lines > cfg.max_diff_lines:
        diff = "\n".join(diff.splitlines()[:cfg.max_diff_lines])
        diff_truncated = True
        print(
            f"::warning::Diff truncated from {diff_lines} to {cfg.max_diff_lines} lines — review may be incomplete",
            file=sys.stderr,
        )
        log.warning("Diff truncated from %d to %d lines for review", diff_lines, cfg.max_diff_lines)

    checks = fetch_pr_checks(cfg.pr_number)
    ci_summary, ci_failing = summarize_ci(checks)

    system_prompt = load_system_prompt(cfg.action_path)
    repo_context = build_context()
    user_message = build_user_message(meta, diff, ci_summary, repo_context)

    raw_review = call_litellm(cfg, system_prompt, user_message)
    review = normalize_review(raw_review)
    review = filter_findings(
        review,
        min_confidence=cfg.min_confidence,
        include_nits=cfg.include_nits,
    )
    review["verdict"] = derive_verdict(review, ci_failing=ci_failing)
    write_github_output(review["verdict"], has_blockers(review))
    markdown = render_markdown(meta, review, ci_summary, diff_truncated)
    post_or_update_comment(cfg.repo, cfg.pr_number, comment_body(markdown))
    write_step_summary(markdown)

    blocked = has_blockers(review) or ci_failing
    if blocked and cfg.fail_on_blockers:
        if ci_failing:
            print("::error::CI checks are failing — review blocked", file=sys.stderr)
            log.error("CI checks are failing — treating as blocker")
        if has_blockers(review):
            print("::error::Review found blockers — see PR comment for details", file=sys.stderr)
            log.error("Review found blockers")
        return 1

    print(f"::notice::Code review complete — verdict: {review['verdict']}", file=sys.stderr)
    log.info("Review completed successfully")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        cfg = Config.from_env()
        return run(cfg)
    except ReviewError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
