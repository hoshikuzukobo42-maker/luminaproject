extends Node
class_name LuminaLivingHubInteractionRay

## First-person interaction ray + highlight routing for Living Hub.

signal focus_changed(target: Node)
signal interact_pressed(target: Node)

@export var ray_length := 3.2
@export var max_focus_distance := 2.6

var _camera: Camera3D
var _actor: Node
var _ray: RayCast3D
var _focused: Node
var _prompt_callback: Callable


func setup(camera: Camera3D, actor: Node) -> void:
	_camera = camera
	_actor = actor
	if _ray == null:
		_ray = RayCast3D.new()
		_ray.name = "LivingHubInteractRay"
		_ray.enabled = true
		_ray.collide_with_areas = true
		_ray.collide_with_bodies = true
		_ray.collision_mask = 0xFFFFFFFF
	_ray.target_position = Vector3(0.0, 0.0, -ray_length)
	if _camera != null:
		if _ray.get_parent() != _camera:
			if _ray.get_parent() != null:
				_ray.get_parent().remove_child(_ray)
			_camera.add_child(_ray)
		_ray.position = Vector3.ZERO
	elif _ray.get_parent() == null:
		add_child(_ray)


func set_prompt_callback(callback: Callable) -> void:
	_prompt_callback = callback


func get_focused() -> Node:
	return _focused


func request_interact() -> void:
	if _focused != null and _focused.has_method("can_interact") and _focused.call("can_interact", _actor):
		interact_pressed.emit(_focused)
		if _focused.has_method("interact"):
			_focused.call("interact", _actor)


func _physics_process(_delta: float) -> void:
	if _ray == null or _camera == null:
		return
	var next := _resolve_focus()
	if next != _focused:
		if _focused != null and _focused.has_method("set_highlighted"):
			_focused.call("set_highlighted", false)
		_focused = next
		if _focused != null and _focused.has_method("set_highlighted"):
			_focused.call("set_highlighted", true)
		focus_changed.emit(_focused)
		_emit_prompt()


func _resolve_focus() -> Node:
	var best: Node = null
	var best_score := INF
	# Prefer ray hit.
	if _ray.is_colliding():
		var collider := _ray.get_collider() as Node
		var interactable := _find_interactable(collider)
		if interactable != null and interactable.call("can_interact", _actor):
			return interactable
	# Fallback: nearest interactable in front of the camera.
	if _actor is Node3D and _camera != null:
		var actor_pos := (_actor as Node3D).global_position
		var forward := -_camera.global_transform.basis.z
		forward.y = 0.0
		if forward.length_squared() < 0.0001:
			forward = Vector3(0, 0, -1)
		else:
			forward = forward.normalized()
		for node in get_tree().get_nodes_in_group("lumina_living_hub_interactable"):
			if node == null or not is_instance_valid(node):
				continue
			if not node.has_method("can_interact") or not node.call("can_interact", _actor):
				continue
			var target_pos := (node as Node3D).global_position
			var to_target := target_pos - actor_pos
			to_target.y = 0.0
			var distance := to_target.length()
			if distance > max_focus_distance or distance < 0.01:
				continue
			var facing := forward.dot(to_target.normalized())
			if facing < 0.45:
				continue
			# Require a clear line of sight so props behind walls don't steal focus.
			var from := actor_pos + Vector3(0.0, 1.35, 0.0)
			var to := target_pos + Vector3(0.0, 0.9, 0.0)
			var space := _camera.get_world_3d().direct_space_state
			if space != null:
				var query := PhysicsRayQueryParameters3D.create(from, to)
				query.collide_with_areas = false
				query.collide_with_bodies = true
				query.exclude = []
				if _actor is CollisionObject3D:
					query.exclude = [(_actor as CollisionObject3D).get_rid()]
				var hit := space.intersect_ray(query)
				if not hit.is_empty():
					var hit_node := hit.get("collider") as Node
					if _find_interactable(hit_node) != node:
						continue
			# Prefer objects the player is looking toward; break ties by distance.
			var score := distance / maxf(facing, 0.25)
			if score < best_score:
				best = node
				best_score = score
	return best


func _find_interactable(node: Node) -> Node:
	var cursor := node
	while cursor != null:
		if cursor.is_in_group("lumina_living_hub_interactable"):
			return cursor
		if cursor.has_method("get_interaction_label") and cursor.has_method("interact"):
			return cursor
		cursor = cursor.get_parent()
	return null


func _emit_prompt() -> void:
	if not _prompt_callback.is_valid():
		return
	if _focused != null and _focused.has_method("get_interaction_label"):
		_prompt_callback.call(str(_focused.call("get_interaction_label")))
	else:
		_prompt_callback.call("")
