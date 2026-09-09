extends Node
class_name EmbodiedAuthority

const ALLOWED_ACTIONS := [
	"living_state",
	"set_expression",
	"set_viseme",
	"gesture",
	"move_to",
	"sit",
	"stand",
	"look_at",
	"speak",
	"reach",
	"pick_up",
	"put_down",
	"offer_hand",
	"hold_hand",
	"release_hand",
	"follow",
	"inspect",
	"use_object",
	"stop",
	"touch_reflex",
]
const FORBIDDEN_DIRECT_CONTROL_FIELDS := [
	"animation",
	"animation_name",
	"asset_path",
	"bone",
	"bone_angle",
	"coordinates",
	"global_position",
	"glb_path",
	"node_path",
	"position",
	"rotation",
	"route",
	"target_position",
	"x",
	"y",
	"z",
]
const ALLOWED_PARAMS := {
	"living_state": ["state", "speed", "blend_seconds"],
	"set_expression": ["expression", "intensity", "transition_seconds"],
	"set_viseme": ["viseme", "vowel", "intensity", "transition_seconds"],
	"gesture": ["name", "gesture_id", "intensity", "duration_ms"],
	"move_to": ["anchor", "speed", "timeout_ms", "stop_distance"],
	"sit": ["anchor"],
	"stand": [],
	"look_at": ["target", "duration_ms"],
	"speak": ["text", "emotion", "style"],
	"reach": ["object", "target", "duration_ms"],
	"pick_up": ["object", "target", "duration_ms"],
	"put_down": ["object", "target", "anchor", "duration_ms"],
	"offer_hand": ["duration_ms"],
	"hold_hand": ["duration_ms"],
	"release_hand": ["duration_ms"],
	"follow": ["target", "anchor", "speed", "timeout_ms", "stop_distance"],
	"inspect": ["object", "target", "duration_ms"],
	"use_object": ["object", "target", "use", "duration_ms"],
	"stop": ["reason"],
	"touch_reflex": [
		"event",
		"zone",
		"gesture",
		"duration_ms",
		"velocity",
		"confidence",
		"path_length",
		"samples",
		"source",
	],
}

@export var enabled := false


func validate(command: Dictionary) -> Dictionary:
	if not enabled:
		return _decision(false, "disabled", "embodied_v1_disabled")
	var action := String(command.get("type", command.get("action", ""))).strip_edges().to_lower()
	if action.is_empty():
		return _decision(false, "rejected", "missing_action")
	if not ALLOWED_ACTIONS.has(action):
		return _decision(false, "rejected", "action_not_allowlisted:%s" % action)
	var forbidden_path := _find_forbidden_field(command)
	if not forbidden_path.is_empty():
		return _decision(false, "rejected", "direct_control_forbidden:%s" % forbidden_path)
	var raw_params = command.get("params", {})
	if typeof(raw_params) != TYPE_DICTIONARY:
		return _decision(false, "rejected", "params_must_be_dictionary")
	var params: Dictionary = raw_params
	var allowed: Array = ALLOWED_PARAMS.get(action, [])
	for raw_key in params.keys():
		var key := String(raw_key)
		if not allowed.has(key):
			return _decision(false, "rejected", "param_not_allowlisted:%s.%s" % [action, key])
	var source := String(command.get("source", "external")).strip_edges().to_lower()
	if action == "touch_reflex" and source != "local_reflex":
		return _decision(false, "rejected", "touch_reflex_requires_local_source")
	return _decision(true, "allowed", "allowlisted_high_level_intent")


func _find_forbidden_field(value: Variant, path := "") -> String:
	if value is Dictionary:
		var dictionary: Dictionary = value
		for raw_key in dictionary.keys():
			var key := String(raw_key)
			var child_path := key if path.is_empty() else "%s.%s" % [path, key]
			if FORBIDDEN_DIRECT_CONTROL_FIELDS.has(key.to_lower()):
				return child_path
			var nested := _find_forbidden_field(dictionary[raw_key], child_path)
			if not nested.is_empty():
				return nested
	elif value is Array:
		var array: Array = value
		for index in range(array.size()):
			var nested := _find_forbidden_field(array[index], "%s[%d]" % [path, index])
			if not nested.is_empty():
				return nested
	return ""


func _decision(allowed: bool, status: String, reason: String) -> Dictionary:
	return {
		"allowed": allowed,
		"status": status,
		"reason": reason,
		"authority": "embodied_v1",
	}
