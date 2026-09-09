"""Offline compatibility regression; saved Case15 remains a historical FAIL."""
import copy
import json
import unittest

from lumina_next.visual_perception import (
    INVENTORY_PROMPT, INVENTORY_SCHEMA, SELECTION_PROMPT, SELECTION_SCHEMA,
    PerceptionError, infer, validate_inventory, validate_selection, visual_category,
)


CHOICE = {"intent": "approach", "target_id": 0, "confidence": 1.0, "reason": "One object"}
CASE15 = [
    {"label": "person, black", "color": "black", "bbox": [888, 288, 999, 999]},
    {"label": "sofa, purple", "color": "purple", "bbox": [396, 588, 999, 999]},
    {"label": "rug, purple", "color": "purple", "bbox": [312, 688, 999, 999]},
    {"label": "cushion, purple", "color": "purple", "bbox": [508, 528, 625, 608]},
    {"label": "cushion, purple", "color": "purple", "bbox": [412, 528, 508, 608]},
    {"label": "cushion, purple", "color": "purple", "bbox": [312, 528, 412, 608]},
    {"label": "cushion, purple", "color": "purple", "bbox": [212, 528, 312, 608]},
    {"label": "cushion, purple", "color": "purple", "bbox": [112, 528, 212, 608]},
]
CASE15_CHOICE = dict(CHOICE, target_id=1, reason="Right side purple sofa matches request.")
CASE15_REQUEST = "右の紫のソファに近づいて"


def one(label, color="purple", box=None):
    return validate_inventory({"objects": [{"label": label, "color": color,
                                           "bbox": box or [200, 300, 600, 800]}]})


class LabelNormalizationTests(unittest.TestCase):
    def test_known_category_comma_color(self):
        for label, color, category in (
            ("sofa, purple", "purple", "sofa"),
            ("  ＣＯＵＣＨ，  Violet  ", "purple", "sofa"),
            ("settee, lavender", "violet", "sofa"),
            ("potted plant, green", "green", "plant"),
            ("coffee table, light brown", "brown", "table"),
            ("chair, grey", "gray", "chair"),
            ("square, red", "light red", "rectangle"),
            ("circle, blue", "blue", "circle"),
            ("triangle, dark green", "dark green", "triangle"),
        ):
            with self.subTest(label=label, color=color):
                self.assertEqual(visual_category(label), category)
                result = validate_selection(CHOICE, one(label, color))
                self.assertTrue(result["visible"])
                self.assertEqual(result["label"], label)

    def test_default_and_existing_prefix_forms(self):
        for label, color, category in (
            ("sofa", "purple", "sofa"), ("couch", "purple", "sofa"),
            ("purple sofa", "purple", "sofa"),
            ("light purple sofa", "purple", "sofa"),
            ("dark green potted plant", "dark green", "plant"),
            ("grey chair", "gray", "chair"),
            ("light sofa", "purple", "sofa"),
            ("rectangle", "red", "rectangle"),
        ):
            with self.subTest(label=label):
                self.assertEqual(visual_category(label), category)
                self.assertTrue(validate_selection(CHOICE, one(label, color))["visible"])

    def test_unknown_lists_negation_and_nonexact_categories_rejected(self):
        for label in (
            "sofa, table", "sofa, purple, chair", "sofa, purple, purple",
            "sofa, not purple", "sofa, purple and green", "sofa, purple color",
            "sofa, unknown", "sofa,", ", purple", "not sofa, purple",
            "sofa-like, purple", "furniture, purple", "sofa chair, purple",
            "purple sofa, purple", "sofa, dark light purple", "sofa purple",
            "red blue sofa", "light dark purple sofa", "not purple sofa",
        ):
            with self.subTest(label=label):
                self.assertIsNone(visual_category(label))
                with self.assertRaisesRegex(PerceptionError, "unresolved_visual_category"):
                    validate_selection(CHOICE, one(label), "ソファに近づいて")

    def test_explicit_label_color_cannot_conflict_with_field_even_without_request(self):
        for label, color in (
            ("sofa, purple", "green"), ("sofa, purple", "not purple"),
            ("sofa, purple", "purple green"), ("sofa, purple", "purple, green"),
            ("sofa, purple", "unknown"), ("sofa, light purple", "dark purple"),
            ("purple sofa", "green"), ("light purple sofa", "dark purple"),
        ):
            for request in (None, "ソファに近づいて", "紫のソファに近づいて"):
                with self.subTest(label=label, color=color, request=request):
                    with self.assertRaisesRegex(PerceptionError, "visual_label_color_mismatch"):
                        validate_selection(CHOICE, one(label, color), request)

    def test_saved_case15_inventory_parses_without_rewriting_evidence(self):
        before = copy.deepcopy(CASE15)
        objects = validate_inventory({"objects": CASE15})
        result = validate_selection(CASE15_CHOICE, objects, CASE15_REQUEST)
        self.assertEqual(CASE15, before)
        self.assertEqual(result["label"], "sofa, purple")
        self.assertEqual(result["target_id"], 1)
        self.assertEqual(result["bbox"], [.396, .588, .999, .999])
        self.assertEqual(result["confidence"], 1.0)
        self.assertTrue(result["visible"])

    def test_comma_and_plain_duplicates_stay_ambiguous(self):
        objects = validate_inventory({"objects": [
            {"label": "sofa, purple", "color": "purple", "bbox": [0, 300, 300, 800]},
            {"label": "purple couch", "color": "purple", "bbox": [700, 300, 1000, 800]},
        ]})
        with self.assertRaisesRegex(PerceptionError, "ambiguous_visual_target"):
            validate_selection(CHOICE, objects, "紫のソファに近づいて")
        self.assertTrue(validate_selection(CHOICE, objects, "左の紫のソファに近づいて")["visible"])
        with self.assertRaisesRegex(PerceptionError, "ambiguous_or_wrong_visual_side"):
            validate_selection(CHOICE, objects, "右の紫のソファに近づいて")

    def test_contradictory_duplicate_is_not_silently_filtered(self):
        objects = validate_inventory({"objects": [
            {"label": "sofa, purple", "color": "purple", "bbox": [0, 300, 300, 800]},
            {"label": "sofa, purple", "color": "green", "bbox": [700, 300, 1000, 800]},
        ]})
        with self.assertRaisesRegex(PerceptionError, "visual_label_color_mismatch"):
            validate_selection(CHOICE, objects, "紫のソファに近づいて")

    def test_invalid_known_category_competitor_cannot_manufacture_uniqueness(self):
        for label in ("red blue sofa", "light dark purple sofa", "sofa, purple, chair",
                      "sofa, not purple", "sofa, unknown", "purple sofa, unknown"):
            for color in ("purple", "green"):
                for request in ("ソファに近づいて", "紫のソファに近づいて", "左の紫のソファに近づいて"):
                    with self.subTest(label=label, color=color, request=request):
                        objects = validate_inventory({"objects": [
                            {"label": "sofa, purple", "color": "purple", "bbox": [0, 300, 300, 800]},
                            {"label": label, "color": color, "bbox": [700, 300, 1000, 800]},
                        ]})
                        with self.assertRaisesRegex(PerceptionError, "ambiguous_visual_category_evidence"):
                            validate_selection(CHOICE, objects, request)

    def test_unrelated_unknown_labels_do_not_block_whole_inventory(self):
        for label in ("person, black", "rug, purple", "cushion, purple", "furniture",
                      "red blue chair", "table, unknown"):
            with self.subTest(label=label):
                objects = validate_inventory({"objects": [
                    {"label": "sofa, purple", "color": "purple", "bbox": [0, 300, 300, 800]},
                    {"label": label, "color": "purple", "bbox": [700, 300, 1000, 800]},
                ]})
                self.assertTrue(validate_selection(CHOICE, objects, "左の紫のソファに近づいて")["visible"])

    def test_existing_color_and_side_guards_still_apply(self):
        for request, box, error in (
            ("赤いソファ", [200, 300, 600, 800], "visual_color_mismatch"),
            ("左のソファ", [400, 300, 600, 800], "ambiguous_or_wrong_visual_side"),
            ("左のソファ", [0, 300, 999, 800], "visual_side_box_too_wide"),
            ("中央のソファ", [700, 300, 1000, 800], "ambiguous_or_wrong_visual_center"),
            ("椅子に近づいて", [200, 300, 600, 800], "visual_category_mismatch"),
        ):
            with self.subTest(request=request):
                with self.assertRaisesRegex(PerceptionError, error):
                    validate_selection(CHOICE, one("sofa, purple", box=box), request)

    def test_absent_selection_remains_absent(self):
        result = validate_selection(dict(CHOICE, target_id=-1, confidence=0),
                                    one("sofa, purple"), "緑の植物が見える？")
        self.assertFalse(result["visible"])
        self.assertEqual(result["bbox"], [0, 0, 0, 0])


class InferenceNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def fake_infer(self, inventory, choice, request):
        calls = []

        async def http(method, path, body=None, *, model=False):
            calls.append((method, path, body, model))
            if path == "/props":
                return {"modalities": {"vision": True}}
            answer = {"objects": [[obj["label"], obj["color"], *obj["bbox"]]
                                  for obj in inventory]} if len(calls) == 2 else choice
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}

        return await infer(http, b"\x89PNG\r\n\x1a\nfixture", request), calls

    async def test_saved_case15_mock_retains_raw_audit_and_unchanged_wire_contract(self):
        result, calls = await self.fake_infer(CASE15, CASE15_CHOICE, "お願いします、" + CASE15_REQUEST)
        audit = result["perception_audit"]
        self.assertEqual(audit["inventory"], validate_inventory({"objects": CASE15}))
        self.assertEqual(audit["selection"], CASE15_CHOICE)
        self.assertEqual(result["label"], "sofa, purple")
        self.assertFalse(audit["target_sent_to_visual_pass"])
        self.assertFalse(audit["world_metadata_sent"])
        visual, selection = calls[1][2], calls[2][2]
        self.assertEqual(visual["messages"][0]["content"][0]["text"], INVENTORY_PROMPT)
        self.assertEqual(selection["messages"][0]["content"], SELECTION_PROMPT)
        self.assertNotIn(CASE15_REQUEST, json.dumps(visual, ensure_ascii=False))
        self.assertEqual(json.loads(selection["messages"][1]["content"])["inventory"], audit["inventory"])
        self.assertNotIn("image_url", json.dumps(selection))
        self.assertEqual((visual["max_tokens"], selection["max_tokens"]), (550, 160))
        self.assertEqual(visual["response_format"]["schema"], INVENTORY_SCHEMA)
        self.assertEqual(selection["response_format"]["schema"], SELECTION_SCHEMA)
        self.assertTrue(all(call[3] for call in calls))

    async def test_conflict_keeps_raw_failure_audit(self):
        inventory = [{"label": "sofa, purple", "color": "green", "bbox": [200, 300, 600, 800]}]
        with self.assertRaisesRegex(PerceptionError, "visual_label_color_mismatch") as caught:
            await self.fake_infer(inventory, CHOICE, "お願いします、ソファに近づいて")
        self.assertEqual(caught.exception.audit["inventory"], validate_inventory({"objects": inventory}))
        self.assertEqual(caught.exception.audit["selection"], CHOICE)
        self.assertIn("selection_seconds", caught.exception.audit)


if __name__ == "__main__":
    unittest.main()
