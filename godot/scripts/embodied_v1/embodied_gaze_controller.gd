extends Node
class_name EmbodiedGazeController

signal gaze_started(target_id: String, duration_ms: int)
signal gaze_finished(target_id: String)

@export var enabled := false
@export_range(1.0, 20.0, 0.5) var turn_speed := 7.5

var _body: CharacterBody3D
var _anchors: Dictionary = {}
var _target_id := ""
var _target: Node3D
var _remaining := 0.0


func bind(body: CharacterBody3D, anchors: Dictionary) -> bool:
	_body = body
	_anchors.clear()
	for raw_id in anchors.keys():
		var anchor = anchors[raw_id]
		if anchor is Node3D:
			_anchors[String(raw_id).strip_edges().to_lower()] = anchor
	return _body != null and not _anchors.is_empty()


func _process(delta: float) -> void:
	advance_gaze(delta)


func look_at(target_id: String, duration_ms := 900) -> bool:
	if not enabled or _body == null:
		return false
	var normalized := target_id.strip_edges().to_lower()
	if not _anchors.has(normalized):
		return false
	_target_id = normalized
	_target = _anchors[normalized] as Node3D
	_remaining = maxf(0.12, float(duration_ms) / 1000.0)
	gaze_started.emit(_target_id, duration_ms)
	return _target != null


func advance_gaze(delta: float) -> void:
	if not enabled or _body == null or _target == null:
		return
	var direction := _target.global_position - _body.global_position
	direction.y = 0.0
	if direction.length() > 0.001:
		var desired_yaw := atan2(direction.x, direction.z)
		_body.rotation.y = lerp_angle(
			_body.rotation.y,
			desired_yaw,
			clampf(delta * turn_speed, 0.0, 1.0)
		)
	_remaining = maxf(0.0, _remaining - delta)
	if _remaining <= 0.0:
		var completed := _target_id
		_target_id = ""
		_target = null
		gaze_finished.emit(completed)


func cancel() -> void:
	_target_id = ""
	_target = null
	_remaining = 0.0


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"bound": _body != null,
		"target": _target_id,
		"active": _target != null,
		"anchor_ids": _anchors.keys(),
		"raw_coordinate_input_allowed": false,
	}
