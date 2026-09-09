extends CharacterBody3D
class_name LuminaWalkablePlayerController

signal interaction_requested
signal soft_bound_hit

@export var mouse_sensitivity := 0.0020
@export var min_pitch_degrees := -68.0
@export var max_pitch_degrees := 72.0
@export var input_enabled := true
@export var acceleration := 12.0
@export var walk_speed := 2.35
@export var sprint_speed := 4.0
## Soft-bound cues stay off until Living Hub expands bounds / finishes boot.
@export var soft_bounds_enabled := false

var _camera_pivot: Node3D
var _spawn_position := Vector3.ZERO
var _spawn_yaw_degrees := 180.0
var _min_x := -6.5
var _max_x := 6.5
var _min_z := -4.5
var _max_z := 4.5
var _pitch := 0.0
var _distance_walked := 0.0
var _last_position := Vector3.ZERO
var _bound_hit_cooldown := 0.0
var _floor_y := 0.0


func _ready() -> void:
	_camera_pivot = get_node_or_null("CameraPivot") as Node3D
	_last_position = global_position
	_floor_y = global_position.y
	_ensure_move_actions()
	set_process_unhandled_input(true)
	set_notify_transform(false)


func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_WINDOW_FOCUS_OUT:
		set_mouse_captured(false)


func configure(room_config: Dictionary) -> void:
	_spawn_position = _vec3(room_config.get("spawn", [0.0, 0.0, 0.0]))
	_spawn_yaw_degrees = float(room_config.get("spawn_yaw_degrees", 180.0))
	_floor_y = _spawn_position.y
	var bounds: Dictionary = room_config.get("bounds", {})
	_min_x = float(bounds.get("min_x", -6.5))
	_max_x = float(bounds.get("max_x", 6.5))
	_min_z = float(bounds.get("min_z", -4.5))
	_max_z = float(bounds.get("max_z", 4.5))
	reset_to_spawn()


func reset_to_spawn() -> void:
	global_position = _spawn_position
	rotation = Vector3(0.0, deg_to_rad(_spawn_yaw_degrees), 0.0)
	velocity = Vector3.ZERO
	_pitch = 0.0
	if _camera_pivot != null:
		_camera_pivot.rotation.x = 0.0
	_last_position = global_position


func set_mouse_captured(captured: bool) -> void:
	if DisplayServer.get_name().to_lower() == "headless":
		return
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED if captured else Input.MOUSE_MODE_VISIBLE


func _unhandled_input(event: InputEvent) -> void:
	if not input_enabled:
		return
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		set_mouse_captured(true)
		get_viewport().set_input_as_handled()
		return
	if event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		rotation.y -= event.relative.x * mouse_sensitivity
		_pitch = clampf(
			_pitch - event.relative.y * mouse_sensitivity,
			deg_to_rad(min_pitch_degrees),
			deg_to_rad(max_pitch_degrees)
		)
		if _camera_pivot != null:
			_camera_pivot.rotation.x = _pitch
		get_viewport().set_input_as_handled()
		return
	if event.is_action_pressed("living_hub_interact") or (event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_E):
		interaction_requested.emit()
		get_viewport().set_input_as_handled()
		return
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_TAB:
		# Esc is owned by the menu HUD (open/close). Tab only releases look.
		set_mouse_captured(false)
		get_viewport().set_input_as_handled()


func _physics_process(delta: float) -> void:
	if not input_enabled:
		return
	if _bound_hit_cooldown > 0.0:
		_bound_hit_cooldown = maxf(0.0, _bound_hit_cooldown - delta)
	var input_vector := Input.get_vector("move_left", "move_right", "move_back", "move_forward")
	# Fallback if InputMap actions missing in older projects.
	if input_vector == Vector2.ZERO:
		if Input.is_key_pressed(KEY_A) or Input.is_key_pressed(KEY_LEFT):
			input_vector.x -= 1.0
		if Input.is_key_pressed(KEY_D) or Input.is_key_pressed(KEY_RIGHT):
			input_vector.x += 1.0
		if Input.is_key_pressed(KEY_W) or Input.is_key_pressed(KEY_UP):
			input_vector.y += 1.0
		if Input.is_key_pressed(KEY_S) or Input.is_key_pressed(KEY_DOWN):
			input_vector.y -= 1.0
	# Gamepad left stick.
	var pad := Vector2(
		Input.get_joy_axis(0, JOY_AXIS_LEFT_X),
		-Input.get_joy_axis(0, JOY_AXIS_LEFT_Y)
	)
	if pad.length() > 0.22:
		input_vector += pad
	var target_velocity := Vector3.ZERO
	if input_vector.length() > 0.0:
		input_vector = input_vector.limit_length(1.0)
		var forward := -global_transform.basis.z
		var right := global_transform.basis.x
		forward.y = 0.0
		right.y = 0.0
		var direction := (right.normalized() * input_vector.x) + (forward.normalized() * input_vector.y)
		var sprinting := Input.is_action_pressed("living_hub_sprint") or Input.is_key_pressed(KEY_SHIFT)
		var speed := sprint_speed if sprinting else walk_speed
		target_velocity = direction.normalized() * speed
	velocity.x = move_toward(velocity.x, target_velocity.x, acceleration * delta)
	velocity.z = move_toward(velocity.z, target_velocity.z, acceleration * delta)
	velocity.y = -0.5
	move_and_slide()
	_enforce_bounds()
	_record_distance()


func drive_toward_for_test(target: Vector3, speed: float = 5.0) -> bool:
	var flat_target := Vector3(target.x, 0.0, target.z)
	var direction := flat_target - Vector3(global_position.x, 0.0, global_position.z)
	if direction.length() <= 0.16:
		velocity = Vector3.ZERO
		return true
	velocity = direction.normalized() * speed
	velocity.y = -0.5
	move_and_slide()
	_enforce_bounds()
	_record_distance()
	return Vector2(global_position.x - flat_target.x, global_position.z - flat_target.z).length() <= 0.16


func get_walk_state() -> Dictionary:
	return {
		"position": {"x": global_position.x, "y": global_position.y, "z": global_position.z},
		"yaw_degrees": rad_to_deg(rotation.y),
		"distance_walked": _distance_walked,
		"mouse_captured": Input.mouse_mode == Input.MOUSE_MODE_CAPTURED,
		"input_enabled": input_enabled,
	}


func _enforce_bounds() -> void:
	var next_x := clampf(global_position.x, _min_x, _max_x)
	var next_z := clampf(global_position.z, _min_z, _max_z)
	var hit := false
	# Kill outward velocity so soft walls don't feel like position "jitter".
	if not is_equal_approx(next_x, global_position.x):
		velocity.x = 0.0
		hit = true
	if not is_equal_approx(next_z, global_position.z):
		velocity.z = 0.0
		hit = true
	global_position.x = next_x
	global_position.z = next_z
	global_position.y = _floor_y
	if hit and soft_bounds_enabled and _bound_hit_cooldown <= 0.0:
		_bound_hit_cooldown = 1.6
		soft_bound_hit.emit()
		print("LUMINA_WALKABLE_SOFT_BOUND_HIT")


func _record_distance() -> void:
	var flat_delta := Vector2(global_position.x - _last_position.x, global_position.z - _last_position.z).length()
	if flat_delta < 1.0:
		_distance_walked += flat_delta
	_last_position = global_position


func _ensure_move_actions() -> void:
	_ensure_key_action("move_left", [KEY_A, KEY_LEFT])
	_ensure_key_action("move_right", [KEY_D, KEY_RIGHT])
	_ensure_key_action("move_forward", [KEY_W, KEY_UP])
	_ensure_key_action("move_back", [KEY_S, KEY_DOWN])
	_ensure_key_action("living_hub_sprint", [KEY_SHIFT])


func _ensure_key_action(action: String, keycodes: Array) -> void:
	if not InputMap.has_action(action):
		InputMap.add_action(action)
	for keycode in keycodes:
		var ev := InputEventKey.new()
		ev.physical_keycode = int(keycode)
		var exists := false
		for existing in InputMap.action_get_events(action):
			if existing is InputEventKey and (existing as InputEventKey).physical_keycode == int(keycode):
				exists = true
				break
		if not exists:
			InputMap.action_add_event(action, ev)


func _vec3(value: Variant) -> Vector3:
	if value is Array and value.size() >= 3:
		return Vector3(float(value[0]), float(value[1]), float(value[2]))
	return Vector3.ZERO
