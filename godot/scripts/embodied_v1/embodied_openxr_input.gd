extends Node
class_name EmbodiedOpenXRInput

@export var enabled := false
@export_range(15.0, 120.0, 1.0) var sample_hz := 60.0

var _gateway: Node
var _head_tracker: Node3D
var _left_hand_tracker: Node3D
var _right_hand_tracker: Node3D
var _sample_accumulator := 0.0
var _last_timestamp_ms := -1
var _submitted_frames := 0


func _ready() -> void:
	set_process(false)


func bind_gateway(gateway: Node) -> bool:
	_gateway = gateway
	return _gateway != null and _gateway.has_method("submit_frame")


func bind_tracker_nodes(head: Node3D, left_hand: Node3D, right_hand: Node3D) -> bool:
	_head_tracker = head
	_left_hand_tracker = left_hand
	_right_hand_tracker = right_hand
	return _head_tracker != null or _left_hand_tracker != null or _right_hand_tracker != null


func set_enabled(next_enabled: bool) -> void:
	enabled = next_enabled
	_sample_accumulator = 0.0
	set_process(enabled and _has_tracker_nodes())


func _process(delta: float) -> void:
	if not enabled or not _has_tracker_nodes():
		return
	_sample_accumulator += delta
	var interval := 1.0 / maxf(sample_hz, 1.0)
	if _sample_accumulator < interval:
		return
	_sample_accumulator = fmod(_sample_accumulator, interval)
	submit_snapshot()


func submit_snapshot(timestamp_ms := -1) -> Dictionary:
	if not enabled:
		return _result(false, "openxr_input_disabled")
	if _gateway == null:
		return _result(false, "tracking_gateway_unavailable")
	if not _has_tracker_nodes():
		return _result(false, "openxr_tracker_nodes_unbound")
	var resolved_timestamp := timestamp_ms if timestamp_ms >= 0 else Time.get_ticks_msec()
	if resolved_timestamp <= _last_timestamp_ms:
		resolved_timestamp = _last_timestamp_ms + 1
	_last_timestamp_ms = resolved_timestamp
	var frame := {
		"schema": "lumina.user_tracking.v1",
		"timestamp_ms": resolved_timestamp,
		"user_present": true,
	}
	_append_tracker(frame, "head", _head_tracker)
	_append_tracker(frame, "left_hand", _left_hand_tracker)
	_append_tracker(frame, "right_hand", _right_hand_tracker)
	var result: Dictionary = _gateway.call("submit_frame", "openxr", frame)
	if bool(result.get("accepted", false)):
		_submitted_frames += 1
	return result


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"tracker_nodes_bound": _has_tracker_nodes(),
		"head_bound": _head_tracker != null,
		"left_hand_bound": _left_hand_tracker != null,
		"right_hand_bound": _right_hand_tracker != null,
		"submitted_frames": _submitted_frames,
		"initializes_openxr_interface": false,
		"changes_rendering_mode": false,
		"controls_companion_skeleton": false,
	}


func _append_tracker(frame: Dictionary, tracker_name: String, tracker: Node3D) -> void:
	if tracker == null:
		return
	var quaternion := tracker.transform.basis.get_rotation_quaternion()
	frame[tracker_name] = {
		"position": [tracker.position.x, tracker.position.y, tracker.position.z],
		"rotation": [quaternion.x, quaternion.y, quaternion.z, quaternion.w],
		"confidence": 1.0,
		"tracked": tracker.is_inside_tree(),
		"gesture": "none",
	}


func _has_tracker_nodes() -> bool:
	return _head_tracker != null or _left_hand_tracker != null or _right_hand_tracker != null


func _result(accepted: bool, reason: String) -> Dictionary:
	return {
		"accepted": accepted,
		"status": "accepted" if accepted else "rejected",
		"reason": reason,
		"provider": "openxr",
	}
