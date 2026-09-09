extends Node
class_name EmbodiedActionAdapter

signal action_started(action_id: String, action_type: String)
signal action_completed(result: Dictionary)
signal action_rejected(result: Dictionary)
signal speech_requested(action_id: String, text: String)
signal look_at_requested(action_id: String, target: String, duration_ms: int)

const SEMANTIC_ANIMATIONS := {
	"idle": ["Lumina_Idle_Neutral", "Aogiri_Idle"],
	"idle_handoff": ["Lumina_Idle_Soft", "Aogiri_Idle_Handoff"],
	"idle_observe": ["Lumina_Idle_Observe", "Aogiri_IdleWeightShift"],
	"idle_confident": ["Lumina_Idle_Confident", "Aogiri_IdleBreathing"],
	"walk": ["Lumina_Walk", "Aogiri_Walk"],
	"run": ["Lumina_Run"],
	"turn_left": ["Lumina_Turn_Left", "Aogiri_TurnRecoverRight"],
	"turn_right": ["Lumina_Turn_Right", "Aogiri_TurnRight"],
	"turn_recover": ["Lumina_Stand_Recover", "Aogiri_TurnRecoverRight"],
	"sit_down": ["Lumina_Sit_Down", "Aogiri_SitDown"],
	"sit": ["Lumina_Sit_Idle", "Aogiri_SitIdle"],
	"sit_idle": ["Lumina_Sit_Idle", "Aogiri_SitIdle"],
	"sit_settle": ["Lumina_Sit_Settle"],
	"sit_prepare_stand": ["Lumina_Sit_Prepare_Stand"],
	"stand": ["Lumina_Stand_Up", "Aogiri_StandUp"],
	"stand_up": ["Lumina_Stand_Up", "Aogiri_StandUp"],
	"stand_recover": ["Lumina_Stand_Recover", "Aogiri_Idle_Handoff"],
	"talk": ["Lumina_Talk_Standing", "Aogiri_TalkStanding"],
	"speaking": ["Lumina_Talk_Standing", "Aogiri_TalkStanding"],
	"talk_sitting": ["Lumina_Talk_Sitting"],
	"catching_breath": ["Lumina_Idle_Confident", "Aogiri_CatchingBreath"],
	"idle_breathing": ["Lumina_Idle_Soft", "Aogiri_IdleBreathing"],
	"idle_weight_shift": ["Lumina_Idle_Observe", "Aogiri_IdleWeightShift"],
	"wave": ["Lumina_Wave", "Aogiri_Wave"],
	"small_wave": ["Lumina_Wave_Small", "Aogiri_Wave"],
	"nod": ["Lumina_Nod", "Aogiri_Nod"],
	"shake_head": ["Lumina_Shake_Head", "Aogiri_ShakeHead"],
	"think": ["Lumina_Think", "Aogiri_Think"],
	"look_around": ["Lumina_Look_Around", "Aogiri_LookAround"],
	"stretch": ["Lumina_Stretch", "Aogiri_Stretch"],
	"laugh": ["Lumina_Laugh", "Aogiri_Laugh"],
	"shy_body": ["Lumina_Shy", "Aogiri_ShyBody"],
	"surprised_body": ["Lumina_Surprised", "Aogiri_SurprisedBody"],
	"sad_body": ["Lumina_Sad", "Aogiri_SadBody"],
	"happy_body": ["Lumina_Happy", "Aogiri_HappyBody"],
	"reach": ["Lumina_Reach", "Aogiri_ReachRight"],
	"point": ["Lumina_Point", "Aogiri_PointRight"],
	"offer_hand": ["Lumina_Offer_Hand", "Aogiri_OfferHand"],
	"hold_hand": ["Lumina_Hold_Hand", "Aogiri_HoldHand"],
	"release_hand": ["Lumina_Stand_Recover", "Aogiri_ReleaseHand"],
	"high_five": ["Lumina_High_Five", "Aogiri_HighFive"],
	"pick_up": ["Lumina_Pick_Up", "Aogiri_PickUp"],
	"put_down": ["Lumina_Put_Down", "Aogiri_PutDown"],
	"inspect": ["Lumina_Look_Around", "Aogiri_Inspect"],
	"head_pat_reaction": ["Lumina_React_Head_Pat", "Aogiri_HeadPatReaction"],
	"shoulder_touch_reaction": ["Lumina_React_Shoulder_Touch", "Aogiri_ShoulderTouchReaction"],
	"step_back": ["Lumina_Step_Back"],
	"approach": ["Lumina_Approach", "Aogiri_Walk"],
}
const GESTURE_FALLBACKS := {
	"small_wave": "small_wave",
	"wave": "wave",
	"nod": "nod",
	"shake_head": "shake_head",
	"hand_emphasis": "point",
	"point": "point",
	"settle": "idle_weight_shift",
	"think": "think",
	"look_around": "look_around",
	"stretch": "stretch",
	"laugh": "laugh",
	"happy": "happy_body",
	"sad": "sad_body",
	"shy": "shy_body",
	"surprised": "surprised_body",
	"high_five": "high_five",
	"head_pat_reaction": "head_pat_reaction",
}
const BODY_AFFECT_BY_EXPRESSION := {
	"happy": "happy_body",
	"happy_soft": "happy_body",
	"relaxed": "idle_breathing",
	"sad": "sad_body",
	"surprised": "surprised_body",
	"shy": "shy_body",
	"confused": "think",
}
const EXPRESSION_BY_SEMANTIC := {
	"wave": "happy_soft",
	"small_wave": "happy_soft",
	"think": "confused",
	"stretch": "relaxed",
	"laugh": "happy",
	"shy_body": "shy",
	"surprised_body": "surprised",
	"sad_body": "sad",
	"happy_body": "happy",
	"head_pat_reaction": "head_pat",
	"shoulder_touch_reaction": "surprised",
	"high_five": "happy_soft",
}
const LOOPING_ANIMATIONS := [
	"Lumina_Idle_Neutral",
	"Lumina_Idle_Soft",
	"Lumina_Idle_Observe",
	"Lumina_Idle_Confident",
	"Lumina_Walk",
	"Lumina_Run",
	"Lumina_Sit_Idle",
	"Lumina_Hold_Hand",
	"Lumina_Step_Back",
	"Lumina_Approach",
	"Lumina_Talk_Standing",
	"Lumina_Talk_Sitting",
	"Aogiri_Idle",
	"Aogiri_Idle_Handoff",
	"Aogiri_IdleBreathing",
	"Aogiri_IdleWeightShift",
	"Aogiri_Walk",
	"Aogiri_SitIdle",
	"Aogiri_TalkStanding",
	"Aogiri_HoldHand",
]

@export var enabled := false

var _avatar_root: Node
var _face_controller: Node
var _authority: Node
var _navigation_controller: Node
var _gaze_controller: Node
var _affordance_controller: Node
var _animation_player: AnimationPlayer
var _sequence := 0
var _current_animation := ""
var _current_posture := "standing"
var _last_result: Dictionary = {}


func bind_avatar(avatar_root: Node, face_controller: Node, authority: Node) -> bool:
	_avatar_root = avatar_root
	_face_controller = face_controller
	_authority = authority
	_animation_player = _find_animation_player(avatar_root)
	_configure_animation_loops()
	return _avatar_root != null and _face_controller != null and _authority != null and _animation_player != null


func bind_navigation(navigation_controller: Node) -> bool:
	_navigation_controller = navigation_controller
	return _navigation_controller != null and _navigation_controller.has_method("start_move_to_anchor")


func bind_gaze(gaze_controller: Node) -> bool:
	_gaze_controller = gaze_controller
	return _gaze_controller != null and _gaze_controller.has_method("look_at")


func bind_affordances(affordance_controller: Node) -> bool:
	_affordance_controller = affordance_controller
	return _affordance_controller != null and _affordance_controller.has_method("perform")


func dispatch(command: Dictionary) -> Dictionary:
	var normalized := _normalize_command(command)
	var action_id := String(normalized.get("action_id", ""))
	var action_type := String(normalized.get("type", ""))
	if not enabled:
		return _reject(action_id, action_type, "disabled", "embodied_v1_adapter_disabled")
	if _authority == null or not _authority.has_method("validate"):
		return _reject(action_id, action_type, "rejected", "authority_unavailable")
	var decision: Dictionary = _authority.call("validate", normalized)
	if not bool(decision.get("allowed", false)):
		return _reject(
			action_id,
			action_type,
			String(decision.get("status", "rejected")),
			String(decision.get("reason", "authority_rejected"))
		)
	action_started.emit(action_id, action_type)
	var params: Dictionary = normalized.get("params", {})
	var result := _execute(action_id, action_type, params)
	_last_result = result.duplicate(true)
	if bool(result.get("accepted", false)):
		action_completed.emit(result)
	else:
		action_rejected.emit(result)
	return result


func dispatch_touch_reflex(event: Dictionary) -> Dictionary:
	var command := {
		"action_id": "reflex_%d" % Time.get_ticks_msec(),
		"type": "touch_reflex",
		"source": "local_reflex",
		"params": event.duplicate(true),
	}
	return dispatch(command)


func get_current_animation() -> String:
	return _current_animation


func get_current_posture() -> String:
	return _current_posture


func get_last_result() -> Dictionary:
	return _last_result.duplicate(true)


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"bound": _avatar_root != null and _animation_player != null,
		"current_animation": _current_animation,
		"current_posture": _current_posture,
		"animation_count": _animation_player.get_animation_list().size() if _animation_player != null else 0,
		"navigation_bound": _navigation_controller != null,
		"gaze_bound": _gaze_controller != null,
		"affordances_bound": _affordance_controller != null,
		"single_model_multi_action": true,
		"gesture_motion_fallbacks": false,
		"resolved_semantic_count": _resolved_semantic_count(),
		"lumina_v033_motion_bank": (
			_animation_player != null
			and _animation_player.has_animation("Lumina_Idle_Neutral")
			and _animation_player.has_animation("Lumina_High_Five")
		),
		"lumina_v036_motion_bank": (
			_animation_player != null
			and _animation_player.has_animation("Lumina_Turn_Left")
			and _animation_player.has_animation("Lumina_Turn_Right")
			and _animation_player.has_animation("Lumina_Surprised")
		),
		"lumina_v038_motion_face_bank": (
			_animation_player != null
			and _animation_player.has_animation("Lumina_Pick_Up")
			and _animation_player.has_animation("Lumina_Sit_Prepare_Stand")
			and _face_controller != null
			and _face_controller.has_method("has_required_shapes")
			and bool(_face_controller.call("has_required_shapes"))
		),
		"linked_expression_route_count": EXPRESSION_BY_SEMANTIC.size(),
	}


func _execute(action_id: String, action_type: String, params: Dictionary) -> Dictionary:
	match action_type:
		"living_state":
			var state := String(params.get("state", "idle"))
			return _play_semantic_result(action_id, action_type, state)
		"set_expression":
			if _face_controller == null or not _face_controller.has_method("set_expression"):
				return _result(false, action_id, action_type, "failed", "face_controller_unavailable")
			var expression := String(params.get("expression", "neutral"))
			var intensity := clampf(float(params.get("intensity", 1.0)), 0.0, 1.0)
			var transition := float(params.get("transition_seconds", -1.0))
			var accepted := bool(_face_controller.call("set_expression", expression, intensity, transition))
			var expression_key := expression.strip_edges().to_lower().replace("-", "_").replace(" ", "_")
			var body_result := {}
			if BODY_AFFECT_BY_EXPRESSION.has(expression_key):
				body_result = _play_semantic_result(
					action_id,
					action_type,
					String(BODY_AFFECT_BY_EXPRESSION[expression_key])
				)
			var result := _result(accepted, action_id, action_type, "accepted" if accepted else "rejected", "expression:%s" % expression)
			result["face_visual_applied"] = bool(
				_face_controller.call("get_face_safety_status").get("facial_deformation_enabled", false)
			)
			result["neutral_face_preserved"] = not bool(result["face_visual_applied"])
			result["body_affect_applied"] = bool(body_result.get("accepted", false))
			if not body_result.is_empty():
				result["animation"] = body_result.get("animation", "")
				result["duration_seconds"] = body_result.get("duration_seconds", 0.0)
			return result
		"set_viseme":
			if _face_controller == null:
				return _result(false, action_id, action_type, "failed", "face_controller_unavailable")
			var viseme := String(params.get("viseme", params.get("vowel", "")))
			var intensity := clampf(float(params.get("intensity", 1.0)), 0.0, 1.0)
			var transition := float(params.get("transition_seconds", -1.0))
			var accepted := false
			if _face_controller.has_method("set_japanese_vowel") and viseme in ["あ", "い", "う", "え", "お"]:
				accepted = bool(_face_controller.call("set_japanese_vowel", viseme, intensity, transition))
			elif _face_controller.has_method("set_viseme"):
				accepted = bool(_face_controller.call("set_viseme", viseme, intensity, transition))
			var result := _result(accepted, action_id, action_type, "accepted" if accepted else "rejected", "viseme:%s" % viseme)
			result["lip_sync_visual_applied"] = bool(
				_face_controller.call("get_face_safety_status").get("facial_deformation_enabled", false)
			)
			result["audio_continues_without_fake_mouth"] = not bool(result["lip_sync_visual_applied"])
			return result
		"gesture":
			var gesture_name := String(params.get("name", params.get("gesture_id", ""))).strip_edges().to_lower()
			if not GESTURE_FALLBACKS.has(gesture_name):
				return _result(false, action_id, action_type, "rejected", "gesture_unavailable:%s" % gesture_name)
			var semantic := String(GESTURE_FALLBACKS[gesture_name])
			var gesture_result := _play_semantic_result(action_id, action_type, semantic)
			gesture_result["gesture"] = gesture_name
			gesture_result["motion_fallback"] = false
			gesture_result["independent_skeletal_motion"] = true
			return gesture_result
		"move_to":
			if _navigation_controller == null:
				return _result(false, action_id, action_type, "failed", "navigation_controller_unavailable")
			var anchor := String(params.get("anchor", ""))
			var speed := String(params.get("speed", "walk"))
			var timeout_ms := maxi(1000, int(params.get("timeout_ms", 10000)))
			var stop_distance := clampf(float(params.get("stop_distance", 0.12)), 0.05, 1.0)
			var started := bool(
				_navigation_controller.call(
					"start_move_to_anchor",
					anchor,
					speed,
					action_id,
					timeout_ms,
					stop_distance
				)
			)
			var move_result := _result(
				started,
				action_id,
				action_type,
				"started_async" if started else "rejected",
				"anchor:%s" % anchor
			)
			move_result["anchor"] = anchor
			return move_result
		"sit":
			var sit_result := _play_semantic_result(action_id, action_type, "sit_down")
			sit_result["anchor"] = String(params.get("anchor", ""))
			return sit_result
		"stand":
			return _play_semantic_result(action_id, action_type, "stand_up")
		"look_at":
			var target := String(params.get("target", "user"))
			var duration_ms := maxi(0, int(params.get("duration_ms", 0)))
			if _gaze_controller == null:
				return _result(false, action_id, action_type, "failed", "gaze_controller_unavailable")
			var gaze_started := bool(_gaze_controller.call("look_at", target, duration_ms))
			if gaze_started:
				look_at_requested.emit(action_id, target, duration_ms)
			var gaze_result := _result(
				gaze_started,
				action_id,
				action_type,
				"accepted" if gaze_started else "rejected",
				"gaze_target:%s" % target
			)
			gaze_result["target"] = target
			gaze_result["duration_seconds"] = float(maxi(120, duration_ms)) / 1000.0
			return gaze_result
		"speak":
			var text := String(params.get("text", ""))
			if text.is_empty():
				return _result(false, action_id, action_type, "rejected", "empty_speech")
			speech_requested.emit(action_id, text)
			var speak_result := _play_semantic_result(action_id, action_type, "talk")
			speak_result["speech_signal_emitted"] = true
			speak_result["speech_posture"] = _current_posture
			return speak_result
		"reach", "pick_up", "put_down", "offer_hand", "hold_hand", "release_hand", "inspect", "use_object":
			return _execute_affordance_action(action_id, action_type, params)
		"follow":
			if _navigation_controller == null:
				return _result(false, action_id, action_type, "failed", "navigation_controller_unavailable")
			var follow_anchor := String(params.get("anchor", params.get("target", "user")))
			var follow_started := bool(
				_navigation_controller.call(
					"start_move_to_anchor",
					follow_anchor,
					String(params.get("speed", "walk")),
					action_id,
					maxi(1000, int(params.get("timeout_ms", 12000))),
					clampf(float(params.get("stop_distance", 0.9)), 0.45, 1.5)
				)
			)
			var follow_result := _result(
				follow_started,
				action_id,
				action_type,
				"started_async" if follow_started else "rejected",
				"follow_anchor:%s" % follow_anchor
			)
			follow_result["anchor"] = follow_anchor
			return follow_result
		"stop":
			if _navigation_controller != null and _navigation_controller.has_method("stop"):
				_navigation_controller.call("stop", String(params.get("reason", "stopped")))
			if _gaze_controller != null and _gaze_controller.has_method("cancel"):
				_gaze_controller.call("cancel")
			if _affordance_controller != null and _affordance_controller.has_method("release_all"):
				_affordance_controller.call("release_all")
			return _play_semantic_result(action_id, action_type, "idle")
		"touch_reflex":
			return _execute_touch_reflex(action_id, params)
	return _result(false, action_id, action_type, "rejected", "unimplemented_action:%s" % action_type)


func _execute_touch_reflex(action_id: String, event: Dictionary) -> Dictionary:
	var zone := String(event.get("zone", "")).strip_edges().to_lower()
	var gesture := String(event.get("gesture", "")).strip_edges().to_lower()
	var integrated_face := _integrated_face_available()
	var expression := "neutral"
	var semantic := "idle"
	if zone in ["head", "hair"] and gesture in ["pat", "stroke", "hold"]:
		expression = "head_pat" if integrated_face else "neutral"
		semantic = "head_pat_reaction"
	elif zone in ["head", "hair"]:
		expression = "neutral"
		semantic = "turn_recover"
	elif zone in ["left_hand", "right_hand", "hand"]:
		expression = "happy_soft" if integrated_face else "neutral"
		semantic = "high_five" if gesture == "high_five" else "offer_hand"
	elif zone == "shoulder":
		expression = "surprised" if integrated_face else "neutral"
		semantic = "shoulder_touch_reaction"
	else:
		return _result(false, action_id, "touch_reflex", "rejected", "unsupported_touch:%s:%s" % [zone, gesture])
	if _face_controller != null and _face_controller.has_method("set_expression"):
		_face_controller.call("set_expression", expression, 1.0, 0.06)
	var result := _play_semantic_result(action_id, "touch_reflex", semantic)
	result["zone"] = zone
	result["gesture"] = gesture
	result["expression"] = expression
	result["llm_waited"] = false
	return result


func _integrated_face_available() -> bool:
	if _face_controller == null or not _face_controller.has_method("get_face_safety_status"):
		return false
	var status: Dictionary = _face_controller.call("get_face_safety_status")
	return (
		bool(status.get("integrated_face_morphs_enabled", false))
		and bool(status.get("facial_deformation_enabled", false))
	)


func _execute_affordance_action(
	action_id: String,
	action_type: String,
	params: Dictionary
) -> Dictionary:
	if _affordance_controller == null:
		return _result(false, action_id, action_type, "failed", "affordance_controller_unavailable")
	var semantic := String({
		"reach": "reach",
		"pick_up": "pick_up",
		"put_down": "put_down",
		"offer_hand": "offer_hand",
		"hold_hand": "hold_hand",
		"release_hand": "release_hand",
		"inspect": "inspect",
		"use_object": "inspect",
	}.get(action_type, ""))
	if semantic.is_empty():
		return _result(false, action_id, action_type, "rejected", "missing_affordance_motion")
	var motion_result := _play_semantic_result(action_id, action_type, semantic)
	if not bool(motion_result.get("accepted", false)):
		return motion_result
	var effect: Dictionary = _affordance_controller.call("perform", action_type, params)
	if not bool(effect.get("accepted", false)):
		effect["action_id"] = action_id
		effect["adapter"] = "embodied_v1"
		return effect
	for key in effect.keys():
		if key not in ["accepted", "type", "status", "reason"]:
			motion_result[key] = effect[key]
	motion_result["reason"] = String(effect.get("reason", motion_result.get("reason", "accepted")))
	motion_result["params"] = params.duplicate(true)
	var requested_duration_ms := int(params.get("duration_ms", 0))
	if requested_duration_ms > 0:
		motion_result["duration_seconds"] = float(requested_duration_ms) / 1000.0
	elif action_type == "hold_hand":
		motion_result["duration_seconds"] = 1.5
	return motion_result


func _play_semantic_result(action_id: String, action_type: String, semantic_state: String) -> Dictionary:
	var normalized := semantic_state.strip_edges().to_lower().replace("-", "_").replace(" ", "_")
	var aliases := {
		"neutral": "idle",
		"waiting": "idle",
		"listening": "idle",
		"thinking": "catching_breath",
		"turn": "turn_right",
		"sitting": "sit",
		"standing": "stand",
		"talking": "talk",
	}
	if aliases.has(normalized):
		normalized = String(aliases[normalized])
	var requested_semantic := normalized
	var posture_preserved := false
	if _current_posture == "sitting" and normalized in ["idle", "idle_handoff", "talk", "speaking"]:
		if normalized in ["talk", "speaking"] and not _resolve_animation_name("talk_sitting").is_empty():
			normalized = "talk_sitting"
		else:
			normalized = "sit"
			posture_preserved = requested_semantic != normalized
	if not SEMANTIC_ANIMATIONS.has(normalized):
		return _result(false, action_id, action_type, "rejected", "semantic_state_unavailable:%s" % normalized)
	var animation_name := _resolve_animation_name(normalized)
	if animation_name.is_empty() or not _play_animation(animation_name):
		return _result(false, action_id, action_type, "failed", "animation_unavailable:%s" % normalized)
	var result := _result(true, action_id, action_type, "accepted", "animation:%s" % animation_name)
	result["semantic_state"] = normalized
	result["animation"] = animation_name
	result["duration_seconds"] = _animation_length(animation_name)
	if EXPRESSION_BY_SEMANTIC.has(normalized) and _face_controller != null and _face_controller.has_method("set_expression"):
		var linked_expression := String(EXPRESSION_BY_SEMANTIC[normalized])
		var expression_accepted := bool(_face_controller.call("set_expression", linked_expression, 1.0, 0.10))
		result["linked_expression"] = linked_expression
		result["linked_expression_accepted"] = expression_accepted
	if normalized in ["sit_down", "sit", "sit_idle", "talk_sitting", "sit_settle", "sit_prepare_stand"]:
		_current_posture = "sitting"
	elif normalized in ["stand", "stand_up"]:
		_current_posture = "standing"
	result["posture"] = _current_posture
	if posture_preserved:
		result["requested_semantic_state"] = requested_semantic
		result["posture_preserved"] = true
		result["seated_motion_fallback"] = true
	return result


func _resolve_animation_name(semantic_state: String) -> String:
	if _animation_player == null or not SEMANTIC_ANIMATIONS.has(semantic_state):
		return ""
	var candidates = SEMANTIC_ANIMATIONS[semantic_state]
	if typeof(candidates) == TYPE_STRING:
		var direct := String(candidates)
		return direct if _animation_player.has_animation(direct) else ""
	if typeof(candidates) != TYPE_ARRAY:
		return ""
	for candidate in candidates:
		var animation_name := String(candidate)
		if _animation_player.has_animation(animation_name):
			return animation_name
	return ""


func _resolved_semantic_count() -> int:
	var count := 0
	for semantic_state in SEMANTIC_ANIMATIONS.keys():
		if not _resolve_animation_name(String(semantic_state)).is_empty():
			count += 1
	return count


func _play_animation(animation_name: String) -> bool:
	if _animation_player == null or not _animation_player.has_animation(animation_name):
		return false
	_animation_player.play(animation_name, 0.10)
	_current_animation = animation_name
	return true


func _animation_length(animation_name: String) -> float:
	if _animation_player == null or not _animation_player.has_animation(animation_name):
		return 0.0
	var animation := _animation_player.get_animation(animation_name)
	if animation == null or animation.loop_mode != Animation.LOOP_NONE:
		return 0.0
	return maxf(0.0, animation.length)


func _configure_animation_loops() -> void:
	if _animation_player == null:
		return
	for animation_name in LOOPING_ANIMATIONS:
		if not _animation_player.has_animation(animation_name):
			continue
		var animation := _animation_player.get_animation(animation_name)
		if animation != null:
			animation.loop_mode = Animation.LOOP_LINEAR


func _normalize_command(command: Dictionary) -> Dictionary:
	var normalized := command.duplicate(true)
	var raw_action := String(
		command.get("type", command.get("action", command.get("skill", command.get("skill_id", ""))))
	).strip_edges().to_lower()
	var raw_params = command.get("params", {})
	var params: Dictionary = raw_params.duplicate(true) if typeof(raw_params) == TYPE_DICTIONARY else {}
	match raw_action:
		"idle_naturally":
			raw_action = "living_state"
			params["state"] = "idle"
		"gesture_small":
			raw_action = "gesture"
			params["name"] = String(params.get("name", params.get("gesture_id", command.get("gesture_id", "settle"))))
		"look_at_user", "face_user":
			raw_action = "look_at"
			params["target"] = "user"
		"reset_to_safe_pose", "stop_motion":
			raw_action = "stop"
		"conversation_activity", "living_state", "semantic_state":
			raw_action = "living_state"
			params["state"] = String(params.get("state", params.get("activity", params.get("semantic_state", "idle"))))
	if raw_action == "speak" and not params.has("text"):
		params["text"] = String(command.get("text", command.get("message", command.get("utterance", ""))))
	_sequence += 1
	normalized["action_id"] = String(command.get("action_id", command.get("command_id", "embodied_%06d" % _sequence)))
	normalized["type"] = raw_action
	normalized["source"] = String(command.get("source", "external"))
	normalized["params"] = params
	return normalized


func _reject(action_id: String, action_type: String, status: String, reason: String) -> Dictionary:
	var result := _result(false, action_id, action_type, status, reason)
	_last_result = result.duplicate(true)
	action_rejected.emit(result)
	return result


func _result(accepted: bool, action_id: String, action_type: String, status: String, reason: String) -> Dictionary:
	return {
		"accepted": accepted,
		"action_id": action_id,
		"type": action_type,
		"status": status,
		"reason": reason,
		"adapter": "embodied_v1",
	}


func _find_animation_player(node: Node) -> AnimationPlayer:
	if node == null:
		return null
	if node is AnimationPlayer:
		return node as AnimationPlayer
	for child in node.get_children():
		var found := _find_animation_player(child)
		if found != null:
			return found
	return null
