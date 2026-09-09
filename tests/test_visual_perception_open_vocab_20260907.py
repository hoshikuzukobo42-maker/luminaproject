"""Focused safety tests for the portable-only open-vocabulary selector."""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from lumina_next.visual_perception import (
    INVENTORY_PROMPT,
    OPEN_VOCAB_CONFIDENCE_THRESHOLD,
    OPEN_VOCAB_JA_VERIFY_SCHEMA,
    OPEN_VOCAB_SELECTION_POLICY_REVISION,
    OPEN_VOCAB_SEMANTIC_CONFIDENCE_THRESHOLD,
    PerceptionError,
    infer,
    open_visual_descriptor,
)


IMAGE = b"\x89PNG\r\n\x1a\nopen-vocabulary-fixture"


def _row(label: str, color: str, box: list[int] | None = None) -> list[Any]:
    return [label, color, *(box or [200, 200, 600, 800])]


def _choice(
    label: str,
    category: str,
    *,
    target_id: int = 0,
    confidence: float = .93,
) -> dict[str, Any]:
    return {
        "intent": "approach" if target_id >= 0 else "none",
        "target_id": target_id,
        "target_label": label if target_id >= 0 else "",
        "requested_category": category if target_id >= 0 else "",
        "confidence": confidence if target_id >= 0 else 0,
        "reason": "direct category match" if target_id >= 0 else "not present",
    }


async def _infer_mocked(
    request: str,
    inventory: list[list[Any]],
    selection: dict[str, Any],
    *,
    open_vocabulary: bool = True,
    semantic_verification: dict[str, Any] | None = None,
) -> tuple[dict, list[tuple[str, str, dict | None, bool]]]:
    calls: list[tuple[str, str, dict | None, bool]] = []

    async def http(method: str, path: str, body: dict | None = None, *, model: bool = False) -> dict:
        calls.append((method, path, body, model))
        if path == "/props":
            return {"modalities": {"vision": True}}
        if len(calls) == 2:
            answer = {"objects": inventory}
        elif body is not None and body["response_format"]["schema"] == OPEN_VOCAB_JA_VERIFY_SCHEMA:
            translations = (
                ("fire extinguisher", "fire extinguisher"),
                ("消火器", "fire extinguisher"),
                ("plant pot", "plant pot"), ("植木鉢", "plant pot"),
                ("coffee table", "table"),
                ("ソファ", "sofa"),
                ("椅子", "chair"), ("いす", "chair"), ("chair", "chair"),
                ("ランプ", "lamp"), ("lamp", "lamp"),
                ("ドア", "door"), ("door", "door"),
                ("ボトル", "bottle"), ("bottle", "bottle"), ("瓶", "bottle"),
            )
            translated = next(
                ((source, category) for source, category in translations if source in request),
                ("", ""),
            )
            answer = semantic_verification or {
                "explicit_category": bool(translated[0]),
                "source_span": translated[0],
                "translated_category": translated[1],
                "ambiguous": not bool(translated[0]),
                "confidence": .96 if translated[0] else 0,
                "reason": "one explicit noun" if translated[0] else "no explicit noun",
            }
        else:
            answer = selection
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}

    result = await infer(http, IMAGE, request, open_vocabulary=open_vocabulary)
    return result, calls


@pytest.mark.parametrize(
    ("visitor_text", "label", "color", "category"),
    [
        ("ランプまで行って", "lamp", "yellow", "lamp"),
        ("go to the lamp", "lamp", "yellow", "lamp"),
        ("ドアに近づいて", "door", "brown", "door"),
        ("approach the door", "door", "brown", "door"),
        ("青いボトルまで行って", "bottle", "blue", "bottle"),
        ("walk to the blue bottle", "bottle", "blue", "bottle"),
        ("消火器まで行って", "fire extinguisher", "red", "fire extinguisher"),
        ("go to the fire extinguisher", "fire extinguisher", "red", "fire extinguisher"),
    ],
)
def test_portable_open_vocab_maps_safe_japanese_or_english_exact_category(
    visitor_text: str,
    label: str,
    color: str,
    category: str,
) -> None:
    result, calls = asyncio.run(
        _infer_mocked(visitor_text, [_row(label, color)], _choice(label, category))
    )

    assert result["visible"] is True
    assert (result["label"], result["category"], result["color"]) == (label, category, color)
    assert result["confidence"] >= OPEN_VOCAB_CONFIDENCE_THRESHOLD
    audit = result["perception_audit"]
    assert audit["selection_policy_revision"] == OPEN_VOCAB_SELECTION_POLICY_REVISION
    assert audit["selection_basis"] == "qwen_open_vocab_text_selection_best_effort"
    assert audit["confidence_basis"] == "qwen_self_report_thresholded_not_calibrated_probability"
    assert audit["exact_inventory_id_label_verified"] is True
    assert "not_arbitrary_recognition" in audit["capability_scope"]

    visual_payload = calls[1][2]
    selector_payload = calls[2][2]
    assert visual_payload is not None and selector_payload is not None
    assert visual_payload["messages"][0]["content"][0]["text"] == INVENTORY_PROMPT
    assert visitor_text not in json.dumps(visual_payload, ensure_ascii=False)
    encoded = visual_payload["messages"][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == IMAGE
    assert "image_url" not in json.dumps(selector_payload)
    assert all(call[3] is True for call in calls)
    verification_payload = calls[3][2]
    assert verification_payload is not None
    assert verification_payload["response_format"]["schema"] == OPEN_VOCAB_JA_VERIFY_SCHEMA
    verification_wire = json.dumps(verification_payload, ensure_ascii=False)
    assert "inventory" not in verification_wire
    assert "image_url" not in verification_wire
    assert json.loads(verification_payload["messages"][1]["content"]) == {
        "visitor": visitor_text,
    }
    assert verification_payload["seed"] == 7
    assert audit["semantic_verification_confidence_threshold"] == OPEN_VOCAB_SEMANTIC_CONFIDENCE_THRESHOLD
    assert audit["semantic_verification_independence_scope"].endswith(
        "not_independent_model"
    )
    assert len(calls) == 4


def test_closed_vocab_default_still_rejects_lamp() -> None:
    with pytest.raises(PerceptionError, match="unresolved_visual_category"):
        asyncio.run(
            _infer_mocked(
                "go to the lamp",
                [_row("lamp", "yellow")],
                {
                    "intent": "approach",
                    "target_id": 0,
                    "confidence": .99,
                    "reason": "lamp",
                },
                open_vocabulary=False,
            )
        )


def test_known_japanese_navigation_command_does_not_depend_on_qwen_selector_intent() -> None:
    result, calls = asyncio.run(
        _infer_mocked(
            "ソファまで行って",
            [_row("sofa", "purple")],
            _choice("", "", target_id=-1),
        )
    )

    assert result["visible"] is True
    assert result["intent"] == "approach"
    assert (result["label"], result["category"], result["color"]) == (
        "sofa", "sofa", "purple",
    )
    assert result["perception_audit"]["selection_basis"] == (
        "closed_japanese_grammar_validated_open_inventory"
    )
    # Only props plus the target-free image inventory are model calls. The
    # deliberately wrong selector/verifier answers are never requested.
    assert len(calls) == 2
    audit = result["perception_audit"]
    assert audit["semantic_verification_basis"] == "closed_japanese_grammar_exact_noun"
    assert audit["semantic_verification_independence_scope"].endswith("not_model")


def test_exact_color_category_revalidation_needs_only_target_free_image_inventory() -> None:
    result, calls = asyncio.run(
        _infer_mocked(
            "go to the purple sofa",
            [_row("sofa", "purple")],
            _choice("", "", target_id=-1),
        )
    )

    assert result["visible"] is True
    assert (result["label"], result["category"], result["color"]) == (
        "sofa", "sofa", "purple",
    )
    assert len(calls) == 2
    audit = result["perception_audit"]
    assert audit["selection_basis"] == (
        "closed_exact_color_category_validated_open_inventory"
    )
    assert audit["semantic_verification_basis"] == (
        "closed_exact_english_color_category_grammar"
    )
    assert audit["semantic_verification"]["source_span"] == "sofa"


def test_exact_category_only_continuity_selection_is_unique_and_target_free() -> None:
    result, calls = asyncio.run(
        _infer_mocked(
            "revalidate unique visible category: sofa",
            [_row("sofa", "pink")],
            _choice("", "", target_id=-1),
        )
    )

    assert result["visible"] is True
    assert (result["category"], result["color"]) == ("sofa", "pink")
    assert len(calls) == 2
    audit = result["perception_audit"]
    assert audit["selection_basis"] == "closed_exact_category_validated_open_inventory"
    assert audit["semantic_verification_basis"] == "closed_exact_english_category_grammar"


def test_exact_category_only_continuity_selection_rejects_multiple_candidates() -> None:
    result, calls = asyncio.run(
        _infer_mocked(
            "revalidate unique visible category: sofa",
            [
                _row("sofa", "purple", box=[100, 300, 400, 700]),
                _row("sofa", "pink", box=[600, 300, 900, 700]),
            ],
            _choice("", "", target_id=-1),
        )
    )

    assert result["visible"] is False
    assert result["target_id"] == -1
    assert len(calls) == 2
    assert result["perception_audit"]["base_color_candidate_count"] == 2


def test_exact_revalidation_accepts_only_same_base_color_shade_variation() -> None:
    result, calls = asyncio.run(
        _infer_mocked(
            "go to the purple sofa",
            [_row("purple sofa", "light purple")],
            _choice("", "", target_id=-1),
        )
    )

    assert result["visible"] is True
    assert (result["category"], result["color"]) == ("sofa", "light purple")
    assert len(calls) == 2
    audit = result["perception_audit"]
    assert audit["selection_basis"] == (
        "closed_exact_base_color_category_validated_open_inventory"
    )
    assert audit["base_color_continuity_match"] is True


def test_exact_revalidation_rejects_different_base_color_without_model_fallback() -> None:
    result, calls = asyncio.run(
        _infer_mocked(
            "go to the purple sofa",
            [_row("pink sofa", "pink")],
            _choice("", "", target_id=-1),
        )
    )

    assert result["visible"] is False
    assert result["intent"] == "none"
    assert len(calls) == 2


def test_exact_revalidation_rejects_opposing_explicit_shades_without_model_fallback() -> None:
    result, calls = asyncio.run(
        _infer_mocked(
            "go to the light purple sofa",
            [_row("dark purple sofa", "dark purple")],
            _choice("", "", target_id=-1),
        )
    )

    assert result["visible"] is False
    assert result["intent"] == "none"
    assert len(calls) == 2


def test_exact_revalidation_same_base_color_ambiguity_fails_closed() -> None:
    result, calls = asyncio.run(
        _infer_mocked(
            "go to the purple sofa",
            [
                _row("sofa", "purple", box=[100, 300, 400, 700]),
                _row("sofa", "light purple", box=[600, 300, 900, 700]),
            ],
            _choice("", "", target_id=-1),
        )
    )

    assert result["visible"] is False
    assert result["target_id"] == -1
    assert len(calls) == 2
    audit = result["perception_audit"]
    assert audit["unique_validated_match"] is False
    assert audit["base_color_candidate_count"] == 2


@pytest.mark.parametrize(
    "selection",
    [
        _choice("lamp", "lamp", confidence=OPEN_VOCAB_CONFIDENCE_THRESHOLD - .01),
        _choice("LAMP", "lamp"),
        _choice("lamp", "light"),
    ],
)
def test_open_vocab_rejects_low_confidence_or_nonexact_id_label_category(
    selection: dict[str, Any],
) -> None:
    with pytest.raises(PerceptionError):
        asyncio.run(_infer_mocked("go to the lamp", [_row("lamp", "yellow")], selection))


def test_open_vocab_rejects_approximate_substitute_and_ambiguous_duplicates() -> None:
    with pytest.raises(PerceptionError, match="open_vocab_approximate_substitute"):
        asyncio.run(
            _infer_mocked("go to the light", [_row("lamp", "yellow")], _choice("lamp", "lamp"))
        )
    with pytest.raises(PerceptionError, match="ambiguous_visual_target"):
        asyncio.run(
            _infer_mocked(
                "ランプまで行って",
                [_row("lamp", "yellow", [50, 200, 350, 800]),
                 _row("lamp", "yellow", [650, 200, 950, 800])],
                _choice("lamp", "lamp"),
            )
        )


@pytest.mark.parametrize(
    ("verification", "error"),
    [
        ({
            "explicit_category": True, "source_span": "ランプ", "translated_category": "door",
            "ambiguous": False, "confidence": .97, "reason": "wrong translation",
        }, "open_vocab_semantic_translation_mismatch"),
        ({
            "explicit_category": True, "source_span": "ランプ", "translated_category": "lamp",
            "ambiguous": False, "confidence": OPEN_VOCAB_SEMANTIC_CONFIDENCE_THRESHOLD - .01,
            "reason": "uncertain",
        }, "open_vocab_semantic_verification_low_confidence"),
        ({
            "explicit_category": False, "source_span": "", "translated_category": "",
            "ambiguous": True, "confidence": 0, "reason": "multiple nouns",
        }, "ambiguous_open_vocab_semantic_verification"),
        ({
            "explicit_category": True, "source_span": "ドア", "translated_category": "lamp",
            "ambiguous": False, "confidence": .97, "reason": "span is absent",
        }, "open_vocab_semantic_source_span_mismatch"),
    ],
)
def test_japanese_unknown_category_requires_independent_exact_text_cross_check(
    verification: dict[str, Any],
    error: str,
) -> None:
    with pytest.raises(PerceptionError, match=error):
        asyncio.run(
            _infer_mocked(
                "ランプまで行って",
                [_row("lamp", "yellow")],
                _choice("lamp", "lamp"),
                semantic_verification=verification,
            )
        )


def test_japanese_unknown_category_rejects_selector_mistranslation_and_vague_request() -> None:
    with pytest.raises(PerceptionError, match="open_vocab_requested_category_mismatch"):
        asyncio.run(
            _infer_mocked(
                "ランプまで行って",
                [_row("lamp", "yellow")],
                _choice("lamp", "door"),
                semantic_verification={
                    "explicit_category": True,
                    "source_span": "ランプ",
                    "translated_category": "lamp",
                    "ambiguous": False,
                    "confidence": .97,
                    "reason": "independent direct translation",
                },
            )
        )
    with pytest.raises(PerceptionError, match="vague_open_vocab_request"):
        asyncio.run(
            _infer_mocked(
                "あれまで行って",
                [_row("lamp", "yellow")],
                _choice("lamp", "lamp"),
                semantic_verification={
                    "explicit_category": False,
                    "source_span": "",
                    "translated_category": "",
                    "ambiguous": True,
                    "confidence": 0,
                    "reason": "pronoun only",
                },
            )
        )


def test_japanese_fire_extinguisher_cannot_be_self_consistently_swapped_for_bottle() -> None:
    # The selector alone used to accept its own requested_category=bottle claim.
    # The visitor-only pass sees the actual noun and independently translates it.
    with pytest.raises(PerceptionError, match="open_vocab_semantic_translation_mismatch"):
        asyncio.run(
            _infer_mocked(
                "消火器まで行って",
                [_row("bottle", "red")],
                _choice("bottle", "bottle", confidence=.99),
                semantic_verification={
                    "explicit_category": True,
                    "source_span": "消火器",
                    "translated_category": "fire extinguisher",
                    "ambiguous": False,
                    "confidence": .98,
                    "reason": "one explicit physical object noun",
                },
            )
        )


@pytest.mark.parametrize(
    "visitor_text",
    [
        "bottleではなく消火器まで行って",
        "not the bottle, go to the fire extinguisher",
        "go to the bottle or the door",
        "ボトルまたはドアまで行って",
        "ボトルではありません、消火器まで行って",
        "ボトルを除いてドアまで行って",
    ],
)
def test_unknown_open_vocab_negation_or_alternatives_reject_even_self_consistent_selector(
    visitor_text: str,
) -> None:
    # Even a second pass repeating the selector's wrong claim cannot override
    # deterministic negation/alternative rejection.
    with pytest.raises(PerceptionError, match="ambiguous_open_vocab_request"):
        asyncio.run(
            _infer_mocked(
                visitor_text,
                [_row("bottle", "red"), _row("door", "brown")],
                _choice("bottle", "bottle", confidence=.99),
                semantic_verification={
                    "explicit_category": True,
                    "source_span": "bottle" if "bottle" in visitor_text else "ボトル",
                    "translated_category": "bottle",
                    "ambiguous": False,
                    "confidence": .99,
                    "reason": "incorrectly repeated selected noun",
                },
            )
        )


def test_portable_open_known_category_cannot_bypass_negation_structure_veto() -> None:
    with pytest.raises(PerceptionError, match="ambiguous_open_vocab_request"):
        asyncio.run(
            _infer_mocked(
                "椅子ではなくランプまで行って",
                [_row("chair", "blue")],
                _choice("chair", "chair", confidence=.99),
            )
        )


@pytest.mark.parametrize(
    "visitor_text",
    [
        "椅子と消火器まで行って",
        "椅子とランプまで行って",
        "椅子かランプまで行って",
        "椅子もランプも行って",
        "椅子、それからランプまで行って",
        "椅子、ランプまで行って",
        "go to chair, lamp",
        "椅子対ランプなら椅子まで行って",
        "avoid chair, go to lamp",
        "go to the chair marked “lamp”",
        "go to the chair marked lamp",
        "go to the chair lamp",
        "椅子まで行ってランプを見て",
        "go to the chair to inspect the lamp",
    ],
)
def test_portable_open_known_category_rejects_unknown_second_target_forms(
    visitor_text: str,
) -> None:
    with pytest.raises(PerceptionError, match="ambiguous_open_vocab_request"):
        asyncio.run(
            _infer_mocked(
                visitor_text,
                [_row("chair", "blue")],
                    _choice("chair", "chair", confidence=.99),
                    semantic_verification={
                        "explicit_category": True,
                        "source_span": "chair" if "chair" in visitor_text else "椅子",
                    "translated_category": "chair",
                    "ambiguous": False,
                    "confidence": .99,
                    "reason": "maliciously ignored second target",
                },
            )
        )


@pytest.mark.parametrize(
    ("visitor_text", "label", "color", "category"),
    [
        ("椅子まで行って", "chair", "blue", "chair"),
        ("椅子のところまで行って", "chair", "blue", "chair"),
        ("左の青い椅子まで行って", "chair", "blue", "chair"),
        ("go to the left blue chair", "chair", "blue", "chair"),
        ("go to the coffee table", "coffee table", "brown", "table"),
    ],
)
def test_portable_open_single_target_particles_and_compound_category_still_pass(
    visitor_text: str,
    label: str,
    color: str,
    category: str,
) -> None:
    result, calls = asyncio.run(
        _infer_mocked(visitor_text, [_row(label, color)], _choice(label, category))
    )

    assert result["visible"] is True
    assert result["category"] == category
    assert result["label"] == label
    expected_calls = 2 if visitor_text.startswith(("椅子", "左の青い椅子")) else 4
    assert len(calls) == expected_calls
    assert result["perception_audit"]["independent_semantic_cross_check_required"] is True


@pytest.mark.parametrize(
    "source_span",
    [
        "消火器と植木鉢", "消火器や植木鉢", "消火器か植木鉢", "消火器も植木鉢も",
        "消火器又は植木鉢", "消火器若しくは植木鉢", "消火器或いは植木鉢",
        "消火器かつ植木鉢", "消火器兼植木鉢", "消火器列挙植木鉢",
        "消火器と椅子", "植木鉢の横の消火器", "植木鉢の隣の消火器",
        "机の上の消火器", "植木鉢なら消火器", "消火器 植木鉢",
        "消火器アンド植木鉢", "消火器プラス植木鉢", "消火器オア植木鉢",
        "消火器バーサス植木鉢", "消火器及植木鉢", "消火器並植木鉢",
        "消火器植木鉢両方", "消火器植木鉢双方", "消火器植木鉢全部",
        "消火器兼用植木鉢", "消火器植木鉢比較", "消火器植木鉢一方",
    ],
)
def test_unknown_japanese_source_span_cannot_swallow_multiple_targets(
    source_span: str,
) -> None:
    with pytest.raises(
        PerceptionError,
        match="(?:unsupported_open_vocab_japanese_source_span|open_vocab_semantic_source_span_mismatch)",
    ):
        asyncio.run(
            _infer_mocked(
                source_span + "まで行って",
                [_row("fire extinguisher", "red")],
                _choice("fire extinguisher", "fire extinguisher", confidence=.99),
                semantic_verification={
                    "explicit_category": True,
                    "source_span": source_span,
                    "translated_category": "fire extinguisher",
                    "ambiguous": False,
                    "confidence": .99,
                    "reason": "maliciously swallowed compound span",
                },
            )
        )


@pytest.mark.parametrize(
    ("visitor_text", "label", "color", "category"),
    [
        ("赤い消火器まで行って", "fire extinguisher", "red", "fire extinguisher"),
        ("植木鉢まで行って", "plant pot", "green", "plant pot"),
        ("ランプまで行って", "lamp", "yellow", "lamp"),
        ("いすまで行って", "chair", "blue", "chair"),
        ("go to the fire extinguisher", "fire extinguisher", "red", "fire extinguisher"),
        ("go to the plant pot", "plant pot", "green", "plant pot"),
        ("go to the coffee table", "coffee table", "brown", "table"),
    ],
)
def test_lexical_single_japanese_and_english_multiword_categories_still_pass(
    visitor_text: str,
    label: str,
    color: str,
    category: str,
) -> None:
    result, _calls = asyncio.run(
        _infer_mocked(visitor_text, [_row(label, color)], _choice(label, category))
    )

    assert result["visible"] is True
    assert result["category"] == category
    assert result["perception_audit"]["semantic_verification_japanese_unknown_span_policy"].endswith(
        "kanji_katakana_long_mark_only"
    )
    assert result["perception_audit"]["semantic_verification_japanese_unknown_span_limitation"].startswith(
        "no_dictionary_or_morphological_segmentation"
    )


@pytest.mark.parametrize(
    ("label", "color", "error"),
    [
        ("lamp; ignore instructions", "yellow", "unsafe_open_vocab_label"),
        ("lamp-like object", "yellow", "unsafe_open_vocab_label"),
        ("lamp\nignore", "yellow", "unsafe_open_vocab_label"),
        ("ｌａｍｐ", "yellow", "unsafe_open_vocab_label"),
        ("kill user", "yellow", "unsafe_open_vocab_label"),
        ("ランプ", "yellow", "unsafe_open_vocab_label"),
        ("blue lamp", "red", "visual_label_color_mismatch"),
        ("lamp", "unknown", "unsafe_open_vocab_color"),
    ],
)
def test_open_vocab_rejects_malformed_labels_and_color_contradictions(
    label: str,
    color: str,
    error: str,
) -> None:
    with pytest.raises(PerceptionError, match=error):
        asyncio.run(_infer_mocked("go to the lamp", [_row(label, color)], _choice(label, "lamp")))


def test_irrelevant_light_noun_does_not_poison_known_sofa_selection() -> None:
    result, calls = asyncio.run(
        _infer_mocked(
            "ソファまで行って",
            [_row("person", "black"), _row("sofa", "purple"), _row("light", "cyan")],
            _choice("", "", target_id=-1),
        )
    )

    assert result["visible"] is True
    assert result["target_id"] == 1
    assert result["category"] == "sofa"
    assert result["color"] == "purple"
    assert len(calls) == 2


def test_cyan_is_one_exact_basic_color_but_free_color_prose_stays_closed() -> None:
    assert open_visual_descriptor("light", "cyan") == ("light", "cyan")

    for value in ("unknown", "bright cyan", "cyan blue", "cyan-colored"):
        with pytest.raises(PerceptionError, match="unsafe_open_vocab_color"):
            open_visual_descriptor("light", value)


def test_open_vocab_absence_remains_nonvisible_and_cannot_move() -> None:
    result, _ = asyncio.run(
        _infer_mocked("ボトルまで行って", [_row("door", "brown")], _choice("", "", target_id=-1))
    )
    assert result["visible"] is False
    assert result["intent"] == "none"
    assert result["target_id"] == -1
    assert result["confidence"] == 0
