extends Node3D
class_name SIVRMAvatarAdapter

signal si_tts_playback_finished(text: String, ok: bool, detail: String)

const VRMAMotionSamplerScript = preload("res://scripts/si_vrma_motion_sampler.gd")
const VRM_EXTENSION := preload("res://addons/vrm/vrm_extension.gd")
const VRM_CONSTANTS := preload("res://addons/vrm/vrm_constants.gd")
const _MOTION_STATE_TRANSITION_SEC := 0.18
## Matches EditorSceneFormatImporter.IMPORT_USE_NAMED_SKIN_BINDS (runtime-safe numeric).
const _VRM_IMPORT_USE_NAMED_SKIN_BINDS := 16
const _LIPSYNC_MOUTH_SHAPE_GROUPS: Array = [
	["Mouth_a_big_op", "Mouth_a_small_op", "Fcl_MTH_A", "Fcl_MTH_AA", "aa", "a", "mouth_a", "mth_a", "Viseme_A"],
	["Mouth_i_big_op", "Mouth_i_small_op", "Fcl_MTH_I", "Fcl_MTH_IH", "ih", "i", "mouth_i", "mth_i", "Viseme_I"],
	["Mouth_u_big_op", "Mouth_u_small_op", "Fcl_MTH_U", "Fcl_MTH_OU", "ou", "u", "mouth_u", "mth_u", "Viseme_U"],
	["Mouth_e_big_op", "Mouth_e_small_op", "Fcl_MTH_E", "Fcl_MTH_EE", "ee", "e", "mouth_e", "mth_e", "Viseme_E"],
	["Mouth_o_big_op", "Mouth_o_small_op", "Fcl_MTH_O", "Fcl_MTH_OH", "oh", "o", "mouth_o", "mth_o", "Viseme_O"],
]
const _LIPSYNC_PRIMARY_VISEME_WEIGHTS: Array[float] = [0.92, 0.58, 0.48, 0.66, 0.82]
const _LIPSYNC_SECONDARY_VISEME_WEIGHTS := {
	0: {4: 0.10},
	1: {3: 0.08},
	2: {4: 0.12},
	3: {1: 0.10},
	4: {0: 0.12, 2: 0.05},
}
const PROFILE_PARAM_RANGES := {
	"idle_pose_blend_speed": Vector2(0.5, 12.0),
	"vrma_idle_blend": Vector2(0.0, 1.0),
	"vrma_idle_playback_speed": Vector2(0.1, 2.0),
	"vrma_motion_blend": Vector2(0.0, 1.0),
	"vrma_motion_playback_speed": Vector2(0.1, 2.0),
	"idle_breath_amount": Vector2(0.0, 0.05),
	"idle_breath_speed": Vector2(0.2, 5.0),
	"idle_spine_pitch_deg": Vector2(-12.0, 12.0),
	"idle_spine_wave_deg": Vector2(0.0, 6.0),
	"idle_shoulder_pitch_deg": Vector2(20.0, 88.0),
	"idle_shoulder_forward_deg": Vector2(-80.0, 80.0),
	"idle_forearm_pitch_deg": Vector2(-35.0, 35.0),
	"idle_arm_sway_deg": Vector2(0.0, 12.0),
	"idle_neck_pitch_deg": Vector2(-8.0, 8.0),
	"idle_head_bob_deg": Vector2(0.0, 4.0),
	"idle_head_roll_deg": Vector2(0.0, 4.0),
	"idle_weight_shift_deg": Vector2(0.0, 2.0),
	"idle_weight_shift_speed": Vector2(0.05, 2.0),
	"talk_spine_pitch_deg": Vector2(-4.0, 12.0),
	"talk_spine_wave_deg": Vector2(0.0, 8.0),
	"talk_chest_pitch_deg": Vector2(-2.0, 14.0),
	"talk_chest_wave_deg": Vector2(0.0, 8.0),
	"talk_neck_pitch_deg": Vector2(-4.0, 10.0),
	"talk_head_pitch_deg": Vector2(-4.0, 8.0),
	"talk_wave_hz": Vector2(0.5, 10.0),
	"talk_wave_deg": Vector2(0.0, 8.0),
	"talk_root_bob_amount": Vector2(0.0, 0.04),
	"talk_pose_scale": Vector2(0.0, 1.4),
	"talk_posture_blend_speed": Vector2(0.5, 14.0),
	"talk_shoulder_motion_deg": Vector2(0.0, 18.0),
	"talk_forearm_motion_deg": Vector2(0.0, 16.0),
	"talk_hand_motion_deg": Vector2(0.0, 14.0),
	"walk_bob_amount": Vector2(0.0, 0.09),
	"walk_step_cycle_hz": Vector2(0.4, 4.0),
	"walk_root_roll_deg": Vector2(0.0, 5.0),
	"walk_root_pitch_deg": Vector2(0.0, 5.0),
	"walk_spine_pitch_deg": Vector2(-4.0, 12.0),
	"walk_spine_wave_deg": Vector2(0.0, 8.0),
	"walk_chest_wave_deg": Vector2(0.0, 8.0),
	"walk_shoulder_pitch_deg": Vector2(20.0, 88.0),
	"walk_shoulder_swing_deg": Vector2(0.0, 55.0),
	"walk_forearm_pitch_deg": Vector2(-10.0, 22.0),
	"walk_forearm_swing_deg": Vector2(0.0, 32.0),
	"walk_neck_pitch_deg": Vector2(-4.0, 8.0),
	"walk_head_pitch_deg": Vector2(-4.0, 8.0),
	"walk_leg_swing_deg": Vector2(0.0, 36.0),
	"walk_knee_bend_deg": Vector2(0.0, 32.0),
	"walk_foot_lift_deg": Vector2(0.0, 24.0),
	"walk_reference_speed": Vector2(0.5, 5.0),
	"walk_speed_ratio_min": Vector2(0.1, 1.0),
	"walk_speed_ratio_max": Vector2(1.0, 3.0),
	"gaze_blend_speed": Vector2(0.5, 14.0),
	"gaze_neck_yaw_limit_deg": Vector2(0.0, 28.0),
	"gaze_head_yaw_limit_deg": Vector2(0.0, 34.0),
	"gaze_pitch_limit_deg": Vector2(0.0, 18.0),
	"gaze_idle_scan_deg": Vector2(0.0, 10.0),
	"gaze_idle_scan_speed": Vector2(0.05, 3.0),
	"gaze_micro_saccade_deg": Vector2(0.0, 2.5),
	"fallback_posture_blend_speed": Vector2(0.5, 14.0),
	"gesture_hold_sec": Vector2(0.2, 8.0),
	"posture_decay_sec": Vector2(0.1, 2.0),
	"sit_hip_bend_deg": Vector2(-70.0, 10.0),
	"sit_knee_bend_deg": Vector2(0.0, 95.0),
	"sit_root_drop_amount": Vector2(0.0, 0.45),
	"sit_spine_pitch_deg": Vector2(-14.0, 8.0),
	"sit_arm_rest_deg": Vector2(-10.0, 34.0),
	"stand_spine_smooth_deg": Vector2(0.0, 10.0),
	"gesture_wave_amplitude_deg": Vector2(0.0, 48.0),
	"gesture_point_forearm_deg": Vector2(0.0, 90.0),
	"gesture_point_shoulder_deg": Vector2(-75.0, 20.0),
	"gesture_point_hand_deg": Vector2(-28.0, 20.0),
	"gesture_hold_wave_hz": Vector2(0.2, 6.0),
	"gesture_hold_wave_scale": Vector2(0.0, 2.0),
	"shoulder_guard_deg": Vector2(30.0, 110.0),
	"forearm_guard_deg": Vector2(30.0, 110.0),
	"spine_guard_deg": Vector2(10.0, 45.0),
	"hip_guard_deg": Vector2(20.0, 70.0),
	"knee_guard_deg": Vector2(30.0, 110.0),
	"vrma_delta_angle_limit_deg": Vector2(10.0, 90.0),
	"lipsync_shape_strength": Vector2(0.0, 1.5),
	"lipsync_follow_speed": Vector2(1.0, 120.0),
	"lipsync_silence_threshold": Vector2(0.0, 0.25),
	"lipsync_volume_scale": Vector2(0.1, 3.0),
	"lipsync_time_offset_sec": Vector2(-0.2, 0.2),
}

@export var vrm_scene_path: String = "res://assets/vrm/stella.vrm"
@export var fallback_body_path: NodePath
@export var model_scale: float = 1.0
@export var model_yaw_degrees: float = 0.0
@export var align_feet_to_origin: bool = true
@export var enable_realistic_model_shading: bool = true
@export var enable_simple_idle_pose: bool = true
@export var idle_pose_blend_speed: float = 5.5
@export var enable_vrma_idle_motion: bool = true
@export var idle_vrma_path: String = "res://assets/animation/idle_loop.vrma"
@export var vrma_idle_blend: float = 0.0
@export var vrma_idle_playback_speed: float = 1.0
@export var enable_vrma_motion_bank: bool = true
@export var animation_asset_dir: String = "res://assets/animation"
@export var walk_vrma_path: String = "res://assets/animation/walk_loop.vrma"
@export var talk_vrma_path: String = "res://assets/animation/talk_loop.vrma"
@export var enable_vrma_talk_motion: bool = true
@export var gesture_wave_vrma_path: String = "res://assets/animation/gesture_wave.vrma"
@export var gesture_point_vrma_path: String = ""
@export var sit_vrma_path: String = "res://assets/animation/sit_loop.vrma"
@export var stand_vrma_path: String = "res://assets/animation/stand_loop.vrma"
@export var enable_fumi2kick_motion_audition: bool = true
@export var enable_vroid_official_motion_audition: bool = true
@export var vrma_motion_blend: float = 0.86
@export var vrma_motion_playback_speed: float = 1.0

# Idle motion tuning
@export var enable_procedural_idle_motion: bool = true
@export var idle_breath_amount: float = 0.005
@export var idle_breath_speed: float = 1.45
@export var idle_spine_pitch_deg: float = -1.2
@export var idle_spine_wave_deg: float = 0.22
@export var idle_shoulder_pitch_deg: float = 84.0
@export var idle_shoulder_forward_deg: float = 66.0
@export var idle_forearm_pitch_deg: float = -6.0
@export var idle_arm_sway_deg: float = 0.12
@export var idle_neck_pitch_deg: float = 0.7
@export var idle_head_bob_deg: float = 0.12
@export var idle_head_roll_deg: float = 0.05
@export var idle_weight_shift_deg: float = 0.28
@export var idle_weight_shift_speed: float = 0.38

@export var enable_procedural_talk_motion: bool = true
@export var talk_spine_pitch_deg: float = 1.7
@export var talk_spine_wave_deg: float = 1.0
@export var talk_chest_pitch_deg: float = 4.4
@export var talk_chest_wave_deg: float = 0.8
@export var talk_neck_pitch_deg: float = 2.2
@export var talk_head_pitch_deg: float = 0.8
@export var talk_wave_hz: float = 4.8
@export var talk_wave_deg: float = 1.0
@export var talk_root_bob_amount: float = 0.006
@export var talk_pose_scale: float = 0.62
@export var talk_posture_blend_speed: float = 6.4
@export var talk_shoulder_motion_deg: float = 4.4
@export var talk_forearm_motion_deg: float = 2.8
@export var talk_hand_motion_deg: float = 2.2

# Walk fallback motion tuning
@export var enable_procedural_walk_motion: bool = true
@export var walk_bob_amount: float = 0.028
@export var walk_step_cycle_hz: float = 2.0
@export var walk_root_roll_deg: float = 1.0
@export var walk_root_pitch_deg: float = 0.65
@export var walk_spine_pitch_deg: float = 2.8
@export var walk_spine_wave_deg: float = 1.0
@export var walk_chest_wave_deg: float = 0.8
@export var walk_shoulder_pitch_deg: float = 88.0
@export var walk_shoulder_swing_deg: float = 3.5
@export var walk_forearm_pitch_deg: float = 2.0
@export var walk_forearm_swing_deg: float = 2.0
@export var walk_neck_pitch_deg: float = 0.8
@export var walk_head_pitch_deg: float = 0.3
@export var walk_leg_swing_deg: float = 16.0
@export var walk_knee_bend_deg: float = 12.0
@export var walk_foot_lift_deg: float = 5.5
@export var walk_reference_speed: float = 2.25
@export var walk_speed_ratio_min: float = 0.55
@export var walk_speed_ratio_max: float = 1.6

@export var enable_procedural_gaze_motion: bool = true
@export var gaze_blend_speed: float = 7.0
@export var gaze_neck_yaw_limit_deg: float = 11.0
@export var gaze_head_yaw_limit_deg: float = 15.0
@export var gaze_pitch_limit_deg: float = 7.0
@export var gaze_idle_scan_deg: float = 1.8
@export var gaze_idle_scan_speed: float = 0.42
@export var gaze_micro_saccade_deg: float = 0.25

@export var fallback_posture_blend_speed: float = 8.0
@export var gesture_hold_sec: float = 1.8
@export var posture_decay_sec: float = 0.5

# Posture fallback tuning
@export var sit_hip_bend_deg: float = -62.0
@export var sit_knee_bend_deg: float = 88.0
@export var sit_root_drop_amount: float = 0.36
@export var sit_spine_pitch_deg: float = -6.0
@export var sit_arm_rest_deg: float = 18.0
@export var stand_spine_smooth_deg: float = 3.0

@export var gesture_wave_amplitude_deg: float = 24.0
@export var gesture_point_forearm_deg: float = 72.0
@export var gesture_point_shoulder_deg: float = -38.0
@export var gesture_point_hand_deg: float = -8.0
@export var gesture_hold_wave_hz: float = 2.2
@export var gesture_hold_wave_scale: float = 1.0

@export var shoulder_guard_deg: float = 110.0
@export var forearm_guard_deg: float = 85.0
@export var spine_guard_deg: float = 24.0
@export var hip_guard_deg: float = 48.0
@export var knee_guard_deg: float = 88.0

@export var vrma_delta_angle_limit_deg: float = 42.0

# Voice playback
@export var enable_tts_audio: bool = true
@export var tts_voice_url: String = "http://127.0.0.1:5056/voice-lipsync"
@export var speech_playback_url: String = "http://127.0.0.1:8765/speech/playback"
@export var tts_speed: float = 1.0
@export var tts_volume_db: float = 6.0
@export_range(1.0, 60.0) var tts_request_timeout_seconds: float = 20.0

@export var lipsync_enabled: bool = true
@export var lipsync_shape_strength: float = 0.88
@export var lipsync_follow_speed: float = 88.0
@export var lipsync_cycle_hz: float = 7.0
@export var lipsync_silence_threshold: float = 0.014
@export var lipsync_profile_fps: float = 75.0
@export var lipsync_volume_scale: float = 1.12
@export var lipsync_time_offset_sec: float = -0.015

var loaded: bool = false
var current_motion: String = "Idle"
var current_expression: String = "neutral"
var current_motion_profile: String = "standing_default"
var current_motion_profile_notes: String = "普通の起立姿勢を既定にし、動作時だけ人間らしさを足す"
var last_speech_text: String = ""
var available_animations: Array[String] = []
var available_blend_shapes: Array[String] = []
var available_vrma_motions: Array[String] = []
var missing_vrma_motions: Array[String] = []
var active_motion_source: String = "procedural"
## Runtime-frame evidence for direct inspection and model-free validation.
## This never substitutes for the live world-state active source sample.
var motion_source_coverage: Dictionary = {}

var _model_root: Node3D
var _vrm_instance: Node
var _animation_player: AnimationPlayer
var _face_mesh: MeshInstance3D
var _fallback_body: MeshInstance3D
var _skeleton: Skeleton3D
var _time := 0.0
var _speech_until := 0.0
var _speech_text := ""
var _voice_player: AudioStreamPlayer
var _tts_request: HTTPRequest
var _speech_playback_request: HTTPRequest
var _speech_playback_queue: Array[Dictionary] = []
var _pending_tts_text := ""
var _active_tts_text := ""
var _active_tts_duration := 0.0
var _tts_sentence_queue: Array[String] = []
var _speech_utterance_id := ""
var _speech_phase := "idle"
var _lipsync_envelope: PackedFloat32Array = PackedFloat32Array()
var _lipsync_viseme_indices: PackedInt32Array = PackedInt32Array()
var _lipsync_envelope_frame_sec := 0.0
var _lipsync_voice_start_time := -1.0
var _voice_request_inflight := false
var _is_voice_playing := false
var _mouth_open_target := 0.0
var _mouth_shapes := ["Fcl_MTH_A", "Fcl_MTH_I", "Fcl_MTH_U", "Fcl_MTH_E", "Fcl_MTH_O"]
var _expression_shape_targets: Dictionary = {}
## Built once per loaded model: shape name -> [{"mesh": MeshInstance3D, "index": int}, ...].
## Lip sync writes five shapes every frame, so walking the full avatar tree in
## `_set_blend_shape` is prohibitively expensive on high-morph product VRMs.
var _blend_shape_bindings: Dictionary = {}
var _pose_bone_indices: Dictionary = {}
var _pose_bone_rest_rotations: Dictionary = {}
var _idle_blend := 0.0
var _idle_animation_name: String = ""
var _walk_animation_name: String = ""
var _active_motion_animation: String = ""
var _current_walk_speed_ratio := 1.0
var _motion_pose_blend := 0.0
var _model_root_base_y := 0.0
var _model_root_base_rotation := Vector3.ZERO
var _motion_state_transition := 0.0
var _vrma_idle_sampler
var _vrma_bone_indices: Dictionary = {}
var _vrma_bone_base_rotations: Dictionary = {}
var _vrma_samplers: Dictionary = {}
var _vrma_motion_bone_indices: Dictionary = {}
var _vrma_motion_base_rotations: Dictionary = {}

var _posture_state: String = ""
var _posture_gesture: String = ""
var _posture_requested_blend := 0.0
var _posture_blend := 0.0
var _posture_until := -1.0
var _talk_posture_blend := 0.0
var _gaze_has_target := false
var _gaze_hold_until := -1.0
var _gaze_target_yaw_deg := 0.0
var _gaze_target_pitch_deg := 0.0
var _gaze_current_yaw_deg := 0.0
var _gaze_current_pitch_deg := 0.0


func _ready() -> void:
	_fallback_body = get_node_or_null(fallback_body_path) as MeshInstance3D
	_setup_voice_audio()
	_load_model()


func _exit_tree() -> void:
	_clear_blend_shape_bindings()


func _process(delta: float) -> void:
	_time += delta
	_tick_motion(delta)
	_observe_active_motion_source(delta)
	_apply_expression_shape_targets()
	_tick_mouth(delta)


func reset_motion_source_coverage() -> void:
	# Keep the observed key set stable across a routine reset so a source cannot
	# disappear and later masquerade as first-seen coverage.
	for source in motion_source_coverage.keys():
		motion_source_coverage[source] = {
			"frames": 0,
			"seconds": 0.0,
		}


func get_motion_source_coverage() -> Dictionary:
	return motion_source_coverage.duplicate(true)


func _observe_active_motion_source(delta: float) -> void:
	var source := active_motion_source.strip_edges()
	if source.is_empty():
		return
	var entry: Dictionary = motion_source_coverage.get(source, {
		"frames": 0,
		"seconds": 0.0,
	})
	entry["frames"] = int(entry.get("frames", 0)) + 1
	entry["seconds"] = float(entry.get("seconds", 0.0)) + maxf(0.0, delta)
	motion_source_coverage[source] = entry


func play_animation(animation_name: String) -> bool:
	if animation_name.is_empty():
		return false
	var lowered := animation_name.to_lower()
	if lowered in ["idle", "walk"]:
		set_motion_state(animation_name)
		return true
	if _animation_player and _animation_player.has_animation(animation_name):
		_animation_player.play(animation_name)
		return true
	var mapped := _animation_name_for_expression(lowered)
	if _animation_player and _animation_player.has_animation(mapped):
		_animation_player.play(mapped)
		return true
	return false


func set_motion_state(motion_name: String, speed: float = 0.0) -> void:
	var normalized := motion_name.capitalize()
	if normalized != "Walk":
		normalized = "Idle"

	_current_walk_speed_ratio = _calc_walk_speed_ratio(speed)
	var target_animation := _animation_name_for_motion(normalized)
	if normalized == current_motion:
		if target_animation != "":
			if _active_motion_animation != target_animation and _animation_player and _active_motion_animation != target_animation:
				_active_motion_animation = target_animation
				_animation_player.play(target_animation, 0.1, 1.0 if normalized == "Idle" else _current_walk_speed_ratio)
			elif _active_motion_animation == target_animation and _animation_player:
				_animation_player.speed_scale = 1.0 if normalized == "Idle" else _current_walk_speed_ratio
			else:
				_active_motion_animation = target_animation
		return

	current_motion = normalized
	_motion_state_transition = _MOTION_STATE_TRANSITION_SEC
	_motion_pose_blend = 0.0
	_idle_blend = 0.0
	if _animation_player and not target_animation.is_empty():
		_active_motion_animation = target_animation
		var animation_speed := 1.0
		if normalized == "Walk":
			animation_speed = _current_walk_speed_ratio
		if _animation_player.current_animation != target_animation:
			_animation_player.play(target_animation, 0.1, animation_speed)
		else:
			_animation_player.speed_scale = animation_speed
	else:
		if _active_motion_animation != "" and _animation_player:
			# Stop only when an active motion clip is no longer available.
			_animation_player.stop()
		_active_motion_animation = ""
		if normalized != "Walk":
			_current_walk_speed_ratio = 1.0


func _calc_walk_speed_ratio(speed: float) -> float:
	if speed <= 0.0 or walk_reference_speed <= 0.0:
		return 1.0
	return clampf(speed / walk_reference_speed, walk_speed_ratio_min, walk_speed_ratio_max)


func set_expression(expression: String, intensity: float = 1.0) -> bool:
	if expression.is_empty():
		return false
	var requested := expression.to_lower()
	var normalized := _normalize_expression_name(requested)
	if normalized != requested:
		print("[SIVRMAvatarAdapter] fallback_neutral_or_alias from=%s to=%s" % [requested, normalized])
	current_expression = normalized
	var value := clampf(intensity, 0.0, 1.0)
	_expression_shape_targets.clear()
	_reset_expression_blends()
	var shapes := _blend_shapes_for_expression(current_expression)
	var applied := false
	for shape_name in shapes:
		_expression_shape_targets[shape_name] = value
		applied = _set_blend_shape(shape_name, value) or applied
	_apply_expression_shape_targets()
	var animation_name := _animation_name_for_expression(current_expression)
	# Native Route B trials expose the VRM0 preset names directly (A/I/U/E/O/
	# Blink). Playing the generated animation after setting those morphs can
	# immediately overwrite them back to zero before the acceptance screenshot.
	# Keep normal runtime behavior unchanged and retain the direct morph values
	# only in the isolated native-validation path.
	if applied and _is_route_b_native_trial_validation():
		if _animation_player:
			_animation_player.stop()
	elif _animation_player and _animation_player.has_animation(animation_name):
		_animation_player.play(animation_name)
		applied = true
	return applied


func _normalize_expression_name(expression: String) -> String:
	var raw := expression.strip_edges().to_lower()
	var aliases := {
		"joy": "happy",
		"glad": "happy",
		"fun": "happy",
		"sorrow": "sad",
		"mad": "angry",
		"shock": "surprised",
		"surprise": "surprised",
		"serious": "thinking",
		"calm": "relaxed",
		"normal": "neutral",
	}
	var mapped := String(aliases.get(raw, raw))
	var allowed := [
		"neutral", "happy", "smile", "sad", "angry", "surprised",
		"relaxed", "blink", "thinking", "shy", "confused",
		"aa", "ih", "ou", "ee", "oh", "a", "i", "u", "e", "o",
	]
	if mapped in allowed:
		return mapped
	return "neutral"


func apply_motion_profile(profile: Dictionary) -> bool:
	var profile_name := String(profile.get("name", profile.get("profile", "custom"))).strip_edges()
	if profile_name.is_empty():
		profile_name = "custom"
	var profile_params: Dictionary = profile.get("params", {})
	if profile_params.is_empty():
		profile_params = profile
	var applied := 0
	for key in profile_params.keys():
		var param_name := String(key)
		var raw_value = profile_params[key]
		if param_name.begins_with("enable_") and typeof(raw_value) == TYPE_BOOL and typeof(get(param_name)) == TYPE_BOOL:
			set(param_name, bool(raw_value))
			applied += 1
			continue
		if param_name == "idle_shoulder_forward_deg" and (typeof(raw_value) == TYPE_FLOAT or typeof(raw_value) == TYPE_INT):
			var forward_value := clampf(float(raw_value), -80.0, 80.0)
			set(param_name, forward_value)
			applied += 1
			continue
		if not PROFILE_PARAM_RANGES.has(param_name):
			continue
		if typeof(raw_value) != TYPE_FLOAT and typeof(raw_value) != TYPE_INT:
			continue
		var bounds: Vector2 = PROFILE_PARAM_RANGES[param_name]
		set(param_name, clampf(float(raw_value), bounds.x, bounds.y))
		applied += 1
	if applied <= 0:
		return false
	clear_posture_state(true)
	_motion_pose_blend = 0.0
	_idle_blend = 0.0
	_talk_posture_blend = 0.0
	_posture_requested_blend = 0.0
	_posture_blend = 0.0
	_posture_state = ""
	_posture_gesture = ""
	_posture_until = -1.0
	current_motion_profile = profile_name
	current_motion_profile_notes = String(profile.get("notes", profile.get("intent", ""))).strip_edges()
	return true


func set_posture_state(state: String, params: Dictionary = {}) -> bool:
	var normalized := String(state).strip_edges().to_lower()
	if normalized.is_empty():
		return false

	match normalized:
		"gesture":
			var gesture_name := String(params.get("gesture", "wave")).strip_edges().to_lower()
			var hold_sec := float(params.get("duration", gesture_hold_sec))
			return set_gesture_posture(gesture_name, hold_sec)
		"sit":
			return set_sit_posture(float(params.get("duration", -1.0)))
		"stand":
			return set_stand_posture(float(params.get("duration", -1.0)))
		"idle", "clear", "neutral", "none", "recover":
			clear_posture_state(true)
			return true
		_:
			return false


func set_gesture_posture(name: String, hold_sec: float = -1.0) -> bool:
	var gesture_name := String(name).strip_edges().to_lower()
	if gesture_name.is_empty():
		return false
	_posture_gesture = gesture_name
	var duration := hold_sec if hold_sec > 0.0 else gesture_hold_sec
	var hold_time := _time + maxf(posture_decay_sec, duration)
	_posture_until = hold_time
	_posture_state = "gesture"
	_posture_requested_blend = 1.0
	_posture_blend = _posture_blend if _posture_blend > 0.0 else 0.0
	return true


func set_sit_posture(duration_sec: float = -1.0) -> bool:
	var hold_sec := duration_sec if duration_sec > 0.0 else -1.0
	_posture_state = "sit"
	_posture_gesture = ""
	_posture_requested_blend = 1.0
	_posture_until = _time + hold_sec if hold_sec > 0.0 else -1.0
	_posture_blend = _posture_blend if _posture_blend > 0.0 else 0.0
	return true


func set_stand_posture(duration_sec: float = -1.0) -> bool:
	var hold_sec := duration_sec if duration_sec > 0.0 else -1.0
	_posture_state = "stand"
	_posture_gesture = ""
	_posture_requested_blend = 1.0
	_posture_until = _time + hold_sec if hold_sec > 0.0 else -1.0
	_posture_blend = _posture_blend if _posture_blend > 0.0 else 0.0
	return true


func clear_posture_state(immediate: bool = false) -> void:
	if immediate:
		active_motion_source = "procedural:restore"
		_motion_pose_blend = 0.0
		_current_walk_speed_ratio = 1.0
		_posture_blend = 0.0
		_posture_requested_blend = 0.0
		_talk_posture_blend = 0.0
		_idle_blend = 0.0
		_posture_until = -1.0
		_motion_state_transition = _MOTION_STATE_TRANSITION_SEC
	_clear_posture_state()


func set_gaze_direction(yaw_deg: float, pitch_deg: float = 0.0, hold_sec: float = 0.45) -> bool:
	if not enable_procedural_gaze_motion:
		return false
	_gaze_target_yaw_deg = clampf(yaw_deg, -gaze_neck_yaw_limit_deg - gaze_head_yaw_limit_deg, gaze_neck_yaw_limit_deg + gaze_head_yaw_limit_deg)
	_gaze_target_pitch_deg = clampf(pitch_deg, -gaze_pitch_limit_deg, gaze_pitch_limit_deg)
	_gaze_has_target = true
	_gaze_hold_until = _time + maxf(0.12, hold_sec)
	return true


func clear_gaze_target() -> void:
	_gaze_has_target = false
	_gaze_hold_until = -1.0


func _clear_posture_state() -> void:
	_posture_blend = 0.0
	_posture_state = ""
	_posture_gesture = ""
	_posture_requested_blend = 0.0
	_posture_until = -1.0


func speak(text: String) -> bool:
	if text.is_empty():
		return false
	# Replace current utterance with sentence-queued playback (句点単位).
	stop_tts_audio()
	_tts_sentence_queue.clear()
	var sentences := _split_speech_sentences(text)
	if sentences.is_empty():
		sentences = [text]
	for s in sentences:
		_tts_sentence_queue.append(s)
	last_speech_text = text
	_speech_text = text
	_speech_phase = "speak_start"
	_speech_utterance_id = "%d" % Time.get_ticks_msec()
	_speech_until = _time + clampf(0.8 + float(text.length()) * 0.055, 1.0, 4.0)
	_active_tts_text = ""
	_active_tts_duration = 0.0
	_pending_tts_text = ""
	_voice_request_inflight = false
	_is_voice_playing = false
	_lipsync_envelope.clear()
	_lipsync_viseme_indices.clear()
	_lipsync_envelope_frame_sec = 0.0
	_lipsync_voice_start_time = -1.0
	_play_next_queued_sentence()
	return true


func enqueue_speech_sentence(text: String) -> bool:
	"""Incremental 句点 feed without cancelling current playback."""
	var cleaned := text.strip_edges()
	if cleaned.is_empty():
		return false
	if _speech_phase == "idle" or _speech_phase == "speak_end" or _speech_phase == "speak_cancel":
		return speak(cleaned)
	_tts_sentence_queue.append(cleaned)
	_speech_phase = "speak_mid"
	if not _is_tts_speaking() and _pending_tts_text.is_empty():
		_play_next_queued_sentence()
	return true


func _split_speech_sentences(text: String) -> Array[String]:
	var out: Array[String] = []
	var buf := ""
	for i in text.length():
		var ch := text.substr(i, 1)
		buf += ch
		if ch == "。" or ch == "！" or ch == "？" or ch == "!" or ch == "?":
			var chunk := buf.strip_edges()
			if not chunk.is_empty():
				out.append(chunk)
			buf = ""
	var rest := buf.strip_edges()
	if not rest.is_empty():
		out.append(rest)
	return out


func _play_next_queued_sentence() -> void:
	if _tts_sentence_queue.is_empty():
		_speech_phase = "speak_end"
		return
	var next := String(_tts_sentence_queue.pop_front())
	_speech_phase = "speak_mid" if not _tts_sentence_queue.is_empty() else "speak_mid"
	_request_tts_audio(next)


func apply_audio_settings(settings: Dictionary) -> bool:
	var applied := false
	if settings.has("volume_db"):
		tts_volume_db = clampf(float(settings.get("volume_db")), -36.0, 12.0)
		if _voice_player:
			_voice_player.volume_db = tts_volume_db
		applied = true
	if settings.has("speed"):
		tts_speed = clampf(float(settings.get("speed")), 0.75, 1.35)
		applied = true
	if settings.has("muted"):
		enable_tts_audio = not bool(settings.get("muted"))
		if not enable_tts_audio:
			stop_tts_audio()
		applied = true
	return applied


func stop_tts_audio() -> void:
	if _is_tts_speaking():
		_post_speech_playback("speak_cancel", _active_tts_text if not _active_tts_text.is_empty() else _pending_tts_text, 0.0)
	if _tts_request and _tts_request.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		_tts_request.cancel_request()
	_pending_tts_text = ""
	_tts_sentence_queue.clear()
	_speech_phase = "speak_cancel"
	_stop_tts_speaking()


func _setup_voice_audio() -> void:
	if not _voice_player:
		_voice_player = AudioStreamPlayer.new()
		_voice_player.name = "SIVoicePlayer"
		_voice_player.bus = "Master"
		_voice_player.volume_db = tts_volume_db
		add_child(_voice_player)
		_voice_player.finished.connect(_on_voice_player_finished)
	if not _tts_request:
		_tts_request = HTTPRequest.new()
		_tts_request.name = "SITTSRequest"
		_tts_request.timeout = _resolved_tts_request_timeout_seconds()
		add_child(_tts_request)
		_tts_request.request_completed.connect(_on_tts_request_completed)
	if not _speech_playback_request:
		_speech_playback_request = HTTPRequest.new()
		_speech_playback_request.name = "SISpeechPlaybackTelemetry"
		_speech_playback_request.timeout = 1.5
		add_child(_speech_playback_request)
		_speech_playback_request.request_completed.connect(_on_speech_playback_request_completed)


func _resolved_tts_request_timeout_seconds() -> float:
	# Separate from the server's 300s timeout: exhibit requests remain bounded.
	var configured := tts_request_timeout_seconds
	var env_value := OS.get_environment("LUMINA_GODOT_TTS_REQUEST_TIMEOUT_SECONDS").strip_edges()
	if env_value.is_valid_float():
		configured = float(env_value)
	if not is_finite(configured):
		configured = 20.0
	return clampf(configured, 1.0, 60.0)


func _fail_tts_utterance(text: String, detail: String, deferred: bool = false) -> void:
	# Finish all failed state before callbacks can synchronously start new speech.
	_pending_tts_text = ""
	_tts_sentence_queue.clear()
	_speech_phase = "speak_end"
	_stop_tts_speaking()
	if deferred:
		call_deferred("_emit_tts_playback_finished", text, false, detail)
	else:
		_emit_tts_playback_finished(text, false, detail)


func _request_tts_audio(text: String) -> void:
	if not enable_tts_audio or tts_voice_url.strip_edges().is_empty():
		_fail_tts_utterance(text, "TTS audio is disabled", true)
		return
	if not _tts_request:
		_setup_voice_audio()
	if not _tts_request:
		_fail_tts_utterance(text, "TTS request node unavailable", true)
		return
	if _tts_request.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		_tts_request.cancel_request()
	_voice_request_inflight = true
	_lipsync_envelope.clear()
	_lipsync_viseme_indices.clear()
	_lipsync_envelope_frame_sec = 0.0
	_pending_tts_text = text
	var payload := {
		"text": text,
		"speed": tts_speed,
		"emotion": current_expression,
	}
	var err := _tts_request.request(
		tts_voice_url,
		["Content-Type: application/json"],
		HTTPClient.METHOD_POST,
		JSON.stringify(payload)
	)
	if err != OK:
		push_warning("[SIVRMAvatarAdapter] TTS request failed to start: %s" % error_string(err))
		_fail_tts_utterance(text, "TTS request failed to start: %s" % error_string(err), true)
	else:
		print("[SIVRMAvatarAdapter] TTS request started: chars=%d url=%s" % [text.length(), tts_voice_url])


func _on_tts_request_completed(result: int, response_code: int, headers: PackedStringArray, body: PackedByteArray) -> void:
	var request_text := _pending_tts_text
	if request_text.is_empty():
		_voice_request_inflight = false
		print("[SIVRMAvatarAdapter] discarded cancelled TTS response")
		return
	if result != HTTPRequest.RESULT_SUCCESS or response_code < 200 or response_code >= 300:
		push_warning("[SIVRMAvatarAdapter] TTS request failed: result=%s code=%s" % [result, response_code])
		_fail_tts_utterance(request_text, "TTS request failed: result=%s code=%s" % [result, response_code])
		return
	var audio_body := body
	var lipsync_payload: Dictionary = {}
	var parsed_response := _parse_tts_lipsync_response(headers, body)
	if not parsed_response.is_empty():
		audio_body = parsed_response.get("audio", body)
		lipsync_payload = parsed_response.get("lipsync", {})
	var stream := _audio_stream_wav_from_bytes(audio_body)
	if not stream:
		push_warning("[SIVRMAvatarAdapter] TTS returned unsupported WAV data")
		_fail_tts_utterance(request_text, "TTS returned unsupported WAV data")
		return
	if not _voice_player:
		_setup_voice_audio()
	if not _voice_player:
		push_warning("[SIVRMAvatarAdapter] TTS voice player is unavailable")
		_fail_tts_utterance(request_text, "TTS voice player is unavailable")
		return

	var duration := 0.0
	if not lipsync_payload.is_empty():
		duration = _prepare_lipsync_profile_from_payload(lipsync_payload)
	if duration <= 0.0:
		duration = _prepare_lipsync_profile(audio_body)
	if duration <= 0.0:
		duration = _wav_duration_seconds(audio_body)

	_active_tts_text = request_text
	_voice_player.volume_db = tts_volume_db
	_voice_player.stream = stream
	_voice_player.play()
	_active_tts_duration = duration
	print("[SIVRMAvatarAdapter] TTS playback started: bytes=%d duration=%.2fs lipsync_frames=%d volume=%.1fdB" % [audio_body.size(), duration, _lipsync_envelope.size(), tts_volume_db])
	_is_voice_playing = true
	_voice_request_inflight = false
	_lipsync_voice_start_time = _time
	_pending_tts_text = ""
	_post_speech_playback("speak_start", request_text, duration)
	if duration > 0.0:
		_speech_until = maxf(_speech_until, _time + duration + 0.15)
	elif _speech_until < _time:
		_speech_until = _time + 0.05


func _on_voice_player_finished() -> void:
	if not _is_voice_playing:
		return
	if _active_tts_text.is_empty():
		return
	var finished_text := _active_tts_text
	var duration := _active_tts_duration
	var detail := "TTS playback finished"
	if duration > 0.0:
		detail = "TTS playback finished; duration=%.2fs; lipsync_frames=%d; lipsync_frame_sec=%.4f; lipsync_offset=%.3f" % [duration, _lipsync_envelope.size(), _lipsync_envelope_frame_sec, lipsync_time_offset_sec]
	_post_speech_playback("speak_end", finished_text, duration)
	_stop_tts_speaking()
	if not _tts_sentence_queue.is_empty():
		_play_next_queued_sentence()
		return
	_speech_phase = "speak_end"
	_emit_tts_playback_finished(finished_text, true, detail)


func _is_tts_speaking() -> bool:
	if _is_voice_playing:
		return true
	if _voice_player and _voice_player.playing:
		return true
	if _voice_request_inflight:
		return true
	return _speech_until > _time


func _stop_tts_speaking() -> void:
	if _voice_player and _voice_player.playing:
		_voice_player.stop()
	_is_voice_playing = false
	_voice_request_inflight = false
	_lipsync_voice_start_time = -1.0
	_mouth_open_target = 0.0
	_active_tts_duration = 0.0
	_active_tts_text = ""
	if _lipsync_envelope.size() > 0:
		_lipsync_envelope.clear()
	if _lipsync_viseme_indices.size() > 0:
		_lipsync_viseme_indices.clear()
	_lipsync_envelope_frame_sec = 0.0
	_speech_until = _time


func _post_speech_playback(phase: String, text: String = "", duration_seconds: float = 0.0) -> void:
	var url := speech_playback_url.strip_edges()
	if url.is_empty():
		return
	if not _speech_playback_request:
		_setup_voice_audio()
	if not _speech_playback_request:
		push_warning("[SIVRMAvatarAdapter] playback telemetry request node unavailable phase=%s" % phase)
		return
	_speech_playback_queue.append({
		"phase": phase,
		"text": text,
		"duration_seconds": maxf(0.0, duration_seconds),
		"source": "godot_playback",
	})
	_flush_speech_playback_queue()


func _flush_speech_playback_queue() -> void:
	if not _speech_playback_request or _speech_playback_queue.is_empty():
		return
	if _speech_playback_request.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		return
	var url := speech_playback_url.strip_edges()
	if url.is_empty():
		_speech_playback_queue.clear()
		return
	var payload: Dictionary = _speech_playback_queue.pop_front()
	var err := _speech_playback_request.request(
		url,
		["Content-Type: application/json"],
		HTTPClient.METHOD_POST,
		JSON.stringify(payload),
	)
	if err != OK:
		push_warning("[SIVRMAvatarAdapter] playback telemetry failed to start phase=%s error=%s" % [payload.get("phase", ""), error_string(err)])
		call_deferred("_flush_speech_playback_queue")


func _on_speech_playback_request_completed(result: int, response_code: int, _headers: PackedStringArray, _body: PackedByteArray) -> void:
	if result != HTTPRequest.RESULT_SUCCESS or response_code < 200 or response_code >= 300:
		push_warning("[SIVRMAvatarAdapter] playback telemetry failed result=%s code=%s" % [result, response_code])
	else:
		print("[SIVRMAvatarAdapter] playback telemetry acknowledged code=%s" % response_code)
	call_deferred("_flush_speech_playback_queue")


func _parse_tts_lipsync_response(headers: PackedStringArray, body: PackedByteArray) -> Dictionary:
	if body.size() <= 0:
		return {}
	var content_type := _header_value(headers, "content-type").to_lower()
	var looks_like_json := content_type.find("application/json") >= 0
	if not looks_like_json and body.size() > 0:
		looks_like_json = int(body[0]) == 123
	if not looks_like_json:
		return {}
	var json_text := body.get_string_from_utf8()
	var parsed = JSON.parse_string(json_text)
	if typeof(parsed) != TYPE_DICTIONARY:
		push_warning("[SIVRMAvatarAdapter] TTS JSON response was not an object")
		return {}
	var payload: Dictionary = parsed
	var audio_base64 := String(payload.get("audio_base64", ""))
	if audio_base64.is_empty():
		push_warning("[SIVRMAvatarAdapter] TTS JSON response did not include audio_base64")
		return {}
	var audio_bytes := Marshalls.base64_to_raw(audio_base64)
	if audio_bytes.size() <= 0:
		push_warning("[SIVRMAvatarAdapter] TTS JSON audio_base64 decoded to empty bytes")
		return {}
	return {
		"audio": audio_bytes,
		"lipsync": payload.get("lipsync", {}),
		"duration": float(payload.get("duration_seconds", 0.0)),
	}


func _header_value(headers: PackedStringArray, wanted_name: String) -> String:
	var prefix := wanted_name.to_lower() + ":"
	for header in headers:
		var header_text := String(header)
		if header_text.to_lower().begins_with(prefix):
			return header_text.substr(prefix.length()).strip_edges()
	return ""


func _current_lipsync_time(_current_time: float) -> float:
	if _lipsync_voice_start_time < 0.0:
		return -1.0
	var normalized_time := _current_time - _lipsync_voice_start_time
	if _voice_player and _voice_player.playing:
		normalized_time = _voice_player.get_playback_position()
	normalized_time += lipsync_time_offset_sec
	return normalized_time


func _current_lipsync_level(_current_time: float) -> float:
	if _lipsync_envelope.size() <= 0 or _lipsync_envelope_frame_sec <= 0.0:
		return 0.0
	var normalized_time := _current_lipsync_time(_current_time)
	if normalized_time < 0.0:
		return 0.0
	if normalized_time <= 0.0:
		return _lipsync_envelope[0]
	var end_time := float(_lipsync_envelope.size() - 1) * _lipsync_envelope_frame_sec
	if normalized_time >= end_time:
		return _lipsync_envelope[_lipsync_envelope.size() - 1]
	var raw_position := normalized_time / _lipsync_envelope_frame_sec
	var i0 := int(floor(raw_position))
	var i1: int = mini(i0 + 1, _lipsync_envelope.size() - 1)
	var alpha := clampf(raw_position - i0, 0.0, 1.0)
	return lerpf(_lipsync_envelope[i0], _lipsync_envelope[i1], alpha)


func _current_lipsync_viseme_index(_current_time: float) -> int:
	if _lipsync_viseme_indices.size() <= 0 or _lipsync_envelope_frame_sec <= 0.0:
		return -1
	var normalized_time := _current_lipsync_time(_current_time)
	if normalized_time < 0.0:
		return -1
	var raw_position := normalized_time / _lipsync_envelope_frame_sec
	var frame_index := int(round(raw_position))
	frame_index = mini(maxi(frame_index, 0), _lipsync_viseme_indices.size() - 1)
	return int(_lipsync_viseme_indices[frame_index])


func _prepare_lipsync_profile_from_payload(lipsync_payload: Dictionary) -> float:
	var frames_value = lipsync_payload.get("frames", [])
	if typeof(frames_value) != TYPE_ARRAY:
		return 0.0
	var frames: Array = frames_value
	if frames.is_empty():
		return 0.0
	var frame_sec := float(lipsync_payload.get("frame_sec", 0.0))
	if frame_sec <= 0.0:
		var fps := float(lipsync_payload.get("fps", 0.0))
		if fps > 0.0:
			frame_sec = 1.0 / fps
	if frame_sec <= 0.0:
		frame_sec = 1.0 / maxf(1.0, lipsync_profile_fps)

	var profile := PackedFloat32Array()
	var visemes := PackedInt32Array()
	profile.resize(0)
	visemes.resize(0)
	for frame in frames:
		var level := 0.0
		var viseme_index := 0
		if typeof(frame) == TYPE_ARRAY:
			var values: Array = frame
			if values.size() > 0:
				level = float(values[0])
			if values.size() > 1:
				viseme_index = int(values[1])
		elif typeof(frame) == TYPE_DICTIONARY:
			var item: Dictionary = frame
			level = float(item.get("open", item.get("level", 0.0)))
			viseme_index = int(item.get("viseme_index", item.get("viseme", 0)))
		else:
			level = float(frame)
		profile.append(clampf(level, 0.0, 1.0))
		visemes.append(maxi(0, viseme_index))

	if profile.is_empty():
		return 0.0
	_lipsync_envelope = profile
	_lipsync_viseme_indices = visemes
	_lipsync_envelope_frame_sec = frame_sec
	var duration := float(lipsync_payload.get("duration_seconds", 0.0))
	if duration <= 0.0:
		duration = float(profile.size()) * frame_sec
	return duration


func _prepare_lipsync_profile(data: PackedByteArray) -> float:
	var wav_info := _parse_wav_chunks(data)
	if wav_info.is_empty():
		return 0.0
	var sample_rate := int(wav_info.sample_rate)
	var channels := int(wav_info.channels)
	var bits_per_sample := int(wav_info.bits_per_sample)
	var pcm_offset := int(wav_info.pcm_offset)
	var pcm_size := int(wav_info.pcm_size)

	var bytes_per_sample: int = bits_per_sample / 8
	if bytes_per_sample <= 0 or sample_rate <= 0 or channels <= 0:
		return 0.0
	var frame_count := int(floor(float(pcm_size) / float(maxi(1, channels * bytes_per_sample))))
	if frame_count <= 0:
		return 0.0

	var fps := maxf(30.0, lipsync_profile_fps)
	var frame_step: int = maxi(1, int(float(sample_rate) / fps))
	_lipsync_envelope_frame_sec = float(frame_step) / float(sample_rate)
	var profile := PackedFloat32Array()
	profile.resize(0)
	for start_frame in range(0, frame_count, frame_step):
		var end_frame := mini(start_frame + frame_step, frame_count)
		var total := 0.0
		var sample_count := 0
		for frame_index in range(start_frame, end_frame):
			for channel_index in range(channels):
				var byte_pos := pcm_offset + ((frame_index * channels) + channel_index) * bytes_per_sample
				if byte_pos + bytes_per_sample > pcm_offset + pcm_size or byte_pos + bytes_per_sample > data.size():
					continue
				if bits_per_sample == 8:
					var sample := float(int(data[byte_pos]) - 128) / 128.0
					total += absf(sample)
					sample_count += 1
				else:
					var sample := float(_i16_le(data, byte_pos)) / 32768.0
					total += absf(sample)
					sample_count += 1
		if sample_count > 0:
			var level := total / float(sample_count)
			profile.append(clampf(level * lipsync_volume_scale, 0.0, 1.0))
	
	if profile.is_empty():
		_lipsync_envelope = PackedFloat32Array()
		_lipsync_viseme_indices = PackedInt32Array()
		_lipsync_envelope_frame_sec = 0.0
		return 0.0

	_lipsync_envelope = profile
	_lipsync_viseme_indices = PackedInt32Array()
	return float(frame_count) / float(sample_rate)


func _emit_tts_playback_finished(text: String, ok: bool, detail: String) -> void:
	if text.is_empty():
		return
	if text == _active_tts_text:
		_active_tts_text = ""
		_active_tts_duration = 0.0
	emit_signal("si_tts_playback_finished", text, ok, detail)



func _parse_wav_chunks(data: PackedByteArray) -> Dictionary:
	if data.size() < 44:
		return {}
	if _ascii_chunk(data, 0, 4) != "RIFF" or _ascii_chunk(data, 8, 4) != "WAVE":
		return {}
	var fmt_offset := -1
	var fmt_size := 0
	var pcm_offset := -1
	var pcm_size := 0
	var offset := 12
	while offset + 8 <= data.size():
		var chunk_id := _ascii_chunk(data, offset, 4)
		var chunk_size := _u32_le(data, offset + 4)
		var chunk_data_offset := offset + 8
		if chunk_data_offset + chunk_size > data.size():
			break
		if chunk_id == "fmt ":
			fmt_offset = chunk_data_offset
			fmt_size = chunk_size
		elif chunk_id == "data":
			pcm_offset = chunk_data_offset
			pcm_size = chunk_size
			break
		offset = chunk_data_offset + chunk_size + (chunk_size % 2)
	if fmt_offset < 0 or fmt_size < 16 or pcm_offset < 0 or pcm_size <= 0:
		return {}

	var channels := _u16_le(data, fmt_offset + 2)
	var sample_rate := _u32_le(data, fmt_offset + 4)
	var bits_per_sample := _u16_le(data, fmt_offset + 14)
	var audio_format := _u16_le(data, fmt_offset)
	return {
		"format": audio_format,
		"channels": channels,
		"sample_rate": sample_rate,
		"bits_per_sample": bits_per_sample,
		"pcm_offset": pcm_offset,
		"pcm_size": pcm_size,
	}


func _audio_stream_wav_from_bytes(data: PackedByteArray) -> AudioStreamWAV:
	var wav_info := _parse_wav_chunks(data)
	if wav_info.is_empty():
		return null
	var channels := int(wav_info.channels)
	var sample_rate := int(wav_info.sample_rate)
	var bits_per_sample := int(wav_info.bits_per_sample)
	var audio_format := int(wav_info.format)
	var pcm_offset := int(wav_info.pcm_offset)
	var pcm_size := int(wav_info.pcm_size)

	if audio_format != 1:
		push_warning("[SIVRMAvatarAdapter] TTS WAV is not PCM: %s" % audio_format)
		return null
	if channels < 1 or channels > 2:
		push_warning("[SIVRMAvatarAdapter] TTS WAV channel count unsupported: %s" % channels)
		return null
	if bits_per_sample != 8 and bits_per_sample != 16:
		push_warning("[SIVRMAvatarAdapter] TTS WAV bit depth unsupported: %s" % bits_per_sample)
		return null

	var stream := AudioStreamWAV.new()
	stream.mix_rate = sample_rate
	stream.stereo = channels == 2
	stream.format = AudioStreamWAV.FORMAT_8_BITS if bits_per_sample == 8 else AudioStreamWAV.FORMAT_16_BITS
	stream.data = data.slice(pcm_offset, pcm_offset + pcm_size)
	return stream


func _wav_duration_seconds(data: PackedByteArray) -> float:
	var wav_info := _parse_wav_chunks(data)
	if wav_info.is_empty():
		return 0.0
	var channels := int(wav_info.channels)
	var sample_rate := int(wav_info.sample_rate)
	var bits_per_sample := int(wav_info.bits_per_sample)
	var pcm_size := int(wav_info.pcm_size)
	var format := int(wav_info.format)
	if format != 1:
		return 0.0
	var bytes_per_sample: int = maxi(1, bits_per_sample / 8)
	var bytes_per_second: int = sample_rate * channels * bytes_per_sample
	if bytes_per_second <= 0:
		return 0.0
	return float(pcm_size) / float(bytes_per_second)


func _ascii_chunk(data: PackedByteArray, offset: int, length: int) -> String:
	if offset < 0 or offset + length > data.size():
		return ""
	return data.slice(offset, offset + length).get_string_from_ascii()


func _u16_le(data: PackedByteArray, offset: int) -> int:
	if offset + 2 > data.size():
		return 0
	return int(data[offset]) | (int(data[offset + 1]) << 8)


func _u32_le(data: PackedByteArray, offset: int) -> int:
	if offset + 4 > data.size():
		return 0
	return int(data[offset]) | (int(data[offset + 1]) << 8) | (int(data[offset + 2]) << 16) | (int(data[offset + 3]) << 24)


func _i16_le(data: PackedByteArray, offset: int) -> int:
	if offset + 2 > data.size():
		return 0
	var value := int(data[offset]) | (int(data[offset + 1]) << 8)
	if value >= 32768:
		value -= 65536
	return value


func _load_model() -> void:
	# A model reload must never retain MeshInstance3D references from the prior
	# scene. `_collect_capabilities` repopulates the cache after the new model is
	# fully instantiated and compatibility overlays have been attached.
	_clear_blend_shape_bindings()
	_model_root = Node3D.new()
	_model_root.name = "AvatarModelRoot"
	_model_root.scale = Vector3.ONE * model_scale
	_model_root.rotation_degrees.y = model_yaw_degrees
	add_child(_model_root)

	_vrm_instance = _instantiate_vrm_scene(vrm_scene_path)
	if _vrm_instance == null:
		return
	_vrm_instance.name = "avatar_vrm"
	_model_root.add_child(_vrm_instance)
	_log_material_diagnostics("pre_tune")
	var native_trial_validation := _is_route_b_native_trial_validation()
	if _is_route_b_candidate_vrm():
		if not native_trial_validation:
			_normalize_route_b_candidate_materials()
			_attach_route_b_overlay_meshes_to_head()
	# The approved WIP15 is a frozen authored appearance, not another shading
	# trial. Keep its native MToon materials byte-for-byte in the packed asset.
	if not vrm_scene_path.contains("lumina_stella_wip_20260906_wip15/"):
		_apply_realistic_model_shading()
	else:
		print("[SIVRMAvatarAdapter] frozen_wip15 authored_materials=preserved")
	if _is_route_b_candidate_vrm() and not native_trial_validation:
		# Realistic shading re-duplicates materials; re-assert body gain + hair anti-speckle.
		_reassert_route_b_materials_after_realistic_shading()
		# Midface shell DEFAULT OFF (Accept8 mesh-replace lane). Opt-in: LUMINA_ROUTE_B_MIDFACE_SHELL=1.
		# Do not mask broken midface with overlays — judge the real VRM mesh only.
		var shell_env := OS.get_environment("LUMINA_ROUTE_B_MIDFACE_SHELL").strip_edges().to_lower()
		if shell_env in ["1", "true", "yes", "on"]:
			_attach_route_b_midface_shell()
		else:
			print("[SIVRMAvatarAdapter] route_b_midface_shell=off (default)")
	if native_trial_validation:
		# Isolated validation must judge the materials and skinned expression meshes
		# authored inside the exported VRM. The legacy Route B compatibility path
		# intentionally replaces textured hair with a flat no-UV material and
		# recentres/shrinks overlay-named meshes, which would invalidate this test.
		# The environment flag is opt-in; live/default behaviour is unchanged.
		print("[SIVRMAvatarAdapter] route_b_native_trial_validation=on compatibility_overrides=skipped")
	_log_material_diagnostics("post_tune")
	_animation_player = _find_first_of_type(_vrm_instance, "AnimationPlayer") as AnimationPlayer
	_face_mesh = _find_face_mesh(_vrm_instance)
	_collect_capabilities()
	_resolve_motion_animations()
	_setup_idle_pose()
	_setup_vrma_motion_bank()
	_ensure_vrma_motion_fallbacks()
	if align_feet_to_origin:
		_align_feet()

	_model_root_base_y = _model_root.position.y
	_model_root_base_rotation = _model_root.rotation_degrees
	print("[SIVRMAvatarAdapter] model_yaw_degrees=%.1f applied=%s" % [
		model_yaw_degrees,
		str(_model_root.rotation_degrees.y),
	])
	if _fallback_body:
		_fallback_body.visible = false
	loaded = true
	set_expression("neutral", 1.0)


func _instantiate_vrm_scene(path: String) -> Node:
	## Prefer editor-imported PackedScene; fall back to runtime GLTF+VRM for
	## candidate / env-only VRM files that lack .import + .godot/imported.
	## Multi-mesh VRM candidates often need a baked sibling `*_preview.tscn`
	## (see tools/bake_star_idol_rc2_preview_20260809.gd) — prefer that over
	## raw GLTFDocument, which can explode skins / wash face materials.
	var normalized := path.strip_edges()
	if normalized.is_empty():
		push_warning("SIVRMAvatarAdapter: VRM scene path is empty")
		return null

	var preview_path := _vrm_sibling_preview_path(normalized)
	if preview_path != "" and ResourceLoader.exists(preview_path):
		var preview_res = load(preview_path)
		if preview_res is PackedScene:
			print("[SIVRMAvatarAdapter] vrm_load=ResourceLoaderPreview path=%s" % preview_path)
			return (preview_res as PackedScene).instantiate()

	if ResourceLoader.exists(normalized):
		var resource = load(normalized)
		if resource is PackedScene:
			print("[SIVRMAvatarAdapter] vrm_load=ResourceLoader path=%s" % normalized)
			return (resource as PackedScene).instantiate()
		push_warning("SIVRMAvatarAdapter: VRM is not imported as PackedScene yet: %s" % normalized)

	var abs_path := _resolve_vrm_filesystem_path(normalized)
	if abs_path.is_empty() or not FileAccess.file_exists(abs_path):
		push_warning("SIVRMAvatarAdapter: VRM scene not found: %s" % normalized)
		return null

	var generated := _load_vrm_via_gltf_document(abs_path)
	if generated == null:
		push_warning("SIVRMAvatarAdapter: VRM GLTFDocument load failed: %s" % abs_path)
		return null
	print("[SIVRMAvatarAdapter] vrm_load=GLTFDocument path=%s" % abs_path)
	return generated


func _vrm_sibling_preview_path(path: String) -> String:
	var normalized := path.strip_edges()
	if not normalized.to_lower().ends_with(".vrm"):
		return ""
	var base := normalized.substr(0, normalized.length() - 4)
	# Prefer binary compressed .scn (Living Hub-safe). Huge text .tscn can OOM.
	var scn_path := base + "_preview.scn"
	if ResourceLoader.exists(scn_path):
		return scn_path
	var tscn_path := base + "_preview.tscn"
	if ResourceLoader.exists(tscn_path):
		return tscn_path
	return ""


func _resolve_vrm_filesystem_path(path: String) -> String:
	if path.begins_with("res://") or path.begins_with("user://"):
		return ProjectSettings.globalize_path(path)
	if path.is_absolute_path():
		return path
	return ProjectSettings.globalize_path("res://" + path.trim_prefix("./"))


func _load_vrm_via_gltf_document(abs_path: String) -> Node:
	## Same runtime path as tools/lumina_route_b_direct_import_validate_20260718.gd
	## and addons/vrm/import_vrm.gd — does not require .import sidecars.
	var gltf := GLTFDocument.new()
	var vrm_extension: GLTFDocumentExtension = VRM_EXTENSION.new()
	gltf.register_gltf_document_extension(vrm_extension, true)

	var state := GLTFState.new()
	# Optional overrides for candidate VRM bring-up (default keeps prior ThirdPersonOnly path).
	#   LUMINA_VRM_HEAD_HIDE=0  -> disable first-person head hiding layers
	#   LUMINA_VRM_NAMED_SKINS=0 -> append_from_file without IMPORT_USE_NAMED_SKIN_BINDS
	var head_hide := OS.get_environment("LUMINA_VRM_HEAD_HIDE").strip_edges().to_lower()
	if head_hide in ["0", "false", "off", "no"]:
		print("[SIVRMAvatarAdapter] vrm_load_opt head_hide=off")
	else:
		state.set_additional_data(&"vrm/head_hiding_method", VRM_CONSTANTS.HeadHidingSetting.ThirdPersonOnly)
		state.set_additional_data(&"vrm/first_person_layers", 2)
		state.set_additional_data(&"vrm/third_person_layers", 4)
	state.handle_binary_image = GLTFState.HANDLE_BINARY_EMBED_AS_UNCOMPRESSED

	var flags := _VRM_IMPORT_USE_NAMED_SKIN_BINDS
	var named_skins := OS.get_environment("LUMINA_VRM_NAMED_SKINS").strip_edges().to_lower()
	if named_skins in ["0", "false", "off", "no"]:
		flags = 0
		print("[SIVRMAvatarAdapter] vrm_load_opt named_skins=off")
	var append_err := gltf.append_from_file(abs_path, state, flags)
	if append_err != OK:
		gltf.unregister_gltf_document_extension(vrm_extension)
		push_warning("SIVRMAvatarAdapter: append_from_file failed err=%s path=%s" % [append_err, abs_path])
		return null

	var scene := gltf.generate_scene(state)
	gltf.unregister_gltf_document_extension(vrm_extension)
	if scene == null:
		return null
	return scene


func _tick_motion(delta: float) -> void:
	if not _model_root:
		return
	var is_speaking := _is_tts_speaking()
	active_motion_source = "procedural"
	var transition_blend := 0.0
	if _motion_state_transition > 0.0:
		transition_blend = clampf(_motion_state_transition / _MOTION_STATE_TRANSITION_SEC, 0.0, 1.0)
		_motion_state_transition = maxf(0.0, _motion_state_transition - delta)
		if transition_blend > 0.0:
			_restore_root_and_pose(delta, transition_blend)
	if enable_procedural_idle_motion or enable_procedural_walk_motion:
		var use_fallback := _should_use_procedural_motion()
		var target_blend := 1.0 if use_fallback else 0.0
		_motion_pose_blend = lerpf(_motion_pose_blend, target_blend, clampf(delta * idle_pose_blend_speed, 0.0, 1.0))
		var active_blend := _motion_pose_blend * (1.0 - transition_blend)
		if use_fallback:
			if current_motion == "Walk":
				if not _apply_vrma_motion("walk", delta, active_blend, _current_walk_speed_ratio):
					_apply_walk_motion(delta, active_blend)
			else:
				if not enable_procedural_idle_motion:
					_restore_root_and_pose(delta, active_blend)
				if _apply_vrma_motion("idle", delta, active_blend, 1.0):
					pass
				elif enable_procedural_idle_motion:
					_apply_idle_motion(delta, active_blend)
		else:
			active_motion_source = "procedural:restore"
			_restore_root_and_pose(delta, _motion_pose_blend)
	else:
		active_motion_source = "procedural:restore"
		_restore_root_and_pose(delta, 1.0)
	_apply_talk_motion(delta, is_speaking)
	_apply_fallback_posture(delta)
	_apply_gaze_motion(delta, is_speaking)


func _tick_mouth(delta: float) -> void:
	if not _mouth_shapes:
		return
	var is_audio_playing := _is_voice_playing
	if _voice_player and _voice_player.playing:
		is_audio_playing = true
	var target_open := 0.0
	var lipsync_viseme_index := -1
	if is_audio_playing and lipsync_enabled:
		if _lipsync_envelope.size() > 0:
			target_open = _current_lipsync_level(_time)
			lipsync_viseme_index = _current_lipsync_viseme_index(_time)
			target_open = maxf(0.0, (target_open - lipsync_silence_threshold) / maxf(0.0001, 1.0 - lipsync_silence_threshold))
		else:
			target_open = clampf((sin(_time * TAU * lipsync_cycle_hz * 0.35) + 1.0) * 0.5, 0.0, 1.0)
			target_open = pow(clampf(target_open, 0.0, 1.0), 0.55)
		target_open *= lipsync_shape_strength
	else:
		target_open = 0.0
	var follow_speed := clampf(delta * maxf(1.0, lipsync_follow_speed), 0.0, 1.0)
	_mouth_open_target = lerpf(_mouth_open_target, target_open, follow_speed)

	if _mouth_open_target < 0.008:
		_mouth_open_target = 0.0

	var cycle_index := 0
	var cycle_speed := maxf(0.01, lipsync_cycle_hz)
	if _mouth_shapes.size() > 0:
		if lipsync_viseme_index >= 0:
			cycle_index = mini(maxi(lipsync_viseme_index, 0), _mouth_shapes.size() - 1)
		else:
			cycle_index = int(floor(_time * cycle_speed)) % _mouth_shapes.size()
	# Viseme priority ladder (keep in sync with services/avatar_runtime/lipsync_priority.py):
	# speaking+viseme > expression mouth underlay; silent => expression.
	for i in range(_mouth_shapes.size()):
		var shape_name: String = _mouth_shapes[i]
		var speech_value := 0.0
		if lipsync_viseme_index >= 0:
			speech_value = _mouth_open_target * _viseme_weight_for_shape(lipsync_viseme_index, i)
		else:
			speech_value = _mouth_open_target if i == cycle_index else 0.0
		var expression_value := float(_expression_shape_targets.get(shape_name, 0.0))
		# Viseme priority ladder while speaking:
		# viseme/speech mouth > expression mouth. When silent, expression wins.
		var final_value := expression_value
		if is_audio_playing and lipsync_enabled and _mouth_open_target > 0.008:
			if speech_value > 0.0:
				final_value = speech_value
			else:
				# Keep a little expression mouth only as soft underlay.
				final_value = expression_value * 0.15
		_set_blend_shape(shape_name, final_value)


func _viseme_weight_for_shape(viseme_index: int, shape_index: int) -> float:
	if viseme_index == shape_index:
		if viseme_index >= 0 and viseme_index < _LIPSYNC_PRIMARY_VISEME_WEIGHTS.size():
			return float(_LIPSYNC_PRIMARY_VISEME_WEIGHTS[viseme_index])
		return 0.8
	var secondary = _LIPSYNC_SECONDARY_VISEME_WEIGHTS.get(viseme_index, {})
	if typeof(secondary) == TYPE_DICTIONARY and secondary.has(shape_index):
		return float(secondary[shape_index])
	return 0.0


func _collect_capabilities() -> void:
	available_animations.clear()
	available_blend_shapes.clear()
	_clear_blend_shape_bindings()
	if _animation_player:
		for animation in _animation_player.get_animation_list():
			available_animations.append(String(animation))
	# A VRM expression can bind multiple meshes. Collect every unique target so
	# lip/cavity/eyelid meshes participate just like the principal face mesh.
	if _vrm_instance:
		for node in _all_nodes(_vrm_instance):
			if not (node is MeshInstance3D):
				continue
			var mesh_instance := node as MeshInstance3D
			if mesh_instance.mesh == null:
				continue
			for i in range(mesh_instance.mesh.get_blend_shape_count()):
				var shape_name := String(mesh_instance.mesh.get_blend_shape_name(i))
				if not _blend_shape_bindings.has(shape_name):
					available_blend_shapes.append(shape_name)
					_blend_shape_bindings[shape_name] = []
				var bindings: Array = _blend_shape_bindings[shape_name]
				bindings.append({"mesh": mesh_instance, "index": i})
				_blend_shape_bindings[shape_name] = bindings
	_resolve_lipsync_mouth_shapes()
	print("[SIVRMAvatarAdapter] animations=%s" % ",".join(available_animations))
	print("[SIVRMAvatarAdapter] face_blends=%s" % ",".join(available_blend_shapes))
	print("[SIVRMAvatarAdapter] blend_shape_cache shapes=%d bindings=%d" % [
		_blend_shape_bindings.size(),
		_blend_shape_binding_count(),
	])


func _clear_blend_shape_bindings() -> void:
	_blend_shape_bindings.clear()


func _blend_shape_binding_count() -> int:
	var total := 0
	for bindings_variant in _blend_shape_bindings.values():
		if typeof(bindings_variant) == TYPE_ARRAY:
			total += (bindings_variant as Array).size()
	return total


func _resolve_lipsync_mouth_shapes() -> void:
	_mouth_shapes.clear()
	for group in _LIPSYNC_MOUTH_SHAPE_GROUPS:
		var selected := ""
		for candidate in group:
			if candidate in available_blend_shapes:
				selected = String(candidate)
				break
		if not selected.is_empty():
			_mouth_shapes.append(selected)
	if _mouth_shapes.size() == 0:
		var fallback := ["Fcl_MTH_A", "Fcl_MTH_I", "Fcl_MTH_U", "Fcl_MTH_E", "Fcl_MTH_O"]
		for shape_name in fallback:
			if shape_name in available_blend_shapes:
				_mouth_shapes.append(shape_name)


func _resolve_motion_animations() -> void:
	if not _animation_player:
		return
	_idle_animation_name = _find_animation_for_keywords(["idle", "idle_loop", "wait", "stand"])
	_walk_animation_name = _find_animation_for_keywords(["walk", "walk_loop", "move"])


func _find_animation_for_keywords(keywords: Array[String]) -> String:
	for animation_name in _animation_player.get_animation_list():
		var lower := String(animation_name).to_lower()
		for keyword in keywords:
			if lower == keyword or lower.begins_with(keyword) or lower.find("_%s_" % keyword) != -1 or lower.find(keyword) != -1:
				return String(animation_name)
	return ""


func _animation_name_for_motion(motion_name: String) -> String:
	if motion_name == "Idle":
		return ""
	if motion_name == "Walk":
		if not _walk_animation_name.is_empty():
			return _walk_animation_name
		return ""
	return ""


func _setup_idle_pose() -> void:
	_skeleton = _find_first_of_type(_vrm_instance, "Skeleton3D") as Skeleton3D
	if not _skeleton:
		return
	# The runtime VRM importer can leave its profile-retarget pose active after
	# generating the corrected rest/bind data. Route B is exported on a clean
	# Stella-compatible T-pose rig, so its procedural motion must start from the
	# corrected rest just like a normal animation clip does.
	if _is_route_b_candidate_vrm():
		if _skeleton.has_method("reset_bone_poses"):
			_skeleton.reset_bone_poses()
		else:
			for bone_index in range(_skeleton.get_bone_count()):
				_skeleton.reset_bone_pose(bone_index)
		print("[SIVRMAvatarAdapter] route_b_skeleton_pose_initialized_to_rest bones=", _skeleton.get_bone_count())
	_pose_bone_indices.clear()
	_pose_bone_rest_rotations.clear()
	_collect_pose_bones()


func _collect_pose_bones() -> void:
	# Upper body
	_cache_pose_bone("left_clavicle", ["leftshoulder", "shoulderleft", "j_bip_l_shoulder", "lshoulder"])
	_cache_pose_bone("right_clavicle", ["rightshoulder", "shoulderright", "j_bip_r_shoulder", "rshoulder"])
	_cache_pose_bone("left_shoulder", ["leftupperarm", "upperarmleft", "j_bip_l_upperarm", "lupperarm", "upperarml", "leftshoulder", "shoulderleft"])
	_cache_pose_bone("right_shoulder", ["rightupperarm", "upperarmright", "j_bip_r_upperarm", "rupperarm", "upperarmr", "rightshoulder", "shoulderright"])
	_ensure_shoulder_fallback_mapping("left_shoulder", "left_clavicle", ["leftupperarm", "upperarmleft", "j_bip_l_upperarm", "lupperarm", "upperarml", "upperarm"])
	_ensure_shoulder_fallback_mapping("right_shoulder", "right_clavicle", ["rightupperarm", "upperarmright", "j_bip_r_upperarm", "rupperarm", "upperarmr", "upperarm"])
	_cache_pose_bone("left_forearm", ["leftlowerarm", "lowerarmleft", "j_bip_l_lowerarm", "llowerarm", "lowerarml", "leftforearm"])
	_cache_pose_bone("right_forearm", ["rightlowerarm", "lowerarmright", "j_bip_r_lowerarm", "rlowerarm", "lowerarmr", "rightforearm"])
	_cache_pose_bone("left_hand", ["lefthand", "j_bip_l_hand", "lhand", "handleft"])
	_cache_pose_bone("right_hand", ["righthand", "j_bip_r_hand", "rhand", "handright"])
	_cache_pose_bone("spine", ["spine3", "spine2", "spine1", "spine", "waist", "hips"])
	_cache_pose_bone("chest", ["chest", "upperchest", "spine2"])
	_cache_pose_bone("neck", ["neck", "neck1", "neck2", "upperneck"])
	_cache_pose_bone("head", ["head", "head1", "face"])

	# Lower body fallback set used by procedural walking.
	_cache_pose_bone("left_hip", ["leftupperleg", "j_bip_l_upperleg", "lupperleg", "upperlegleft", "leftthigh"])
	_cache_pose_bone("right_hip", ["rightupperleg", "j_bip_r_upperleg", "rupperleg", "upperlegright", "rightthigh"])
	_cache_pose_bone("left_knee", ["leftlowerleg", "j_bip_l_lowerleg", "llowerleg", "lowerlegleft", "leftshin", "kneel"])
	_cache_pose_bone("right_knee", ["rightlowerleg", "j_bip_r_lowerleg", "rlowerleg", "lowerlegright", "rightshin", "kneer"])
	_cache_pose_bone("left_foot", ["leftfoot", "j_bip_l_foot", "lfoot"])
	_cache_pose_bone("right_foot", ["rightfoot", "j_bip_r_foot", "rfoot"])

	for bone_key in _pose_bone_indices.keys():
		var idx := int(_pose_bone_indices[bone_key])
		_pose_bone_rest_rotations[idx] = _skeleton.get_bone_pose_rotation(idx)


func _setup_vrma_motion_bank() -> void:
	_vrma_samplers.clear()
	_vrma_motion_bone_indices.clear()
	_vrma_motion_base_rotations.clear()
	available_vrma_motions.clear()
	missing_vrma_motions.clear()
	_vrma_bone_indices.clear()
	_vrma_bone_base_rotations.clear()
	_vrma_idle_sampler = null
	if not _skeleton:
		return
	if enable_vrma_idle_motion:
		_register_vrma_motion("idle", _resolve_vrma_path("idle", idle_vrma_path, ["idle_loop.vrma", "idle.vrma", "stand_idle.vrma"]))
	if enable_vrma_motion_bank:
		_register_vrma_motion("walk", _resolve_vrma_path("walk", walk_vrma_path, ["walk_loop.vrma", "walk.vrma", "walking_loop.vrma"]))
		_register_vrma_motion("talk", _resolve_vrma_path("talk", talk_vrma_path, ["talk_loop.vrma", "speak_loop.vrma", "talk_idle.vrma"]))
		_register_vrma_motion("gesture_wave", _resolve_vrma_path("gesture_wave", gesture_wave_vrma_path, ["gesture_wave.vrma", "wave.vrma", "goodbye.vrma", "Greeting.vrma"]))
		_register_vrma_motion("gesture_point", _resolve_vrma_path("gesture_point", gesture_point_vrma_path, []))
		_register_vrma_motion("sit", _resolve_vrma_path("sit", sit_vrma_path, ["sit_loop.vrma", "sit_down.vrma", "squat.vrma"]))
		_register_vrma_motion("stand", _resolve_vrma_path("stand", stand_vrma_path, ["stand_loop.vrma", "stand_ready.vrma", "stand.vrma"]))
		if enable_fumi2kick_motion_audition:
			_register_fumi2kick_audition_motions()
		if enable_vroid_official_motion_audition:
			_register_vroid_official_audition_motions()
	print("[SIVRMAvatarAdapter] vrma_motions=%s missing=%s" % [
		",".join(available_vrma_motions),
		",".join(missing_vrma_motions),
	])


func _ensure_vrma_motion_fallbacks() -> void:
	## A different motion is not a fallback: stand cannot prove walk, wave cannot
	## prove point, and stand cannot prove sit. Missing semantic clips remain
	## missing so their existing procedural branches execute and telemetry stays
	## truthful.
	for motion_key in ["walk", "gesture_point", "sit"]:
		if _vrma_samplers.has(motion_key):
			missing_vrma_motions.erase(motion_key)
			continue
		available_vrma_motions.erase(motion_key)
		if not missing_vrma_motions.has(motion_key):
			missing_vrma_motions.append(motion_key)
		print("[SIVRMAvatarAdapter] vrma_missing %s -> procedural" % motion_key)


func _is_route_b_candidate_vrm() -> bool:
	var path := vrm_scene_path.strip_edges().to_lower()
	return path.contains("route_b") and path.contains("candidate")


func _is_route_b_native_trial_validation() -> bool:
	return OS.get_environment("LUMINA_ROUTE_B_NATIVE_TRIAL_VALIDATE").strip_edges().to_lower() in ["1", "true", "yes", "on"]


func _log_material_diagnostics(phase: String) -> void:
	if not _vrm_instance:
		return
	print("[SIVRMAvatarAdapter] material_diag phase=%s candidate=%s" % [phase, str(_is_route_b_candidate_vrm())])
	for node in _all_nodes(_vrm_instance):
		if not (node is MeshInstance3D):
			continue
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.mesh == null:
			continue
		for surface_index in range(mesh_instance.mesh.get_surface_count()):
			var material := mesh_instance.get_active_material(surface_index)
			_print_material_diag_line(mesh_instance.name, surface_index, material)


func _print_material_diag_line(mesh_name: String, surface_index: int, material: Material) -> void:
	if material == null:
		print("[SIVRMAvatarAdapter] mat mesh=%s surface=%d material=<null>" % [mesh_name, surface_index])
		return
	var mat_name := material.resource_name
	var mat_type := material.get_class()
	var albedo := Color(1, 1, 1, 1)
	var has_tex := false
	var tex_path := ""
	var emission := Color(0, 0, 0, 1)
	var metallic := -1.0
	var roughness := -1.0
	var transparency := -1
	var cull_mode := -1
	var shade_toony := -1.0
	var shade_color := Color(0, 0, 0, 0)
	var light_color := Color(0, 0, 0, 0)
	var rim_color := Color(0, 0, 0, 0)
	var has_override := false
	if material is StandardMaterial3D:
		var standard := material as StandardMaterial3D
		albedo = standard.albedo_color
		has_tex = standard.albedo_texture != null
		if has_tex:
			tex_path = standard.albedo_texture.resource_path
		emission = standard.emission
		metallic = standard.metallic
		roughness = standard.roughness
		transparency = int(standard.transparency)
		cull_mode = int(standard.cull_mode)
	elif material is ShaderMaterial:
		var shader_mat := material as ShaderMaterial
		albedo = _get_shader_color_parameter(shader_mat, "_Color")
		var main_tex = shader_mat.get_shader_parameter("_MainTex") if _shader_parameter_exists(shader_mat, "_MainTex") else null
		has_tex = main_tex != null and typeof(main_tex) == TYPE_OBJECT
		if has_tex and main_tex is Resource:
			tex_path = (main_tex as Resource).resource_path
		emission = _get_shader_color_parameter(shader_mat, "_EmissionColor")
		shade_color = _get_shader_color_parameter(shader_mat, "_ShadeColor")
		light_color = albedo
		rim_color = _get_shader_color_parameter(shader_mat, "_RimColor")
		if _shader_parameter_exists(shader_mat, "_ShadeToony"):
			var toony_value = shader_mat.get_shader_parameter("_ShadeToony")
			if typeof(toony_value) in [TYPE_FLOAT, TYPE_INT]:
				shade_toony = float(toony_value)
		if shader_mat.next_pass != null:
			has_override = true
	print(
		"[SIVRMAvatarAdapter] mat mesh=%s surface=%d name=%s type=%s albedo=%s tex=%s tex_path=%s emission=%s metallic=%.3f roughness=%.3f transparency=%d cull=%d override=%s shade_toony=%.3f shade=%s light=%s rim=%s"
		% [
			mesh_name,
			surface_index,
			mat_name,
			mat_type,
			str(albedo),
			str(has_tex),
			tex_path,
			str(emission),
			metallic,
			roughness,
			transparency,
			cull_mode,
			str(has_override),
			shade_toony,
			str(shade_color),
			str(light_color),
			str(rim_color),
		]
	)


func _normalize_route_b_one_material(material: Material, hint: String) -> void:
	var lower_hint := hint.to_lower()
	if material is ShaderMaterial:
		var shader_mat := material as ShaderMaterial
		if _is_route_b_authored_retopo_material_hint(lower_hint):
			_normalize_route_b_authored_retopo_material(shader_mat, lower_hint)
			return
		var main_tex = shader_mat.get_shader_parameter("_MainTex") if _shader_parameter_exists(shader_mat, "_MainTex") else null
		var has_main_tex := main_tex != null and typeof(main_tex) == TYPE_OBJECT
		# Body/cloth atlas: convert MToon → StandardMaterial3D so albedo texture is reliably
		# sampled in sRGB and not washed out by lit-only MToon + near-white atlas islands.
		if has_main_tex and _matches_any(lower_hint, ["body", "repaired_body", "basecolor", "uniform", "cloth"]):
			_convert_route_b_mtoon_to_standard(shader_mat, hint)
			return
		# Preserve candidate ShadeToony=0.3; never force 0.62 here.
		if _shader_parameter_exists(shader_mat, "_ShadeToony"):
			shader_mat.set_shader_parameter("_ShadeToony", 0.3)
		_set_shader_parameter_if_present(shader_mat, "_EmissionColor", Color(0, 0, 0, 1))
		_set_shader_parameter_if_present(shader_mat, "_EmissionMultiplier", 0.0)
		_set_shader_parameter_if_present(shader_mat, "_RimLift", 0.0)
		_set_shader_parameter_if_present(shader_mat, "_RimLightingMix", 0.0)
		if has_main_tex:
			# UniVRM convention: reuse MainTex as ShadeTexture when absent.
			var shade_tex = shader_mat.get_shader_parameter("_ShadeTexture") if _shader_parameter_exists(shader_mat, "_ShadeTexture") else null
			if shade_tex == null or typeof(shade_tex) != TYPE_OBJECT:
				_set_shader_parameter_if_present(shader_mat, "_ShadeTexture", main_tex)
		if _matches_any(lower_hint, ["mouth", "eyelid", "blink", "viseme", "blinklid", "mouthviseme"]):
			var dark := _get_shader_color_parameter(shader_mat, "_Color")
			if dark.a <= 0.0 or maxf(dark.r, maxf(dark.g, dark.b)) > 0.55:
				dark = Color(0.08, 0.03, 0.05, 1.0) if _matches_any(lower_hint, ["mouth", "viseme"]) else Color(0.16, 0.04, 0.22, 1.0)
				_set_shader_parameter_if_present(shader_mat, "_Color", dark)
			_set_shader_parameter_if_present(shader_mat, "_ShadeColor", _shade_color_from_lit_color(dark, lower_hint))
		if _matches_any(lower_hint, ["hair", "kami", "bang", "back_hair", "nouv"]):
			if _is_route_b_native_trial_validation():
				# New isolated trials carry a real UV texture. Preserve it; the legacy
				# no-UV compatibility conversion deliberately drops MainTex.
				return
			# noUV solid MToon hair cards produce black stipple / purple noise under
			# double-sided toon shading. Convert to Standard like body albedo path.
			_convert_route_b_hair_mtoon_to_standard(shader_mat, hint)
			return
	elif material is StandardMaterial3D:
		var standard := material as StandardMaterial3D
		standard.emission_enabled = false
		standard.emission_energy = 0.0
		standard.metallic = minf(standard.metallic, 0.03)
		if standard.albedo_texture != null:
			# Aggressive gain cut: near-white atlas face islands blow out under lit Standard.
			_apply_route_b_body_standard_tuning(standard)
		if _matches_any(lower_hint, ["mouth", "eyelid", "blink", "viseme", "blinklid", "mouthviseme"]):
			if maxf(standard.albedo_color.r, maxf(standard.albedo_color.g, standard.albedo_color.b)) > 0.55:
				standard.albedo_color = Color(0.08, 0.03, 0.05, 1.0) if _matches_any(lower_hint, ["mouth", "viseme"]) else Color(0.16, 0.04, 0.22, 1.0)
		if _matches_any(lower_hint, ["hair", "kami", "bang", "back_hair"]):
			_apply_route_b_hair_standard_tuning(standard)


func _is_route_b_authored_retopo_material_hint(lower_hint: String) -> bool:
	return _matches_any(lower_hint, [
		"lumina_hair_mtoon_",
		"lumina_headskin_mtoon_",
		"lumina_mouthcavity_",
		"lumina_eyewhite_",
		"lumina_irispurple_",
		"lumina_pupil_",
		"lumina_eyehighlight_",
		"lumina_lashpurple_",
	])


func _normalize_route_b_authored_retopo_material(shader_mat: ShaderMaterial, lower_hint: String) -> void:
	# These materials are authored by the closed-head trial pipeline and carry a
	# valid UV texture or an explicit eye/mouth color. Legacy Route B gain and
	# no-UV hair workarounds must not replace them.
	var lit := _get_shader_color_parameter(shader_mat, "_Color")
	var shade := _get_shader_color_parameter(shader_mat, "_ShadeColor")
	if lower_hint.find("lumina_mouthcavity_") >= 0:
		lit = Color(0.08, 0.03, 0.05, 1.0)
		shade = Color(0.04, 0.01, 0.025, 1.0)
	elif lower_hint.find("lumina_eyewhite_") >= 0:
		lit = Color(0.965, 0.955, 0.985, 1.0)
		shade = Color(0.74, 0.70, 0.80, 1.0)
	elif lower_hint.find("lumina_irispurple_") >= 0:
		lit = Color(0.40, 0.16, 0.76, 1.0)
		shade = Color(0.14, 0.035, 0.34, 1.0)
	elif lower_hint.find("lumina_pupil_") >= 0:
		lit = Color(0.03, 0.008, 0.055, 1.0)
		shade = Color(0.008, 0.002, 0.018, 1.0)
	elif lower_hint.find("lumina_lashpurple_") >= 0:
		lit = Color(0.14, 0.025, 0.25, 1.0)
		shade = Color(0.045, 0.004, 0.085, 1.0)
	elif lower_hint.find("lumina_eyehighlight_") >= 0:
		lit = Color.WHITE
		shade = Color(0.92, 0.90, 1.0, 1.0)
	_set_shader_parameter_if_present(shader_mat, "_Color", lit)
	_set_shader_parameter_if_present(shader_mat, "_ShadeColor", shade)
	_set_shader_parameter_if_present(shader_mat, "_ShadeToony", 0.72)
	_set_shader_parameter_if_present(shader_mat, "_EmissionColor", Color(0, 0, 0, 1))
	_set_shader_parameter_if_present(shader_mat, "_EmissionMultiplier", 0.0)
	_set_shader_parameter_if_present(shader_mat, "_RimLift", 0.0)
	_set_shader_parameter_if_present(shader_mat, "_RimLightingMix", 0.0)


func _convert_route_b_mtoon_to_standard(shader_mat: ShaderMaterial, hint: String) -> void:
	## Replace the ShaderMaterial in-place is not possible; callers must assign the returned
	## material. This helper mutates by swapping shader→standard via a side channel on the
	## mesh is handled by _normalize_route_b_candidate_materials after this returns... 
	## Actually we need the caller to set the new material. Use meta marker.
	var standard := StandardMaterial3D.new()
	standard.resource_name = shader_mat.resource_name
	standard.resource_local_to_scene = true
	var main_tex = shader_mat.get_shader_parameter("_MainTex")
	if main_tex != null and typeof(main_tex) == TYPE_OBJECT:
		standard.albedo_texture = main_tex as Texture2D
	var base := _get_shader_color_parameter(shader_mat, "_Color")
	if base.a <= 0.0:
		base = Color(1, 1, 1, 1)
	# Near-white atlas islands + lit Standard blow face to white; cut gain hard (not room exposure).
	_apply_route_b_body_standard_tuning(standard)
	shader_mat.set_meta("route_b_standard_replacement", standard)
	print("[SIVRMAvatarAdapter] route_b_mtoon_to_standard hint=%s tex=%s albedo=%s" % [
		hint, str(standard.albedo_texture != null), str(standard.albedo_color)
	])


func _apply_route_b_body_standard_tuning(standard: StandardMaterial3D) -> void:
	## Candidate preview only.
	## Imported/runtime face PNG (ImageTexture) caused Compatibility gray-grain — withdrawn.
	## Keep embedded atlas + gain≈0.52 (accept2-era). Geometry feature nudge is separate.
	var face_tex := _load_route_b_face_fix_texture()
	if face_tex != null:
		standard.albedo_texture = face_tex
		standard.albedo_color = Color(0.88, 0.82, 0.78, 1.0)
		print("[SIVRMAvatarAdapter] route_b_face_tex_override=on type=%s" % face_tex.get_class())
	else:
		standard.albedo_color = Color(0.52, 0.49, 0.47, 1.0)
		print("[SIVRMAvatarAdapter] route_b_face_tex_override=off fallback_gain=0.52")
	standard.metallic = 0.0
	standard.roughness = 0.92
	standard.emission_enabled = false
	standard.emission_energy = 0.0
	standard.emission = Color(0, 0, 0, 1)
	standard.cull_mode = BaseMaterial3D.CULL_BACK
	standard.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
	_set_object_property_if_present(standard, "metallic_specular", 0.15)
	_set_object_property_if_present(standard, "specular", 0.15)
	_set_object_property_if_present(standard, "clearcoat_enabled", false)
	_set_object_property_if_present(standard, "clearcoat", 0.0)
	_set_object_property_if_present(standard, "subsurf_scatter_enabled", false)
	_set_object_property_if_present(standard, "subsurf_scatter_strength", 0.0)
	print("[SIVRMAvatarAdapter] route_b_body_gain albedo=%s" % str(standard.albedo_color))


func _load_route_b_face_fix_texture() -> Texture2D:
	## Accept5: body atlas override stays off (gain≈0.52). Midface readability comes from
	## UV-island remapped tris + Lumina_MidfaceShell (Compressed/Portable path on shell only).
	return null


func _load_route_b_portable_png(res_path: String) -> Texture2D:
	## Prefer formal CompressedTexture2D only when imported .ctex exists; else PortableCompressed.
	var abs_png := ProjectSettings.globalize_path(res_path)
	var ctex_guess := ProjectSettings.globalize_path(
		"res://.godot/imported/%s-accept5.ctex" % res_path.get_file()
	)
	if FileAccess.file_exists(ctex_guess) and ResourceLoader.exists(res_path):
		var imported: Resource = ResourceLoader.load(res_path, "", ResourceLoader.CACHE_MODE_IGNORE)
		if imported is CompressedTexture2D:
			return imported as CompressedTexture2D
		if imported is Texture2D:
			return imported as Texture2D
	if not FileAccess.file_exists(abs_png):
		return null
	var img := Image.new()
	if img.load(abs_png) != OK:
		return null
	var tex := PortableCompressedTexture2D.new()
	tex.create_from_image(img, PortableCompressedTexture2D.COMPRESSION_MODE_LOSSLESS)
	return tex


func _attach_route_b_midface_shell() -> void:
	## Candidate-only midface overlay. Accept8 experiments failed — use Accept5a shell path.
	if not _vrm_instance:
		return
	var glb_res := "res://assets/avatars/lumina_route_b_candidate_20260718/bake/midface_shell_accept5_20260718.glb"
	var abs_glb := ProjectSettings.globalize_path(glb_res)
	if not FileAccess.file_exists(abs_glb):
		push_warning("SIVRMAvatarAdapter: midface shell GLB missing")
		return
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err := doc.append_from_file(abs_glb, state)
	if err != OK:
		push_warning("SIVRMAvatarAdapter: midface shell GLTF load failed err=%s" % str(err))
		return
	var root := doc.generate_scene(state)
	if root == null:
		return
	root.name = "Lumina_MidfaceShellRoot"
	_vrm_instance.add_child(root)
	var shell_tex := _load_route_b_portable_png(
		"res://assets/avatars/lumina_route_b_candidate_20260718/bake/midface_shell_albedo_accept5_20260718.png"
	)
	for node in _all_nodes(root):
		if not (node is MeshInstance3D):
			continue
		var mi := node as MeshInstance3D
		mi.name = "Lumina_MidfaceShell"
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		mi.set_meta("route_b_midface_shell", true)
		mi.sorting_offset = 0.02
		mi.position.z += 0.002
		var mat := StandardMaterial3D.new()
		mat.resource_local_to_scene = true
		mat.set_meta("route_b_midface_shell_mat", true)
		if shell_tex != null:
			mat.albedo_texture = shell_tex
			mat.albedo_color = Color(1.0, 1.0, 1.0, 1.0)
		else:
			mat.albedo_color = Color(0.90, 0.74, 0.68, 1.0)
		mat.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
		mat.cull_mode = BaseMaterial3D.CULL_BACK
		mat.roughness = 0.88
		mat.metallic = 0.0
		mat.render_priority = 1
		mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
		_set_object_property_if_present(mat, "disable_receive_shadows", true)
		mi.set_surface_override_material(0, mat)
		if mi.mesh != null and mi.mesh.get_surface_count() > 0:
			mi.mesh.surface_set_material(0, mat)
	print(
		"[SIVRMAvatarAdapter] route_b_midface_shell=on tex=%s variant=accept5a_stable_after_accept8"
		% (shell_tex.get_class() if shell_tex else "null")
	)


func _convert_route_b_hair_mtoon_to_standard(shader_mat: ShaderMaterial, hint: String) -> void:
	var standard := StandardMaterial3D.new()
	standard.resource_name = shader_mat.resource_name
	standard.resource_local_to_scene = true
	var hair_color := _get_shader_color_parameter(shader_mat, "_Color")
	if maxf(hair_color.r, maxf(hair_color.g, hair_color.b)) < 0.05:
		hair_color = Color(0.3569, 0.1843, 0.5647, 1.0)
	# Keep authored purple; slightly darken to reduce fringe sparkle against gray BG.
	standard.albedo_color = Color(
		clampf(hair_color.r * 0.92, 0.0, 1.0),
		clampf(hair_color.g * 0.90, 0.0, 1.0),
		clampf(hair_color.b * 0.95, 0.0, 1.0),
		1.0
	)
	# noUV hair must not sample a broken atlas island.
	standard.albedo_texture = null
	_apply_route_b_hair_standard_tuning(standard)
	shader_mat.set_meta("route_b_standard_replacement", standard)
	print("[SIVRMAvatarAdapter] route_b_hair_mtoon_to_standard hint=%s color=%s" % [hint, str(standard.albedo_color)])


func _apply_route_b_hair_standard_tuning(standard: StandardMaterial3D) -> void:
	standard.metallic = 0.0
	standard.roughness = 1.0
	standard.emission_enabled = false
	standard.emission_energy = 0.0
	standard.emission = Color(0, 0, 0, 1)
	standard.transparency = BaseMaterial3D.TRANSPARENCY_DISABLED
	# Backface cull: double-sided noUV cards were a major stipple source.
	standard.cull_mode = BaseMaterial3D.CULL_BACK
	standard.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_OPAQUE_ONLY
	standard.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
	# Overlapping noUV hair cards create stipple under lit shading; unshaded kills most noise.
	standard.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	standard.next_pass = null
	_set_object_property_if_present(standard, "metallic_specular", 0.0)
	_set_object_property_if_present(standard, "specular", 0.0)
	_set_object_property_if_present(standard, "clearcoat_enabled", false)
	_set_object_property_if_present(standard, "clearcoat", 0.0)
	_set_object_property_if_present(standard, "disable_receive_shadows", true)
	_set_object_property_if_present(standard, "grow", false)
	_set_object_property_if_present(standard, "grow_amount", 0.0)


func _normalize_route_b_candidate_materials() -> void:
	## Candidate-only repair: reconnect missing MToon albedo, kill unintended emission,
	## keep metallic low on skin/cloth, and keep overlay morphs non-white.
	if not _vrm_instance:
		return
	print("[SIVRMAvatarAdapter] route_b_candidate_material_normalize=on")
	for node in _all_nodes(_vrm_instance):
		if not (node is MeshInstance3D):
			continue
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.get_meta("route_b_midface_shell", false):
			continue
		if str(mesh_instance.name).to_lower().find("midfaceshell") >= 0:
			continue
		if mesh_instance.mesh == null:
			continue
		for surface_index in range(mesh_instance.mesh.get_surface_count()):
			var material := mesh_instance.get_active_material(surface_index)
			if material == null:
				continue
			var tuned := material.duplicate() as Material
			if tuned == null:
				continue
			_set_object_property_if_present(tuned, "resource_local_to_scene", true)
			var hint := "%s %s" % [mesh_instance.name, tuned.resource_name]
			_normalize_route_b_one_material(tuned, hint)
			if tuned.has_meta("route_b_standard_replacement"):
				tuned = tuned.get_meta("route_b_standard_replacement") as Material
			mesh_instance.set_surface_override_material(surface_index, tuned)


func _reassert_route_b_materials_after_realistic_shading() -> void:
	## Realistic shading re-duplicates materials and can restore bright skin gain / hair specular.
	if not _vrm_instance:
		return
	for node in _all_nodes(_vrm_instance):
		if not (node is MeshInstance3D):
			continue
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.get_meta("route_b_midface_shell", false):
			continue
		if str(mesh_instance.name).to_lower().find("midfaceshell") >= 0:
			continue
		if mesh_instance.mesh == null:
			continue
		for surface_index in range(mesh_instance.mesh.get_surface_count()):
			var material := mesh_instance.get_active_material(surface_index)
			if material == null:
				continue
			var hint := "%s %s" % [mesh_instance.name, material.resource_name]
			var lower_hint := hint.to_lower()
			if material is ShaderMaterial and _is_route_b_authored_retopo_material_hint(lower_hint):
				_normalize_route_b_authored_retopo_material(material as ShaderMaterial, lower_hint)
				continue
			# Never treat MidfaceShell as body/face (substring "face" false-positive).
			if lower_hint.find("midfaceshell") >= 0 or lower_hint.find("midface_shell") >= 0:
				continue
			var is_hair := _matches_any(lower_hint, ["hair", "kami", "bang", "back_hair", "nouv"])
			var is_body := _matches_any(lower_hint, ["body", "repaired_body", "basecolor", "uniform", "cloth", "skin", "hada"])
			# Require whole-token face match — avoid "midface" → "face".
			if not is_body and _matches_any(lower_hint, ["face"]):
				if lower_hint.find("midface") < 0:
					is_body = true
			if not is_hair and not is_body:
				continue
			if is_hair and material is ShaderMaterial:
				if _is_route_b_native_trial_validation():
					continue
				_convert_route_b_hair_mtoon_to_standard(material as ShaderMaterial, hint)
				if material.has_meta("route_b_standard_replacement"):
					mesh_instance.set_surface_override_material(
						surface_index,
						material.get_meta("route_b_standard_replacement") as Material
					)
				continue
			if material is StandardMaterial3D:
				var standard := (material as StandardMaterial3D).duplicate() as StandardMaterial3D
				if standard == null:
					continue
				_set_object_property_if_present(standard, "resource_local_to_scene", true)
				if is_hair:
					_apply_route_b_hair_standard_tuning(standard)
				elif is_body and standard.albedo_texture != null:
					_apply_route_b_body_standard_tuning(standard)
				mesh_instance.set_surface_override_material(surface_index, standard)
			elif is_body and material is ShaderMaterial:
				_convert_route_b_mtoon_to_standard(material as ShaderMaterial, hint)
				if material.has_meta("route_b_standard_replacement"):
					mesh_instance.set_surface_override_material(
						surface_index,
						material.get_meta("route_b_standard_replacement") as Material
					)


func _resolve_vrma_path(motion_key: String, explicit_path: String, candidates: Array[String]) -> String:
	if not explicit_path.is_empty() and FileAccess.file_exists(explicit_path):
		return explicit_path
	var base_dir := animation_asset_dir.trim_suffix("/")
	for candidate in candidates:
		var path := "%s/%s" % [base_dir, candidate]
		if FileAccess.file_exists(path):
			return path
	missing_vrma_motions.append(motion_key)
	return ""


func _register_fumi2kick_audition_motions() -> void:
	var base_dir := animation_asset_dir.trim_suffix("/") + "/fumi2kick"
	var candidates := {
		"gesture_motion_pose": "001_motion_pose.vrma",
		"gesture_dogeza": "002_dogeza.vrma",
		"gesture_humidai": "003_humidai.vrma",
		"gesture_hello": "004_hello_1.vrma",
		"gesture_smartphone": "005_smartphone.vrma",
		"gesture_drinkwater": "006_drinkwater.vrma",
		"gesture_gekirei": "007_gekirei.vrma",
		"gesture_gatan": "008_gatan.vrma",
	}
	for motion_key in candidates.keys():
		var source_path := "%s/%s" % [base_dir, String(candidates[motion_key])]
		if FileAccess.file_exists(source_path):
			_register_vrma_motion(String(motion_key), source_path)


func _register_vroid_official_audition_motions() -> void:
	var base_dir := animation_asset_dir.trim_suffix("/") + "/vroid_official"
	var candidates := {
		"gesture_showcase": "gesture_showcase.vrma",
		"gesture_greeting": "gesture_greeting.vrma",
		"gesture_vsign": "gesture_vsign.vrma",
		"gesture_shoot": "gesture_shoot.vrma",
		"gesture_turn": "gesture_turn.vrma",
		"gesture_model_pose": "gesture_model_pose.vrma",
		"gesture_squat": "gesture_squat.vrma",
	}
	for motion_key in candidates.keys():
		var source_path := "%s/%s" % [base_dir, String(candidates[motion_key])]
		if FileAccess.file_exists(source_path):
			_register_vrma_motion(String(motion_key), source_path)


func _register_vrma_motion(motion_key: String, source_path: String) -> bool:
	if source_path.is_empty():
		return false
	var sampler = VRMAMotionSamplerScript.new()
	if not sampler.load_vrma(source_path):
		push_warning("SIVRMAvatarAdapter: VRMA %s load failed: %s" % [motion_key, sampler.last_error])
		if not missing_vrma_motions.has(motion_key):
			missing_vrma_motions.append(motion_key)
		return false
	var bone_indices := _map_vrma_bones(sampler)
	if bone_indices.is_empty():
		push_warning("SIVRMAvatarAdapter: VRMA %s has no mapped bones: %s" % [motion_key, source_path])
		if not missing_vrma_motions.has(motion_key):
			missing_vrma_motions.append(motion_key)
		return false
	var base_rotations := _base_rotations_for_vrma_motion(sampler, bone_indices)
	_vrma_samplers[motion_key] = sampler
	_vrma_motion_bone_indices[motion_key] = bone_indices
	_vrma_motion_base_rotations[motion_key] = base_rotations
	available_vrma_motions.append(motion_key)
	if motion_key == "idle":
		_vrma_idle_sampler = sampler
		_vrma_bone_indices = bone_indices
		_vrma_bone_base_rotations = base_rotations
	print("[SIVRMAvatarAdapter] vrma_%s_tracks=%d mapped_bones=%d duration=%.2f path=%s" % [
		motion_key,
		sampler.get_human_bone_keys().size(),
		bone_indices.size(),
		sampler.duration,
		source_path,
	])
	return true


func _map_vrma_bones(sampler) -> Dictionary:
	var bone_indices := {}
	for human_key in sampler.get_human_bone_keys():
		var candidates := _bone_candidates_for_vrma_key(String(human_key))
		if candidates.is_empty():
			continue
		var idx := _find_bone_by_candidates(candidates)
		if idx >= 0:
			bone_indices[String(human_key)] = idx
			if not _pose_bone_rest_rotations.has(idx):
				_pose_bone_rest_rotations[idx] = _skeleton.get_bone_pose_rotation(idx)
	return bone_indices


func _base_rotations_for_vrma_motion(sampler, bone_indices: Dictionary) -> Dictionary:
	var base_rotations := {}
	var base_sample: Dictionary = sampler.sample(0.0)
	for human_key in base_sample.keys():
		if bone_indices.has(human_key):
			base_rotations[String(human_key)] = base_sample[human_key]
	return base_rotations


func _bone_candidates_for_vrma_key(human_key: String) -> Array[String]:
	match human_key:
		"hips":
			return ["hips"]
		"spine":
			return ["spine"]
		"chest":
			return ["chest"]
		"upperChest":
			return ["upperchest"]
		"neck":
			return ["neck"]
		"head":
			return ["head"]
		"leftShoulder":
			return ["leftshoulder"]
		"leftUpperArm":
			return ["leftupperarm"]
		"leftLowerArm":
			return ["leftlowerarm"]
		"leftHand":
			return ["lefthand"]
		"rightShoulder":
			return ["rightshoulder"]
		"rightUpperArm":
			return ["rightupperarm"]
		"rightLowerArm":
			return ["rightlowerarm"]
		"rightHand":
			return ["righthand"]
		"leftUpperLeg":
			return ["leftupperleg"]
		"leftLowerLeg":
			return ["leftlowerleg"]
		"leftFoot":
			return ["leftfoot"]
		"leftToes":
			return ["lefttoes"]
		"rightUpperLeg":
			return ["rightupperleg"]
		"rightLowerLeg":
			return ["rightlowerleg"]
		"rightFoot":
			return ["rightfoot"]
		"rightToes":
			return ["righttoes"]
		_:
			return []


func _cache_pose_bone(bone_key: String, candidate_names: Array[String]) -> void:
	if _pose_bone_indices.has(bone_key):
		return
	var idx := _find_bone_by_candidates(candidate_names)
	if idx >= 0:
		_pose_bone_indices[bone_key] = idx


func _ensure_shoulder_fallback_mapping(shoulder_key: String, clavicle_key: String, fallback_candidates: Array[String]) -> void:
	if not _pose_bone_indices.has(shoulder_key):
		return
	if not _pose_bone_indices.has(clavicle_key):
		return
	var shoulder_idx := int(_pose_bone_indices[shoulder_key])
	var clavicle_idx := int(_pose_bone_indices[clavicle_key])
	if shoulder_idx != clavicle_idx:
		return

	var corrected := _find_bone_by_candidates(fallback_candidates)
	if corrected < 0 or corrected == clavicle_idx:
		return
	_pose_bone_indices[shoulder_key] = corrected


func _find_bone_by_candidates(candidate_names: Array[String]) -> int:
	if not _skeleton:
		return -1
	var normalized_candidates: Array[String] = []
	for candidate in candidate_names:
		normalized_candidates.append(_normalize_bone_name(candidate))
	for candidate in normalized_candidates:
		for i in range(_skeleton.get_bone_count()):
			if _normalize_bone_name(_skeleton.get_bone_name(i)) == candidate:
				return i
	for candidate in normalized_candidates:
		for i in range(_skeleton.get_bone_count()):
			if _normalize_bone_name(_skeleton.get_bone_name(i)).find(candidate) != -1:
				return i
	return -1


func _normalize_bone_name(value: String) -> String:
	var lowered := value.to_lower()
	var normalized := ""
	for i in range(lowered.length()):
		var code := lowered.unicode_at(i)
		if (code >= 48 and code <= 57) or (code >= 97 and code <= 122):
			normalized += char(code)
	return normalized


func _should_use_procedural_motion() -> bool:
	if current_motion == "Idle":
		return true
	if _active_motion_animation.is_empty():
		return true
	if not _animation_player:
		return true
	return _animation_player.current_animation != _active_motion_animation


func _apply_vrma_motion(motion_key: String, delta: float, blend: float, speed_scale: float = 1.0) -> bool:
	if not enable_vrma_motion_bank and motion_key != "idle":
		return false
	if motion_key == "idle" and not enable_vrma_idle_motion:
		return false
	if not _skeleton or not _vrma_samplers.has(motion_key):
		return false
	var sampler = _vrma_samplers[motion_key]
	if not sampler or not sampler.valid:
		return false
	var bone_indices: Dictionary = _vrma_motion_bone_indices.get(motion_key, {})
	var base_rotations: Dictionary = _vrma_motion_base_rotations.get(motion_key, {})
	if bone_indices.is_empty():
		return false
	var playback_speed := maxf(0.05, vrma_motion_playback_speed * speed_scale)
	if motion_key == "idle":
		playback_speed = maxf(0.05, vrma_idle_playback_speed)

	var samples: Dictionary = sampler.sample(_time * playback_speed)
	if samples.is_empty():
		return false
	if motion_key == "idle":
		_apply_idle_motion(delta, blend)
	var motion_blend := vrma_idle_blend if motion_key == "idle" else vrma_motion_blend
	var final_blend := clampf(blend * motion_blend, 0.0, 1.0)
	if final_blend <= 0.01:
		if motion_key == "idle" and enable_procedural_idle_motion:
			return false
		return true

	var applied := 0
	for human_key in samples.keys():
		if not _should_apply_vrma_key(motion_key, String(human_key)):
			continue
		if not bone_indices.has(human_key):
			continue
		var bone_idx := int(bone_indices[human_key])
		if not _pose_bone_rest_rotations.has(bone_idx):
			continue
		if not base_rotations.has(human_key):
			continue
		var rest_rotation := _base_rotation_for_vrma_key(motion_key, String(human_key), bone_idx)
		var base_rotation: Quaternion = base_rotations[human_key] as Quaternion
		var sampled_rotation: Quaternion = samples[human_key] as Quaternion
		if not _is_finite_quat(sampled_rotation) or not _is_finite_quat(base_rotation):
			continue
		var delta_rotation := (base_rotation.inverse() * sampled_rotation).normalized()
		if _quat_angle_between(Quaternion.IDENTITY, delta_rotation) > vrma_delta_angle_limit_deg:
			continue
		var target_rotation := rest_rotation.slerp((rest_rotation * delta_rotation).normalized(), final_blend)
		_skeleton.set_bone_pose_rotation(bone_idx, _clamp_delta_rotation(rest_rotation, target_rotation, vrma_delta_angle_limit_deg))
		applied += 1

	if motion_key == "idle":
		var breath := sin(_time * idle_breath_speed) * idle_breath_amount
		var shift := sin(_time * idle_weight_shift_speed * TAU) * idle_weight_shift_deg
		_model_root.position.y = lerpf(_model_root.position.y, _model_root_base_y + breath, clampf(delta * 4.5, 0.0, 1.0))
		_model_root.rotation_degrees.x = lerpf(_model_root.rotation_degrees.x, _model_root_base_rotation.x, clampf(delta * 4.5, 0.0, 1.0))
		_model_root.rotation_degrees.z = lerpf(_model_root.rotation_degrees.z, _model_root_base_rotation.z + shift, clampf(delta * 4.5, 0.0, 1.0))
	elif motion_key == "walk":
		var cycle := _time * TAU * walk_step_cycle_hz * maxf(0.05, _current_walk_speed_ratio)
		_model_root.position.y = lerpf(_model_root.position.y, _model_root_base_y + sin(cycle) * walk_bob_amount * 0.35, clampf(delta * 8.0, 0.0, 1.0))
	active_motion_source = "vrma:%s" % motion_key
	return applied > 0


func _should_apply_vrma_key(motion_key: String, human_key: String) -> bool:
	match motion_key:
		"idle":
			return human_key in ["spine", "chest", "upperChest", "neck", "head"]
		"talk":
			return human_key in ["spine", "chest", "upperChest", "neck", "head", "leftShoulder", "leftUpperArm", "rightShoulder", "rightUpperArm"]
		"gesture_wave", "gesture_point":
			return human_key in ["spine", "chest", "upperChest", "neck", "head", "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand", "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand"]
		_ when motion_key.begins_with("gesture_"):
			return true
		"sit", "stand":
			return human_key in ["hips", "spine", "chest", "upperChest", "leftUpperLeg", "leftLowerLeg", "leftFoot", "rightUpperLeg", "rightLowerLeg", "rightFoot"]
		_:
			return true


func _base_rotation_for_vrma_key(motion_key: String, human_key: String, bone_idx: int) -> Quaternion:
	var rest_rotation: Quaternion = _pose_bone_rest_rotations[bone_idx] as Quaternion
	if motion_key != "idle" or not enable_procedural_idle_motion:
		return rest_rotation
	var rest_basis := Basis(rest_rotation)
	var shoulder_pitch := _resolve_shoulder_pitch_deg(_pose_bone_key_from_index(human_key), idle_shoulder_pitch_deg)
	var shoulder_forward := _resolve_shoulder_forward_deg(_pose_bone_key_from_index(human_key), shoulder_pitch, idle_shoulder_forward_deg)
	match human_key:
		"spine":
			return (rest_basis * Basis(Vector3.RIGHT, deg_to_rad(idle_spine_pitch_deg))).get_rotation_quaternion()
		"chest", "upperChest":
			return (rest_basis * Basis(Vector3.RIGHT, deg_to_rad(idle_spine_pitch_deg * 0.45))).get_rotation_quaternion()
		"neck":
			return (rest_basis * Basis(Vector3.RIGHT, deg_to_rad(idle_neck_pitch_deg))).get_rotation_quaternion()
		"leftShoulder", "leftUpperArm":
			return (rest_basis * Basis(Vector3.FORWARD, deg_to_rad(shoulder_pitch)) * Basis(Vector3.RIGHT, deg_to_rad(shoulder_forward))).get_rotation_quaternion()
		"rightShoulder", "rightUpperArm":
			return (rest_basis * Basis(Vector3.FORWARD, deg_to_rad(shoulder_pitch)) * Basis(Vector3.RIGHT, deg_to_rad(shoulder_forward))).get_rotation_quaternion()
		"leftLowerArm", "rightLowerArm":
			return (rest_basis * Basis(Vector3.RIGHT, deg_to_rad(idle_forearm_pitch_deg))).get_rotation_quaternion()
		_:
			return rest_rotation


func _apply_idle_motion(delta: float, blend: float) -> void:
	active_motion_source = "procedural:idle"
	if not _skeleton or _pose_bone_indices.is_empty():
		return
	if not enable_procedural_idle_motion:
		return

	var target_blend := blend
	_idle_blend = lerpf(_idle_blend, target_blend, clampf(delta * idle_pose_blend_speed, 0.0, 1.0))
	if _idle_blend <= 0.01:
		return

	var left_shoulder_pitch := _resolve_shoulder_pitch_deg("left_shoulder", idle_shoulder_pitch_deg)
	var right_shoulder_pitch := _resolve_shoulder_pitch_deg("right_shoulder", idle_shoulder_pitch_deg)
	var left_shoulder_forward := _resolve_shoulder_forward_deg("left_shoulder", left_shoulder_pitch, idle_shoulder_forward_deg)
	var right_shoulder_forward := _resolve_shoulder_forward_deg("right_shoulder", right_shoulder_pitch, idle_shoulder_forward_deg)
	var wave := sin(_time * 1.7) * idle_arm_sway_deg
	var torso_wave := sin(_time * 1.35) * idle_spine_wave_deg
	var chest_wave := sin(_time * 1.9) * 0.6
	var breath := sin(_time * idle_breath_speed) * idle_breath_amount
	var head_wave := cos(_time * 1.2) * 0.25
	var weight_shift := sin(_time * idle_weight_shift_speed * TAU) * idle_weight_shift_deg

	_apply_motion_bone_pose("spine", idle_spine_pitch_deg + torso_wave, 0.0, Vector3.RIGHT, _idle_blend * 0.58, spine_guard_deg * 0.7)
	_apply_motion_bone_pose("chest", idle_spine_pitch_deg * 0.45 + chest_wave, 0.0, Vector3.RIGHT, _idle_blend * 0.38, spine_guard_deg * 0.6)
	_apply_motion_bone_pose("neck", idle_neck_pitch_deg, head_wave * 1.5, Vector3.RIGHT, _idle_blend * 0.35, 18.0)
	_apply_motion_bone_pose("head", 0.0, head_wave * idle_head_bob_deg, Vector3.RIGHT, _idle_blend * 0.25, 18.0)
	_apply_motion_bone_pose("head", idle_head_roll_deg, sin(_time * 2.0) * 0.2, Vector3.FORWARD, _idle_blend * 0.2, 10.0)
	_apply_motion_bone_pose("left_clavicle", _resolve_shoulder_pitch_deg("left_clavicle", idle_shoulder_pitch_deg * 0.22), wave * 0.15, Vector3.FORWARD, _idle_blend * 0.55, shoulder_guard_deg * 0.45)
	_apply_motion_bone_pose("right_clavicle", _resolve_shoulder_pitch_deg("right_clavicle", idle_shoulder_pitch_deg * 0.22), -wave * 0.15, Vector3.FORWARD, _idle_blend * 0.55, shoulder_guard_deg * 0.45)
	_apply_motion_bone_pose_dual("left_shoulder", left_shoulder_pitch, wave, Vector3.FORWARD, left_shoulder_forward, Vector3.RIGHT, _idle_blend, shoulder_guard_deg)
	_apply_motion_bone_pose_dual("right_shoulder", right_shoulder_pitch, -wave, Vector3.FORWARD, right_shoulder_forward, Vector3.RIGHT, _idle_blend, shoulder_guard_deg)
	_apply_motion_bone_pose("left_forearm", idle_forearm_pitch_deg, -wave * 0.85, Vector3.RIGHT, _idle_blend, forearm_guard_deg)
	_apply_motion_bone_pose("right_forearm", idle_forearm_pitch_deg, wave * 0.85, Vector3.RIGHT, _idle_blend, forearm_guard_deg)

	_model_root.position.y = lerpf(_model_root.position.y, _model_root_base_y + breath, clampf(delta * 4.5, 0.0, 1.0))
	_model_root.rotation_degrees.x = lerpf(_model_root.rotation_degrees.x, _model_root_base_rotation.x, clampf(delta * 4.5, 0.0, 1.0))
	_model_root.rotation_degrees.z = lerpf(_model_root.rotation_degrees.z, _model_root_base_rotation.z + weight_shift, clampf(delta * 4.5, 0.0, 1.0))


func _apply_walk_motion(delta: float, blend: float) -> void:
	if not enable_procedural_walk_motion or not _skeleton or _pose_bone_indices.is_empty():
		return
	active_motion_source = "procedural:walk"
	if blend <= 0.01:
		return
	var effective_blend := clampf(blend, 0.0, 1.0)

	var cycle := _time * TAU * walk_step_cycle_hz * maxf(0.05, _current_walk_speed_ratio)
	var step := sin(cycle)
	var swing := cos(cycle)
	var step_alt := sin(cycle + PI)
	var arm_delay := 0.38
	var forearm_delay := 0.42

	var body_bob := walk_bob_amount * swing
	var spine_wave := step_alt * walk_spine_wave_deg
	var chest_wave := step * walk_chest_wave_deg

	var left_shoulder_wave := sin(cycle + arm_delay + PI) * walk_shoulder_swing_deg
	var right_shoulder_wave := sin(cycle + arm_delay) * walk_shoulder_swing_deg
	var left_forearm_wave := sin(cycle + forearm_delay + PI) * walk_forearm_swing_deg
	var right_forearm_wave := sin(cycle + forearm_delay) * walk_forearm_swing_deg
	var left_leg_weight := (step + 1.0) * 0.5
	var right_leg_weight := (step_alt + 1.0) * 0.5
	var left_knee_wave := walk_knee_bend_deg * clampf(left_leg_weight, 0.0, 1.0)
	var right_knee_wave := walk_knee_bend_deg * clampf(right_leg_weight, 0.0, 1.0)
	var left_hip_wave := walk_leg_swing_deg * step
	var right_hip_wave := walk_leg_swing_deg * step_alt
	var left_foot_wave := walk_leg_swing_deg * 0.2 * cos(cycle + 0.65) * left_leg_weight
	var right_foot_wave := walk_leg_swing_deg * 0.2 * cos(cycle + PI + 0.65) * right_leg_weight
	var left_foot_pitch := walk_foot_lift_deg * maxf(0.0, sin(cycle + 0.35)) - walk_foot_lift_deg * 0.38 * maxf(0.0, -sin(cycle - 0.25))
	var right_foot_pitch := walk_foot_lift_deg * maxf(0.0, sin(cycle + PI + 0.35)) - walk_foot_lift_deg * 0.38 * maxf(0.0, -sin(cycle + PI - 0.25))

	_apply_motion_bone_pose("spine", walk_spine_pitch_deg + spine_wave, 0.0, Vector3.RIGHT, effective_blend * 0.58, spine_guard_deg)
	_apply_motion_bone_pose("chest", walk_spine_pitch_deg * 0.4 + chest_wave, 0.0, Vector3.RIGHT, effective_blend * 0.40, spine_guard_deg * 0.8)
	_apply_motion_bone_pose("neck", walk_neck_pitch_deg, step_alt * 0.12, Vector3.RIGHT, effective_blend * 0.26, 18.0)
	_apply_motion_bone_pose("head", walk_head_pitch_deg, step_alt * 0.2, Vector3.RIGHT, effective_blend * 0.22, 18.0)
	var left_walk_base := _resolve_shoulder_pitch_deg("left_shoulder", walk_shoulder_pitch_deg)
	var right_walk_base := _resolve_shoulder_pitch_deg("right_shoulder", walk_shoulder_pitch_deg)
	var left_shoulder_forward := _resolve_shoulder_forward_deg("left_shoulder", left_walk_base, idle_shoulder_forward_deg)
	var right_shoulder_forward := _resolve_shoulder_forward_deg("right_shoulder", right_walk_base, idle_shoulder_forward_deg)
	_apply_motion_bone_pose("left_clavicle", _resolve_shoulder_pitch_deg("left_clavicle", walk_shoulder_pitch_deg * 0.18), left_shoulder_wave * 0.35, Vector3.FORWARD, effective_blend * 0.55, shoulder_guard_deg * 0.42)
	_apply_motion_bone_pose("right_clavicle", _resolve_shoulder_pitch_deg("right_clavicle", walk_shoulder_pitch_deg * 0.18), -right_shoulder_wave * 0.35, Vector3.FORWARD, effective_blend * 0.55, shoulder_guard_deg * 0.42)
	_apply_motion_bone_pose_dual("left_shoulder", left_walk_base, left_shoulder_wave, Vector3.FORWARD, left_shoulder_forward, Vector3.RIGHT, effective_blend, shoulder_guard_deg)
	_apply_motion_bone_pose_dual("right_shoulder", right_walk_base, right_shoulder_wave, Vector3.FORWARD, right_shoulder_forward, Vector3.RIGHT, effective_blend, shoulder_guard_deg)
	_apply_motion_bone_pose("left_forearm", walk_forearm_pitch_deg, left_forearm_wave * 0.75, Vector3.RIGHT, effective_blend * 0.7, forearm_guard_deg)
	_apply_motion_bone_pose("right_forearm", walk_forearm_pitch_deg, right_forearm_wave * 0.75, Vector3.RIGHT, effective_blend * 0.7, forearm_guard_deg)
	_apply_motion_bone_pose("left_hand", walk_forearm_pitch_deg * 0.35, left_forearm_wave * 0.35, Vector3.RIGHT, effective_blend * 0.4, 24.0)
	_apply_motion_bone_pose("right_hand", walk_forearm_pitch_deg * 0.35, right_forearm_wave * 0.35, Vector3.RIGHT, effective_blend * 0.4, 24.0)

	_apply_motion_bone_pose("left_hip", left_hip_wave, left_hip_wave * 0.04, Vector3.RIGHT, effective_blend, hip_guard_deg)
	_apply_motion_bone_pose("right_hip", right_hip_wave, right_hip_wave * 0.04, Vector3.RIGHT, effective_blend, hip_guard_deg)
	_apply_motion_bone_pose("left_knee", -left_knee_wave, left_foot_wave, Vector3.RIGHT, effective_blend * 0.72, knee_guard_deg)
	_apply_motion_bone_pose("right_knee", -right_knee_wave, right_foot_wave, Vector3.RIGHT, effective_blend * 0.72, knee_guard_deg)
	_apply_motion_bone_pose("left_foot", left_foot_pitch, 0.0, Vector3.RIGHT, effective_blend * 0.55, 28.0)
	_apply_motion_bone_pose("right_foot", right_foot_pitch, 0.0, Vector3.RIGHT, effective_blend * 0.55, 28.0)

	_model_root.position.y = lerpf(_model_root.position.y, _model_root_base_y + body_bob * 0.85, clampf(delta * 9.0, 0.0, 1.0))
	_model_root.rotation_degrees.x = lerpf(
		_model_root.rotation_degrees.x,
		_model_root_base_rotation.x + walk_root_pitch_deg * effective_blend,
		clampf(delta * 8.0, 0.0, 1.0)
	)
	_model_root.rotation_degrees.z = lerpf(
		_model_root.rotation_degrees.z,
		_model_root_base_rotation.z + (step * walk_root_roll_deg),
		clampf(delta * 10.0, 0.0, 1.0)
	)


func _restore_root_and_pose(delta: float, blend: float) -> void:
	var recover := clampf(delta * 10.0, 0.0, 1.0)
	_model_root.position.y = lerpf(_model_root.position.y, _model_root_base_y, recover)
	_model_root.rotation_degrees.x = lerpf(_model_root.rotation_degrees.x, _model_root_base_rotation.x, recover)
	_model_root.rotation_degrees.z = lerpf(_model_root.rotation_degrees.z, _model_root_base_rotation.z, recover)

	if not _skeleton:
		return
	for idx in _pose_bone_rest_rotations.keys():
		var rest_rotation := _pose_bone_rest_rotations[idx] as Quaternion
		var bone_idx := int(idx)
		var current_rot := _skeleton.get_bone_pose_rotation(bone_idx)
		_skeleton.set_bone_pose_rotation(bone_idx, current_rot.slerp(rest_rotation, blend))


func _apply_fallback_posture(delta: float) -> void:
	if not _skeleton:
		return

	if _posture_until >= 0.0 and _time >= _posture_until:
		_posture_requested_blend = 0.0

	var target_blend := 0.0
	if _posture_state != "":
		target_blend = maxf(0.0, _posture_requested_blend)
	target_blend = clampf(target_blend, 0.0, 1.0)
	_posture_blend = lerpf(_posture_blend, target_blend, clampf(delta * fallback_posture_blend_speed, 0.0, 1.0))

	if _posture_blend <= 0.02:
		if target_blend <= 0.0:
			_clear_posture_state()
		return

	match _posture_state:
		"gesture":
			_apply_gesture_posture(delta, _posture_gesture, _posture_blend)
		"sit":
			if not _apply_vrma_motion("sit", delta, _posture_blend, 1.0):
				_apply_sit_posture(delta, _posture_blend)
		"stand":
			if not _apply_vrma_motion("stand", delta, _posture_blend, 1.0):
				_apply_stand_posture(delta, _posture_blend)
		_:
			return


func _apply_gesture_posture(delta: float, gesture: String, blend: float) -> void:
	var normalized := gesture.strip_edges().to_lower()
	var vrma_key := "gesture_%s" % normalized
	if _apply_vrma_motion(vrma_key, delta, blend, 1.0):
		return
	if normalized == "wave" and _apply_vrma_motion("gesture_wave", delta, blend, 1.0):
		return
	if normalized in ["encourage", "cheer", "gekirei"] and _apply_vrma_motion("gesture_gekirei", delta, blend, 1.0):
		return
	if normalized == "point" and _apply_vrma_motion("gesture_point", delta, blend, 1.0):
		return
	var wave := sin(_time * gesture_hold_wave_hz) * gesture_wave_amplitude_deg * gesture_hold_wave_scale
	match normalized:
		"wave":
			active_motion_source = "procedural:wave"
			_apply_motion_bone_pose("right_shoulder", -idle_shoulder_pitch_deg * 0.35, wave, Vector3.FORWARD, blend, shoulder_guard_deg)
			_apply_motion_bone_pose("right_forearm", gesture_point_forearm_deg, wave * 0.3, Vector3.RIGHT, blend * 0.8, forearm_guard_deg)
		"point":
			active_motion_source = "procedural:point"
			var point_settle := sin(_time * gesture_hold_wave_hz * 0.45) * 1.5
			_apply_motion_bone_pose("spine", idle_spine_pitch_deg * 0.2, -2.0, Vector3.FORWARD, blend * 0.28, spine_guard_deg)
			_apply_motion_bone_pose("head", -1.0, point_settle * 0.25, Vector3.RIGHT, blend * 0.25, 16.0)
			_apply_motion_bone_pose("right_shoulder", gesture_point_shoulder_deg, point_settle, Vector3.FORWARD, blend, shoulder_guard_deg)
			_apply_motion_bone_pose("right_forearm", gesture_point_forearm_deg * 0.08, 0.0, Vector3.RIGHT, blend * 0.9, forearm_guard_deg)
			_apply_motion_bone_pose("right_hand", gesture_point_hand_deg, 0.0, Vector3.RIGHT, blend * 0.75, 32.0)
			_apply_motion_bone_pose("left_shoulder", -idle_shoulder_pitch_deg * 0.18, -point_settle * 0.4, Vector3.FORWARD, blend * 0.45, shoulder_guard_deg)
			_apply_motion_bone_pose("left_forearm", idle_forearm_pitch_deg * 0.3, 0.0, Vector3.RIGHT, blend * 0.35, forearm_guard_deg)
		"nod":
			active_motion_source = "procedural:nod"
			var nod_phase := sin(_time * 9.0)
			var nod_amount := 5.5 + maxf(0.0, nod_phase) * 3.0
			_apply_motion_bone_pose("neck", nod_amount * 0.35, 0.0, Vector3.RIGHT, blend * 0.55, 18.0)
			_apply_motion_bone_pose("head", nod_amount, 0.0, Vector3.RIGHT, blend * 0.85, 18.0)
			_apply_motion_bone_pose("spine", idle_spine_pitch_deg * 0.35, 0.0, Vector3.RIGHT, blend * 0.25, spine_guard_deg * 0.5)
		"bow":
			active_motion_source = "procedural:bow"
			var bow_wave := sin(_time * 4.0) * 1.2
			_apply_motion_bone_pose("spine", 8.0 + bow_wave, 0.0, Vector3.RIGHT, blend * 0.45, spine_guard_deg)
			_apply_motion_bone_pose("chest", 5.5 + bow_wave * 0.5, 0.0, Vector3.RIGHT, blend * 0.4, spine_guard_deg * 0.8)
			_apply_motion_bone_pose("neck", 3.0, 0.0, Vector3.RIGHT, blend * 0.4, 18.0)
			_apply_motion_bone_pose("head", 4.0, 0.0, Vector3.RIGHT, blend * 0.45, 18.0)
		"shrug":
			active_motion_source = "procedural:shrug"
			var shrug_wave := 1.0 + maxf(0.0, sin(_time * 7.5)) * 0.55
			_apply_motion_bone_pose("left_clavicle", -10.0 * shrug_wave, 0.0, Vector3.FORWARD, blend * 0.75, shoulder_guard_deg * 0.45)
			_apply_motion_bone_pose("right_clavicle", -10.0 * shrug_wave, 0.0, Vector3.FORWARD, blend * 0.75, shoulder_guard_deg * 0.45)
			_apply_motion_bone_pose("left_shoulder", -idle_shoulder_pitch_deg * 0.18, 2.0, Vector3.FORWARD, blend * 0.42, shoulder_guard_deg)
			_apply_motion_bone_pose("right_shoulder", -idle_shoulder_pitch_deg * 0.18, -2.0, Vector3.FORWARD, blend * 0.42, shoulder_guard_deg)
			_apply_motion_bone_pose("head", -1.0, sin(_time * 4.0) * 0.6, Vector3.FORWARD, blend * 0.3, 12.0)
		"clap":
			active_motion_source = "procedural:clap"
			var clap_wave := sin(_time * 10.5) * 4.5
			_apply_motion_bone_pose_dual("left_shoulder", -38.0, clap_wave, Vector3.FORWARD, 34.0, Vector3.RIGHT, blend * 0.75, shoulder_guard_deg)
			_apply_motion_bone_pose_dual("right_shoulder", -38.0, -clap_wave, Vector3.FORWARD, 34.0, Vector3.RIGHT, blend * 0.75, shoulder_guard_deg)
			_apply_motion_bone_pose("left_forearm", 44.0, -clap_wave, Vector3.RIGHT, blend * 0.65, forearm_guard_deg)
			_apply_motion_bone_pose("right_forearm", 44.0, clap_wave, Vector3.RIGHT, blend * 0.65, forearm_guard_deg)
		_:
			active_motion_source = "procedural:gesture"
			_apply_motion_bone_pose("left_shoulder", -idle_shoulder_pitch_deg * 0.2, wave * 0.4, Vector3.FORWARD, blend, shoulder_guard_deg)
			_apply_motion_bone_pose("left_forearm", gesture_point_forearm_deg * 0.5, wave * 0.5, Vector3.RIGHT, blend * 0.7, forearm_guard_deg)


func _apply_sit_posture(_unused_delta: float, blend: float) -> void:
	active_motion_source = "procedural:sit"
	var hip_pitch := clampf(sit_hip_bend_deg, -90.0, 30.0)
	var knee_bend := clampf(sit_knee_bend_deg, 0.0, 95.0)
	_model_root.position.y = lerpf(_model_root.position.y, _model_root_base_y - sit_root_drop_amount * blend, clampf(blend, 0.0, 1.0))
	_apply_motion_bone_pose("left_hip", hip_pitch, 0.0, Vector3.RIGHT, blend, hip_guard_deg)
	_apply_motion_bone_pose("right_hip", hip_pitch, 0.0, Vector3.RIGHT, blend, hip_guard_deg)
	_apply_motion_bone_pose("left_knee", -knee_bend, 0.0, Vector3.RIGHT, blend, knee_guard_deg)
	_apply_motion_bone_pose("right_knee", -knee_bend, 0.0, Vector3.RIGHT, blend, knee_guard_deg)
	_apply_motion_bone_pose("spine", sit_spine_pitch_deg, 0.0, Vector3.RIGHT, blend * 0.45, spine_guard_deg)
	_apply_motion_bone_pose("left_forearm", sit_arm_rest_deg, 0.0, Vector3.RIGHT, blend * 0.45, forearm_guard_deg)
	_apply_motion_bone_pose("right_forearm", sit_arm_rest_deg, 0.0, Vector3.RIGHT, blend * 0.45, forearm_guard_deg)


func _apply_stand_posture(_unused_delta: float, blend: float) -> void:
	active_motion_source = "procedural:stand"
	_model_root.position.y = lerpf(_model_root.position.y, _model_root_base_y, clampf(blend, 0.0, 1.0))
	_apply_motion_bone_pose("left_knee", -stand_spine_smooth_deg * 1.8, 0.0, Vector3.RIGHT, blend * 0.35, knee_guard_deg)
	_apply_motion_bone_pose("right_knee", -stand_spine_smooth_deg * 1.8, 0.0, Vector3.RIGHT, blend * 0.35, knee_guard_deg)
	_apply_motion_bone_pose("spine", -stand_spine_smooth_deg, 0.0, Vector3.RIGHT, blend * 0.4, spine_guard_deg)


func _apply_talk_motion(delta: float, speaking: bool) -> void:
	if not _skeleton:
		var decay_target := 0.0
		_talk_posture_blend = lerpf(_talk_posture_blend, decay_target, clampf(delta * idle_pose_blend_speed, 0.0, 1.0))
		return

	var target_blend := 1.0 if speaking else 0.0
	_talk_posture_blend = lerpf(_talk_posture_blend, target_blend, clampf(delta * talk_posture_blend_speed, 0.0, 1.0))
	if _talk_posture_blend <= 0.02:
		return

	var posture_scale := 1.0
	if _posture_state in ["sit", "stand", "gesture"]:
		posture_scale = 0.65

	var blend := _talk_posture_blend * talk_pose_scale * posture_scale
	if enable_vrma_talk_motion and _apply_vrma_motion("talk", delta, blend, 1.0):
		return
	if not enable_procedural_talk_motion:
		return
	active_motion_source = "procedural:talk"
	var wave := sin(_time * maxf(0.1, talk_wave_hz)) * talk_wave_deg
	var slow_wave := sin(_time * maxf(0.1, talk_wave_hz) * 0.43)
	var phrase_wave := sin(_time * 1.17 + 0.4)
	var answer_emphasis := maxf(0.0, sin(_time * 0.74 - 0.2))

	_apply_motion_bone_pose("spine", talk_spine_pitch_deg + wave * 0.55 + talk_spine_wave_deg * cos(_time * talk_wave_hz * 0.72), phrase_wave * 0.35, Vector3.RIGHT, blend * 0.34, spine_guard_deg * 0.8)
	_apply_motion_bone_pose("chest", talk_chest_pitch_deg + talk_chest_wave_deg * sin(_time * talk_wave_hz * 0.82), phrase_wave * 0.45, Vector3.RIGHT, blend * 0.32, spine_guard_deg * 0.75)
	_apply_motion_bone_pose("neck", talk_neck_pitch_deg + talk_wave_deg * 0.22, wave * 0.22 + phrase_wave * 0.3, Vector3.RIGHT, blend * 0.42, 20.0)
	_apply_motion_bone_pose("head", talk_head_pitch_deg, wave * 0.45 + phrase_wave * 0.4, Vector3.RIGHT, blend * 0.33, 18.0)
	_apply_motion_bone_pose("left_shoulder", -talk_shoulder_motion_deg * 0.28, slow_wave * talk_shoulder_motion_deg * 0.7, Vector3.FORWARD, blend * 0.32, shoulder_guard_deg)
	_apply_motion_bone_pose("right_shoulder", talk_shoulder_motion_deg * 0.18 + answer_emphasis * 1.2, -slow_wave * talk_shoulder_motion_deg, Vector3.FORWARD, blend * 0.44, shoulder_guard_deg)
	_apply_motion_bone_pose("left_forearm", talk_forearm_motion_deg * 0.65, slow_wave * talk_hand_motion_deg * 0.55, Vector3.RIGHT, blend * 0.22, forearm_guard_deg)
	_apply_motion_bone_pose("right_forearm", talk_forearm_motion_deg + answer_emphasis * 1.6, -slow_wave * talk_hand_motion_deg, Vector3.RIGHT, blend * 0.34, forearm_guard_deg)
	_apply_motion_bone_pose("left_hand", talk_hand_motion_deg * 0.35, slow_wave * talk_hand_motion_deg * 0.38, Vector3.RIGHT, blend * 0.2, 24.0)
	_apply_motion_bone_pose("right_hand", talk_hand_motion_deg * 0.58, -slow_wave * talk_hand_motion_deg * 0.62, Vector3.RIGHT, blend * 0.27, 24.0)
	_model_root.position.y = lerpf(_model_root.position.y, _model_root_base_y + (sin(_time * talk_wave_hz) * talk_root_bob_amount * blend), clampf(delta * 6.0, 0.0, 1.0))


func _apply_gaze_motion(delta: float, speaking: bool) -> void:
	if not enable_procedural_gaze_motion or not _skeleton:
		return

	if _gaze_has_target and _gaze_hold_until >= 0.0 and _time > _gaze_hold_until:
		_gaze_has_target = false

	var target_yaw := 0.0
	var target_pitch := 0.0
	var target_blend := 0.0
	if _gaze_has_target:
		target_yaw = _gaze_target_yaw_deg
		target_pitch = _gaze_target_pitch_deg
		target_blend = 1.0
	elif current_motion == "Idle":
		var scan := sin(_time * gaze_idle_scan_speed * TAU) * gaze_idle_scan_deg
		var scan_pitch := sin(_time * gaze_idle_scan_speed * TAU * 0.57 + 0.8) * gaze_idle_scan_deg * 0.16
		target_yaw = scan
		target_pitch = scan_pitch
		target_blend = 0.55
	else:
		target_blend = 0.0

	var micro := (
		sin(_time * 11.7) * gaze_micro_saccade_deg
		+ sin(_time * 4.3 + 1.2) * gaze_micro_saccade_deg * 0.35
	)
	target_yaw += micro
	target_pitch += sin(_time * 6.1 + 0.4) * gaze_micro_saccade_deg * 0.18

	if current_motion == "Walk":
		target_yaw *= 0.45
		target_pitch *= 0.5
		target_blend *= 0.6
	if speaking:
		target_blend *= 0.85
	if _posture_state in ["gesture", "sit", "stand"]:
		target_blend *= 0.65

	var follow := clampf(delta * gaze_blend_speed, 0.0, 1.0)
	_gaze_current_yaw_deg = lerpf(_gaze_current_yaw_deg, clampf(target_yaw, -gaze_neck_yaw_limit_deg - gaze_head_yaw_limit_deg, gaze_neck_yaw_limit_deg + gaze_head_yaw_limit_deg), follow)
	_gaze_current_pitch_deg = lerpf(_gaze_current_pitch_deg, clampf(target_pitch, -gaze_pitch_limit_deg, gaze_pitch_limit_deg), follow)

	if target_blend <= 0.01 and absf(_gaze_current_yaw_deg) < 0.05 and absf(_gaze_current_pitch_deg) < 0.05:
		return

	var yaw_abs := absf(_gaze_current_yaw_deg)
	var neck_yaw := clampf(_gaze_current_yaw_deg * 0.42, -gaze_neck_yaw_limit_deg, gaze_neck_yaw_limit_deg)
	var head_yaw := clampf(_gaze_current_yaw_deg - neck_yaw, -gaze_head_yaw_limit_deg, gaze_head_yaw_limit_deg)
	if yaw_abs > gaze_neck_yaw_limit_deg + gaze_head_yaw_limit_deg:
		head_yaw = clampf(head_yaw, -gaze_head_yaw_limit_deg, gaze_head_yaw_limit_deg)
	var neck_pitch := clampf(_gaze_current_pitch_deg * 0.38, -gaze_pitch_limit_deg * 0.65, gaze_pitch_limit_deg * 0.65)
	var head_pitch := clampf(_gaze_current_pitch_deg - neck_pitch, -gaze_pitch_limit_deg, gaze_pitch_limit_deg)
	var blend := clampf(target_blend, 0.0, 1.0)

	_apply_motion_bone_pose_dual("neck", neck_pitch, 0.0, Vector3.RIGHT, neck_yaw, Vector3.UP, blend * 0.62, 20.0)
	_apply_motion_bone_pose_dual("head", head_pitch, 0.0, Vector3.RIGHT, head_yaw, Vector3.UP, blend * 0.78, 22.0)


func _apply_motion_bone_pose(
		bone_key: String,
		base_deg: float,
		sway_deg: float,
		axis: Vector3,
		blend: float,
		joint_delta_limit_deg: float = -1.0
	) -> void:
	if not _pose_bone_indices.has(bone_key) or not _skeleton:
		return
	var bone_idx := int(_pose_bone_indices[bone_key])
	if bone_idx < 0:
		return
	if not _pose_bone_rest_rotations.has(bone_idx):
		return

	var current_blend := clampf(blend, 0.0, 1.0)
	var rest_rotation: Quaternion = _pose_bone_rest_rotations[bone_idx] as Quaternion
	var rest_basis := Basis(rest_rotation)
	var axis_norm := axis.normalized()
	if axis_norm.length() < 0.0001:
		axis_norm = Vector3.RIGHT
	var target_basis := rest_basis * Basis(axis_norm, deg_to_rad(base_deg + sway_deg))
	var target_rotation: Quaternion = target_basis.get_rotation_quaternion()
	if joint_delta_limit_deg > 0.0:
		target_rotation = _clamp_delta_rotation(rest_rotation, target_rotation, joint_delta_limit_deg)
	var current_rotation := _skeleton.get_bone_pose_rotation(bone_idx)
	if not _is_finite_quat(current_rotation) or not _is_finite_quat(target_rotation):
		return
	var output_rotation: Quaternion = current_rotation.slerp(target_rotation, current_blend)
	_skeleton.set_bone_pose_rotation(bone_idx, output_rotation)


func _resolve_shoulder_pitch_deg(bone_key: String, pitch_deg: float) -> float:
	var normalized_key := bone_key.to_lower()
	if not _pose_bone_indices.has(bone_key) or not _pose_bone_rest_rotations.has(int(_pose_bone_indices[bone_key])):
		return -absf(pitch_deg)
	if not is_finite(pitch_deg):
		return 0.0

	var abs_pitch := absf(pitch_deg)
	if abs_pitch < 0.0001:
		return 0.0
	var preferred_sign := -1.0 if _shoulder_key_side_sign(normalized_key) > 0.0 else 1.0

	var bone_idx := int(_pose_bone_indices[bone_key])
	var rest_rotation: Quaternion = _pose_bone_rest_rotations[bone_idx] as Quaternion
	var rest_basis := Basis(rest_rotation)
	var shoulder_pitch_axis := _select_shoulder_axis(_shoulder_pitch_axis_key(normalized_key), rest_basis)
	if shoulder_pitch_axis.length() < 0.0001:
		shoulder_pitch_axis = _select_shoulder_axis("forward", rest_basis)

	# Use the lower-arm direction when available so side-specific rest poses are respected.
	var upper_arm_direction := _resolve_upper_arm_direction(rest_basis, bone_key)
	if upper_arm_direction.length() < 0.0001:
		return 0.0

	var shoulder_pitch_axis_norm := shoulder_pitch_axis.normalized()
	var upper_arm_dir_abs_dot := absf(upper_arm_direction.dot(shoulder_pitch_axis_norm))
	if upper_arm_dir_abs_dot > 0.92:
		var fallback_axis := _select_shoulder_axis("forward", rest_basis)
		var fallback_dot := absf(upper_arm_direction.dot(fallback_axis.normalized()))
		if fallback_dot < upper_arm_dir_abs_dot:
			shoulder_pitch_axis = fallback_axis
			shoulder_pitch_axis_norm = shoulder_pitch_axis.normalized()

	var plus_pitch := (Quaternion(shoulder_pitch_axis.normalized(), deg_to_rad(abs_pitch)) * upper_arm_direction).normalized()
	var minus_pitch := (Quaternion(shoulder_pitch_axis.normalized(), deg_to_rad(-abs_pitch)) * upper_arm_direction).normalized()

	# Prefer the candidate that points more downward in world coordinates.
	var plus_score := plus_pitch.dot(Vector3.DOWN)
	var minus_score := minus_pitch.dot(Vector3.DOWN)
	if plus_score > minus_score + 0.0005:
		return abs_pitch
	if minus_score > plus_score + 0.0005:
		return -abs_pitch
	return abs_pitch * preferred_sign


func _resolve_shoulder_forward_deg(bone_key: String, shoulder_pitch_deg: float, forward_deg: float) -> float:
	if not is_finite(forward_deg):
		return 0.0
	if not is_finite(shoulder_pitch_deg) or absf(forward_deg) <= 0.0001:
		return 0.0
	return forward_deg


func _shoulder_pitch_axis_key(bone_key: String) -> String:
	var key := bone_key.to_lower()
	if key.find("left") == 0 || key.find("left_") == 0:
		return "left"
	if key.find("right") == 0 || key.find("right_") == 0:
		return "right"
	return "generic"


func _select_shoulder_axis(axis_name: String, basis: Basis) -> Vector3:
	match axis_name:
		"left", "right":
			if basis.z.length() > 0.0001:
				return basis.z
			return basis.x
		"up":
			if basis.y.length() > 0.0001:
				return basis.y
			return Vector3.UP
		"forward":
			if basis.z.length() > 0.0001:
				return basis.z
			return Vector3.FORWARD
		_:
			if basis.z.length() > 0.0001:
				return basis.z
			if basis.x.length() > 0.0001:
				return basis.x
			return Vector3.BACK


func _resolve_upper_arm_direction(rest_basis: Basis, bone_key: String) -> Vector3:
	var upper_arm_direction := rest_basis * Vector3.RIGHT
	var forearm_key := _get_attached_forearm_key(bone_key)
	if forearm_key.is_empty() or not _pose_bone_indices.has(forearm_key) or not _skeleton:
		return upper_arm_direction.normalized()

	var forearm_idx := int(_pose_bone_indices[forearm_key])
	if forearm_idx < 0:
		return upper_arm_direction.normalized()

	var forearm_rest := _skeleton.get_bone_rest(forearm_idx)
	var forearm_offset := forearm_rest.origin
	if forearm_offset.length() < 0.0001:
		return upper_arm_direction.normalized()
	var local_dir := forearm_offset.normalized()
	return (rest_basis * local_dir).normalized()


func _get_attached_forearm_key(bone_key: String) -> String:
	var key := bone_key.to_lower()
	if key.find("left") == 0 || key.find("left_") == 0:
		return "left_forearm"
	if key.find("right") == 0 || key.find("right_") == 0:
		return "right_forearm"
	return ""


func _shoulder_key_side_sign(bone_key: String) -> float:
	var key := bone_key.to_lower()
	if key.begins_with("left_") || key.find("left") == 0:
		return 1.0
	if key.begins_with("right_") || key.find("right") == 0:
		return -1.0
	return 1.0


func _pose_bone_key_from_index(human_key: String) -> String:
	# Prefer explicit key naming for shoulder correction fallback.
	match human_key:
		"leftShoulder", "leftUpperArm":
			return "left_shoulder"
		"rightShoulder", "rightUpperArm":
			return "right_shoulder"
		_:
			return ""


func _apply_motion_bone_pose_dual(
		bone_key: String,
		base_deg: float,
		sway_deg: float,
		axis: Vector3,
		secondary_deg: float,
		secondary_axis: Vector3,
		blend: float,
		joint_delta_limit_deg: float = -1.0
	) -> void:
	if not _pose_bone_indices.has(bone_key) or not _skeleton:
		return
	var bone_idx := int(_pose_bone_indices[bone_key])
	if bone_idx < 0:
		return
	if not _pose_bone_rest_rotations.has(bone_idx):
		return

	var current_blend := clampf(blend, 0.0, 1.0)
	var rest_rotation: Quaternion = _pose_bone_rest_rotations[bone_idx] as Quaternion
	var rest_basis := Basis(rest_rotation)
	var primary_axis := axis.normalized()
	var secondary_axis_norm := secondary_axis.normalized()
	if primary_axis.length() < 0.0001:
		primary_axis = Vector3.RIGHT
	if secondary_axis_norm.length() < 0.0001:
		secondary_axis_norm = Vector3.UP
	var target_basis := (
		rest_basis
		* Basis(primary_axis, deg_to_rad(base_deg + sway_deg))
		* Basis(secondary_axis_norm, deg_to_rad(secondary_deg))
	)
	var target_rotation: Quaternion = target_basis.get_rotation_quaternion()
	if joint_delta_limit_deg > 0.0:
		target_rotation = _clamp_delta_rotation(rest_rotation, target_rotation, joint_delta_limit_deg)
	var current_rotation := _skeleton.get_bone_pose_rotation(bone_idx)
	if not _is_finite_quat(current_rotation) or not _is_finite_quat(target_rotation):
		return
	var output_rotation: Quaternion = current_rotation.slerp(target_rotation, current_blend)
	_skeleton.set_bone_pose_rotation(bone_idx, output_rotation)


func _quat_angle_between(a: Quaternion, b: Quaternion) -> float:
	var delta := a.inverse() * b
	var raw_w := clampf(delta.w, -1.0, 1.0)
	return rad_to_deg(acos(abs(raw_w)) * 2.0)


func _clamp_delta_rotation(from_rotation: Quaternion, target_rotation: Quaternion, max_delta_deg: float) -> Quaternion:
	var max_delta := maxf(0.0, max_delta_deg)
	if max_delta <= 0.0:
		return target_rotation
	var delta_deg := _quat_angle_between(from_rotation, target_rotation)
	if delta_deg <= max_delta:
		return target_rotation
	var ratio := clampf(deg_to_rad(max_delta) / maxf(deg_to_rad(delta_deg), 0.000001), 0.0, 1.0)
	return from_rotation.slerp(target_rotation, ratio)


func _is_finite_quat(value: Quaternion) -> bool:
	return is_finite(value.x) and is_finite(value.y) and is_finite(value.z) and is_finite(value.w)


func _attach_route_b_overlay_meshes_to_head() -> void:
	## Mouth/blink overlays: prefer already Head-bone-parented (accept3 build). Legacy
	## world-space overlays get BoneAttachment + tight scale so they cannot crush the face.
	if not _vrm_instance:
		return
	var skeleton := _find_first_of_type(_vrm_instance, "Skeleton3D") as Skeleton3D
	if skeleton == null:
		return
	var head_idx := skeleton.find_bone("Head")
	if head_idx < 0:
		head_idx = _find_bone_by_candidates(["head", "j_bip_c_head"])
	if head_idx < 0:
		push_warning("SIVRMAvatarAdapter: route_b overlay attach skipped (no Head bone)")
		return
	var head_name := skeleton.get_bone_name(head_idx)
	for node in _all_nodes(_vrm_instance):
		if not (node is MeshInstance3D):
			continue
		var mesh_instance := node as MeshInstance3D
		var lower := mesh_instance.name.to_lower()
		if not ("mouth" in lower or "blink" in lower or "viseme" in lower or "lid" in lower):
			continue
		# Avoid matching "solid" via "lid": require eyelid/blink/mouth/viseme tokens.
		if "solid" in lower and not ("mouth" in lower or "blink" in lower or "viseme" in lower or "eyelid" in lower):
			continue
		# Soft materials: never let opaque dark plates hide nose/lips at rest.
		_soften_route_b_overlay_material(mesh_instance)
		var mesh_aabb := mesh_instance.mesh.get_aabb() if mesh_instance.mesh else AABB()
		var extent := mesh_aabb.size.length()
		# Already tiny head-local overlays (accept3): light nudge only, no reparent crush.
		if extent > 0.0 and extent < 0.08:
			if "mouth" in lower or "viseme" in lower:
				mesh_instance.position = mesh_instance.position + Vector3(0.0, -0.002, -0.004)
			else:
				mesh_instance.position = mesh_instance.position + Vector3(0.0, 0.001, -0.003)
			mesh_instance.scale = Vector3.ONE
			print("[SIVRMAvatarAdapter] route_b_overlay_kept_local mesh=%s extent=%.4f" % [mesh_instance.name, extent])
			continue
		var attachment := BoneAttachment3D.new()
		attachment.name = "%s_HeadAttach" % mesh_instance.name
		attachment.bone_name = head_name
		skeleton.add_child(attachment)
		var parent := mesh_instance.get_parent()
		if parent:
			parent.remove_child(mesh_instance)
		attachment.add_child(mesh_instance)
		# Legacy world-space overlays: recenter + shrink hard so dark plates cannot cover face.
		var recenter := -mesh_aabb.get_center()
		if "mouth" in lower or "viseme" in lower:
			mesh_instance.position = recenter + Vector3(0.0, -0.042, -0.030)
		else:
			mesh_instance.position = recenter + Vector3(0.0, 0.018, -0.028)
		mesh_instance.rotation_degrees = Vector3.ZERO
		# Scale down oversized legacy ellipses (accept2 crush root cause).
		mesh_instance.scale = Vector3(0.35, 0.35, 0.35)
		print("[SIVRMAvatarAdapter] route_b_overlay_attached mesh=%s bone=%s scale=0.35 extent=%.4f" % [mesh_instance.name, head_name, extent])


func _attach_route_b_face_feature_card() -> void:
	## Candidate-only: tiny Head-local nose/mouth guide card.
	## Uses PortableCompressedTexture2D (not ImageTexture) to avoid Compatibility grain.
	## Accept host hides mouth/blink overlays but keeps this card (name has neither token).
	if not _vrm_instance:
		return
	var skeleton := _find_first_of_type(_vrm_instance, "Skeleton3D") as Skeleton3D
	if skeleton == null:
		return
	var head_idx := skeleton.find_bone("Head")
	if head_idx < 0:
		head_idx = _find_bone_by_candidates(["head", "j_bip_c_head"])
	if head_idx < 0:
		return
	var abs_png := ProjectSettings.globalize_path(
		"res://assets/avatars/lumina_route_b_candidate_20260718/bake/face_feature_card_20260718.png"
	)
	if not FileAccess.file_exists(abs_png):
		push_warning("SIVRMAvatarAdapter: face feature card PNG missing")
		return
	var img := Image.new()
	if img.load(abs_png) != OK:
		return
	var tex := PortableCompressedTexture2D.new()
	tex.create_from_image(img, PortableCompressedTexture2D.COMPRESSION_MODE_LOSSLESS)
	var mat := StandardMaterial3D.new()
	mat.resource_local_to_scene = true
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_texture = tex
	mat.albedo_color = Color(1, 1, 1, 1)
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	_set_object_property_if_present(mat, "disable_receive_shadows", true)
	var quad := QuadMesh.new()
	# Smaller than accept3 trial (0.095x0.118) which broke framing AABB.
	quad.size = Vector2(0.038, 0.048)
	var mi := MeshInstance3D.new()
	mi.name = "Lumina_FaceFeatureCard"
	mi.mesh = quad
	mi.set_surface_override_material(0, mat)
	var attachment := BoneAttachment3D.new()
	attachment.name = "Lumina_FaceFeatureCard_HeadAttach"
	attachment.bone_name = skeleton.get_bone_name(head_idx)
	skeleton.add_child(attachment)
	attachment.add_child(mi)
	# Sit just in front of mid-face; keep within head bounds for framing.
	mi.position = Vector3(0.0, -0.022, -0.058)
	mi.rotation_degrees = Vector3(0.0, 180.0, 0.0)
	print("[SIVRMAvatarAdapter] route_b_face_feature_card=on size=0.038x0.048 class=PortableCompressedTexture2D")


func _soften_route_b_overlay_material(mesh_instance: MeshInstance3D) -> void:
	## Candidate-only: translucent dark overlays so even a mis-scale cannot erase the face.
	if mesh_instance.mesh == null:
		return
	for surface_i in range(mesh_instance.mesh.get_surface_count()):
		var mat := mesh_instance.get_active_material(surface_i)
		if mat == null:
			continue
		if mat is StandardMaterial3D:
			var standard := (mat as StandardMaterial3D).duplicate() as StandardMaterial3D
			standard.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			standard.albedo_color.a = minf(standard.albedo_color.a, 0.55)
			if maxf(standard.albedo_color.r, maxf(standard.albedo_color.g, standard.albedo_color.b)) > 0.35:
				standard.albedo_color = Color(0.06, 0.02, 0.05, 0.50)
			standard.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
			standard.cull_mode = BaseMaterial3D.CULL_BACK
			mesh_instance.set_surface_override_material(surface_i, standard)
		elif mat is ShaderMaterial:
			var shader_mat := mat as ShaderMaterial
			var color := _get_shader_color_parameter(shader_mat, "_Color")
			color.a = minf(color.a if color.a > 0.0 else 1.0, 0.50)
			_set_shader_parameter_if_present(shader_mat, "_Color", color)


func _apply_realistic_model_shading() -> void:
	if not enable_realistic_model_shading or not _vrm_instance:
		return
	for node in _all_nodes(_vrm_instance):
		if not (node is MeshInstance3D):
			continue
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.get_meta("route_b_midface_shell", false):
			continue
		if str(mesh_instance.name).to_lower().find("midfaceshell") >= 0:
			continue
		mesh_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
		_set_object_property_if_present(mesh_instance, "gi_mode", 1)
		if mesh_instance.material_override:
			var override_material := _duplicate_and_tune_avatar_material(mesh_instance.material_override, mesh_instance.name)
			if override_material:
				mesh_instance.material_override = override_material
			continue
		if not mesh_instance.mesh:
			continue
		for surface_index in range(mesh_instance.mesh.get_surface_count()):
			var surface_material := mesh_instance.get_active_material(surface_index)
			if surface_material == null:
				continue
			var tuned_material := _duplicate_and_tune_avatar_material(
				surface_material,
				"%s %s" % [mesh_instance.name, surface_material.resource_name]
			)
			if tuned_material:
				mesh_instance.set_surface_override_material(surface_index, tuned_material)


func _duplicate_and_tune_avatar_material(material: Material, hint: String) -> Material:
	var duplicated := material.duplicate() as Material
	if duplicated == null:
		return null
	_set_object_property_if_present(duplicated, "resource_local_to_scene", true)
	if duplicated is StandardMaterial3D:
		_tune_avatar_standard_material(duplicated as StandardMaterial3D, hint)
	elif duplicated is ShaderMaterial:
		_tune_avatar_shader_material(duplicated as ShaderMaterial, hint)
	return duplicated


func _tune_avatar_standard_material(material: StandardMaterial3D, hint: String) -> void:
	var lower_hint := hint.to_lower()
	var target_roughness := 0.66
	var target_specular := 0.34
	var clearcoat := 0.0
	if _matches_any(lower_hint, ["skin", "face", "body", "hand", "arm", "leg", "hada", "head"]):
		target_roughness = 0.74
		target_specular = 0.24
		_set_object_property_if_present(material, "subsurf_scatter_enabled", true)
		_set_object_property_if_present(material, "subsurf_scatter_strength", 0.055)
	elif _matches_any(lower_hint, ["hair", "kami"]):
		target_roughness = 0.48
		target_specular = 0.46
		clearcoat = 0.10
	elif _matches_any(lower_hint, ["eye", "iris", "pupil", "hitomi"]):
		target_roughness = 0.20
		target_specular = 0.66
		clearcoat = 0.22
	elif _matches_any(lower_hint, ["cloth", "dress", "skirt", "shirt", "wear", "uniform", "ribbon", "shoe", "boot"]):
		target_roughness = 0.82
		target_specular = 0.22
	_compress_standard_material_albedo(material, lower_hint)
	material.roughness = lerpf(clampf(material.roughness, 0.0, 1.0), target_roughness, 0.62)
	material.metallic = minf(material.metallic, 0.03)
	_set_object_property_if_present(material, "metallic_specular", target_specular)
	_set_object_property_if_present(material, "specular", target_specular)
	if clearcoat > 0.0:
		_set_object_property_if_present(material, "clearcoat_enabled", true)
		_set_object_property_if_present(material, "clearcoat", clearcoat)
		_set_object_property_if_present(material, "clearcoat_roughness", 0.42)


func _tune_avatar_shader_material(material: ShaderMaterial, hint: String) -> void:
	var lower_hint := hint.to_lower()
	var max_value := 0.84
	if _matches_any(lower_hint, ["cloth", "dress", "skirt", "shirt", "wear", "uniform", "ribbon", "shoe", "boot"]):
		max_value = 0.74
	elif _matches_any(lower_hint, ["skin", "face", "body", "hand", "arm", "leg", "hada", "head"]):
		max_value = 0.86
	elif _matches_any(lower_hint, ["hair", "kami"]):
		max_value = 0.82
	elif _matches_any(lower_hint, ["eye", "iris", "pupil", "hitomi"]):
		max_value = 0.96
	var base_color := _get_shader_color_parameter(material, "_Color")
	if base_color.a > 0.0:
		var compressed := _compress_color_brightness(base_color, max_value)
		_set_shader_parameter_if_present(material, "_Color", compressed)
		_set_shader_parameter_if_present(material, "_ShadeColor", _shade_color_from_lit_color(compressed, lower_hint))
	_set_shader_parameter_if_present(material, "_IndirectLightIntensity", 0.055)
	_set_shader_parameter_if_present(material, "_ReceiveShadowRate", 0.86)
	_set_shader_parameter_if_present(material, "_ShadingGradeRate", 0.92)
	_set_shader_parameter_if_present(material, "_ShadeShift", 0.08)
	# Route B candidate preview must keep authored ShadeToony=0.3 (do not overwrite to 0.62).
	if _is_route_b_candidate_vrm():
		_set_shader_parameter_if_present(material, "_ShadeToony", 0.3)
		_set_shader_parameter_if_present(material, "_EmissionMultiplier", 0.0)
		_set_shader_parameter_if_present(material, "_RimLift", 0.0)
		_set_shader_parameter_if_present(material, "_RimLightingMix", 0.0)
		_set_shader_parameter_if_present(material, "_LightColorAttenuation", 0.0)
	else:
		_set_shader_parameter_if_present(material, "_ShadeToony", 0.62)
		_set_shader_parameter_if_present(material, "_LightColorAttenuation", 0.12)
		_set_shader_parameter_if_present(material, "_EmissionMultiplier", 0.16)
		_set_shader_parameter_if_present(material, "_RimLift", 0.015)
		_set_shader_parameter_if_present(material, "_RimLightingMix", 0.16)


func _compress_standard_material_albedo(material: StandardMaterial3D, lower_hint: String) -> void:
	var max_value := 0.86
	if _matches_any(lower_hint, ["cloth", "dress", "skirt", "shirt", "wear", "uniform", "ribbon", "shoe", "boot"]):
		max_value = 0.76
	elif _matches_any(lower_hint, ["skin", "face", "body", "hand", "arm", "leg", "hada", "head"]):
		max_value = 0.88
	material.albedo_color = _compress_color_brightness(material.albedo_color, max_value)


func _shade_color_from_lit_color(color: Color, lower_hint: String) -> Color:
	var shade_amount := 0.24
	if _matches_any(lower_hint, ["skin", "face", "body", "hand", "arm", "leg", "hada", "head"]):
		shade_amount = 0.16
	elif _matches_any(lower_hint, ["hair", "kami"]):
		shade_amount = 0.30
	return Color(
		clampf(color.r * (1.0 - shade_amount), 0.0, 1.0),
		clampf(color.g * (1.0 - shade_amount), 0.0, 1.0),
		clampf(color.b * (1.0 - shade_amount), 0.0, 1.0),
		color.a
	)


func _compress_color_brightness(color: Color, max_value: float) -> Color:
	var max_channel := maxf(color.r, maxf(color.g, color.b))
	if max_channel <= max_value or max_channel <= 0.0001:
		return color
	var scale := max_value / max_channel
	return Color(color.r * scale, color.g * scale, color.b * scale, color.a)


func _get_shader_color_parameter(material: ShaderMaterial, parameter_name: String) -> Color:
	if not _shader_parameter_exists(material, parameter_name):
		return Color(0.0, 0.0, 0.0, 0.0)
	var value = material.get_shader_parameter(parameter_name)
	if typeof(value) == TYPE_COLOR:
		return value
	if typeof(value) == TYPE_VECTOR4:
		return Color(value.x, value.y, value.z, value.w)
	return Color(0.0, 0.0, 0.0, 0.0)


func _set_shader_parameter_if_present(material: ShaderMaterial, parameter_name: String, value) -> bool:
	if not _shader_parameter_exists(material, parameter_name):
		return false
	material.set_shader_parameter(parameter_name, value)
	return true


func _shader_parameter_exists(material: ShaderMaterial, parameter_name: String) -> bool:
	if material == null:
		return false
	var property_path := "shader_parameter/%s" % parameter_name
	for property_info in material.get_property_list():
		if String(property_info.get("name", "")) == property_path:
			return true
	return false


func _matches_any(text: String, keywords: Array) -> bool:
	for keyword in keywords:
		if text.contains(String(keyword)):
			return true
	return false


func _set_object_property_if_present(target: Object, property_name: String, value) -> bool:
	if target == null:
		return false
	for property_info in target.get_property_list():
		if String(property_info.get("name", "")) == property_name:
			target.set(property_name, value)
			return true
	return false


func _align_feet() -> void:
	var aabb := _combined_aabb(_model_root)
	if aabb.size == Vector3.ZERO:
		return
	_model_root.position.y -= aabb.position.y


func _combined_aabb(root: Node) -> AABB:
	var result := AABB()
	var has_aabb := false
	for node in _all_nodes(root):
		if not (node is MeshInstance3D):
			continue
		var mesh_instance := node as MeshInstance3D
		if not mesh_instance.mesh:
			continue
		var global_aabb := mesh_instance.global_transform * mesh_instance.mesh.get_aabb()
		if has_aabb:
			result = result.merge(global_aabb)
		else:
			result = global_aabb
			has_aabb = true
	return result


func _all_nodes(root: Node) -> Array[Node]:
	var nodes: Array[Node] = [root]
	for child in root.get_children():
		nodes.append_array(_all_nodes(child))
	return nodes


func _find_first_of_type(root: Node, wanted_class: String) -> Node:
	if root.is_class(wanted_class):
		return root
	for child in root.get_children():
		var found := _find_first_of_type(child, wanted_class)
		if found:
			return found
	return null


func _find_face_mesh(root: Node) -> MeshInstance3D:
	# Aogiri v051 keeps the face and body in one mesh. Prefer the mesh that
	# actually owns morph targets, then retain the legacy name-based fallback.
	if root is MeshInstance3D:
		var mesh_instance := root as MeshInstance3D
		if mesh_instance.mesh != null and mesh_instance.mesh.get_blend_shape_count() > 0:
			return mesh_instance
	for child in root.get_children():
		var found := _find_face_mesh(child)
		if found:
			return found
	if root is MeshInstance3D and root.name.to_lower().contains("face"):
		return root as MeshInstance3D
	return null


func _reset_expression_blends() -> void:
	if not _face_mesh or not _face_mesh.mesh:
		return
	_expression_shape_targets.clear()
	for name in available_blend_shapes:
		if name in [
			"A", "I", "U", "E", "O", "Blink", "Blink_L", "Blink_R",
			"BlinkLeft", "BlinkRight", "Happy", "Relaxed", "Sad",
			"Surprised", "Shy", "Confused", "Viseme_A", "Viseme_I",
			"Viseme_U", "Viseme_E", "Viseme_O"
		] or name.begins_with("Mouth_") or name.begins_with("Fcl_ALL_") or name.begins_with("Fcl_BRW_") or name.begins_with("Fcl_EYE_") or name.begins_with("Fcl_MTH_"):
			_set_blend_shape(name, 0.0)


func _apply_expression_shape_targets() -> void:
	for key in _expression_shape_targets.keys():
		var value := float(_expression_shape_targets[key])
		_set_blend_shape(key, value)


func _set_blend_shape(shape_name: String, value: float) -> bool:
	if not _vrm_instance:
		return false
	if not _blend_shape_bindings.has(shape_name):
		return false
	var bindings_variant = _blend_shape_bindings[shape_name]
	if typeof(bindings_variant) != TYPE_ARRAY:
		return false
	var applied := false
	for binding_variant in bindings_variant as Array:
		if typeof(binding_variant) != TYPE_DICTIONARY:
			continue
		var binding: Dictionary = binding_variant
		var mesh_variant = binding.get("mesh")
		if not (mesh_variant is MeshInstance3D):
			continue
		var mesh_instance := mesh_variant as MeshInstance3D
		if not is_instance_valid(mesh_instance) or mesh_instance.mesh == null:
			continue
		var index := int(binding.get("index", -1))
		if index < 0 or index >= mesh_instance.mesh.get_blend_shape_count():
			continue
		mesh_instance.set_blend_shape_value(index, value)
		applied = true
	return applied


func _blend_shapes_for_expression(expression: String) -> Array[String]:
	match expression:
		"happy", "joy":
			return _combined_or_components(
				["Fcl_ALL_Joy", "Happy"],
				["Fcl_EYE_Joy", "Fcl_MTH_Joy"]
			)
		"smile":
			return _combined_or_components(
				["Fcl_ALL_Joy", "Happy"],
				["Fcl_EYE_Joy", "Fcl_MTH_Smile", "Fcl_MTH_Joy"]
			)
		"fun", "relaxed":
			return _combined_or_components(
				["Fcl_ALL_Fun", "Relaxed"],
				["Fcl_EYE_Fun", "Fcl_MTH_Fun"]
			)
		"sad", "sorrow":
			return _combined_or_components(
				["Fcl_ALL_Sorrow", "Sad"],
				["Fcl_BRW_Sorrow", "Fcl_EYE_Sorrow", "Fcl_MTH_Sorrow"]
			)
		"angry":
			return _combined_or_components(
				["Fcl_ALL_Angry"],
				["Fcl_BRW_Angry", "Fcl_EYE_Angry", "Fcl_MTH_Angry"]
			)
		"surprised", "surprise":
			return _combined_or_components(
				["Fcl_ALL_Surprised", "Surprised"],
				["Fcl_BRW_Surprised", "Fcl_EYE_Surprised", "Fcl_MTH_Surprised"]
			)
		"thinking", "serious":
			return _combined_or_components(
				["Confused"],
				["Fcl_BRW_Angry", "Fcl_EYE_Close", "Fcl_MTH_Neutral"]
			)
		"shy":
			return _first_available_expression_shape(["Shy"])
		"confused":
			return _first_available_expression_shape(["Confused"])
		"blink":
			return _first_available_expression_shape(["Fcl_EYE_Close", "Blink"])
		"aa", "a":
			return _first_available_expression_shape(_LIPSYNC_MOUTH_SHAPE_GROUPS[0])
		"ih", "i":
			return _first_available_expression_shape(_LIPSYNC_MOUTH_SHAPE_GROUPS[1])
		"ou", "u":
			return _first_available_expression_shape(_LIPSYNC_MOUTH_SHAPE_GROUPS[2])
		"ee", "e":
			return _first_available_expression_shape(_LIPSYNC_MOUTH_SHAPE_GROUPS[3])
		"oh", "o":
			return _first_available_expression_shape(_LIPSYNC_MOUTH_SHAPE_GROUPS[4])
		"neutral":
			return _first_available_expression_shape(["Fcl_ALL_Neutral", "Fcl_MTH_Neutral"])
		_:
			# Unknown → neutral (hardening).
			print("[SIVRMAvatarAdapter] fallback_neutral expression=%s" % expression)
			return _first_available_expression_shape(["Fcl_ALL_Neutral", "Fcl_MTH_Neutral"])


func _combined_or_components(combined_candidates: Array, component_candidates: Array) -> Array[String]:
	var combined := _first_available_expression_shape(combined_candidates)
	if not combined.is_empty():
		return combined
	var resolved: Array[String] = []
	for candidate_variant in component_candidates:
		var candidate := String(candidate_variant)
		if _blend_shape_bindings.has(candidate):
			resolved.append(candidate)
	return resolved


func _first_available_expression_shape(candidates: Array) -> Array[String]:
	## Direct bridge expression commands and live TTS must resolve the same
	## concrete morph. Applying both big/small variants at once distorts models
	## that expose both, so select the first cached binding exactly as lipsync does.
	var resolved: Array[String] = []
	for candidate_variant in candidates:
		var candidate := String(candidate_variant)
		if _blend_shape_bindings.has(candidate):
			resolved.append(candidate)
			break
	return resolved


func _animation_name_for_expression(expression: String) -> String:
	match expression:
		"joy":
			return "happy"
		"sorrow":
			return "sad"
		"surprise":
			return "Surprised"
		"a":
			return "aa"
		"i":
			return "ih"
		"u":
			return "ou"
		"e":
			return "ee"
		"o":
			return "oh"
		_:
			return expression
