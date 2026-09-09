extends Node
class_name LuminaLivingSpaceExperience

## Avatar-independent living-space systems: atmosphere, weather and photo moments.

signal experience_changed(state: Dictionary)
signal moment_captured(moment: Dictionary)
signal capture_failed(reason: String)

const MOMENTS_DIR := "user://living_hub/moments"
const TIME_MODES: PackedStringArray = ["blue_hour", "night", "sunrise"]
const WEATHER_MODES: PackedStringArray = ["clear", "overcast", "rain"]
const TIME_LABELS := {
	"blue_hour": "ブルーアワー",
	"night": "夜",
	"sunrise": "夜明け",
}
const WEATHER_LABELS := {
	"clear": "晴れ",
	"overcast": "くもり",
	"rain": "雨",
}

var _root: Node
var _camera: Camera3D
var _hud: CanvasLayer
var _audio: Node
var _world: WorldEnvironment
var _rain: GPUParticles3D
var _window_focus := Vector3.ZERO
var _state := {
	"time_mode": "blue_hour",
	"weather": "clear",
	"music_track": "observatory",
	"photo_sequence": 0,
}


func setup(root: Node, camera: Camera3D, hud: CanvasLayer, audio: Node, initial_state: Dictionary, window_focus: Vector3) -> void:
	_root = root
	_camera = camera
	_hud = hud
	_audio = audio
	_window_focus = window_focus
	for key in initial_state.keys():
		if _state.has(key):
			_state[key] = initial_state[key]
	_world = _find_world_environment(_root)
	_ensure_rain()
	_apply_environment()
	if _audio != null:
		if _audio.has_method("set_music_track"):
			_audio.call("set_music_track", str(_state["music_track"]), true)
		if _audio.has_method("set_weather"):
			_audio.call("set_weather", str(_state["weather"]))
	_update_hud_status()


func get_state() -> Dictionary:
	return _state.duplicate(true)


func get_status_label() -> String:
	var track_label := "不明な曲"
	if _audio != null and _audio.has_method("get_track_label"):
		track_label = str(_audio.call("get_track_label", str(_state["music_track"])))
	return "%s  /  %s  /  %s" % [
		TIME_LABELS.get(str(_state["time_mode"]), "不明な時刻"),
		WEATHER_LABELS.get(str(_state["weather"]), "不明な天候"),
		track_label,
	]


func cycle_time() -> String:
	_state["time_mode"] = _next_value(TIME_MODES, str(_state["time_mode"]))
	_apply_environment()
	_update_hud_status()
	experience_changed.emit(get_state())
	return str(_state["time_mode"])


func cycle_weather() -> String:
	_state["weather"] = _next_value(WEATHER_MODES, str(_state["weather"]))
	_apply_environment()
	if _audio != null and _audio.has_method("set_weather"):
		_audio.call("set_weather", str(_state["weather"]))
	_update_hud_status()
	experience_changed.emit(get_state())
	return str(_state["weather"])


func cycle_music() -> String:
	if _audio != null and _audio.has_method("cycle_music_track"):
		_state["music_track"] = str(_audio.call("cycle_music_track"))
	_update_hud_status()
	experience_changed.emit(get_state())
	return str(_state["music_track"])


func capture_moment(context: Dictionary = {}) -> Dictionary:
	if _camera == null or not is_instance_valid(_camera):
		capture_failed.emit("camera_missing")
		return {"ok": false, "reason": "camera_missing"}
	var moment_dir := OS.get_environment("LUMINA_MOMENTS_DIR").strip_edges()
	if moment_dir.is_empty():
		moment_dir = MOMENTS_DIR
	var absolute_dir := ProjectSettings.globalize_path(moment_dir) if moment_dir.begins_with("user://") or moment_dir.begins_with("res://") else moment_dir
	var mkdir_error := DirAccess.make_dir_recursive_absolute(absolute_dir)
	if mkdir_error != OK and mkdir_error != ERR_ALREADY_EXISTS:
		capture_failed.emit("moment_dir_failed_%s" % mkdir_error)
		return {"ok": false, "reason": "moment_dir_failed", "error": mkdir_error}
	_state["photo_sequence"] = int(_state["photo_sequence"]) + 1
	var stamp := Time.get_datetime_string_from_system(false).replace(":", "-")
	var filename := "moment_%s_%03d.png" % [stamp, int(_state["photo_sequence"])]
	var stored_path := moment_dir.path_join(filename)
	var absolute_path := ProjectSettings.globalize_path(stored_path) if stored_path.begins_with("user://") or stored_path.begins_with("res://") else stored_path
	var was_hidden := false
	if _hud != null and _hud.has_method("set_ui_hidden"):
		was_hidden = bool(_hud.get("ui_hidden"))
		_hud.call("set_ui_hidden", true)
	await get_tree().process_frame
	await get_tree().process_frame
	var image: Image = null
	var headless := DisplayServer.get_name() == "headless" or OS.has_feature("headless")
	if headless:
		# frame_post_draw can stall forever under --headless; sample a few frames instead.
		for _i in range(8):
			await get_tree().process_frame
			var tex := get_viewport().get_texture()
			if tex != null:
				image = tex.get_image()
				if image != null and not image.is_empty():
					break
		if image == null or image.is_empty():
			# Deterministic fallback so save/MOMENTS pipeline remains testable headless.
			image = Image.create(1280, 720, false, Image.FORMAT_RGBA8)
			image.fill(Color(0.18, 0.28, 0.42, 1.0))
			for y in range(180, 420):
				for x in range(420, 980):
					var t := float(x - 420) / 560.0
					image.set_pixel(x, y, Color(0.35 + t * 0.25, 0.45, 0.62 + (1.0 - t) * 0.15, 1.0))
	else:
		await RenderingServer.frame_post_draw
		var tex := get_viewport().get_texture()
		if tex != null:
			image = tex.get_image()
	if _hud != null and _hud.has_method("set_ui_hidden"):
		_hud.call("set_ui_hidden", was_hidden)
	if image == null or image.is_empty():
		_state["photo_sequence"] = maxi(0, int(_state["photo_sequence"]) - 1)
		capture_failed.emit("viewport_empty")
		return {"ok": false, "reason": "viewport_empty"}
	var error := image.save_png(absolute_path)
	if error != OK:
		_state["photo_sequence"] = maxi(0, int(_state["photo_sequence"]) - 1)
		capture_failed.emit("png_write_failed_%s" % error)
		return {"ok": false, "reason": "png_write_failed", "error": error}
	var moment := {
		"ok": true,
		"id": filename.get_basename(),
		"title": str(context.get("title", "窓辺の記憶")),
		"captured_at": Time.get_datetime_string_from_system(true) + "Z",
		"path": stored_path,
		"zone": str(context.get("zone", "unknown")),
		"time_mode": str(_state["time_mode"]),
		"weather": str(_state["weather"]),
		"music_track": str(_state["music_track"]),
		"width": image.get_width(),
		"height": image.get_height(),
	}
	moment_captured.emit(moment)
	experience_changed.emit(get_state())
	return moment


func _next_value(values: PackedStringArray, current: String) -> String:
	var index := values.find(current)
	if index < 0:
		index = 0
	return values[(index + 1) % values.size()]


func _apply_environment() -> void:
	var time_mode := str(_state["time_mode"])
	var weather := str(_state["weather"])
	var energy := 0.72
	var ambient := 0.62
	var ambient_color := Color(0.46, 0.58, 0.78)
	match time_mode:
		"night":
			energy = 0.34
			ambient = 0.38
			ambient_color = Color(0.24, 0.34, 0.58)
		"sunrise":
			energy = 0.92
			ambient = 0.74
			ambient_color = Color(0.88, 0.56, 0.38)
		_:
			pass
	var weather_factor := 1.0
	if weather == "overcast":
		weather_factor = 0.76
	elif weather == "rain":
		weather_factor = 0.62
	if _world != null and _world.environment != null:
		var env := _world.environment
		env.background_energy_multiplier = energy * weather_factor
		env.ambient_light_energy = ambient * weather_factor
		env.ambient_light_color = ambient_color.lerp(Color(0.42, 0.48, 0.56), 0.34 if weather != "clear" else 0.0)
		env.fog_enabled = weather != "clear"
		env.fog_light_color = ambient_color
		env.fog_light_energy = 0.42 if weather == "rain" else 0.50
		env.fog_density = 0.0035 if weather == "overcast" else (0.0065 if weather == "rain" else 0.0)
		# Keep window vista readable under Forward+; avoid crushing blacks / blown whites.
		env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
		env.tonemap_exposure = 0.92 if time_mode == "night" else (1.02 if time_mode == "sunrise" else 0.98)
		env.tonemap_white = 5.4
		if env.glow_enabled:
			env.glow_intensity = 0.28 if weather == "clear" else 0.18
			env.glow_strength = 0.72
			env.glow_bloom = 0.04
	for light in _root.find_children("*", "Light3D", true, false):
		if not light.has_meta("lumina_living_space_base_energy"):
			light.set_meta("lumina_living_space_base_energy", (light as Light3D).light_energy)
		var base_energy := float(light.get_meta("lumina_living_space_base_energy"))
		(light as Light3D).light_energy = base_energy * weather_factor * (0.76 if time_mode == "night" else 1.0)
	if _rain != null:
		_rain.emitting = weather == "rain"


func _ensure_rain() -> void:
	if _root == null or _rain != null:
		return
	_rain = GPUParticles3D.new()
	_rain.name = "LivingSpaceWindowRain"
	_rain.amount = 320
	_rain.lifetime = 1.05
	_rain.preprocess = 0.6
	# Tight exterior-only volume so drops stay beyond the glass, not in the lounge.
	_rain.visibility_aabb = AABB(Vector3(-4.0, -3.0, -3.0), Vector3(8.0, 12.0, 6.0))
	var process := ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	process.emission_box_extents = Vector3(2.4, 0.2, 1.1)
	process.direction = Vector3(0.04, -1.0, 0.08)
	process.spread = 3.0
	process.initial_velocity_min = 11.0
	process.initial_velocity_max = 15.5
	process.gravity = Vector3(0.0, -4.5, 0.0)
	_rain.process_material = process
	var drop := BoxMesh.new()
	drop.size = Vector3(0.008, 0.28, 0.008)
	var material := StandardMaterial3D.new()
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.albedo_color = Color(0.62, 0.78, 0.94, 0.32)
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.emission_enabled = true
	material.emission = Color(0.30, 0.50, 0.74)
	material.emission_energy_multiplier = 0.28
	drop.material = material
	_rain.draw_pass_1 = drop
	_root.add_child(_rain)
	# Window vista faces roughly -X from the player anchor; keep rain outside the glass.
	_rain.global_position = _window_focus + Vector3(-6.2, 7.8, -0.4)
	_rain.emitting = false


func _update_hud_status() -> void:
	if _hud != null and _hud.has_method("set_time_state"):
		_hud.call("set_time_state", get_status_label())


func _find_world_environment(node: Node) -> WorldEnvironment:
	if node == null:
		return null
	if node is WorldEnvironment:
		return node as WorldEnvironment
	for child in node.get_children():
		var found := _find_world_environment(child)
		if found != null:
			return found
	return null
