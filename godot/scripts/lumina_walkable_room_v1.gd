extends Node3D

const GLB_PATH := "res://assets/world/lumina_walkable_room_v1_20260718/export/lumina_walkable_room_v1.glb"
const CONTRACT_PATH := "res://assets/world/lumina_walkable_room_v1_20260718/room_contract.json"
const PlayerController = preload("res://scripts/lumina_walkable_player_controller.gd")

var _contract: Dictionary = {}
var _visual_root: Node3D
var _player: CharacterBody3D
var _camera: Camera3D
var _anchor_nodes: Array[Node3D] = []
var _status_label: Label
var _nearest_label: Label
var _loaded_ok := false


func _ready() -> void:
	_contract = _load_contract()
	_setup_environment()
	_visual_root = _load_visual_glb()
	_build_collisions()
	_build_anchors()
	_set_anchor_markers_visible(false)
	_create_player()
	_create_hud()
	_polish_hud()
	_print_summary()
	var capture_path := OS.get_environment("LUMINA_WALKABLE_ROOM_CAPTURE_PATH").strip_edges()
	if capture_path != "":
		_capture_and_quit.call_deferred(capture_path)
	elif _env_enabled("LUMINA_WALKABLE_ROOM_VALIDATE"):
		_run_validation_and_quit.call_deferred()
	elif _player != null and _env_enabled("LUMINA_WALKABLE_MOUSE_CAPTURE"):
		_player.call_deferred("set_mouse_captured", true)


func _process(_delta: float) -> void:
	if _player == null or _nearest_label == null:
		return
	var nearest := _nearest_anchor()
	if nearest.is_empty():
		_nearest_label.text = "自由に歩けます"
		return
	_nearest_label.text = "近く: %s  [Eで調べる]" % str(nearest.get("label", "地点"))


func _load_contract() -> Dictionary:
	var file := FileAccess.open(CONTRACT_PATH, FileAccess.READ)
	if file == null:
		push_error("Walkable room contract could not be opened: %s" % CONTRACT_PATH)
		return {}
	var parsed: Variant = JSON.parse_string(file.get_as_text())
	if not (parsed is Dictionary):
		push_error("Walkable room contract is not a dictionary: %s" % CONTRACT_PATH)
		return {}
	return parsed


func _load_visual_glb() -> Node3D:
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	var err := document.append_from_file(GLB_PATH, state)
	if err != OK:
		push_error("Walkable room GLB load failed: %s err=%s" % [GLB_PATH, err])
		return null
	var generated := document.generate_scene(state) as Node3D
	if generated == null:
		push_error("Walkable room GLB generated scene is null")
		return null
	generated.name = "LuminaWalkableRoomV1Visual"
	# Blender +Y is imported as Godot -Z. Mirror once so the authored front,
	# collision contract, spawn, anchors, and acceptance route share coordinates.
	generated.scale.z = -1.0
	add_child(generated)
	_loaded_ok = true
	print("LUMINA_WALKABLE_ROOM_GLB_LOADED path=", GLB_PATH, " children=", generated.get_child_count())
	return generated


func _setup_environment() -> void:
	var world_environment := WorldEnvironment.new()
	world_environment.name = "WalkableRoomEnvironment"
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color(0.018, 0.028, 0.052, 1.0)
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color(0.42, 0.53, 0.72, 1.0)
	environment.ambient_light_energy = 0.40
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	world_environment.environment = environment
	add_child(world_environment)

	var key := OmniLight3D.new()
	key.name = "WarmEntryKey"
	key.position = Vector3(0.0, 2.75, -2.6)
	key.light_color = Color(1.0, 0.70, 0.48, 1.0)
	key.light_energy = 1.55
	key.omni_range = 8.0
	key.shadow_enabled = true
	add_child(key)

	var fill := OmniLight3D.new()
	fill.name = "CoolWindowFill"
	fill.position = Vector3(0.0, 2.45, 4.1)
	fill.light_color = Color(0.40, 0.68, 1.0, 1.0)
	fill.light_energy = 1.15
	fill.omni_range = 7.0
	add_child(fill)

	var reading := OmniLight3D.new()
	reading.name = "ReadingNookLight"
	reading.position = Vector3(-5.4, 2.0, -0.6)
	reading.light_color = Color(1.0, 0.57, 0.32, 1.0)
	reading.light_energy = 0.85
	reading.omni_range = 4.0
	add_child(reading)

	var studio := OmniLight3D.new()
	studio.name = "StudioLight"
	studio.position = Vector3(5.3, 2.0, -0.6)
	studio.light_color = Color(0.25, 0.82, 1.0, 1.0)
	studio.light_energy = 1.15
	studio.omni_range = 4.0
	add_child(studio)


func _build_collisions() -> void:
	for item in _contract.get("colliders", []):
		if not (item is Dictionary):
			continue
		var body := StaticBody3D.new()
		body.name = "WRV1_Collision_%s" % str(item.get("name", "Box"))
		body.position = _vec3(item.get("position", [0, 0, 0]))
		var shape_node := CollisionShape3D.new()
		var shape := BoxShape3D.new()
		shape.size = _vec3(item.get("size", [1, 1, 1]))
		shape_node.shape = shape
		body.add_child(shape_node)
		add_child(body)


func _build_anchors() -> void:
	for item in _contract.get("anchors", []):
		if not (item is Dictionary):
			continue
		var anchor := Node3D.new()
		anchor.name = "WRV1_Anchor_%s" % str(item.get("id", "point"))
		anchor.position = _vec3(item.get("position", [0, 0, 0]))
		anchor.set_meta("anchor_id", str(item.get("id", "")))
		anchor.set_meta("label", str(item.get("label", "地点")))
		anchor.set_meta("radius", float(item.get("radius", 1.2)))
		anchor.add_to_group("lumina_room_anchor")
		add_child(anchor)
		_anchor_nodes.append(anchor)
		if str(item.get("id", "")) != "spawn":
			_add_anchor_marker(anchor, str(item.get("id", "")))


func _add_anchor_marker(anchor: Node3D, anchor_id: String) -> void:
	var marker := MeshInstance3D.new()
	marker.name = "Marker"
	var mesh := CylinderMesh.new()
	mesh.top_radius = 0.24
	mesh.bottom_radius = 0.24
	mesh.height = 0.025
	mesh.radial_segments = 32
	marker.mesh = mesh
	marker.position.y = 0.022
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.18, 0.80, 0.92, 0.48) if anchor_id in ["studio", "window"] else Color(1.0, 0.50, 0.20, 0.48)
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.emission_enabled = true
	material.emission = material.albedo_color
	material.emission_energy_multiplier = 0.55
	marker.material_override = material
	anchor.add_child(marker)


func _create_player() -> void:
	var room_config: Dictionary = _contract.get("room", {})
	_player = CharacterBody3D.new()
	_player.name = "WalkableUserPlayer"
	_player.set_script(PlayerController)
	var collision := CollisionShape3D.new()
	collision.name = "PlayerCapsule"
	var capsule := CapsuleShape3D.new()
	capsule.radius = float(room_config.get("player_radius", 0.32))
	capsule.height = float(room_config.get("player_height", 1.72))
	collision.shape = capsule
	collision.position.y = float(room_config.get("player_height", 1.72)) * 0.5
	_player.add_child(collision)

	var pivot := Node3D.new()
	pivot.name = "CameraPivot"
	pivot.position.y = float(room_config.get("eye_height", 1.60))
	_player.add_child(pivot)
	_camera = Camera3D.new()
	_camera.name = "FirstPersonCamera"
	_camera.current = true
	_camera.fov = 68.0
	_camera.near = 0.05
	pivot.add_child(_camera)

	add_child(_player)
	_player.call("configure", room_config)
	_player.connect("interaction_requested", _on_interaction_requested)


func _create_hud() -> void:
	if DisplayServer.get_name().to_lower() == "headless":
		return
	var layer := CanvasLayer.new()
	layer.name = "WalkableRoomHUD"
	add_child(layer)
	var panel := ColorRect.new()
	panel.position = Vector2(22, 22)
	panel.size = Vector2(430, 112)
	panel.color = Color(0.025, 0.045, 0.075, 0.86)
	layer.add_child(panel)
	var title := Label.new()
	title.position = Vector2(18, 12)
	title.text = "Lumina クリエイティブ観測所"
	title.add_theme_font_size_override("font_size", 20)
	panel.add_child(title)
	_status_label = Label.new()
	_status_label.position = Vector2(18, 43)
	_status_label.text = "WASD: 移動  Shift: 走る  左クリック: 視点  E: 調べる"
	panel.add_child(_status_label)
	_nearest_label = Label.new()
	_nearest_label.position = Vector2(18, 72)
	_nearest_label.text = "自由に歩けます"
	_nearest_label.modulate = Color(0.53, 0.89, 1.0, 1.0)
	panel.add_child(_nearest_label)


func _nearest_anchor() -> Dictionary:
	if _player == null:
		return {}
	var nearest: Node3D
	var nearest_distance := INF
	for anchor in _anchor_nodes:
		if str(anchor.get_meta("anchor_id", "")) == "spawn":
			continue
		var distance := _player.global_position.distance_to(anchor.global_position)
		var radius := float(anchor.get_meta("radius", 1.2))
		if distance <= radius and distance < nearest_distance:
			nearest = anchor
			nearest_distance = distance
	if nearest == null:
		return {}
	return {
		"id": nearest.get_meta("anchor_id", ""),
		"label": nearest.get_meta("label", "地点"),
		"distance": nearest_distance,
	}


func _on_interaction_requested() -> void:
	# Living Hub owns E / prompts; avoid double handling and stale Walkable HUD text.
	if get_tree().current_scene != null and get_tree().current_scene.get_node_or_null("LivingHubIntegration") != null:
		return
	var nearest := _nearest_anchor()
	if _status_label == null:
		return
	if nearest.is_empty():
		_status_label.text = "周囲に調べられる場所はありません"
	else:
		_status_label.text = "%sを確認しました" % str(nearest.get("label", "地点"))


func _print_summary() -> void:
	print(
		"LUMINA_WALKABLE_ROOM_READY loaded=", _loaded_ok,
		" colliders=", _contract.get("colliders", []).size(),
		" anchors=", _anchor_nodes.size(),
		" route_points=", _contract.get("acceptance_route", []).size(),
		" avatar_dependency=false"
	)


func _polish_hud() -> void:
	for child in get_children():
		if not (child is CanvasLayer):
			continue
		for node in child.find_children("*", "Control", true, false):
			if node is PanelContainer:
				var panel := StyleBoxFlat.new()
				panel.bg_color = Color(0.018, 0.030, 0.052, 0.78)
				panel.border_color = Color(0.25, 0.65, 0.82, 0.40)
				panel.set_border_width_all(1)
				panel.set_corner_radius_all(12)
				panel.set_content_margin_all(14.0)
				node.add_theme_stylebox_override("panel", panel)
			elif node is ColorRect:
				node.color = Color(0.018, 0.030, 0.052, 0.72)
			elif node is Label:
				node.add_theme_color_override("font_color", Color(0.89, 0.94, 0.98, 1.0))


func _set_anchor_markers_visible(visible: bool) -> void:
	for anchor in _anchor_nodes:
		for node in anchor.find_children("*", "GeometryInstance3D", true, false):
			if node is GeometryInstance3D:
				node.visible = visible


func _capture_and_quit(path: String) -> void:
	for child in get_children():
		if child is CanvasLayer:
			child.visible = false
	# Forward+ SDFGI and the room ReflectionProbe need several rendered frames
	# before a deterministic acceptance capture is representative.
	for _frame in range(45):
		await get_tree().process_frame
	var mode := OS.get_environment("LUMINA_WALKABLE_ROOM_CAPTURE_MODE").strip_edges().to_lower()
	if mode.is_empty():
		mode = "entrance"
	if mode in ["overview", "topdown"] and _visual_root != null:
		for ceiling_name in ["Ceiling canopy", "Ceiling lounge inset", "Ceiling entry inset"]:
			var ceiling := _visual_root.find_child(ceiling_name, true, false)
			if ceiling != null:
				ceiling.visible = false
	var camera_config: Dictionary = _contract.get("capture_cameras", {}).get(mode, {})
	var capture_camera := Camera3D.new()
	capture_camera.name = "AcceptanceCamera_%s" % mode
	capture_camera.position = _vec3(camera_config.get("position", [0, 2.1, -7.3]))
	capture_camera.fov = float(camera_config.get("fov", 54.0))
	if camera_config.has("orthographic_size"):
		capture_camera.projection = Camera3D.PROJECTION_ORTHOGONAL
		capture_camera.size = float(camera_config.get("orthographic_size", 15.0))
	add_child(capture_camera)
	capture_camera.look_at(_vec3(camera_config.get("target", [0, 1, 0])), Vector3.UP if mode != "topdown" else Vector3.FORWARD)
	capture_camera.current = true
	await get_tree().process_frame
	await RenderingServer.frame_post_draw
	var image := get_viewport().get_texture().get_image()
	var err := image.save_png(path)
	print("LUMINA_WALKABLE_ROOM_CAPTURE mode=", mode, " path=", path, " err=", err)
	get_tree().quit()


func _run_validation_and_quit() -> void:
	await get_tree().physics_frame
	await get_tree().physics_frame
	if _player != null:
		_player.set("input_enabled", false)
	var route_raw: Array = _contract.get("acceptance_route", [])
	var route: Array[Vector3] = []
	for value in route_raw:
		route.append(_vec3(value))
	var space := get_world_3d().direct_space_state
	var shape_route_clear := _route_shape_clear(space, route)
	var floor_under_route := _floor_under_route(space, route)
	var actual_walk := await _drive_acceptance_route(route)
	var collision_count := get_tree().get_nodes_in_group("walkable_room_collision").size()
	if collision_count == 0:
		collision_count = _contract.get("colliders", []).size()
	var checks := {
		"contract_loaded": not _contract.is_empty(),
		"visual_glb_loaded": _loaded_ok and _visual_root != null,
		"player_created": _player != null,
		"camera_created": _camera != null,
		"collider_count_matches": collision_count == _contract.get("colliders", []).size(),
		"anchors_present": _anchor_nodes.size() >= 5,
		"shape_route_clear": shape_route_clear,
		"floor_under_route": floor_under_route,
		"actual_character_walk": bool(actual_walk.get("ok", false)),
		"returned_to_spawn": bool(actual_walk.get("returned_to_spawn", false)),
	}
	var ok := true
	for value in checks.values():
		if not bool(value):
			ok = false
	var report := {
		"ok": ok,
		"schema": "lumina.walkable_room.validation.v1",
		"checks": checks,
		"actual_walk": actual_walk,
		"room": _contract.get("room", {}),
		"colliders": _contract.get("colliders", []).size(),
		"anchors": _anchor_nodes.size(),
		"route_points": route.size(),
		"avatar_dependency": false,
	}
	var report_path := OS.get_environment("LUMINA_WALKABLE_ROOM_REPORT_PATH").strip_edges()
	if report_path != "":
		var file := FileAccess.open(report_path, FileAccess.WRITE)
		if file != null:
			file.store_string(JSON.stringify(report, "  ") + "\n")
	print("LUMINA_WALKABLE_ROOM_VALIDATION ", JSON.stringify(report))
	get_tree().quit(0 if ok else 1)


func _route_shape_clear(space: PhysicsDirectSpaceState3D, route: Array[Vector3]) -> bool:
	if route.size() < 2:
		return false
	for index in range(route.size() - 1):
		var start := route[index]
		var finish := route[index + 1]
		var distance := start.distance_to(finish)
		var steps := maxi(2, int(ceil(distance / 0.28)))
		for step in range(steps + 1):
			var point := start.lerp(finish, float(step) / float(steps))
			if not _player_space_clear(space, point):
				return false
	return true


func _player_space_clear(space: PhysicsDirectSpaceState3D, point: Vector3) -> bool:
	var sphere := SphereShape3D.new()
	sphere.radius = float(_contract.get("room", {}).get("player_radius", 0.32))
	var params := PhysicsShapeQueryParameters3D.new()
	params.shape = sphere
	params.transform = Transform3D(Basis(), point + Vector3.UP * 0.72)
	params.collide_with_bodies = true
	params.collide_with_areas = false
	if _player != null:
		params.exclude = [_player.get_rid()]
	return space.intersect_shape(params, 4).is_empty()


func _floor_under_route(space: PhysicsDirectSpaceState3D, route: Array[Vector3]) -> bool:
	for point in route:
		var query := PhysicsRayQueryParameters3D.create(point + Vector3.UP * 1.0, point + Vector3.DOWN * 0.35)
		query.collide_with_bodies = true
		if _player != null:
			query.exclude = [_player.get_rid()]
		var hit := space.intersect_ray(query)
		if hit.is_empty() or not str((hit.get("collider") as Object).name).contains("Floor"):
			return false
	return true


func _drive_acceptance_route(route: Array[Vector3]) -> Dictionary:
	if _player == null or route.size() < 2:
		return {"ok": false, "reason": "player_or_route_missing"}
	_player.global_position = route[0]
	var reached: Array = []
	var max_error := 0.0
	for index in range(1, route.size()):
		var target := route[index]
		var arrived := false
		for _frame in range(300):
			await get_tree().physics_frame
			arrived = bool(_player.call("drive_toward_for_test", target, 5.4))
			if arrived:
				break
		var error := Vector2(_player.global_position.x - target.x, _player.global_position.z - target.z).length()
		max_error = maxf(max_error, error)
		reached.append({"index": index, "arrived": arrived, "error": error})
		if not arrived:
			return {"ok": false, "reached": reached, "max_error": max_error, "failed_index": index}
	var spawn := route[0]
	var final_distance := Vector2(_player.global_position.x - spawn.x, _player.global_position.z - spawn.z).length()
	return {
		"ok": true,
		"reached": reached,
		"max_error": max_error,
		"returned_to_spawn": final_distance <= 0.22,
		"final_distance": final_distance,
		"player_state": _player.call("get_walk_state"),
	}


func _env_enabled(name: String) -> bool:
	return OS.get_environment(name).strip_edges().to_lower() in ["1", "true", "yes", "on"]


func _vec3(value: Variant) -> Vector3:
	if value is Array and value.size() >= 3:
		return Vector3(float(value[0]), float(value[1]), float(value[2]))
	return Vector3.ZERO
