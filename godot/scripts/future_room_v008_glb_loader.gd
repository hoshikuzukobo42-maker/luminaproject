extends Node3D

const GLB_RES_PATH := "res://assets/world/future_room_godot/v008/lumina_future_room_prototype_v008_floorplan_rebuilt.glb"
const ROOM_W := 16.0
const ROOM_D := 12.0
const LUMINA_MARKER_LOCAL := Vector3(0.0, 0.08, -4.45)
const PORTABLE_SURFACE_ROLE_META := "si_portable_surface_role"
const SURFACE_ROLE_NAVIGATION_OBSTACLE := "navigation_obstacle"
const SURFACE_ROLE_WALKABLE_GROUND := "walkable_ground"
const SURFACE_ROLE_PHYSICAL_SURFACE := "physical_surface"

var _loaded_glb: Node3D

func _ready() -> void:
	_setup_environment()
	_loaded_glb = _load_glb_scene()
	_add_collision_guides()
	_add_lumina_marker()
	_build_verification_camera()
	_select_camera_from_env()
	_print_validation_summary()
	if _env_enabled("LUMINA_FUTURE_ROOM_VALIDATE_ONLY"):
		get_tree().quit.call_deferred()
		return
	if _env_enabled("LUMINA_FUTURE_ROOM_PHYSICS_VALIDATE"):
		_physics_validate_and_quit.call_deferred()
		return
	if _env_enabled("LUMINA_FUTURE_ROOM_PATH_VALIDATE"):
		_path_validate_and_quit.call_deferred()
		return
	var capture_path := OS.get_environment("LUMINA_FUTURE_ROOM_CAPTURE")
	if capture_path != "":
		_capture_and_quit.call_deferred(capture_path)


func _env_enabled(name: String) -> bool:
	return OS.get_environment(name).strip_edges().to_lower() in ["1", "true", "yes", "on"]


func _load_glb_scene() -> Node3D:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err := doc.append_from_file(GLB_RES_PATH, state)
	if err != OK:
		push_error("Future room v008 GLB could not be read: %s err=%s" % [GLB_RES_PATH, err])
		print("FUTURE_ROOM_V008_GLB_LOAD_FAILED path=", GLB_RES_PATH, " err=", err)
		return null
	var generated := doc.generate_scene(state)
	if generated == null:
		push_error("Future room v008 GLB generated scene is null: %s" % GLB_RES_PATH)
		print("FUTURE_ROOM_V008_GLB_GENERATE_FAILED path=", GLB_RES_PATH)
		return null
	var node := generated as Node3D
	if node == null:
		push_error("Future room v008 GLB root is not Node3D: %s" % GLB_RES_PATH)
		print("FUTURE_ROOM_V008_GLB_NOT_NODE3D path=", GLB_RES_PATH)
		return null
	node.name = "FutureRoomV008_GLB_Visual"
	node.position = Vector3.ZERO
	add_child(node)
	_tune_v008_visual_materials(node)
	print("FUTURE_ROOM_V008_GLB_LOADED path=", GLB_RES_PATH, " child_count=", node.get_child_count())
	return node


func _tune_v008_visual_materials(root: Node) -> void:
	var tuned_count := 0
	for node in _walk_nodes(root):
		var mesh_instance := node as MeshInstance3D
		if mesh_instance == null or mesh_instance.mesh == null:
			continue
		for surface_index in range(mesh_instance.mesh.get_surface_count()):
			var source_material := mesh_instance.get_surface_override_material(surface_index)
			if source_material == null:
				source_material = mesh_instance.mesh.surface_get_material(surface_index)
			var tuned := _duplicate_and_tune_v008_material(source_material, mesh_instance.name)
			if tuned != null:
				mesh_instance.set_surface_override_material(surface_index, tuned)
				tuned_count += 1
	print("FUTURE_ROOM_V008_MATERIAL_TUNED count=", tuned_count)


func _duplicate_and_tune_v008_material(material: Material, hint: String) -> Material:
	var standard := material as StandardMaterial3D
	if standard == null:
		return material
	var tuned := standard.duplicate() as StandardMaterial3D
	if tuned == null:
		return material
	var key := "%s %s" % [hint.to_lower(), tuned.resource_name.to_lower()]
	tuned.metallic = 0.0
	if key.contains("floor"):
		tuned.albedo_color = Color(0.89, 0.93, 0.98, maxf(tuned.albedo_color.a, 0.92))
		tuned.roughness = 0.30
	elif key.contains("glass") or key.contains("window") or key.contains("holo") or key.contains("screen"):
		tuned.albedo_color = Color(0.54, 0.78, 0.95, 0.24)
		tuned.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		tuned.roughness = 0.12
		tuned.emission_enabled = true
		tuned.emission = Color(0.16, 0.50, 0.88, 1.0)
		tuned.emission_energy_multiplier = 0.12
	elif key.contains("neon") or key.contains("cyan") or key.contains("pink") or key.contains("lavender") or key.contains("ring"):
		tuned.roughness = 0.18
		tuned.emission_enabled = true
		if key.contains("pink"):
			tuned.albedo_color = Color(0.95, 0.36, 0.84, 1.0)
			tuned.emission = Color(0.95, 0.28, 0.80, 1.0)
			tuned.emission_energy_multiplier = 1.05
		elif key.contains("cyan"):
			tuned.albedo_color = Color(0.16, 0.82, 0.95, 1.0)
			tuned.emission = Color(0.10, 0.72, 0.90, 1.0)
			tuned.emission_energy_multiplier = 1.10
		else:
			tuned.albedo_color = Color(0.72, 0.58, 0.92, 1.0)
			tuned.emission = Color(0.60, 0.46, 0.88, 1.0)
			tuned.emission_energy_multiplier = 0.95
	elif key.contains("sofa") or key.contains("rug"):
		tuned.albedo_color = Color(0.68, 0.64, 0.84, 1.0)
		tuned.roughness = 0.64
	elif key.contains("plant"):
		tuned.albedo_color = Color(0.34, 0.64, 0.56, 1.0)
		tuned.roughness = 0.66
	else:
		tuned.albedo_color = tuned.albedo_color.lerp(Color(0.88, 0.91, 0.98, tuned.albedo_color.a), 0.18)
		tuned.roughness = clampf(tuned.roughness, 0.24, 0.72)
	return tuned


func _setup_environment() -> void:
	var env := WorldEnvironment.new()
	env.name = "FutureRoomV008SoftEnvironment"
	var e := Environment.new()
	e.background_mode = Environment.BG_COLOR
	e.background_color = Color(0.66, 0.72, 0.82, 1.0)
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	e.ambient_light_color = Color(0.86, 0.90, 0.98, 1.0)
	e.ambient_light_energy = 0.84
	env.environment = e
	add_child(env)
	_add_light("FutureRoomV008_KeyLight", Vector3(0, 5.2, -3.2), 3.2, 8.5)
	_add_light("FutureRoomV008_BackWindowGlow", Vector3(0, 2.6, 5.45), 1.7, 7.2)
	_add_light("FutureRoomV008_EntrySoftFill", Vector3(0, 2.2, -4.8), 0.8, 5.2)


func _add_collision_guides() -> void:
	# Import conversion changes axes. Derive invisible collision from the actual
	# imported mesh bounds instead of replaying authoring-space coordinates.
	if not is_instance_valid(_loaded_glb):
		push_error("FutureRoomV008: refusing collision generation without visual geometry")
		return
	var count := 0
	for raw_node in _walk_nodes(_loaded_glb):
		var mesh_instance := raw_node as MeshInstance3D
		if mesh_instance == null or mesh_instance.mesh == null or not _is_physical_room_mesh(str(mesh_instance.name)):
			continue
		var relative_transform := global_transform.affine_inverse() * mesh_instance.global_transform
		var bounds: AABB = relative_transform * mesh_instance.get_aabb()
		if not bounds.position.is_finite() or not bounds.size.is_finite() or bounds.size.x <= 0.0 or bounds.size.y <= 0.0 or bounds.size.z <= 0.0:
			push_error("FutureRoomV008: invalid mesh collision bounds: %s" % mesh_instance.name)
			continue
		var is_floor := str(mesh_instance.name) == "v008ROOM_FLOOR_single_clean_white_gloss_16m_x_12m"
		var body_name := "FutureRoomV008_Floor_Collision" if is_floor else "FutureRoomV008_MeshCollider_" + str(mesh_instance.name)
		var surface_role := _portable_surface_role_for_mesh(str(mesh_instance.name))
		var body := _add_box_collision(
			body_name, bounds.get_center(), bounds.size, surface_role, not is_floor
		)
		body.set_meta("si_collision_source_mesh", str(mesh_instance.get_path()))
		body.set_meta("si_collision_basis", "actual_imported_mesh_aabb")
		count += 1
	print("FUTURE_ROOM_V008_MESH_DERIVED_COLLIDERS count=", count)


func _is_physical_room_mesh(mesh_name: String) -> bool:
	if mesh_name == "v008ROOM_FLOOR_single_clean_white_gloss_16m_x_12m":
		return true
	var key := mesh_name.to_lower()
	# Sky outside the glass, ceiling lights, inset floor decor and holograms do
	# not obstruct walking. Furniture, shelves, posts and all plant parts do.
	if key.contains("neon") or key.contains("_back_window_") or key.contains("_ceiling_") or key.contains("_rug_") or key.contains("floor_marker"):
		return false
	if key.contains("_holo_") and not key.contains("_stand_"):
		return false
	return key.begins_with("v008room_") and not key.contains("floor_inner")


func _portable_surface_role_for_mesh(mesh_name: String) -> String:
	if mesh_name == "v008ROOM_FLOOR_single_clean_white_gloss_16m_x_12m":
		return SURFACE_ROLE_WALKABLE_GROUND
	var key := mesh_name.to_lower()
	# Closed authoring-name prefixes are deliberately conservative.  Only
	# standalone furniture and plant geometry that can be a visual target gets
	# the navigation_obstacle role.  Walls, windows, posts, attached fixtures
	# and any future/unregistered mesh remain generic physical surfaces.
	for targetable_prefix in [
		"v008room_lounge_sofa_",
		"v008room_lounge_coffee_table_",
		"v008room_left_reading_chair_",
		"v008room_left_reading_desk_",
		"v008room_right_studio_chair_",
		"v008room_right_studio_desk_",
		"v008room_plant_",
	]:
		if key.begins_with(targetable_prefix):
			return SURFACE_ROLE_NAVIGATION_OBSTACLE
	return SURFACE_ROLE_PHYSICAL_SURFACE


func _add_lumina_marker() -> void:
	var marker := MeshInstance3D.new()
	marker.name = "LuminaStandingMarker_V008_no_collision"
	var mesh := CylinderMesh.new()
	mesh.top_radius = 0.42
	mesh.bottom_radius = 0.42
	mesh.height = 0.035
	mesh.radial_segments = 48
	marker.mesh = mesh
	var mat := StandardMaterial3D.new()
	mat.resource_name = "v008 lumina standing marker"
	mat.albedo_color = Color(1.0, 0.55, 0.95, 0.55)
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.emission_enabled = true
	mat.emission = Color(1.0, 0.35, 0.9, 1.0)
	mat.emission_energy_multiplier = 0.35
	marker.material_override = mat
	marker.position = LUMINA_MARKER_LOCAL
	add_child(marker)


func _add_box_collision(
	name: String,
	pos: Vector3,
	size: Vector3,
	surface_role: Variant = SURFACE_ROLE_PHYSICAL_SURFACE,
	is_navigation_solid: bool = false
) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = name
	body.position = pos
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = size
	shape.shape = box
	body.add_child(shape)
	add_child(body)
	var safe_surface_role := _sanitize_portable_surface_role(surface_role)
	body.set_meta(PORTABLE_SURFACE_ROLE_META, safe_surface_role)
	# Route-planning solidity is independent from the portable grounding role:
	# structural walls must still block motion while remaining physical_surface.
	if is_navigation_solid:
		body.add_to_group("si_navigation_obstacle")
		body.set_meta("si_navigation_solid_geometry", true)
	# Keep dimensions on every physical shape.  The explicit solidity flag,
	# rather than thickness or grounding role, controls route participation.
	body.set_meta("si_navigation_half_extents", Vector2(size.x * 0.5, size.z * 0.5))
	body.set_meta("si_navigation_height", size.y)
	body.set_meta("si_navigation_center_y", body.position.y)
	return body


func _sanitize_portable_surface_role(raw_role: Variant) -> String:
	if typeof(raw_role) != TYPE_STRING:
		return SURFACE_ROLE_PHYSICAL_SURFACE
	var role := String(raw_role)
	if role in [
		SURFACE_ROLE_NAVIGATION_OBSTACLE,
		SURFACE_ROLE_WALKABLE_GROUND,
		SURFACE_ROLE_PHYSICAL_SURFACE,
	]:
		return role
	return SURFACE_ROLE_PHYSICAL_SURFACE


func _add_light(name: String, pos: Vector3, energy: float, range_size: float) -> void:
	var light := OmniLight3D.new()
	light.name = name
	light.position = pos
	light.light_energy = energy
	light.omni_range = range_size
	add_child(light)


func _build_verification_camera() -> void:
	var preview_current := _preview_camera_current_by_default()
	var cam := Camera3D.new()
	cam.name = "PreviewCamera_V008_Front"
	cam.position = Vector3(0, 3.3, -11.8)
	cam.fov = 52.0
	add_child(cam)
	cam.look_at(Vector3(0, 1.35, 0.2), Vector3.UP)
	cam.current = preview_current

	var top := Camera3D.new()
	top.name = "PreviewCamera_V008_Topdown"
	top.position = Vector3(0, 18.0, 0)
	top.projection = Camera3D.PROJECTION_ORTHOGONAL
	top.size = 18.0
	add_child(top)
	top.look_at(Vector3(0, 0, 0), Vector3.FORWARD)

	var eye := Camera3D.new()
	eye.name = "PreviewCamera_V008_LuminaEye"
	eye.position = LUMINA_MARKER_LOCAL + Vector3(0, 1.45, 0)
	eye.fov = 62.0
	add_child(eye)
	eye.look_at(Vector3(0, 1.55, 1.35), Vector3.UP)
	if not preview_current:
		print("FUTURE_ROOM_V008_PREVIEW_CAMERAS_EMBEDDED_NON_CURRENT parent=", get_parent().name if get_parent() else "<none>")


func _preview_camera_current_by_default() -> bool:
	if _env_enabled("LUMINA_FUTURE_ROOM_VALIDATE_ONLY"):
		return true
	if _env_enabled("LUMINA_FUTURE_ROOM_PHYSICS_VALIDATE"):
		return true
	if _env_enabled("LUMINA_FUTURE_ROOM_PATH_VALIDATE"):
		return true
	if OS.get_environment("LUMINA_FUTURE_ROOM_CAPTURE").strip_edges() != "":
		return true
	if OS.get_environment("LUMINA_FUTURE_ROOM_CAMERA").strip_edges() != "":
		return true
	var current_scene := get_tree().current_scene
	if current_scene == self:
		return true
	return get_parent() == get_tree().root


func _select_camera_from_env() -> void:
	var mode := OS.get_environment("LUMINA_FUTURE_ROOM_CAMERA").strip_edges().to_lower()
	if mode == "":
		return
	var target_name := "PreviewCamera_V008_Front"
	if mode in ["eye", "lumina", "lumina_eye", "first_person"]:
		target_name = "PreviewCamera_V008_LuminaEye"
	elif mode in ["top", "topdown", "floorplan"]:
		target_name = "PreviewCamera_V008_Topdown"
	var cam := get_node_or_null(target_name) as Camera3D
	if cam != null:
		cam.current = true
		print("FUTURE_ROOM_V008_CAMERA_SELECTED mode=", mode, " camera=", target_name)


func _capture_and_quit(capture_path: String) -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	await RenderingServer.frame_post_draw
	var image := get_viewport().get_texture().get_image()
	var err := image.save_png(capture_path)
	print("FUTURE_ROOM_V008_GLB_CAPTURE ", capture_path, " err=", err)
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
		"FUTURE_ROOM_V008_GLB_VALIDATION ",
		"loaded=", _loaded_glb != null,
		" mesh_nodes=", mesh_nodes,
		" static_bodies=", static_bodies,
		" marker_position=", LUMINA_MARKER_LOCAL,
		" room_size=16x12",
		" topdown_goal=clean_zoned_room"
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
	var entrance_clear := _sphere_clear(space, Vector3(0, 0.65, -5.3), 0.36)
	var left_outer_clear := _sphere_clear(space, Vector3(-5.2, 0.65, 0.0), 0.36)
	var right_outer_clear := _sphere_clear(space, Vector3(5.2, 0.65, 0.0), 0.36)
	print(
		"FUTURE_ROOM_V008_PHYSICS_VALIDATION ",
		"marker_floor_hit=", marker_floor_hit,
		" marker_clear=", marker_clear,
		" entrance_clear=", entrance_clear,
		" left_outer_clear=", left_outer_clear,
		" right_outer_clear=", right_outer_clear
	)
	get_tree().quit()


func _path_validate_and_quit() -> void:
	await get_tree().physics_frame
	await get_tree().physics_frame
	var space := get_world_3d().direct_space_state
	var entrance_to_marker := _path_clear(space, Vector3(0, 0.65, -5.4), Vector3(0, 0.65, -4.45), 0.36, 5)
	var marker_to_lounge_front := _path_clear(space, Vector3(0, 0.65, -4.45), Vector3(0, 0.65, -2.1), 0.36, 9)
	var left_outer_path := _path_clear(space, Vector3(-5.2, 0.65, -4.2), Vector3(-5.2, 0.65, 4.4), 0.36, 12)
	var right_outer_path := _path_clear(space, Vector3(5.2, 0.65, -4.2), Vector3(5.2, 0.65, 4.4), 0.36, 12)
	var back_window_front := _path_clear(space, Vector3(-3.8, 0.65, 4.35), Vector3(3.8, 0.65, 4.35), 0.36, 10)
	var all_ok := entrance_to_marker and marker_to_lounge_front and left_outer_path and right_outer_path and back_window_front
	print(
		"FUTURE_ROOM_V008_PATH_VALIDATION ",
		"all=", all_ok,
		" entrance_to_marker=", entrance_to_marker,
		" marker_to_lounge_front=", marker_to_lounge_front,
		" left_outer_path=", left_outer_path,
		" right_outer_path=", right_outer_path,
		" back_window_front=", back_window_front
	)
	get_tree().quit()


func _path_clear(space: PhysicsDirectSpaceState3D, from: Vector3, to: Vector3, radius: float, steps: int) -> bool:
	for i in range(steps + 1):
		var t := float(i) / float(max(steps, 1))
		var p := from.lerp(to, t)
		if not _sphere_clear(space, p, radius):
			return false
	return true


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
