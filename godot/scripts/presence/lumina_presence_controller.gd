extends Node
class_name LuminaPresenceController

## Spatial co-presence FSM for Lumina.
## Owns greet / walk-with / guide / shared-gaze / sit / wait / farewell behaviors.
## References avatar and player as Node3D (or CharacterBody3D) only — never mutates VRM meshes.

signal state_changed(previous: String, current: String)
signal destination_reached(anchor_name: String)
signal shared_gaze_started(target: String)
signal sit_completed
signal behavior_failed(reason: String)
signal si_motion_finished(token: String, ok: bool, detail: String)

const States = preload("res://scripts/presence/lumina_presence_states.gd")
const Anchors = preload("res://scripts/presence/lumina_presence_anchors.gd")
const Mover = preload("res://scripts/presence/lumina_presence_mover.gd")
const StuckGuard = preload("res://scripts/presence/lumina_presence_stuck_guard.gd")

@export var enabled := true
@export var arrive_notice_distance := 6.0
@export var leave_distance := 8.0
@export var quiet_idle_seconds := 18.0
@export var thinking_sway_degrees := 8.0
@export var log_transitions := true

var _state := States.IDLE_PRIVATE
var _previous_state := States.IDLE_PRIVATE
var _avatar: Node3D
var _player: Node3D
var _anchors: RefCounted
var _mover: RefCounted
var _stuck: RefCounted
var _pending_resume_state := ""
var _active_destination := ""
var _shared_gaze_target := ""
var _sit_pair: Dictionary = {}
var _thinking := false
var _thinking_phase := 0.0
var _thinking_base_yaw := 0.0
var _idle_elapsed := 0.0
var _player_noticed := false
var _auto_arrive_armed := true
var _gesture := "none"
var _transition_log: Array[Dictionary] = []
var _last_fail := ""
var _safe_home := Vector3.ZERO
var _si_motion_token := ""
var _si_motion_kind := ""
var _si_motion_target := ""


func _ready() -> void:
	_anchors = Anchors.new()
	_mover = Mover.new()
	_stuck = StuckGuard.new()
	_mover.arrived.connect(_on_mover_arrived)
	_mover.move_failed.connect(_on_mover_failed)
	_stuck.stuck_detected.connect(_on_stuck_detected)


func bind(avatar: Node3D, player: Node3D = null, anchor_root: Node = null) -> bool:
	_si_motion_token = ""
	_si_motion_kind = ""
	_si_motion_target = ""
	_avatar = avatar
	_player = player
	if not _mover.bind_avatar(avatar):
		return false
	_mover.set_player(player)
	_safe_home = avatar.global_position if avatar != null else Vector3.ZERO
	_anchors.set_fallback_origin(_safe_home)
	_anchors.clear()
	if anchor_root != null:
		_anchors.register_from_parent(anchor_root)
	elif avatar != null and avatar.get_tree() != null and avatar.get_tree().current_scene != null:
		_anchors.register_from_parent(avatar.get_tree().current_scene)
	_set_state(States.IDLE_PRIVATE, "bind")
	return true


func set_player(player: Node3D) -> void:
	_player = player
	_mover.set_player(player)
	_auto_arrive_armed = true
	_player_noticed = false


func register_anchor(anchor_name: String, node: Node3D) -> bool:
	return _anchors.register_node(anchor_name, node)


func get_state() -> String:
	return _state


func get_transition_log() -> Array[Dictionary]:
	return _transition_log.duplicate(true)


func clear_transition_log() -> void:
	_transition_log.clear()


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"state": _state,
		"previous_state": _previous_state,
		"gesture": _gesture,
		"thinking": _thinking,
		"active_destination": _active_destination,
		"shared_gaze_target": _shared_gaze_target,
		"idle_elapsed": _idle_elapsed,
		"player_noticed": _player_noticed,
		"last_fail": _last_fail,
		"mover": _mover.get_status(),
		"stuck": _stuck.get_status(),
		"anchors": _anchors.get_status(),
		"transition_count": _transition_log.size(),
	}


func get_si_motion_status() -> Dictionary:
	return {
		"active": not _si_motion_token.is_empty(),
		"token": _si_motion_token,
		"kind": _si_motion_kind,
		"target": _si_motion_target,
		"state": _state,
		"mover": _mover.get_status(),
	}


# --- External interface -----------------------------------------------------

func request_si_move(target: Vector3, token: String, params: Dictionary = {}) -> bool:
	if not _si_request_is_valid(token) or not _vector_is_finite(target):
		return false
	_cancel_si_for_replacement()
	var flat_target := Vector3(target.x, _avatar.global_position.y, target.z)
	_start_si_motion(token, "move", "position")
	if not _mover.goto(flat_target, token, NAN, _si_mover_params(params, true)):
		_clear_si_motion_state(States.IDLE_PRIVATE, "si_move_start_rejected")
		return false
	_stuck.reset_behavior()
	_stuck.begin_move(_mover.get_position())
	return true


func request_si_move_anchor(anchor_name: String, token: String, params: Dictionary = {}) -> bool:
	if not _si_request_is_valid(token):
		return false
	var resolved := _resolve_si_anchor_strict(anchor_name)
	if not bool(resolved.get("ok", false)):
		return false
	_cancel_si_for_replacement()
	var target: Vector3 = resolved["position"]
	target.y = _avatar.global_position.y
	_start_si_motion(token, "move_anchor", String(resolved.get("name", anchor_name)))
	if not _mover.goto(
		target,
		token,
		float(resolved.get("yaw", NAN)),
		_si_mover_params(params, false)
	):
		_clear_si_motion_state(States.IDLE_PRIVATE, "si_anchor_start_rejected")
		return false
	_stuck.reset_behavior()
	_stuck.begin_move(_mover.get_position())
	return true


func request_si_sit(token: String, params: Dictionary = {}) -> bool:
	if not _si_request_is_valid(token):
		return false
	var resolved := _resolve_si_anchor_strict(Anchors.ANCHOR_BENCH_LUMINA)
	if not bool(resolved.get("ok", false)):
		return false
	_cancel_si_for_replacement()
	var target: Vector3 = resolved["position"]
	target.y = _avatar.global_position.y
	_start_si_motion(token, "sit", Anchors.ANCHOR_BENCH_LUMINA)
	if not _mover.goto(
		target,
		token,
		float(resolved.get("yaw", NAN)),
		_si_mover_params(params, false)
	):
		_clear_si_motion_state(States.IDLE_PRIVATE, "si_sit_start_rejected")
		return false
	_stuck.reset_behavior()
	_stuck.begin_move(_mover.get_position())
	return true


func request_si_stand(_params: Dictionary = {}) -> bool:
	if not enabled or _avatar == null:
		return false
	_cancel_si_for_replacement()
	_mover.cancel()
	_stuck.end_move()
	_gesture = "stand"
	_active_destination = ""
	_set_state(States.IDLE_PRIVATE, "si_stand")
	return true


func cancel_si_motion(expected_token: String, detail: String = "SI motion cancelled") -> bool:
	if _si_motion_token.is_empty():
		return false
	if expected_token.strip_edges().is_empty() or expected_token != _si_motion_token:
		return false
	_finish_si_motion(_si_motion_token, false, detail, States.IDLE_PRIVATE)
	return true

func notify_player_arrived() -> void:
	if not enabled:
		return
	_interrupt_si_for_natural("interrupted by notify_player_arrived")
	_player_noticed = true
	_auto_arrive_armed = false
	_idle_elapsed = 0.0
	_stuck.reset_behavior()
	_set_state(States.NOTICE_PLAYER, "notify_player_arrived")
	_gesture = "notice"
	_face_player()
	_set_state(States.GREET_AT_LOBBY, "greet_walk")
	_begin_goto_anchor(Anchors.ANCHOR_LOBBY_GREETING, States.GREET_AT_LOBBY)


func request_follow() -> void:
	if not _require_bound("request_follow"):
		return
	_interrupt_si_for_natural("interrupted by request_follow")
	_stuck.reset_behavior()
	_set_state(States.WALK_WITH_PLAYER, "request_follow")
	_gesture = "walk"
	if not _mover.start_lead():
		# Fallback: side-by-side follow if player forward lead fails.
		if not _mover.start_follow():
			_fail("follow_start_failed")
			return
	_stuck.begin_move(_mover.get_position())


func request_guide(destination: String) -> void:
	if not _require_bound("request_guide"):
		return
	_interrupt_si_for_natural("interrupted by request_guide")
	var dest := destination.strip_edges()
	if dest.is_empty():
		dest = Anchors.ANCHOR_ROOM_GUIDE
	_stuck.reset_behavior()
	_active_destination = dest.to_upper()
	_set_state(States.GUIDE_TO_ROOM, "request_guide")
	_gesture = "guide"
	_begin_goto_anchor(_active_destination, States.GUIDE_TO_ROOM)


func request_shared_gaze(target: String) -> void:
	if not _require_bound("request_shared_gaze"):
		return
	_interrupt_si_for_natural("interrupted by request_shared_gaze")
	_shared_gaze_target = target.strip_edges()
	if _shared_gaze_target.is_empty():
		_shared_gaze_target = Anchors.ANCHOR_MEMORY_WALL
	_stuck.reset_behavior()
	_set_state(States.INVITE_TO_VIEW, "request_shared_gaze")
	_gesture = "invite"
	_begin_goto_anchor(Anchors.ANCHOR_WINDOW_VIEW_LUMINA, States.INVITE_TO_VIEW)


func request_sit(anchor_pair: Variant = "bench") -> void:
	if not _require_bound("request_sit"):
		return
	_interrupt_si_for_natural("interrupted by request_sit")
	_sit_pair = _anchors.resolve_pair(anchor_pair)
	_stuck.reset_behavior()
	_set_state(States.SIT_TOGETHER, "request_sit")
	_gesture = "sit_approach"
	var lumina: Dictionary = _sit_pair.get("lumina", {})
	if not bool(lumina.get("ok", false)):
		_fail("sit_anchor_missing")
		return
	_active_destination = String(_sit_pair.get("lumina_name", Anchors.ANCHOR_BENCH_LUMINA))
	_mover.goto(
		lumina["position"],
		_active_destination,
		float(lumina.get("yaw", NAN))
	)
	_stuck.begin_move(_mover.get_position())


func request_quiet_companionship() -> void:
	if not _require_bound("request_quiet_companionship"):
		return
	_interrupt_si_for_natural("interrupted by request_quiet_companionship")
	_mover.cancel()
	_stuck.end_move()
	_thinking = false
	_set_state(States.QUIET_COMPANIONSHIP, "request_quiet_companionship")
	_gesture = "quiet"
	_idle_elapsed = 0.0
	_adjust_distance_to_player(1.25)


func request_farewell() -> void:
	if not _require_bound("request_farewell"):
		return
	_interrupt_si_for_natural("interrupted by request_farewell")
	_stuck.reset_behavior()
	_set_state(States.FAREWELL, "request_farewell")
	_gesture = "farewell"
	_begin_goto_anchor(Anchors.ANCHOR_FAREWELL, States.FAREWELL)


func cancel_current_behavior() -> void:
	if not _si_motion_token.is_empty():
		cancel_si_motion(_si_motion_token, "Presence behavior cancelled")
	_mover.cancel()
	_stuck.end_move()
	_thinking = false
	_gesture = "none"
	_active_destination = ""
	_shared_gaze_target = ""
	_pending_resume_state = ""
	_auto_arrive_armed = false
	_set_state(States.IDLE_PRIVATE, "cancel_current_behavior")


func enter_conversation() -> void:
	if not enabled:
		return
	_interrupt_si_for_natural("interrupted by enter_conversation")
	_mover.cancel()
	_stuck.end_move()
	_set_state(States.CONVERSATION, "enter_conversation")
	_gesture = "talk_ready"


func set_conversation_thinking(active: bool) -> void:
	_thinking = active
	if active and _state != States.CONVERSATION:
		enter_conversation()
	if active and _avatar != null:
		_thinking_base_yaw = _avatar.rotation.y
		_thinking_phase = 0.0
	_gesture = "thinking" if active else ("talk_ready" if _state == States.CONVERSATION else _gesture)
	if active:
		_idle_elapsed = 0.0
	elif _avatar != null and _state == States.CONVERSATION:
		_avatar.rotation.y = _thinking_base_yaw


# --- Frame loop -------------------------------------------------------------

func _process(delta: float) -> void:
	if not enabled or _avatar == null:
		return
	_idle_elapsed += delta
	_maybe_auto_notice_player()
	_maybe_handle_player_left()
	_advance_thinking_gesture(delta)
	_advance_quiet_idle(delta)
	_mover.advance(delta)
	var moving := bool(_mover.is_active()) and States.is_locomotion(_state)
	# Lead look-back intentionally pauses translation; do not treat as path stall.
	var mover_status: Dictionary = _mover.get_status()
	if bool(mover_status.get("looking_back", false)) or String(mover_status.get("mode", "")) in ["follow", "lead"]:
		moving = false
	var _si_token_before_observe := _si_motion_token
	var stuck_info: Dictionary = _stuck.observe(delta, _mover.get_position(), moving)
	if bool(stuck_info.get("stuck", false)):
		# The guard emits synchronously. A directed SI stall is already terminalized
		# by _on_stuck_detected; never start an unrequested recovery move afterward.
		if not _si_token_before_observe.is_empty():
			return
		_enter_recover(stuck_info)


func _physics_process(_delta: float) -> void:
	pass



# --- Internals --------------------------------------------------------------

func _begin_goto_anchor(anchor_name: String, for_state: String) -> void:
	var resolved: Dictionary = _anchors.resolve_position(anchor_name)
	if not bool(resolved.get("ok", false)):
		_fail("anchor_unresolved:%s" % anchor_name)
		return
	_active_destination = String(resolved.get("name", anchor_name))
	if String(resolved.get("source", "")) == "fallback" and log_transitions:
		_log("anchor_fallback name=%s" % _active_destination)
	_mover.goto(
		resolved["position"],
		_active_destination,
		float(resolved.get("yaw", NAN))
	)
	_stuck.begin_move(_mover.get_position())
	if _state != for_state and for_state != "":
		# Keep NOTICE→GREET explicit when already transitioned.
		pass


func _on_mover_arrived(target_name: String) -> void:
	_stuck.end_move()
	if _state == States.DIRECTED_MOVE and not _si_motion_token.is_empty():
		var token := _si_motion_token
		var kind := _si_motion_kind
		if target_name.strip_edges() != token:
			return
		if kind == "sit":
			_gesture = "sit"
			sit_completed.emit()
			_finish_si_motion(token, true, "seated at %s" % Anchors.ANCHOR_BENCH_LUMINA, States.SIT_TOGETHER)
		else:
			_finish_si_motion(token, true, "Presence target reached", States.IDLE_PRIVATE)
		return
	destination_reached.emit(target_name)
	_log("destination_reached name=%s state=%s" % [target_name, _state])
	match _state:
		States.NOTICE_PLAYER, States.GREET_AT_LOBBY:
			_set_state(States.GREET_AT_LOBBY, "arrived_lobby")
			_gesture = "greet"
			_face_player()
		States.GUIDE_TO_ROOM:
			_gesture = "present_room"
			_face_player()
		States.INVITE_TO_VIEW:
			_set_state(States.SHARED_GAZE, "arrived_view")
			_gesture = "shared_gaze"
			shared_gaze_started.emit(_shared_gaze_target)
			_face_target_name(_shared_gaze_target)
		States.SIT_TOGETHER:
			_gesture = "sit"
			_align_beside_player_anchor()
			sit_completed.emit()
		States.FAREWELL:
			_gesture = "wave"
			_face_player()
		States.RECOVER_FROM_STUCK:
			_resume_after_recover()
		_:
			pass


func _on_mover_failed(reason: String) -> void:
	if not _si_motion_token.is_empty():
		_finish_si_motion(_si_motion_token, false, reason, States.IDLE_PRIVATE)
		return
	_fail(reason)


func _on_stuck_detected(detail: Dictionary) -> void:
	if not _si_motion_token.is_empty():
		_finish_si_motion(
			_si_motion_token,
			false,
			"SI motion stalled: %s" % String(detail.get("reason", "path_stall")),
			States.IDLE_PRIVATE
		)
		return
	_enter_recover(detail)


func _start_si_motion(token: String, kind: String, target: String) -> void:
	_si_motion_token = token.strip_edges()
	_si_motion_kind = kind
	_si_motion_target = target
	_active_destination = target
	_gesture = "sit_approach" if kind == "sit" else ("stand" if kind == "stand" else "walk")
	_set_state(States.DIRECTED_MOVE, "si_%s" % kind)


func _finish_si_motion(
	token: String,
	ok: bool,
	detail: String,
	terminal_state: String
) -> void:
	if token.is_empty() or token != _si_motion_token:
		return
	_mover.cancel()
	_stuck.end_move()
	# Clear the active token/state before emission so stale or re-entrant callbacks
	# cannot terminalize the same accepted request twice.
	_clear_si_motion_state(terminal_state, "si_terminal")
	si_motion_finished.emit(token, ok, detail)


func _clear_si_motion_state(terminal_state: String, reason: String) -> void:
	_si_motion_token = ""
	_si_motion_kind = ""
	_si_motion_target = ""
	_active_destination = ""
	if terminal_state != States.SIT_TOGETHER:
		_gesture = "none"
	_set_state(terminal_state, reason)


func _cancel_si_for_replacement() -> void:
	if not _si_motion_token.is_empty():
		cancel_si_motion(_si_motion_token, "replaced by a newer SI motion")


func _interrupt_si_for_natural(detail: String) -> void:
	if not _si_motion_token.is_empty():
		cancel_si_motion(_si_motion_token, detail)


func _si_request_is_valid(token: String) -> bool:
	return enabled and _avatar != null and not token.strip_edges().is_empty()


func _si_mover_params(params: Dictionary, exact_finish: bool) -> Dictionary:
	return {
		"walk_speed": clampf(float(params.get("walk_speed", 1.35)), 0.4, 2.5),
		"stop_distance": clampf(float(params.get("stop_distance", 0.12)), 0.05, 3.0),
		"exact_finish": exact_finish,
	}


func _resolve_si_anchor_strict(anchor_name: String) -> Dictionary:
	var requested := anchor_name.strip_edges()
	if requested.is_empty():
		return {"ok": false}
	var normalized := requested.to_upper()
	if normalized in Anchors.CONTRACT_NAMES:
		var status: Dictionary = _anchors.get_status()
		var present: Array = status.get("present", [])
		if not present.has(normalized):
			return {"ok": false}
		return _anchors.resolve_position(normalized)
	var allowed_room_anchors := [
		"WRV1_Anchor_conversation",
	]
	if not allowed_room_anchors.has(requested):
		return {"ok": false}
	var node := _find_node3d_exact(_avatar.get_tree().current_scene, requested)
	if node == null:
		return {"ok": false}
	return {
		"ok": true,
		"name": requested,
		"position": node.global_position,
		"yaw": node.global_rotation.y,
	}


func _find_node3d_exact(root: Node, target_name: String) -> Node3D:
	if root == null:
		return null
	if root is Node3D and String(root.name) == target_name:
		return root as Node3D
	for child in root.get_children():
		var found := _find_node3d_exact(child, target_name)
		if found != null:
			return found
	return null


func _vector_is_finite(value: Vector3) -> bool:
	return not (
		is_nan(value.x) or is_inf(value.x)
		or is_nan(value.y) or is_inf(value.y)
		or is_nan(value.z) or is_inf(value.z)
	)


func _enter_recover(detail: Dictionary) -> void:
	if _state == States.RECOVER_FROM_STUCK:
		return
	_pending_resume_state = _state
	_set_state(States.RECOVER_FROM_STUCK, "stuck:%s" % String(detail.get("reason", "unknown")))
	_gesture = "recover"
	_mover.cancel()
	var safe: Dictionary = _anchors.resolve_position(Anchors.ANCHOR_LOBBY_GREETING)
	var safe_pos: Vector3 = safe.get("position", _safe_home)
	if bool(detail.get("exhausted", false)):
		_mover.teleport(safe_pos, float(safe.get("yaw", 0.0)))
		_stuck.mark_recovered(safe_pos, detail)
		_fail("stuck_exhausted")
		_set_state(States.IDLE_PRIVATE, "recover_exhausted")
		return
	# Soft recovery: nudge toward safe point without hard fail.
	_mover.goto(safe_pos, Anchors.ANCHOR_LOBBY_GREETING, float(safe.get("yaw", NAN)))
	_stuck.mark_recovered(safe_pos, detail)
	_stuck.begin_move(_mover.get_position())


func _resume_after_recover() -> void:
	var resume := _pending_resume_state
	_pending_resume_state = ""
	_stuck.reset_behavior()
	match resume:
		States.WALK_WITH_PLAYER:
			request_follow()
		States.GUIDE_TO_ROOM:
			request_guide(_active_destination)
		States.INVITE_TO_VIEW, States.SHARED_GAZE:
			request_shared_gaze(_shared_gaze_target)
		States.SIT_TOGETHER:
			request_sit(_sit_pair if not _sit_pair.is_empty() else "bench")
		States.FAREWELL:
			request_farewell()
		States.GREET_AT_LOBBY, States.NOTICE_PLAYER:
			notify_player_arrived()
		_:
			_set_state(States.IDLE_PRIVATE, "recover_done")


func _maybe_auto_notice_player() -> void:
	if _player == null or _avatar == null:
		return
	var distance := _horizontal_distance(_avatar.global_position, _player.global_position)
	if distance > arrive_notice_distance * 1.15:
		_auto_arrive_armed = true
		return
	if not _auto_arrive_armed or _state != States.IDLE_PRIVATE:
		return
	if distance <= arrive_notice_distance:
		_auto_arrive_armed = false
		notify_player_arrived()


func _maybe_handle_player_left() -> void:
	if _player == null or _avatar == null:
		return
	if _state in [
		States.IDLE_PRIVATE,
		States.FAREWELL,
		States.RECOVER_FROM_STUCK,
		States.NOTICE_PLAYER,
		States.GREET_AT_LOBBY,
		States.GUIDE_TO_ROOM,
		States.INVITE_TO_VIEW,
		States.SHARED_GAZE,
		States.SIT_TOGETHER,
		States.CONVERSATION,
		States.DIRECTED_MOVE,
	]:
		# Goal-directed or stationary social states should not be interrupted by chase.
		return
	var distance := _horizontal_distance(_avatar.global_position, _player.global_position)
	if distance < leave_distance:
		return
	if _state == States.WALK_WITH_PLAYER:
		_log("player_left follow_decision=keep_follow distance=%.2f" % distance)
		return
	if _state == States.QUIET_COMPANIONSHIP:
		_log("player_left follow_decision=chase distance=%.2f" % distance)
		request_follow()
		return
	_log("player_left follow_decision=wait distance=%.2f" % distance)
	_gesture = "wait_glance"
	_face_player()


func _advance_thinking_gesture(delta: float) -> void:
	if not _thinking or _avatar == null:
		return
	_thinking_phase += delta
	var sway := sin(_thinking_phase * 2.2) * deg_to_rad(thinking_sway_degrees)
	_avatar.rotation.y = lerp_angle(
		_avatar.rotation.y,
		_thinking_base_yaw + sway,
		clampf(delta * 4.0, 0.0, 1.0)
	)


func _advance_quiet_idle(delta: float) -> void:
	if _state != States.IDLE_PRIVATE and _state != States.QUIET_COMPANIONSHIP:
		return
	if _mover.is_active():
		return
	if _idle_elapsed < quiet_idle_seconds:
		return
	# Natural wait: slight look around then settle.
	_idle_elapsed = 0.0
	_gesture = "idle_wait"
	if _player != null and _state == States.QUIET_COMPANIONSHIP:
		_face_player()
	elif _avatar != null:
		_avatar.rotation.y = lerp_angle(_avatar.rotation.y, _avatar.rotation.y + deg_to_rad(25.0), 0.35)


func _adjust_distance_to_player(desired: float) -> void:
	if _player == null or _avatar == null:
		return
	var offset := _avatar.global_position - _player.global_position
	offset.y = 0.0
	var distance := offset.length()
	if distance < 0.001:
		offset = Vector3(1, 0, 0)
		distance = 1.0
	var scaled := offset.normalized() * desired
	var target := Vector3(
		_player.global_position.x + scaled.x,
		_avatar.global_position.y,
		_player.global_position.z + scaled.z
	)
	if absf(distance - desired) > 0.25:
		_mover.goto(target, "DISTANCE_ADJUST")
		_stuck.begin_move(_mover.get_position())
	else:
		_face_player()


func _align_beside_player_anchor() -> void:
	var player_anchor: Dictionary = _sit_pair.get("player", {})
	if bool(player_anchor.get("ok", false)) and _player != null:
		# Soft hint only — do not teleport the player. Face the shared sit axis.
		var mid: Vector3 = player_anchor["position"]
		_face_toward(mid)
	else:
		_face_player()


func _face_player() -> void:
	if _player == null:
		return
	_face_toward(_player.global_position)


func _face_target_name(target_name: String) -> void:
	var resolved: Dictionary = _anchors.resolve_position(target_name)
	if bool(resolved.get("ok", false)):
		_face_toward(resolved["position"])
	else:
		_face_player()


func _face_toward(world_point: Vector3) -> void:
	if _avatar == null:
		return
	var offset := world_point - _avatar.global_position
	offset.y = 0.0
	if offset.length() < 0.001:
		return
	_avatar.rotation.y = atan2(offset.x, offset.z)


func _set_state(next_state: String, reason: String) -> void:
	if not States.is_valid(next_state):
		_fail("invalid_state:%s" % next_state)
		return
	if next_state == _state and reason != "bind":
		return
	_previous_state = _state
	_state = next_state
	if next_state != States.IDLE_PRIVATE:
		_idle_elapsed = 0.0
	var entry := {
		"previous": _previous_state,
		"current": _state,
		"reason": reason,
		"msec": Time.get_ticks_msec(),
	}
	_transition_log.append(entry)
	if log_transitions:
		_log("state_changed %s -> %s reason=%s" % [_previous_state, _state, reason])
	state_changed.emit(_previous_state, _state)


func _require_bound(op: String) -> bool:
	if not enabled:
		_fail("%s_disabled" % op)
		return false
	if _avatar == null:
		_fail("%s_unbound" % op)
		return false
	return true


func _fail(reason: String) -> void:
	_last_fail = reason
	_log("behavior_failed reason=%s" % reason)
	behavior_failed.emit(reason)


func _log(message: String) -> void:
	print("LUMINA_PRESENCE %s" % message)


func debug_force_stuck(exhausted := false) -> void:
	_enter_recover({"reason": "forced_test", "exhausted": exhausted})


func _horizontal_distance(a: Vector3, b: Vector3) -> float:
	return Vector3(a.x - b.x, 0.0, a.z - b.z).length()
