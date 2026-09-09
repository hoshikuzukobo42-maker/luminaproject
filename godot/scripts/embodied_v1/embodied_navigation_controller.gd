extends Node
class_name EmbodiedNavigationController

signal navigation_started(action_id: String, anchor_id: String)
signal navigation_finished(action_id: String, ok: bool, status: String, detail: String)

@export var enabled := false
@export_range(0.2, 4.0, 0.1) var walk_speed := 1.15
@export_range(0.2, 6.0, 0.1) var run_speed := 2.2
@export_range(1.0, 20.0, 0.5) var rotation_speed := 8.0

var _body: CharacterBody3D
var _agent: NavigationAgent3D
var _action_adapter: Node
var _anchors: Dictionary = {}
var _moving := false
var _target_anchor := ""
var _target_node: Node3D
var _active_action_id := ""
var _speed := 1.15
var _stop_distance := 0.12
var _timeout_remaining := 0.0


func bind(body: CharacterBody3D, agent: NavigationAgent3D, action_adapter: Node) -> bool:
	_body = body
	_agent = agent
	_action_adapter = action_adapter
	if _agent != null:
		_agent.path_desired_distance = 0.10
		_agent.target_desired_distance = 0.10
		_agent.avoidance_enabled = false
	return _body != null and _agent != null and _action_adapter != null


func _physics_process(delta: float) -> void:
	advance_navigation(delta)


func register_anchor(anchor_id: String, anchor: Node3D) -> bool:
	var normalized := anchor_id.strip_edges().to_lower()
	if normalized.is_empty() or anchor == null:
		return false
	_anchors[normalized] = anchor
	return true


func start_move_to_anchor(
	anchor_id: String,
	speed_label := "walk",
	action_id := "",
	timeout_ms := 10000,
	stop_distance := 0.12
) -> bool:
	if not enabled or _body == null or _agent == null:
		return false
	var normalized := anchor_id.strip_edges().to_lower()
	if not _anchors.has(normalized):
		return false
	_target_node = _anchors[normalized] as Node3D
	if _target_node == null:
		return false
	_target_anchor = normalized
	_active_action_id = action_id
	_speed = run_speed if speed_label.strip_edges().to_lower() == "run" else walk_speed
	_stop_distance = clampf(stop_distance, 0.05, 1.0)
	_timeout_remaining = maxf(1.0, float(timeout_ms) / 1000.0)
	_agent.target_desired_distance = _stop_distance
	_agent.target_position = _flat_target_position()
	_moving = true
	_set_body_state("walk")
	navigation_started.emit(_active_action_id, _target_anchor)
	return true


func advance_navigation(delta: float) -> void:
	if not enabled or not _moving or _body == null or _target_node == null:
		return
	_timeout_remaining -= delta
	if _timeout_remaining <= 0.0:
		_finish(false, "timeout", "navigation timeout to %s" % _target_anchor)
		return
	var target := _flat_target_position()
	_agent.target_position = target
	var offset := target - _body.global_position
	offset.y = 0.0
	if offset.length() <= _stop_distance:
		_finish(true, "completed", "arrived:%s" % _target_anchor)
		return
	var next_position := _agent.get_next_path_position()
	var direction := next_position - _body.global_position
	direction.y = 0.0
	if direction.length() < 0.02:
		# The NavigationServer can take one physics frame to synchronize a new map.
		# Keep the target anchor as the bounded fallback; raw coordinates never enter this API.
		direction = offset
	if direction.length() < 0.001:
		return
	direction = direction.normalized()
	_body.velocity = direction * _speed
	_body.move_and_slide()
	var desired_yaw := atan2(direction.x, direction.z)
	_body.rotation.y = lerp_angle(_body.rotation.y, desired_yaw, clampf(delta * rotation_speed, 0.0, 1.0))


func stop(reason := "stopped") -> void:
	if not _moving:
		return
	_finish(false, "interrupted", reason)


func is_moving() -> bool:
	return _moving


func get_anchor_ids() -> PackedStringArray:
	var ids := PackedStringArray()
	for anchor_id in _anchors.keys():
		ids.append(String(anchor_id))
	ids.sort()
	return ids


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"moving": _moving,
		"target_anchor": _target_anchor,
		"active_action_id": _active_action_id,
		"anchor_ids": get_anchor_ids(),
		"raw_coordinate_input_allowed": false,
	}


func _finish(ok: bool, status: String, detail: String) -> void:
	var completed_id := _active_action_id
	_moving = false
	_body.velocity = Vector3.ZERO
	if ok and _target_node != null:
		_body.global_rotation.y = _target_node.global_rotation.y
	_set_body_state("idle")
	_target_node = null
	_target_anchor = ""
	_active_action_id = ""
	navigation_finished.emit(completed_id, ok, status, detail)


func _set_body_state(state: String) -> void:
	if _action_adapter == null or not _action_adapter.has_method("dispatch"):
		return
	_action_adapter.call(
		"dispatch",
		{
			"action_id": "navigation_state_%s" % state,
			"type": "living_state",
			"source": "local_navigation",
			"params": {"state": state},
		}
	)


func _flat_target_position() -> Vector3:
	var target := _target_node.global_position
	target.y = _body.global_position.y
	return target
