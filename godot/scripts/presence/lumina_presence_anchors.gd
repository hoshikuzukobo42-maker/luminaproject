extends RefCounted
class_name LuminaPresenceAnchors

## Resolves Blender-supplied presence anchors with safe fallbacks.

const ANCHOR_LOBBY_GREETING := "ANCHOR_LOBBY_GREETING"
const ANCHOR_WINDOW_VIEW_PLAYER := "ANCHOR_WINDOW_VIEW_PLAYER"
const ANCHOR_WINDOW_VIEW_LUMINA := "ANCHOR_WINDOW_VIEW_LUMINA"
const ANCHOR_BENCH_PLAYER := "ANCHOR_BENCH_PLAYER"
const ANCHOR_BENCH_LUMINA := "ANCHOR_BENCH_LUMINA"
const ANCHOR_ROOM_GUIDE := "ANCHOR_ROOM_GUIDE"
const ANCHOR_MEMORY_WALL := "ANCHOR_MEMORY_WALL"
const ANCHOR_FAREWELL := "ANCHOR_FAREWELL"

const CONTRACT_NAMES: PackedStringArray = [
	ANCHOR_LOBBY_GREETING,
	ANCHOR_WINDOW_VIEW_PLAYER,
	ANCHOR_WINDOW_VIEW_LUMINA,
	ANCHOR_BENCH_PLAYER,
	ANCHOR_BENCH_LUMINA,
	ANCHOR_ROOM_GUIDE,
	ANCHOR_MEMORY_WALL,
	ANCHOR_FAREWELL,
]

## Fallback world positions used when scene anchors are missing.
const FALLBACK_POSITIONS := {
	ANCHOR_LOBBY_GREETING: Vector3(0.0, 0.0, -2.0),
	ANCHOR_WINDOW_VIEW_PLAYER: Vector3(-1.4, 0.0, 1.2),
	ANCHOR_WINDOW_VIEW_LUMINA: Vector3(-0.7, 0.0, 1.2),
	ANCHOR_BENCH_PLAYER: Vector3(1.2, 0.0, 0.4),
	ANCHOR_BENCH_LUMINA: Vector3(0.5, 0.0, 0.4),
	ANCHOR_ROOM_GUIDE: Vector3(0.0, 0.0, 3.5),
	ANCHOR_MEMORY_WALL: Vector3(2.2, 0.0, -0.5),
	ANCHOR_FAREWELL: Vector3(0.0, 0.0, -3.5),
}

var _nodes: Dictionary = {}
var _fallback_origin := Vector3.ZERO
var _using_fallback: Dictionary = {}


func set_fallback_origin(origin: Vector3) -> void:
	_fallback_origin = origin


func clear() -> void:
	_nodes.clear()
	_using_fallback.clear()


func register_node(anchor_name: String, node: Node3D) -> bool:
	var key := _normalize(anchor_name)
	if key.is_empty() or node == null:
		return false
	_nodes[key] = node
	_using_fallback[key] = false
	return true


func register_from_parent(root: Node) -> int:
	if root == null:
		return 0
	var found := 0
	for name in CONTRACT_NAMES:
		var node := _find_named_node3d(root, name)
		if node != null and register_node(name, node):
			found += 1
	return found


func resolve_position(anchor_name: String) -> Dictionary:
	var key := _normalize(anchor_name)
	if key.is_empty():
		return {"ok": false, "position": _fallback_origin, "source": "invalid", "name": ""}
	if _nodes.has(key):
		var node: Node3D = _nodes[key] as Node3D
		if node != null and is_instance_valid(node):
			_using_fallback[key] = false
			return {
				"ok": true,
				"position": node.global_position,
				"source": "scene",
				"name": key,
				"yaw": node.global_rotation.y,
			}
	var local: Vector3 = FALLBACK_POSITIONS.get(key, Vector3.ZERO)
	_using_fallback[key] = true
	return {
		"ok": true,
		"position": _fallback_origin + local,
		"source": "fallback",
		"name": key,
		"yaw": 0.0,
	}


func resolve_pair(anchor_pair: Variant) -> Dictionary:
	var lumina_name := ANCHOR_BENCH_LUMINA
	var player_name := ANCHOR_BENCH_PLAYER
	if typeof(anchor_pair) == TYPE_DICTIONARY:
		var d: Dictionary = anchor_pair
		if d.has("lumina"):
			lumina_name = String(d["lumina"])
		if d.has("player"):
			player_name = String(d["player"])
	elif typeof(anchor_pair) == TYPE_STRING:
		var s := String(anchor_pair).strip_edges()
		if s == "window" or s == "view":
			lumina_name = ANCHOR_WINDOW_VIEW_LUMINA
			player_name = ANCHOR_WINDOW_VIEW_PLAYER
		elif s == "memory":
			lumina_name = ANCHOR_MEMORY_WALL
			player_name = ANCHOR_MEMORY_WALL
		elif not s.is_empty():
			lumina_name = s
	return {
		"lumina": resolve_position(lumina_name),
		"player": resolve_position(player_name),
		"lumina_name": _normalize(lumina_name),
		"player_name": _normalize(player_name),
	}


func get_status() -> Dictionary:
	var present: Array[String] = []
	var missing: Array[String] = []
	for name in CONTRACT_NAMES:
		if _nodes.has(name) and _nodes[name] != null and is_instance_valid(_nodes[name]):
			present.append(name)
		else:
			missing.append(name)
	return {
		"present": present,
		"missing": missing,
		"using_fallback": _using_fallback.duplicate(true),
		"fallback_origin": _fallback_origin,
	}


func _normalize(anchor_name: String) -> String:
	return anchor_name.strip_edges().to_upper()


func _find_named_node3d(root: Node, target_name: String) -> Node3D:
	if root == null:
		return null
	if root.name == target_name and root is Node3D:
		return root as Node3D
	for child in root.get_children():
		var found := _find_named_node3d(child, target_name)
		if found != null:
			return found
	return null
