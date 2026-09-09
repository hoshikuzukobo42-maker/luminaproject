extends Area3D
class_name LuminaLivingHubInteractable

## Common interaction contract for Living Hub props.
## Public methods: get_interaction_label / can_interact / interact / cancel_interaction

signal interacted(actor: Node)
signal interaction_cancelled(actor: Node)

@export var interactable_id: String = ""
@export var interaction_label: String = "調べる"
@export var interaction_radius: float = 1.8
@export var highlight_color: Color = Color(0.35, 0.85, 1.0, 0.55)
@export var enabled: bool = true

var _highlight: MeshInstance3D
var _highlighted := false
var _busy := false
var _active_actor: Node


func _ready() -> void:
	monitoring = true
	monitorable = true
	collision_layer = 2
	collision_mask = 0
	if interactable_id.is_empty():
		interactable_id = name.to_snake_case()
	_ensure_collision()
	_ensure_highlight()
	add_to_group("lumina_living_hub_interactable")


func get_interaction_label() -> String:
	return interaction_label


func can_interact(actor: Node) -> bool:
	if not enabled or _busy:
		return false
	if actor == null or not is_instance_valid(actor):
		return false
	if actor is Node3D:
		return global_position.distance_to((actor as Node3D).global_position) <= interaction_radius + 0.35
	return true


func interact(actor: Node) -> void:
	if not can_interact(actor):
		return
	_busy = true
	_active_actor = actor
	interacted.emit(actor)
	_on_interact(actor)
	# Safety: if the hub handler forgets release_busy, unlock after a beat.
	get_tree().create_timer(0.35).timeout.connect(func() -> void:
		if _busy and _active_actor == actor:
			release_busy()
	, CONNECT_ONE_SHOT)


func cancel_interaction(actor: Node) -> void:
	if _active_actor != null and actor != null and actor != _active_actor:
		return
	_busy = false
	_active_actor = null
	interaction_cancelled.emit(actor)
	_on_cancel(actor)


func set_highlighted(active: bool) -> void:
	_highlighted = active
	if _highlight != null:
		_highlight.visible = active


func is_highlighted() -> bool:
	return _highlighted


func release_busy() -> void:
	_busy = false
	_active_actor = null


func _on_interact(_actor: Node) -> void:
	pass


func _on_cancel(_actor: Node) -> void:
	pass


func _ensure_collision() -> void:
	if get_child_count() > 0:
		for child in get_children():
			if child is CollisionShape3D:
				return
	var shape_node := CollisionShape3D.new()
	shape_node.name = "InteractShape"
	var sphere := SphereShape3D.new()
	sphere.radius = interaction_radius
	shape_node.shape = sphere
	add_child(shape_node)


func _ensure_highlight() -> void:
	_highlight = get_node_or_null("HighlightRing") as MeshInstance3D
	if _highlight != null:
		_highlight.visible = _highlighted
		return
	# Always-on soft floor disc so interactables are discoverable before focus.
	if get_node_or_null("FloorDisc") == null:
		var disc := MeshInstance3D.new()
		disc.name = "FloorDisc"
		var cyl := CylinderMesh.new()
		cyl.top_radius = 0.22
		cyl.bottom_radius = 0.22
		cyl.height = 0.02
		cyl.radial_segments = 24
		disc.mesh = cyl
		disc.position.y = 0.02
		var disc_mat := StandardMaterial3D.new()
		disc_mat.albedo_color = Color(highlight_color.r, highlight_color.g, highlight_color.b, 0.18)
		disc_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		disc_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		disc.material_override = disc_mat
		add_child(disc)
	_highlight = MeshInstance3D.new()
	_highlight.name = "HighlightRing"
	var mesh := TorusMesh.new()
	mesh.inner_radius = 0.30
	mesh.outer_radius = 0.42
	mesh.rings = 12
	mesh.ring_segments = 24
	_highlight.mesh = mesh
	_highlight.position.y = 0.04
	var mat := StandardMaterial3D.new()
	mat.albedo_color = highlight_color
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.emission_enabled = true
	mat.emission = highlight_color
	mat.emission_energy_multiplier = 1.4
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_highlight.material_override = mat
	_highlight.visible = false
	add_child(_highlight)
