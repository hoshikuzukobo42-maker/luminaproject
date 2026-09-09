extends Camera3D
class_name SIFollowCamera

@export var target_path: NodePath
@export var avatar_target_path: NodePath
@export var target_height: float = 1.25
@export var distance: float = 3.2
@export var min_distance: float = 0.75
@export var max_distance: float = 14.0
@export var height: float = 1.6
@export var side_offset: float = 0.45
@export var front_bias: float = 1.0
@export var follow_smoothing: float = 8.0
@export var look_smoothing: float = 10.0
@export var orbit_speed: float = 0.35
@export var pan_speed: float = 0.0055
@export var zoom_speed: float = 0.45
@export var move_speed: float = 2.9
@export var vertical_speed: float = 2.4
@export var min_pitch_deg: float = 3.0
@export var max_pitch_deg: float = 78.0
@export var field_of_view_deg: float = 60.0
@export var start_in_first_person: bool = true
@export var startup_preset: String = ""
@export var first_person_eye_height: float = 1.55
@export var first_person_forward_offset: float = 0.08
@export var first_person_fov_deg: float = 62.0
@export var third_person_distance: float = 4.1
@export var third_person_height: float = 1.55
@export var third_person_pitch_deg: float = 12.0
@export var third_person_side_offset: float = 0.0
@export var third_person_yaw_offset_deg: float = 0.0
@export var player_mouse_look_enabled: bool = true
@export var player_mouse_button: int = MOUSE_BUTTON_RIGHT
@export var player_mouse_yaw_speed: float = 0.14
@export var player_mouse_pitch_speed: float = 0.10
@export var first_person_min_pitch_deg: float = -56.0
@export var first_person_max_pitch_deg: float = 58.0
@export var third_person_min_pitch_deg: float = 8.0
@export var third_person_max_pitch_deg: float = 46.0
@export var camera_bounds_enabled: bool = true
@export var camera_min_x: float = -24.35
@export var camera_max_x: float = 24.35
@export var camera_min_z: float = -24.35
@export var camera_max_z: float = 24.35
@export var camera_min_y: float = 0.35
@export var camera_max_y: float = 14.0
@export var camera_collision_enabled: bool = true
@export var camera_collision_margin: float = 0.22
@export var camera_collision_min_distance: float = 0.75
@export var view_toggle_keycode: int = KEY_V

var _target: Node3D
var _player_target: Node3D
var _avatar_target: Node3D
var _target_role: String = "player"
var _focus_position: Vector3 = Vector3.ZERO
var _distance: float
var _focus_height_offset: float = 0.0
var _yaw_deg: float = 180.0
var _pitch_deg: float = 18.0
var _follow_mode: bool = true

var _drag_left: bool = false
var _drag_right: bool = false
var _move_forward := false
var _move_backward := false
var _move_left := false
var _move_right := false
var _move_up := false
var _move_down := false

var _default_distance: float = 3.2
var _default_focus_height_offset: float = 0.38
var _default_pitch: float = 18.0
var _default_yaw: float = 180.0
var _current_preset_label: String = "custom"
var _player_view_first_person := false
var _player_view_third_person := false
var _player_view_pitch_deg := 0.0
var _yaw_relative_to_target := false
var _target_relative_yaw_deg := 0.0

const _PRESET_CAMERA: Dictionary = {
	1: {"label": "full_front", "distance": 4.0, "pitch": 10.0, "height": 1.20, "yaw": 0.0, "side_offset": 0.0, "fov": 58.0},
		2: {"label": "upper_front", "distance": 2.0, "pitch": 10.0, "height": 1.42, "yaw": 0.0, "side_offset": 0.0, "fov": 40.0},
	3: {"label": "side", "distance": 4.4, "pitch": 14.0, "height": 1.22, "yaw": 90.0, "side_offset": 0.0, "fov": 68.0},
	4: {"label": "diag_top", "distance": 2.2, "pitch": 48.0, "height": 1.75, "yaw": 145.0, "side_offset": 0.0, "fov": 48.0},
	5: {"label": "user_pov", "distance": 2.35, "pitch": 16.0, "height": 1.36, "yaw": 90.0, "side_offset": 0.0, "fov": 46.0},
	6: {"label": "user_over_shoulder", "distance": 4.35, "pitch": 14.0, "height": 1.55, "yaw": 106.0, "side_offset": -0.28, "fov": 58.0},
	7: {"label": "autonomy_audience", "distance": 4.15, "pitch": 12.0, "height": 1.34, "yaw": 0.0, "side_offset": 0.0, "fov": 56.0},
	8: {"label": "player_third_person", "distance": 4.1, "pitch": 12.0, "height": 1.55, "yaw": 180.0, "side_offset": 0.0, "fov": 62.0},
}
const _PRESET_LABEL_TO_INDEX: Dictionary = {
	"full_front": 1,
	"front": 1,
	"upper_front": 2,
	"upper": 2,
	"side": 3,
	"diag_top": 4,
	"user_pov": 5,
	"user": 5,
	"audience": 5,
	"user_over_shoulder": 6,
	"over_shoulder": 6,
	"shoulder": 6,
	"third_person": 8,
	"player_third_person": 8,
	"third": 8,
	"autonomy": 7,
	"autonomy_audience": 7,
	"autonomous": 7,
	"autonomy_view": 7,
	"life_mode": 7,
}


func _ready() -> void:
	set_process(true)
	set_process_input(true)
	set_process_unhandled_input(false)
	projection = Camera3D.PROJECTION_PERSPECTIVE
	fov = clampf(field_of_view_deg, 24.0, 85.0)
	make_current()
	_player_target = get_node_or_null(target_path) as Node3D
	_avatar_target = get_node_or_null(avatar_target_path) as Node3D
	_target = _player_target if _player_target else _avatar_target
	_target_role = "player" if _target == _player_target else "avatar"
	_distance = maxf(min_distance, distance)
	_focus_height_offset = clampf(height - target_height, -2.0, 2.5)
	if _target:
		_focus_position = _resolve_target_focus()
		_align_orbit_from_current_position()
	else:
		_focus_position = global_position

	_default_distance = _distance
	_default_focus_height_offset = _focus_height_offset
	_default_pitch = _pitch_deg
	_default_yaw = _yaw_deg

	if _target != null and _focus_position != global_position:
		look_at(_focus_position, Vector3.UP)
	var normalized_startup_preset := startup_preset.strip_edges().to_lower()
	if not normalized_startup_preset.is_empty() and _PRESET_LABEL_TO_INDEX.has(normalized_startup_preset):
		_apply_preset_by_label(normalized_startup_preset)
	elif start_in_first_person:
		_set_player_view_first_person(true)


func _process(delta: float) -> void:
	_update_target()
	if _player_view_first_person:
		_apply_first_person_camera(delta)
		return
	if _player_view_third_person:
		_sync_player_third_person_yaw()
	_update_target_relative_yaw()
	_apply_follow_focus(delta)
	_apply_free_translation(delta)

	var desired_position: Vector3 = _focus_position + _orbit_offset()
	desired_position = _resolve_safe_camera_position(_focus_position, desired_position)
	var follow_blend := clampf(delta * follow_smoothing, 0.0, 1.0)
	var transform_blend := clampf(delta * look_smoothing, 0.0, 1.0)
	_apply_camera_transform(desired_position, follow_blend if _follow_mode else transform_blend, transform_blend)


func _update_target() -> void:
	if _player_target == null and target_path != NodePath():
		_player_target = get_node_or_null(target_path) as Node3D
	if _avatar_target == null and avatar_target_path != NodePath():
		_avatar_target = get_node_or_null(avatar_target_path) as Node3D
	if _target == null:
		_set_active_target(_target_role)


func _set_active_target(role: String) -> bool:
	var normalized := role.strip_edges().to_lower()
	var next_target: Node3D = null
	match normalized:
		"avatar", "lumina", "ai":
			next_target = _avatar_target if _avatar_target else _player_target
			normalized = "avatar" if _avatar_target else "player"
		_:
			next_target = _player_target if _player_target else _avatar_target
			normalized = "player" if _player_target else "avatar"
	if next_target == null:
		return false
	if _target == next_target and _target_role == normalized:
		return true
	if _player_view_first_person and _target and _target != next_target:
		_notify_target_first_person_view(false)
	_target = next_target
	_target_role = normalized
	_focus_position = _resolve_target_focus()
	_update_target_relative_yaw()
	return true


func _apply_follow_focus(delta: float) -> void:
	if not _follow_mode:
		return
	if not _target:
		return
	var target_focus := _resolve_target_focus()
	_focus_position = _focus_position.lerp(target_focus, clampf(delta * follow_smoothing, 0.0, 1.0))


func _apply_free_translation(delta: float) -> void:
	if _follow_mode:
		return
	var move_direction := Vector3.ZERO
	if _move_forward:
		move_direction -= global_transform.basis.z
	if _move_backward:
		move_direction += global_transform.basis.z
	if _move_left:
		move_direction -= global_transform.basis.x
	if _move_right:
		move_direction += global_transform.basis.x
	move_direction.y = 0.0
	if move_direction.length() > 0.0:
		move_direction = move_direction.normalized() * move_speed * delta

	var vertical_delta := 0.0
	if _move_up:
		vertical_delta += 1.0
	if _move_down:
		vertical_delta -= 1.0
	var vertical := Vector3.UP * vertical_delta * vertical_speed * delta
	if move_direction != Vector3.ZERO or vertical != Vector3.ZERO:
		var step := move_direction + vertical
		global_position += step
		_focus_position += step


func _orbit_offset() -> Vector3:
	var pitch_rad := deg_to_rad(_pitch_deg)
	var yaw_rad := deg_to_rad(_yaw_deg)
	var cos_pitch := cos(pitch_rad)
	return Vector3(
		-sin(yaw_rad) * _distance * cos_pitch,
		sin(pitch_rad) * _distance,
		-cos(yaw_rad) * _distance * cos_pitch
	)


func _yaw_from_orbit_offset(offset: Vector3) -> float:
	var flat_offset := Vector3(offset.x, 0.0, offset.z)
	if flat_offset.length() <= 0.001:
		return _yaw_deg
	flat_offset = flat_offset.normalized()
	return rad_to_deg(atan2(-flat_offset.x, -flat_offset.z))


func _target_forward() -> Vector3:
	if not _target:
		return Vector3.FORWARD
	var forward := -_target.global_transform.basis.z
	forward.y = 0.0
	if forward.length() <= 0.001:
		return Vector3.FORWARD
	return forward.normalized()


func _target_right() -> Vector3:
	if not _target:
		return Vector3.RIGHT
	var right := _target.global_transform.basis.x
	right.y = 0.0
	if right.length() <= 0.001:
		return Vector3.RIGHT
	return right.normalized()


func _apply_camera_transform(desired_position: Vector3, position_blend: float, rotation_blend: float) -> void:
	global_position = global_position.lerp(desired_position, clampf(position_blend, 0.0, 1.0))
	if _focus_position == Vector3.ZERO or global_position.distance_to(_focus_position) < 0.001:
		return
	var target_transform := global_transform.looking_at(_focus_position, Vector3.UP)
	global_transform = global_transform.interpolate_with(target_transform, clampf(rotation_blend, 0.0, 1.0))


func _apply_first_person_camera(delta: float) -> void:
	if not _target:
		return
	var forward := _target_forward()
	var pitch_rad := deg_to_rad(_player_view_pitch_deg)
	var look_direction := (forward * cos(pitch_rad)) + (Vector3.UP * sin(pitch_rad))
	if look_direction.length() <= 0.001:
		look_direction = forward
	look_direction = look_direction.normalized()
	var eye_position := _target.global_position + Vector3.UP * first_person_eye_height + forward * first_person_forward_offset
	_focus_position = eye_position + look_direction * 8.0
	var blend := clampf(delta * follow_smoothing * 1.8, 0.0, 1.0)
	global_position = global_position.lerp(eye_position, blend)
	var target_transform := global_transform.looking_at(_focus_position, Vector3.UP)
	global_transform = global_transform.interpolate_with(target_transform, clampf(delta * look_smoothing * 1.8, 0.0, 1.0))


func _snap_to_camera_state() -> void:
	make_current()
	_update_target_relative_yaw()
	var desired_position := _focus_position + _orbit_offset()
	desired_position = _resolve_safe_camera_position(_focus_position, desired_position)
	_apply_camera_transform(desired_position, 1.0, 1.0)


func _resolve_safe_camera_position(focus: Vector3, desired_position: Vector3) -> Vector3:
	var safe_position := desired_position
	if camera_collision_enabled and not _player_view_first_person:
		safe_position = _resolve_camera_collision(focus, safe_position)
	if camera_bounds_enabled and not _player_view_first_person:
		safe_position.x = clampf(safe_position.x, camera_min_x, camera_max_x)
		safe_position.y = clampf(safe_position.y, camera_min_y, camera_max_y)
		safe_position.z = clampf(safe_position.z, camera_min_z, camera_max_z)
	return safe_position


func _resolve_camera_collision(focus: Vector3, desired_position: Vector3) -> Vector3:
	var world := get_world_3d()
	if world == null:
		return desired_position
	var offset := desired_position - focus
	var distance_to_focus := offset.length()
	if distance_to_focus <= camera_collision_min_distance:
		return desired_position
	var query := PhysicsRayQueryParameters3D.create(focus, desired_position)
	query.collide_with_areas = false
	query.collide_with_bodies = true
	query.exclude = _camera_collision_excludes()
	var hit := world.direct_space_state.intersect_ray(query)
	if hit.is_empty() or not hit.has("position"):
		return desired_position
	var direction := offset.normalized()
	var hit_position: Vector3 = hit.get("position", desired_position)
	var adjusted := hit_position - direction * camera_collision_margin
	var adjusted_distance := adjusted.distance_to(focus)
	if adjusted_distance < camera_collision_min_distance:
		return focus + direction * camera_collision_min_distance
	return adjusted


func _camera_collision_excludes() -> Array[RID]:
	var excludes: Array[RID] = []
	var target_collision := _target as CollisionObject3D
	if target_collision != null:
		excludes.append(target_collision.get_rid())
	return excludes


func _resolve_target_focus() -> Vector3:
	if not _target:
		return _focus_position
	var basis := _target.global_transform.basis
	var base := _target.global_position
	base.y += target_height + _focus_height_offset
	if side_offset != 0.0:
		base += basis.x.normalized() * side_offset
	return base


func _align_orbit_from_current_position() -> void:
	var focus := _resolve_target_focus()
	var raw_offset := global_position - focus
	var offset_len := raw_offset.length()
	if offset_len < 0.0001:
		return
	_distance = clampf(offset_len, min_distance, max_distance)
	var pitch_rad := atan2(raw_offset.y, Vector2(raw_offset.x, raw_offset.z).length())
	var yaw_rad := atan2(-raw_offset.x, -raw_offset.z)
	_pitch_deg = rad_to_deg(pitch_rad)
	_yaw_deg = rad_to_deg(yaw_rad)
	_pitch_deg = clampf(_pitch_deg, min_pitch_deg, max_pitch_deg)


func _input(event: InputEvent) -> void:
	if _keyboard_control_blocked():
		_clear_transient_input_state()
		return
	if event is InputEventMouseButton:
		_handle_mouse_button(event)
		return
	if event is InputEventMouseMotion:
		_handle_mouse_motion(event)
		return
	if event is InputEventKey and not event.echo:
		_handle_key_input(event)


func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_WINDOW_FOCUS_OUT or what == NOTIFICATION_APPLICATION_FOCUS_OUT:
		_clear_transient_input_state()


func _handle_mouse_button(event: InputEventMouseButton) -> void:
	if _is_player_view_active() and _is_pointer_over_gui_control():
		return
	if event.button_index == MOUSE_BUTTON_LEFT:
		_drag_left = event.pressed
	elif event.button_index == MOUSE_BUTTON_RIGHT:
		_drag_right = event.pressed
	elif event.button_index == MOUSE_BUTTON_WHEEL_UP and event.pressed:
		if _player_view_first_person:
			return
		_set_distance(-zoom_speed)
		get_viewport().set_input_as_handled()
	elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN and event.pressed:
		if _player_view_first_person:
			return
		_set_distance(zoom_speed)
		get_viewport().set_input_as_handled()

func _handle_mouse_motion(event: InputEventMouseMotion) -> void:
	if _is_player_view_active():
		_handle_player_view_mouse_motion(event)
		return
	if not (_drag_left or _drag_right):
		return
	var delta := event.relative
	if delta == Vector2.ZERO:
		return
	var wants_pan := _drag_right or (_drag_left and Input.is_key_pressed(KEY_SHIFT))
	if wants_pan:
		_pan_camera(delta)
	else:
		_orbit_camera(delta)
	get_viewport().set_input_as_handled()


func _handle_player_view_mouse_motion(event: InputEventMouseMotion) -> void:
	if not player_mouse_look_enabled:
		return
	if not _drag_right and player_mouse_button == MOUSE_BUTTON_RIGHT:
		return
	if not _drag_left and player_mouse_button == MOUSE_BUTTON_LEFT:
		return
	var delta := event.relative
	if delta == Vector2.ZERO:
		return
	_apply_player_yaw_delta(-delta.x * player_mouse_yaw_speed)
	if _player_view_first_person:
		_player_view_pitch_deg = clampf(
			_player_view_pitch_deg - delta.y * player_mouse_pitch_speed,
			first_person_min_pitch_deg,
			first_person_max_pitch_deg
		)
	elif _player_view_third_person:
		_pitch_deg = clampf(
			_pitch_deg - delta.y * player_mouse_pitch_speed,
			third_person_min_pitch_deg,
			third_person_max_pitch_deg
		)
		_sync_player_third_person_yaw()
		_focus_position = _resolve_target_focus()
		_snap_to_camera_state()
	get_viewport().set_input_as_handled()


func _apply_player_yaw_delta(delta_degrees: float) -> void:
	if not _target:
		return
	if _target.has_method("apply_view_yaw_delta"):
		_target.call("apply_view_yaw_delta", delta_degrees)
	else:
		_target.rotation.y += deg_to_rad(delta_degrees)


func _orbit_camera(delta: Vector2) -> void:
	_yaw_relative_to_target = false
	_yaw_deg -= delta.x * orbit_speed
	_pitch_deg += -delta.y * orbit_speed
	_pitch_deg = clampf(_pitch_deg, min_pitch_deg, max_pitch_deg)
	_snap_to_camera_state()


func _pan_camera(delta: Vector2) -> void:
	var right := global_transform.basis.x.normalized()
	var scale: float = max(0.2, _distance * pan_speed)
	var pan: Vector3 = (-delta.x * right + delta.y * Vector3.UP) * scale
	_focus_position += pan
	_snap_to_camera_state()


func _set_distance(amount: float) -> void:
	_distance = clampf(_distance + amount, min_distance, max_distance)
	_snap_to_camera_state()


func _focus_camera() -> bool:
	if not _target:
		return false
	_yaw_relative_to_target = false
	_clear_player_view_mode()
	_follow_mode = true
	_focus_position = _resolve_target_focus()
	_align_orbit_from_current_position()
	_snap_to_camera_state()
	return true


func _set_follow_mode(enabled: bool) -> bool:
	if _player_view_first_person or _player_view_third_person:
		_clear_player_view_mode()
	if _follow_mode == enabled:
		return false
	_follow_mode = enabled
	if _follow_mode and _target:
		_focus_position = _resolve_target_focus()
	_snap_to_camera_state()
	return true


func _handle_key_input(event: InputEventKey) -> void:
	var pressed := event.pressed
	match event.keycode:
		KEY_W:
			_move_forward = pressed if not _is_player_view_active() else false
		KEY_S:
			_move_backward = pressed if not _is_player_view_active() else false
		KEY_A:
			_move_left = pressed if not _is_player_view_active() else false
		KEY_D:
			_move_right = pressed if not _is_player_view_active() else false
		KEY_Q:
			_move_down = pressed if not _is_player_view_active() else false
		KEY_E:
			_move_up = pressed if not _is_player_view_active() else false
		KEY_F:
			if pressed:
				_focus_camera()
		KEY_R:
			if pressed:
				if _is_player_view_active():
					_set_player_view_first_person(true)
				else:
					_reset_camera()
		KEY_G:
			if pressed:
				_set_follow_mode(not _follow_mode)
		KEY_V, KEY_C:
			if pressed:
				_set_player_view_first_person(not _player_view_first_person)
		KEY_1:
			if pressed:
				_apply_preset(1)
		KEY_2:
			if pressed:
				_apply_preset(2)
		KEY_3:
			if pressed:
				_apply_preset(3)
		KEY_4:
			if pressed:
				_apply_preset(4)
		KEY_5:
			if pressed:
				_apply_preset(5)
		KEY_6:
			if pressed:
				_apply_preset(6)
		KEY_7:
			if pressed:
				_apply_preset(7)
		KEY_8:
			if pressed:
				_apply_preset(8)


func _is_player_view_active() -> bool:
	return _player_view_first_person or _player_view_third_person


func _clear_transient_input_state() -> void:
	_drag_left = false
	_drag_right = false
	_move_forward = false
	_move_backward = false
	_move_left = false
	_move_right = false
	_move_up = false
	_move_down = false


func _keyboard_control_blocked() -> bool:
	var viewport := get_viewport()
	if viewport == null:
		return false
	return _is_text_entry_control(viewport.gui_get_focus_owner())


func _is_pointer_over_gui_control() -> bool:
	var viewport := get_viewport()
	if viewport == null:
		return false
	return viewport.gui_get_hovered_control() != null


func _is_text_entry_control(control: Control) -> bool:
	if control == null:
		return false
	return control is LineEdit or control is TextEdit or control is SpinBox



func _reset_camera() -> bool:
	if not _target:
		return false
	_yaw_relative_to_target = false
	_focus_height_offset = _default_focus_height_offset
	_distance = _default_distance
	_pitch_deg = _default_pitch
	_yaw_deg = _default_yaw
	_clear_player_view_mode()
	_set_follow_mode(true)
	_focus_position = _resolve_target_focus()
	_snap_to_camera_state()
	return true


func _apply_preset(index: int) -> bool:
	if not _PRESET_CAMERA.has(index):
		return false
	var preset: Dictionary = _PRESET_CAMERA[index]
	var label := String(preset.get("label", "preset_%d" % index))
	if label == "user_pov":
		return _set_player_view_first_person(true)
	if label == "player_third_person":
		return _set_player_view_first_person(false)
	if label in ["full_front", "upper_front", "side", "diag_top", "autonomy_audience"]:
		_set_active_target("avatar")
	else:
		_set_active_target("player")
	_clear_player_view_mode()
	_set_follow_mode(true)
	_distance = clampf(float(preset.get("distance", _distance)), min_distance, max_distance)
	_pitch_deg = clampf(float(preset.get("pitch", _pitch_deg)), min_pitch_deg, max_pitch_deg)
	_focus_height_offset = float(preset.get("height", target_height + _focus_height_offset)) - target_height
	_target_relative_yaw_deg = float(preset.get("yaw", _yaw_deg))
	_yaw_relative_to_target = _target_role == "avatar"
	_yaw_deg = _target_relative_yaw_deg
	_update_target_relative_yaw()
	if preset.has("side_offset"):
		side_offset = float(preset.get("side_offset", side_offset))
	if preset.has("target_height"):
		target_height = float(preset.get("target_height", target_height))
	if preset.has("front_bias"):
		front_bias = float(preset.get("front_bias", front_bias))
	if preset.has("fov"):
		fov = clampf(float(preset.get("fov", fov)), 24.0, 85.0)
	_current_preset_label = label
	_focus_position = _resolve_target_focus()
	_snap_to_camera_state()
	return true


func _set_camera_state(params: Dictionary) -> bool:
	var changed := false
	if params.has("third_person_yaw_offset") and params.size() == 1:
		third_person_yaw_offset_deg = float(params.get("third_person_yaw_offset"))
		if _player_view_third_person and _target:
			_sync_player_third_person_yaw()
			_focus_position = _resolve_target_focus()
		_snap_to_camera_state()
		return true
	if params.has("mode"):
		var requested_mode := str(params.get("mode", "")).strip_edges().to_lower()
		if requested_mode in ["first_person", "first-person", "user_pov", "user"]:
			return _set_player_view_first_person(true)
		if requested_mode in ["third_person", "third-person", "player_third_person", "shoulder"]:
			var handled := _set_player_view_first_person(false)
			if params.has("third_person_yaw_offset"):
				third_person_yaw_offset_deg = float(params.get("third_person_yaw_offset"))
				if _target:
					_sync_player_third_person_yaw()
					_focus_position = _resolve_target_focus()
				_snap_to_camera_state()
			return handled
	if params.has("target") or params.has("target_role"):
		var requested_target := str(params.get("target", params.get("target_role", "player"))).strip_edges().to_lower()
		_set_active_target(requested_target)
		changed = true
	_clear_player_view_mode()
	if params.has("distance"):
		_distance = clampf(float(params.get("distance", _distance)), min_distance, max_distance)
		changed = true
		_current_preset_label = "custom"
	if params.has("pitch"):
		_pitch_deg = clampf(float(params.get("pitch")), min_pitch_deg, max_pitch_deg)
		changed = true
		_current_preset_label = "custom"
	if params.has("yaw"):
		_yaw_relative_to_target = bool(params.get("relative_to_target", false))
		_yaw_deg = float(params.get("yaw"))
		_target_relative_yaw_deg = _yaw_deg
		changed = true
		_current_preset_label = "custom"
	if params.has("height"):
		_focus_height_offset = float(params.get("height")) - target_height
		changed = true
	if params.has("target_height"):
		target_height = float(params.get("target_height"))
		changed = true
	if params.has("side_offset"):
		side_offset = float(params.get("side_offset"))
		changed = true
	if params.has("front_bias"):
		front_bias = float(params.get("front_bias"))
		changed = true
	if params.has("third_person_yaw_offset"):
		third_person_yaw_offset_deg = float(params.get("third_person_yaw_offset"))
		changed = true
	if params.has("fov"):
		fov = clampf(float(params.get("fov")), 24.0, 85.0)
		changed = true
	if params.has("mode"):
		var mode := str(params.get("mode", "")).strip_edges().to_lower()
		if mode in ["follow", "tracking", "locked"]:
			_set_follow_mode(true)
		elif mode in ["free", "manual"]:
			_set_follow_mode(false)
		elif mode in ["autonomy", "autonomous", "life_mode", "autonomy_view", "audience_mode"]:
			_apply_preset(7)
		changed = true
		_current_preset_label = "autonomy_audience" if mode in ["autonomy", "autonomous", "life_mode", "autonomy_view", "audience_mode"] else _current_preset_label
	if params.has("follow"):
		_set_follow_mode(bool(params.get("follow", _follow_mode)))
		changed = true
	if params.has("focus"):
		_focus_position = _dict_to_vector3(params.get("focus", _focus_position))
		_set_follow_mode(false)
		changed = true
	if params.has("x") or params.has("y") or params.has("z"):
		var focus := _focus_position
		focus.x = float(params.get("x", focus.x))
		focus.y = float(params.get("y", focus.y))
		focus.z = float(params.get("z", focus.z))
		_focus_position = focus
		_set_follow_mode(false)
		changed = true
	if params.has("yaw_offset"):
		_yaw_relative_to_target = false
		_yaw_deg += float(params.get("yaw_offset", 0.0))
		changed = true
	if params.has("pitch_offset"):
		_pitch_deg += float(params.get("pitch_offset", 0.0))
		_pitch_deg = clampf(_pitch_deg, min_pitch_deg, max_pitch_deg)
		changed = true
	if params.has("distance_offset"):
		_set_distance(float(params.get("distance_offset", 0.0)))
		changed = true
	if params.has("autonomy_mode"):
		var autonomy_mode := bool(params.get("autonomy_mode"))
		if autonomy_mode:
			_apply_preset(7)
			changed = true
	if changed:
		if _follow_mode and _target:
			if _player_view_third_person:
				_sync_player_third_person_yaw()
			_focus_position = _resolve_target_focus()
		_snap_to_camera_state()
	return changed


func _dict_to_vector3(value: Variant) -> Vector3:
	if typeof(value) == TYPE_DICTIONARY:
		var dict := value as Dictionary
		return Vector3(float(dict.get("x", 0.0)), float(dict.get("y", 0.0)), float(dict.get("z", 0.0)))
	if value is Vector3:
		return value
	return _focus_position


func apply_si_command(command: Dictionary) -> bool:
	var action := String(command.get("action", ""))
	var params: Dictionary = command.get("params", {})
	if typeof(params) != TYPE_DICTIONARY:
		params = {}
	var handled := false
	match action:
		"camera_preset":
			if params.has("index"):
				handled = _apply_preset(int(params.get("index", 1)))
			elif params.has("preset"):
				var preset_value: Variant = params.get("preset", 1)
				if typeof(preset_value) == TYPE_STRING:
					handled = _apply_preset_by_label(String(preset_value))
				else:
					handled = _apply_preset(int(preset_value))
			elif params.has("name"):
				handled = _apply_preset_by_label(String(params.get("name", "")))
			else:
				handled = false
		"camera_toggle_follow":
			if params.has("follow"):
				handled = _set_follow_mode(bool(params.get("follow")))
			elif params.has("mode"):
				var mode := str(params.get("mode", "")).strip_edges().to_lower()
				if mode in ["follow", "tracking", "locked"]:
					handled = _set_follow_mode(true)
				else:
					handled = _set_follow_mode(false)
			else:
				handled = _set_follow_mode(not _follow_mode)
		"camera_focus":
			handled = _focus_camera()
		"camera_reset":
			handled = _reset_camera()
		"camera_set":
			handled = _set_camera_state(params)
		
		_: 
			handled = false
	return handled


func get_camera_state() -> Dictionary:
	var view_mode := "stage_camera"
	if _player_view_first_person:
		view_mode = "first_person"
	elif _player_view_third_person:
		view_mode = "third_person"
	var target_forward := _target_forward()
	return {
		"follow_mode": _follow_mode,
		"view_mode": view_mode,
		"distance": _distance,
		"preset_label": _current_preset_label,
		"pitch": _pitch_deg,
		"player_view_pitch": _player_view_pitch_deg,
		"yaw": _yaw_deg,
		"third_person_yaw_offset": third_person_yaw_offset_deg,
		"mouse_look_enabled": player_mouse_look_enabled,
		"distance_limits": {
			"min": min_distance,
			"max": max_distance,
		},
		"height": target_height + _focus_height_offset,
		"fov": fov,
		"projection": projection,
		"focus": {
			"x": _focus_position.x,
			"y": _focus_position.y,
			"z": _focus_position.z,
		},
		"position": {
			"x": global_position.x,
			"y": global_position.y,
			"z": global_position.z,
		},
		"current": is_current(),
		"target": {
			"role": _target_role,
			"path": String(target_path),
			"avatar_path": String(avatar_target_path),
			"found": _target != null,
			"forward": {
				"x": target_forward.x,
				"y": target_forward.y,
				"z": target_forward.z,
			},
		},
		"front_bias": front_bias,
		"side_offset": side_offset,
	}


func _apply_preset_by_label(label: String) -> bool:
	var normalized := label.strip_edges().to_lower().replace("-", "_").replace(" ", "_")
	if not _PRESET_LABEL_TO_INDEX.has(normalized):
		return false
	return _apply_preset(int(_PRESET_LABEL_TO_INDEX[normalized]))


func _set_player_view_first_person(enabled: bool) -> bool:
	_clear_transient_input_state()
	_set_active_target("player")
	_yaw_relative_to_target = false
	_player_view_first_person = enabled
	_player_view_third_person = not enabled
	_follow_mode = true
	_notify_target_first_person_view(enabled)
	if enabled:
		_current_preset_label = "user_pov"
		_player_view_pitch_deg = 0.0
		side_offset = 0.0
		fov = clampf(first_person_fov_deg, 24.0, 85.0)
		if _target:
			_apply_first_person_camera(1.0)
		return true

	_current_preset_label = "player_third_person"
	_distance = clampf(third_person_distance, min_distance, max_distance)
	_pitch_deg = clampf(third_person_pitch_deg, third_person_min_pitch_deg, third_person_max_pitch_deg)
	_focus_height_offset = third_person_height - target_height
	side_offset = third_person_side_offset
	fov = clampf(60.0, 24.0, 85.0)
	if _target:
		_sync_player_third_person_yaw()
		_focus_position = _resolve_target_focus()
	_snap_to_camera_state()
	return true


func _clear_player_view_mode() -> void:
	_player_view_first_person = false
	_player_view_third_person = false
	_notify_target_first_person_view(false)


func _sync_player_third_person_yaw() -> void:
	if not _target:
		return
	_yaw_relative_to_target = false
	var camera_behind_player := -_target_forward()
	_yaw_deg = _yaw_from_orbit_offset(camera_behind_player) + third_person_yaw_offset_deg


func _update_target_relative_yaw() -> void:
	if not _yaw_relative_to_target or not _target:
		return
	_yaw_deg = _yaw_from_orbit_offset(_target_forward()) + _target_relative_yaw_deg


func _notify_target_first_person_view(enabled: bool) -> void:
	if _target and _target.has_method("set_first_person_view"):
		_target.call("set_first_person_view", enabled)
