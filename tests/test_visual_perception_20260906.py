import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from lumina_next.visual_perception import (
    INVENTORY_PROMPT, INVENTORY_SCHEMA, INVENTORY_WIRE_FORMAT, MODEL, SELECTION_POLICY_REVISION,
    PerceptionError, infer, normalize_inventory_wire, select_inventory_deterministically,
    validate_inventory, validate_selection,
)

OBJ={"label":"rectangle","color":"red","bbox":[100,200,400,700]}
CHOICE={"intent":"approach","target_id":0,"confidence":0.9,"reason":"One red rectangle"}


def compact(obj):
    return [obj["label"], obj["color"], *obj["bbox"]]


class CompactWireTests(unittest.TestCase):
    def test_same_information_order_and_public_dictionary_contract(self):
        original = [dict(OBJ, label="sofa, purple", color="purple", bbox=[0, 1, 999, 1000]),
                    dict(OBJ, label="plant", color="green")]
        raw = {"objects": [compact(obj) for obj in original]}
        before = copy.deepcopy(raw)
        normalized = normalize_inventory_wire(raw)
        self.assertEqual(normalized, validate_inventory({"objects": original}))
        self.assertEqual(raw, before)
        normalized[0]["bbox"][0] = 23
        self.assertEqual(raw, before)
        with self.assertRaises(PerceptionError):
            validate_inventory(raw)

    def test_empty_and_eight_objects_allowed_nine_rejected(self):
        self.assertEqual(normalize_inventory_wire({"objects": []}), [])
        objects = normalize_inventory_wire({"objects": [compact(OBJ) for _ in range(8)]})
        self.assertEqual([obj["id"] for obj in objects], list(range(8)))
        with self.assertRaises(PerceptionError):
            normalize_inventory_wire({"objects": [compact(OBJ) for _ in range(9)]})

    def test_exact_root_and_six_value_lists_only_no_legacy_fallback(self):
        for malformed in (None, [], True, {}, {"objects": None}, {"objects": {}},
                          {"objects": [], "world_position": [1, 2, 3]},
                          {"objects": [OBJ]}, {"objects": [True]},
                          {"objects": [tuple(compact(OBJ))]},
                          {"objects": [["rectangle", "red", [100, 200, 400, 700]]]},
                          {"objects": [compact(OBJ)[:-1]]},
                          {"objects": [compact(OBJ) + ["hidden_id"]]}):
            with self.subTest(malformed=malformed):
                with self.assertRaises(PerceptionError):
                    normalize_inventory_wire(malformed)

    def test_same_strict_labels_and_coordinate_validation_no_coercion(self):
        for index, invalids in ((0, (None, True, 1, "", " \t", "x" * 41)),
                                (1, (None, True, 1, "", " \n", "x" * 25))):
            for value in invalids:
                row = compact(OBJ)
                row[index] = value
                with self.subTest(index=index, value=value):
                    with self.assertRaisesRegex(PerceptionError, "invalid_visual_label"):
                        normalize_inventory_wire({"objects": [row]})
        for box in ([-1, 0, 400, 700], [0, 0, 1001, 700], [0, 0, True, 700],
                    [0, 0, 400.0, 700], [0, 0, "400", 700], [0, 0, None, 700],
                    [0, 0, float("nan"), 700], [0, 0, float("inf"), 700],
                    [400, 0, 400, 700], [500, 0, 400, 700], [0, 700, 400, 700],
                    [0, 800, 400, 700]):
            with self.subTest(box=box):
                with self.assertRaisesRegex(PerceptionError, "invalid_native_visual_box"):
                    normalize_inventory_wire({"objects": [["rectangle", "red", *box]]})

    def test_normalized_values_retain_semantic_and_side_guards(self):
        cases = (([{"label": "sofa, purple", "color": "green", "bbox": [0, 200, 400, 700]}],
                  "ソファに近づいて", "visual_label_color_mismatch"),
                 ([dict(OBJ, label="sofa", bbox=[0, 200, 999, 700])],
                  "左のソファに近づいて", "visual_side_box_too_wide"),
                 ([OBJ, dict(OBJ, bbox=[600, 200, 900, 700])],
                  "赤い四角に近づいて", "ambiguous_visual_target"))
        for original, request, error in cases:
            with self.subTest(error=error):
                objects = normalize_inventory_wire({"objects": [compact(obj) for obj in original]})
                with self.assertRaisesRegex(PerceptionError, error):
                    validate_selection(CHOICE, objects, request)

    def test_wire_schema_is_fixed_tuple_without_items_shadowing(self):
        self.assertEqual(INVENTORY_WIRE_FORMAT, "compact_tuple_v1")
        objects = INVENTORY_SCHEMA["properties"]["objects"]
        self.assertEqual(objects["maxItems"], 8)
        row = objects["items"]
        self.assertNotIn("items", row)
        self.assertEqual((row["minItems"], row["maxItems"]), (6, 6))
        self.assertEqual([entry["type"] for entry in row["prefixItems"]],
                         ["string", "string", "integer", "integer", "integer", "integer"])
        self.assertEqual([row["prefixItems"][i]["maxLength"] for i in (0, 1)], [40, 24])
        self.assertEqual(INVENTORY_SCHEMA["required"], ["objects"])
        self.assertFalse(INVENTORY_SCHEMA["additionalProperties"])
        self.assertIn('["label","color",left,top,right,bottom]', INVENTORY_PROMPT)

    def test_local_llama_schema_converter_emits_exact_six_tuple_and_eight_cap(self):
        path = (Path(__file__).resolve().parents[1] / "tools/llama.cpp/src_convert_plamo3"
                / "examples/json_schema_to_grammar.py")
        if not path.is_file():
            self.skipTest("Optional local llama.cpp converter source not present")
        spec = importlib.util.spec_from_file_location("compact_inventory_schema_converter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        converter = module.SchemaConverter(prop_order={}, allow_fetch=False, dotall=False, raw_pattern=False)
        converter.visit(copy.deepcopy(INVENTORY_SCHEMA), "")
        rules = dict(line.split(" ::= ", 1) for line in converter.format_grammar().splitlines())
        row = rules["objects-item"]
        self.assertEqual(row.count('\",\" space'), 5)
        self.assertEqual([row.count(f"objects-item-tuple-{index}") for index in (0, 1)], [1, 1])
        self.assertEqual(row.count(" integer"), 4)
        self.assertTrue(row.endswith('space \"]\"'))
        self.assertIn("{0,7}", rules["objects"])
        self.assertIn("{0,40}", rules["objects-item-tuple-0"])
        self.assertIn("{0,24}", rules["objects-item-tuple-1"])


class ValidationTests(unittest.TestCase):
    def test_native_box_normalization(self):
        objects=validate_inventory({"objects":[OBJ]})
        result=validate_selection(CHOICE,objects)
        self.assertEqual(result["bbox"],[.1,.2,.4,.7])

    def test_invalid_boxes_not_clamped(self):
        for box in ([0,0,1001,700],[0,0,True,700],[0,0,200.0,700],[300,0,200,700],[0,0,float("nan"),700]):
            with self.subTest(box=box):
                with self.assertRaises(PerceptionError):
                    validate_inventory({"objects":[dict(OBJ,bbox=box)]})

    def test_inventory_rejects_hidden_coordinates(self):
        with self.assertRaises(PerceptionError):
            validate_inventory({"objects":[dict(OBJ,world_position=[1,2,3])]})

    def test_absence_has_no_box(self):
        result=validate_selection(dict(CHOICE,target_id=-1,confidence=0),[])
        self.assertFalse(result["visible"])
        self.assertEqual(result["bbox"],[0,0,0,0])

    def test_invalid_selection(self):
        objects=validate_inventory({"objects":[OBJ]})
        for values in ({"target_id":True},{"target_id":1},{"target_id":-2},{"confidence":float("nan")},
                       {"confidence":True},{"confidence":1.01},{"target_id":-1,"confidence":.5},{"reason":"x"*81}):
            with self.subTest(values=values):
                with self.assertRaises(PerceptionError):validate_selection(dict(CHOICE,**values),objects)

    def test_generic_furniture_not_a_sofa(self):
        objects=validate_inventory({"objects":[dict(OBJ,label="furniture")]})
        with self.assertRaisesRegex(PerceptionError,"unresolved_visual_category"):
            validate_selection(CHOICE,objects,"ソファに近づいて")

    def test_sofa_like_not_exact_sofa(self):
        objects=validate_inventory({"objects":[dict(OBJ,label="sofa-like furniture")]})
        with self.assertRaises(PerceptionError):validate_selection(CHOICE,objects,"ソファに近づいて")

    def test_wrong_category_rejected(self):
        objects=validate_inventory({"objects":[dict(OBJ,label="table")]})
        with self.assertRaisesRegex(PerceptionError,"visual_category_mismatch"):
            validate_selection(CHOICE,objects,"ソファに近づいて")

    def test_synonym_matches(self):
        objects=validate_inventory({"objects":[dict(OBJ,label="couch")]})
        self.assertTrue(validate_selection(CHOICE,objects,"ソファに近づいて")["visible"])

    def test_color_must_match(self):
        objects=validate_inventory({"objects":[dict(OBJ,label="chair",color="blue")]})
        with self.assertRaisesRegex(PerceptionError,"visual_color_mismatch"):
            validate_selection(CHOICE,objects,"赤い椅子に近づいて")

    def test_ambiguous_duplicates_rejected(self):
        objects=validate_inventory({"objects":[OBJ,dict(OBJ,bbox=[600,200,900,700])]})
        with self.assertRaisesRegex(PerceptionError,"ambiguous_visual_target"):
            validate_selection(CHOICE,objects,"赤い四角に近づいて")
        self.assertTrue(validate_selection(CHOICE,objects,"左の赤い四角に近づいて")["visible"])
        with self.assertRaises(PerceptionError):validate_selection(CHOICE,objects,"右の赤い四角に近づいて")

    def test_unknown_freeform_is_ambiguous(self):
        objects=validate_inventory({"objects":[OBJ]})
        with self.assertRaisesRegex(PerceptionError,"ambiguous_visual_request"):
            validate_selection(CHOICE,objects,"あれに近づいて")

    def test_unique_center_category(self):
        objects=validate_inventory({"objects":[dict(OBJ,label="sofa",bbox=box) for box in
            ([0,400,200,700],[400,400,600,700],[800,400,1000,700])]})
        for request in ("中央のソファ", "真ん中のソファ", "正面のソファ", "center sofa", "middle couch"):
            with self.subTest(request=request):
                self.assertTrue(validate_selection(dict(CHOICE,target_id=1),objects,request)["visible"])
                with self.assertRaisesRegex(PerceptionError,"ambiguous_or_wrong_visual_center"):
                    validate_selection(CHOICE,objects,request)

    def test_center_tie_and_near_tie_rejected(self):
        for boxes in (([300,200,500,700],[500,200,700,700]),
                      ([400,200,600,700],[420,200,620,700])):
            objects=validate_inventory({"objects":[dict(OBJ,bbox=box) for box in boxes]})
            with self.assertRaisesRegex(PerceptionError,"ambiguous_or_wrong_visual_center"):
                validate_selection(CHOICE,objects,"中央の赤い四角")

    def test_center_requires_central_band_and_no_side_conflict(self):
        objects=validate_inventory({"objects":[dict(OBJ,bbox=[0,200,100,700])]})
        with self.assertRaisesRegex(PerceptionError,"ambiguous_or_wrong_visual_center"):
            validate_selection(CHOICE,objects,"中央の赤い四角")
        with self.assertRaisesRegex(PerceptionError,"ambiguous_visual_side"):
            validate_selection(CHOICE,objects,"中央の左の赤い四角")

    def test_single_candidate_must_be_on_requested_absolute_side(self):
        for request, box, allowed in (("左のソファ",[0,200,400,700],True),
                                      ("右のソファ",[600,200,1000,700],True),
                                      ("左のソファ",[400,200,600,700],False),
                                      ("右のソファ",[400,200,600,700],False),
                                      ("左のソファ",[600,200,1000,700],False),
                                      ("右のソファ",[0,200,400,700],False)):
            objects=validate_inventory({"objects":[dict(OBJ,label="sofa",bbox=box)]})
            with self.subTest(request=request,box=box):
                if allowed:self.assertTrue(validate_selection(CHOICE,objects,request)["visible"])
                else:
                    with self.assertRaisesRegex(PerceptionError,"ambiguous_or_wrong_visual_side"):
                        validate_selection(CHOICE,objects,request)

    def test_side_rejects_scene_wide_box_even_when_extreme(self):
        for boxes in (([0,556,999,725],),([0,200,800,700],),
                      ([0,200,800,700],[850,200,950,700])):
            objects=validate_inventory({"objects":[dict(OBJ,label="sofa",bbox=box) for box in boxes]})
            with self.assertRaisesRegex(PerceptionError,"visual_side_box_too_wide"):
                validate_selection(CHOICE,objects,"左のソファ")


class DeterministicSelectionTests(unittest.TestCase):
    def select(self, request, original):
        return select_inventory_deterministically(validate_inventory({"objects": original}), request)

    def test_closed_approach_and_observe_exact_intents(self):
        for request, label, color, intent in (
            ("ソファのところへ行って", "sofa", "purple", "approach"),
            ("植物まで行って", "plant", "green", "approach"),
            ("赤い椅子が見える？", "chair", "red", "observe"),
            ("緑の植物は見える", "plant", "green", "observe"),
            ("机に近づいて。", "table", "brown", "approach"),
            ("  ｿﾌｧまで行って！  ", "couch", "purple", "approach"),
        ):
            with self.subTest(request=request):
                result, audit = self.select(request, [dict(OBJ, label=label, color=color)])
                self.assertEqual((result["intent"], result["target_id"], result["confidence"]), (intent, 0, 1.0))
                self.assertTrue(audit["unique_validated_match"])
                self.assertFalse(audit["absence_proven"])

    def test_unsupported_negated_quoted_compound_conditional_are_fallback(self):
        for request in (
            "ソファに近づかないで", "ソファ以外を見て", "「左のソファに近づいて」と言って",
            '"ソファまで行って"', "椅子かソファを見て", "机を見てから植物に近づいて",
            "ソファの左の机", "もし植物があれば見て", "ソファまで行ってもいい？",
            "左と右のソファに近づいて", "赤と青の椅子が見える？", "右の左のソファまで行って",
            "赤い青い椅子が見える？", "大きいソファまで行って", "ソファ", "ソファと椅子まで行って",
            "ソファに近づいて、植物も見て", "お願いします、ソファに近づいて", "ソファまで行って?",
            "ソファまで行って\n植物まで行って", "ソファ まで行って", "ソファ\u200bまで行って",
            "赤い丸い椅子まで行って", "ソファへ行ってもいい？", "ソファに行ってはいけない",
            "もしソファのところに行っていたら", "ソファのそばまで行ってほしくない",
            "ソファを見て", "go to the sofa", "" , None,
        ):
            with self.subTest(request=request):
                self.assertIsNone(self.select(request, [dict(OBJ, label="sofa")]))

    def test_same_clear_approach_suffixes_are_general_not_case_specific(self):
        for suffix in ("へ行って", "に行って", "のところに行って", "のところまで行って", "のそばまで行って"):
            for noun, label in (("ソファ", "sofa"), ("植物", "plant"), ("椅子", "chair"), ("机", "table")):
                with self.subTest(noun=noun,suffix=suffix):
                    result, _ = self.select(noun+suffix, [dict(OBJ,label=label)])
                    self.assertEqual((result["intent"],result["target_id"]),("approach",0))
                    self.assertIsNone(self.select(noun+suffix+"?", [dict(OBJ,label=label)]))

    def test_short_request_limit_before_and_after_normalization(self):
        request = "ソファまで行って"
        self.assertIsNotNone(self.select(request + " " * (64-len(request)), [dict(OBJ, label="sofa")]))
        self.assertIsNone(self.select(request + " " * (65-len(request)), [dict(OBJ, label="sofa")]))
        self.assertIsNone(self.select("㍍" * 33, [dict(OBJ, label="sofa")]))

    def test_every_candidate_kept_and_evaluated_with_unrelated_objects(self):
        original = [dict(OBJ, label=label) for label in ("person", "rug", "sofa", "cushion")]
        clean = validate_inventory({"objects": original})
        before = copy.deepcopy(clean)
        result, audit = select_inventory_deterministically(clean, "ソファまで行って")
        self.assertEqual(result["target_id"], 2)
        self.assertEqual([item["target_id"] for item in audit["candidate_evaluations"]], [0,1,2,3])
        self.assertEqual(clean, before)

    def test_absent_ambiguous_and_unresolved_do_not_claim_proven_absence(self):
        for original in ([], [dict(OBJ, label="person")], [dict(OBJ, label="furniture")],
                         [dict(OBJ, label="sofa-like furniture")],
                         [dict(OBJ, label="sofa"), dict(OBJ, label="sofa", bbox=[600,200,900,700])]):
            with self.subTest(original=original):
                result, audit = self.select("ソファまで行って", original)
                self.assertEqual((result["target_id"], result["confidence"]), (-1,0))
                self.assertFalse(audit["unique_validated_match"])
                self.assertFalse(audit["absence_proven"])
                self.assertEqual(result["reason"], "no uniquely validated visible target")

    def test_existing_uncertain_competitor_and_color_contradiction_veto(self):
        for label, color, expected in (
            ("red blue sofa", "purple", "ambiguous_visual_category_evidence"),
            ("sofa, purple, chair", "purple", "ambiguous_visual_category_evidence"),
            ("sofa, not purple", "purple", "ambiguous_visual_category_evidence"),
            ("sofa, unknown", "purple", "ambiguous_visual_category_evidence"),
            ("sofa, purple", "green", "visual_label_color_mismatch"),
        ):
            original = [dict(OBJ, label="sofa", color="purple"),
                        dict(OBJ, label=label, color=color, bbox=[600,200,900,700])]
            with self.subTest(label=label):
                result, audit = self.select("左の紫のソファまで行って", original)
                self.assertEqual(result["target_id"], -1)
                self.assertIn(expected, [item.get("reason") for item in audit["candidate_evaluations"]])

    def test_absolute_side_width_relative_extremes_and_center_rules_unchanged(self):
        cases = (
            ("左のソファまで行って", [[0,200,400,700]], 0),
            ("右のソファまで行って", [[600,200,1000,700]], 0),
            ("左のソファまで行って", [[600,200,1000,700]], -1),
            ("右のソファまで行って", [[400,200,600,700]], -1),
            ("左のソファまで行って", [[0,200,999,700]], -1),
            ("左のソファまで行って", [[600,200,900,700],[100,200,400,700]], 1),
            ("右のソファまで行って", [[100,200,400,700],[600,200,900,700]], 1),
            ("中央のソファまで行って", [[0,200,200,700],[400,200,600,700],[800,200,1000,700]], 1),
            ("真ん中のソファまで行って", [[300,200,500,700],[500,200,700,700]], -1),
            ("正面のソファまで行って", [[400,200,600,700],[420,200,620,700]], -1),
        )
        for request, boxes, expected in cases:
            with self.subTest(request=request, boxes=boxes):
                result, _ = self.select(request, [dict(OBJ,label="sofa",bbox=box) for box in boxes])
                self.assertEqual(result["target_id"], expected)

    def test_color_and_category_are_not_approximated(self):
        for request in ("赤い椅子が見える？", "植物まで行って"):
            result, audit = self.select(request, [dict(OBJ,label="chair",color="blue")])
            self.assertEqual(result["target_id"], -1)
            self.assertFalse(audit["absence_proven"])

    def test_multiple_successes_are_still_rejected(self):
        objects = validate_inventory({"objects": [OBJ, OBJ]})
        with patch("lumina_next.visual_perception.validate_selection", return_value={}):
            result, audit = select_inventory_deterministically(objects, "四角まで行って")
        self.assertEqual(result["target_id"], -1)
        self.assertFalse(audit["unique_validated_match"])


class InferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_text_cannot_bypass_missing_or_invalid_image_evidence(self):
        async def no_http(*args,**kwargs):
            raise AssertionError("invalid image must not dispatch")
        with self.assertRaisesRegex(PerceptionError,"invalid_eye_image"):
            await infer(no_http,b"not PNG","ソファまで行って")
        calls=[]
        async def malformed_http(method,path,body=None,*,model=False):
            calls.append(path)
            if path=="/props":return {"modalities":{"vision":True}}
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"objects":[["sofa","purple",0,0,True,1000]]})}}]}
        with self.assertRaisesRegex(PerceptionError,"invalid_native_visual_box"):
            await infer(malformed_http,b"\x89PNG\r\n\x1a\nfixture","ソファまで行って")
        self.assertEqual(calls,["/props","/v1/chat/completions"])

    async def test_model_only_drops_degenerate_unrelated_box_and_audits_it(self):
        calls=[]
        async def http(method,path,body=None,*,model=False):
            calls.append(path)
            if path=="/props":
                return {"modalities":{"vision":True}}
            content={"objects":[
                ["sofa","purple",0,560,1000,760],
                ["wall","gray",500,300,500,700],
            ]}
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(content)}}]}

        result=await infer(http,b"\x89PNG\r\n\x1a\nfixture","ソファまで行って",open_vocabulary=True)

        self.assertTrue(result["visible"])
        self.assertEqual(result["category"],"sofa")
        self.assertEqual(calls,["/props","/v1/chat/completions"])
        audit=result["perception_audit"]
        self.assertEqual(audit["dropped_degenerate_native_box_rows"],[1])
        self.assertEqual(
            audit["degenerate_native_box_policy"],
            "drop_row_target_unselectable_same_category_remains_ambiguity_evidence",
        )

    async def test_degenerate_target_row_is_dropped_and_cannot_be_selected(self):
        calls=[]
        async def http(method,path,body=None,*,model=False):
            calls.append(path)
            if path=="/props":
                return {"modalities":{"vision":True}}
            content={"objects":[["sofa","purple",0,600,1000,600]]}
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(content)}}]}

        result=await infer(http,b"\x89PNG\r\n\x1a\nfixture","ソファまで行って",open_vocabulary=True)

        self.assertFalse(result["visible"])
        self.assertEqual(result["target_id"],-1)
        self.assertEqual(calls,["/props","/v1/chat/completions"])
        self.assertEqual(result["perception_audit"]["dropped_degenerate_native_box_rows"],[0])

    async def test_degenerate_same_category_competitor_prevents_false_uniqueness(self):
        async def http(method,path,body=None,*,model=False):
            if path=="/props":
                return {"modalities":{"vision":True}}
            content={"objects":[
                ["sofa","purple",0,560,1000,760],
                ["couch","purple",500,300,500,700],
            ]}
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(content)}}]}

        with self.assertRaisesRegex(
            PerceptionError,
            "ambiguous_degenerate_visual_category_evidence",
        ) as caught:
            await infer(
                http,
                b"\x89PNG\r\n\x1a\nfixture",
                "ソファまで行って",
                open_vocabulary=True,
            )
        self.assertEqual(caught.exception.audit["ambiguous_dropped_category_rows"],[1])

    async def test_fast_path_always_requires_blind_image_pass_but_no_selector_post(self):
        for request, original, expected in (
            ("ソファのところへ行って", [dict(OBJ,label="sofa")], 0),
            ("赤い椅子が見える？", [dict(OBJ,label="chair",color="blue")], -1),
            ("ソファまで行って", [dict(OBJ,label="sofa"),dict(OBJ,label="sofa",bbox=[600,200,900,700])], -1),
        ):
            calls=[]
            async def http(method,path,body=None,*,model=False):
                calls.append((method,path,body))
                if path=="/props":return {"modalities":{"vision":True}}
                self.assertEqual(len(calls),2,"fast path must not dispatch selector")
                return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"objects":[compact(o) for o in original]})}}]}
            with self.subTest(request=request):
                result=await infer(http,b"\x89PNG\r\n\x1a\nfixture",request)
                self.assertEqual(result["target_id"],expected)
                self.assertEqual(len(calls),2)
                self.assertNotIn(request,json.dumps(calls[1][2],ensure_ascii=False))
                self.assertEqual(calls[1][2]["messages"][0]["content"][0]["text"],INVENTORY_PROMPT)
                self.assertEqual(calls[1][2]["max_tokens"],550)
                audit=result["perception_audit"]
                self.assertEqual(audit["inventory"],validate_inventory({"objects":original}))
                self.assertEqual(audit["selection_policy_revision"],SELECTION_POLICY_REVISION)
                self.assertEqual(audit["selection_basis"],"closed_grammar_validated_inventory")
                self.assertEqual(audit["confidence_basis"],"deterministic_rule_match_not_image_confidence")
                self.assertFalse(audit["absence_proven"])

    async def test_unsupported_text_preserved_in_old_selector_only(self):
        request="「ソファに近づいて」と言って"
        calls=[]
        async def http(method,path,body=None,*,model=False):
            calls.append(body)
            if path=="/props":return {"modalities":{"vision":True}}
            answer={"objects":[compact(dict(OBJ,label="sofa"))]} if len(calls)==2 else dict(CHOICE,intent="none",target_id=-1,confidence=0)
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(answer)}}]}
        result=await infer(http,b"\x89PNG\r\n\x1a\nfixture",request)
        self.assertEqual(len(calls),3)
        self.assertEqual(json.loads(calls[2]["messages"][1]["content"])["visitor"],request)
        self.assertNotIn(request,json.dumps(calls[1],ensure_ascii=False))
        self.assertEqual(calls[2]["max_tokens"],160)
        self.assertEqual(result["perception_audit"]["selection_basis"],"qwen_text_selection")
        self.assertEqual(result["perception_audit"]["confidence_basis"],"qwen_self_report_not_calibrated_probability")

    async def test_target_excluded_from_vision_pass(self):
        calls=[]
        async def http(method,path,body=None,*,model=False):
            calls.append((method,path,body,model))
            if path=="/props":return {"modalities":{"vision":True}}
            answer={"objects":[compact(OBJ)]} if len(calls)==2 else CHOICE
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(answer)}}]}
        result=await infer(http,b"\x89PNG\r\n\x1a\nfixture","SECRET_VISITOR_TARGET rectangle")
        visual_wire=json.dumps(calls[1][2])
        selector_wire=json.dumps(calls[2][2])
        self.assertNotIn("SECRET_VISITOR_TARGET",visual_wire)
        self.assertIn("SECRET_VISITOR_TARGET",selector_wire)
        self.assertNotIn("image_url",selector_wire)
        self.assertTrue(all(row[3] for row in calls))
        self.assertTrue(result["visible"])
        self.assertEqual(result["perception_audit"]["inventory_wire_format"], INVENTORY_WIRE_FORMAT)
        self.assertEqual(json.loads(calls[2][2]["messages"][1]["content"])["inventory"],
                         validate_inventory({"objects": [OBJ]}))
        self.assertEqual([call[2]["max_tokens"] for call in calls[1:]], [550, 160])
        self.assertTrue(all(call[2]["model"] == MODEL for call in calls[1:]))
        self.assertTrue(all(call[2]["cache_prompt"] is False for call in calls[1:]))
        self.assertEqual(calls[1][2]["response_format"]["schema"], INVENTORY_SCHEMA)

    async def test_dictionary_wire_refused_without_selector_or_fallback(self):
        calls=[]
        async def http(method,path,body=None,*,model=False):
            calls.append(path)
            if path=="/props":return {"modalities":{"vision":True}}
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"objects":[OBJ]})}}]}
        with self.assertRaisesRegex(PerceptionError,"invalid_visual_object_wire"):
            await infer(http,b"\x89PNG\r\n\x1a\nfixture","red square")
        self.assertEqual(calls,["/props","/v1/chat/completions"])

    async def test_text_only_server_refused_before_image_post(self):
        calls=[]
        async def http(method,path,body=None,*,model=False):
            calls.append(method)
            return {"modalities":{"vision":False}}
        with self.assertRaises(PerceptionError):await infer(http,b"\x89PNG\r\n\x1a\nfixture","red square")
        self.assertEqual(calls,["GET"])

    async def test_truncated_generation_refused(self):
        async def http(method,path,body=None,*,model=False):
            if path=="/props":return {"modalities":{"vision":True}}
            return {"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}
        with self.assertRaises(PerceptionError):await infer(http,b"\x89PNG\r\n\x1a\nfixture","red square")

    async def test_invalid_input_no_network(self):
        async def http(*args,**kwargs):raise AssertionError("must not call network")
        with self.assertRaises(PerceptionError):await infer(http,b"not png","red square")

    async def test_malformed_choices_refused(self):
        for malformed in (None,[],{"choices":[4]},{"choices":[None]},{"choices":[{"finish_reason":"stop","message":None}]}):
            with self.subTest(malformed=malformed):
                async def http(method,path,body=None,*,model=False):
                    if path=="/props":return {"modalities":{"vision":True}}
                    return malformed
                with self.assertRaises(PerceptionError):await infer(http,b"\x89PNG\r\n\x1a\nfixture","red square")

    async def test_semantic_and_selection_errors_retain_audit_in_memory(self):
        for choice, error in ((CHOICE,"ambiguous_visual_target"),
                              (dict(CHOICE,target_id=9),"invalid_visual_target_id")):
            calls=[]
            async def http(method,path,body=None,*,model=False):
                calls.append(body)
                if path=="/props":return {"modalities":{"vision":True}}
                answer={"objects":[compact(OBJ),compact(dict(OBJ,bbox=[600,200,900,700]))]} if len(calls)==2 else choice
                return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(answer)}}]}
            with self.assertRaisesRegex(PerceptionError,error) as raised:
                await infer(http,b"\x89PNG\r\n\x1a\nfixture","お願いします、赤い四角に近づいて")
            audit=raised.exception.audit
            self.assertEqual(len(audit["inventory"]),2)
            self.assertEqual(audit["selection"],choice)
            self.assertEqual(len(audit["image_sha256"]),64)
            self.assertIn("visual_seconds",audit)
            self.assertIn("selection_seconds",audit)
            self.assertNotIn("image",audit)
            self.assertFalse(audit["target_sent_to_visual_pass"])
            self.assertFalse(audit["world_metadata_sent"])
            self.assertEqual(audit["inventory_wire_format"], INVENTORY_WIRE_FORMAT)

    async def test_live_full_width_sofa_side_error_retains_audit(self):
        calls=[]
        sofa=dict(OBJ,label="sofa",bbox=[0,556,999,725])
        async def http(method,path,body=None,*,model=False):
            calls.append(body)
            if path=="/props":return {"modalities":{"vision":True}}
            answer={"objects":[compact(sofa)]} if len(calls)==2 else CHOICE
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(answer)}}]}
        with self.assertRaisesRegex(PerceptionError,"visual_side_box_too_wide") as raised:
            await infer(http,b"\x89PNG\r\n\x1a\nfixture","お願いします、左のソファに近づいて")
        self.assertEqual(raised.exception.audit["inventory"][0]["bbox"],[0,556,999,725])
        self.assertEqual(raised.exception.audit["selection"],CHOICE)
        self.assertIn("selection_seconds",raised.exception.audit)


if __name__=="__main__":unittest.main()
