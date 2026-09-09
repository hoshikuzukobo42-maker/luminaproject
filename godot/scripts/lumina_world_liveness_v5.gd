extends Node

const REPORT_SCHEMA := "lumina.world_liveness.validation.v1"
const AUDIO_AIR := "res://assets/world/lumina_observatory_lobby_v1_20260718/audio/observatory_air.wav"
const AUDIO_WIND := "res://assets/world/lumina_observatory_lobby_v1_20260718/audio/bay_wind.wav"
const AUDIO_RAIN := "res://assets/world/lumina_observatory_lobby_v1_20260718/audio/rain_window.wav"

var _runtime_root: Node3D
var _anchors: Dictionary = {}
var _dust_systems: Array[GPUParticles3D] = []
var _rain_system: GPUParticles3D
var _audio_players: Dictionary = {}
var _presence_lights: Array[OmniLight3D] = []
var _mobile: Node3D
var _player: Node3D
var _quality_name := "high"
var _quality_scale := 1.0
var _time_mode := "blue_hour"
var _weather_mode := "clear"
var _time_blend := 0.55
var _target_time_blend := 0.55
var _rain_blend := 0.0
var _target_rain_blend := 0.0
var _transition_seconds := 3.0
var _elapsed := 0.0
var _bootstrapped := false


func _ready() -> void:
	add_to_group("lumina_world_environment")
	set_process(false)
	call_deferred("_bootstrap")


func _bootstrap() -> void:
	for _frame in range(240):
		_scan_world(get_parent())
		if _anchors.size() == 4:
			break
		await get_tree().process_frame

	_runtime_root = Node3D.new()
	_runtime_root.name = "WorldLivenessV5Runtime"
	get_parent().add_child(_runtime_root)

	_configure_quality(OS.get_environment("LUMINA_WORLD_QUALITY"))
	_create_dust_systems()
	_create_rain_system()
	_create_presence_lights()
	_create_spatial_audio()

	var requested_time := OS.get_environment("LUMINA_LIVING_HUB_TIME_MODE")
	var requested_weather := OS.get_environment("LUMINA_LIVING_HUB_WEATHER")
	set_time_mode(requested_time if not requested_time.is_empty() else "blue_hour", 0.01)
	set_weather(requested_weather if not requested_weather.is_empty() else "clear", 0.01)
	_time_blend = _target_time_blend
	_rain_blend = _target_rain_blend
	_bootstrapped = true
	set_process(true)
	_apply_environment_state(0.0)
	_write_validation_report_if_requested()


func _process(delta: float) -> void:
	if not _bootstrapped:
		return
	_elapsed += delta
	var transition_rate := delta / maxf(_transition_seconds, 0.01)
	_time_blend = move_toward(_time_blend, _target_time_blend, transition_rate)
	_rain_blend = move_toward(_rain_blend, _target_rain_blend, transition_rate)
	if _mobile != null and is_instance_valid(_mobile):
		_mobile.rotate_y(delta * 0.035)
	if _player == null or not is_instance_valid(_player):
		_player = _find_player(get_parent())
	_apply_environment_state(delta)


func set_time_mode(mode: String, transition_seconds: float = 3.0) -> void:
	_time_mode = mode.strip_edges().to_lower()
	_transition_seconds = maxf(transition_seconds, 0.01)
	match _time_mode:
		"day", "morning":
			_target_time_blend = 0.08
		"night", "late_night":
			_target_time_blend = 1.0
		_:
			_time_mode = "blue_hour"
			_target_time_blend = 0.55


func set_world_time_mode(mode: String, transition_seconds: float = 3.0) -> void:
	set_time_mode(mode, transition_seconds)


func set_weather(mode: String, transition_seconds: float = 3.0) -> void:
	_weather_mode = mode.strip_edges().to_lower()
	_transition_seconds = maxf(transition_seconds, 0.01)
	_target_rain_blend = 1.0 if _weather_mode in ["rain", "rainy", "storm"] else 0.0
	if _target_rain_blend == 0.0:
		_weather_mode = "clear"
	else:
		_weather_mode = "rain"


func set_world_weather(mode: String, transition_seconds: float = 3.0) -> void:
	set_weather(mode, transition_seconds)


func set_quality_tier(tier: String) -> void:
	_configure_quality(tier)
	for particles in _dust_systems:
		particles.amount = maxi(8, int(round(72.0 * _quality_scale)))
	if _rain_system != null:
		_rain_system.amount = maxi(32, int(round(320.0 * _quality_scale)))


func get_world_state() -> Dictionary:
	return {
		"time_mode": _time_mode,
		"weather": _weather_mode,
		"quality": _quality_name,
		"time_blend": _time_blend,
		"rain_blend": _rain_blend,
	}


func _configure_quality(requested: String) -> void:
	_quality_name = requested.strip_edges().to_lower()
	match _quality_name:
		"low":
			_quality_scale = 0.32
		"medium":
			_quality_scale = 0.62
		"ultra":
			_quality_scale = 1.45
		_:
			_quality_name = "high"
			_quality_scale = 1.0


func _scan_world(node: Node) -> void:
	if node == null:
		return
	if node is Node3D:
		var node_3d := node as Node3D
		var upper_name := String(node_3d.name).to_upper()
		if "ENV_AUDIO_LOBBY_V5" in upper_name:
			_anchors["lobby"] = node_3d
		elif "ENV_AUDIO_PASSAGE_V5" in upper_name:
			_anchors["passage"] = node_3d
		elif "ENV_AUDIO_ROOM_V5" in upper_name:
			_anchors["room"] = node_3d
		elif "ENV_WINDOW_FX_V5" in upper_name:
			_anchors["window"] = node_3d
		elif "KINETIC_MOBILE_V5" in upper_name:
			_mobile = node_3d
	for child in node.get_children():
		if child != self:
			_scan_world(child)


func _create_dust_systems() -> void:
	var dust_texture := _make_radial_texture(32)
	for key in ["lobby", "passage", "room"]:
		if not _anchors.has(key):
			continue
		var extents := Vector3(5.4, 1.9, 5.4)
		if key == "passage":
			extents = Vector3(4.2, 1.4, 2.2)
		var particles := GPUParticles3D.new()
		particles.name = "DustMotes_%s_V5" % key.capitalize()
		particles.amount = maxi(8, int(round(72.0 * _quality_scale)))
		particles.lifetime = 8.0
		particles.preprocess = 8.0
		particles.randomness = 0.85
		particles.visibility_aabb = AABB(-extents, extents * 2.0)

		var process_material := ParticleProcessMaterial.new()
		process_material.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
		process_material.emission_box_extents = extents
		process_material.direction = Vector3.UP
		process_material.spread = 180.0
		process_material.gravity = Vector3(0.0, 0.006, 0.0)
		process_material.initial_velocity_min = 0.008
		process_material.initial_velocity_max = 0.035
		process_material.scale_min = 0.45
		process_material.scale_max = 1.15
		process_material.color = Color(0.95, 0.78, 0.48, 0.24)
		particles.process_material = process_material

		var draw_material := StandardMaterial3D.new()
		draw_material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		draw_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		draw_material.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
		draw_material.vertex_color_use_as_albedo = true
		draw_material.albedo_texture = dust_texture
		draw_material.albedo_color = Color(1.0, 0.88, 0.62, 0.34)
		var quad := QuadMesh.new()
		quad.size = Vector2(0.026, 0.026)
		quad.material = draw_material
		particles.draw_pass_1 = quad

		_runtime_root.add_child(particles)
		particles.global_position = (_anchors[key] as Node3D).global_position
		_dust_systems.append(particles)


func _create_rain_system() -> void:
	if not _anchors.has("window"):
		return
	_rain_system = GPUParticles3D.new()
	_rain_system.name = "WindowRainV5"
	_rain_system.amount = maxi(32, int(round(320.0 * _quality_scale)))
	_rain_system.amount_ratio = 0.0
	_rain_system.lifetime = 1.15
	_rain_system.randomness = 0.72
	_rain_system.visibility_aabb = AABB(Vector3(-0.5, -2.2, -4.2), Vector3(1.0, 4.4, 8.4))

	var process_material := ParticleProcessMaterial.new()
	process_material.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	process_material.emission_box_extents = Vector3(0.18, 1.45, 3.75)
	process_material.direction = Vector3(0.04, -1.0, 0.0)
	process_material.spread = 3.0
	process_material.gravity = Vector3(0.0, -3.4, 0.0)
	process_material.initial_velocity_min = 2.6
	process_material.initial_velocity_max = 4.2
	process_material.scale_min = 0.55
	process_material.scale_max = 1.35
	process_material.color = Color(0.62, 0.78, 0.92, 0.24)
	_rain_system.process_material = process_material

	var draw_material := StandardMaterial3D.new()
	draw_material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	draw_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	draw_material.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	draw_material.vertex_color_use_as_albedo = true
	draw_material.albedo_texture = _make_rain_texture()
	var quad := QuadMesh.new()
	quad.size = Vector2(0.009, 0.16)
	quad.material = draw_material
	_rain_system.draw_pass_1 = quad
	_runtime_root.add_child(_rain_system)
	_rain_system.global_position = (_anchors["window"] as Node3D).global_position


func _create_presence_lights() -> void:
	for key in ["lobby", "room"]:
		if not _anchors.has(key):
			continue
		var light := OmniLight3D.new()
		light.name = "PresenceLight_%s_V5" % key.capitalize()
		light.light_color = Color(1.0, 0.53, 0.25)
		light.light_energy = 0.16
		light.omni_range = 4.8
		light.shadow_enabled = false
		light.distance_fade_enabled = true
		light.distance_fade_begin = 12.0
		light.distance_fade_length = 6.0
		_runtime_root.add_child(light)
		light.global_position = (_anchors[key] as Node3D).global_position + Vector3(0.0, 0.65, 0.0)
		_presence_lights.append(light)


func _create_spatial_audio() -> void:
	_create_audio_player("lobby_air", AUDIO_AIR, "lobby", -31.0, 11.0)
	_create_audio_player("room_air", AUDIO_AIR, "room", -33.0, 10.0)
	_create_audio_player("bay_wind", AUDIO_WIND, "window", -34.0, 13.0)
	_create_audio_player("window_rain", AUDIO_RAIN, "window", -80.0, 14.0)


func _create_audio_player(key: String, path: String, anchor_key: String, volume_db: float, max_distance: float) -> void:
	if not _anchors.has(anchor_key):
		return
	var loaded: AudioStream = AudioStreamWAV.load_from_file(ProjectSettings.globalize_path(path))
	if loaded == null and ResourceLoader.exists(path):
		loaded = load(path) as AudioStream
	if loaded == null:
		return
	var stream := loaded.duplicate()
	if stream is AudioStreamWAV:
		(stream as AudioStreamWAV).loop_mode = AudioStreamWAV.LOOP_FORWARD
	var player := AudioStreamPlayer3D.new()
	player.name = "%s_V5" % key.capitalize()
	player.stream = stream
	player.volume_db = volume_db
	player.max_distance = max_distance
	player.unit_size = 3.0
	player.panning_strength = 0.82
	_runtime_root.add_child(player)
	player.global_position = (_anchors[anchor_key] as Node3D).global_position
	player.play()
	_audio_players[key] = player


func _apply_environment_state(_delta: float) -> void:
	if _rain_system != null:
		_rain_system.amount_ratio = clampf(_rain_blend, 0.0, 1.0)
	for particles in _dust_systems:
		particles.amount_ratio = clampf(1.0 - _rain_blend * 0.45, 0.35, 1.0)

	var rain_audio := _audio_players.get("window_rain") as AudioStreamPlayer3D
	if rain_audio != null:
		rain_audio.volume_db = lerpf(-80.0, -24.0, _rain_blend)
	var wind_audio := _audio_players.get("bay_wind") as AudioStreamPlayer3D
	if wind_audio != null:
		wind_audio.volume_db = lerpf(-35.0, -29.0, _rain_blend)

	for light in _presence_lights:
		var proximity := 0.0
		if _player != null and is_instance_valid(_player):
			var distance := light.global_position.distance_to(_player.global_position)
			proximity = clampf(1.0 - distance / 5.5, 0.0, 1.0) * 0.08
		var breathing := sin(_elapsed * 0.42 + float(light.get_instance_id() % 19)) * 0.012
		light.light_energy = lerpf(0.08, 0.28, _time_blend) + proximity + breathing


func _find_player(node: Node) -> Node3D:
	if node == null:
		return null
	if node is CharacterBody3D:
		return node as Node3D
	var upper_name := String(node.name).to_upper()
	if node is Node3D and ("PLAYER" in upper_name or "WALKER" in upper_name):
		return node as Node3D
	for child in node.get_children():
		var result := _find_player(child)
		if result != null:
			return result
	return null


func _make_radial_texture(size: int) -> ImageTexture:
	var image := Image.create(size, size, false, Image.FORMAT_RGBA8)
	var center := Vector2(float(size - 1), float(size - 1)) * 0.5
	for y in range(size):
		for x in range(size):
			var distance := Vector2(float(x), float(y)).distance_to(center) / maxf(center.x, 1.0)
			var alpha := pow(clampf(1.0 - distance, 0.0, 1.0), 2.4)
			image.set_pixel(x, y, Color(1.0, 1.0, 1.0, alpha))
	return ImageTexture.create_from_image(image)


func _make_rain_texture() -> ImageTexture:
	var width := 8
	var height := 64
	var image := Image.create(width, height, false, Image.FORMAT_RGBA8)
	for y in range(height):
		var vertical := sin(PI * float(y) / float(height - 1))
		for x in range(width):
			var horizontal: float = 1.0 - absf(float(x) - 3.5) / 4.0
			var alpha := pow(clampf(horizontal, 0.0, 1.0), 2.0) * vertical * 0.42
			image.set_pixel(x, y, Color(0.72, 0.86, 1.0, alpha))
	return ImageTexture.create_from_image(image)


func _write_validation_report_if_requested() -> void:
	if OS.get_environment("LUMINA_WORLD_LIVENESS_VALIDATE") != "1":
		return
	var checks := {
		"anchor_contract": _anchors.size() == 4,
		"kinetic_mobile": _mobile != null,
		"dust_zones": _dust_systems.size() == 3,
		"rain_system": _rain_system != null,
		"spatial_audio": _audio_players.size() == 4,
		"presence_lights": _presence_lights.size() == 2,
		"quality_valid": _quality_name in ["low", "medium", "high", "ultra"],
	}
	var ok := true
	for value in checks.values():
		ok = ok and bool(value)
	var report := {
		"schema": REPORT_SCHEMA,
		"ok": ok,
		"checks": checks,
		"anchors": _anchors.keys(),
		"quality": _quality_name,
		"time_mode": _time_mode,
		"weather": _weather_mode,
		"audio_players": _audio_players.keys(),
	}
	var report_path := OS.get_environment("LUMINA_WORLD_LIVENESS_REPORT_PATH")
	if not report_path.is_empty():
		var file := FileAccess.open(report_path, FileAccess.WRITE)
		if file != null:
			file.store_string(JSON.stringify(report, "\t") + "\n")
	print("lumina_world_liveness_validation=", JSON.stringify(report))
