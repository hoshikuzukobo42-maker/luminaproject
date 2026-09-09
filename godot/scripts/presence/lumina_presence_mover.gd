extends RefCounted
class_name LuminaPresenceMover

## Lightweight locomotion for presence. Moves a Node3D / CharacterBody3D
## toward a target without depending on NavigationServer (unit-test friendly).

signal arrived(target_name: String)
signal move_failed(reason: String)

const MAX_EXACT_FINISH_DISTANCE := 0.12

var walk_speed := 1.35
var lead_distance := 1.6
var follow_distance := 1.1
var arrive_epsilon := 0.12
var rotation_speed := 9.0
var look_back_interval := 1.8

var _avatar: Node3D
var _body: CharacterBody3D
var _active := false
var _target := Vector3.ZERO
var _target_name := ""
var _target_yaw := NAN
var _mode := "goto" # goto | follow | lead
var _player: Node3D
var _look_back_timer := 0.0
var _looking_back := false
var _active_walk_speed := 1.35
var _active_arrive_epsilon := 0.12
var _exact_finish_requested := false
var _exact_finish_applied := false
var _exact_finish_blocked := false
var _exact_finish_distance := 0.0


func bind_avatar(avatar: Node3D) -> bool:
	_avatar = avatar
	_body = avatar as CharacterBody3D
	return _avatar != null


func set_player(player: Node3D) -> void:
	_player = player


func is_active() -> bool:
	return _active


func get_position() -> Vector3:
	if _avatar == null or not is_instance_valid(_avatar):
		return Vector3.ZERO
	return _avatar.global_position


func cancel() -> void:
	_active = false
	_target_name = ""
	_looking_back = false
	if _body != null:
		_body.velocity = Vector3.ZERO


func goto(target: Vector3, target_name := "", yaw := NAN, params: Dictionary = {}) -> bool:
	if _avatar == null:
		move_failed.emit("no_avatar")
		return false
	_mode = "goto"
	_target = target
	_target_name = target_name
	_target_yaw = yaw
	_active_walk_speed = clampf(float(params.get("walk_speed", walk_speed)), 0.4, 2.5)
	_active_arrive_epsilon = clampf(
		float(params.get("stop_distance", params.get("arrive_epsilon", arrive_epsilon))),
		0.05,
		3.0
	)
	_exact_finish_requested = bool(params.get("exact_finish", false))
	_exact_finish_applied = false
	_exact_finish_blocked = false
	_exact_finish_distance = 0.0
	_active = true
	_looking_back = false
	return true


func start_follow() -> bool:
	if _avatar == null or _player == null:
		move_failed.emit("follow_requires_player")
		return false
	_mode = "follow"
	_target_name = "PLAYER_FOLLOW"
	_active = true
	_looking_back = false
	return true


func start_lead() -> bool:
	if _avatar == null or _player == null:
		move_failed.emit("lead_requires_player")
		return false
	_mode = "lead"
	_target_name = "PLAYER_LEAD"
	_active = true
	_look_back_timer = look_back_interval
	_looking_back = false
	return true


func advance(delta: float) -> void:
	if not _active or _avatar == null or not is_instance_valid(_avatar):
		return
	match _mode:
		"follow":
			_advance_follow(delta)
		"lead":
			_advance_lead(delta)
		_:
			_advance_goto(delta)


func _advance_goto(delta: float) -> void:
	var pos := _avatar.global_position
	var flat_target := Vector3(_target.x, pos.y, _target.z)
	var offset := flat_target - pos
	offset.y = 0.0
	var distance := offset.length()
	if distance <= _active_arrive_epsilon:
		_finish_arrive()
		return
	_apply_motion(offset.normalized(), delta)
	if not is_nan(_target_yaw) and distance < _active_arrive_epsilon * 3.0:
		_avatar.rotation.y = lerp_angle(
			_avatar.rotation.y,
			_target_yaw,
			clampf(delta * rotation_speed, 0.0, 1.0)
		)


func _advance_follow(delta: float) -> void:
	if _player == null or not is_instance_valid(_player):
		move_failed.emit("player_missing")
		cancel()
		return
	var pos := _avatar.global_position
	var player_pos := _player.global_position
	var offset := player_pos - pos
	offset.y = 0.0
	var distance := offset.length()
	if distance <= follow_distance:
		if _body != null:
			_body.velocity = Vector3.ZERO
		_face_toward(player_pos, delta)
		return
	_apply_motion(offset.normalized(), delta)


func _advance_lead(delta: float) -> void:
	if _player == null or not is_instance_valid(_player):
		move_failed.emit("player_missing")
		cancel()
		return
	var pos := _avatar.global_position
	var player_pos := _player.global_position
	var forward := -_player.global_transform.basis.z
	forward.y = 0.0
	if forward.length() < 0.001:
		forward = Vector3(0, 0, -1)
	else:
		forward = forward.normalized()
	var lead_point := player_pos + forward * lead_distance
	var offset := lead_point - pos
	offset.y = 0.0
	_look_back_timer -= delta
	if _look_back_timer <= 0.0:
		_looking_back = not _looking_back
		_look_back_timer = look_back_interval * (0.45 if _looking_back else 1.0)
	if _looking_back:
		if _body != null:
			_body.velocity = Vector3.ZERO
		_face_toward(player_pos, delta)
		return
	if offset.length() > arrive_epsilon:
		_apply_motion(offset.normalized(), delta)
	else:
		if _body != null:
			_body.velocity = Vector3.ZERO
		_face_toward(lead_point + forward, delta)


func _apply_motion(direction: Vector3, delta: float) -> void:
	if direction.length() < 0.001:
		return
	var desired_yaw := atan2(direction.x, direction.z)
	_avatar.rotation.y = lerp_angle(
		_avatar.rotation.y,
		desired_yaw,
		clampf(delta * rotation_speed, 0.0, 1.0)
	)
	# Prefer physics when avatar is a CharacterBody3D so companions don't clip walls.
	if _body != null:
		var effective_speed := _active_walk_speed if _mode == "goto" else walk_speed
		_body.velocity = direction.normalized() * effective_speed
		_body.velocity.y = -0.5
		_body.move_and_slide()
		return
	var effective_speed := _active_walk_speed if _mode == "goto" else walk_speed
	var step := direction * effective_speed * delta
	_avatar.global_position = _avatar.global_position + step


func _face_toward(world_point: Vector3, delta: float) -> void:
	var offset := world_point - _avatar.global_position
	offset.y = 0.0
	if offset.length() < 0.001:
		return
	var desired_yaw := atan2(offset.x, offset.z)
	_avatar.rotation.y = lerp_angle(
		_avatar.rotation.y,
		desired_yaw,
		clampf(delta * rotation_speed, 0.0, 1.0)
	)


func _finish_arrive() -> void:
	var name := _target_name
	_active = false
	_try_collision_safe_exact_finish()
	if _body != null:
		_body.velocity = Vector3.ZERO
	if not is_nan(_target_yaw):
		_avatar.rotation.y = _target_yaw
	arrived.emit(name)


func _try_collision_safe_exact_finish() -> void:
	if not _exact_finish_requested or _body == null:
		return
	var remainder := Vector3(
		_target.x - _body.global_position.x,
		0.0,
		_target.z - _body.global_position.z
	)
	_exact_finish_distance = remainder.length()
	var allowed_distance := minf(_active_arrive_epsilon, MAX_EXACT_FINISH_DISTANCE)
	if _exact_finish_distance > allowed_distance + 0.0001:
		return
	if _exact_finish_distance <= 0.0001:
		_exact_finish_applied = true
		return
	# Query the full remainder first, then use CharacterBody collision motion only.
	# If either query or move is blocked, the approached position is already valid.
	if _body.move_and_collide(remainder, true) != null:
		_exact_finish_blocked = true
		return
	if _body.move_and_collide(remainder) == null:
		_exact_finish_applied = true
	else:
		_exact_finish_blocked = true


func teleport(position: Vector3, yaw := NAN) -> void:
	if _avatar == null:
		return
	_avatar.global_position = position
	if not is_nan(yaw):
		_avatar.rotation.y = yaw
	if _body != null:
		_body.velocity = Vector3.ZERO


func get_status() -> Dictionary:
	return {
		"active": _active,
		"mode": _mode,
		"target_name": _target_name,
		"looking_back": _looking_back,
		"active_walk_speed": _active_walk_speed,
		"active_arrive_epsilon": _active_arrive_epsilon,
		"exact_finish_requested": _exact_finish_requested,
		"exact_finish_applied": _exact_finish_applied,
		"exact_finish_blocked": _exact_finish_blocked,
		"exact_finish_distance": _exact_finish_distance,
		"position": get_position(),
	}
