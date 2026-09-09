class_name LuminaWorldTelemetry
extends Node
## Captures exact, read-only Godot world state for Lumina integration.
##
## Geometry and visibility in this snapshot come from the running Godot scene.
## No visual-language-model result is read by this component or allowed to
## replace an engine value. Attach this node from a scene or another script;
## this file intentionally does not modify project settings or scenes.

signal snapshot_captured(snapshot: Dictionary)

const SCHEMA_VERSION := "1.0"
const INTERACTABLE_GROUPS := [
	"lumina_interactable",
	"lumina_living_hub_interactable",
	"si_addressable",
]

@export_group("Required actors")
@export var player_path: NodePath = NodePath("")
@export var lumina_path: NodePath = NodePath("")
@export var camera_path: NodePath = NodePath("")

@export_group("Room and zone")
@export var room_node_path: NodePath = NodePath("")
@export var zone_node_path: NodePath = NodePath("")
@export var room_id: String = ""
@export var zone_id: String = ""

@export_group("Interactables")
@export var interactables_root_path: NodePath = NodePath("")

@export_group("Navigation")
@export var navigation_agent_path: NodePath = NodePath("")
@export var navigation_region_path: NodePath = NodePath("")

@export_group("Capture")
@export var capture_on_process: bool = false
@export_range(0.05, 60.0, 0.05) var capture_interval_seconds: float = 0.25
@export_file("*.jsonl") var output_path: String = ""

var last_snapshot: Dictionary = {}
var _last_capture_msec: int = -1


func _process(_delta: float) -> void:
	if not capture_on_process:
		return
	var now_msec := Time.get_ticks_msec()
	var interval_msec := maxi(1, int(round(capture_interval_seconds * 1000.0)))
	if _last_capture_msec >= 0 and now_msec - _last_capture_msec < interval_msec:
		return
	_last_capture_msec = now_msec
	if output_path.is_empty():
		capture_snapshot()
	else:
		capture_to_file(output_path)


## Returns one deterministic snapshot without changing scene state.
func capture_snapshot() -> Dictionary:
	var errors: Array[String] = []
	var player := _resolve_node_path(player_path, "player", errors, true)
	var lumina := _resolve_node_path(lumina_path, "lumina", errors, true)
	var camera := _resolve_node_path(camera_path, "camera", errors)
	var room_node := _resolve_node_path(room_node_path, "room", errors)
	var zone_node := _resolve_node_path(zone_node_path, "zone", errors)
	var interactables_root := _resolve_node_path(interactables_root_path, "interactables_root", errors)
	var navigation_agent := _resolve_node_path(navigation_agent_path, "navigation_agent", errors)
	var navigation_region := _resolve_node_path(navigation_region_path, "navigation_region", errors)

	var snapshot := {
		"schema_version": SCHEMA_VERSION,
		"source": "godot_engine",
		"captured_at_utc": _timestamp_utc(),
		"unix_time": Time.get_unix_time_from_system(),
		"monotonic_time_ms": Time.get_ticks_msec(),
		"authority": {
			"geometry": "godot_engine",
			"visibility": "godot_engine",
			"navigation": "godot_engine",
			"vlm_may_override_geometry": false,
		},
		"room": _location_snapshot(room_node, room_id, "room"),
		"zone": _location_snapshot(zone_node, zone_id, "zone"),
		"player": _node_snapshot(player, camera),
		"lumina": _node_snapshot(lumina, camera),
		"interactables": _capture_interactables(interactables_root, camera),
		"navigation": _capture_navigation(navigation_agent, navigation_region),
	}
	snapshot["errors"] = errors
	snapshot["valid"] = errors.is_empty()
	last_snapshot = snapshot
	snapshot_captured.emit(snapshot)
	return snapshot


## Captures a JSONL record at a local Godot FileAccess path, such as user://.
## Returns false for invalid paths or file-open failures and logs a useful
## engine error without raising an exception into the scene.
func capture_to_file(path: String = output_path) -> bool:
	if path.is_empty():
		push_error("LuminaWorldTelemetry: output path is empty")
		return false
	var global_path := ProjectSettings.globalize_path(path)
	var parent_path := global_path.get_base_dir()
	if not parent_path.is_empty():
		var mkdir_error := DirAccess.make_dir_recursive_absolute(parent_path)
		if mkdir_error != OK:
			push_error("LuminaWorldTelemetry: could not create output directory: %s" % parent_path)
			return false
	var snapshot := capture_snapshot()
	var mode := FileAccess.READ_WRITE if FileAccess.file_exists(path) else FileAccess.WRITE_READ
	var file := FileAccess.open(path, mode)
	if file == null:
		push_error("LuminaWorldTelemetry: could not open output path: %s" % path)
		return false
	file.seek_end()
	file.store_line(JSON.stringify(snapshot))
	file.close()
	return true


func _resolve_node_path(path: NodePath, label: String, errors: Array[String], required: bool = false) -> Node:
	if path.is_empty():
		if required:
			errors.append("%s_path_not_configured" % label)
		return null
	var node := get_node_or_null(path)
	if node == null:
		errors.append("%s_path_not_found: %s" % [label, str(path)])
	return node


func _location_snapshot(node: Node, configured_id: String, kind: String) -> Dictionary:
	var value := configured_id.strip_edges()
	if value.is_empty() and node != null:
		value = _metadata_string(node, "lumina_%s_id" % kind)
	if value.is_empty() and node != null:
		value = str(node.name)
	return {
		"id": value,
		"node_path": str(node.get_path()) if node != null else "",
		"available": node != null and node.is_inside_tree(),
	}


func configure_runtime_nodes(
	player: Node,
	lumina: Node,
	navigation_agent: Node,
	navigation_region: Node,
	camera: Node,
	room: Node,
	zone: Node,
	interactables_root: Node
) -> void:
	player_path = get_path_to(player) if player != null else NodePath("")
	lumina_path = get_path_to(lumina) if lumina != null else NodePath("")
	navigation_agent_path = get_path_to(navigation_agent) if navigation_agent != null else NodePath("")
	navigation_region_path = get_path_to(navigation_region) if navigation_region != null else NodePath("")
	camera_path = get_path_to(camera) if camera != null else NodePath("")
	room_node_path = get_path_to(room) if room != null else NodePath("")
	zone_node_path = get_path_to(zone) if zone != null else NodePath("")
	interactables_root_path = get_path_to(interactables_root) if interactables_root != null else NodePath("")


func _node_snapshot(node: Node, camera: Node) -> Dictionary:
	if node == null:
		return {"available": false, "reason": "node_not_resolved"}
	var node_3d := node as Node3D
	if node_3d == null:
		return {
			"available": false,
			"reason": "node_is_not_node3d",
			"node_path": str(node.get_path()),
		}
	return {
		"available": node_3d.is_inside_tree(),
		"node_path": str(node_3d.get_path()),
		"name": str(node_3d.name),
		"transform": _transform_snapshot(node_3d.global_transform),
		"visibility": _visibility_snapshot(node_3d, camera),
	}


func _capture_interactables(root: Node, camera: Node) -> Array[Dictionary]:
	var candidates: Array[Node] = []
	if root != null:
		_collect_marked_interactables(root, candidates)
	else:
		for group_name in INTERACTABLE_GROUPS:
			for node in get_tree().get_nodes_in_group(group_name):
				if is_instance_valid(node) and not candidates.has(node):
					candidates.append(node)
	candidates.sort_custom(_sort_nodes_by_path)

	var records: Array[Dictionary] = []
	for node in candidates:
		var node_3d := node as Node3D
		if node_3d == null:
			records.append({
				"available": false,
				"id": _node_id(node),
				"node_path": str(node.get_path()),
				"reason": "interactable_is_not_node3d",
			})
			continue
		records.append({
			"available": node_3d.is_inside_tree(),
			"id": _node_id(node),
			"name": str(node_3d.name),
			"node_path": str(node_3d.get_path()),
			"transform": _transform_snapshot(node_3d.global_transform),
			"aabb": _world_aabb_snapshot(node_3d),
			"visibility": _visibility_snapshot(node_3d, camera),
		})
	return records


func _collect_marked_interactables(node: Node, candidates: Array[Node]) -> void:
	if _is_marked_interactable(node):
		candidates.append(node)
	for child in node.get_children():
		_collect_marked_interactables(child, candidates)


func _is_marked_interactable(node: Node) -> bool:
	for group_name in INTERACTABLE_GROUPS:
		if node.is_in_group(group_name):
			return true
	return node.has_meta("lumina_interactable") and bool(node.get_meta("lumina_interactable"))


func _sort_nodes_by_path(left: Node, right: Node) -> bool:
	return str(left.get_path()) < str(right.get_path())


func _node_id(node: Node) -> String:
	var configured_id := _metadata_string(node, "lumina_interactable_id")
	return configured_id if not configured_id.is_empty() else str(node.get_path())


func _world_aabb_snapshot(root: Node) -> Dictionary:
	var state := {
		"has_bounds": false,
		"bounds": AABB(Vector3.ZERO, Vector3.ZERO),
		"source_count": 0,
	}
	_accumulate_world_aabb(root, state)
	if not bool(state["has_bounds"]):
		return {"available": false, "reason": "no_visual_or_collision_geometry", "source_count": 0}
	var bounds: AABB = state["bounds"]
	return {
		"available": true,
		"position": _vector3_snapshot(bounds.position),
		"size": _vector3_snapshot(bounds.size),
		"max": _vector3_snapshot(bounds.position + bounds.size),
		"source_count": int(state["source_count"]),
	}


func _accumulate_world_aabb(node: Node, state: Dictionary) -> void:
	if node is VisualInstance3D:
		var visual := node as VisualInstance3D
		_union_world_aabb(state, visual.get_aabb(), visual.global_transform)
	elif node is CollisionShape3D:
		var collision_shape := node as CollisionShape3D
		if collision_shape.shape != null:
			var debug_mesh: Mesh = collision_shape.shape.get_debug_mesh()
			if debug_mesh != null:
				_union_world_aabb(state, debug_mesh.get_aabb(), collision_shape.global_transform)
	for child in node.get_children():
		_accumulate_world_aabb(child, state)


func _union_world_aabb(state: Dictionary, local_aabb: AABB, transform: Transform3D) -> void:
	var world_aabb := _transform_aabb(local_aabb, transform)
	if not bool(state["has_bounds"]):
		state["bounds"] = world_aabb
		state["has_bounds"] = true
	else:
		var current: AABB = state["bounds"]
		state["bounds"] = current.merge(world_aabb)
	state["source_count"] = int(state["source_count"]) + 1


func _transform_aabb(local_aabb: AABB, transform: Transform3D) -> AABB:
	var corners := [
		local_aabb.position,
		local_aabb.position + Vector3(local_aabb.size.x, 0.0, 0.0),
		local_aabb.position + Vector3(0.0, local_aabb.size.y, 0.0),
		local_aabb.position + Vector3(0.0, 0.0, local_aabb.size.z),
		local_aabb.position + Vector3(local_aabb.size.x, local_aabb.size.y, 0.0),
		local_aabb.position + Vector3(local_aabb.size.x, 0.0, local_aabb.size.z),
		local_aabb.position + Vector3(0.0, local_aabb.size.y, local_aabb.size.z),
		local_aabb.position + local_aabb.size,
	]
	var first: Vector3 = transform * corners[0]
	var result := AABB(first, Vector3.ZERO)
	for index in range(1, corners.size()):
		result = result.expand(transform * corners[index])
	return result


func _visibility_snapshot(root: Node, camera_node: Node) -> Dictionary:
	var state := {
		"visual_instance_count": 0,
		"visible_visual_instance_count": 0,
	}
	_accumulate_visibility(root, state)
	var total := int(state["visual_instance_count"])
	var visible := int(state["visible_visual_instance_count"])
	var result := {
		"visible_in_tree": root.is_inside_tree() and (total == 0 or visible > 0),
		"visual_instance_count": total,
		"visible_visual_instance_count": visible,
		"has_render_geometry": total > 0,
	}
	var camera := camera_node as Camera3D
	if camera == null or not camera.is_inside_tree():
		result["camera_available"] = false
		result["in_camera_frustum"] = null
		result["occlusion_checked"] = false
		return result
	var target_position := (root as Node3D).global_position
	var projected := camera.unproject_position(target_position)
	var viewport_rect := camera.get_viewport().get_visible_rect()
	result["camera_available"] = true
	result["camera_node_path"] = str(camera.get_path())
	result["in_camera_frustum"] = (
		not camera.is_position_behind(target_position)
		and viewport_rect.has_point(projected)
	)
	result["occlusion_checked"] = false
	result["occlusion_reason"] = "not_claimed_without_collision_filter_contract"
	return result


func _accumulate_visibility(node: Node, state: Dictionary) -> void:
	if node is VisualInstance3D:
		state["visual_instance_count"] = int(state["visual_instance_count"]) + 1
		var visual := node as VisualInstance3D
		if visual.visible and _visual_is_visible_in_tree(visual):
			state["visible_visual_instance_count"] = int(state["visible_visual_instance_count"]) + 1
	for child in node.get_children():
		_accumulate_visibility(child, state)


func _visual_is_visible_in_tree(visual: VisualInstance3D) -> bool:
	if not visual.is_inside_tree():
		return false
	var current: Node = visual
	while current != null:
		if current is VisualInstance3D and not (current as VisualInstance3D).visible:
			return false
		current = current.get_parent()
	return true


func _capture_navigation(agent_node: Node, region_node: Node) -> Dictionary:
	var result := {
		"configured": agent_node != null or region_node != null,
		"status": "not_configured" if agent_node == null and region_node == null else "configured",
	}
	if agent_node is NavigationAgent3D:
		var agent := agent_node as NavigationAgent3D
		var path := agent.get_current_navigation_path()
		result["agent"] = {
			"available": agent.is_inside_tree(),
			"node_path": str(agent.get_path()),
			"navigation_finished": agent.is_navigation_finished(),
			"target_reachable": agent.is_target_reachable(),
			"target_position": _vector3_snapshot(agent.target_position),
			"path_point_count": path.size(),
			"path_length": _path_length(path),
			"map": _navigation_map_snapshot(agent.get_navigation_map()),
		}
	else:
		result["agent"] = {"available": false, "reason": "not_navigation_agent3d"}
	if region_node is NavigationRegion3D:
		var region := region_node as NavigationRegion3D
		result["region"] = {
			"available": region.is_inside_tree(),
			"node_path": str(region.get_path()),
			"enabled": region.enabled,
			"has_navigation_mesh": region.navigation_mesh != null,
			"map": _navigation_map_snapshot(region.get_navigation_map()),
		}
	else:
		result["region"] = {"available": false, "reason": "not_navigation_region3d"}
	return result


func _path_length(path: PackedVector3Array) -> float:
	var length := 0.0
	for index in range(1, path.size()):
		length += path[index - 1].distance_to(path[index])
	return length


func _navigation_map_snapshot(map_rid: RID) -> Dictionary:
	if not map_rid.is_valid():
		return {"valid": false}
	return {
		"valid": true,
		"iteration_id": NavigationServer3D.map_get_iteration_id(map_rid),
		"region_count": NavigationServer3D.map_get_regions(map_rid).size(),
	}


func _transform_snapshot(transform: Transform3D) -> Dictionary:
	return {
		"origin": _vector3_snapshot(transform.origin),
		"basis_x": _vector3_snapshot(transform.basis.x),
		"basis_y": _vector3_snapshot(transform.basis.y),
		"basis_z": _vector3_snapshot(transform.basis.z),
		"rotation_quaternion": _quaternion_snapshot(transform.basis.get_rotation_quaternion()),
	}


func _vector3_snapshot(value: Vector3) -> Array[float]:
	return [value.x, value.y, value.z]


func _quaternion_snapshot(value: Quaternion) -> Array[float]:
	return [value.x, value.y, value.z, value.w]


func _metadata_string(node: Node, key: String) -> String:
	if node == null or not node.has_meta(key):
		return ""
	var value = node.get_meta(key)
	return str(value).strip_edges() if value is String else ""


func _timestamp_utc() -> String:
	return "%sZ" % Time.get_datetime_string_from_system(true)
