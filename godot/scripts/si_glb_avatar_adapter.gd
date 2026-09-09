extends SIVRMAvatarAdapter
class_name SIGLBAvatarAdapter

const NaturalAnimationSchedulerScript = preload("res://scripts/si_glb_natural_animation_scheduler.gd")

@export var glb_manifest_path: String = "res://assets/avatars/lumina_meshi_biped/animations/lumina_animation_manifest.json"
@export var glb_profile_root: String = "res://assets/avatars/lumina_meshi_biped/animations_user_fixed_visual_20260624/"
@export var glb_scene_cache_root: String = "res://assets/avatars/lumina_meshi_biped/generated_scene_cache/animations_user_fixed_visual_20260624/"
@export var blocked_glb_profile_fragments: Array[String] = ["profile_62_hair_uv_texture_white_speckle_fix_from_profile60_20260606"]
@export var natural_animation_policy_path: String = "res://assets/avatars/lumina_meshi_biped/lumina_natural_animation_policy_20260621.json"
@export var default_state: String = "idle"
@export var walk_state: String = "walk"
@export var talk_state: String = "talk_standing"
@export var sit_state: String = "sit_idle"
@export var stand_state: String = "stand_up_primary"
@export var target_glb_height: float = 1.64
@export var enable_talk_state_while_speaking: bool = true
@export var enable_natural_animation_scheduler: bool = true
@export var enable_glb_vrm_compat_motion: bool = true
@export var enable_glb_visual_shell_micro_motion: bool = true
@export var glb_visual_shell_motion_preset: String = "normal"
@export var glb_visual_shell_breath_height: float = 0.012
@export var glb_visual_shell_sway_degrees: float = 0.55
@export var glb_visual_shell_gaze_yaw_degrees: float = 0.42
@export var glb_visual_shell_talk_boost: float = 1.35
@export var apply_glb_vrm_compat_gentle_tuning: bool = true
@export var glb_vrm_compat_procedural_gestures: Array[String] = ["wave", "point", "nod", "bow", "shrug", "clap"]
@export var enable_glb_hair_material_smoothing: bool = true
@export var glb_hair_material_roughness: float = 0.74
@export var glb_hair_material_specular: float = 0.16
@export var glb_hair_material_clearcoat: float = 0.0
@export var glb_hair_material_tint: Color = Color(0.42, 0.04, 0.82, 1.0)
@export var glb_hair_material_tint_strength: float = 0.42
@export var enable_glb_atlas_emissive_neutralize: bool = true
@export var glb_atlas_emission_energy: float = 0.0
@export var glb_atlas_clear_emission_texture: bool = true
@export var glb_atlas_min_roughness: float = 0.74
@export var glb_atlas_specular: float = 0.16
@export var glb_atlas_force_backface_cull: bool = true

var _glb_manifest: Dictionary = {}
var _glb_states: Dictionary = {}
var _glb_state_name: String = ""
var _glb_source_path: String = ""
var _glb_state_loop: bool = true
var _natural_scheduler: Node = null
var _natural_semantic_state: String = "idle_base"
var _glb_vrm_compat_ready := false
var _glb_vrm_compat_tuning_applied := false
var _glb_visual_shell_base_position := Vector3.ZERO
var _glb_visual_shell_base_rotation_degrees := Vector3.ZERO
var _glb_visual_shell_motion_initialized := false
var _glb_no_embedded_tracks_warned := false
var _glb_motion_runtime_status: Dictionary = {
	"procedural_only_mode": false,
	"last_unavailable_reason": "",
	"last_unavailable_state": "",
	"has_playable_animation_tracks": false,
	"visual_shell_motion_preset": "normal"
}


func _ready() -> void:
	_fallback_body = get_node_or_null(fallback_body_path) as MeshInstance3D
	_setup_voice_audio()
	_load_glb_manifest()
	_load_model()
	_setup_natural_animation_scheduler()


func _process(delta: float) -> void:
	_time += delta
	_tick_glb_visual_shell_micro_motion(delta)
	if enable_talk_state_while_speaking and _is_tts_speaking():
		if enable_natural_animation_scheduler and _natural_scheduler != null:
			_set_natural_semantic_state("speaking")
		elif _glb_state_name == default_state:
			_switch_glb_state(talk_state, false, 1.0)
	elif enable_natural_animation_scheduler and _natural_scheduler != null and _natural_semantic_state == "speaking":
		_set_natural_semantic_state("idle_base")
	elif _glb_state_name == talk_state:
		_switch_glb_state(default_state, false, 1.0)
	if enable_glb_vrm_compat_motion and _glb_vrm_compat_ready:
		_tick_motion(delta)
	_apply_expression_shape_targets()
	_tick_mouth(delta)


func _load_model() -> void:
	_model_root = Node3D.new()
	_model_root.name = "AvatarModelRoot"
	_model_root.scale = Vector3.ONE * model_scale
	_model_root.rotation_degrees.y = model_yaw_degrees
	_glb_visual_shell_base_position = _model_root.position
	_glb_visual_shell_base_rotation_degrees = _model_root.rotation_degrees
	_glb_visual_shell_motion_initialized = true
	add_child(_model_root)
	if _fallback_body:
		_fallback_body.visible = false
	loaded = _switch_glb_state(default_state, true, 1.0)
	set_expression("neutral", 1.0)


func _setup_natural_animation_scheduler() -> void:
	if not enable_natural_animation_scheduler:
		return
	if _natural_scheduler != null:
		return
	_natural_scheduler = NaturalAnimationSchedulerScript.new()
	_natural_scheduler.name = "NaturalAnimationScheduler"
	if _natural_scheduler.has_method("set_policy_path"):
		_natural_scheduler.call("set_policy_path", natural_animation_policy_path)
	if _natural_scheduler.has_signal("animation_requested"):
		_natural_scheduler.connect("animation_requested", Callable(self, "_on_natural_animation_requested"))
	if _natural_scheduler.has_signal("state_changed"):
		_natural_scheduler.connect("state_changed", Callable(self, "_on_natural_animation_state_changed"))
	add_child(_natural_scheduler)


func _tick_glb_visual_shell_micro_motion(_delta: float) -> void:
	if not enable_glb_visual_shell_micro_motion:
		return
	if _model_root == null:
		return
	if not _glb_visual_shell_motion_initialized:
		_glb_visual_shell_base_position = _model_root.position
		_glb_visual_shell_base_rotation_degrees = _model_root.rotation_degrees
		_glb_visual_shell_motion_initialized = true

	var intensity := 1.0
	var tempo := 1.0
	match _natural_semantic_state:
		"speaking":
			intensity = glb_visual_shell_talk_boost
			tempo = 1.22
		"listening":
			intensity = 0.78
			tempo = 0.82
		"thinking":
			intensity = 0.58
			tempo = 0.62
		"turn_recover":
			intensity = 0.9
			tempo = 1.08
	if _is_tts_speaking():
		intensity = max(intensity, glb_visual_shell_talk_boost)
		tempo = max(tempo, 1.22)
	intensity *= _glb_visual_shell_motion_preset_multiplier()

	var t := _time * tempo
	var breath_y := sin(t * TAU * 0.18) * glb_visual_shell_breath_height * intensity
	var sway_z := sin((t * TAU * 0.07) + 0.6) * glb_visual_shell_sway_degrees * intensity
	var nod_x := sin((t * TAU * 0.11) + 1.4) * glb_visual_shell_sway_degrees * 0.35 * intensity
	var gaze_y := sin((t * TAU * 0.045) + 2.1) * glb_visual_shell_gaze_yaw_degrees * intensity
	if _natural_semantic_state == "speaking":
		gaze_y += sin((t * TAU * 0.21) + 0.2) * glb_visual_shell_gaze_yaw_degrees * 0.36
	elif _natural_semantic_state == "listening":
		gaze_y *= 0.45
	elif _natural_semantic_state == "thinking":
		nod_x -= glb_visual_shell_sway_degrees * 0.18
		gaze_y += sin((t * TAU * 0.035) + 3.4) * glb_visual_shell_gaze_yaw_degrees * 0.28
	_model_root.position = _glb_visual_shell_base_position + Vector3(0.0, breath_y, 0.0)
	_model_root.rotation_degrees.x = _glb_visual_shell_base_rotation_degrees.x + nod_x
	_model_root.rotation_degrees.y = _glb_visual_shell_base_rotation_degrees.y + gaze_y
	_model_root.rotation_degrees.z = _glb_visual_shell_base_rotation_degrees.z + sway_z


func _glb_visual_shell_motion_preset_multiplier() -> float:
	match glb_visual_shell_motion_preset.strip_edges().to_lower():
		"off", "none", "disabled":
			return 0.0
		"low", "subtle", "safe":
			return 0.62
		"high", "strong":
			return 1.22
	return 1.0


func _set_natural_semantic_state(next_state: String) -> void:
	if _natural_scheduler == null or not _natural_scheduler.has_method("set_semantic_state"):
		return
	_natural_scheduler.call("set_semantic_state", next_state)


func _on_natural_animation_requested(file_name: String, state_name: String, blend_seconds: float) -> void:
	# A v051 enterprise profile stores all 39 actions in one GLB.  Prefer the
	# semantic state key supplied by the scheduler when it exists, because a
	# file-name lookup alone cannot distinguish multiple animations in that
	# shared GLB.  Keep the legacy file lookup as the compatibility fallback.
	var target_state := ""
	if _glb_states.has(state_name):
		target_state = state_name
	else:
		target_state = _state_for_glb_file(file_name)
	if target_state.is_empty():
		push_warning("[SIGLBAvatarAdapter] natural scheduler requested unknown file: %s state=%s" % [file_name, state_name])
		_notify_scheduler_animation_unavailable(state_name, "unknown_file")
		return
	var restart := state_name != "idle_base"
	var switched := _switch_glb_state(target_state, restart, 1.0, blend_seconds)
	if not switched:
		_notify_scheduler_animation_unavailable(state_name, "switch_failed")
		return
	if not _glb_has_playable_animation_tracks():
		_notify_scheduler_animation_unavailable(state_name, "no_embedded_tracks")


func _glb_has_playable_animation_tracks() -> bool:
	if _animation_player == null:
		_glb_motion_runtime_status["has_playable_animation_tracks"] = false
		return false
	var has_tracks := not _animation_player.get_animation_list().is_empty()
	_glb_motion_runtime_status["has_playable_animation_tracks"] = has_tracks
	return has_tracks


func get_glb_motion_runtime_status() -> Dictionary:
	_glb_motion_runtime_status["visual_shell_motion_preset"] = glb_visual_shell_motion_preset
	return _glb_motion_runtime_status.duplicate(true)


func get_glb_motion_trace() -> Dictionary:
	# Keep a public, runtime-safe trace of the state that was actually selected.
	# This is intentionally separate from the natural scheduler's requested
	# semantic state: one-shot commands such as Point and Stand may be issued
	# directly from the avatar controller.
	var active_animation := ""
	if _animation_player != null:
		active_animation = String(_animation_player.current_animation)
	return {
		"loaded": loaded,
		"active_state": _glb_state_name,
		"active_animation": active_animation,
		"loop": _glb_state_loop,
		"semantic_state": _natural_semantic_state,
		"current_motion": current_motion,
		"posture_state": _posture_state,
		"posture_gesture": _posture_gesture,
		"has_playable_animation_tracks": _glb_has_playable_animation_tracks(),
	}


func _notify_scheduler_animation_unavailable(state_name: String, reason: String) -> void:
	_glb_motion_runtime_status["procedural_only_mode"] = true
	_glb_motion_runtime_status["last_unavailable_reason"] = reason
	_glb_motion_runtime_status["last_unavailable_state"] = state_name
	_glb_motion_runtime_status["visual_shell_motion_preset"] = glb_visual_shell_motion_preset
	if reason == "no_embedded_tracks" and not _glb_no_embedded_tracks_warned:
		push_warning("[SIGLBAvatarAdapter] GLB_MOTION_STATUS procedural_only=true reason=no_embedded_tracks route=%s state=%s preset=%s" % [glb_profile_root, state_name, glb_visual_shell_motion_preset])
		_glb_no_embedded_tracks_warned = true
	if _natural_scheduler != null and _natural_scheduler.has_method("set_procedural_only_mode"):
		_natural_scheduler.call("set_procedural_only_mode", true)
	if _natural_scheduler != null and _natural_scheduler.has_method("notify_animation_unavailable"):
		_natural_scheduler.call("notify_animation_unavailable", state_name)


func _on_natural_animation_state_changed(state_name: String) -> void:
	_natural_semantic_state = state_name


func _state_for_glb_file(file_name: String) -> String:
	for state_key in _glb_states.keys():
		var state_name := String(state_key)
		var spec: Dictionary = _glb_states[state_key]
		if String(spec.get("file", "")) == file_name:
			return state_name
	return ""


func _semantic_state_for_request(state_name: String) -> String:
	var normalized := state_name.strip_edges().to_lower()
	if normalized.is_empty():
		return ""
	match normalized:
		"idle", "idle_base", "neutral", "wait", "waiting", "clear", "recover", "none":
			return "idle_base"
		"listen", "listening", "attentive", "attention", "user_speaking", "input", "hearing":
			return "listening"
		"think", "thinking", "processing", "llm", "generating", "ponder", "hesitate":
			return "thinking"
		"speak", "speaking", "talk", "talking", "chat", "chatting", "tts", "voice":
			return "speaking"
		"turn", "turn_recover", "orient", "orientation", "face_user", "look_at_user":
			return "turn_recover"
	return ""


func play_animation(animation_name: String) -> bool:
	var state_name := _state_for_animation_request(animation_name)
	if state_name.is_empty():
		return false
	return _switch_glb_state(state_name, true, 1.0)


func set_living_semantic_state(state_name: String) -> bool:
	var semantic_state := _semantic_state_for_request(state_name)
	if semantic_state.is_empty():
		return false
	if enable_natural_animation_scheduler and _natural_scheduler != null:
		_set_natural_semantic_state(semantic_state)
		return true
	match semantic_state:
		"idle_base", "listening":
			return _switch_glb_state(default_state, false, 1.0)
		"thinking":
			return _switch_glb_state("catching_breath", true, 1.0)
		"speaking":
			return _switch_glb_state(talk_state, true, 1.0)
		"turn_recover":
			return _switch_glb_state("turn_right", true, 1.0)
	return false


func set_conversation_activity(activity: String) -> bool:
	return set_living_semantic_state(activity)


func set_living_animation_style(style_name: String) -> bool:
	if _natural_scheduler == null or not _natural_scheduler.has_method("set_activity_style"):
		return false
	return bool(_natural_scheduler.call("set_activity_style", style_name))


func set_motion_state(motion_name: String, speed: float = 0.0) -> void:
	var normalized := motion_name.strip_edges().to_lower()
	if normalized == "walk":
		var ratio := _calc_walk_speed_ratio(speed)
		_switch_glb_state(walk_state, false, ratio)
		current_motion = "Walk"
		return
	if _glb_state_name == walk_state:
		_return_to_idle_state()
	current_motion = "Idle"


func set_posture_state(state: String, params: Dictionary = {}) -> bool:
	var normalized := state.strip_edges().to_lower()
	match normalized:
		"listen", "listening", "attentive", "attention":
			return set_living_semantic_state("listening")
		"think", "thinking", "wait_thinking", "processing":
			return set_living_semantic_state("thinking")
		"speak", "speaking", "talking", "chatting":
			return set_living_semantic_state("speaking")
		"turn_recover", "orient", "face_user":
			return set_living_semantic_state("turn_recover")
		"gesture":
			var gesture_name := String(params.get("gesture", "talk")).strip_edges().to_lower()
			return set_gesture_posture(gesture_name, float(params.get("duration", -1.0)))
		"sit":
			return set_sit_posture(float(params.get("duration", -1.0)))
		"stand":
			return set_stand_posture(float(params.get("duration", -1.0)))
		"idle", "clear", "neutral", "none", "recover":
			clear_posture_state(true)
			return true
	return false


func set_gesture_posture(name: String, hold_sec: float = -1.0) -> bool:
	var gesture_name := String(name).strip_edges().to_lower()
	# V051 includes an authored Lumina_Point clip. Prefer it to the older
	# procedural compatibility overlay so semantic traces and visible motion
	# agree. Other legacy gestures keep their existing compatibility behavior.
	if gesture_name == "point":
		var authored_state := _state_for_animation_request(gesture_name)
		if not authored_state.is_empty() and authored_state != default_state:
			return _switch_glb_state(authored_state, true, 1.0)
	if enable_glb_vrm_compat_motion and _is_glb_vrm_compat_procedural_gesture(gesture_name):
		return _set_glb_vrm_compat_procedural_gesture(gesture_name, hold_sec)
	var state_name := _state_for_animation_request(name)
	var semantic_state := _semantic_state_for_request(name)
	if not semantic_state.is_empty():
		return set_living_semantic_state(semantic_state)
	if state_name.is_empty() or state_name == default_state:
		state_name = talk_state
	return _switch_glb_state(state_name, true, 1.0)


func set_sit_posture(duration_sec: float = -1.0) -> bool:
	return _switch_glb_state(sit_state, true, 1.0)


func play_sit_transition() -> bool:
	# The controller snaps the actor to the approved seat anchor, then this
	# one-shot provides the visible sit-down transition before sit_idle begins.
	if _glb_states.has("sit_from_approach"):
		return _switch_glb_state("sit_from_approach", true, 1.0)
	return set_sit_posture()


func set_stand_posture(duration_sec: float = -1.0) -> bool:
	return _switch_glb_state(stand_state, true, 1.0)


func clear_posture_state(immediate: bool = false) -> void:
	_return_to_idle_state()


func _return_to_idle_state() -> bool:
	if _glb_state_name == default_state:
		return true
	# Scheduler state is intentionally semantic, while direct commands can be
	# in an authored one-shot state. Re-requesting idle_base is a no-op when the
	# scheduler already believes it is idle, so explicitly restore the clip.
	if enable_natural_animation_scheduler and _natural_scheduler != null and _natural_semantic_state != "idle_base":
		_set_natural_semantic_state("idle_base")
		return true
	return _switch_glb_state(default_state, false, 1.0)


func _load_glb_manifest() -> void:
	var file := FileAccess.open(glb_manifest_path, FileAccess.READ)
	if file == null:
		push_warning("[SIGLBAvatarAdapter] manifest not found: %s" % glb_manifest_path)
		_glb_manifest = {}
		_glb_states = {}
		return
	var parsed = JSON.parse_string(file.get_as_text())
	if typeof(parsed) != TYPE_DICTIONARY:
		push_warning("[SIGLBAvatarAdapter] manifest is not a JSON object: %s" % glb_manifest_path)
		_glb_manifest = {}
		_glb_states = {}
		return
	_glb_manifest = parsed
	_glb_states = _glb_manifest.get("states", {})
	print("[SIGLBAvatarAdapter] manifest=%s states=%d profile=%s" % [glb_manifest_path, _glb_states.size(), glb_profile_root])


func _switch_glb_state(state_name: String, restart: bool = false, speed_scale: float = 1.0, blend_seconds: float = 0.1) -> bool:
	if state_name.is_empty():
		return false
	if _is_blocked_glb_profile_root():
		push_warning("[SIGLBAvatarAdapter] blocked unsafe GLB profile root: %s" % glb_profile_root)
		return false
	if not _glb_states.has(state_name):
		push_warning("[SIGLBAvatarAdapter] state not found: %s" % state_name)
		return false
	if _glb_state_name == state_name and not restart:
		_set_animation_speed(speed_scale)
		return true

	var spec: Dictionary = _glb_states[state_name]
	var file_name := String(spec.get("file", ""))
	if file_name.is_empty():
		push_warning("[SIGLBAvatarAdapter] state has no file: %s" % state_name)
		return false
	var source_path := _normalized_profile_root() + file_name
	var reused_scene := (
		_glb_source_path == source_path
		and _vrm_instance != null
		and is_instance_valid(_vrm_instance)
		and _animation_player != null
		and is_instance_valid(_animation_player)
	)
	if not reused_scene:
		var scene_path := _cached_scene_path(file_name)
		var node := _load_glb_node(scene_path)
		if node == null and scene_path != source_path:
			node = _load_glb_node(source_path)
		if node == null:
			push_warning("[SIGLBAvatarAdapter] GLB load failed: %s" % source_path)
			return false

		if _vrm_instance != null and is_instance_valid(_vrm_instance):
			_vrm_instance.queue_free()
		_vrm_instance = node
		_vrm_instance.name = "avatar_glb_%s" % state_name
		_model_root.add_child(_vrm_instance)
		_fit_glb_to_origin(_vrm_instance)
		_apply_realistic_model_shading()
		_apply_glb_hair_material_smoothing()
		_neutralize_glb_atlas_materials()
		_animation_player = _find_first_of_type(_vrm_instance, "AnimationPlayer") as AnimationPlayer
		_face_mesh = _find_face_mesh(_vrm_instance)
		_collect_capabilities()
		_setup_glb_vrm_compatibility()
		_glb_source_path = source_path
	_glb_state_name = state_name
	_glb_state_loop = bool(spec.get("loop", true))
	var requested_animation := String(spec.get("animation", ""))
	var playable := _choose_glb_animation(requested_animation)
	if _animation_player != null and not playable.is_empty():
		var animation := _animation_player.get_animation(playable)
		if animation != null:
			animation.loop_mode = Animation.LOOP_LINEAR if _glb_state_loop else Animation.LOOP_NONE
		var finished_callback := Callable(self, "_on_glb_animation_finished")
		if not _animation_player.animation_finished.is_connected(finished_callback):
			_animation_player.animation_finished.connect(finished_callback)
		_animation_player.play(playable, maxf(0.0, blend_seconds), maxf(0.05, speed_scale))
	if state_name == walk_state:
		current_motion = "Walk"
		_active_motion_animation = playable
	elif state_name != walk_state:
		current_motion = "Idle"
		_active_motion_animation = ""
	_set_animation_speed(speed_scale)
	_model_root_base_y = _model_root.position.y
	_model_root_base_rotation = _model_root.rotation_degrees
	loaded = true
	print("[SIGLBAvatarAdapter] state=%s file=%s animation=%s loop=%s reused_scene=%s" % [state_name, file_name, playable, str(_glb_state_loop), str(reused_scene)])
	return true


func _setup_glb_vrm_compatibility() -> void:
	_glb_vrm_compat_ready = false
	if not enable_glb_vrm_compat_motion:
		return
	_apply_glb_vrm_compat_tuning_once()
	_setup_idle_pose()
	_glb_vrm_compat_ready = _skeleton != null and not _pose_bone_indices.is_empty()
	if not _glb_vrm_compat_ready:
		push_warning("[SIGLBAvatarAdapter] GLB VRM-compat motion unavailable: no compatible skeleton/bones")


func _apply_glb_vrm_compat_tuning_once() -> void:
	if _glb_vrm_compat_tuning_applied:
		return
	_glb_vrm_compat_tuning_applied = true
	if not apply_glb_vrm_compat_gentle_tuning:
		return
	enable_procedural_idle_motion = true
	enable_procedural_talk_motion = true
	enable_procedural_walk_motion = true
	enable_procedural_gaze_motion = true
	enable_vrma_idle_motion = false
	enable_vrma_talk_motion = false
	idle_pose_blend_speed = maxf(idle_pose_blend_speed, 5.0)
	idle_breath_amount = minf(idle_breath_amount, 0.0035)
	idle_spine_pitch_deg = clampf(idle_spine_pitch_deg, -1.0, 1.0)
	idle_spine_wave_deg = minf(idle_spine_wave_deg, 0.35)
	idle_shoulder_pitch_deg = 4.0
	idle_shoulder_forward_deg = 0.0
	idle_forearm_pitch_deg = 1.0
	idle_arm_sway_deg = 0.55
	idle_neck_pitch_deg = clampf(idle_neck_pitch_deg, -0.3, 1.0)
	idle_head_bob_deg = minf(idle_head_bob_deg, 0.18)
	idle_head_roll_deg = minf(idle_head_roll_deg, 0.08)
	idle_weight_shift_deg = minf(idle_weight_shift_deg, 0.22)
	talk_spine_pitch_deg = minf(talk_spine_pitch_deg, 1.4)
	talk_chest_pitch_deg = minf(talk_chest_pitch_deg, 2.4)
	talk_neck_pitch_deg = minf(talk_neck_pitch_deg, 1.4)
	talk_head_pitch_deg = minf(talk_head_pitch_deg, 0.55)
	talk_wave_deg = minf(talk_wave_deg, 0.65)
	talk_root_bob_amount = minf(talk_root_bob_amount, 0.0035)
	talk_pose_scale = minf(talk_pose_scale, 0.42)
	talk_shoulder_motion_deg = minf(talk_shoulder_motion_deg, 2.0)
	talk_forearm_motion_deg = minf(talk_forearm_motion_deg, 1.4)
	talk_hand_motion_deg = minf(talk_hand_motion_deg, 1.0)
	walk_bob_amount = minf(walk_bob_amount, 0.012)
	walk_root_roll_deg = minf(walk_root_roll_deg, 0.45)
	walk_root_pitch_deg = minf(walk_root_pitch_deg, 0.35)
	walk_shoulder_swing_deg = minf(walk_shoulder_swing_deg, 1.8)
	walk_forearm_swing_deg = minf(walk_forearm_swing_deg, 1.2)
	walk_leg_swing_deg = minf(walk_leg_swing_deg, 6.0)
	walk_knee_bend_deg = minf(walk_knee_bend_deg, 4.5)
	walk_foot_lift_deg = minf(walk_foot_lift_deg, 2.0)
	gaze_blend_speed = maxf(gaze_blend_speed, 7.0)
	gaze_idle_scan_deg = minf(gaze_idle_scan_deg, 1.4)
	gaze_micro_saccade_deg = minf(gaze_micro_saccade_deg, 0.22)
	gesture_wave_amplitude_deg = minf(gesture_wave_amplitude_deg, 12.0)
	gesture_point_forearm_deg = minf(gesture_point_forearm_deg, 36.0)
	gesture_point_shoulder_deg = maxf(gesture_point_shoulder_deg, -24.0)
	gesture_hold_wave_scale = minf(gesture_hold_wave_scale, 0.65)


func _is_glb_vrm_compat_procedural_gesture(gesture_name: String) -> bool:
	if gesture_name.is_empty():
		return false
	for allowed in glb_vrm_compat_procedural_gestures:
		if String(allowed).strip_edges().to_lower() == gesture_name:
			return true
	return false


func _set_glb_vrm_compat_procedural_gesture(gesture_name: String, hold_sec: float = -1.0) -> bool:
	if gesture_name.is_empty():
		return false
	_posture_gesture = gesture_name
	var duration := hold_sec if hold_sec > 0.0 else gesture_hold_sec
	_posture_until = _time + maxf(posture_decay_sec, duration)
	_posture_state = "gesture"
	_posture_requested_blend = 1.0
	_posture_blend = _posture_blend if _posture_blend > 0.0 else 0.0
	return true


func get_glb_vrm_compat_state() -> Dictionary:
	return {
		"enabled": enable_glb_vrm_compat_motion,
		"ready": _glb_vrm_compat_ready,
		"skeleton": _skeleton.name if _skeleton != null else "",
		"mapped_bones": _pose_bone_indices.keys(),
		"motion_source": active_motion_source,
		"current_motion": current_motion,
		"posture_state": _posture_state,
		"gaze_active": _gaze_has_target,
		"gaze_yaw_deg": _gaze_current_yaw_deg,
		"gaze_pitch_deg": _gaze_current_pitch_deg,
	}


func _apply_glb_hair_material_smoothing() -> void:
	if not enable_glb_hair_material_smoothing or _vrm_instance == null:
		return
	for node in _all_nodes(_vrm_instance):
		if not (node is MeshInstance3D):
			continue
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.material_override != null:
			_smooth_glb_hair_material(mesh_instance.material_override, mesh_instance.name)
		if mesh_instance.mesh == null:
			continue
		for surface_index in range(mesh_instance.mesh.get_surface_count()):
			var surface_material := mesh_instance.get_active_material(surface_index)
			if surface_material == null:
				continue
			var hint := "%s %s" % [mesh_instance.name, surface_material.resource_name]
			if not _looks_like_glb_hair_material(hint, surface_material):
				continue
			var material := surface_material
			if material.resource_local_to_scene == false:
				material = surface_material.duplicate() as Material
				if material != null:
					_set_object_property_if_present(material, "resource_local_to_scene", true)
					mesh_instance.set_surface_override_material(surface_index, material)
			_smooth_glb_hair_material(material, hint)


func _smooth_glb_hair_material(material: Material, hint: String) -> void:
	if material == null:
		return
	if not _looks_like_glb_hair_material(hint, material):
		return
	if material is StandardMaterial3D:
		var standard := material as StandardMaterial3D
		standard.roughness = minf(clampf(standard.roughness, 0.0, 1.0), clampf(glb_hair_material_roughness, 0.0, 1.0))
		standard.roughness = maxf(standard.roughness, clampf(glb_hair_material_roughness, 0.0, 1.0))
		standard.metallic = minf(standard.metallic, 0.02)
		standard.albedo_color = standard.albedo_color.lerp(glb_hair_material_tint, clampf(glb_hair_material_tint_strength, 0.0, 1.0))
		_set_object_property_if_present(standard, "metallic_specular", clampf(glb_hair_material_specular, 0.0, 1.0))
		_set_object_property_if_present(standard, "specular", clampf(glb_hair_material_specular, 0.0, 1.0))
		_set_object_property_if_present(standard, "clearcoat_enabled", glb_hair_material_clearcoat > 0.0)
		_set_object_property_if_present(standard, "clearcoat", clampf(glb_hair_material_clearcoat, 0.0, 1.0))
		_set_object_property_if_present(standard, "clearcoat_roughness", 0.72)
		_set_object_property_if_present(standard, "texture_filter", BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC)
	elif material is ShaderMaterial:
		var shader_material := material as ShaderMaterial
		_set_shader_parameter_if_present(shader_material, "_Roughness", clampf(glb_hair_material_roughness, 0.0, 1.0))
		_set_shader_parameter_if_present(shader_material, "_Specular", clampf(glb_hair_material_specular, 0.0, 1.0))
		_set_shader_parameter_if_present(shader_material, "_Smoothness", 1.0 - clampf(glb_hair_material_roughness, 0.0, 1.0))
		_set_shader_parameter_if_present(shader_material, "_RimLift", 0.02)


func _looks_like_glb_hair_material(hint: String, material: Material) -> bool:
	var lower_hint := String(hint).to_lower()
	if _matches_any(lower_hint, ["hair", "kami", "bang", "tail", "twin", "backhair", "front_hair", "side_hair"]):
		return true
	if material is StandardMaterial3D:
		var color := (material as StandardMaterial3D).albedo_color
		var purple_score := color.b + color.r - color.g * 1.8
		return color.b > 0.32 and color.r > 0.16 and color.g < 0.34 and purple_score > 0.45
	return false


func _neutralize_glb_atlas_materials() -> void:
	if not enable_glb_atlas_emissive_neutralize or _vrm_instance == null:
		return
	for node in _all_nodes(_vrm_instance):
		if not (node is MeshInstance3D):
			continue
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.material_override != null:
			var override_material := mesh_instance.material_override
			if override_material.resource_local_to_scene == false:
				override_material = override_material.duplicate() as Material
				if override_material != null:
					_set_object_property_if_present(override_material, "resource_local_to_scene", true)
					mesh_instance.material_override = override_material
			_neutralize_glb_atlas_material(override_material)
		if mesh_instance.mesh == null:
			continue
		for surface_index in range(mesh_instance.mesh.get_surface_count()):
			var surface_material := mesh_instance.get_active_material(surface_index)
			if surface_material == null:
				continue
			var material := surface_material
			if material.resource_local_to_scene == false:
				material = surface_material.duplicate() as Material
				if material != null:
					_set_object_property_if_present(material, "resource_local_to_scene", true)
					mesh_instance.set_surface_override_material(surface_index, material)
			_neutralize_glb_atlas_material(material)


func _neutralize_glb_atlas_material(material: Material) -> void:
	if material == null:
		return
	if material is StandardMaterial3D:
		var standard := material as StandardMaterial3D
		var energy := clampf(glb_atlas_emission_energy, 0.0, 1.0)
		if glb_atlas_clear_emission_texture:
			_set_object_property_if_present(standard, "emission_texture", null)
		_set_object_property_if_present(standard, "emission_enabled", energy > 0.0)
		_set_object_property_if_present(standard, "emission_energy_multiplier", energy)
		_set_object_property_if_present(standard, "emission_energy", energy)
		_set_object_property_if_present(standard, "emission", Color(0.0, 0.0, 0.0, 1.0))
		standard.roughness = maxf(standard.roughness, clampf(glb_atlas_min_roughness, 0.0, 1.0))
		standard.metallic = minf(standard.metallic, 0.02)
		_set_object_property_if_present(standard, "metallic_specular", clampf(glb_atlas_specular, 0.0, 1.0))
		_set_object_property_if_present(standard, "specular", clampf(glb_atlas_specular, 0.0, 1.0))
		_set_object_property_if_present(standard, "texture_filter", BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC)
		if glb_atlas_force_backface_cull:
			_set_object_property_if_present(standard, "cull_mode", BaseMaterial3D.CULL_BACK)
	elif material is ShaderMaterial:
		var shader_material := material as ShaderMaterial
		_set_shader_parameter_if_present(shader_material, "_EmissionMultiplier", clampf(glb_atlas_emission_energy, 0.0, 1.0))
		_set_shader_parameter_if_present(shader_material, "_Roughness", clampf(glb_atlas_min_roughness, 0.0, 1.0))
		_set_shader_parameter_if_present(shader_material, "_Specular", clampf(glb_atlas_specular, 0.0, 1.0))


func _is_blocked_glb_profile_root() -> bool:
	var root := glb_profile_root.strip_edges()
	for fragment in blocked_glb_profile_fragments:
		var blocked := String(fragment).strip_edges()
		if not blocked.is_empty() and root.find(blocked) >= 0:
			return true
	return false


func _load_glb_node(scene_path: String) -> Node3D:
	if ResourceLoader.exists(scene_path, "PackedScene"):
		var packed := ResourceLoader.load(scene_path, "PackedScene")
		if packed != null and packed is PackedScene:
			return (packed as PackedScene).instantiate() as Node3D
	var gltf := GLTFDocument.new()
	var gltf_state := GLTFState.new()
	var global_path := ProjectSettings.globalize_path(scene_path)
	var err := gltf.append_from_file(global_path, gltf_state)
	if err != OK:
		push_warning("[SIGLBAvatarAdapter] GLTFDocument append failed: %s err=%s" % [scene_path, err])
		return null
	var generated := gltf.generate_scene(gltf_state)
	if generated == null or not (generated is Node3D):
		return null
	return generated as Node3D


func _cached_scene_path(file_name: String) -> String:
	var source_path := _normalized_profile_root() + file_name
	var cache_root := glb_scene_cache_root.strip_edges()
	if cache_root.is_empty():
		return source_path
	if not cache_root.ends_with("/"):
		cache_root += "/"
	var cached := cache_root + file_name.get_basename() + ".scn"
	if ResourceLoader.exists(cached, "PackedScene"):
		return cached
	return source_path


func _choose_glb_animation(preferred_name: String) -> String:
	if _animation_player == null:
		return ""
	var names := _animation_player.get_animation_list()
	if names.is_empty():
		return ""
	if not preferred_name.is_empty() and names.has(preferred_name):
		return preferred_name
	return String(names[0])


func _set_animation_speed(speed_scale: float) -> void:
	if _animation_player != null:
		_animation_player.speed_scale = maxf(0.05, speed_scale)


func _on_glb_animation_finished(_animation_name: StringName) -> void:
	if _glb_state_loop:
		return
	if _glb_state_name in ["sit_from_behind", "sit_from_approach"]:
		_switch_glb_state(sit_state, false, 1.0)
		return
	# Direct one-shots (for example V051 Point and Stand) do not change the
	# scheduler semantic state. If it already says idle_base, notify would be a
	# no-op and the last frame would remain frozen. Restore idle explicitly.
	_return_to_idle_state()


func _state_for_animation_request(animation_name: String) -> String:
	var normalized := animation_name.strip_edges().to_lower()
	if normalized.is_empty():
		return ""
	match normalized:
		"idle", "neutral", "wait":
			return default_state
		"walk", "walking":
			return walk_state
		"talk", "speak", "speaking", "chat", "wave", "nod":
			return talk_state
		"sit", "sitting":
			return sit_state
		"stand", "standing":
			return stand_state
		"run", "run_long":
			return "run_long"
		"catching_breath", "breath":
			return "catching_breath"
		"turn_right":
			return "turn_right"
		"sit_from_behind":
			return "sit_from_behind"
		"sit_from_approach":
			return "sit_from_approach"
	for state_key in _glb_states.keys():
		var state_name := String(state_key)
		if state_name.to_lower() == normalized:
			return state_name
		if state_name.to_lower().find(normalized) >= 0:
			return state_name
	return ""


func _normalized_profile_root() -> String:
	if glb_profile_root.ends_with("/"):
		return glb_profile_root
	return glb_profile_root + "/"


func _fit_glb_to_origin(root: Node3D) -> void:
	var bounds := _visual_bounds(root)
	if not bool(bounds.get("valid", false)):
		return
	var min_v: Vector3 = bounds["min"]
	var max_v: Vector3 = bounds["max"]
	var size := max_v - min_v
	if size.y > 0.001 and target_glb_height > 0.0:
		var fit_scale := clampf(target_glb_height / size.y, 0.001, 100.0)
		root.scale *= fit_scale
	bounds = _visual_bounds(root)
	if not bool(bounds.get("valid", false)):
		return
	min_v = bounds["min"]
	max_v = bounds["max"]
	var center := (min_v + max_v) * 0.5
	root.global_position += Vector3(-center.x, -min_v.y, -center.z)


func _visual_bounds(root: Node) -> Dictionary:
	var result := {
		"valid": false,
		"min": Vector3(1.0e20, 1.0e20, 1.0e20),
		"max": Vector3(-1.0e20, -1.0e20, -1.0e20),
	}
	_collect_visual_bounds(root, result)
	return result


func _collect_visual_bounds(node: Node, result: Dictionary) -> void:
	if node is MeshInstance3D:
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.mesh != null:
			var aabb := mesh_instance.mesh.get_aabb()
			var transform := mesh_instance.global_transform
			var corners := [
				aabb.position,
				aabb.position + Vector3(aabb.size.x, 0.0, 0.0),
				aabb.position + Vector3(0.0, aabb.size.y, 0.0),
				aabb.position + Vector3(0.0, 0.0, aabb.size.z),
				aabb.position + Vector3(aabb.size.x, aabb.size.y, 0.0),
				aabb.position + Vector3(aabb.size.x, 0.0, aabb.size.z),
				aabb.position + Vector3(0.0, aabb.size.y, aabb.size.z),
					aabb.position + aabb.size,
			]
			for corner in corners:
					var point: Vector3 = transform * corner
					result["min"] = Vector3(
						minf((result["min"] as Vector3).x, point.x),
						minf((result["min"] as Vector3).y, point.y),
						minf((result["min"] as Vector3).z, point.z)
					)
					result["max"] = Vector3(
						maxf((result["max"] as Vector3).x, point.x),
						maxf((result["max"] as Vector3).y, point.y),
						maxf((result["max"] as Vector3).z, point.z)
					)
					result["valid"] = true
	for child in node.get_children():
		_collect_visual_bounds(child, result)
