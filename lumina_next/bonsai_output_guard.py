"""Final-only inspection + repair hooks for Bonsai Q1 outputs."""

from __future__ import annotations

from typing import Any

from tools.bonsai_promotion_v2.detector import detect_japanese_anomalies
from tools.bonsai_promotion_v2.self_repair import (
    compress_one_sentence,
    rewrite_natural_ja,
    structured_json_rebuild,
)


def inspect_final(
    text: str,
    *,
    expect_json: bool = False,
    required_json_keys: list[str] | None = None,
) -> dict[str, Any]:
    return detect_japanese_anomalies(
        text or "",
        expect_json=expect_json,
        required_json_keys=required_json_keys,
    )


def guard_and_repair(
    text: str,
    *,
    client: Any | None = None,
    messages: list[dict[str, str]] | None = None,
    expect_json: bool = False,
    required_json_keys: list[str] | None = None,
    enable_repair: bool = True,
) -> dict[str, Any]:
    report = inspect_final(text, expect_json=expect_json, required_json_keys=required_json_keys)
    if not report["is_anomalous"]:
        return {"text": text, "repaired": False, "report": report, "ok": True}

    if not enable_repair:
        return {"text": text, "repaired": False, "report": report, "ok": False}

    if expect_json:
        fixed = structured_json_rebuild(
            text, required_keys=required_json_keys, client=client, messages=messages
        )
    else:
        fixed = rewrite_natural_ja(text, client=client, messages=messages)
        report2 = inspect_final(fixed)
        if report2["is_anomalous"]:
            fixed = compress_one_sentence(fixed, client=client, messages=messages)

    final_report = inspect_final(fixed, expect_json=expect_json, required_json_keys=required_json_keys)
    if final_report["is_anomalous"] and not expect_json:
        fixed = "少し調子が乱れました。もう一度、短く言い直しますね。"
        final_report = inspect_final(fixed)
    return {
        "text": fixed,
        "repaired": True,
        "report": final_report,
        "ok": not final_report["is_anomalous"],
        "first_report": report,
    }
