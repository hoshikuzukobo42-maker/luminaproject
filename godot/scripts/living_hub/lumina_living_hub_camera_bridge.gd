extends Node

## Fail-closed SI camera adapter for the Living Hub.
##
## The Living Hub owns a plain first-person Camera3D, while the generic SI
## receiver expects a controller with apply_si_command/get_camera_state.  This
## adapter preserves that first-person camera for user_pov and owns a separate
## spectator Camera3D for deterministic avatar-framing presets.

const USER_POV_INDEX := 5
const MIN_DISTANCE := 0.75
const MAX_DISTANCE := 14.0

const PRESET_CAMERA: Dictionary = {
	1: {"label": "full_front", "distance": 4.0, "pitch": 10.0, "height": 1.20, "yaw": 0.0, "side_offset": 0.0, "fov": 58.0, "target": "avatar"},
	2: {"label": "upper_front", "distance": 2.0, "pitch": 10.0, "height": 1.42, "yaw": 0.0, "side_offset": 0.0, "fov": 40.0, "target": "avatar"},
	3: {"label": "side", "distance": 4.4, "pitch": 14.0, "height": 1.22, "yaw": 90.0, "side_offset": 0.0, "fov": 68.0, "target": "avatar"},
	4: {"label": "diag_top", "distance": 2.2, "pitch": 48.0, "height": 1.75, "yaw": 145.0, "side_offset": 0.0, "fov": 48.0, "target": "avatar"},
	5: {"label": "user_pov", "distance": 0.0, "pitch": 0.0, "height": 1.60, "yaw": 0.0, "side_offset": 0.0, "fov": 68.0, "target": "player"},
	6: {"label": "user_over_shoulder", "distance": 4.35, "pitch": 14.0, "height": 1.55, "yaw": 106.0, "side_offset": -0.28, "fov": 58.0, "target": "player"},
	7: {"label": "autonomy_audience", "distance": 4.15, "pitch": 12.0, "height": 1.34, "yaw": 0.0, "side_offset": 0.0, "fov": 56.0, "target": "avatar"},
	8: {"label": "player_third_person", "distance": 4.1, "pitch": 12.0, "height": 1.55, "yaw": 180.0, "side_offset": 0.0, "fov": 62.0, "target": "player"},
}

const PRESET_LABEL_TO_INDEX: Dictionary = {
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
	"autonomy_audience": 7,
	"autonomy": 7,
	"autonomous": 7,
	"autonomy_view": 7,
	"life_mode": 7,
	"player_third_person": 8,
	"third_person": 8,
	"third": 8,
}

var _first_person_camera: Camera3D
var _player: Node3D
var _avatar: Node3D
var _spectator_camera: Camera3D
var _preset_index := USER_POV_INDEX
var _preset_label := "user_pov"
var _focus_position := Vector3.ZERO
var _follow_enabled := true


func setup(first_person_camera: Camera3D, player: Node3D, avatar: Node3D) -> bool:
	if first_person_camera == null or player == null or avatar == null:
		return false
	_first_person_camera = first_person_camera
	_player = player
	_avatar = avatar
	_spectator_camera = Camera3D.new()
	_spectator_camera.name = "LivingHubSICamera"
	_spectator_camera.near = maxf(0.05, first_person_camera.near)
	_spectator_camera.far = first_person_camera.far
	_spectator_camera.current = false
	add_child(_spectator_camera)
	_first_person_camera.make_current()
	_focus_position = _first_person_focus()
	set_process(true)
	return true


func _process(_delta: float) -> void:
	if _preset_index == USER_POV_INDEX or not _follow_enabled:
		return
	_apply_stage_transform(_preset_index, false)


func apply_si_command(command: Dictionary) -> bool:
	var action := String(command.get("action", ""))
	var raw_params: Variant = command.get("params", {})
	var params: Dictionary = raw_params if raw_params is Dictionary else {}
	match action:
		"camera_preset":
			return _apply_preset_command(params)
		"camera_reset":
			return _apply_preset(USER_POV_INDEX)
		"camera_focus":
			if _preset_index == USER_POV_INDEX:
				return false
			return _apply_stage_transform(_preset_index, true)
		"camera_toggle_follow":
			return _apply_follow_command(params)
		_:
			return false


func get_supported_camera_actions() -> Array[String]:
	return ["camera_preset", "camera_reset", "camera_focus", "camera_toggle_follow"]


func get_camera_state() -> Dictionary:
	var active_camera := _first_person_camera if _preset_index == USER_POV_INDEX else _spectator_camera
	var position := Vector3.ZERO
	var camera_fov := 0.0
	var projection_value := Camera3D.PROJECTION_PERSPECTIVE
	if active_camera != null and is_instance_valid(active_camera):
		position = active_camera.global_position
		camera_fov = active_camera.fov
		projection_value = active_camera.projection
	return {
		"controller": "living_hub_camera_bridge",
		"preset_index": _preset_index,
		"preset_label": _preset_label,
		"view_mode": "first_person" if _preset_index == USER_POV_INDEX else "stage_camera",
		"follow_mode": _follow_enabled,
		"fov": camera_fov,
		"projection": projection_value,
		"focus": _vector3_dict(_focus_position),
		"position": _vector3_dict(position),
	}


func _apply_preset_command(params: Dictionary) -> bool:
	if params.has("index"):
		var raw_index: Variant = params.get("index")
		if typeof(raw_index) != TYPE_INT and typeof(raw_index) != TYPE_FLOAT:
			return false
		var numeric_index := int(raw_index)
		if float(numeric_index) != float(raw_index):
			return false
		return _apply_preset(numeric_index)

	var raw_label := ""
	if params.has("preset"):
		raw_label = String(params.get("preset", ""))
	elif params.has("name"):
		raw_label = String(params.get("name", ""))
	var label := raw_label.strip_edges().to_lower().replace("-", "_").replace(" ", "_")
	if label.is_empty() or not PRESET_LABEL_TO_INDEX.has(label):
		return false
	return _apply_preset(int(PRESET_LABEL_TO_INDEX[label]))


func _apply_preset(index: int) -> bool:
	if not PRESET_CAMERA.has(index):
		return false
	if index == USER_POV_INDEX:
		if _first_person_camera == null or not is_instance_valid(_first_person_camera):
			return false
		_preset_index = index
		_preset_label = String((PRESET_CAMERA[index] as Dictionary).get("label", "user_pov"))
		_follow_enabled = true
		_first_person_camera.make_current()
		_focus_position = _first_person_focus()
		return true
	return _apply_stage_transform(index, true)


func _apply_stage_transform(index: int, make_current: bool) -> bool:
	if not PRESET_CAMERA.has(index):
		return false
	if _spectator_camera == null or not is_instance_valid(_spectator_camera):
		return false
	var preset: Dictionary = PRESET_CAMERA[index]
	var target := _target_for_preset(preset)
	if target == null or not is_instance_valid(target):
		return false

	var distance := clampf(float(preset.get("distance", 3.2)), MIN_DISTANCE, MAX_DISTANCE)
	var pitch_rad := deg_to_rad(clampf(float(preset.get("pitch", 10.0)), -60.0, 78.0))
	var yaw_rad := deg_to_rad(float(preset.get("yaw", 0.0)))
	var target_right := target.global_transform.basis.x
	target_right.y = 0.0
	if target_right.length_squared() < 0.0001:
		target_right = Vector3.RIGHT
	else:
		target_right = target_right.normalized()
	_focus_position = target.global_position + Vector3.UP * float(preset.get("height", 1.25))
	_focus_position += target_right * float(preset.get("side_offset", 0.0))

	var horizontal_direction := _target_forward(target).rotated(Vector3.UP, yaw_rad)
	var desired_position := _focus_position
	desired_position += horizontal_direction * (cos(pitch_rad) * distance)
	desired_position += Vector3.UP * (sin(pitch_rad) * distance)
	_spectator_camera.global_position = desired_position
	_spectator_camera.fov = clampf(float(preset.get("fov", 58.0)), 24.0, 85.0)
	_spectator_camera.look_at(_focus_position, Vector3.UP)
	if make_current:
		_spectator_camera.make_current()
		_preset_index = index
		_preset_label = String(preset.get("label", "preset_%d" % index))
		_follow_enabled = true
	return true


func _apply_follow_command(params: Dictionary) -> bool:
	if _preset_index == USER_POV_INDEX:
		return false
	var next_follow := not _follow_enabled
	if params.has("follow"):
		if typeof(params.get("follow")) != TYPE_BOOL:
			return false
		next_follow = bool(params.get("follow"))
	elif params.has("mode"):
		var mode := String(params.get("mode", "")).strip_edges().to_lower()
		if mode in ["follow", "tracking", "locked"]:
			next_follow = true
		elif mode in ["free", "manual"]:
			next_follow = false
		else:
			return false
	_follow_enabled = next_follow
	if _follow_enabled:
		return _apply_stage_transform(_preset_index, true)
	return true


func _target_for_preset(preset: Dictionary) -> Node3D:
	return _player if String(preset.get("target", "avatar")) == "player" else _avatar


func _target_forward(target: Node3D) -> Vector3:
	var forward := -target.global_transform.basis.z
	forward.y = 0.0
	if forward.length_squared() < 0.0001:
		return Vector3.FORWARD
	return forward.normalized()


func _first_person_focus() -> Vector3:
	if _first_person_camera == null or not is_instance_valid(_first_person_camera):
		return Vector3.ZERO
	var forward := -_first_person_camera.global_transform.basis.z
	if forward.length_squared() < 0.0001:
		forward = Vector3.FORWARD
	return _first_person_camera.global_position + forward.normalized() * 8.0


func _vector3_dict(value: Vector3) -> Dictionary:
	return {"x": value.x, "y": value.y, "z": value.z}
