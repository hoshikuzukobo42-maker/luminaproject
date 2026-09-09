extends Node3D

const ROOM_W := 16.0
const ROOM_D := 11.5

var mat_floor: StandardMaterial3D
var mat_platform: StandardMaterial3D
var mat_glass: StandardMaterial3D
var mat_sofa: StandardMaterial3D
var mat_table: StandardMaterial3D
var mat_shelf: StandardMaterial3D
var mat_cyan: StandardMaterial3D
var mat_pink: StandardMaterial3D
var mat_lav: StandardMaterial3D
var mat_dark: StandardMaterial3D
var mat_plant: StandardMaterial3D
var mat_pot: StandardMaterial3D
var mat_window: StandardMaterial3D
var mat_marker: StandardMaterial3D

func _ready() -> void:
	_build_materials()
	_setup_environment()
	_build_room()
	_build_verification_camera()
	print("FUTURE_ROOM_V007_PROCEDURAL_READY objects=", get_child_count())
	_print_validation_summary()
	var capture_path := OS.get_environment("LUMINA_FUTURE_ROOM_CAPTURE")
	if capture_path != "":
		_capture_and_quit.call_deferred(capture_path)

func _capture_and_quit(capture_path: String) -> void:
	await get_tree().process_frame
	await RenderingServer.frame_post_draw
	var image := get_viewport().get_texture().get_image()
	var err := image.save_png(capture_path)
	print("FUTURE_ROOM_V007_CAPTURE", capture_path, " err=", err)
	get_tree().quit()

func _build_materials() -> void:
	mat_floor = _mat("warm white gloss floor", Color(0.82, 0.85, 0.90, 1.0), Color.BLACK, 0.0, 1.0, 0.25)
	mat_platform = _mat("soft lavender platform", Color(0.64, 0.62, 0.76, 1.0), Color.BLACK, 0.0, 1.0, 0.35)
	mat_glass = _mat("clear blue glass", Color(0.42, 0.68, 0.92, 0.18), Color.BLACK, 0.0, 0.18, 0.12)
	mat_sofa = _mat("white lavender sofa", Color(0.70, 0.69, 0.82, 1.0), Color.BLACK, 0.0, 1.0, 0.46)
	mat_table = _mat("glass table", Color(0.45, 0.78, 0.92, 0.38), Color.BLACK, 0.0, 0.38, 0.16)
	mat_shelf = _mat("wall integrated shelf", Color(0.62, 0.64, 0.74, 1.0), Color.BLACK, 0.0, 1.0, 0.42)
	mat_dark = _mat("floor plan seam", Color(0.22, 0.25, 0.33, 1.0), Color.BLACK, 0.0, 1.0, 0.60)
	mat_plant = _mat("sage plant", Color(0.25, 0.48, 0.34, 1.0), Color.BLACK, 0.0, 1.0, 0.70)
	mat_pot = _mat("pearl plant pot", Color(0.62, 0.68, 0.72, 1.0), Color.BLACK, 0.0, 1.0, 0.42)
	mat_cyan = _mat("embedded cyan neon", Color(0.20, 0.80, 0.90, 1.0), Color(0.20, 0.95, 1.0, 1.0), 0.9, 1.0, 0.20)
	mat_pink = _mat("embedded pink neon", Color(0.86, 0.30, 0.78, 1.0), Color(1.0, 0.30, 0.90, 1.0), 0.7, 1.0, 0.20)
	mat_lav = _mat("embedded lavender neon", Color(0.52, 0.42, 0.86, 1.0), Color(0.60, 0.42, 1.0, 1.0), 0.65, 1.0, 0.20)
	mat_window = _mat("back city hologram window", Color(0.36, 0.70, 0.90, 0.42), Color(0.32, 0.72, 1.0, 1.0), 0.35, 0.42, 0.12)
	mat_marker = _mat("lumina standing marker", Color(0.56, 0.42, 0.82, 1.0), Color(0.60, 0.38, 1.0, 1.0), 0.5, 1.0, 0.25)

func _mat(label: String, albedo: Color, emission: Color, emission_energy: float, alpha: float, roughness: float) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.resource_name = label
	m.albedo_color = albedo
	m.roughness = roughness
	m.metallic = 0.0
	if alpha < 1.0:
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		m.cull_mode = BaseMaterial3D.CULL_DISABLED
	if emission_energy > 0.0:
		m.emission_enabled = true
		m.emission = emission
		m.emission_energy_multiplier = emission_energy
	return m

func _setup_environment() -> void:
	var env := WorldEnvironment.new()
	env.name = "FutureRoomSoftEnvironment"
	var e := Environment.new()
	e.background_mode = Environment.BG_COLOR
	e.background_color = Color(0.63, 0.66, 0.74, 1.0)
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	e.ambient_light_color = Color(0.78, 0.80, 0.90, 1.0)
	e.ambient_light_energy = 0.65
	env.environment = e
	add_child(env)
	_add_light("LargeSoftKey", Vector3(0, 5.4, -2.4), 520.0, 7.0)
	_add_light("BackWindowGlow", Vector3(0, 3.0, 4.8), 180.0, 5.0)

func _build_room() -> void:
	_box("Floor_16x11p5_collision", Vector3(0, -0.03, 0), Vector3(ROOM_W, 0.06, ROOM_D), mat_floor, true)
	_box("CentralLounge_Platform_collision", Vector3(0, 0.08, -0.15), Vector3(5.4, 0.12, 3.6), mat_platform, true)
	_box("BackGlassWall_collision", Vector3(0, 1.95, 5.75), Vector3(15.8, 3.75, 0.16), mat_glass, true)
	_box("LeftGlassWall_collision", Vector3(-8.0, 1.95, 0), Vector3(0.16, 3.75, 11.4), mat_glass, true)
	_box("RightGlassWall_collision", Vector3(8.0, 1.95, 0), Vector3(0.16, 3.75, 11.4), mat_glass, true)
	_box("BackCityHologramWindow", Vector3(0, 2.15, 5.62), Vector3(7.3, 2.25, 0.08), mat_window, false)

	# Central lounge, clear approach from front marker.
	_box("CentralSofa_Back_collision", Vector3(0, 0.48, 2.05), Vector3(4.2, 0.70, 0.70), mat_sofa, true)
	_box("CentralSofa_Left_collision", Vector3(-2.35, 0.48, 0.25), Vector3(0.70, 0.70, 2.25), mat_sofa, true)
	_box("CentralSofa_Right_collision", Vector3(2.35, 0.48, 0.25), Vector3(0.70, 0.70, 2.25), mat_sofa, true)
	_box("CentralCoffeeTable_collision", Vector3(0, 0.42, 0.20), Vector3(2.1, 0.22, 1.1), mat_table, true)
	_cylinder("LuminaStandingMarker_no_collision", Vector3(0, 0.065, -2.55), 0.68, 0.035, mat_marker, false)

	# Wall zones.
	_box("LeftReading_WallShelf_collision", Vector3(-7.55, 1.65, 0.10), Vector3(0.28, 2.35, 4.45), mat_shelf, true)
	_box("LeftReading_Bench_collision", Vector3(-6.65, 0.38, -2.25), Vector3(1.45, 0.45, 1.0), mat_sofa, true)
	_box("RightStudio_LongWallDesk_collision", Vector3(6.75, 0.62, -0.55), Vector3(1.85, 0.35, 3.6), mat_shelf, true)
	_box("RightStudio_Chair_collision", Vector3(5.35, 0.55, -0.55), Vector3(0.85, 0.72, 0.95), mat_sofa, true)

	# Plants only at corners/window side.
	for p in [Vector3(-6.7, 0.25, 4.35), Vector3(6.7, 0.25, 4.35), Vector3(-6.9, 0.25, -4.15), Vector3(6.9, 0.25, -4.15)]:
		_cylinder("CornerPlantPot", p, 0.32, 0.50, mat_pot, true)
		_cylinder("CornerPlantFoliage", p + Vector3(0, 0.75, 0), 0.48, 0.55, mat_plant, false)

	_build_neon_and_floorplan_lines()
	_build_city_panel_bars()

func _build_neon_and_floorplan_lines() -> void:
	_box("FloorPerimeter_Back_cyan", Vector3(0, 0.075, 5.36), Vector3(14.4, 0.035, 0.035), mat_cyan, false)
	_box("FloorPerimeter_Left_lavender", Vector3(-7.48, 0.075, 0), Vector3(0.035, 0.035, 10.1), mat_lav, false)
	_box("FloorPerimeter_Right_lavender", Vector3(7.48, 0.075, 0), Vector3(0.035, 0.035, 10.1), mat_lav, false)
	_box("OpenEntrance_Threshold_pink", Vector3(0, 0.075, -5.10), Vector3(10.8, 0.035, 0.035), mat_pink, false)
	_box("CentralPlatformOutline_Back", Vector3(0, 0.20, 1.82), Vector3(5.65, 0.025, 0.025), mat_dark, false)
	_box("CentralPlatformOutline_Front", Vector3(0, 0.20, -2.13), Vector3(5.65, 0.025, 0.025), mat_dark, false)
	_box("CentralPlatformOutline_Left", Vector3(-2.86, 0.20, -0.15), Vector3(0.025, 0.025, 3.90), mat_dark, false)
	_box("CentralPlatformOutline_Right", Vector3(2.86, 0.20, -0.15), Vector3(0.025, 0.025, 3.90), mat_dark, false)
	_box("LeftPathGuide", Vector3(-4.55, 0.08, 0), Vector3(0.035, 0.035, 8.55), mat_cyan, false)
	_box("RightPathGuide", Vector3(4.55, 0.08, 0), Vector3(0.035, 0.035, 8.55), mat_cyan, false)
	_box("CeilingRing_Front", Vector3(0, 3.68, -1.95), Vector3(5.3, 0.045, 0.045), mat_lav, false)
	_box("CeilingRing_Back", Vector3(0, 3.68, 1.60), Vector3(5.3, 0.045, 0.045), mat_lav, false)
	_box("CeilingRing_Left", Vector3(-2.65, 3.68, -0.17), Vector3(0.045, 0.045, 3.55), mat_cyan, false)
	_box("CeilingRing_Right", Vector3(2.65, 3.68, -0.17), Vector3(0.045, 0.045, 3.55), mat_pink, false)

func _build_city_panel_bars() -> void:
	var xs := [-2.8, -2.05, -1.2, -0.25, 0.65, 1.45, 2.2, 2.95]
	var hs := [0.75, 1.15, 1.65, 1.05, 1.40, 0.95, 1.85, 1.20]
	for i in xs.size():
		var h: float = hs[i]
		var m := mat_cyan if i % 3 == 0 else (mat_pink if i % 3 == 1 else mat_lav)
		_box("CityHologramBar_%02d" % i, Vector3(xs[i], 1.12 + h * 0.5, 5.55), Vector3(0.36, h, 0.035), m, false)


func _print_validation_summary() -> void:
	var static_bodies := 0
	var mesh_nodes := 0
	var marker_found := false
	for child in get_children():
		if child is StaticBody3D:
			static_bodies += 1
		elif child is MeshInstance3D:
			mesh_nodes += 1
		if child.name == "LuminaStandingMarker_no_collision":
			marker_found = true
	print(
		"FUTURE_ROOM_V007_VALIDATION ",
		"mesh_nodes=", mesh_nodes,
		" static_bodies=", static_bodies,
		" marker_found=", marker_found,
		" marker_position=(0,0.065,-2.55)",
		" room_size=16x11.5",
		" candidate_default_position=(0,0,-14)"
	)


func _box(name: String, pos: Vector3, size: Vector3, material: Material, collision: bool) -> MeshInstance3D:
	var mesh := BoxMesh.new()
	mesh.size = size
	var mi := MeshInstance3D.new()
	mi.name = name
	mi.mesh = mesh
	mi.material_override = material
	mi.position = pos
	add_child(mi)
	if collision:
		_add_box_collision(name + "_StaticBody", pos, size)
	return mi

func _cylinder(name: String, pos: Vector3, radius: float, height: float, material: Material, collision: bool) -> MeshInstance3D:
	var mesh := CylinderMesh.new()
	mesh.top_radius = radius
	mesh.bottom_radius = radius
	mesh.height = height
	mesh.radial_segments = 48
	var mi := MeshInstance3D.new()
	mi.name = name
	mi.mesh = mesh
	mi.material_override = material
	mi.position = pos
	add_child(mi)
	if collision:
		_add_cylinder_collision(name + "_StaticBody", pos, radius, height)
	return mi

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

func _add_cylinder_collision(name: String, pos: Vector3, radius: float, height: float) -> void:
	var body := StaticBody3D.new()
	body.name = name
	body.position = pos
	var shape := CollisionShape3D.new()
	var cylinder := CylinderShape3D.new()
	cylinder.radius = radius
	cylinder.height = height
	shape.shape = cylinder
	body.add_child(shape)
	add_child(body)

func _add_light(name: String, pos: Vector3, energy: float, size: float) -> void:
	var light := OmniLight3D.new()
	light.name = name
	light.position = pos
	light.light_energy = energy / 100.0
	light.omni_range = size
	add_child(light)

func _build_verification_camera() -> void:
	var cam := Camera3D.new()
	cam.name = "PreviewCamera_Front"
	cam.position = Vector3(0, 3.1, -12.0)
	cam.fov = 54.0
	add_child(cam)
	cam.look_at(Vector3(0, 1.4, 0.2), Vector3.UP)
	cam.current = true

	var cam2 := Camera3D.new()
	cam2.name = "PreviewCamera_Topdown"
	cam2.position = Vector3(0, 18.0, 0)
	cam2.projection = Camera3D.PROJECTION_ORTHOGONAL
	cam2.size = 18.0
	add_child(cam2)
	cam2.look_at(Vector3(0, 0, 0), Vector3.FORWARD)
