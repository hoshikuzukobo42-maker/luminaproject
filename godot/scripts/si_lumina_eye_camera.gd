extends Camera3D

@export var target_controller_path: NodePath
@export var eye_label: String = "left"
@export var eye_forward_offset: float = 0.0
@export var align_to_lumina_gaze: bool = false


func _ready() -> void:
	# Imported VRM eye bones use local +Y along the optical axis, -Z up.
	# Follow the actual animated bone; do not rotate the camera directly to an
	# intended target that the head/eyes have not physically looked at yet.
	if not align_to_lumina_gaze:
		var optical_axis := Vector3.BACK if eye_label == "head_fallback" else Vector3.UP
		var optical_up := Vector3.UP if eye_label == "head_fallback" else Vector3.FORWARD
		position = optical_axis * eye_forward_offset
		basis = Basis.looking_at(optical_axis, optical_up)


func _process(_delta: float) -> void:
	if not align_to_lumina_gaze:
		return
	var target_controller := _get_target_controller()
	if target_controller == null or not target_controller.has_method("get_view_forward"):
		return
	var raw_forward: Variant = target_controller.call("get_view_forward")
	if typeof(raw_forward) != TYPE_VECTOR3:
		return
	var forward := raw_forward as Vector3
	if forward.length() <= 0.01:
		return
	forward = forward.normalized()
	var parent_node := get_parent() as Node3D
	if parent_node == null:
		return
	global_position = parent_node.global_position + forward * maxf(0.0, eye_forward_offset)
	look_at(global_position + forward, Vector3.UP)


func _get_target_controller() -> Node:
	if String(target_controller_path).is_empty():
		return null
	return get_node_or_null(target_controller_path)
