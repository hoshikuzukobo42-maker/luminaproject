"""Deterministic Gemma 4 E4B candidate style guards.  No model load."""

from __future__ import annotations

import re
from typing import Mapping

CARE_RE = re.compile(
    r"(今日もいるよ|休んでいいよ|隣にいるよ|無理しないで|"
    r"今できる一手|一手だけ|材料にして|了解。|"
    r"ゆっくりして。|気をつけてね。)"
)
LECTURE_RE = re.compile(r"(まずは|次に|最後に|手順としては|ポイントは)")
BULLET_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+")
ROLE_RE = re.compile(r"(?:user|lumina|Human|Assistant)\s*[:：]", re.IGNORECASE)

_FALLBACK = "ん"


def _retrieve_fallback(user: str) -> str:
    query = str(user or "").strip()
    if not query:
        return _FALLBACK
    try:
        from .gemma4_e4b_rag import best_lumina_line

        line = best_lumina_line(query)
    except Exception:
        return _FALLBACK
    body = str(line or "").strip()
    if not body or CARE_RE.search(body) or len(body) > 80:
        return _FALLBACK
    if not re.search(r"[\w\u3040-\u30ff\u3400-\u9fff]", body):
        return _FALLBACK
    return body


def sanitize_candidate_reply(text: str, *, user: str = "") -> str:
    """Strip care-scripts and lecture chrome.  Empty → retrieve or ん.

    Caller must treat a changed string as a LiteRT history reset.
    Retrieved fallback is display-only; it is not injected into KV before generate.
    """
    raw = str(text or "").strip()
    if not raw:
        return _retrieve_fallback(user)
    had_care = bool(CARE_RE.search(raw))
    cleaned = CARE_RE.sub("", raw)
    cleaned = ROLE_RE.sub("", cleaned)
    cleaned = BULLET_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n", cleaned).strip()
    cleaned = re.sub(r"^[。．、,.\s]+", "", cleaned).strip()
    if not cleaned or CARE_RE.search(cleaned) or not re.search(r"[\w\u3040-\u30ff\u3400-\u9fff]", cleaned):
        return _retrieve_fallback(user)
    if had_care and re.match(r"^[はがをにでとへも]", cleaned):
        return _retrieve_fallback(user)
    lecture_hits = LECTURE_RE.findall(cleaned)
    if len(lecture_hits) >= 2 or (lecture_hits and len(cleaned) > 24):
        first = re.split(r"[。！？\n]", cleaned, maxsplit=1)[0].strip()
        cleaned = first or _retrieve_fallback(user)
    if len(cleaned) > 80:
        first = re.split(r"[。！？]", cleaned, maxsplit=1)[0].strip()
        if 1 <= len(first) <= 80:
            cleaned = first
        else:
            cleaned = cleaned[:80].rstrip()
    return cleaned or _retrieve_fallback(user)


def style_flags(text: str) -> tuple[str, ...]:
    flags: list[str] = []
    raw = str(text or "")
    if CARE_RE.search(raw):
        flags.append("care_script")
    if BULLET_RE.search(raw):
        flags.append("bullets")
    if ROLE_RE.search(raw):
        flags.append("role_leak")
    lecture_hits = LECTURE_RE.findall(raw)
    if len(lecture_hits) >= 2 or (lecture_hits and len(raw) > 24):
        flags.append("lecture")
    return tuple(flags)


def evaluate_reply(user: str, answer: str) -> dict[str, object]:
    flags = style_flags(answer)
    sanitized = sanitize_candidate_reply(answer, user=user)
    return {
        "user": user,
        "answer": answer,
        "sanitized": sanitized,
        "changed": sanitized != str(answer or "").strip(),
        "flags": list(flags),
        "pass": not flags,
    }


def evaluate_cases(cases: Mapping[str, Mapping[str, str]]) -> dict[str, object]:
    reports = []
    failed = 0
    for name, case in cases.items():
        report = evaluate_reply(str(case.get("user") or ""), str(case.get("answer") or ""))
        report["name"] = name
        expected_fail = bool(case.get("expect_flags"))
        ok = (not report["pass"]) if expected_fail else bool(report["pass"])
        report["case_ok"] = ok
        if not ok:
            failed += 1
        reports.append(report)
    return {
        "failed": failed,
        "total": len(reports),
        "pass": failed == 0,
        "reports": reports,
    }
