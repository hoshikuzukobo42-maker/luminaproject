extends "res://scripts/si_demo_avatar_controller.gd"
class_name LuminaLivingHubMotionController

## SI command facade for Living Hub. Presence is the only proxy-transform
## writer; this controller translates legacy commands and drives only the
## visible adapter/status on the mirrored bridge avatar.

const ANCHOR_LOBBY_GREETING := "ANCHOR_LOBBY_GREETING"
const ANCHOR_BENCH_LUMINA := "ANCHOR_BENCH_LUMINA"
const ROOM_CONVERSATION_ANCHOR := "WRV1_Anchor_conversation"
const MAX_LEGACY_OFFSET_METERS := 3.0
const PRESENCE_MOVING_EPSILON := 0.035

var _presence: Node
var _presence_proxy: CharacterBody3D
var _greeting_frame := Transform3D.IDENTITY
var _active_presence_token := ""
var _last_presence_position := Vector3.ZERO
var _have_presence_position := false
var _accepted_token_count := 0
var _terminal_token_count := 0
var _stale_terminal_count := 0


func bind_living_hub(presence: Node, proxy: CharacterBody3D, greeting_anchor: Node3D) -> bool:
	_presence = presence
	_presence_proxy = proxy
	if _presence == null or _presence_proxy == null or greeting_anchor == null:
		return false
	if String(greeting_anchor.name) != ANCHOR_LOBBY_GREETING:
		return false
	if not _presence.has_signal("si_motion_finished"):
		return false
	var terminal_callable := Callable(self, "_on_presence_si_motion_finished")
	if not _presence.is_connected("si_motion_finished", terminal_callable):
		_presence.connect("si_motion_finished", terminal_callable)
	if _presence.has_signal("sit_completed"):
		var sit_callable := Callable(self, "_on_presence_sit_completed")
		if not _presence.is_connected("sit_completed", sit_callable):
			_presence.connect("sit_completed", sit_callable)
	if _presence.has_signal("state_changed"):
		var state_callable := Callable(self, "_on_presence_state_changed")
		if not _presence.is_connected("state_changed", state_callable):
			_presence.connect("state_changed", state_callable)
	_greeting_frame = _immutable_yaw_frame(greeting_anchor)
	_last_presence_position = _presence_proxy.global_position
	_have_presence_position = true
	navigation_ready = true
	return true


func _physics_process(delta: float) -> void:
	# Never call the base locomotion loop: it owns a second move_and_slide path.
	_tick_temporary_posture()
	_tick_look_at(delta)


func apply_si_command(command: Dictionary) -> bool:
	var action := String(command.get("action", ""))
	var reason := String(command.get("reason", ""))
	if action == "set_motion_profile" and reason == "motion_qa_reset":
		if _vrm_adapter and _vrm_adapter.has_method("reset_motion_source_coverage"):
			_vrm_adapter.call("reset_motion_source_coverage")

	match action:
		"move_to_position":
			return _accept_presence_move(command)
		"move_to_node":
			return _accept_presence_anchor_move(command)
		"look_at_position":
			return _apply_living_hub_look(command)
		"sit":
			return _accept_presence_sit(command)
		"follow_user":
			# Natural Presence lead/follow has no attainable one-shot completion.
			return false
		"stand":
			return _apply_presence_stand(command)
		"stop":
			_cancel_active_presence("stopped by SI command")
			return super.apply_si_command(command)
		_:
			return super.apply_si_command(command)


func observe_presence_motion(position: Vector3, speed: float, moving: bool) -> void:
	var delta_position := position - _last_presence_position if _have_presence_position else Vector3.ZERO
	delta_position.y = 0.0
	var actual_moving := moving or speed > PRESENCE_MOVING_EPSILON
	velocity = delta_position.normalized() * speed if actual_moving and delta_position.length() > 0.0001 else Vector3.ZERO

	if actual_moving:
		if current_posture == "sit":
			_apply_visible_stand(true)
		if _vrm_adapter and _vrm_adapter.has_method("set_motion_state"):
			_vrm_adapter.call("set_motion_state", "Walk", speed)
		current_animation = "Walk"
		if not current_posture.begins_with("gesture"):
			current_posture = "walk"
		movement_mode = "direct"
	else:
		var presence_state := _presence_state()
		if presence_state != "SIT_TOGETHER" and not (
			current_posture == "sit"
			or current_posture == "stand"
			or current_posture.begins_with("gesture")
		):
			if _vrm_adapter and _vrm_adapter.has_method("set_motion_state"):
				_vrm_adapter.call("set_motion_state", "Idle", 0.0)
			current_animation = "Idle"
			if current_posture == "walk":
				current_posture = "idle"
			movement_mode = "idle"

	_last_presence_position = position
	_have_presence_position = true


func get_living_hub_motion_status() -> Dictionary:
	return {
		"presence_authority": true,
		"active_presence_token": _active_presence_token,
		"accepted_token_count": _accepted_token_count,
		"terminal_token_count": _terminal_token_count,
		"stale_terminal_count": _stale_terminal_count,
		"greeting_origin": _greeting_frame.origin,
		"presence": _presence.call("get_si_motion_status") if _presence and _presence.has_method("get_si_motion_status") else {},
	}


func _accept_presence_move(command: Dictionary) -> bool:
	if not _presence_ready("request_si_move"):
		return false
	var token := String(command.get("command_id", "")).strip_edges()
	if token.is_empty():
		return false
	var requested := _command_vector(command.get("position", {}))
	if not _vector_is_finite(requested):
		return false
	var coordinate_space := _coordinate_space(command)
	if coordinate_space.is_empty():
		return false
	var target := requested
	if coordinate_space != "world":
		requested.y = 0.0
		if requested.length() > MAX_LEGACY_OFFSET_METERS:
			return false
		target = _greeting_frame * requested
	target.y = _presence_proxy.global_position.y
	_cancel_active_presence("replaced by move_to_position")
	var accepted := bool(_presence.call("request_si_move", target, token, _command_params(command)))
	if not accepted:
		return false
	_begin_presence_async(token, "move_to_position", coordinate_space)
	return true


func _accept_presence_anchor_move(command: Dictionary) -> bool:
	if not _presence_ready("request_si_move_anchor"):
		return false
	var token := String(command.get("command_id", "")).strip_edges()
	if token.is_empty():
		return false
	var requested := String(command.get("target_node", "")).strip_edges()
	var anchor_name := _semantic_anchor(requested)
	if anchor_name.is_empty() or not _presence_can_resolve_anchor(anchor_name):
		return false
	_cancel_active_presence("replaced by move_to_node")
	var accepted := bool(_presence.call(
		"request_si_move_anchor",
		anchor_name,
		token,
		_command_params(command)
	))
	if not accepted:
		return false
	_begin_presence_async(token, "move_to_node", anchor_name)
	return true


func _accept_presence_sit(command: Dictionary) -> bool:
	if not _presence_ready("request_si_sit"):
		return false
	var token := String(command.get("command_id", "")).strip_edges()
	if token.is_empty():
		return false
	if not _presence_can_resolve_anchor(ANCHOR_BENCH_LUMINA):
		return false
	_cancel_active_presence("replaced by sit")
	var accepted := bool(_presence.call("request_si_sit", token, _command_params(command)))
	if not accepted:
		return false
	_begin_presence_async(token, "sit", ANCHOR_BENCH_LUMINA)
	current_goal = "sit_to_bench"
	return true


func _apply_presence_stand(command: Dictionary) -> bool:
	if not _presence_ready("request_si_stand"):
		return false
	_cancel_active_presence("interrupted by stand")
	var accepted := bool(_presence.call("request_si_stand", _command_params(command)))
	if not accepted:
		return false
	_apply_visible_stand(false)
	# Receiver treats stand as synchronous; do not leave an async command id.
	active_command_id = ""
	return true


func _begin_presence_async(token: String, action: String, target: String) -> void:
	_active_presence_token = token
	_accepted_token_count += 1
	active_command_id = token
	_begin_async_command(token, action)
	current_goal = target
	current_posture = "walk" if action != "sit" else "sit_approach"
	movement_mode = "direct"
	target_node_name = target


func _on_presence_si_motion_finished(token: String, ok: bool, detail: String) -> void:
	if token != _active_presence_token or token != active_command_id:
		_stale_terminal_count += 1
		return
	_active_presence_token = ""
	_terminal_token_count += 1
	if ok and _active_async_action == "sit":
		_apply_visible_sit()
	else:
		if _vrm_adapter and _vrm_adapter.has_method("set_motion_state"):
			_vrm_adapter.call("set_motion_state", "Idle", 0.0)
		current_posture = "idle"
		current_animation = "Idle"
		movement_mode = "idle"
		target_distance = 0.0
	_complete_async_command(ok, "completed" if ok else "failed", detail)


func _on_presence_sit_completed() -> void:
	# Covers both tokenized SI sit and natural Presence request_sit().
	_apply_visible_sit()


func _on_presence_state_changed(previous: String, current: String) -> void:
	if previous == "SIT_TOGETHER" and current != "SIT_TOGETHER":
		_apply_visible_stand(true)


func _apply_visible_sit() -> void:
	if _vrm_adapter and _vrm_adapter.has_method("set_sit_posture"):
		_vrm_adapter.call("set_sit_posture", -1.0)
	current_posture = "sit"
	current_animation = "Sit"
	movement_mode = "idle"
	target_node_name = ANCHOR_BENCH_LUMINA
	target_distance = 0.0


func _apply_visible_stand(immediate: bool) -> void:
	if _vrm_adapter:
		if immediate and _vrm_adapter.has_method("clear_posture_state"):
			_vrm_adapter.call("clear_posture_state", true)
		elif _vrm_adapter.has_method("set_stand_posture"):
			_vrm_adapter.call("set_stand_posture", 1.0)
	current_posture = "idle" if immediate else "stand"
	current_animation = "Idle" if immediate else "Stand"
	movement_mode = "idle"
	_temporary_posture_until = (
		-1.0
		if immediate
		else Time.get_ticks_msec() / 1000.0 + 1.2
	)


func _cancel_active_presence(detail: String) -> void:
	if _presence == null or not _presence.has_method("get_si_motion_status"):
		_terminalize_orphaned_presence(detail)
		return
	var status = _presence.call("get_si_motion_status")
	if typeof(status) == TYPE_DICTIONARY and bool(status.get("active", false)):
		var token := String(status.get("token", ""))
		if not token.is_empty():
			var cancelled := bool(_presence.call("cancel_si_motion", token, detail))
			if cancelled and _active_presence_token.is_empty():
				return
	_terminalize_orphaned_presence(detail)


func _terminalize_orphaned_presence(detail: String) -> void:
	if _active_presence_token.is_empty():
		return
	_active_presence_token = ""
	_terminal_token_count += 1
	if _vrm_adapter and _vrm_adapter.has_method("set_motion_state"):
		_vrm_adapter.call("set_motion_state", "Idle", 0.0)
	current_posture = "idle"
	current_animation = "Idle"
	movement_mode = "idle"
	_complete_async_command(false, "interrupted", "%s (Presence token unavailable)" % detail)


func _presence_ready(method_name: String) -> bool:
	return _presence != null and _presence_proxy != null and _presence.has_method(method_name)


func _immutable_yaw_frame(anchor: Node3D) -> Transform3D:
	return Transform3D(
		Basis.from_euler(Vector3(0.0, anchor.global_rotation.y, 0.0)),
		anchor.global_position
	)


func _semantic_anchor(target_name: String) -> String:
	var requested := target_name.strip_edges()
	var upper := requested.to_upper()
	if upper == ANCHOR_LOBBY_GREETING:
		return ANCHOR_LOBBY_GREETING
	if upper == ANCHOR_BENCH_LUMINA:
		return ANCHOR_BENCH_LUMINA
	if upper == ROOM_CONVERSATION_ANCHOR.to_upper():
		return ROOM_CONVERSATION_ANCHOR
	var normalized := requested.to_lower()
	if normalized in [
		"chair_a", "chair_b", "chair", "seat", "bench",
		"rearloungesofaleft", "rearloungesofaright", "sofa",
		"couch", "frontgallerybench", "southentrybench",
	]:
		return ANCHOR_BENCH_LUMINA
	if normalized in [
		"centerconversationtable", "table", "readingroundtable",
		"rearloungelowtable", "studiolongdesk",
	]:
		return ROOM_CONVERSATION_ANCHOR
	# Unknown names are passed to Presence's strict resolver, which fails closed.
	return requested


func _apply_living_hub_look(command: Dictionary) -> bool:
	var target := _command_vector(command.get("position", {}))
	if not _vector_is_finite(target):
		return false
	var coordinate_space := _coordinate_space(command)
	if coordinate_space.is_empty():
		return false
	if coordinate_space != "world":
		target = _greeting_frame * target
	return look_at_position(target)


func _coordinate_space(command: Dictionary) -> String:
	var params := _command_params(command)
	if not params.has("coordinate_space"):
		return "legacy_greeting_frame"
	var requested := String(params.get("coordinate_space", "")).strip_edges().to_lower()
	if requested == "world":
		return "world"
	if requested == "legacy_greeting_frame":
		return "legacy_greeting_frame"
	return ""


func _presence_can_resolve_anchor(anchor_name: String) -> bool:
	if _presence == null or not _presence.has_method("get_status"):
		return false
	var status = _presence.call("get_status")
	if typeof(status) != TYPE_DICTIONARY:
		return false
	var anchors = (status as Dictionary).get("anchors", {})
	if typeof(anchors) == TYPE_DICTIONARY:
		var present = (anchors as Dictionary).get("present", [])
		if typeof(present) == TYPE_ARRAY and (present as Array).has(anchor_name.to_upper()):
			return true
	if anchor_name.to_upper() == ROOM_CONVERSATION_ANCHOR.to_upper():
		return _find_node3d(ROOM_CONVERSATION_ANCHOR) != null
	return false


func _vector_is_finite(value: Vector3) -> bool:
	return not (
		is_nan(value.x) or is_inf(value.x)
		or is_nan(value.y) or is_inf(value.y)
		or is_nan(value.z) or is_inf(value.z)
	)


func _command_params(command: Dictionary) -> Dictionary:
	var raw = command.get("params", {})
	return (raw as Dictionary).duplicate(true) if typeof(raw) == TYPE_DICTIONARY else {}


func _command_vector(value: Variant) -> Vector3:
	if value is Vector3:
		return value as Vector3
	if typeof(value) != TYPE_DICTIONARY:
		return Vector3.ZERO
	var data := value as Dictionary
	return Vector3(
		float(data.get("x", 0.0)),
		float(data.get("y", 0.0)),
		float(data.get("z", 0.0))
	)


func _presence_state() -> String:
	return String(_presence.call("get_state")) if _presence and _presence.has_method("get_state") else ""
