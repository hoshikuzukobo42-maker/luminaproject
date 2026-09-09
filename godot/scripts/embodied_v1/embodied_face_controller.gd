extends Node
class_name EmbodiedFaceController

signal avatar_bound(shape_names: PackedStringArray)
signal expression_changed(expression_name: String, intensity: float)
signal viseme_changed(viseme_name: String, intensity: float)

const BLINK_IDLE := 0
const BLINK_CLOSING := 1
const BLINK_HOLD := 2
const BLINK_OPENING := 3

const EXPRESSION_SHAPES := [
	"Happy",
	"Relaxed",
	"Sad",
	"Surprised",
	"Shy",
	"Confused",
]
const VISEME_SHAPES := ["Viseme_A", "Viseme_I", "Viseme_U", "Viseme_E", "Viseme_O"]
const REQUIRED_SHAPES := [
	"Blink",
	"BlinkLeft",
	"BlinkRight",
	"Happy",
	"Relaxed",
	"Sad",
	"Surprised",
	"Shy",
	"Confused",
	"Viseme_A",
	"Viseme_I",
	"Viseme_U",
	"Viseme_E",
	"Viseme_O",
]
const EXPRESSION_PRESETS := {
	"neutral": {},
	"happy": {"Happy": 1.0},
	"happy_soft": {"Happy": 0.62},
	"relaxed": {"Relaxed": 1.0, "Shy": 0.18},
	"sad": {"Sad": 1.0},
	"surprised": {"Surprised": 1.0},
	"shy": {"Shy": 1.0},
	"confused": {"Confused": 1.0},
	# Legacy detached-overlay QA must keep this neutral. Integrated source-mesh
	# candidates replace it with INTEGRATED_HEAD_PAT_PRESET at runtime.
	"head_pat": {},
}
const INTEGRATED_HEAD_PAT_PRESET := {"Relaxed": 0.72, "Happy": 0.32}

@export var synthetic_face_overlays_enabled := false
@export var integrated_face_morphs_enabled := false
@export var auto_blink_enabled := false
@export_range(0.05, 0.30, 0.01) var blink_close_seconds := 0.09
@export_range(0.01, 0.20, 0.01) var blink_hold_seconds := 0.045
@export_range(0.05, 0.35, 0.01) var blink_open_seconds := 0.12
@export_range(1.0, 12.0, 0.1) var blink_interval_min_seconds := 2.8
@export_range(1.0, 12.0, 0.1) var blink_interval_max_seconds := 6.2
@export_range(0.02, 1.0, 0.01) var default_transition_seconds := 0.16
@export var deterministic_seed := 0

var _avatar_root: Node
var _bindings: Dictionary = {}
var _canonical_names: Dictionary = {}
var _current_weights: Dictionary = {}
var _target_weights: Dictionary = {}
var _transition_seconds: Dictionary = {}
var _active_expression := "neutral"
var _active_viseme := ""
var _blink_state := BLINK_IDLE
var _blink_elapsed := 0.0
var _blink_clock := 0.0
var _blink_locked_by_expression := false
var _rng := RandomNumberGenerator.new()


func _ready() -> void:
	if deterministic_seed == 0:
		_rng.randomize()
	else:
		_rng.seed = deterministic_seed
	_schedule_next_blink()


func _process(delta: float) -> void:
	advance_face(delta)


func bind_avatar(avatar_root: Node) -> bool:
	if avatar_root == null:
		push_error("EmbodiedFaceController cannot bind a null avatar")
		return false
	_avatar_root = avatar_root
	_bindings.clear()
	_canonical_names.clear()
	_current_weights.clear()
	_target_weights.clear()
	_transition_seconds.clear()

	for node in _collect_nodes(avatar_root):
		if not (node is MeshInstance3D):
			continue
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.mesh == null:
			continue
		for blend_index in range(mesh_instance.mesh.get_blend_shape_count()):
			var canonical_name := String(mesh_instance.mesh.get_blend_shape_name(blend_index))
			var key := _shape_key(canonical_name)
			var entries: Array = _bindings.get(key, [])
			entries.append({"mesh": mesh_instance, "index": blend_index})
			_bindings[key] = entries
			_canonical_names[key] = canonical_name
			if not _current_weights.has(key):
				var current := mesh_instance.get_blend_shape_value(blend_index)
				_current_weights[key] = current
				_target_weights[key] = current
				_transition_seconds[key] = default_transition_seconds

	var names := get_available_shapes()
	# Imported blend-shape state is not trusted. Zero every shape before the
	# avatar can be presented to a user.
	reset_face(true)
	avatar_bound.emit(names)
	if names.is_empty():
		push_warning("EmbodiedFaceController bound in neutral-only mode: source avatar has no blend shapes")
	return true


func has_required_shapes() -> bool:
	for shape_name in REQUIRED_SHAPES:
		if not has_shape(shape_name):
			return false
	return true


func has_shape(shape_name: String) -> bool:
	return _bindings.has(_shape_key(shape_name))


func get_available_shapes() -> PackedStringArray:
	var names := PackedStringArray()
	for canonical_name in _canonical_names.values():
		names.append(String(canonical_name))
	names.sort()
	return names


func set_expression(expression_name: String, intensity := 1.0, transition_seconds := -1.0) -> bool:
	var preset_name := _preset_key(expression_name)
	if not EXPRESSION_PRESETS.has(preset_name):
		push_warning("Unsupported embodied expression: %s" % expression_name)
		return false
	var duration := default_transition_seconds if transition_seconds < 0.0 else transition_seconds
	for shape_name in EXPRESSION_SHAPES:
		_set_shape_target(shape_name, 0.0, duration)
	if not _face_deformation_enabled():
		_active_expression = "neutral"
		_blink_locked_by_expression = false
		_cancel_blink()
		expression_changed.emit(_active_expression, 0.0)
		return true
	var preset: Dictionary = (
		INTEGRATED_HEAD_PAT_PRESET
		if preset_name == "head_pat" and integrated_face_morphs_enabled
		else EXPRESSION_PRESETS[preset_name]
	)
	var level := clampf(intensity, 0.0, 1.0)
	for shape_name in preset:
		_set_shape_target(String(shape_name), float(preset[shape_name]) * level, duration)
	_active_expression = preset_name
	_blink_locked_by_expression = float(preset.get("Relaxed", 0.0)) * level > 0.05
	if _blink_locked_by_expression:
		_cancel_blink()
	expression_changed.emit(_active_expression, level)
	return true


func set_viseme(viseme_name: String, intensity := 1.0, transition_seconds := -1.0) -> bool:
	var symbol := viseme_name.strip_edges().to_upper().trim_prefix("VISEME_")
	var requested_shape := "Viseme_%s" % symbol
	if not VISEME_SHAPES.has(requested_shape):
		push_warning("Unsupported embodied viseme: %s" % viseme_name)
		return false
	var duration := default_transition_seconds if transition_seconds < 0.0 else transition_seconds
	for shape_name in VISEME_SHAPES:
		_set_shape_target(shape_name, 0.0, duration)
	if not _face_deformation_enabled():
		_active_viseme = ""
		viseme_changed.emit("", 0.0)
		return true
	if not has_shape(requested_shape):
		push_warning("Unsupported embodied viseme: %s" % viseme_name)
		return false
	var level := clampf(intensity, 0.0, 1.0)
	_set_shape_target(requested_shape, level, duration)
	_active_viseme = symbol
	viseme_changed.emit(_active_viseme, level)
	return true


func set_japanese_vowel(vowel: String, intensity := 1.0, transition_seconds := -1.0) -> bool:
	var normalized := vowel.strip_edges().to_lower()
	var aliases := {
		"a": "A", "あ": "A",
		"i": "I", "い": "I",
		"u": "U", "う": "U",
		"e": "E", "え": "E",
		"o": "O", "お": "O",
	}
	if not aliases.has(normalized):
		return false
	return set_viseme(String(aliases[normalized]), intensity, transition_seconds)


func clear_viseme(transition_seconds := -1.0) -> void:
	var duration := default_transition_seconds if transition_seconds < 0.0 else transition_seconds
	for shape_name in VISEME_SHAPES:
		_set_shape_target(shape_name, 0.0, duration)
	_active_viseme = ""
	viseme_changed.emit("", 0.0)


func set_auto_blink_enabled(enabled: bool) -> void:
	auto_blink_enabled = enabled and _face_deformation_enabled()
	if enabled:
		_schedule_next_blink()
	else:
		_cancel_blink()


func force_blink() -> bool:
	if not _face_deformation_enabled() or not has_shape("Blink") or _blink_locked_by_expression:
		return false
	_blink_state = BLINK_CLOSING
	_blink_elapsed = 0.0
	return true


func get_shape_weight(shape_name: String) -> float:
	return float(_current_weights.get(_shape_key(shape_name), 0.0))


func get_shape_target(shape_name: String) -> float:
	return float(_target_weights.get(_shape_key(shape_name), 0.0))


func get_active_expression() -> String:
	return _active_expression


func get_active_viseme() -> String:
	return _active_viseme


func get_face_safety_status() -> Dictionary:
	var source_shapes_available := not _bindings.is_empty()
	return {
		"mode": (
			"integrated_source_mesh"
			if integrated_face_morphs_enabled
			else ("isolated_overlay_qa"
			if synthetic_face_overlays_enabled
			else ("neutral_fallback" if source_shapes_available else "source_rig_unavailable_neutral")
			)
		),
		"source_shapes_available": source_shapes_available,
		"available_shape_count": _bindings.size(),
		"facial_deformation_enabled": _face_deformation_enabled(),
		"integrated_face_morphs_enabled": integrated_face_morphs_enabled,
		"synthetic_face_overlays_enabled": synthetic_face_overlays_enabled,
		"auto_blink_enabled": auto_blink_enabled,
		"active_expression": _active_expression,
		"active_viseme": _active_viseme,
	}


func advance_face(delta: float) -> void:
	if delta <= 0.0 or _bindings.is_empty():
		return
	_update_blink(delta)
	for key in _target_weights.keys():
		var target := float(_target_weights[key])
		var current := float(_current_weights.get(key, 0.0))
		var duration := maxf(0.001, float(_transition_seconds.get(key, default_transition_seconds)))
		var next_weight := move_toward(current, target, delta / duration)
		_current_weights[key] = next_weight
		_apply_weight(String(key), next_weight)


func reset_face(immediate := false) -> void:
	_active_expression = "neutral"
	_active_viseme = ""
	_blink_locked_by_expression = false
	_blink_state = BLINK_IDLE
	_schedule_next_blink()
	for key in _target_weights.keys():
		_target_weights[key] = 0.0
		_transition_seconds[key] = 0.001 if immediate else default_transition_seconds
		if immediate:
			_current_weights[key] = 0.0
			_apply_weight(String(key), 0.0)


func _update_blink(delta: float) -> void:
	if not _face_deformation_enabled() or not auto_blink_enabled or _blink_locked_by_expression or not has_shape("Blink"):
		_set_shape_target("Blink", 0.0, 0.001)
		return
	match _blink_state:
		BLINK_IDLE:
			_blink_clock -= delta
			_set_shape_target("Blink", 0.0, 0.001)
			if _blink_clock <= 0.0:
				_blink_state = BLINK_CLOSING
				_blink_elapsed = 0.0
		BLINK_CLOSING:
			_blink_elapsed += delta
			var closing := clampf(_blink_elapsed / maxf(0.001, blink_close_seconds), 0.0, 1.0)
			_set_shape_target("Blink", closing, 0.001)
			if closing >= 1.0:
				_blink_state = BLINK_HOLD
				_blink_elapsed = 0.0
		BLINK_HOLD:
			_blink_elapsed += delta
			_set_shape_target("Blink", 1.0, 0.001)
			if _blink_elapsed >= blink_hold_seconds:
				_blink_state = BLINK_OPENING
				_blink_elapsed = 0.0
		BLINK_OPENING:
			_blink_elapsed += delta
			var opening := 1.0 - clampf(_blink_elapsed / maxf(0.001, blink_open_seconds), 0.0, 1.0)
			_set_shape_target("Blink", opening, 0.001)
			if opening <= 0.0:
				_blink_state = BLINK_IDLE
				_blink_elapsed = 0.0
				_schedule_next_blink()


func _cancel_blink() -> void:
	_blink_state = BLINK_IDLE
	_blink_elapsed = 0.0
	_set_shape_target("Blink", 0.0, 0.001)


func _schedule_next_blink() -> void:
	var low := minf(blink_interval_min_seconds, blink_interval_max_seconds)
	var high := maxf(blink_interval_min_seconds, blink_interval_max_seconds)
	_blink_clock = _rng.randf_range(low, high)


func _set_shape_target(shape_name: String, value: float, transition_seconds: float) -> bool:
	var key := _shape_key(shape_name)
	if not _bindings.has(key):
		return false
	_target_weights[key] = clampf(value, 0.0, 1.0)
	_transition_seconds[key] = maxf(0.001, transition_seconds)
	return true


func _apply_weight(key: String, value: float) -> void:
	var entries: Array = _bindings.get(key, [])
	for entry in entries:
		var mesh_instance: MeshInstance3D = entry["mesh"]
		if is_instance_valid(mesh_instance):
			mesh_instance.set_blend_shape_value(int(entry["index"]), value)


func _collect_nodes(root: Node) -> Array[Node]:
	var nodes: Array[Node] = []
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		nodes.append(node)
		for child in node.get_children():
			stack.append(child)
	return nodes


func _shape_key(shape_name: String) -> String:
	return shape_name.strip_edges().to_lower()


func _face_deformation_enabled() -> bool:
	return integrated_face_morphs_enabled or synthetic_face_overlays_enabled


func _preset_key(expression_name: String) -> String:
	return expression_name.strip_edges().to_lower().replace("-", "_").replace(" ", "_")
