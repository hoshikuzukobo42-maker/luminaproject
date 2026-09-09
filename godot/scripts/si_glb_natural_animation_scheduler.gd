extends Node
class_name SiGlbNaturalAnimationScheduler

signal animation_requested(file_name: String, state_name: String, blend_seconds: float)
signal state_changed(state_name: String)

const DEFAULT_POLICY_PATH := "res://assets/avatars/lumina_meshi_biped/lumina_natural_animation_policy_20260621.json"

var policy_path: String = DEFAULT_POLICY_PATH
var policy: Dictionary = {}
var semantic_state: String = "idle_base"
var activity_style: String = "calm"
var fallback_state: String = "idle_base"
var current_clip: String = ""
var last_non_idle_clip: String = ""
var state_elapsed: float = 0.0
var clip_elapsed: float = 0.0
var ambient_elapsed: float = 0.0
var ambient_gap_elapsed: float = 999.0
var tick_elapsed: float = 0.0
var runtime_seconds: float = 0.0
var rng := RandomNumberGenerator.new()
var disabled := false
var procedural_only_mode := false
var recent_non_idle_clips: Array[String] = []
var state_cooldown_until: Dictionary = {}

func _ready() -> void:
	rng.randomize()
	_load_policy()
	_request_state_clip(semantic_state)

func _process(delta: float) -> void:
	if disabled:
		return
	runtime_seconds += delta
	state_elapsed += delta
	clip_elapsed += delta
	ambient_elapsed += delta
	ambient_gap_elapsed += delta
	tick_elapsed += delta
	var tick_seconds := float(policy.get("scheduler_defaults", {}).get("tick_seconds", 0.20))
	if tick_elapsed < tick_seconds:
		return
	tick_elapsed = 0.0
	_tick_living_motion()

func set_policy_path(next_path: String) -> void:
	policy_path = next_path
	_load_policy()

func set_activity_style(next_style: String) -> bool:
	var normalized := next_style.strip_edges().to_lower()
	var styles: Dictionary = policy.get("styles", {})
	if normalized.is_empty() or not styles.has(normalized):
		return false
	activity_style = normalized
	return true

func set_semantic_state(next_state: String) -> void:
	if next_state == "" or next_state == semantic_state:
		return
	_mark_state_cooldown(semantic_state)
	semantic_state = next_state
	state_elapsed = 0.0
	clip_elapsed = 0.0
	emit_signal("state_changed", semantic_state)
	_request_state_clip(semantic_state)

func notify_animation_finished() -> void:
	_mark_state_cooldown(semantic_state)
	var recovery := _state_recovery(semantic_state)
	if recovery == "":
		recovery = fallback_state
	set_semantic_state(recovery)

func notify_animation_unavailable(state_name: String = "") -> void:
	procedural_only_mode = true
	if state_name != "" and state_name == semantic_state and state_name != fallback_state:
		set_semantic_state(_state_recovery(state_name))

func set_procedural_only_mode(enabled: bool) -> void:
	procedural_only_mode = enabled

func force_idle() -> void:
	set_semantic_state(fallback_state)

func _load_policy() -> void:
	policy = {}
	if not ResourceLoader.exists(policy_path):
		policy = _fallback_policy()
		return
	var file := FileAccess.open(policy_path, FileAccess.READ)
	if file == null:
		policy = _fallback_policy()
		return
	var parsed = JSON.parse_string(file.get_as_text())
	if typeof(parsed) == TYPE_DICTIONARY:
		policy = parsed
	else:
		policy = _fallback_policy()
	fallback_state = String(policy.get("scheduler_defaults", {}).get("fallback_state", "idle_base"))
	activity_style = String(policy.get("default_style", activity_style)).strip_edges().to_lower()

func _tick_living_motion() -> void:
	if procedural_only_mode and semantic_state != fallback_state:
		var procedural_max_seconds := float(_state_def(semantic_state).get("max_seconds", 1.6))
		if procedural_max_seconds <= 0.0:
			procedural_max_seconds = 1.6
		if state_elapsed >= procedural_max_seconds:
			notify_animation_finished()
		return
	if semantic_state == "idle_base":
		_maybe_idle_micro_action()
		return
	var state := _state_def(semantic_state)
	var max_seconds := float(state.get("max_seconds", 0.0))
	if max_seconds <= 0.0 and not bool(state.get("loop", true)):
		max_seconds = float(policy.get("scheduler_defaults", {}).get("one_shot_max_seconds", 5.0))
	if max_seconds > 0.0 and state_elapsed >= max_seconds:
		notify_animation_finished()

func _maybe_idle_micro_action() -> void:
	var defaults: Dictionary = policy.get("scheduler_defaults", {})
	var ambient_check_seconds := _style_float("ambient_check_seconds", float(defaults.get("ambient_check_seconds", 3.0)))
	var ambient_min_gap_seconds := _style_float("ambient_min_gap_seconds", float(defaults.get("ambient_min_gap_seconds", 12.0)))
	if ambient_elapsed < ambient_check_seconds:
		return
	ambient_elapsed = 0.0
	if ambient_gap_elapsed < ambient_min_gap_seconds:
		return
	if _state_on_cooldown("idle_micro"):
		return
	var chance := _style_float("idle_micro_chance", 0.18)
	if rng.randf() > chance:
		return
	ambient_gap_elapsed = 0.0
	set_semantic_state("idle_micro")

func _request_state_clip(state_name: String) -> void:
	var state := _state_def(state_name)
	if state.is_empty():
		state = _state_def(fallback_state)
	var clip := _choose_clip(state)
	if clip == "":
		return
	if _is_disabled_clip(clip):
		clip = String(_state_def(fallback_state).get("primary", ""))
	if clip == "":
		return
	current_clip = clip
	if state_name != fallback_state:
		last_non_idle_clip = clip
		_remember_non_idle_clip(clip)
	clip_elapsed = 0.0
	var blend_seconds := _style_float("blend_seconds", float(policy.get("scheduler_defaults", {}).get("blend_seconds", 0.35)))
	emit_signal("animation_requested", clip, state_name, blend_seconds)

func _choose_clip(state: Dictionary) -> String:
	if state.has("primary"):
		return String(state.get("primary", ""))
	var weighted: Array = state.get("weighted_clips", [])
	if weighted.is_empty():
		return ""
	var usable: Array = []
	for item in weighted:
		if typeof(item) != TYPE_DICTIONARY:
			continue
		var file_name := String(item.get("file", ""))
		if file_name == "":
			continue
		if _is_disabled_clip(file_name):
			continue
		if _is_recent_non_idle_clip(file_name) and weighted.size() > 1:
			continue
		usable.append(item)
	if usable.is_empty():
		for item in weighted:
			if typeof(item) != TYPE_DICTIONARY:
				continue
			var file_name := String(item.get("file", ""))
			if file_name == "" or _is_disabled_clip(file_name) or file_name == last_non_idle_clip:
				continue
			usable.append(item)
	if usable.is_empty():
		usable = weighted
	var total := 0.0
	for item in usable:
		if typeof(item) == TYPE_DICTIONARY:
			total += float(item.get("weight", 0.0))
	if total <= 0.0:
		return String(usable[0].get("file", ""))
	var pick := rng.randf() * total
	var cursor := 0.0
	for item in usable:
		if typeof(item) != TYPE_DICTIONARY:
			continue
		cursor += float(item.get("weight", 0.0))
		if pick <= cursor:
			return String(item.get("file", ""))
	return String(usable.back().get("file", ""))

func _remember_non_idle_clip(file_name: String) -> void:
	if file_name == "":
		return
	recent_non_idle_clips.push_front(file_name)
	var limit := int(policy.get("scheduler_defaults", {}).get("non_idle_repetition_window", 1))
	if limit < 1:
		limit = 1
	while recent_non_idle_clips.size() > limit:
		recent_non_idle_clips.pop_back()

func _is_recent_non_idle_clip(file_name: String) -> bool:
	return recent_non_idle_clips.has(file_name)

func _mark_state_cooldown(state_name: String) -> void:
	var cooldown_seconds := float(_state_def(state_name).get("cooldown_seconds", 0.0))
	if cooldown_seconds > 0.0:
		state_cooldown_until[state_name] = runtime_seconds + cooldown_seconds

func _state_on_cooldown(state_name: String) -> bool:
	return runtime_seconds < float(state_cooldown_until.get(state_name, -1.0))

func _state_def(state_name: String) -> Dictionary:
	return policy.get("states", {}).get(state_name, {})

func _state_recovery(state_name: String) -> String:
	return String(_state_def(state_name).get("recovery", fallback_state))

func _style_float(key: String, fallback: float) -> float:
	var styles: Dictionary = policy.get("styles", {})
	var style: Dictionary = styles.get(activity_style, {})
	return float(style.get(key, fallback))

func _is_disabled_clip(file_name: String) -> bool:
	var disabled_groups: Dictionary = policy.get("disabled_clips", {})
	for group_name in disabled_groups.keys():
		var files: Array = disabled_groups[group_name]
		if files.has(file_name):
			return true
	return false

func _fallback_policy() -> Dictionary:
	# PHASE2_LIFE_LOOP_DESIGN.md §7: Phase 2 adds breathing / listening /
	# talk_underlay semantics as distinct logical states. GLB clips are
	# REUSED from idle_base / speaking — visible animation is the same,
	# but trace / state machine can distinguish them. New clips can be
	# swapped in later (Phase 4+) without changing the scheduler API.
	var idle_glb := "Meshy_AI_Aogiri_High_School_Un_biped_Animation_Idle_12_withSkin.glb"
	var speak_glb := "Meshy_AI_Aogiri_High_School_Un_biped_Animation_Stand_and_Chat_withSkin.glb"
	return {
		"states": {
			"idle_base": {
				"primary": idle_glb,
				"loop": true
			},
			"speaking": {
				"primary": speak_glb,
				"loop": true,
				"recovery": "idle_base"
			},
			"breathing": {
				"primary": idle_glb,
				"loop": true,
				"recovery": "idle_base",
				"max_seconds": 12.0
			},
			"listening": {
				"primary": idle_glb,
				"loop": true,
				"recovery": "idle_base",
				"max_seconds": 8.0
			},
			"talk_underlay": {
				"primary": speak_glb,
				"loop": true,
				"recovery": "idle_base",
				"max_seconds": 12.0
			}
		},
		"scheduler_defaults": {
			"tick_seconds": 0.20,
			"blend_seconds": 0.35,
			"ambient_check_seconds": 3.0,
			"ambient_min_gap_seconds": 12.0,
			"one_shot_max_seconds": 5.0,
			"non_idle_repetition_window": 1,
			"fallback_state": "idle_base"
		}
	}
