extends CharacterBody3D
class_name SIPlayerAvatarController

@export var input_enabled: bool = true
@export var walk_speed: float = 2.2
@export var run_speed: float = 3.8
@export var turn_speed: float = 10.0
@export var keyboard_turn_speed_deg: float = 145.0
@export var min_x: float = -24.25
@export var max_x: float = 24.25
@export var min_z: float = -24.25
@export var max_z: float = 24.25
@export var home_position: Vector3 = Vector3(0.0, 0.0, -2.05)
@export var near_lumina_position: Vector3 = Vector3(0.0, 0.0, -1.35)
@export var observe_sofa_position: Vector3 = Vector3(1.9, 0.0, -1.75)
@export var visual_root_path: NodePath
@export var vrm_adapter_path: NodePath
@export var eye_height: float = 1.55
@export var first_person_visual_hidden: bool = true
@export var mouse_turn_enabled: bool = true

var _target_position: Vector3
var _has_target := false
var _visual_root: Node3D
var _vrm_adapter: Node
var _first_person_view := false
var _current_motion := "Idle"
var _turn_left_pressed := false
var _turn_right_pressed := false
var _forward_pressed := false
var _backward_pressed := false
var _strafe_left_pressed := false
var _strafe_right_pressed := false
var _run_pressed := false


func _ready() -> void:
	_target_position = global_position
	set_process_input(true)
	add_to_group("player")
	call_deferred("_resolve_runtime_nodes")


func _input(event: InputEvent) -> void:
	if not input_enabled:
		return
	if event is InputEventKey and not event.echo:
		if _keyboard_control_blocked():
			_clear_keyboard_state()
			return
		if _handle_keyboard_event(event):
			get_viewport().set_input_as_handled()


func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_WINDOW_FOCUS_OUT or what == NOTIFICATION_APPLICATION_FOCUS_OUT:
		stop_keyboard_input()


func _physics_process(delta: float) -> void:
	if input_enabled:
		_apply_keyboard_movement(delta)
	_apply_target_movement(delta)


func move_by(offset: Vector3) -> void:
	_has_target = false
	global_position = _clamp_stage_position(global_position + _local_offset_to_world(offset))
	_set_motion_state("Idle")


func move_to_position(position: Vector3) -> void:
	_target_position = _clamp_stage_position(position)
	_has_target = true


func teleport_home() -> void:
	move_to_position(home_position)


func teleport_near_lumina() -> void:
	move_to_position(near_lumina_position)


func teleport_near_sena() -> void:
	teleport_near_lumina()


func teleport_observe_sofa() -> void:
	move_to_position(observe_sofa_position)


func face_position(position: Vector3) -> void:
	var direction := position - global_position
	direction.y = 0.0
	if direction.length() <= 0.05:
		return
	rotation.y = atan2(-direction.x, -direction.z)


func apply_view_yaw_delta(delta_degrees: float) -> void:
	if not mouse_turn_enabled:
		return
	_has_target = false
	rotation.y += deg_to_rad(delta_degrees)


func set_view_yaw_degrees(yaw_degrees: float) -> void:
	if not mouse_turn_enabled:
		return
	_has_target = false
	rotation.y = deg_to_rad(yaw_degrees)


func get_player_state() -> Dictionary:
	return {
		"position": {
			"x": global_position.x,
			"y": global_position.y,
			"z": global_position.z,
		},
		"yaw_degrees": rad_to_deg(rotation.y),
		"input_enabled": input_enabled,
		"has_target": _has_target,
		"first_person_view": _first_person_view,
		"motion": _current_motion,
	}


func get_eye_position() -> Vector3:
	return global_position + Vector3.UP * eye_height


func set_first_person_view(active: bool) -> void:
	_first_person_view = active
	if first_person_visual_hidden and is_instance_valid(_visual_root):
		_visual_root.visible = not active


func stop_keyboard_input() -> void:
	_clear_keyboard_state()
	if not _has_target:
		velocity = Vector3.ZERO
		_set_motion_state("Idle")


func _apply_keyboard_movement(delta: float) -> void:
	if _keyboard_control_blocked():
		stop_keyboard_input()
		return

	var turn_input := 0.0
	if _turn_left_pressed:
		turn_input += 1.0
	if _turn_right_pressed:
		turn_input -= 1.0
	if turn_input != 0.0:
		rotation.y += deg_to_rad(keyboard_turn_speed_deg) * turn_input * delta

	var forward_axis := 0.0
	if _forward_pressed:
		forward_axis += 1.0
	if _backward_pressed:
		forward_axis -= 1.0

	var strafe_axis := 0.0
	if _strafe_left_pressed:
		strafe_axis -= 1.0
	if _strafe_right_pressed:
		strafe_axis += 1.0

	var forward := -global_transform.basis.z
	forward.y = 0.0
	forward = forward.normalized()
	var right := global_transform.basis.x
	right.y = 0.0
	right = right.normalized()
	var direction := (forward * forward_axis) + (right * strafe_axis)
	if direction == Vector3.ZERO:
		velocity = Vector3.ZERO
		move_and_slide()
		_set_motion_state("Idle")
		return

	_has_target = false
	var speed := run_speed if _run_pressed else walk_speed
	direction = direction.normalized()
	velocity = direction * speed
	move_and_slide()
	global_position = _clamp_stage_position(global_position)
	_set_motion_state("Walk")


func _apply_target_movement(delta: float) -> void:
	if not _has_target:
		return
	var direction := _target_position - global_position
	direction.y = 0.0
	if direction.length() <= 0.05:
		_has_target = false
		velocity = Vector3.ZERO
		global_position = _target_position
		_set_motion_state("Idle")
		return
	var step_direction := direction.normalized()
	velocity = step_direction * walk_speed
	move_and_slide()
	global_position = _clamp_stage_position(global_position)
	_face_direction(step_direction, delta)
	_set_motion_state("Walk")


func _face_direction(direction: Vector3, delta: float) -> void:
	if direction.length() <= 0.05:
		return
	var target_yaw := atan2(-direction.x, -direction.z)
	rotation.y = lerp_angle(rotation.y, target_yaw, clampf(delta * turn_speed, 0.0, 1.0))


func _clamp_stage_position(position: Vector3) -> Vector3:
	return Vector3(
		clampf(position.x, min_x, max_x),
		0.0,
		clampf(position.z, min_z, max_z)
	)


func _local_offset_to_world(offset: Vector3) -> Vector3:
	var forward := -global_transform.basis.z
	forward.y = 0.0
	if forward.length() <= 0.001:
		forward = Vector3.FORWARD
	forward = forward.normalized()

	var right := global_transform.basis.x
	right.y = 0.0
	if right.length() <= 0.001:
		right = Vector3.RIGHT
	right = right.normalized()

	return (right * offset.x) + (Vector3.UP * offset.y) + (forward * -offset.z)


func _handle_keyboard_event(event: InputEventKey) -> bool:
	var pressed := event.pressed
	match event.keycode:
		KEY_LEFT:
			_turn_left_pressed = pressed
		KEY_RIGHT:
			_turn_right_pressed = pressed
		KEY_UP, KEY_W:
			_forward_pressed = pressed
		KEY_DOWN, KEY_S:
			_backward_pressed = pressed
		KEY_A:
			_strafe_left_pressed = pressed
		KEY_D:
			_strafe_right_pressed = pressed
		KEY_SHIFT:
			_run_pressed = pressed
		_:
			return false
	return true


func _clear_keyboard_state() -> void:
	_turn_left_pressed = false
	_turn_right_pressed = false
	_forward_pressed = false
	_backward_pressed = false
	_strafe_left_pressed = false
	_strafe_right_pressed = false
	_run_pressed = false


func _keyboard_control_blocked() -> bool:
	var viewport := get_viewport()
	if viewport == null:
		return false
	return _is_text_entry_control(viewport.gui_get_focus_owner())


func _is_text_entry_control(control: Control) -> bool:
	if control == null:
		return false
	return control is LineEdit or control is TextEdit or control is SpinBox


func _resolve_runtime_nodes() -> void:
	if visual_root_path != NodePath():
		_visual_root = get_node_or_null(visual_root_path) as Node3D
	if vrm_adapter_path != NodePath():
		_vrm_adapter = get_node_or_null(vrm_adapter_path)
	set_first_person_view(_first_person_view)
	_set_motion_state("Idle")


func _set_motion_state(motion_name: String) -> void:
	if motion_name != _current_motion:
		_current_motion = motion_name
	if _vrm_adapter and _vrm_adapter.has_method("set_motion_state"):
		_vrm_adapter.call("set_motion_state", motion_name, velocity.length())
