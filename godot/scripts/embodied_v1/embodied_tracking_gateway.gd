extends Node
class_name EmbodiedTrackingGateway

signal frame_accepted(provider: String, frame: Dictionary)
signal frame_rejected(provider: String, result: Dictionary)
signal contact_ready(event: Dictionary)

const PROVIDERS := ["vmc", "openxr", "webcam"]
const TOP_LEVEL_FIELDS := ["schema", "timestamp_ms", "user_present", "head", "left_hand", "right_hand"]
const TRACKER_FIELDS := ["position", "rotation", "confidence", "tracked", "gesture"]
const GESTURES := [
	"none",
	"open_palm",
	"closed_fist",
	"pinch",
	"point",
	"thumb_up",
	"victory",
	"offer_hand",
	"high_five",
	"pat",
	"stroke",
	"hold",
]
const CONTACT_ZONES := ["head", "hair", "left_hand", "right_hand", "hand", "shoulder"]
const CONTACT_GESTURES := ["pat", "stroke", "hold", "tap", "high_five", "offer_hand"]
const SENSITIVE_FIELDS := [
	"audio",
	"audio_base64",
	"base64",
	"bytes",
	"file",
	"image",
	"image_base64",
	"path",
	"pixels",
	"raw_frame",
	"raw_image",
	"texture",
]

@export var enabled := false
@export_range(0.5, 20.0, 0.5) var max_abs_coordinate_m := 5.0

var _provider_opt_in := {
	"vmc": false,
	"openxr": false,
	"webcam": false,
}
var _last_timestamp_ms := {}
var _last_frame_summary := {}
var _accepted_frames := 0
var _rejected_frames := 0
var _accepted_contacts := 0


func set_provider_opt_in(provider: String, next_enabled: bool) -> bool:
	var normalized := _normalize_provider(provider)
	if not PROVIDERS.has(normalized):
		return false
	_provider_opt_in[normalized] = next_enabled
	if not next_enabled:
		_last_timestamp_ms.erase(normalized)
		_last_frame_summary.erase(normalized)
	return true


func is_provider_opted_in(provider: String) -> bool:
	var normalized := _normalize_provider(provider)
	return bool(_provider_opt_in.get(normalized, false))


func is_provider_active(provider: String) -> bool:
	return enabled and is_provider_opted_in(provider)


func submit_frame(provider: String, frame: Dictionary) -> Dictionary:
	var normalized_provider := _normalize_provider(provider)
	var gate := _provider_gate(normalized_provider)
	if not bool(gate.get("accepted", false)):
		return _reject_frame(normalized_provider, String(gate.get("reason", "provider_rejected")))
	var sensitive_path := _find_sensitive_field(frame)
	if not sensitive_path.is_empty():
		return _reject_frame(normalized_provider, "raw_media_forbidden:%s" % sensitive_path)
	for raw_key in frame.keys():
		var key := String(raw_key)
		if not TOP_LEVEL_FIELDS.has(key):
			return _reject_frame(normalized_provider, "frame_field_not_allowlisted:%s" % key)
	var schema := String(frame.get("schema", "lumina.user_tracking.v1"))
	if schema != "lumina.user_tracking.v1":
		return _reject_frame(normalized_provider, "unsupported_schema:%s" % schema)
	var timestamp_ms := int(frame.get("timestamp_ms", -1))
	if timestamp_ms < 0:
		return _reject_frame(normalized_provider, "invalid_timestamp")
	if timestamp_ms <= int(_last_timestamp_ms.get(normalized_provider, -1)):
		return _reject_frame(normalized_provider, "non_monotonic_timestamp")
	var normalized_frame := {
		"schema": schema,
		"provider": normalized_provider,
		"timestamp_ms": timestamp_ms,
		"user_present": bool(frame.get("user_present", true)),
	}
	var tracked_nodes: Array[String] = []
	for tracker_name in ["head", "left_hand", "right_hand"]:
		if not frame.has(tracker_name):
			continue
		var tracker_result := _normalize_tracker(frame[tracker_name], tracker_name)
		if not bool(tracker_result.get("accepted", false)):
			return _reject_frame(normalized_provider, String(tracker_result.get("reason", "invalid_tracker")))
		normalized_frame[tracker_name] = tracker_result["tracker"]
		if bool((tracker_result["tracker"] as Dictionary).get("tracked", true)):
			tracked_nodes.append(tracker_name)
	_last_timestamp_ms[normalized_provider] = timestamp_ms
	_last_frame_summary[normalized_provider] = {
		"timestamp_ms": timestamp_ms,
		"user_present": normalized_frame["user_present"],
		"tracked_nodes": tracked_nodes,
	}
	_accepted_frames += 1
	frame_accepted.emit(normalized_provider, normalized_frame.duplicate(true))
	return {
		"accepted": true,
		"status": "accepted",
		"reason": "tracking_frame_sanitized",
		"provider": normalized_provider,
		"frame": normalized_frame,
	}


func submit_contact(provider: String, contact: Dictionary) -> Dictionary:
	var normalized_provider := _normalize_provider(provider)
	var gate := _provider_gate(normalized_provider)
	if not bool(gate.get("accepted", false)):
		return {
			"accepted": false,
			"status": "rejected",
			"reason": String(gate.get("reason", "provider_rejected")),
			"provider": normalized_provider,
		}
	var sensitive_path := _find_sensitive_field(contact)
	if not sensitive_path.is_empty():
		return {
			"accepted": false,
			"status": "rejected",
			"reason": "raw_media_forbidden:%s" % sensitive_path,
			"provider": normalized_provider,
		}
	var zone := String(contact.get("zone", "")).strip_edges().to_lower()
	var gesture := String(contact.get("gesture", "")).strip_edges().to_lower()
	if not CONTACT_ZONES.has(zone):
		return _contact_rejection(normalized_provider, "unsupported_contact_zone:%s" % zone)
	if not CONTACT_GESTURES.has(gesture):
		return _contact_rejection(normalized_provider, "unsupported_contact_gesture:%s" % gesture)
	var confidence := float(contact.get("confidence", 0.0))
	if not is_finite(confidence) or confidence < 0.0 or confidence > 1.0:
		return _contact_rejection(normalized_provider, "invalid_contact_confidence")
	var event := {
		"event": "touch",
		"zone": zone,
		"gesture": gesture,
		"duration_ms": maxi(0, int(contact.get("duration_ms", 0))),
		"velocity": maxf(0.0, float(contact.get("velocity", 0.0))),
		"confidence": confidence,
		"path_length": maxf(0.0, float(contact.get("path_length", 0.0))),
		"samples": maxi(1, int(contact.get("samples", 1))),
		"source": "tracking:%s" % normalized_provider,
	}
	_accepted_contacts += 1
	contact_ready.emit(event.duplicate(true))
	return {
		"accepted": true,
		"status": "accepted",
		"reason": "tracking_contact_sanitized",
		"provider": normalized_provider,
		"event": event,
	}


func get_status() -> Dictionary:
	var providers := {}
	for provider in PROVIDERS:
		providers[provider] = {
			"opt_in": bool(_provider_opt_in.get(provider, false)),
			"active": enabled and bool(_provider_opt_in.get(provider, false)),
		}
	return {
		"enabled": enabled,
		"providers": providers,
		"accepted_frames": _accepted_frames,
		"rejected_frames": _rejected_frames,
		"accepted_contacts": _accepted_contacts,
		"last_frame_summary": _last_frame_summary.duplicate(true),
		"raw_media_accepted": false,
		"persists_tracking_data": false,
		"controls_companion_skeleton": false,
		"provider_network_auto_start": false,
	}


func _normalize_tracker(raw_tracker: Variant, tracker_name: String) -> Dictionary:
	if typeof(raw_tracker) != TYPE_DICTIONARY:
		return {"accepted": false, "reason": "tracker_must_be_dictionary:%s" % tracker_name}
	var tracker: Dictionary = raw_tracker
	for raw_key in tracker.keys():
		var key := String(raw_key)
		if not TRACKER_FIELDS.has(key):
			return {"accepted": false, "reason": "tracker_field_not_allowlisted:%s.%s" % [tracker_name, key]}
	var normalized := {"tracked": bool(tracker.get("tracked", true))}
	if tracker.has("position"):
		var position_result := _parse_vector(tracker["position"], 3, "%s.position" % tracker_name)
		if not bool(position_result.get("accepted", false)):
			return position_result
		normalized["position"] = position_result["values"]
	if tracker.has("rotation"):
		var rotation_result := _parse_vector(tracker["rotation"], 4, "%s.rotation" % tracker_name, false)
		if not bool(rotation_result.get("accepted", false)):
			return rotation_result
		normalized["rotation"] = rotation_result["values"]
	var confidence := float(tracker.get("confidence", 1.0))
	if not is_finite(confidence) or confidence < 0.0 or confidence > 1.0:
		return {"accepted": false, "reason": "invalid_confidence:%s" % tracker_name}
	normalized["confidence"] = confidence
	var gesture := String(tracker.get("gesture", "none")).strip_edges().to_lower()
	if not GESTURES.has(gesture):
		return {"accepted": false, "reason": "unsupported_gesture:%s.%s" % [tracker_name, gesture]}
	normalized["gesture"] = gesture
	return {"accepted": true, "tracker": normalized}


func _parse_vector(raw_value: Variant, size: int, label: String, bounded := true) -> Dictionary:
	if typeof(raw_value) != TYPE_ARRAY:
		return {"accepted": false, "reason": "vector_must_be_array:%s" % label}
	var raw_array: Array = raw_value
	if raw_array.size() != size:
		return {"accepted": false, "reason": "vector_size_invalid:%s" % label}
	var values: Array[float] = []
	for raw_component in raw_array:
		var component := float(raw_component)
		if not is_finite(component):
			return {"accepted": false, "reason": "vector_not_finite:%s" % label}
		if bounded and absf(component) > max_abs_coordinate_m:
			return {"accepted": false, "reason": "vector_out_of_bounds:%s" % label}
		values.append(component)
	return {"accepted": true, "values": values}


func _provider_gate(provider: String) -> Dictionary:
	if not PROVIDERS.has(provider):
		return {"accepted": false, "reason": "unsupported_provider:%s" % provider}
	if not enabled:
		return {"accepted": false, "reason": "tracking_disabled"}
	if not bool(_provider_opt_in.get(provider, false)):
		return {"accepted": false, "reason": "provider_not_opted_in:%s" % provider}
	return {"accepted": true}


func _reject_frame(provider: String, reason: String) -> Dictionary:
	_rejected_frames += 1
	var result := {
		"accepted": false,
		"status": "rejected",
		"reason": reason,
		"provider": provider,
	}
	frame_rejected.emit(provider, result.duplicate(true))
	return result


func _contact_rejection(provider: String, reason: String) -> Dictionary:
	return {
		"accepted": false,
		"status": "rejected",
		"reason": reason,
		"provider": provider,
	}


func _find_sensitive_field(value: Variant, path := "") -> String:
	if value is Dictionary:
		var dictionary: Dictionary = value
		for raw_key in dictionary.keys():
			var key := String(raw_key)
			var child_path := key if path.is_empty() else "%s.%s" % [path, key]
			if SENSITIVE_FIELDS.has(key.to_lower()):
				return child_path
			var nested := _find_sensitive_field(dictionary[raw_key], child_path)
			if not nested.is_empty():
				return nested
	elif value is Array:
		var array: Array = value
		for index in range(array.size()):
			var nested := _find_sensitive_field(array[index], "%s[%d]" % [path, index])
			if not nested.is_empty():
				return nested
	return ""


func _normalize_provider(provider: String) -> String:
	return provider.strip_edges().to_lower().replace("-", "_")
