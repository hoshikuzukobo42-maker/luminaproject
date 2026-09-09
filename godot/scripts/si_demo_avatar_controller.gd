extends CharacterBody3D
class_name SIDemoAvatarController

const ObstacleRoutePlanner = preload("res://scripts/si_obstacle_route_planner.gd")

signal si_speak_requested(text: String)
signal si_command_completed(command_id: String, ok: bool, status: String, detail: String)

@export var player_group_name: String = "player"
@export var navigation_agent_path: NodePath
@export var default_walk_speed: float = 2.2
@export var default_stop_distance: float = 0.35
@export var follow_default_distance: float = 1.2
@export var rotation_speed: float = 8.0
@export var obstacle_avoidance_lookahead: float = 2.8
@export var obstacle_avoidance_strength: float = 0.95
@export var obstacle_avoidance_padding: float = 0.15
@export var obstacle_avoidance_side_memory: float = 1.0
@export var movement_stall_abort_seconds: float = 3.2
@export var movement_timeout_extra_seconds: float = 4.5
@export var movement_min_timeout_seconds: float = 6.0
@export var body_mesh_path: NodePath
@export var vrm_adapter_path: NodePath
@export var default_gesture_duration: float = 1.8
@export var look_target_height_offset: float = 1.25
@export var user_look_target_height_offset: float = 1.55
@export var eye_view_height: float = 1.50
@export var gaze_hold_seconds: float = 2.4
@export var user_attention_hold_seconds: float = 45.0
@export var look_attention_rotation_boost: float = 1.45
@export var user_attention_soft_expression: String = "relaxed"
@export var user_attention_soft_expression_intensity: float = 0.16
@export var stage_min_x: float = -24.25
@export var stage_max_x: float = 24.25
@export var stage_min_z: float = -24.25
@export var stage_max_z: float = 24.25
@export var route_planner_cell_size: float = 0.45
@export var route_planner_clearance: float = 0.42
@export var route_planner_max_waypoints: int = 96
@export var route_replan_stall_seconds: float = 0.9
@export var route_replan_max_attempts: int = 3
@export var route_progress_distance: float = 0.04
@export var route_obstacle_min_height: float = 0.16
@export var route_obstacle_floor_tolerance: float = 0.45
@export var route_agent_height: float = 1.35
@export var route_arrival_epsilon: float = 0.035

var active_command_id: String = ""
var navigation_ready: bool = true
var current_animation: String = "Idle"
var current_expression: String = "neutral"
var current_posture: String = "idle"
var last_speech_text: String = ""
var current_goal: String = ""
var movement_mode: String = "idle"
var target_node_name: String = ""
var target_distance: float = 0.0
var looking_at_user: bool = false
var look_target_name: String = ""
var look_attention_state: String = "none"
var look_hold_remaining: float = 0.0
var navigation_detour_active: bool = false
var navigation_detour_waypoint_count: int = 0
var navigation_replan_count: int = 0
var navigation_last_plan_status: String = "idle"
var navigation_planned_path_length: float = 0.0
var route_obstacle_count: int = 0
var last_navigation_result: Dictionary = {}
var _cancelled_command_ids: Dictionary = {}

var _body_mesh: MeshInstance3D
var _vrm_adapter: Node
var _agent: NavigationAgent3D
var _target_position := Vector3.ZERO
var _has_target := false
var _using_navigation := false
var _walk_speed := 2.2
var _stop_distance := 0.35
var _look_target_node: Node3D
var _look_target_position := Vector3.ZERO
var _has_look_position := false
var _following_user := false
var _follow_target: Node3D
var _follow_distance := 1.2
var _navigation_fallback_timer := 0.0
var _target_node: Node3D
var _route_positions: Array[Vector3] = []
var _route_final_node: Node3D = null
var _route_params: Dictionary = {}
var _route_goal_position := Vector3.ZERO
var _route_planning_active := false
var _route_force_direct := false
var _route_target_instance_id: int = 0
var _route_target_transform := Transform3D.IDENTITY
var _route_blocked := false
var _route_retry_after: float = 0.0
var _route_failed_attempts: int = 0
var _is_in_navigation_fallback := false
var _is_avoiding := false
var _avoidance_side_sign := 1.0
var _last_target_distance := 999999.0
var _movement_stall_timer := 0.0
var _last_progress_position := Vector3.ZERO
var _movement_started_at: float = 0.0
var _movement_timeout_seconds: float = 0.0
var _active_async_command_id: String = ""
var _active_async_action: String = ""
var _active_async_started_at: float = 0.0
var _temporary_posture_until: float = -1.0
var _speak_completion_supported := false
var _pending_sit_node: Node3D = null
var _pending_sit_params: Dictionary = {}
var _look_hold_until: float = -1.0
var _look_target_is_user := false
var _automatic_physics_obstacle_cache: Array = []
var _automatic_physics_obstacle_cache_at: float = -999999.0

const _NAVIGATION_WATCHDOG_SECONDS: float = 0.65
const _AUTOMATIC_PHYSICS_OBSTACLE_REFRESH_SECONDS: float = 0.25
const _AUTOMATIC_PHYSICS_OBSTACLE_LIMIT: int = 4096


func _ready() -> void:
	_body_mesh = get_node_or_null(body_mesh_path) as MeshInstance3D
	_vrm_adapter = get_node_or_null(vrm_adapter_path)
	_connect_vrm_adapter_signals()
	_agent = get_node_or_null(navigation_agent_path) as NavigationAgent3D
	_walk_speed = default_walk_speed
	_stop_distance = maxf(0.05, default_stop_distance)
	_follow_distance = follow_default_distance
	_avoidance_side_sign = 1.0 if is_zero_approx(obstacle_avoidance_side_memory) else signf(obstacle_avoidance_side_memory)
	navigation_ready = _agent != null
	if _agent:
		_agent.path_desired_distance = 0.35
		_agent.target_desired_distance = minf(_stop_distance, route_arrival_epsilon)
	_apply_expression_color()
	_set_motion_state("Idle")


func _physics_process(delta: float) -> void:
	_tick_temporary_posture()
	if _following_user:
		_update_follow_target()
	if _has_target:
		if _using_navigation:
			_tick_navigation(delta)
		else:
			_tick_move(delta)
		return
	
	if _agent:
		_agent.target_position = global_position
	velocity = Vector3.ZERO
	_set_motion_state("Idle")
	_tick_look_at(delta)


func _update_follow_target() -> void:
	if not is_instance_valid(_follow_target) or _follow_target.is_queued_for_deletion():
		_abort_unfinished_motion(target_distance, "follow target unavailable")
		return
	var user_position := _follow_target.global_position
	user_position.y = global_position.y
	var distance_to_user := global_position.distance_to(user_position)
	if distance_to_user <= _follow_distance and _current_route_segment_clear(user_position):
		# follow_user is a one-shot approach. Completion ends movement ownership.
		var player := _follow_target
		_record_navigation_result(true, "completed")
		stop_motion(false)
		_look_target_node = player
		_has_look_position = false
		current_goal = "follow_user"
		movement_mode = "follow_hold"
		target_distance = distance_to_user
		_complete_async_command(true, "completed", "follow range reached")
		return
	# The shared route tick refreshes a moved target without resetting its lifetime.


func apply_si_command(command: Dictionary) -> bool:
	var command_id := String(command.get("command_id", ""))
	var action := String(command.get("action", ""))
	var params: Dictionary = command.get("params", {})
	if not command_id.is_empty() and _cancelled_command_ids.has(command_id):
		return false
	active_command_id = command_id
	var handled := false
	match action:
		"move_to_position":
			handled = move_to_position(_dict_to_vector3(command.get("position", {})), params)
		"move_to_node":
			handled = move_to_node(String(command.get("target_node", "")), params)
		"look_at_position":
			handled = look_at_position(_dict_to_vector3(command.get("position", {})))
		"look_at_node":
			handled = look_at_node(String(command.get("target_node", "")))
		"look_at_user":
			handled = look_at_user()
		"follow_user":
			handled = follow_user(params)
		"play_animation":
			handled = play_animation(String(params.get("animation", "")))
		"set_expression":
			handled = set_expression(String(params.get("expression", "")), float(params.get("intensity", 1.0)))
		"set_motion_profile":
			handled = set_motion_profile(params)
		"set_audio_settings":
			handled = set_audio_settings(params)
		"conversation_activity", "living_state", "semantic_state":
			handled = set_conversation_activity(String(params.get("activity", params.get("state", params.get("semantic_state", "")))))
		"living_animation_style", "animation_style", "motion_style":
			handled = set_living_animation_style(String(params.get("style", params.get("name", params.get("activity_style", "")))))
		"stop_audio":
			handled = stop_audio()
			set_conversation_activity("idle")
		"speak":
			set_conversation_activity("speaking")
			handled = speak(String(params.get("text", "")))
			if not handled:
				set_conversation_activity("idle")
		"sit":
			handled = sit(String(command.get("target_node", "")), params)
		"stand":
			handled = stand()
		"gesture":
			handled = gesture(
				String(params.get("gesture", params.get("intent", ""))),
				float(params.get("duration", -1.0))
			)
		"set_gaze_direction":
			if _vrm_adapter and _vrm_adapter.has_method("set_gaze_direction"):
				_vrm_adapter.call(
					"set_gaze_direction",
					float(params.get("yaw_deg", 0.0)),
					float(params.get("pitch_deg", 0.0)),
					float(params.get("hold_sec", gaze_hold_seconds))
				)
				handled = true
			else:
				handled = false
		"stop":
			var expected_id := String(params.get("expected_command_id", ""))
			if not expected_id.is_empty():
				_cancelled_command_ids[expected_id] = true
				while _cancelled_command_ids.size() > 512:
					_cancelled_command_ids.erase(_cancelled_command_ids.keys()[0])
			if expected_id.is_empty() or expected_id == _active_async_command_id:
				stop_motion(true, true)
			handled = true
		"wait":
			stop_motion()
			handled = true
		_ when action.begins_with("gesture_"):
			handled = gesture(action.substr("gesture_".length()))
	var requires_async := action in ["move_to_position", "move_to_node", "follow_user"] or (action == "sit" and _pending_sit_node != null) or (action == "speak" and _speak_completion_supported)
	if handled and requires_async:
		_begin_async_command(command_id, action)
	elif _active_async_command_id.is_empty():
		active_command_id = ""
	else:
		active_command_id = _active_async_command_id
	return handled


func move_to_position(position: Vector3, params: Dictionary = {}) -> bool:
	return _start_planned_move(position, params, null)


func move_to_node(target_node: String, params: Dictionary = {}) -> bool:
	var target := _find_node3d(target_node)
	if not target:
		push_warning("SIDemoAvatarController: target node not found: %s" % target_node)
		return false
	# Recognition binds to the actual instance, not a reusable scene-node name.
	# This synchronous guard runs before planning or replacing existing movement.
	if target.is_queued_for_deletion():
		return false
	if params.has("expected_target_instance_id") and int(params["expected_target_instance_id"]) != target.get_instance_id():
		return false
	var stop_distance := maxf(0.05, float(params.get("stop_distance", default_stop_distance)))
	var target_position := _resolve_addressable_target_position(target.global_position, target, stop_distance)
	target_position = _resolve_reachable_addressable_target_position(target_position, target, stop_distance)
	return _start_planned_move(target_position, params, target)


func follow_user(params: Dictionary = {}) -> bool:
	var player := _first_player()
	if not player:
		push_warning("SIDemoAvatarController: follow_user failed: player not found")
		return false
	var follow_distance := clampf(float(params.get("distance", params.get("stop_distance", params.get("max_distance", follow_default_distance)))), 0.3, 5.0)
	var started := _start_planned_move(player.global_position, {
		"walk_speed": params.get("walk_speed", default_walk_speed),
		"stop_distance": follow_distance
	}, player)
	if started:
		_following_user = true
		_follow_target = player
		_follow_distance = follow_distance
		current_posture = "follow"
		current_goal = "follow_user"
	return started


func look_at_user() -> bool:
	var nodes := get_tree().get_nodes_in_group(player_group_name)
	if nodes.is_empty() or not (nodes[0] is Node3D):
		return false
	var motion_active := _is_motion_active()
	_look_target_node = nodes[0] as Node3D
	if not motion_active:
		_follow_target = null
		_following_user = false
	_has_look_position = false
	_look_target_is_user = true
	_look_hold_until = Time.get_ticks_msec() / 1000.0 + maxf(0.4, user_attention_hold_seconds)
	looking_at_user = true
	look_target_name = "user"
	look_attention_state = "noticed_user"
	look_hold_remaining = maxf(0.0, _look_hold_until - Time.get_ticks_msec() / 1000.0)
	if not motion_active:
		current_goal = "notice_user"
		if current_posture != "sit":
			current_posture = "attention"
		movement_mode = "attention"
	_apply_user_attention_expression()
	return true


func look_at_node(target_node: String) -> bool:
	var target := _find_node3d(target_node)
	if not target:
		return false
	_look_target_node = target
	if not _is_motion_active():
		_follow_target = null
		_following_user = false
	_has_look_position = false
	_look_target_is_user = false
	_look_hold_until = Time.get_ticks_msec() / 1000.0 + maxf(0.4, gaze_hold_seconds)
	looking_at_user = false
	look_target_name = target.name
	look_attention_state = "looking_at_object"
	look_hold_remaining = maxf(0.0, _look_hold_until - Time.get_ticks_msec() / 1000.0)
	return true


func look_at_position(position: Vector3) -> bool:
	_look_target_node = null
	_look_target_position = position
	if not _is_motion_active():
		_follow_target = null
		_following_user = false
	_has_look_position = true
	_look_target_is_user = false
	_look_hold_until = Time.get_ticks_msec() / 1000.0 + maxf(0.4, gaze_hold_seconds)
	looking_at_user = false
	look_target_name = "position"
	look_attention_state = "looking_at_position"
	look_hold_remaining = maxf(0.0, _look_hold_until - Time.get_ticks_msec() / 1000.0)
	return true


func play_animation(animation_name: String) -> bool:
	if animation_name.is_empty():
		return false
	if _vrm_adapter and _vrm_adapter.has_method("play_animation"):
		if bool(_vrm_adapter.call("play_animation", animation_name)):
			current_animation = animation_name
			_set_motion_state("Idle")
			return true
	current_animation = animation_name
	print("[SIDemoAvatar] animation: %s" % current_animation)
	return true


func set_expression(expression: String, intensity: float = 1.0) -> bool:
	if expression.is_empty():
		return false
	current_expression = expression
	if _vrm_adapter and _vrm_adapter.has_method("set_expression"):
		_vrm_adapter.call("set_expression", expression, clampf(intensity, 0.0, 1.0))
	_apply_expression_color(clampf(intensity, 0.0, 1.0))
	print("[SIDemoAvatar] expression: %s" % current_expression)
	return true


func set_motion_profile(profile: Dictionary) -> bool:
	if profile.is_empty():
		return false
	if _vrm_adapter and _vrm_adapter.has_method("apply_motion_profile"):
		var applied := bool(_vrm_adapter.call("apply_motion_profile", profile))
		if applied:
			print("[SIDemoAvatar] motion profile: %s" % String(profile.get("name", profile.get("profile", "custom"))))
		return applied
	return false


func set_audio_settings(settings: Dictionary) -> bool:
	if settings.is_empty():
		return false
	if _vrm_adapter and _vrm_adapter.has_method("apply_audio_settings"):
		return bool(_vrm_adapter.call("apply_audio_settings", settings))
	return false


func stop_audio() -> bool:
	if _active_async_action == "speak":
		_complete_async_command(false, "interrupted", "audio interrupted")
	if _vrm_adapter and _vrm_adapter.has_method("stop_tts_audio"):
		_vrm_adapter.call("stop_tts_audio")
		return true
	return false


func sit(seat_target_name: String = "", params: Dictionary = {}) -> bool:
	var resolved_target_name := _resolve_sit_target_name(seat_target_name, params)
	var seat_node := _find_node3d(resolved_target_name)
	if not seat_node:
		push_warning("SIDemoAvatarController: sit target node not found: %s" % resolved_target_name)
		return false
	if not _is_sittable_furniture(seat_node):
		push_warning("SIDemoAvatarController: sit rejected for non-seat furniture: %s" % seat_node.name)
		return false

	var stop_distance := maxf(0.05, float(params.get("stop_distance", 0.12)))
	var seat_position := _resolve_addressable_target_position(seat_node.global_position, seat_node, stop_distance)
	seat_position = _resolve_reachable_addressable_target_position(seat_position, seat_node, stop_distance)
	var flat_offset := seat_position - global_position
	flat_offset.y = 0.0
	var arrival_distance := maxf(0.2, float(params.get("arrival_distance", 0.45)))
	if flat_offset.length() > arrival_distance:
		var move_params := params.duplicate(true)
		move_params["stop_distance"] = stop_distance
		move_params["walk_speed"] = float(params.get("walk_speed", default_walk_speed))
		_pending_sit_node = seat_node
		_pending_sit_params = params.duplicate(true)
		var started := _start_planned_move(seat_position, move_params, seat_node)
		if started:
			current_goal = "sit_to_seat"
			return true
		_pending_sit_node = null
		_pending_sit_params = {}
		return false

	if not _record_stationary_route_plan(seat_position, seat_node):
		return false
	return _apply_sit_posture(seat_node, params)


func _apply_sit_posture(seat_node: Node3D, params: Dictionary = {}) -> bool:
	_following_user = false
	_follow_target = null
	_has_target = false
	_using_navigation = false
	_is_in_navigation_fallback = false
	_target_node = null
	_is_avoiding = false
	velocity = Vector3.ZERO
	_snap_to_seat_pose(seat_node, params)
	current_posture = "sit"
	current_goal = "sit"
	movement_mode = "idle"
	target_node_name = seat_node.name if seat_node else String(params.get("target_node", ""))
	target_distance = 0.0
	_temporary_posture_until = -1.0
	var played_sit_transition := false
	if _vrm_adapter:
		# GLB V051 has an authored sit-down transition. Prefer it when the
		# adapter exposes the capability; legacy VRM adapters retain sit_idle.
		if _vrm_adapter.has_method("play_sit_transition"):
			played_sit_transition = bool(_vrm_adapter.call("play_sit_transition"))
		elif _vrm_adapter.has_method("set_sit_posture"):
			_vrm_adapter.call("set_sit_posture", -1.0)
		elif _vrm_adapter.has_method("set_posture_state"):
			_vrm_adapter.call("set_posture_state", "sit", {})
	if played_sit_transition:
		current_animation = "Sit"
		return true
	if _play_animation_chain(["sit", "Sit", "kneel", "knee"]):
		current_animation = "Sit"
		return true
	_set_motion_state("Idle")
	current_animation = "Sit"
	return true


func stand() -> bool:
	_following_user = false
	_follow_target = null
	current_posture = "stand"
	_temporary_posture_until = Time.get_ticks_msec() / 1000.0 + 1.2
	if _vrm_adapter:
		if _vrm_adapter.has_method("set_stand_posture"):
			_vrm_adapter.call("set_stand_posture", 1.0)
		elif _vrm_adapter.has_method("set_posture_state"):
			_vrm_adapter.call("set_posture_state", "stand", {"duration": 1.0})
	if _play_animation_chain(["stand", "Stand", "idle", "Idle"]):
		current_animation = "Stand"
		return true
	_set_motion_state("Idle")
	current_animation = "Idle"
	return true


func gesture(name: String, duration_sec: float = -1.0) -> bool:
	var normalized := name.strip_edges().to_lower()
	if normalized.is_empty():
		return false
	# Accept avatar_runtime intent names as well as legacy gesture tokens.
	var aliases := {
		"small_nod": "nod",
		"deep_nod": "nod",
		"agree": "nod",
		"idle_calm": "idle",
		"idle_happy": "idle",
		"thinking": "think",
		"head_tilt": "tilt_left",
		"look_around": "look_away",
		"return_attention": "look_user",
		"greeting": "wave",
		"happy_reaction": "wave",
		"wave": "wave",
		"explain_small": "nod",
		"surprised": "wave",
		"confused": "tilt_right",
		"concerned": "look_away",
		"disagree": "tilt_left",
	}
	if aliases.has(normalized):
		normalized = String(aliases[normalized])
	current_posture = "gesture:%s" % normalized
	var hold_sec := duration_sec if duration_sec > 0.0 else default_gesture_duration
	_temporary_posture_until = Time.get_ticks_msec() / 1000.0 + maxf(0.2, hold_sec) + 0.35
	if _vrm_adapter:
		if _vrm_adapter.has_method("set_gesture_posture"):
			_vrm_adapter.call("set_gesture_posture", normalized, duration_sec)
		elif _vrm_adapter.has_method("set_posture_state"):
			_vrm_adapter.call("set_posture_state", "gesture", {"gesture": normalized, "duration": duration_sec})
	if _play_animation_chain([normalized, "gesture_%s" % normalized, "Gesture_%s" % normalized.capitalize()]):
		current_animation = normalized
		return true
	# Keep expression/state stable when no gesture animation exists.
	_apply_expression_color(1.0)
	return true


func set_conversation_activity(activity: String) -> bool:
	var semantic := _semantic_activity_for_request(activity)
	if semantic.is_empty():
		return false
	var handled := false
	if _vrm_adapter and _vrm_adapter.has_method("set_conversation_activity"):
		handled = bool(_vrm_adapter.call("set_conversation_activity", semantic))
	elif _vrm_adapter and _vrm_adapter.has_method("set_posture_state"):
		handled = bool(_vrm_adapter.call("set_posture_state", semantic, {}))
	if not handled:
		return false
	if _is_motion_active():
		_set_movement_posture()
		return true
	match semantic:
		"idle":
			current_posture = "idle"
			current_animation = "Idle"
			_temporary_posture_until = -1.0
		"listening":
			current_posture = "listening"
			current_animation = "Listening"
			_temporary_posture_until = -1.0
		"thinking":
			current_posture = "thinking"
			current_animation = "Thinking"
			_temporary_posture_until = -1.0
		"speaking":
			current_posture = "speaking"
			current_animation = "Speaking"
			_temporary_posture_until = -1.0
		"face_user":
			current_posture = "attention"
			current_animation = "TurnRecover"
			_temporary_posture_until = Time.get_ticks_msec() / 1000.0 + 1.4
	return true


func set_living_animation_style(style_name: String) -> bool:
	var normalized := style_name.strip_edges().to_lower()
	if normalized.is_empty():
		return false
	if _vrm_adapter and _vrm_adapter.has_method("set_living_animation_style"):
		return bool(_vrm_adapter.call("set_living_animation_style", normalized))
	return false


func _semantic_activity_for_request(activity: String) -> String:
	var normalized := activity.strip_edges().to_lower()
	if normalized.is_empty():
		return ""
	match normalized:
		"idle", "idle_base", "neutral", "wait", "waiting", "clear", "recover", "none":
			return "idle"
		"listen", "listening", "attentive", "attention", "user_speaking", "input", "hearing":
			return "listening"
		"think", "thinking", "processing", "llm", "generating", "ponder", "hesitate":
			return "thinking"
		"speak", "speaking", "talk", "talking", "chat", "chatting", "tts", "voice":
			return "speaking"
		"turn", "turn_recover", "orient", "orientation", "face_user", "look_at_user":
			return "face_user"
	return ""


func _tick_temporary_posture() -> void:
	if _temporary_posture_until < 0.0:
		return
	if Time.get_ticks_msec() / 1000.0 < _temporary_posture_until:
		return
	_temporary_posture_until = -1.0
	if current_posture.begins_with("gesture") or current_posture == "stand":
		current_posture = "idle"
		if _vrm_adapter and _vrm_adapter.has_method("clear_posture_state"):
			_vrm_adapter.call("clear_posture_state", true)


func _play_animation_chain(candidates: Array[String]) -> bool:
	if _vrm_adapter and _vrm_adapter.has_method("play_animation"):
		for candidate in candidates:
			if candidate.is_empty():
				continue
			if bool(_vrm_adapter.call("play_animation", candidate)):
				return true
	if _vrm_adapter and _vrm_adapter.has_method("set_expression") and not candidates.is_empty():
		_vrm_adapter.call("set_expression", "neutral", 0.6)
	return false


func speak(text: String) -> bool:
	if text.is_empty():
		return false
	last_speech_text = text
	if _vrm_adapter and _vrm_adapter.has_method("speak"):
		_vrm_adapter.call("speak", text)
	emit_signal("si_speak_requested", text)
	print("[SIDemoAvatar] speak: %s" % text)
	return true


func _connect_vrm_adapter_signals() -> void:
	_speak_completion_supported = false
	if not _vrm_adapter:
		return
	if not _vrm_adapter.has_signal("si_tts_playback_finished"):
		return
	var callable := Callable(self, "_on_vrm_tts_playback_finished")
	if not _vrm_adapter.is_connected("si_tts_playback_finished", callable):
		_vrm_adapter.connect("si_tts_playback_finished", callable)
	_speak_completion_supported = true


func _on_vrm_tts_playback_finished(_text: String, ok: bool, detail: String) -> void:
	set_conversation_activity("idle")
	if _active_async_action != "speak":
		return
	_complete_async_command(ok, "completed" if ok else "failed", detail)


func _begin_async_command(command_id: String, action: String) -> void:
	if command_id.is_empty():
		return
	var previous := _take_async_completion(false, "interrupted", "replaced by %s" % action)
	_active_async_command_id = command_id
	_active_async_action = action
	_active_async_started_at = Time.get_ticks_msec() / 1000.0
	active_command_id = command_id
	# New state is installed before a synchronous listener can issue another command.
	_emit_async_completion(previous)


func _take_async_completion(ok: bool, status: String, detail: String) -> Dictionary:
	if _active_async_command_id.is_empty():
		return {}
	var elapsed := (Time.get_ticks_msec() / 1000.0) - _active_async_started_at
	var event := {
		"id": _active_async_command_id, "ok": ok, "status": status,
		"detail": "%s; action=%s; elapsed=%.2fs" % [detail, _active_async_action, maxf(0.0, elapsed)]
	}
	if active_command_id == _active_async_command_id:
		active_command_id = ""
	_active_async_command_id = ""
	_active_async_action = ""
	_active_async_started_at = 0.0
	return event


func _emit_async_completion(event: Dictionary) -> void:
	if not event.is_empty():
		emit_signal("si_command_completed", event.id, event.ok, event.status, event.detail)


func _complete_async_command(ok: bool, status: String, detail: String) -> void:
	_emit_async_completion(_take_async_completion(ok, status, detail))


func stop_motion(interrupt_current: bool = true, stop_audio: bool = false) -> void:
	if interrupt_current:
		_record_navigation_result(false, "interrupted")
	var was_planned_route := _route_planning_active
	var terminal := _take_async_completion(false, "interrupted", "motion interrupted") if interrupt_current else {}
	_has_target = false
	_following_user = false
	_follow_target = null
	_using_navigation = false
	_is_in_navigation_fallback = false
	_navigation_fallback_timer = 0.0
	_target_node = null
	_route_positions.clear()
	_route_final_node = null
	_route_params = {}
	_route_goal_position = Vector3.ZERO
	_route_target_instance_id = 0
	_route_blocked = false
	_route_retry_after = 0.0
	_route_planning_active = false
	navigation_detour_active = false
	_route_force_direct = false
	_pending_sit_node = null
	_pending_sit_params = {}
	_is_avoiding = false
	_movement_started_at = 0.0
	_movement_timeout_seconds = 0.0
	_movement_stall_timer = 0.0
	_last_progress_position = global_position
	_temporary_posture_until = -1.0
	velocity = Vector3.ZERO
	current_posture = "idle"
	current_goal = ""
	movement_mode = "idle"
	target_node_name = ""
	target_distance = 0.0
	looking_at_user = false
	look_target_name = ""
	look_attention_state = "none"
	look_hold_remaining = 0.0
	_look_target_is_user = false
	_look_hold_until = -1.0
	if stop_audio and _vrm_adapter and _vrm_adapter.has_method("stop_tts_audio"):
		_vrm_adapter.call("stop_tts_audio")
	if _vrm_adapter and _vrm_adapter.has_method("clear_posture_state"):
		_vrm_adapter.call("clear_posture_state", true)
	_set_motion_state("Idle")
	active_command_id = ""
	if interrupt_current and was_planned_route:
		navigation_last_plan_status = "interrupted"
	_emit_async_completion(terminal)


func _start_planned_move(position: Vector3, params: Dictionary, target_node: Node3D) -> bool:
	# Rejection must leave the previously accepted command and route untouched.
	if not position.is_finite():
		return false
	var goal := _clamp_stage_position(position)
	goal.y = global_position.y
	var obstacles := _collect_route_obstacles(target_node)
	var result: Dictionary
	if bool(params.get("portable_route", false)):
		result = _validate_portable_waypoint_route(goal, params, obstacles)
	else:
		result = _plan_route_with_obstacles(goal, obstacles)
	if not bool(result.get("ok", false)):
		if not _has_target:
			navigation_last_plan_status = "blocked:%s" % String(result.get("status", "no_path"))
		return false
	_record_navigation_result(false, "interrupted")
	_route_goal_position = goal
	_route_final_node = target_node
	_route_target_instance_id = target_node.get_instance_id() if is_instance_valid(target_node) else 0
	_route_target_transform = target_node.global_transform if is_instance_valid(target_node) else Transform3D.IDENTITY
	_route_params = params.duplicate(true)
	_pending_sit_node = null
	_pending_sit_params = {}
	_following_user = false
	_follow_target = null
	_route_positions.clear()
	_route_planning_active = true
	_route_blocked = false
	_route_retry_after = 0.0
	navigation_detour_active = false
	_route_force_direct = false
	navigation_replan_count = 0
	_route_failed_attempts = 0
	route_obstacle_count = obstacles.size()
	_movement_started_at = 0.0
	return _install_planned_route(result)


func _validate_portable_waypoint_route(goal: Vector3, params: Dictionary, obstacles: Array) -> Dictionary:
	var effective_clearance := _route_clearance_for_params(params)
	if effective_clearance < 0.0:
		return {"ok": false, "status": "portable_clearance_invalid"}
	if not _portable_point_within_stage_bounds(
		Vector2(global_position.x, global_position.z), effective_clearance
	):
		return {"ok": false, "status": "portable_start_out_of_bounds"}
	var raw_value: Variant = params.get("portable_waypoints", null)
	if not (raw_value is Array):
		return {"ok": false, "status": "portable_waypoints_missing"}
	var raw_waypoints: Array = raw_value
	if raw_waypoints.is_empty() or raw_waypoints.size() > 512:
		return {"ok": false, "status": "portable_waypoints_count"}
	var waypoints: Array[Vector3] = []
	for index in range(raw_waypoints.size()):
		var raw_waypoint: Variant = raw_waypoints[index]
		if not (raw_waypoint is Dictionary):
			return {"ok": false, "status": "portable_waypoint_%d_invalid" % index}
		var waypoint_dict: Dictionary = raw_waypoint
		for axis in ["x", "y", "z"]:
			var component: Variant = waypoint_dict.get(axis, null)
			if typeof(component) not in [TYPE_INT, TYPE_FLOAT]:
				return {"ok": false, "status": "portable_waypoint_%d_invalid" % index}
		var waypoint := Vector3(
			float(waypoint_dict["x"]),
			float(waypoint_dict["y"]),
			float(waypoint_dict["z"])
		)
		if not waypoint.is_finite():
			return {"ok": false, "status": "portable_waypoint_%d_invalid" % index}
		if not _portable_point_within_stage_bounds(
			Vector2(waypoint.x, waypoint.z), effective_clearance
		):
			return {"ok": false, "status": "portable_waypoint_%d_out_of_bounds" % index}
		waypoint.y = global_position.y
		waypoints.append(waypoint)
	if Vector2(waypoints[-1].x - goal.x, waypoints[-1].z - goal.z).length() > 0.04:
		return {"ok": false, "status": "portable_final_waypoint_mismatch"}

	var normalized: Dictionary = ObstacleRoutePlanner._normalize_obstacles(
		obstacles, effective_clearance
	)
	if not bool(normalized.get("ok", false)):
		return {"ok": false, "status": "portable_obstacles_invalid"}
	var previous := Vector2(global_position.x, global_position.z)
	var path_length := 0.0
	for index in range(waypoints.size()):
		var waypoint := waypoints[index]
		var endpoint := Vector2(waypoint.x, waypoint.z)
		if not ObstacleRoutePlanner._segment_is_clear(previous, endpoint, normalized.obstacles):
			return {"ok": false, "status": "portable_segment_%d_blocked" % index}
		path_length += previous.distance_to(endpoint)
		previous = endpoint
	return {
		"ok": true,
		"status": "portable_waypoints_validated",
		"waypoints": waypoints,
		"path_length": path_length,
	}


func _route_clearance_for_params(params: Dictionary) -> float:
	var footprint_value: Variant = params.get("portable_footprint_radius_m", route_planner_clearance)
	if typeof(footprint_value) not in [TYPE_INT, TYPE_FLOAT]:
		return -1.0
	var footprint := float(footprint_value)
	if not is_finite(footprint) or footprint < 0.01 or footprint > 5.0:
		return -1.0
	var extra_value: Variant = params.get("portable_minimum_clearance_m", 0.0)
	if typeof(extra_value) not in [TYPE_INT, TYPE_FLOAT]:
		return -1.0
	var extra := float(extra_value)
	if not is_finite(extra) or extra < 0.0 or extra > 10.0:
		return -1.0
	return maxf(maxf(0.0, route_planner_clearance), footprint) + extra


func _portable_point_within_stage_bounds(point: Vector2, clearance: float) -> bool:
	# Stage bounds describe physical world limits.  Portable waypoints describe
	# the avatar centre, so require its footprint and requested free margin to
	# remain strictly inside every edge (boundary contact fails closed).
	return (
		point.x > stage_min_x + clearance
		and point.x < stage_max_x - clearance
		and point.y > stage_min_z + clearance
		and point.y < stage_max_z - clearance
	)


func _active_route_clearance() -> float:
	if bool(_route_params.get("portable_route", false)):
		var portable_clearance := _route_clearance_for_params(_route_params)
		if portable_clearance >= 0.0:
			return portable_clearance
	return maxf(0.0, route_planner_clearance)


func _record_stationary_route_plan(position: Vector3, target_node: Node3D) -> bool:
	_route_goal_position = _clamp_stage_position(position)
	_route_goal_position.y = global_position.y
	_route_final_node = target_node if target_node is Node3D else null
	_route_params = {}
	_route_positions.clear()
	_route_planning_active = true
	navigation_detour_active = false
	_route_force_direct = false
	navigation_last_plan_status = "planning"
	navigation_detour_waypoint_count = 0
	navigation_replan_count = 0
	navigation_planned_path_length = 0.0
	var result := _request_route_plan(_route_goal_position, _route_final_node)
	if not bool(result.get("ok", false)) and route_obstacle_count > 0:
		navigation_last_plan_status = "blocked:%s" % String(result.get("status", "no_path"))
		_route_planning_active = false
		navigation_detour_active = false
		_route_final_node = null
		return false
	var stationary_waypoints: Array = result.get("waypoints", [])
	navigation_detour_waypoint_count = stationary_waypoints.size()
	navigation_planned_path_length = maxf(0.0, float(result.get("path_length", 0.0)))
	navigation_last_plan_status = "completed:%s" % String(result.get("status", "already_in_range"))
	_route_planning_active = false
	navigation_detour_active = false
	_route_final_node = null
	_route_goal_position = Vector3.ZERO
	return true


func _request_route_plan(goal: Vector3, target_node: Node3D) -> Dictionary:
	var obstacles := _collect_route_obstacles(target_node)
	route_obstacle_count = obstacles.size()
	return _plan_route_with_obstacles(goal, obstacles, _active_route_clearance())


func _plan_route_with_obstacles(goal: Vector3, obstacles: Array, clearance: float = -1.0) -> Dictionary:
	var bounds := Rect2(
		Vector2(stage_min_x, stage_min_z),
		Vector2(maxf(0.1, stage_max_x - stage_min_x), maxf(0.1, stage_max_z - stage_min_z))
	)
	var effective_clearance := maxf(0.0, route_planner_clearance) if clearance < 0.0 else clearance
	return ObstacleRoutePlanner.plan_route(
		global_position,
		goal,
		bounds,
		obstacles,
		maxf(0.1, route_planner_cell_size),
		maxf(0.0, effective_clearance),
		maxi(1, route_planner_max_waypoints)
	)


func _install_planned_route(result: Dictionary) -> bool:
	_route_positions.clear()
	var raw_waypoints: Array = result.get("waypoints", [])
	for raw_waypoint in raw_waypoints:
		if typeof(raw_waypoint) != TYPE_VECTOR3:
			continue
		var waypoint: Vector3 = _clamp_stage_position(raw_waypoint)
		waypoint.y = global_position.y
		if waypoint.distance_to(global_position) <= 0.04:
			continue
		if waypoint.distance_to(_route_goal_position) <= 0.04:
			continue
		_route_positions.append(waypoint)
	navigation_detour_active = not _route_positions.is_empty()
	navigation_detour_waypoint_count = _route_positions.size()
	_route_positions.append(_route_goal_position)
	navigation_planned_path_length = maxf(0.0, float(result.get("path_length", 0.0)))
	navigation_last_plan_status = String(result.get("status", "planned"))
	return _start_next_route_segment()


func _start_next_route_segment() -> bool:
	if _route_positions.is_empty():
		return false
	var final_target_name: String = _route_final_node.name if _route_final_node and is_instance_valid(_route_final_node) else ""
	var next_position: Vector3 = _route_positions.pop_front()
	var final_node: Node3D = _route_final_node if _route_positions.is_empty() else null
	var started := _set_move_target(next_position, _following_user, _route_params, final_node)
	if started:
		if not final_target_name.is_empty():
			target_node_name = final_target_name
		if _pending_sit_node != null:
			current_goal = "sit_to_seat"
	return started


func _attempt_route_replan(reason: String) -> bool:
	if not _route_planning_active:
		return false
	if _route_failed_attempts >= maxi(0, route_replan_max_attempts):
		_abort_unfinished_motion(target_distance, "route replan exhausted")
		return true
	navigation_replan_count += 1
	var result := _request_route_plan(_route_goal_position, _route_final_node)
	if bool(result.get("ok", false)):
		_route_blocked = false
		_route_failed_attempts = 0
		if _install_planned_route(result):
			navigation_last_plan_status = "replanned:%s:%s" % [reason, String(result.get("status", "planned"))]
			return true
	_route_blocked = true
	_route_failed_attempts += 1
	_route_retry_after = Time.get_ticks_msec() / 1000.0 + 0.3
	navigation_last_plan_status = "replan_failed:%s:%s" % [reason, String(result.get("status", "no_path"))]
	movement_mode = "blocked"
	velocity = Vector3.ZERO
	_set_motion_state("Idle")
	if _route_failed_attempts >= maxi(0, route_replan_max_attempts):
		_abort_unfinished_motion(target_distance, "route replan exhausted")
	return true


func _current_route_segment_clear(goal: Vector3) -> bool:
	var obstacles := _collect_route_obstacles(_route_final_node if is_instance_valid(_route_final_node) else null)
	route_obstacle_count = obstacles.size()
	var normalized: Dictionary = ObstacleRoutePlanner._normalize_obstacles(obstacles, _active_route_clearance())
	if not bool(normalized.get("ok", false)):
		return false
	var start := Vector2(global_position.x, global_position.z)
	var end := Vector2(goal.x, goal.z)
	if bool(_route_params.get("portable_route", false)):
		var clearance := _active_route_clearance()
		if (
			not _portable_point_within_stage_bounds(start, clearance)
			or not _portable_point_within_stage_bounds(end, clearance)
		):
			return false
	return ObstacleRoutePlanner._segment_is_clear(start, end, normalized.obstacles)


func _validate_active_route() -> bool:
	if not _route_planning_active:
		return true
	if _route_target_instance_id != 0:
		if not is_instance_valid(_route_final_node) or _route_final_node.is_queued_for_deletion() or _route_final_node.get_instance_id() != _route_target_instance_id:
			_abort_unfinished_motion(target_distance, "target unavailable")
			return false
		var live_transform := _route_final_node.global_transform
		if not live_transform.is_equal_approx(_route_target_transform):
			_route_target_transform = live_transform
			var goal := live_transform.origin
			if not _following_user:
				goal = _resolve_addressable_target_position(goal, _route_final_node, _stop_distance)
				goal = _resolve_reachable_addressable_target_position(goal, _route_final_node, _stop_distance)
			goal.y = global_position.y
			_route_goal_position = _clamp_stage_position(goal)
			_attempt_route_replan("target_moved")
			return false
	if _route_blocked:
		if Time.get_ticks_msec() / 1000.0 >= _route_retry_after:
			_attempt_route_replan("blocked_retry")
		return false
	# Always read current obstacles, including additions after an empty snapshot.
	if not _current_route_segment_clear(_target_position):
		_attempt_route_replan("segment_changed")
		return false
	return true


func _arrive_at_segment(detail: String) -> void:
	if not _validate_active_route() or not _has_target:
		return
	var displacement := _target_position - global_position
	displacement.y = 0.0
	if displacement.length() > minf(_stop_distance, route_arrival_epsilon) or not _current_route_segment_clear(_target_position):
		return
	# Collision geometry can exist without semantic/navigation metadata.
	if test_move(global_transform, displacement):
		_attempt_route_replan("arrival_collision")
		return
	# Arrival never teleports the body or its eye cameras.
	_finish_target_arrival(detail)


func _collect_route_obstacles(excluded_target: Node3D) -> Array:
	var obstacles: Array = []
	var seen: Dictionary = {}
	# Physical actors are dynamic obstacles even without furniture metadata.
	# Read their current collision bounds each tick, not a fixed social radius.
	for raw_actor in get_tree().get_nodes_in_group(player_group_name):
		if raw_actor is Node3D and raw_actor != self and not _is_route_target_node(raw_actor, excluded_target):
			seen[raw_actor.get_instance_id()] = true
			obstacles.append_array(_actor_route_obstacles(raw_actor))
	# Semantic/addressable markers can be nonsolid; only tagged world geometry
	# participates below, in addition to the actual actor shapes above.
	for group_name in ["si_navigation_obstacle"]:
		for raw_node in get_tree().get_nodes_in_group(group_name):
			if not (raw_node is Node3D):
				continue
			var node := raw_node as Node3D
			var instance_id := node.get_instance_id()
			if seen.has(instance_id):
				continue
			if node == self or _is_route_target_node(node, excluded_target):
				continue

			var height := _route_obstacle_height(node)
			var solid_geometry := bool(node.get_meta("si_navigation_solid_geometry", false))
			if not solid_geometry and height < maxf(0.01, route_obstacle_min_height):
				continue
			var center_y := _route_obstacle_center_y(node, height)
			var bottom_y := center_y - height * 0.5
			var top_y := center_y + height * 0.5
			var avatar_floor_y := global_position.y
			if not solid_geometry and bottom_y > avatar_floor_y + maxf(0.0, route_obstacle_floor_tolerance):
				continue
			if top_y < avatar_floor_y - 0.12 or bottom_y > avatar_floor_y + maxf(0.1, route_agent_height):
				continue

			var center_position := node.global_position
			if node.has_meta("si_navigation_center_offset"):
				var raw_offset: Variant = node.get_meta("si_navigation_center_offset")
				if typeof(raw_offset) == TYPE_VECTOR3:
					center_position = node.to_global(raw_offset)
			var center := Vector2(center_position.x, center_position.z)
			var half_extents := _route_obstacle_half_extents(node)
			if half_extents.x > 0.0 and half_extents.y > 0.0:
				var basis := node.global_transform.basis
				var world_half_extents := Vector2(
					absf(basis.x.x) * half_extents.x + absf(basis.z.x) * half_extents.y,
					absf(basis.x.z) * half_extents.x + absf(basis.z.z) * half_extents.y
				)
				if world_half_extents.x > 0.01 and world_half_extents.y > 0.01:
					seen[instance_id] = true
					obstacles.append({
						"kind": "rect",
						"center": center,
						"half_extents": world_half_extents
					})
					continue

			var radius := _route_obstacle_radius(node)
			if radius > 0.01:
				seen[instance_id] = true
				obstacles.append({"kind": "circle", "center": center, "radius": radius})

	# A different Godot world cannot be expected to carry Lumina-specific
	# semantic groups. Discover blocking PhysicsBody3D shapes independently of
	# node names and metadata, while retaining the tagged shapes above for
	# nonsolid/manual navigation geometry. The scan is cached so route validation
	# can call this method every physics tick without traversing the scene tree at
	# frame rate.
	for raw_physical in _collect_automatic_physics_obstacles():
		if not (raw_physical is Dictionary):
			continue
		var physical: Dictionary = raw_physical
		var owner_value: Variant = physical.get("_owner", null)
		if not (owner_value is Node3D) or not is_instance_valid(owner_value):
			continue
		var owner := owner_value as Node3D
		if _is_route_target_node(owner, excluded_target) or _physical_owner_is_seen(owner, seen):
			continue
		obstacles.append({
			"kind": physical.get("kind", "rect"),
			"center": physical.get("center", Vector2.ZERO),
			"half_extents": physical.get("half_extents", Vector2.ZERO),
		})
	return obstacles


func _physical_owner_is_seen(owner: Node, seen: Dictionary) -> bool:
	var cursor: Node = owner
	while cursor != null:
		if seen.has(cursor.get_instance_id()):
			return true
		cursor = cursor.get_parent()
	return false


func _collect_automatic_physics_obstacles() -> Array:
	var now := Time.get_ticks_msec() / 1000.0
	if now - _automatic_physics_obstacle_cache_at < _AUTOMATIC_PHYSICS_OBSTACLE_REFRESH_SECONDS:
		return _automatic_physics_obstacle_cache.duplicate(true)
	var discovered: Array = []
	var tree := get_tree()
	if tree == null:
		return discovered
	var scan_root: Node = tree.current_scene
	if scan_root == null:
		scan_root = tree.root
	if scan_root == null:
		return discovered
	for raw_shape in scan_root.find_children("*", "CollisionShape3D", true, false):
		if discovered.size() >= _AUTOMATIC_PHYSICS_OBSTACLE_LIMIT:
			break
		var collision := raw_shape as CollisionShape3D
		if collision == null or collision.disabled or collision.shape == null:
			continue
		var cursor: Node = collision.get_parent()
		while cursor != null and not (cursor is CollisionObject3D):
			cursor = cursor.get_parent()
		if not (cursor is PhysicsBody3D):
			# Area3D trigger volumes are not motion blockers.
			continue
		var body := cursor as PhysicsBody3D
		if body == self or self.is_ancestor_of(body):
			continue
		if body.get_world_3d() != get_world_3d():
			continue
		if (body.collision_layer & collision_mask) == 0:
			continue
		var debug_mesh := collision.shape.get_debug_mesh()
		if debug_mesh == null:
			continue
		var bounds: AABB = collision.global_transform * debug_mesh.get_aabb()
		if bounds.size.x <= 0.01 or bounds.size.z <= 0.01:
			continue
		var avatar_floor_y := global_position.y
		# Do not turn the supporting floor or overhead ceiling into a wall.
		if bounds.end.y <= avatar_floor_y + maxf(0.01, route_obstacle_min_height):
			continue
		if bounds.position.y >= avatar_floor_y + maxf(0.1, route_agent_height):
			continue
		var center := Vector2(bounds.get_center().x, bounds.get_center().z)
		var half := Vector2(bounds.size.x, bounds.size.z) * 0.5
		discovered.append({
			"kind": "rect",
			"center": center,
			"half_extents": half,
			"_owner": body,
		})
	_automatic_physics_obstacle_cache = discovered
	_automatic_physics_obstacle_cache_at = now
	return discovered.duplicate(true)


func _actor_route_obstacles(actor: Node3D) -> Array:
	var result: Array = []
	if not is_instance_valid(actor) or actor.is_queued_for_deletion():
		return result
	for raw_shape in actor.find_children("*", "CollisionShape3D", true, false):
		var collision := raw_shape as CollisionShape3D
		if collision.disabled or collision.shape == null:
			continue
		var body := collision.get_parent() as CollisionObject3D
		if body == null or (body.collision_layer & collision_mask) == 0:
			continue
		var debug_mesh := collision.shape.get_debug_mesh()
		if debug_mesh == null:
			continue
		var bounds: AABB = collision.global_transform * debug_mesh.get_aabb()
		if bounds.end.y < global_position.y - 0.12 or bounds.position.y > global_position.y + route_agent_height:
			continue
		var center := Vector2(bounds.get_center().x, bounds.get_center().z)
		var half := Vector2(bounds.size.x, bounds.size.z) * 0.5
		if collision.shape is CapsuleShape3D or collision.shape is SphereShape3D:
			result.append({"kind": "circle", "center": center, "radius": maxf(half.x, half.y)})
		elif half.x > 0.0 and half.y > 0.0:
			result.append({"kind": "rect", "center": center, "half_extents": half})
	return result


func _is_route_target_node(candidate: Node3D, target: Node3D) -> bool:
	if not target or not is_instance_valid(target):
		return false
	return candidate == target or candidate.is_ancestor_of(target) or target.is_ancestor_of(candidate)


func _route_obstacle_half_extents(node: Node3D) -> Vector2:
	for key in ["si_navigation_half_extents", "si_obstacle_half_extents", "si_half_extents", "half_extents"]:
		if not node.has_meta(key):
			continue
		var raw: Variant = node.get_meta(key)
		if typeof(raw) == TYPE_VECTOR2:
			return Vector2(absf(raw.x), absf(raw.y))
		if typeof(raw) == TYPE_VECTOR3:
			return Vector2(absf(raw.x), absf(raw.z))
		if typeof(raw) == TYPE_ARRAY and raw.size() >= 2:
			return Vector2(absf(float(raw[0])), absf(float(raw[1])))
	return Vector2.ZERO


func _route_obstacle_height(node: Node3D) -> float:
	for key in ["si_navigation_height", "si_obstacle_height", "si_addressable_height", "height"]:
		if node.has_meta(key):
			return maxf(0.0, _meta_float(node, key, 0.0))
	if node.is_in_group("si_addressable") and _addressable_radius(node) > 0.0:
		return 1.0
	return 0.0


func _route_obstacle_center_y(node: Node3D, height: float) -> float:
	for key in ["si_navigation_center_y", "si_obstacle_center_y"]:
		if node.has_meta(key):
			return _meta_float(node, key, node.global_position.y + height * 0.5)
	# Legacy addressable nodes store their transform at the base of the prop.
	return node.global_position.y + height * 0.5


func _route_obstacle_radius(node: Node3D) -> float:
	for key in ["si_navigation_radius", "si_obstacle_radius", "si_addressable_radius"]:
		if node.has_meta(key):
			var radius := _meta_float(node, key, 0.0)
			if radius > 0.0:
				return radius * maxf(node.global_transform.basis.x.length(), node.global_transform.basis.z.length())
	if node.is_in_group("si_addressable"):
		return _addressable_radius(node)
	return 0.0


func _set_move_target(position: Vector3, keep_follow: bool, params: Dictionary = {}, target_node: Node3D = null) -> bool:
	if _vrm_adapter and _vrm_adapter.has_method("clear_posture_state"):
		_vrm_adapter.call("clear_posture_state", true)
	current_posture = "follow" if keep_follow else "walk"
	_target_position = _clamp_stage_position(position)
	_target_position.y = global_position.y
	_walk_speed = float(params.get("walk_speed", default_walk_speed))
	_stop_distance = maxf(0.05, float(params.get("stop_distance", default_stop_distance)))
	_target_node = target_node if target_node is Node3D else null
	current_goal = "follow_user" if keep_follow else "move"
	var can_use_navigation := _can_use_navigation() and not (_route_planning_active and _route_force_direct)
	movement_mode = "navigation" if can_use_navigation else "direct"
	target_node_name = _target_node.name if _target_node else ""
	target_distance = global_position.distance_to(_target_position)
	_last_target_distance = target_distance
	_movement_stall_timer = 0.0
	_last_progress_position = global_position
	if _movement_started_at <= 0.0:
		_movement_started_at = Time.get_ticks_msec() / 1000.0
		_movement_timeout_seconds = maxf(
			movement_min_timeout_seconds,
			(maxf(target_distance, navigation_planned_path_length) / maxf(0.1, _walk_speed)) + movement_timeout_extra_seconds
		)
	_is_in_navigation_fallback = false
	_is_avoiding = false
	if not keep_follow:
		_following_user = false
		_follow_target = null

	if can_use_navigation:
		navigation_ready = true
		_agent.path_desired_distance = 0.35
		_agent.target_desired_distance = minf(_stop_distance, route_arrival_epsilon)
		_agent.target_position = _target_position
		_using_navigation = true
		_navigation_fallback_timer = _NAVIGATION_WATCHDOG_SECONDS
	else:
		# A planner-approved segment is direct movement, not an unsafe fallback.
		_is_in_navigation_fallback = not _route_planning_active
		_using_navigation = false
		if _agent:
			_agent.target_position = _target_position
			_agent.target_desired_distance = minf(_stop_distance, route_arrival_epsilon)
			navigation_ready = true
		else:
			navigation_ready = false

	_clear_look_target()
	_has_target = true
	_set_motion_state("Walk")
	return true


func _can_use_navigation() -> bool:
	if not _agent:
		return false
	if not _agent.has_method("get_navigation_map"):
		return true
	return _agent.get_navigation_map() != RID()


func _tick_navigation(delta: float) -> void:
	if not _validate_active_route():
		return
	if not _agent:
		_activate_navigation_fallback()
		return

	target_distance = global_position.distance_to(_target_position)
	if _movement_timed_out(target_distance):
		return
	if target_distance <= minf(_stop_distance, route_arrival_epsilon):
		_arrive_at_segment("target reached")
		return

	if _record_actual_movement(delta) and _movement_stall_timer >= maxf(0.2, route_replan_stall_seconds):
		if _route_planning_active:
			_attempt_route_replan("navigation_stall")
		else:
			_activate_navigation_fallback()
		return

	if _agent.is_navigation_finished():
		_navigation_fallback_timer -= delta
		if _navigation_fallback_timer <= 0.0:
			_activate_navigation_fallback()
		else:
			velocity = Vector3.ZERO
			_set_motion_state("Idle")
		return

	if _agent.has_method("is_target_reachable"):
		if not bool(_agent.call("is_target_reachable")):
			_navigation_fallback_timer -= delta
			if _navigation_fallback_timer <= 0.0:
				_activate_navigation_fallback()
			else:
				velocity = Vector3.ZERO
				_set_motion_state("Idle")
			return

	var next_position := _agent.get_next_path_position()
	var direction := next_position - global_position
	direction.y = 0.0
	movement_mode = "navigation"
	if direction.length() < 0.05:
		_navigation_fallback_timer -= delta
		if _navigation_fallback_timer <= 0.0:
			_activate_navigation_fallback()
			return
		_is_avoiding = false
		_set_movement_posture()
		velocity = Vector3.ZERO
	else:
		_navigation_fallback_timer = _NAVIGATION_WATCHDOG_SECONDS
		var steer_direction := _apply_obstacle_avoidance(direction.normalized())
		if steer_direction.length() <= 0.001:
			velocity = Vector3.ZERO
			_set_motion_state("Idle")
			return
		_set_movement_posture()
		velocity = steer_direction * minf(_walk_speed, target_distance / maxf(delta, 0.001))
		_face_direction(steer_direction, delta)

	if _route_planning_active and not _current_route_segment_clear(global_position + velocity * delta):
		velocity = Vector3.ZERO
		_attempt_route_replan("steering_segment_blocked")
		return
	move_and_slide()
	global_position = _clamp_stage_position(global_position)
	_set_motion_state("Walk")


func _activate_navigation_fallback() -> void:
	if _route_planning_active:
		# Never revert to a straight-line fallback in an obstacle-bearing world.
		_route_force_direct = true
		_attempt_route_replan("navigation_unavailable")
		return
	_using_navigation = false
	_is_in_navigation_fallback = true
	_is_avoiding = false
	_navigation_fallback_timer = 0.0
	_movement_stall_timer = 0.0
	_last_target_distance = target_distance
	movement_mode = "direct_fallback"
	# Keep target but move with direct movement until it arrives.


func _record_actual_movement(delta: float) -> bool:
	var displacement := global_position - _last_progress_position
	displacement.y = 0.0
	if displacement.length() >= maxf(0.005, route_progress_distance):
		_last_progress_position = global_position
		_movement_stall_timer = 0.0
		return false
	_movement_stall_timer += delta
	return true


func _set_movement_posture() -> void:
	if current_posture.begins_with("gesture") or current_posture in ["sit", "stand"]:
		return
	if _following_user:
		current_posture = "follow_avoid" if _is_avoiding else "follow"
	else:
		current_posture = "walk_avoid" if _is_avoiding else "walk"


func _is_motion_active() -> bool:
	return _has_target or _following_user or movement_mode in ["navigation", "direct", "direct_fallback", "follow_hold"]


func _resolve_addressable_target_position(original_position: Vector3, target_node: Node3D, stop_distance: float) -> Vector3:
	if not target_node or stop_distance <= 0.0:
		return _clamp_stage_position(original_position)
	if not _is_addressable_furniture(target_node):
		return _clamp_stage_position(original_position)
	var furniture_radius := _addressable_radius(target_node)
	if furniture_radius <= 0.0:
		return _clamp_stage_position(original_position)
	var to_target := original_position - global_position
	to_target.y = 0.0
	if to_target.length() <= 0.001:
		return _clamp_stage_position(original_position)
	var approach_distance := _addressable_approach_distance(target_node, Vector2(-to_target.x, -to_target.z), stop_distance)
	return _clamp_stage_position(original_position - to_target.normalized() * approach_distance)


func _addressable_approach_distance(target: Node3D, outward: Vector2, stop_distance: float) -> float:
	var half: Variant = target.get_meta("si_addressable_half_extents", Vector2.ZERO)
	if half is Vector2 and half.x > 0.0 and half.y > 0.0 and outward.length() > 0.001:
		var direction := outward.normalized().abs()
		# Ray/expanded rectangle intersection: clearance is from the actual near
		# surface, not a circle based on the sofa's much larger width.
		var expanded: Vector2 = half + Vector2.ONE * stop_distance
		return minf(expanded.x / maxf(direction.x, 0.000001), expanded.y / maxf(direction.y, 0.000001))
	return maxf(0.0, _addressable_radius(target)) + stop_distance


func _resolve_reachable_addressable_target_position(initial_position: Vector3, target_node: Node3D, stop_distance: float) -> Vector3:
	if not target_node or not _is_addressable_furniture(target_node):
		return initial_position
	var obstacles := _collect_route_obstacles(target_node)
	if obstacles.is_empty():
		return initial_position
	var target_position := target_node.global_position
	target_position.y = global_position.y
	var initial_plan := _plan_route_with_obstacles(initial_position, obstacles)
	if (
		bool(initial_plan.get("ok", false))
		and _route_candidate_can_view_target(initial_position, target_position, obstacles)
	):
		return initial_position

	var outward := Vector2(global_position.x - target_position.x, global_position.z - target_position.z)
	if outward.length() <= 0.001:
		outward = Vector2(0.0, -1.0)
	else:
		outward = outward.normalized()
	var candidate_directions: Array[Vector2] = [
		outward,
		outward.rotated(PI * 0.25),
		outward.rotated(-PI * 0.25),
		outward.rotated(PI * 0.5),
		outward.rotated(-PI * 0.5),
		outward.rotated(PI * 0.75),
		outward.rotated(-PI * 0.75),
		-outward,
	]
	var clearance_step := maxf(0.2, route_planner_clearance)
	var candidate_expansions: Array[float] = [0.0, clearance_step, clearance_step * 2.0, clearance_step * 4.0, clearance_step * 6.0, clearance_step * 8.0]
	for extra_clearance in candidate_expansions:
		var found_on_ring := false
		var best_candidate := initial_position
		var best_path_length := INF
		for direction in candidate_directions:
			var candidate_distance := _addressable_approach_distance(target_node, direction, stop_distance + extra_clearance)
			var candidate := Vector3(
				target_position.x + direction.x * candidate_distance,
				global_position.y,
				target_position.z + direction.y * candidate_distance
			)
			candidate = _clamp_stage_position(candidate)
			var candidate_2d := Vector2(candidate.x, candidate.z)
			if _route_point_is_blocked(candidate_2d, obstacles, route_planner_clearance):
				continue
			if not _route_candidate_can_view_target(candidate, target_position, obstacles):
				continue
			var plan := _plan_route_with_obstacles(candidate, obstacles)
			if not bool(plan.get("ok", false)):
				continue
			var path_length := float(plan.get("path_length", INF))
			if not found_on_ring or path_length < best_path_length:
				found_on_ring = true
				best_candidate = candidate
				best_path_length = path_length
		if found_on_ring:
			return best_candidate
	return initial_position


func _route_candidate_can_view_target(candidate: Vector3, target: Vector3, obstacles: Array) -> bool:
	var candidate_2d := Vector2(candidate.x, candidate.z)
	var target_2d := Vector2(target.x, target.z)
	if candidate_2d.distance_squared_to(target_2d) <= 0.0025:
		return false
	for raw_obstacle in obstacles:
		var obstacle: Dictionary = raw_obstacle
		# Geometry containing the semantic target is the object being approached;
		# it may terminate the sightline, but unrelated furniture must not occlude it.
		if _route_point_blocked_by_obstacle(target_2d, obstacle, 0.0):
			continue
		if _route_segment_hits_obstacle(candidate_2d, target_2d, obstacle):
			return false
	return true


func _route_point_is_blocked(point: Vector2, obstacles: Array, clearance: float) -> bool:
	for raw_obstacle in obstacles:
		if _route_point_blocked_by_obstacle(point, raw_obstacle as Dictionary, clearance):
			return true
	return false


func _route_point_blocked_by_obstacle(point: Vector2, obstacle: Dictionary, clearance: float) -> bool:
	var center: Vector2 = obstacle.get("center", Vector2.ZERO)
	if String(obstacle.get("kind", "")) == "rect":
		var half_extents: Vector2 = obstacle.get("half_extents", Vector2.ZERO)
		half_extents += Vector2.ONE * maxf(0.0, clearance)
		var delta := point - center
		return absf(delta.x) <= half_extents.x + 0.0001 and absf(delta.y) <= half_extents.y + 0.0001
	var radius := maxf(0.0, float(obstacle.get("radius", 0.0)) + clearance)
	return point.distance_squared_to(center) <= radius * radius + 0.0001


func _route_segment_hits_obstacle(start: Vector2, goal: Vector2, obstacle: Dictionary) -> bool:
	var center: Vector2 = obstacle.get("center", Vector2.ZERO)
	if String(obstacle.get("kind", "")) == "rect":
		var half_extents: Vector2 = obstacle.get("half_extents", Vector2.ZERO)
		return _route_segment_intersects_rect(start, goal, center - half_extents, center + half_extents)
	var radius := maxf(0.0, float(obstacle.get("radius", 0.0)))
	return _route_distance_squared_to_segment(center, start, goal) <= radius * radius + 0.0001


func _route_segment_intersects_rect(start: Vector2, goal: Vector2, minimum: Vector2, maximum: Vector2) -> bool:
	var direction := goal - start
	var t_min := 0.0
	var t_max := 1.0
	for axis in range(2):
		var origin := start.x if axis == 0 else start.y
		var delta := direction.x if axis == 0 else direction.y
		var slab_min := minimum.x if axis == 0 else minimum.y
		var slab_max := maximum.x if axis == 0 else maximum.y
		if absf(delta) <= 0.0001:
			if origin < slab_min - 0.0001 or origin > slab_max + 0.0001:
				return false
			continue
		var inverse_delta := 1.0 / delta
		var near_t := (slab_min - origin) * inverse_delta
		var far_t := (slab_max - origin) * inverse_delta
		if near_t > far_t:
			var swap := near_t
			near_t = far_t
			far_t = swap
		t_min = maxf(t_min, near_t)
		t_max = minf(t_max, far_t)
		if t_min > t_max + 0.0001:
			return false
	return true


func _route_distance_squared_to_segment(point: Vector2, start: Vector2, goal: Vector2) -> float:
	var segment := goal - start
	var length_squared := segment.length_squared()
	if length_squared <= 0.0001:
		return point.distance_squared_to(start)
	var fraction := clampf((point - start).dot(segment) / length_squared, 0.0, 1.0)
	return point.distance_squared_to(start + segment * fraction)


func _clamp_stage_position(position: Vector3) -> Vector3:
	return Vector3(
		clampf(position.x, stage_min_x, stage_max_x),
		position.y,
		clampf(position.z, stage_min_z, stage_max_z)
	)


func _snap_to_seat_pose(seat_node: Node3D, params: Dictionary = {}) -> void:
	if not seat_node or not is_instance_valid(seat_node):
		return
	var seat_anchor := _seat_anchor_position(seat_node, params)
	global_position = Vector3(seat_anchor.x, global_position.y, seat_anchor.z)
	var seat_forward := _seat_forward_direction(seat_node, params)
	if seat_forward.length() > 0.001:
		seat_forward.y = 0.0
		rotation.y = atan2(-seat_forward.x, -seat_forward.z)


func _seat_anchor_position(seat_node: Node3D, params: Dictionary = {}) -> Vector3:
	var local_offset := _seat_local_offset(seat_node, params)
	var seat_anchor := seat_node.to_global(local_offset)
	seat_anchor.y = global_position.y
	return seat_anchor


func _seat_local_offset(seat_node: Node3D, params: Dictionary = {}) -> Vector3:
	if params.has("seat_local_offset"):
		var explicit_offset := _dict_to_vector3(params.get("seat_local_offset", {}))
		return explicit_offset
	var object_type := String(seat_node.get_meta("si_type", "")).to_lower()
	match object_type:
		"chair":
			return Vector3(0.0, 0.0, 0.04)
		"sofa":
			return Vector3(0.0, 0.0, -0.08)
		_:
			return Vector3.ZERO


func _seat_forward_direction(seat_node: Node3D, params: Dictionary = {}) -> Vector3:
	if params.has("seat_forward"):
		var explicit_forward := _dict_to_vector3(params.get("seat_forward", {}))
		explicit_forward.y = 0.0
		if explicit_forward.length() > 0.001:
			return explicit_forward.normalized()
	var basis := seat_node.global_transform.basis
	var object_type := String(seat_node.get_meta("si_type", "")).to_lower()
	var forward := Vector3.ZERO
	match object_type:
		"chair":
			forward = basis.z
		"sofa":
			forward = -basis.z
		_:
			forward = basis.z
	forward.y = 0.0
	return forward.normalized() if forward.length() > 0.001 else Vector3.ZERO


func _apply_obstacle_avoidance(direction: Vector3) -> Vector3:
	if direction.length() <= 0.001:
		_is_avoiding = false
		return Vector3.ZERO

	_is_avoiding = false
	var steer := Vector3.ZERO
	var avoid_strength := 0.0
	var right_reference := Vector3.UP.cross(direction).normalized()
	if right_reference == Vector3.ZERO:
		right_reference = Vector3.RIGHT

	var obstacle_nodes: Array = []
	obstacle_nodes.append_array(get_tree().get_nodes_in_group("si_addressable"))
	obstacle_nodes.append_array(get_tree().get_nodes_in_group(player_group_name))
	for obstacle in obstacle_nodes:
		if not (obstacle is Node3D):
			continue
		var obstacle_node := obstacle as Node3D
		if obstacle_node == self:
			continue
		# Verified visual anchors are semantic proxies; their actual colliders
		# already participate in the fresh global plan and swept-step validation.
		if _route_planning_active and String(obstacle_node.get_meta("si_anchor_basis", "")) == "actual_imported_visual_bounds":
			continue
		if _route_planning_active and obstacle_node.is_in_group(player_group_name) and not _actor_route_obstacles(obstacle_node).is_empty():
			continue # The global physical route already clears this actor.
		if _target_node and obstacle_node == _target_node:
			continue
		if _following_user and _follow_target and obstacle_node == _follow_target:
			continue
		var radius := _movement_obstacle_radius(obstacle_node)
		if radius <= 0.0:
			continue
		var to_obstacle := obstacle_node.global_position - global_position
		to_obstacle.y = 0.0
		var distance_to_obstacle := to_obstacle.length()
		if distance_to_obstacle <= 0.001:
			continue
		var ahead := to_obstacle.dot(direction)
		if ahead <= 0.0:
			continue
		var clearance := radius + obstacle_avoidance_padding + _obstacle_clearance_radius()
		var effective_clearance := maxf(clearance, 0.05)
		if ahead > obstacle_avoidance_lookahead + effective_clearance:
			continue
		var lateral := to_obstacle - direction * ahead
		var lateral_distance := lateral.length()
		if lateral_distance > effective_clearance:
			continue

		var lateral_dir := -lateral
		if lateral_dir.length() <= 0.001:
			var side := direction.cross(to_obstacle).y
			if is_zero_approx(side):
				side = _avoidance_side_sign
			else:
				side = -sign(side)
			_avoidance_side_sign = side
			lateral_dir = right_reference * side
		lateral_dir = lateral_dir.normalized()
		var overlap := 1.0 - (lateral_distance / effective_clearance)
		var distance_scale := 1.0
		if distance_to_obstacle > 0.001:
			distance_scale = 1.0 - minf(1.0, (distance_to_obstacle - effective_clearance) / maxf(effective_clearance, 0.001))
			distance_scale = maxf(0.1, distance_scale)
		var add_weight := overlap * distance_scale
		steer += lateral_dir * add_weight
		avoid_strength += add_weight
		_is_avoiding = true

	if _is_avoiding:
		_set_movement_posture()
		var steering := direction * (1.0 - minf(1.0, avoid_strength))
		var extra := steer.normalized() * minf(1.0, avoid_strength * obstacle_avoidance_strength)
		var steer_direction := steering + extra
		if steer_direction.length() <= 0.001:
			var side_push := right_reference * _avoidance_side_sign
			steer_direction = (direction + side_push * 0.5).normalized()
		return steer_direction.normalized()

	_set_movement_posture()
	return direction


func _movement_obstacle_radius(node: Node3D) -> float:
	if node.is_in_group(player_group_name):
		return 0.62
	return _addressable_radius(node)


func _addressable_radius(node: Node3D) -> float:
	var metadata_radius := _meta_float(node, "si_addressable_radius", 0.0)
	if metadata_radius > 0.0:
		return metadata_radius
	if node.has_meta("si_addressable_radius"):
		metadata_radius = float(node.get_meta("si_addressable_radius"))
		if metadata_radius > 0.0:
			return metadata_radius

	var object_type := String(node.get_meta("si_type", "")).to_lower()
	match object_type:
		"table":
			return 0.8
		"chair":
			return 0.42
		"sofa":
			return 0.95
		"plant":
			return 0.35
		"bed":
			return 1.0
		"desk":
			return 0.7
		_:
			var mesh_node := node.find_child("Mesh", true, false)
			if mesh_node is MeshInstance3D:
				var mesh := mesh_node as MeshInstance3D
				if mesh.mesh:
					var box := mesh.mesh.get_aabb()
					return maxf(0.1, maxf(box.size.x * 0.5, box.size.z * 0.5))
			return 0.0


func _is_addressable_furniture(node: Node3D) -> bool:
	if not node.is_in_group("si_addressable"):
		return false
	if node.has_meta("si_can_approach"):
		return _meta_bool(node, "si_can_approach", false)
	var object_type := String(node.get_meta("si_type", "")).to_lower()
	match object_type:
		"table", "chair", "sofa", "plant", "bed", "desk":
			return true
	return false


func _route_positions_for_target(target_node: Node3D, _final_position: Vector3) -> Array[Vector3]:
	var route: Array[Vector3] = []
	if not target_node:
		return route
	var target_name := target_node.name.to_lower()
	var target_area := String(target_node.get_meta("si_area", "")).strip_edges().to_lower()
	var uses_left_reading_route := target_name in ["sidetable", "plant"] or target_area == "left_reading"
	if not uses_left_reading_route:
		return route
	var y := global_position.y
	if global_position.x > -2.55 or global_position.z > -1.35:
		route.append(Vector3(-2.8, y, -1.8))
	if global_position.x > -3.55 or global_position.z > -1.65:
		var left_waypoint := Vector3(-3.8, y, -1.8)
		if route.is_empty() or route[route.size() - 1].distance_to(left_waypoint) > 0.2:
			route.append(left_waypoint)
	return route


func _is_sittable_furniture(node: Node3D) -> bool:
	if not node:
		return false
	if node.has_meta("si_can_sit"):
		return _meta_bool(node, "si_can_sit", false)
	var object_type := String(node.get_meta("si_type", "")).to_lower()
	var label := "%s %s" % [node.name.to_lower(), object_type]
	for keyword in ["chair", "sofa", "couch", "seat", "bench", "stool", "lounge", "椅子", "イス", "いす", "ソファ", "ベンチ", "座席"]:
		if label.contains(keyword):
			return true
	return false


func _meta_bool(node: Node3D, key: String, default: bool) -> bool:
	if not node.has_meta(key):
		return default
	var raw: Variant = node.get_meta(key)
	if typeof(raw) == TYPE_BOOL:
		return raw
	if typeof(raw) == TYPE_INT:
		return int(raw) != 0
	if typeof(raw) == TYPE_FLOAT:
		return not is_zero_approx(float(raw))
	if typeof(raw) == TYPE_STRING:
		var text := String(raw).strip_edges().to_lower()
		if text.is_empty():
			return default
		if text in ["1", "true", "yes", "on", "y", "t"]:
			return true
		if text in ["0", "false", "no", "off", "n", "f"]:
			return false
	return default


func _meta_float(node: Node3D, key: String, default: float) -> float:
	if not node.has_meta(key):
		return default
	var raw: Variant = node.get_meta(key)
	if typeof(raw) in [TYPE_INT, TYPE_FLOAT]:
		return float(raw)
	if typeof(raw) == TYPE_STRING:
		var text := String(raw).strip_edges()
		if text.is_empty():
			return default
		return float(text.to_float())
	return default


func _obstacle_clearance_radius() -> float:
	var clearance := 0.2
	if _target_node and _is_addressable_furniture(_target_node):
		var target_clearance := float(_target_node.get_meta("si_addressable_clearance", clearance))
		if target_clearance > 0.0:
			clearance = target_clearance
	return clearance


func _tick_move(delta: float) -> void:
	if not _validate_active_route():
		return
	var offset := _target_position - global_position
	offset.y = 0.0
	var distance := offset.length()
	target_distance = distance
	if _movement_timed_out(distance):
		return
	if _is_in_navigation_fallback:
		movement_mode = "direct_fallback"
	else:
		movement_mode = "direct"
	if distance <= minf(_stop_distance, route_arrival_epsilon):
		_arrive_at_segment("target reached")
		return
	_record_actual_movement(delta)
	if _route_planning_active and _movement_stall_timer >= maxf(0.2, route_replan_stall_seconds):
		_attempt_route_replan("direct_stall")
		return
	if _movement_stall_timer >= movement_stall_abort_seconds:
		_abort_stalled_motion(distance)
		return
	var direction := offset.normalized()
	var steer_direction := _apply_obstacle_avoidance(direction)
	if steer_direction.length() <= 0.001:
		velocity = Vector3.ZERO
		_set_motion_state("Idle")
		return
	_set_movement_posture()
	velocity = steer_direction * minf(_walk_speed, distance / maxf(delta, 0.001))
	_face_direction(steer_direction, delta)
	if _route_planning_active and not _current_route_segment_clear(global_position + velocity * delta):
		velocity = Vector3.ZERO
		_attempt_route_replan("steering_segment_blocked")
		return
	move_and_slide()
	global_position = _clamp_stage_position(global_position)
	_set_motion_state("Walk")


func _finish_target_arrival(detail: String) -> void:
	if not _route_positions.is_empty():
		if _start_next_route_segment():
			return
		navigation_last_plan_status = "failed:route_continuation"
		_route_positions.clear()
		_route_final_node = null
		_route_params = {}
		stop_motion(false)
		_complete_async_command(false, "failed", "route continuation failed")
		return
	if _pending_sit_node != null:
		var seat_node := _pending_sit_node
		var sit_params := _pending_sit_params.duplicate(true)
		_pending_sit_node = null
		_pending_sit_params = {}
		if is_instance_valid(seat_node):
			navigation_last_plan_status = "completed"
			_route_planning_active = false
			navigation_detour_active = false
			_route_force_direct = false
			_route_final_node = null
			_route_params = {}
			var seated := _apply_sit_posture(seat_node, sit_params)
			_complete_async_command(seated, "completed" if seated else "failed", "seated at %s" % seat_node.name if seated else "sit posture failed")
			return
		stop_motion(false)
		_complete_async_command(false, "failed", "sit target unavailable")
		return
	if _route_planning_active:
		navigation_last_plan_status = "completed"
	_record_navigation_result(true, "completed")
	stop_motion(false)
	_complete_async_command(true, "completed", detail)


func _abort_stalled_motion(distance: float) -> void:
	_abort_unfinished_motion(distance, "movement stalled")


func _movement_timed_out(distance: float) -> bool:
	if _movement_started_at <= 0.0 or _movement_timeout_seconds <= 0.0:
		return false
	var elapsed := (Time.get_ticks_msec() / 1000.0) - _movement_started_at
	if elapsed <= _movement_timeout_seconds:
		return false
	_abort_unfinished_motion(distance, "movement timed out after %.2fs" % elapsed)
	return true


func _abort_unfinished_motion(distance: float, reason: String) -> void:
	var target_label := target_node_name if not target_node_name.is_empty() else "position"
	var detail := "%s %.2fm before %s" % [reason, distance, target_label]
	_record_navigation_result(false, "failed")
	if _route_planning_active:
		navigation_last_plan_status = "failed:%s" % reason.replace(" ", "_")
	_movement_started_at = 0.0
	_movement_timeout_seconds = 0.0
	stop_motion(false)
	_complete_async_command(false, "failed", detail)


func _record_navigation_result(ok: bool, status: String) -> void:
	if _active_async_command_id.is_empty() or _active_async_action not in ["move_to_position", "move_to_node", "follow_user"]:
		return
	var target_valid := _route_target_instance_id == 0 or (is_instance_valid(_route_final_node) and not _route_final_node.is_queued_for_deletion())
	var target_position := _route_goal_position
	if _route_target_instance_id != 0:
		target_position = _route_final_node.global_position if target_valid else _route_target_transform.origin
	var approach := _route_goal_position if _route_planning_active else _target_position
	var target_delta := Vector2(target_position.x - global_position.x, target_position.z - global_position.z)
	var approach_delta := Vector2(approach.x - target_position.x, approach.z - target_position.z)
	last_navigation_result = {
		"command_id": _active_async_command_id,
		"target_node": String(_route_final_node.name) if is_instance_valid(_route_final_node) else target_node_name,
		"target_instance_id": _route_target_instance_id, "target_valid": target_valid,
		"target_position": _vector3_dict(target_position),
		"approach_position": _vector3_dict(approach),
		"avatar_position": _vector3_dict(global_position),
		"effective_arrival_radius": maxf(_stop_distance, approach_delta.length()),
		"stop_distance": _stop_distance, "final_distance": target_delta.length(),
		"distance_to_approach": Vector2(global_position.x - approach.x, global_position.z - approach.z).length(),
		"ok": ok, "status": status, "completed_unix": Time.get_unix_time_from_system()
	}


func get_state_snapshot() -> Dictionary:
	return {
		"last_navigation_result": last_navigation_result.duplicate(true),
		# Read-only, engine-facing telemetry for the portable navigation adapter.
		# This deliberately excludes scene/node names.  Legacy consumers retain
		# the exact last_navigation_result shape above.
		"portable_navigation": _portable_navigation_snapshot(),
	}


func _portable_navigation_snapshot() -> Dictionary:
	var raw_obstacles := _collect_route_obstacles(null)
	var portable_obstacles: Array = []
	for raw_index in range(raw_obstacles.size()):
		var raw_value: Variant = raw_obstacles[raw_index]
		if not (raw_value is Dictionary):
			continue
		var obstacle: Dictionary = raw_value
		var kind := String(obstacle.get("kind", "")).strip_edges().to_lower()
		var center_value: Variant = obstacle.get("center", null)
		if typeof(center_value) != TYPE_VECTOR2 or kind not in ["rect", "circle"]:
			continue
		var center: Vector2 = center_value
		var item := {
			"obstacle_id": "godot-obstacle-%03d" % raw_index,
			"kind": kind,
			"center_xz": {"x": center.x, "z": center.y},
		}
		if kind == "rect":
			var half_value: Variant = obstacle.get("half_extents", null)
			if typeof(half_value) != TYPE_VECTOR2:
				continue
			var half: Vector2 = half_value
			item["half_extents_xz"] = {"x": half.x, "z": half.y}
		else:
			var radius := float(obstacle.get("radius", 0.0))
			if radius <= 0.0:
				continue
			item["radius_m"] = radius
		portable_obstacles.append(item)

	var remaining_waypoints: Array = []
	if _has_target:
		remaining_waypoints.append(_vector3_dict(_target_position))
	for waypoint in _route_positions:
		remaining_waypoints.append(_vector3_dict(waypoint))

	return {
		"schema_version": "lumina.godot.portable-navigation-snapshot.v1",
		"captured_unix": Time.get_unix_time_from_system(),
		"coordinate_frame": "godot_x_right_y_up_z_back",
		"bounds_xz": {
			"min_x": stage_min_x,
			"max_x": stage_max_x,
			"min_z": stage_min_z,
			"max_z": stage_max_z,
		},
		"agent": {
			"position": _vector3_dict(global_position),
			"velocity": _vector3_dict(velocity),
			"forward": _vector3_dict((-global_transform.basis.z).normalized()),
			"footprint_radius_m": maxf(0.0, route_planner_clearance),
			"height_m": maxf(0.1, route_agent_height),
		},
		"obstacles": portable_obstacles,
		"route": {
			"active": _route_planning_active and _has_target,
			"blocked": _route_blocked,
			"status": navigation_last_plan_status,
			"detour_active": navigation_detour_active,
			"remaining_waypoints": remaining_waypoints,
			"replan_count": navigation_replan_count,
			"path_length_m": maxf(0.0, navigation_planned_path_length),
		},
		"collision": {
			"contact_count": get_slide_collision_count(),
			"on_floor": is_on_floor(),
			"on_wall": is_on_wall(),
			"on_ceiling": is_on_ceiling(),
			"route_segment_blocked": _route_blocked,
		},
	}


func _set_motion_state(motion_name: String) -> void:
	if motion_name != current_animation:
		current_animation = motion_name
	if _vrm_adapter and _vrm_adapter.has_method("set_motion_state"):
		_vrm_adapter.call("set_motion_state", motion_name, velocity.length())


func _tick_look_at(delta: float) -> void:
	if _has_target:
		return
	_update_look_attention_state()
	if _look_target_is_user and _look_hold_until > 0.0 and Time.get_ticks_msec() / 1000.0 >= _look_hold_until:
		_clear_look_target()
		_clear_vrm_gaze_target()
		return
	var target_position := Vector3.ZERO
	var has_target := false
	if _look_target_node:
		if not is_instance_valid(_look_target_node):
			_clear_look_target()
			_clear_vrm_gaze_target()
			return
		target_position = _look_target_node.global_position
		has_target = true
	elif _has_look_position:
		target_position = _look_target_position
		has_target = true
	if not has_target:
		_clear_vrm_gaze_target()
		return
	var gaze_target_position := target_position
	if _look_target_is_user:
		gaze_target_position = _resolve_user_gaze_target_position(gaze_target_position)
	elif absf(gaze_target_position.y - global_position.y) < 0.05:
		gaze_target_position.y = global_position.y + look_target_height_offset
	var gaze_direction := gaze_target_position - global_position
	_update_vrm_gaze(gaze_direction)
	var direction := target_position - global_position
	direction.y = 0.0
	if direction.length() > 0.05:
		var speed_scale := 1.0
		if _look_target_is_user:
			speed_scale = look_attention_rotation_boost
		_face_direction(direction.normalized(), delta, speed_scale)


func get_look_state() -> Dictionary:
	_update_look_attention_state()
	var pose: Dictionary = {}
	var world := get_parent()
	if world and world.has_method("get_lumina_eye_pose"):
		var raw_pose: Variant = world.call("get_lumina_eye_pose")
		if raw_pose is Dictionary:
			pose = raw_pose
	var actual_forward: Vector3 = pose.get("forward", get_view_forward())
	return {
		"looking_at_user": looking_at_user,
		"look_target": look_target_name,
		"look_attention_state": look_attention_state,
		"look_hold_remaining": look_hold_remaining,
		"user_attention": looking_at_user,
		"eye_position": _vector3_dict(get_eye_position()),
		"view_forward": _vector3_dict(actual_forward),
		"view_source": String(pose.get("source", "body_height_fallback")),
		"eye_pose_timestamp_unix": float(pose.get("timestamp_unix", 0.0)),
	}


func get_eye_position() -> Vector3:
	var world := get_parent()
	if world and world.has_method("get_lumina_eye_pose"):
		var pose: Variant = world.call("get_lumina_eye_pose")
		if pose is Dictionary and pose.get("position") is Vector3:
			return pose.position
	return global_position + Vector3.UP * maxf(0.1, eye_view_height)


func get_view_forward() -> Vector3:
	var target: Variant = _current_gaze_target_position()
	var eye_position := get_eye_position()
	if target != null:
		var target_position := target as Vector3
		var direction := target_position - eye_position
		if direction.length() > 0.05:
			return direction.normalized()
	return (-global_transform.basis.orthonormalized().z).normalized()


func _current_gaze_target_position() -> Variant:
	if _look_target_node and is_instance_valid(_look_target_node):
		var target_position := _look_target_node.global_position
		if _look_target_is_user:
			return _resolve_user_gaze_target_position(target_position)
		if absf(target_position.y - global_position.y) < 0.05:
			target_position.y = global_position.y + look_target_height_offset
		return target_position
	if _has_look_position:
		var target_position := _look_target_position
		if absf(target_position.y - global_position.y) < 0.05:
			target_position.y = global_position.y + look_target_height_offset
		return target_position
	return null


func _update_look_attention_state() -> void:
	var now := Time.get_ticks_msec() / 1000.0
	if _look_target_node and not is_instance_valid(_look_target_node):
		_clear_look_target()
		return
	if _look_target_is_user and _look_target_node and _look_hold_until > 0.0 and now >= _look_hold_until:
		_clear_look_target()
		return
	if _look_target_node and is_instance_valid(_look_target_node):
		look_target_name = "user" if _look_target_is_user else _look_target_node.name
	elif _has_look_position:
		look_target_name = "position"
	else:
		look_target_name = ""
	if _look_hold_until > 0.0:
		look_hold_remaining = maxf(0.0, _look_hold_until - now)
	else:
		look_hold_remaining = 0.0
	looking_at_user = _look_target_is_user and _look_target_node != null and is_instance_valid(_look_target_node)
	if looking_at_user:
		look_attention_state = "looking_at_user" if look_hold_remaining <= 0.0 else "noticed_user"
	elif not look_target_name.is_empty():
		look_attention_state = "looking_at_object"
	else:
		look_attention_state = "none"


func _update_vrm_gaze(direction: Vector3) -> void:
	if not _vrm_adapter or not _vrm_adapter.has_method("set_gaze_direction"):
		return
	if direction.length() <= 0.05:
		return
	var local_direction := global_transform.basis.inverse() * direction.normalized()
	var yaw_deg := rad_to_deg(atan2(-local_direction.x, -local_direction.z))
	var pitch_deg := rad_to_deg(asin(clampf(local_direction.y, -1.0, 1.0)))
	_vrm_adapter.call("set_gaze_direction", yaw_deg, pitch_deg, gaze_hold_seconds)


func _resolve_user_gaze_target_position(fallback_position: Vector3) -> Vector3:
	if _look_target_node and is_instance_valid(_look_target_node) and _look_target_node.has_method("get_eye_position"):
		var eye_position = _look_target_node.call("get_eye_position")
		if typeof(eye_position) == TYPE_VECTOR3:
			return eye_position
	var resolved := fallback_position
	resolved.y += user_look_target_height_offset
	return resolved


func _apply_user_attention_expression() -> void:
	var soft_expression := user_attention_soft_expression.strip_edges()
	if soft_expression.is_empty():
		return
	var normalized_current := current_expression.strip_edges().to_lower()
	if not (normalized_current in ["", "neutral", "relaxed", "fun"]):
		return
	set_expression(soft_expression, clampf(user_attention_soft_expression_intensity, 0.0, 1.0))


func _clear_vrm_gaze_target() -> void:
	if _vrm_adapter and _vrm_adapter.has_method("clear_gaze_target"):
		_vrm_adapter.call("clear_gaze_target")


func _face_direction(direction: Vector3, delta: float, speed_scale: float = 1.0) -> void:
	var target_yaw := atan2(-direction.x, -direction.z)
	rotation.y = lerp_angle(rotation.y, target_yaw, clampf(delta * rotation_speed * maxf(1.0, speed_scale), 0.0, 1.0))


func _apply_expression_color(intensity: float = 1.0) -> void:
	if not _body_mesh:
		return
	var material := StandardMaterial3D.new()
	match current_expression:
		"happy":
			material.albedo_color = Color(0.1, 0.74, 0.43).lerp(Color(1.0, 0.85, 0.2), intensity * 0.35)
		"sad":
			material.albedo_color = Color(0.18, 0.38, 0.70)
		"angry":
			material.albedo_color = Color(0.78, 0.16, 0.12)
		_:
			material.albedo_color = Color(0.1, 0.74, 0.43)
	_body_mesh.material_override = material


func _clear_look_target() -> void:
	_look_target_node = null
	_has_look_position = false
	_look_target_is_user = false
	_look_hold_until = -1.0
	looking_at_user = false
	look_target_name = ""
	look_attention_state = "none"
	look_hold_remaining = 0.0


func _find_node3d(name_or_path: String) -> Node3D:
	if name_or_path.is_empty():
		return null
	var by_path := get_node_or_null(NodePath(name_or_path))
	if by_path is Node3D:
		return by_path
	var root := get_tree().root
	var by_name := root.find_child(name_or_path, true, false)
	if by_name is Node3D:
		return by_name
	return null


func _resolve_sit_target_name(seat_target_name: String, params: Dictionary) -> String:
	var resolved := seat_target_name.strip_edges()
	if resolved.is_empty():
		resolved = String(params.get("target_node", "")).strip_edges()
	if resolved.is_empty():
		resolved = String(params.get("seat", "")).strip_edges()
	if resolved.is_empty():
		resolved = String(params.get("target", "")).strip_edges()
	return resolved


func _first_player() -> Node3D:
	var nodes := get_tree().get_nodes_in_group(player_group_name)
	if nodes.is_empty() or not (nodes[0] is Node3D):
		return null
	return nodes[0] as Node3D


func _dict_to_vector3(value: Variant) -> Vector3:
	if typeof(value) == TYPE_DICTIONARY:
		return Vector3(float(value.get("x", 0.0)), float(value.get("y", 0.0)), float(value.get("z", 0.0)))
	return Vector3.ZERO


func _vector3_dict(value: Vector3) -> Dictionary:
	return {"x": value.x, "y": value.y, "z": value.z}
