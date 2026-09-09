extends Node3D

const MANIFEST_PATH := "res://assets/avatars/lumina_meshi_biped/animations/lumina_animation_manifest.json"
const AVATAR_ROOT_PATH := "res://assets/avatars/lumina_meshi_biped/animations_fixed/"
const MATERIAL_MODE_IMPORTED := 0
const MATERIAL_MODE_FLAT_TEXTURE := 1
const MATERIAL_MODE_DEBUG := 2
const ALPHA_CUTOFF_VALUES := [0.45, 0.55, 0.65, 0.75]

@export var auto_advance_seconds: float = 5.0
@export var avatar_scale: float = 1.0
@export var target_avatar_height: float = 1.6
@export var camera_distance: float = 8.0
@export var camera_height: float = 2.2
@export var use_debug_material: bool = false
@export var auto_play_animation: bool = true
@export var disable_skin_for_debug: bool = false

var _manifest: Dictionary = {}
var _states: Array[String] = []
var _index: int = 0
var _avatar: Node3D = null
var _animation_player: AnimationPlayer = null
var _auto_play: bool = false
var _auto_timer: float = 0.0
var _material_mode: int = MATERIAL_MODE_IMPORTED
var _alpha_cutoff_index: int = 1
var _info_label: Label
var _status_label: Label


func _ready() -> void:
	if use_debug_material:
		_material_mode = MATERIAL_MODE_DEBUG
	_load_manifest()
	_build_runtime_ui()
	_setup_camera()
	if _states.is_empty():
		_set_status("No states found in manifest.")
		return
	_show_state(0)


func _process(delta: float) -> void:
	if _auto_play and not _states.is_empty():
		_auto_timer += delta
		if _auto_timer >= auto_advance_seconds:
			_auto_timer = 0.0
			_show_state((_index + 1) % _states.size())


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		match event.keycode:
			KEY_RIGHT:
				_show_state((_index + 1) % _states.size())
			KEY_LEFT:
				_show_state((_index - 1 + _states.size()) % _states.size())
			KEY_SPACE:
				_replay_current()
			KEY_A:
				_auto_play = not _auto_play
				_auto_timer = 0.0
				_update_info()
			KEY_R:
				_rotate_avatar(45.0)
			KEY_L:
				_rotate_avatar(-45.0)
			KEY_M:
				_cycle_material_mode()
			KEY_C:
				_cycle_alpha_cutoff()
			KEY_ESCAPE:
				get_tree().quit()


func _load_manifest() -> void:
	var file := FileAccess.open(MANIFEST_PATH, FileAccess.READ)
	if file == null:
		_manifest = {}
		_states = []
		return
	var text := file.get_as_text()
	var parsed = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		_manifest = {}
		_states = []
		return
	_manifest = parsed
	var states_dict: Dictionary = _manifest.get("states", {})
	var preferred_order := [
		"idle",
		"talk_standing",
		"walk",
		"run_long",
		"run_finish",
		"catching_breath",
		"turn_right",
		"turn_right_idle_style",
		"sit_from_behind",
		"sit_from_approach",
		"sit_idle",
		"sit_doze_off",
		"stand_up_primary",
		"stand_up_fallback",
		"sit_cross_legged"
	]
	_states = []
	for state_name in preferred_order:
		if states_dict.has(state_name):
			var entry: Dictionary = states_dict[state_name]
			if _get_entry_file_name(entry) != "":
				_states.append(state_name)
	for key in states_dict.keys():
		var state_name := str(key)
		if _states.has(state_name):
			continue
		var entry: Dictionary = states_dict[state_name]
		if _get_entry_file_name(entry) != "":
			_states.append(state_name)


func _show_state(next_index: int) -> void:
	if _states.is_empty():
		return
	_index = clampi(next_index, 0, _states.size() - 1)
	_auto_timer = 0.0
	_clear_avatar()
	var state_name := _states[_index]
	var states_dict: Dictionary = _manifest.get("states", {})
	var entry: Dictionary = states_dict.get(state_name, {})
	var file_name := _get_entry_file_name(entry)
	var animation_name := String(entry.get("animation", ""))
	if file_name == "":
		_set_status("State has no file: %s" % state_name)
		_update_info()
		return
	var scene_path := AVATAR_ROOT_PATH + file_name
	var packed := ResourceLoader.load(scene_path)
	if packed == null or not (packed is PackedScene):
		_set_status("Failed to load: %s" % scene_path)
		_update_info()
		return
	_avatar = (packed as PackedScene).instantiate() as Node3D
	if _avatar == null:
		_set_status("Loaded scene is not Node3D: %s" % file_name)
		_update_info()
		return
	_avatar.name = "PreviewAvatar_%s" % state_name
	_avatar.scale = Vector3.ONE * avatar_scale
	_avatar.rotation_degrees = Vector3.ZERO
	add_child(_avatar)
	if disable_skin_for_debug:
		_disable_skin_recursive(_avatar)
	_fit_avatar_to_preview()
	_apply_current_material_mode()
	_animation_player = _find_animation_player(_avatar)
	if _animation_player == null:
		_set_status("No AnimationPlayer found: %s" % file_name)
		_update_info()
		return
	var playable_name := _choose_animation(animation_name)
	if playable_name == "":
		_set_status("AnimationPlayer has no playable animations: %s" % file_name)
		_update_info()
		return
	if auto_play_animation:
		_animation_player.play(playable_name)
		_set_status("Playing: %s" % playable_name)
	else:
		_animation_player.stop()
		_set_status("Loaded static pose. Animation available: %s" % playable_name)
	_update_info()


func _clear_avatar() -> void:
	if _avatar != null and is_instance_valid(_avatar):
		_avatar.queue_free()
	_avatar = null
	_animation_player = null


func _find_animation_player(node: Node) -> AnimationPlayer:
	if node is AnimationPlayer:
		return node as AnimationPlayer
	for child in node.get_children():
		var found := _find_animation_player(child)
		if found != null:
			return found
	return null


func _choose_animation(preferred_name: String) -> String:
	if _animation_player == null:
		return ""
	var names := _animation_player.get_animation_list()
	if names.is_empty():
		return ""
	if preferred_name != "" and names.has(preferred_name):
		return preferred_name
	return String(names[0])


func _replay_current() -> void:
	if _animation_player == null:
		return
	var current := _animation_player.current_animation
	if current == "":
		current = _choose_animation("")
	if current != "":
		_animation_player.stop()
		_animation_player.play(current)
		_set_status("Replayed: %s" % current)


func _rotate_avatar(degrees: float) -> void:
	if _avatar == null:
		return
	_avatar.rotate_y(deg_to_rad(degrees))
	_set_status("Rotated avatar by %.1f degrees" % degrees)


func _build_runtime_ui() -> void:
	var canvas := CanvasLayer.new()
	canvas.name = "PreviewUI"
	add_child(canvas)

	var panel := PanelContainer.new()
	panel.name = "InfoPanel"
	panel.set_anchors_preset(Control.PRESET_TOP_LEFT)
	panel.position = Vector2(16, 16)
	panel.custom_minimum_size = Vector2(560, 120)
	canvas.add_child(panel)

	var box := VBoxContainer.new()
	panel.add_child(box)

	_info_label = Label.new()
	_info_label.name = "InfoLabel"
	_info_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_info_label)

	_status_label = Label.new()
	_status_label.name = "StatusLabel"
	_status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_status_label)

	var help := Label.new()
	help.text = "操作: ←/→ 切替  Space 再生  A 自動巡回  R/L 回転  M 材質切替  C alpha比較  Esc 終了"
	box.add_child(help)


func _setup_camera() -> void:
	var camera := get_node_or_null("Camera3D") as Camera3D
	if camera:
		camera.projection = Camera3D.PROJECTION_ORTHOGONAL
		camera.size = 4.0
		camera.position = Vector3(0, camera_height, camera_distance)
		camera.look_at(Vector3(0, 1.1, 0), Vector3.UP)
		camera.near = 0.01
		camera.far = 200.0
		camera.current = true


func _fit_avatar_to_preview() -> void:
	if _avatar == null:
		return
	var bounds := _get_visual_bounds(_avatar)
	if not bool(bounds.get("valid", false)):
		_set_status("Loaded avatar, but no visible MeshInstance3D was found.")
		return
	var min_v: Vector3 = bounds["min"]
	var max_v: Vector3 = bounds["max"]
	var size := max_v - min_v
	if size.y > 0.001:
		var fit_scale := clampf(target_avatar_height / size.y, 0.001, 100.0)
		_avatar.scale *= fit_scale
	bounds = _get_visual_bounds(_avatar)
	if not bool(bounds.get("valid", false)):
		return
	min_v = bounds["min"]
	max_v = bounds["max"]
	var center := (min_v + max_v) * 0.5
	_avatar.global_position += Vector3(-center.x, -min_v.y, -center.z)
	_focus_camera_on_avatar()


func _focus_camera_on_avatar() -> void:
	var camera := get_node_or_null("Camera3D") as Camera3D
	if camera == null or _avatar == null:
		return
	var bounds := _get_visual_bounds(_avatar)
	if not bool(bounds.get("valid", false)):
		return
	var min_v: Vector3 = bounds["min"]
	var max_v: Vector3 = bounds["max"]
	var center := (min_v + max_v) * 0.5
	var size := max_v - min_v
	var height: float = maxf(size.y, 1.0)
	var width: float = maxf(size.x, size.z)
	var distance: float = maxf(camera_distance, maxf(width, height) * 3.0 + maxf(size.z, 1.0) * 2.0)
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.size = maxf(4.0, maxf(height * 2.4, width * 2.4))
	camera.position = Vector3(center.x + distance * 0.42, center.y + height * 1.25, center.z + distance)
	camera.look_at(Vector3(center.x, center.y + height * 0.45, center.z), Vector3.UP)
	camera.current = true
	print("Avatar bounds min=%s max=%s size=%s camera_size=%.3f distance=%.3f" % [
		str(min_v),
		str(max_v),
		str(size),
		camera.size,
		distance
	])


func _get_visual_bounds(root: Node) -> Dictionary:
	var result := {
		"valid": false,
		"min": Vector3.ZERO,
		"max": Vector3.ZERO
	}
	_collect_visual_bounds(root, result)
	return result


func _collect_visual_bounds(node: Node, result: Dictionary) -> void:
	if node is MeshInstance3D:
		var mesh_node := node as MeshInstance3D
		var aabb := mesh_node.get_aabb()
		if aabb.size.length_squared() > 0.0:
			var p := aabb.position
			var s := aabb.size
			var corners := [
				p,
				p + Vector3(s.x, 0, 0),
				p + Vector3(0, s.y, 0),
				p + Vector3(0, 0, s.z),
				p + Vector3(s.x, s.y, 0),
				p + Vector3(s.x, 0, s.z),
				p + Vector3(0, s.y, s.z),
				p + s
			]
			for corner in corners:
				var world_point: Vector3 = mesh_node.global_transform * corner
				if not bool(result.get("valid", false)):
					result["valid"] = true
					result["min"] = world_point
					result["max"] = world_point
				else:
					result["min"] = (result["min"] as Vector3).min(world_point)
					result["max"] = (result["max"] as Vector3).max(world_point)
	for child in node.get_children():
		_collect_visual_bounds(child, result)


func _apply_debug_material(root: Node) -> void:
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.92, 0.88, 0.8, 1.0)
	material.roughness = 0.75
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_apply_material_recursive(root, material)


func _apply_flat_texture_material(root: Node) -> void:
	var texture := _load_current_preview_texture()
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.82, 0.82, 0.82, 1.0)
	material.roughness = 1.0
	material.metallic = 0.0
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
	material.alpha_scissor_threshold = _get_current_alpha_cutoff()
	if texture != null:
		material.albedo_texture = texture
	_apply_material_recursive(root, material)


func _apply_current_material_mode() -> void:
	if _avatar == null:
		return
	_clear_material_override_recursive(_avatar)
	match _material_mode:
		MATERIAL_MODE_FLAT_TEXTURE:
			_apply_flat_texture_material(_avatar)
		MATERIAL_MODE_DEBUG:
			_apply_debug_material(_avatar)


func _cycle_material_mode() -> void:
	_material_mode = (_material_mode + 1) % 3
	_apply_current_material_mode()
	_set_status("Material mode: %s" % _get_material_mode_name(_material_mode))
	_update_info()


func _cycle_alpha_cutoff() -> void:
	_alpha_cutoff_index = (_alpha_cutoff_index + 1) % ALPHA_CUTOFF_VALUES.size()
	_material_mode = MATERIAL_MODE_FLAT_TEXTURE
	_apply_current_material_mode()
	_set_status("Alpha cutoff compare: %.2f" % _get_current_alpha_cutoff())
	_update_info()


func _get_current_alpha_cutoff() -> float:
	return float(ALPHA_CUTOFF_VALUES[_alpha_cutoff_index])


func _get_material_mode_name(mode: int) -> String:
	match mode:
		MATERIAL_MODE_IMPORTED:
			return "補正済みGLB材質"
		MATERIAL_MODE_FLAT_TEXTURE:
			return "白飛び確認用の弱め材質"
		MATERIAL_MODE_DEBUG:
			return "単色シルエット確認"
	return "unknown"


func _load_current_preview_texture() -> Texture2D:
	if _states.is_empty():
		return null
	var state_name := _states[_index]
	var states_dict: Dictionary = _manifest.get("states", {})
	var entry: Dictionary = states_dict.get(state_name, {})
	var file_name := _get_entry_file_name(entry)
	if file_name == "":
		return null
	var texture_path := AVATAR_ROOT_PATH + file_name.trim_suffix(".glb") + "_texture_0.png"
	if ResourceLoader.exists(texture_path):
		var texture := ResourceLoader.load(texture_path)
		if texture is Texture2D:
			return texture as Texture2D
	return null


func _apply_material_recursive(node: Node, material: Material) -> void:
	if node is MeshInstance3D:
		(node as MeshInstance3D).material_override = material
	for child in node.get_children():
		_apply_material_recursive(child, material)


func _clear_material_override_recursive(node: Node) -> void:
	if node is MeshInstance3D:
		(node as MeshInstance3D).material_override = null
	for child in node.get_children():
		_clear_material_override_recursive(child)


func _disable_skin_recursive(node: Node) -> void:
	if node is MeshInstance3D:
		var mesh_node := node as MeshInstance3D
		mesh_node.skeleton = NodePath("")
		mesh_node.skin = null
	for child in node.get_children():
		_disable_skin_recursive(child)


func _update_info() -> void:
	if _info_label == null:
		return
	var state_name := "none"
	var entry: Dictionary = {}
	if not _states.is_empty():
		state_name = _states[_index]
		entry = Dictionary(_manifest.get("states", {})).get(state_name, {})
	var file_name := _get_entry_file_name(entry)
	var loop_value := str(entry.get("loop", "unknown"))
	var in_place_value := str(entry.get("in_place", false))
	_info_label.text = "State %d/%d: %s\nfile: %s\nloop: %s  in_place: %s  auto: %s" % [
		_index + 1,
		_states.size(),
		state_name,
		file_name,
		loop_value,
		in_place_value,
		str(_auto_play)
	]
	_info_label.text += "\nmaterial: %s  alpha_cutoff: %.2f" % [
		_get_material_mode_name(_material_mode),
		_get_current_alpha_cutoff()
	]


func _set_status(text: String) -> void:
	if _status_label != null:
		_status_label.text = text
	print(text)


func _get_entry_file_name(entry: Dictionary) -> String:
	var file_value = entry.get("file", "")
	if typeof(file_value) == TYPE_STRING:
		return file_value
	if typeof(file_value) == TYPE_STRING_NAME:
		return str(file_value)
	return ""
