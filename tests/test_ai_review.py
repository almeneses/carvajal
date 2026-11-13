"""Pruebas de la selección de hallazgos del revisor automático de PRs."""

import importlib.util
import sys
import urllib.error
from copy import deepcopy
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "ai_review.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ai_review", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ai_review = _load()

DIFF = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 context
-old
+new
@@ -8,0 +9 @@
+++ b/literal
diff --git a/deleted.py b/deleted.py
--- a/deleted.py
+++ /dev/null
@@ -1 +0,0 @@
-old
"""


def _finding(line: int, severity: str, comment: str = "detalle") -> dict[str, Any]:
    return {"path": "a.py", "line": line, "severity": severity, "comment": comment}


def test_anchors_are_on_the_right_side_of_the_diff() -> None:
    assert ai_review.parse_anchor_lines(DIFF) == {"a.py": {1, 2, 9}}


def test_select_findings_keeps_valid_p0_and_p1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_INLINE", "2")
    p0, p1 = _finding(9, "P0"), _finding(2, "P1")
    data = {
        "findings": [
            p1,
            _finding(1, "P2"),
            _finding(2, "P0", ""),
            {**p0, "line": True},
            {**p0, "path": "otro.py"},
            {**p0, "severity": "P1"},
            p0,
            _finding(1, "P1"),
        ]
    }

    assert ai_review.select_findings(data, {"a.py": {1, 2, 9}}) == [p0, p1]


@pytest.mark.parametrize("reject_anchor", [False, True])
def test_native_comment_review_uses_head_commit(
    monkeypatch: pytest.MonkeyPatch, reject_anchor: bool
) -> None:
    calls = []

    def github_request(method: str, path: str, token: str, payload: dict[str, Any]) -> None:
        assert (method, path, token) == ("POST", "/repos/owner/repo/pulls/42/reviews", "token")
        calls.append(deepcopy(payload))
        if reject_anchor and len(calls) == 1:
            raise urllib.error.HTTPError(path, 422, "invalid line", Message(), None)

    monkeypatch.setattr(ai_review, "github_request", github_request)
    p0 = {**_finding(2, "P0"), "suggestion": "corrección()"}
    p1 = _finding(50, "P1", "sin ancla")

    ai_review.publish_review("owner/repo", "42", "abc", "token", [p0, p1], {"a.py": {2}}, True)

    review = calls[0]
    assert review["event"] == "COMMENT"
    assert review["commit_id"] == "abc"
    assert review["comments"][0] == {
        "path": "a.py",
        "line": 2,
        "side": "RIGHT",
        "body": "**[P0]** detalle\n\n```suggestion\ncorrección()\n```",
    }
    assert "sin ancla" in review["body"]
    assert "parcial" in review["body"]
    if reject_anchor:
        assert len(calls) == 2
        assert calls[-1]["comments"] == []
        assert "detalle" in calls[-1]["body"]
    else:
        assert len(calls) == 1


@pytest.mark.parametrize("manual", [False, True])
def test_clean_review_is_only_published_on_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, manual: bool
) -> None:
    diff = tmp_path / "pr.diff"
    diff.write_text(DIFF)
    argv = ["ai_review.py", "--diff", str(diff)] + (["--manual"] if manual else [])
    monkeypatch.setattr(sys, "argv", argv)
    for name, value in {
        "GITHUB_REPOSITORY": "owner/repo",
        "PR_NUMBER": "42",
        "PR_HEAD_SHA": "abc",
        "GITHUB_TOKEN": "token",
        "DEEPSEEK_API_KEY": "test",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(ai_review, "ask_deepseek", lambda *_: ('{"findings": []}', "stop"))
    published: list[dict[str, Any]] = []
    monkeypatch.setattr(ai_review, "github_request", lambda *args: published.append(args[-1]))

    assert ai_review.main() == 0
    assert len(published) == int(manual)
    if published:
        assert "no encontró hallazgos" in published[0]["body"]
