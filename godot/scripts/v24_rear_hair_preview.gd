## Minimal standalone preview script for v24 rear-hair candidate.
## Uses GLTFDocument.append_from_file (no .import files needed, no preload deps).
## Usage: set LUMINA_AVATAR_GLB_PROFILE_ROOT env var, then launch with this scene.
extends Node3D

func _ready() -> void:
	var route_dir := OS.get_environment("LUMINA_AVATAR_GLB_PROFILE_ROOT").strip_edges()
	if route_dir.is_empty():
		route_dir = "/LOCAL_RUNTIME_NOT_INCLUDED/godot/assets/avatars/lumina_meshi_biped/animations_user_fixed_visual_public_quality_candidate_v24_20260629_rear_hair_emissive_lift"
	var glb_path := route_dir.path_join("Meshy_AI_Aogiri_High_School_Un_biped_Animation_Idle_12_withSkin.glb")

	var gltf := GLTFDocument.new()
	var state := GLTFState.new()
	var err := gltf.append_from_file(glb_path, state)
	if err != OK:
		push_error("v24_preview: GLTFDocument.append_from_file failed: %d  path=%s" % [err, glb_path])
		return
	var avatar := gltf.generate_scene(state)
	if avatar == null:
		push_error("v24_preview: generate_scene returned null")
		return
	add_child(avatar)
	avatar.name = "Avatar"

	# Camera: rear view
	var cam := Camera3D.new()
	cam.name = "RearCam"
	cam.position = Vector3(0.0, 1.55, 3.0)
	cam.rotation_degrees = Vector3(0.0, 180.0, 0.0)
	add_child(cam)

	# Environment
	var env := Environment.new()
	env.ambient_light_color = Color(0.18, 0.14, 0.25)
	env.ambient_light_energy = 1.0
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.12, 0.10, 0.18)
	var wenv := WorldEnvironment.new()
	wenv.environment = env
	add_child(wenv)

	for ld_pos in [Vector3(0.0, 2.5, 2.0), Vector3(0.8, 1.5, 1.5), Vector3(-0.8, 1.5, 1.5)]:
		var light := OmniLight3D.new()
		light.position = ld_pos
		light.light_energy = 5.0
		add_child(light)

	print("v24_preview: avatar loaded OK — ", glb_path)
