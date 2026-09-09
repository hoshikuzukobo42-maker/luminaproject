extends SceneTree

const PROFILE_ROOT := "res://assets/avatars/lumina_meshi_biped/material_profiles/profile_62_hair_uv_texture_white_speckle_fix_from_profile60_20260606/"
const CACHE_ROOT := "res://assets/avatars/lumina_meshi_biped/generated_scene_cache/profile_62_hair_uv_texture_white_speckle_fix_from_profile60_20260606/"


func _initialize() -> void:
	var source_dir := ProjectSettings.globalize_path(PROFILE_ROOT)
	var cache_dir := ProjectSettings.globalize_path(CACHE_ROOT)
	DirAccess.make_dir_recursive_absolute(cache_dir)
	var dir := DirAccess.open(source_dir)
	if dir == null:
		push_error("source dir open failed: %s" % source_dir)
		quit(1)
		return
	var files: Array[String] = []
	dir.list_dir_begin()
	while true:
		var file_name := dir.get_next()
		if file_name == "":
			break
		if not dir.current_is_dir() and file_name.get_extension().to_lower() == "glb":
			files.append(file_name)
	dir.list_dir_end()
	files.sort()
	var ok_count := 0
	for file_name in files:
		var source_path := PROFILE_ROOT.path_join(file_name)
		var output_name := file_name.get_basename() + ".scn"
		var output_path := CACHE_ROOT.path_join(output_name)
		if _convert_glb_to_scene(source_path, output_path):
			ok_count += 1
	print("[LuminaSceneCache] converted=%d total=%d cache=%s" % [ok_count, files.size(), CACHE_ROOT])
	quit(0 if ok_count == files.size() else 2)


func _convert_glb_to_scene(source_path: String, output_path: String) -> bool:
	var gltf := GLTFDocument.new()
	var state := GLTFState.new()
	var err := gltf.append_from_file(ProjectSettings.globalize_path(source_path), state)
	if err != OK:
		push_error("[LuminaSceneCache] append failed: %s err=%s" % [source_path, err])
		return false
	var node := gltf.generate_scene(state)
	if node == null or not (node is Node3D):
		push_error("[LuminaSceneCache] scene generate failed: %s" % source_path)
		return false
	var packed := PackedScene.new()
	err = packed.pack(node)
	if err != OK:
		push_error("[LuminaSceneCache] pack failed: %s err=%s" % [source_path, err])
		node.free()
		return false
	err = ResourceSaver.save(packed, output_path)
	node.free()
	if err != OK:
		push_error("[LuminaSceneCache] save failed: %s err=%s" % [output_path, err])
		return false
	print("[LuminaSceneCache] %s -> %s" % [source_path.get_file(), output_path])
	return true
