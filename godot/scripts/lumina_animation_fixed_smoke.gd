extends SceneTree

const ROOT_PATH: String = "res://assets/avatars/lumina_meshi_biped/animations_fixed/"

var ok_count: int = 0
var fail_count: int = 0

func _init() -> void:
	var files: Array[String] = _list_glb_files()
	print("LUMINA_FIXED_GLB_SMOKE_START count=%d" % files.size())
	for file_name: String in files:
		_check_file(file_name)
	print("LUMINA_FIXED_GLB_SMOKE_DONE ok=%d fail=%d" % [ok_count, fail_count])
	quit(0 if fail_count == 0 else 1)

func _list_glb_files() -> Array[String]:
	var result: Array[String] = []
	var dir: DirAccess = DirAccess.open(ROOT_PATH)
	if dir == null:
		print("FAIL open_dir %s" % ROOT_PATH)
		return result
	dir.list_dir_begin()
	while true:
		var file_name: String = dir.get_next()
		if file_name == "":
			break
		if dir.current_is_dir():
			continue
		if file_name.ends_with(".glb"):
			result.append(file_name)
	dir.list_dir_end()
	result.sort()
	return result

func _check_file(file_name: String) -> void:
	var path: String = ROOT_PATH + file_name
	var packed: Resource = ResourceLoader.load(path)
	if packed == null or not (packed is PackedScene):
		fail_count += 1
		print("FAIL load %s" % file_name)
		return
	var node: Node = (packed as PackedScene).instantiate()
	if node == null:
		fail_count += 1
		print("FAIL instantiate %s" % file_name)
		return
	var player: AnimationPlayer = _find_animation_player(node)
	if player == null:
		fail_count += 1
		print("FAIL no_animation_player %s" % file_name)
		node.queue_free()
		return
	var animations: PackedStringArray = player.get_animation_list()
	if animations.is_empty():
		fail_count += 1
		print("FAIL no_animations %s" % file_name)
		node.queue_free()
		return
	ok_count += 1
	print("OK %s animation=%s" % [file_name, animations[0]])
	node.queue_free()

func _find_animation_player(node: Node) -> AnimationPlayer:
	if node is AnimationPlayer:
		return node as AnimationPlayer
	for child: Node in node.get_children():
		var found: AnimationPlayer = _find_animation_player(child)
		if found != null:
			return found
	return null
