extends Node3D

const GLB_RES_PATH := "res://assets/world/future_room_godot/v007/lumina_future_room_prototype_v007_future_room_style_pass.glb"
const ROOM_W := 16.0
const ROOM_D := 11.5
const LUMINA_MARKER_LOCAL := Vector3(0.0, 0.08, -3.05)

var _loaded_glb: Node3D

func _ready() -> void:
	_setup_environment()
	_loaded_glb = _load_glb_scene()
	_add_collision_guides()
	_add_lumina_marker()
	_build_verification_camera()
	_select_camera_from_env()
	_print_validation_summary()
	if OS.get_environment("LUMINA_FUTURE_ROOM_PATH_VALIDATE").strip_edges().to_lower() in ["1", "true", "yes", "on"]:
		_path_validate_and_quit.call_deferred()
		return
	if OS.get_environment("LUMINA_FUTURE_ROOM_DETAILED_WALK_VALIDATE").strip_edges().to_lower() in ["1", "true", "yes", "on"]:
		_detailed_walk_validate_and_quit.call_deferred()
		return
	if OS.get_environment("LUMINA_FUTURE_ROOM_PHYSICS_VALIDATE").strip_edges().to_lower() in ["1", "true", "yes", "on"]:
		_physics_validate_and_quit.call_deferred()
		return
	if OS.get_environment("LUMINA_FUTURE_ROOM_VALIDATE_ONLY").strip_edges().to_lower() in ["1", "true", "yes", "on"]:
		get_tree().quit.call_deferred()
	var capture_path := OS.get_environment("LUMINA_FUTURE_ROOM_CAPTURE")
	if capture_path != "":
		_capture_and_quit.call_deferred(capture_path)


func _load_glb_scene() -> Node3D:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err := doc.append_from_file(GLB_RES_PATH, state)
	if err != OK:
		push_error("Future room v007 GLB could not be read: %s err=%s" % [GLB_RES_PATH, err])
		print("FUTURE_ROOM_V007_GLB_LOAD_FAILED path=", GLB_RES_PATH, " err=", err)
		return null
	var generated := doc.generate_scene(state)
	if generated == null:
		push_error("Future room v007 GLB generated scene is null: %s" % GLB_RES_PATH)
		print("FUTURE_ROOM_V007_GLB_GENERATE_FAILED path=", GLB_RES_PATH)
		return null
	var node := generated as Node3D
	if node == null:
		push_error("Future room v007 GLB root is not Node3D: %s" % GLB_RES_PATH)
		print("FUTURE_ROOM_V007_GLB_NOT_NODE3D path=", GLB_RES_PATH)
		return null
	node.name = "FutureRoomV007_GLB_Visual"
	node.position = Vector3.ZERO
	add_child(node)
	print("FUTURE_ROOM_V007_GLB_LOADED path=", GLB_RES_PATH, " child_count=", node.get_child_count())
	return node


func _setup_environment() -> void:
	var env := WorldEnvironment.new()
	env.name = "FutureRoomV007GLBSoftEnvironment"
	var e := Environment.new()
	e.background_mode = Environment.BG_COLOR
	e.background_color = Color(0.62, 0.65, 0.74, 1.0)
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	e.ambient_light_color = Color(0.80, 0.82, 0.90, 1.0)
	e.ambient_light_energy = 0.72
	env.environment = e
	add_child(env)
	_add_light("FutureRoomV007_GLB_KeyLight", Vector3(0, 5.2, -3.0), 5.4, 8.0)
	_add_light("FutureRoomV007_GLB_BackGlow", Vector3(0, 2.8, 5.3), 2.1, 6.0)


func _add_collision_guides() -> void:
	# GLB is visual. These simple guides are the Godot-ready collision/nav basis.
	_add_box_collision("FutureRoomV007_Floor_Collision", Vector3(0, -0.05, 0), Vector3(ROOM_W, 0.10, ROOM_D))
	_add_box_collision("FutureRoomV007_BackWall_Collision", Vector3(0, 1.8, 5.75), Vector3(15.8, 3.6, 0.18))
	_add_box_collision("FutureRoomV007_LeftWall_Collision", Vector3(-8.0, 1.8, 0), Vector3(0.18, 3.6, 11.35))
	_add_box_collision("FutureRoomV007_RightWall_Collision", Vector3(8.0, 1.8, 0), Vector3(0.18, 3.6, 11.35))
	_add_box_collision("FutureRoomV007_CentralSofa_Back_Collision", Vector3(0, 0.42, 1.23), Vector3(3.8, 0.75, 0.75))
	_add_box_collision("FutureRoomV007_CentralSofa_Left_Collision", Vector3(-2.25, 0.42, -0.2), Vector3(0.75, 0.75, 2.35))
	_add_box_collision("FutureRoomV007_CentralSofa_Right_Collision", Vector3(2.25, 0.42, -0.2), Vector3(0.75, 0.75, 2.35))
	_add_box_collision("FutureRoomV007_CoffeeTable_Collision", Vector3(0, 0.30, -0.18), Vector3(1.9, 0.35, 1.15))
	_add_box_collision("FutureRoomV007_LeftReadingWall_Collision", Vector3(-7.45, 0.9, 0.5), Vector3(0.75, 1.8, 5.9))
	_add_box_collision("FutureRoomV007_RightStudioDesk_Collision", Vector3(7.2, 0.62, -2.0), Vector3(1.3, 0.7, 2.5))


func _add_lumina_marker() -> void:
	var marker := MeshInstance3D.new()
	marker.name = "LuminaStandingMarker_GLB_no_collision"
	var mesh := CylinderMesh.new()
	mesh.top_radius = 0.45
	mesh.bottom_radius = 0.45
	mesh.height = 0.035
	mesh.radial_segments = 48
	marker.mesh = mesh
	var mat := StandardMaterial3D.new()
	mat.resource_name = "lumina standing marker pink"
	mat.albedo_color = Color(1.0, 0.55, 0.95, 0.72)
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.emission_enabled = true
	mat.emission = Color(1.0, 0.35, 0.9, 1.0)
	mat.emission_energy_multiplier = 0.45
	marker.material_override = mat
	marker.position = LUMINA_MARKER_LOCAL
	add_child(marker)


func _add_box_collision(name: String, pos: Vector3, size: Vector3) -> void:
	var body := StaticBody3D.new()
	body.name = name
	body.position = pos
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = size
	shape.shape = box
	body.add_child(shape)
	add_child(body)


func _add_light(name: String, pos: Vector3, energy: float, range_size: float) -> void:
	var light := OmniLight3D.new()
	light.name = name
	light.position = pos
	light.light_energy = energy
	light.omni_range = range_size
	add_child(light)


func _build_verification_camera() -> void:
	var cam := Camera3D.new()
	cam.name = "PreviewCamera_GLB_Front"
	cam.position = Vector3(0, 3.3, -11.5)
	cam.fov = 52.0
	add_child(cam)
	cam.look_at(Vector3(0, 1.4, 0.1), Vector3.UP)
	cam.current = true

	var top := Camera3D.new()
	top.name = "PreviewCamera_GLB_Topdown"
	top.position = Vector3(0, 18.0, 0)
	top.projection = Camera3D.PROJECTION_ORTHOGONAL
	top.size = 18.0
	add_child(top)
	top.look_at(Vector3(0, 0, 0), Vector3.FORWARD)

	var eye := Camera3D.new()
	eye.name = "PreviewCamera_GLB_LuminaEye"
	eye.position = LUMINA_MARKER_LOCAL + Vector3(0, 1.45, 0)
	eye.fov = 64.0
	add_child(eye)
	eye.look_at(Vector3(0, 1.95, 4.8), Vector3.UP)


func _capture_and_quit(capture_path: String) -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	await RenderingServer.frame_post_draw
	var image := get_viewport().get_texture().get_image()
	var err := image.save_png(capture_path)
	print("FUTURE_ROOM_V007_GLB_CAPTURE", capture_path, " err=", err)
	get_tree().quit()


func _print_validation_summary() -> void:
	var static_bodies := 0
	var mesh_nodes := 0
	for node in _walk_nodes(self):
		if node is StaticBody3D:
			static_bodies += 1
		if node is MeshInstance3D:
			mesh_nodes += 1
	print(
		"FUTURE_ROOM_V007_GLB_VALIDATION ",
		"loaded=", _loaded_glb != null,
		" mesh_nodes=", mesh_nodes,
		" static_bodies=", static_bodies,
		" marker_position=", LUMINA_MARKER_LOCAL,
		" room_size=16x11.5",
		" candidate_default_position=(0,0,-14)"
	)


func _walk_nodes(root: Node) -> Array[Node]:
	var out: Array[Node] = []
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		out.append(node)
		for child in node.get_children():
			stack.append(child)
	return out


func _physics_validate_and_quit() -> void:
	await get_tree().physics_frame
	await get_tree().physics_frame
	var space := get_world_3d().direct_space_state
	var marker_floor_hit := _ray_hits_floor(space, LUMINA_MARKER_LOCAL + Vector3(0, 1.8, 0), LUMINA_MARKER_LOCAL + Vector3(0, -0.8, 0))
	var marker_clear := _sphere_clear(space, LUMINA_MARKER_LOCAL + Vector3(0, 0.65, 0), 0.36)
	var entrance_clear := _sphere_clear(space, Vector3(0, 0.65, -4.65), 0.36)
	var left_path_clear := _sphere_clear(space, Vector3(-4.35, 0.65, 0.0), 0.36)
	var right_path_clear := _sphere_clear(space, Vector3(4.35, 0.65, 0.0), 0.36)
	print(
		"FUTURE_ROOM_V007_PHYSICS_VALIDATION ",
		"marker_floor_hit=", marker_floor_hit,
		" marker_clear=", marker_clear,
		" entrance_clear=", entrance_clear,
		" left_path_clear=", left_path_clear,
		" right_path_clear=", right_path_clear
	)
	get_tree().quit()


func _ray_hits_floor(space: PhysicsDirectSpaceState3D, from: Vector3, to: Vector3) -> bool:
	var query := PhysicsRayQueryParameters3D.create(from, to)
	query.collide_with_bodies = true
	query.collide_with_areas = false
	var hit := space.intersect_ray(query)
	if hit.is_empty():
		return false
	var collider: Object = hit.get("collider")
	return collider != null and str(collider.name).contains("Floor")


func _sphere_clear(space: PhysicsDirectSpaceState3D, center: Vector3, radius: float) -> bool:
	var params := PhysicsShapeQueryParameters3D.new()
	var sphere := SphereShape3D.new()
	sphere.radius = radius
	params.shape = sphere
	params.transform = Transform3D(Basis(), center)
	params.collide_with_bodies = true
	params.collide_with_areas = false
	var hits := space.intersect_shape(params, 8)
	return hits.is_empty()


func _select_camera_from_env() -> void:
	var mode := OS.get_environment("LUMINA_FUTURE_ROOM_CAMERA").strip_edges().to_lower()
	if mode == "":
		return
	var target_name := "PreviewCamera_GLB_Front"
	if mode in ["eye", "lumina", "lumina_eye", "first_person"]:
		target_name = "PreviewCamera_GLB_LuminaEye"
	elif mode in ["top", "topdown", "floorplan"]:
		target_name = "PreviewCamera_GLB_Topdown"
	var cam := get_node_or_null(target_name) as Camera3D
	if cam != null:
		cam.current = true
		print("FUTURE_ROOM_V007_CAMERA_SELECTED mode=", mode, " camera=", target_name)
	else:
		push_warning("Future room v007 requested camera was not found: %s" % target_name)


func _path_validate_and_quit() -> void:
	await get_tree().physics_frame
	await get_tree().physics_frame
	var space := get_world_3d().direct_space_state
	var entrance_to_marker := _path_clear(space, Vector3(0, 0.65, -5.05), Vector3(0, 0.65, -3.05), 0.36, 8)
	var marker_to_lounge := _path_clear(space, Vector3(0, 0.65, -3.05), Vector3(0, 0.65, -1.45), 0.36, 8)
	var left_outer_path := _path_clear(space, Vector3(-4.35, 0.65, -4.2), Vector3(-4.35, 0.65, 3.9), 0.36, 16)
	var right_outer_path := _path_clear(space, Vector3(4.35, 0.65, -4.2), Vector3(4.35, 0.65, 3.9), 0.36, 16)
	var back_window_path := _path_clear(space, Vector3(-3.2, 0.65, 3.95), Vector3(3.2, 0.65, 3.95), 0.36, 12)
	print(
		"FUTURE_ROOM_V007_PATH_VALIDATION ",
		"entrance_to_marker=", entrance_to_marker,
		" marker_to_lounge_front_clear=", marker_to_lounge,
		" left_outer_path=", left_outer_path,
		" right_outer_path=", right_outer_path,
		" back_window_path=", back_window_path
	)
	get_tree().quit()


func _path_clear(space: PhysicsDirectSpaceState3D, start: Vector3, end: Vector3, radius: float, steps: int) -> bool:
	if steps <= 0:
		return _sphere_clear(space, start, radius)
	for i in range(steps + 1):
		var t := float(i) / float(steps)
		var p := start.lerp(end, t)
		if not _sphere_clear(space, p, radius):
			print("FUTURE_ROOM_V007_PATH_BLOCKED point=", p, " radius=", radius)
			return false
	return true


func _detailed_walk_validate_and_quit() -> void:
	await get_tree().physics_frame
	await get_tree().physics_frame
	var space := get_world_3d().direct_space_state
	var radius := 0.36
	var results := {}
	results["entrance_lane"] = _path_clear(space, Vector3(0, 0.65, -5.05), Vector3(0, 0.65, -3.05), radius, 8)
	results["marker_to_left_reading"] = _path_clear(space, Vector3(0, 0.65, -3.05), Vector3(-5.85, 0.65, -2.35), radius, 12)
	results["marker_to_right_studio"] = _path_clear(space, Vector3(0, 0.65, -3.05), Vector3(5.85, 0.65, -1.35), radius, 12)
	results["sofa_left_side_walk"] = _path_clear(space, Vector3(-3.35, 0.65, -2.35), Vector3(-3.35, 0.65, 2.10), radius, 12)
	results["sofa_right_side_walk"] = _path_clear(space, Vector3(3.35, 0.65, -2.35), Vector3(3.35, 0.65, 2.10), radius, 12)
	results["sofa_back_walk"] = _path_clear(space, Vector3(-3.15, 0.65, 2.10), Vector3(3.15, 0.65, 2.10), radius, 12)
	results["coffee_table_front_clear"] = _path_clear(space, Vector3(-1.35, 0.65, -1.75), Vector3(1.35, 0.65, -1.75), radius, 8)
	results["back_window_front_walk"] = _path_clear(space, Vector3(-5.6, 0.65, 4.25), Vector3(5.6, 0.65, 4.25), radius, 18)
	results["left_wall_approach_points"] = _points_clear(space, [
		Vector3(-6.55, 0.65, -4.25),
		Vector3(-6.55, 0.65, -0.35),
		Vector3(-6.55, 0.65, 4.25),
	], radius)
	results["right_wall_approach_points"] = _points_clear(space, [
		Vector3(6.55, 0.65, -4.25),
		Vector3(6.55, 0.65, 0.35),
		Vector3(6.55, 0.65, 4.25),
	], radius)
	results["front_entry_wall_points"] = _points_clear(space, [
		Vector3(-2.05, 0.65, -5.05),
		Vector3(0, 0.65, -5.05),
		Vector3(2.05, 0.65, -5.05),
	], radius)
	results["floor_hits_key_points"] = _floor_hits_all(space, [
		Vector3(0, 0.65, -3.05),
		Vector3(-5.85, 0.65, -2.35),
		Vector3(5.85, 0.65, -1.35),
		Vector3(-3.35, 0.65, 2.10),
		Vector3(3.35, 0.65, 2.10),
		Vector3(0, 0.65, 4.25),
	])
	var all_clear := true
	var detail := ""
	for key in results.keys():
		var ok := bool(results[key])
		all_clear = all_clear and ok
		detail += " %s=%s" % [key, ok]
	print("FUTURE_ROOM_V007_DETAILED_WALK_VALIDATION all=", all_clear, detail)
	get_tree().quit()


func _points_clear(space: PhysicsDirectSpaceState3D, points: Array, radius: float) -> bool:
	for point in points:
		if not _sphere_clear(space, point, radius):
			print("FUTURE_ROOM_V007_POINT_BLOCKED point=", point, " radius=", radius)
			return false
	return true


func _floor_hits_all(space: PhysicsDirectSpaceState3D, points: Array) -> bool:
	for point in points:
		var p := point as Vector3
		if not _ray_hits_floor(space, p + Vector3(0, 1.6, 0), p + Vector3(0, -0.9, 0)):
			print("FUTURE_ROOM_V007_FLOOR_MISSING point=", p)
			return false
	return true
