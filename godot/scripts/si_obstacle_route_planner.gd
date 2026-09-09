extends RefCounted
class_name SIObstacleRoutePlanner

## Pure XZ-plane route planner for the demo avatar.
##
## Obstacles use one of these shapes:
##   {"kind": "rect", "center": Vector2(x, z), "half_extents": Vector2(x, z)}
##   {"kind": "circle", "center": Vector2(x, z), "radius": float}
##
## `bounds` describes the permitted avatar-center area. `clearance` expands every
## obstacle, so callers should not pre-expand obstacle dimensions. Returned
## waypoints are intermediate points only; the caller retains the exact goal.

const _EPSILON := 0.0001
const _MAX_GRID_CELLS := 262144


static func plan_route(
	start: Vector3,
	goal: Vector3,
	bounds: Rect2,
	obstacles: Array,
	cell_size: float,
	clearance: float,
	max_waypoints: int
) -> Dictionary:
	var empty_waypoints: Array[Vector3] = []
	if not _valid_vector3(start) or not _valid_vector3(goal):
		return _result(false, "invalid_input", empty_waypoints, 0.0)
	if not _valid_rect(bounds):
		return _result(false, "invalid_input", empty_waypoints, 0.0)
	if not is_finite(cell_size) or cell_size <= _EPSILON:
		return _result(false, "invalid_input", empty_waypoints, 0.0)
	if not is_finite(clearance) or clearance < 0.0 or max_waypoints < 0:
		return _result(false, "invalid_input", empty_waypoints, 0.0)

	var start_2d := Vector2(start.x, start.z)
	var goal_2d := Vector2(goal.x, goal.z)
	if not _point_in_bounds_inclusive(start_2d, bounds) or not _point_in_bounds_inclusive(goal_2d, bounds):
		return _result(false, "out_of_bounds", empty_waypoints, 0.0)

	var normalized_result := _normalize_obstacles(obstacles, clearance)
	if not bool(normalized_result.get("ok", false)):
		return _result(false, "invalid_input", empty_waypoints, 0.0)
	var normalized_obstacles: Array = normalized_result.get("obstacles", [])

	if _point_is_blocked(start_2d, normalized_obstacles):
		return _result(false, "start_blocked", empty_waypoints, 0.0)
	if _point_is_blocked(goal_2d, normalized_obstacles):
		return _result(false, "goal_blocked", empty_waypoints, 0.0)
	if _segment_is_clear(start_2d, goal_2d, normalized_obstacles):
		return _result(true, "direct_clear", empty_waypoints, start.distance_to(goal))

	var grid_size := Vector2i(
		maxi(1, int(ceil(bounds.size.x / cell_size))),
		maxi(1, int(ceil(bounds.size.y / cell_size)))
	)
	if grid_size.x * grid_size.y > _MAX_GRID_CELLS:
		return _result(false, "grid_too_large", empty_waypoints, 0.0)

	var astar := AStarGrid2D.new()
	astar.region = Rect2i(Vector2i.ZERO, grid_size)
	astar.cell_size = Vector2.ONE
	astar.diagonal_mode = AStarGrid2D.DIAGONAL_MODE_NEVER
	astar.default_compute_heuristic = AStarGrid2D.HEURISTIC_MANHATTAN
	astar.default_estimate_heuristic = AStarGrid2D.HEURISTIC_MANHATTAN
	astar.jumping_enabled = false
	astar.update()

	for grid_y in range(grid_size.y):
		for grid_x in range(grid_size.x):
			var cell_id := Vector2i(grid_x, grid_y)
			# Clearance already expands every obstacle by the avatar radius. Marking a
			# whole cell on any overlap would add almost another full cell on both
			# sides and can incorrectly seal valid exhibition walkways.
			if _point_is_blocked(_cell_center(cell_id, bounds, cell_size), normalized_obstacles):
				astar.set_point_solid(cell_id, true)

	var start_cell_result := _nearest_connectable_cell(
		start_2d,
		astar,
		grid_size,
		bounds,
		cell_size,
		normalized_obstacles
	)
	var goal_cell_result := _nearest_connectable_cell(
		goal_2d,
		astar,
		grid_size,
		bounds,
		cell_size,
		normalized_obstacles
	)
	if not bool(start_cell_result.get("ok", false)) or not bool(goal_cell_result.get("ok", false)):
		return _result(false, "no_path", empty_waypoints, 0.0)

	var start_cell: Vector2i = start_cell_result.get("cell", Vector2i(-1, -1))
	var goal_cell: Vector2i = goal_cell_result.get("cell", Vector2i(-1, -1))
	# AStarGrid2D checks cell occupancy, not the connecting edges. Build an
	# explicit graph so sub-cell walls cannot silently invalidate the chosen path.
	var graph := AStar2D.new()
	for grid_y in range(grid_size.y):
		for grid_x in range(grid_size.x):
			var cell_id := Vector2i(grid_x, grid_y)
			if not astar.is_point_solid(cell_id):
				graph.add_point(grid_y * grid_size.x + grid_x, _cell_center(cell_id, bounds, cell_size))
	for point_id in graph.get_point_ids():
		var cell_id := Vector2i(point_id % grid_size.x, point_id / grid_size.x)
		for offset in [Vector2i.RIGHT, Vector2i.DOWN]:
			var neighbor: Vector2i = cell_id + offset
			if neighbor.x >= grid_size.x or neighbor.y >= grid_size.y:
				continue
			var neighbor_id := neighbor.y * grid_size.x + neighbor.x
			if graph.has_point(neighbor_id) and _segment_is_clear(graph.get_point_position(point_id), graph.get_point_position(neighbor_id), normalized_obstacles):
				graph.connect_points(point_id, neighbor_id)
	var graph_path := graph.get_id_path(start_cell.y * grid_size.x + start_cell.x, goal_cell.y * grid_size.x + goal_cell.x)
	var id_path: Array[Vector2i] = []
	for point_id in graph_path:
		id_path.append(Vector2i(point_id % grid_size.x, point_id / grid_size.x))
	if id_path.is_empty():
		return _result(false, "no_path", empty_waypoints, 0.0)

	var raw_points: Array[Vector2] = []
	_append_unique_point(raw_points, start_2d)
	for cell_id_variant in id_path:
		var cell_id: Vector2i = cell_id_variant
		_append_unique_point(raw_points, _cell_center(cell_id, bounds, cell_size))
	_append_unique_point(raw_points, goal_2d)

	var smooth_points := _smooth_path(raw_points, normalized_obstacles)
	if smooth_points.size() < 2:
		return _result(false, "no_path", empty_waypoints, 0.0)
	var intermediate_count := maxi(0, smooth_points.size() - 2)
	if intermediate_count > max_waypoints:
		return _result(false, "waypoint_limit_exceeded", empty_waypoints, 0.0)

	var waypoints := _to_intermediate_waypoints(smooth_points, start.y, goal.y)
	var path_length := _path_length_3d(start, waypoints, goal)
	return _result(true, "planned", waypoints, path_length)


static func _result(ok: bool, status: String, waypoints: Array[Vector3], path_length: float) -> Dictionary:
	return {
		"ok": ok,
		"status": status,
		"waypoints": waypoints,
		"path_length": path_length,
	}


static func _normalize_obstacles(obstacles: Array, clearance: float) -> Dictionary:
	var normalized: Array = []
	for raw_obstacle in obstacles:
		if not (raw_obstacle is Dictionary):
			return {"ok": false, "obstacles": []}
		var obstacle: Dictionary = raw_obstacle
		var kind := String(obstacle.get("kind", "")).strip_edges().to_lower()
		var center_value: Variant = obstacle.get("center", null)
		if not (center_value is Vector2):
			return {"ok": false, "obstacles": []}
		var center: Vector2 = center_value
		if not _valid_vector2(center):
			return {"ok": false, "obstacles": []}

		if kind == "rect":
			var half_extents_value: Variant = obstacle.get("half_extents", null)
			if not (half_extents_value is Vector2):
				return {"ok": false, "obstacles": []}
			var half_extents: Vector2 = half_extents_value
			if not _valid_vector2(half_extents) or half_extents.x < 0.0 or half_extents.y < 0.0:
				return {"ok": false, "obstacles": []}
			normalized.append({
				"kind": "rect",
				"center": center,
				"half_extents": half_extents + Vector2.ONE * clearance,
			})
		elif kind == "circle":
			var radius_value: Variant = obstacle.get("radius", null)
			if typeof(radius_value) not in [TYPE_INT, TYPE_FLOAT]:
				return {"ok": false, "obstacles": []}
			var radius := float(radius_value)
			if not is_finite(radius) or radius < 0.0:
				return {"ok": false, "obstacles": []}
			normalized.append({
				"kind": "circle",
				"center": center,
				"radius": radius + clearance,
			})
		else:
			return {"ok": false, "obstacles": []}
	return {"ok": true, "obstacles": normalized}


static func _nearest_connectable_cell(
	point: Vector2,
	astar: AStarGrid2D,
	grid_size: Vector2i,
	bounds: Rect2,
	cell_size: float,
	obstacles: Array
) -> Dictionary:
	var found := false
	var best_cell := Vector2i(-1, -1)
	var best_distance_squared := 1.0e30
	for grid_y in range(grid_size.y):
		for grid_x in range(grid_size.x):
			var cell_id := Vector2i(grid_x, grid_y)
			if astar.is_point_solid(cell_id):
				continue
			var center := _cell_center(cell_id, bounds, cell_size)
			var distance_squared := point.distance_squared_to(center)
			if found and distance_squared > best_distance_squared + _EPSILON:
				continue
			if not _segment_is_clear(point, center, obstacles):
				continue
			if (
				not found
				or distance_squared < best_distance_squared - _EPSILON
				or (
					absf(distance_squared - best_distance_squared) <= _EPSILON
					and _cell_precedes(cell_id, best_cell)
				)
			):
				found = true
				best_cell = cell_id
				best_distance_squared = distance_squared
	return {"ok": found, "cell": best_cell}


static func _cell_precedes(left: Vector2i, right: Vector2i) -> bool:
	if right.x < 0 or right.y < 0:
		return true
	if left.y != right.y:
		return left.y < right.y
	return left.x < right.x


static func _cell_rect(cell_id: Vector2i, bounds: Rect2, cell_size: float) -> Rect2:
	var cell_min := bounds.position + Vector2(float(cell_id.x), float(cell_id.y)) * cell_size
	var cell_max := Vector2(
		minf(cell_min.x + cell_size, bounds.end.x),
		minf(cell_min.y + cell_size, bounds.end.y)
	)
	return Rect2(cell_min, Vector2(maxf(0.0, cell_max.x - cell_min.x), maxf(0.0, cell_max.y - cell_min.y)))


static func _cell_center(cell_id: Vector2i, bounds: Rect2, cell_size: float) -> Vector2:
	var rect := _cell_rect(cell_id, bounds, cell_size)
	return rect.position + rect.size * 0.5


static func _cell_is_blocked(cell: Rect2, obstacles: Array) -> bool:
	for obstacle_variant in obstacles:
		var obstacle: Dictionary = obstacle_variant
		var center: Vector2 = obstacle["center"]
		if String(obstacle["kind"]) == "rect":
			var half_extents: Vector2 = obstacle["half_extents"]
			var obstacle_rect := Rect2(center - half_extents, half_extents * 2.0)
			if _rects_overlap_inclusive(cell, obstacle_rect):
				return true
		else:
			var radius := float(obstacle["radius"])
			var closest := Vector2(
				clampf(center.x, cell.position.x, cell.end.x),
				clampf(center.y, cell.position.y, cell.end.y)
			)
			if center.distance_squared_to(closest) <= radius * radius + _EPSILON:
				return true
	return false


static func _rects_overlap_inclusive(first: Rect2, second: Rect2) -> bool:
	return not (
		first.end.x < second.position.x - _EPSILON
		or second.end.x < first.position.x - _EPSILON
		or first.end.y < second.position.y - _EPSILON
		or second.end.y < first.position.y - _EPSILON
	)


static func _point_is_blocked(point: Vector2, obstacles: Array) -> bool:
	for obstacle_variant in obstacles:
		var obstacle: Dictionary = obstacle_variant
		var center: Vector2 = obstacle["center"]
		if String(obstacle["kind"]) == "rect":
			var half_extents: Vector2 = obstacle["half_extents"]
			var delta := point - center
			if absf(delta.x) <= half_extents.x + _EPSILON and absf(delta.y) <= half_extents.y + _EPSILON:
				return true
		else:
			var radius := float(obstacle["radius"])
			if point.distance_squared_to(center) <= radius * radius + _EPSILON:
				return true
	return false


static func _segment_is_clear(start: Vector2, goal: Vector2, obstacles: Array) -> bool:
	for obstacle_variant in obstacles:
		var obstacle: Dictionary = obstacle_variant
		var center: Vector2 = obstacle["center"]
		if String(obstacle["kind"]) == "rect":
			var half_extents: Vector2 = obstacle["half_extents"]
			if _segment_intersects_rect(start, goal, center - half_extents, center + half_extents):
				return false
		else:
			var radius := float(obstacle["radius"])
			if _distance_squared_to_segment(center, start, goal) <= radius * radius + _EPSILON:
				return false
	return true


static func _segment_intersects_rect(start: Vector2, goal: Vector2, minimum: Vector2, maximum: Vector2) -> bool:
	var direction := goal - start
	var t_min := 0.0
	var t_max := 1.0
	for axis in range(2):
		var origin := start.x if axis == 0 else start.y
		var delta := direction.x if axis == 0 else direction.y
		var slab_min := minimum.x if axis == 0 else minimum.y
		var slab_max := maximum.x if axis == 0 else maximum.y
		if absf(delta) <= _EPSILON:
			if origin < slab_min - _EPSILON or origin > slab_max + _EPSILON:
				return false
			continue
		var inverse_delta := 1.0 / delta
		var near_t := (slab_min - origin) * inverse_delta
		var far_t := (slab_max - origin) * inverse_delta
		if near_t > far_t:
			var swap := near_t
			near_t = far_t
			far_t = swap
		t_min = maxf(t_min, near_t)
		t_max = minf(t_max, far_t)
		if t_min > t_max + _EPSILON:
			return false
	return true


static func _distance_squared_to_segment(point: Vector2, start: Vector2, goal: Vector2) -> float:
	var segment := goal - start
	var length_squared := segment.length_squared()
	if length_squared <= _EPSILON:
		return point.distance_squared_to(start)
	var fraction := clampf((point - start).dot(segment) / length_squared, 0.0, 1.0)
	return point.distance_squared_to(start + segment * fraction)


static func _smooth_path(points: Array[Vector2], obstacles: Array) -> Array[Vector2]:
	var smoothed: Array[Vector2] = []
	if points.is_empty():
		return smoothed
	smoothed.append(points[0])
	var anchor_index := 0
	var final_index := points.size() - 1
	while anchor_index < final_index:
		var next_index := final_index
		while next_index > anchor_index + 1:
			if _segment_is_clear(points[anchor_index], points[next_index], obstacles):
				break
			next_index -= 1
		if not _segment_is_clear(points[anchor_index], points[next_index], obstacles):
			var failed: Array[Vector2] = []
			return failed
		smoothed.append(points[next_index])
		anchor_index = next_index
	return smoothed


static func _append_unique_point(points: Array[Vector2], point: Vector2) -> void:
	if points.is_empty() or points[points.size() - 1].distance_squared_to(point) > _EPSILON * _EPSILON:
		points.append(point)


static func _to_intermediate_waypoints(points: Array[Vector2], start_y: float, goal_y: float) -> Array[Vector3]:
	var waypoints: Array[Vector3] = []
	if points.size() <= 2:
		return waypoints
	var horizontal_length := 0.0
	for index in range(1, points.size()):
		horizontal_length += points[index - 1].distance_to(points[index])
	var traversed := 0.0
	for index in range(1, points.size() - 1):
		traversed += points[index - 1].distance_to(points[index])
		var fraction := 0.0 if horizontal_length <= _EPSILON else traversed / horizontal_length
		var point := points[index]
		waypoints.append(Vector3(point.x, lerpf(start_y, goal_y, fraction), point.y))
	return waypoints


static func _path_length_3d(start: Vector3, waypoints: Array[Vector3], goal: Vector3) -> float:
	var length := 0.0
	var previous := start
	for waypoint in waypoints:
		length += previous.distance_to(waypoint)
		previous = waypoint
	length += previous.distance_to(goal)
	return length


static func _point_in_bounds_inclusive(point: Vector2, bounds: Rect2) -> bool:
	return (
		point.x >= bounds.position.x - _EPSILON
		and point.x <= bounds.end.x + _EPSILON
		and point.y >= bounds.position.y - _EPSILON
		and point.y <= bounds.end.y + _EPSILON
	)


static func _valid_rect(value: Rect2) -> bool:
	return (
		_valid_vector2(value.position)
		and _valid_vector2(value.size)
		and value.size.x > _EPSILON
		and value.size.y > _EPSILON
	)


static func _valid_vector2(value: Vector2) -> bool:
	return is_finite(value.x) and is_finite(value.y)


static func _valid_vector3(value: Vector3) -> bool:
	return is_finite(value.x) and is_finite(value.y) and is_finite(value.z)
