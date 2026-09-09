extends SceneTree

const VRM_PATH: String = "res://assets/vrm/stella.vrm"
const VRMA_PATH: String = "res://assets/animation/idle_loop.vrma"

const MOTION_CANDIDATES := {
	"shoulder": ["leftshoulder", "rightshoulder", "j_bip_l_shoulder", "j_bip_r_shoulder", "left_shoulder", "right_shoulder"],
	"forearm": ["leftlowerarm", "rightlowerarm", "j_bip_l_lowerarm", "j_bip_r_lowerarm", "lowerarmleft", "lowerarmright"],
	"chest": ["chest", "upperchest", "spine2", "spine_02", "upper_spine"],
	"neck": ["neck", "upperneck", "neck01", "neck1"],
	"head": ["head", "face", "j_bip_head", "head01", "head1"],
	"thigh": ["leftupperleg", "rightupperleg", "j_bip_l_upperleg", "j_bip_r_upperleg", "upperlegleft", "upperlegright"],
	"knee": ["leftlowerleg", "rightlowerleg", "j_bip_l_lowerleg", "j_bip_r_lowerleg", "leftknee", "rightknee", "kneel", "kneer"],
	"foot": ["leftfoot", "rightfoot", "j_bip_l_foot", "j_bip_r_foot", "lfoot", "rfoot"],
}


func _init() -> void:
	var vrm_scene := load(VRM_PATH)
	print("[Readiness] vrm_exists=%s" % ResourceLoader.exists(VRM_PATH))
	if not (vrm_scene is PackedScene):
		print("[Readiness] ERROR stella.vrm is not loaded as PackedScene")
		quit(1)

	var root := (vrm_scene as PackedScene).instantiate()
	get_root().add_child(root)
	print("[Readiness] loaded_root=%s type=%s" % [root.name, root.get_class()])

	var animation_player := _find_first_of_type(root, "AnimationPlayer") as AnimationPlayer
	if animation_player:
		print("[Readiness] animation_player=%s anims=%d" % [
			animation_player.name,
			animation_player.get_animation_list().size(),
		])
		print("[Readiness] animation_names=%s" % ",".join(animation_player.get_animation_list()))
	else:
		print("[Readiness] animation_player=none")

	var skeleton := _find_first_of_type(root, "Skeleton3D") as Skeleton3D
	if not skeleton:
		print("[Readiness] ERROR Skeleton3D is not found")
		quit(1)

	_print_bone_inventory(skeleton)
	_print_adapter_candidates(skeleton)
	_check_face_mesh(root)

	print("[Readiness] vrma_path=%s exists=%s" % [VRMA_PATH, FileAccess.file_exists(VRMA_PATH)])
	var vrma_import_path := VRMA_PATH + ".import"
	print("[Readiness] vrma_import_path=%s exists=%s" % [vrma_import_path, FileAccess.file_exists(vrma_import_path)])
	if FileAccess.file_exists(VRMA_PATH):
		var has_loader := ResourceLoader.exists(VRMA_PATH)
		print("[Readiness] vrma_loader_exists=%s" % has_loader)
		if has_loader:
			var vrma_resource := ResourceLoader.load(VRMA_PATH)
			if vrma_resource:
				print("[Readiness] vrma_loadable=%s" % vrma_resource.get_class())
			else:
				print("[Readiness] vrma_loadable=none")
		else:
			print("[Readiness] vrma_loadable=loader_not_found (expected for .vrma without VRM importer)")
	else:
		print("[Readiness] vrma_loadable=file_missing")
	quit()


func _print_bone_inventory(skeleton: Skeleton3D) -> void:
	print("[Readiness] skeleton_name=%s" % skeleton.name)
	print("[Readiness] bone_count=%d" % skeleton.get_bone_count())
	for i in range(skeleton.get_bone_count()):
		var name := skeleton.get_bone_name(i)
		var parent_idx := skeleton.get_bone_parent(i)
		var parent_name := "<root>" if parent_idx < 0 else skeleton.get_bone_name(parent_idx)
		print("[Readiness] bone=%d name=%s parent=%s" % [i, name, parent_name])


func _print_adapter_candidates(skeleton: Skeleton3D) -> void:
	for key in MOTION_CANDIDATES.keys():
		var found := false
		for candidate in MOTION_CANDIDATES[key]:
			if _find_bone_by_candidates(skeleton, [candidate]) != -1:
				var idx := _find_bone_by_candidates(skeleton, [candidate])
				print("[Readiness] candidate_match group=%s => %s (idx=%d)" % [key, _normalize_name(skeleton.get_bone_name(idx)), idx])
				found = true
		if not found:
			print("[Readiness] candidate_missing group=%s" % key)


func _check_face_mesh(root: Node) -> void:
	for child in root.get_children():
		var mesh := child as MeshInstance3D
		if mesh and mesh.mesh and mesh.mesh.get_blend_shape_count() > 0:
			var blend_shapes: Array[String] = []
			for i in range(mesh.mesh.get_blend_shape_count()):
				blend_shapes.append(mesh.mesh.get_blend_shape_name(i))
			if blend_shapes.size() > 0:
				print("[Readiness] mesh=%s blend_shapes=%d" % [child.name, blend_shapes.size()])


func _find_first_of_type(node: Node, wanted_type: String) -> Node:
	if node is Node and node.is_class(wanted_type):
		return node
	for child in node.get_children():
		var found := _find_first_of_type(child, wanted_type)
		if found:
			return found
	return null


func _find_bone_by_candidates(skeleton: Skeleton3D, candidates: Array[String]) -> int:
	var normalized_candidates: Array[String] = []
	for candidate in candidates:
		normalized_candidates.append(_normalize_name(candidate))
	for i in range(skeleton.get_bone_count()):
		var bone_name := _normalize_name(skeleton.get_bone_name(i))
		for candidate in normalized_candidates:
			if bone_name.find(candidate) != -1:
				return i
	return -1


func _normalize_name(value: String) -> String:
	var lowered := value.to_lower()
	var normalized := ""
	for i in range(lowered.length()):
		var code := lowered.unicode_at(i)
		if (code >= 97 and code <= 122) or (code >= 48 and code <= 57):
			normalized += char(code)
	return normalized
