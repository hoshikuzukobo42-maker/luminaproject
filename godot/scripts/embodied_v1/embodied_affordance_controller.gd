extends Node
class_name EmbodiedAffordanceController

signal object_state_changed(object_id: String, state: Dictionary)
signal contact_state_changed(state: String)

@export var enabled := false

var _avatar_root: Node
var _skeleton: Skeleton3D
var _right_hand_attachment: BoneAttachment3D
var _objects: Dictionary = {}
var _anchors: Dictionary = {}
var _held_object_id := ""
var _contact_state := "released"


func bind_avatar(avatar_root: Node) -> bool:
	_avatar_root = avatar_root
	_skeleton = _find_skeleton(avatar_root)
	if _skeleton == null or _skeleton.find_bone("RightHand") < 0:
		return false
	_right_hand_attachment = BoneAttachment3D.new()
	_right_hand_attachment.name = "EmbodiedRightHandAttachment"
	_right_hand_attachment.bone_name = "RightHand"
	_skeleton.add_child(_right_hand_attachment)
	return true


func register_anchor(anchor_id: String, anchor: Node3D) -> bool:
	var normalized := anchor_id.strip_edges().to_lower()
	if normalized.is_empty() or anchor == null:
		return false
	_anchors[normalized] = anchor
	return true


func register_object(
	object_id: String,
	node: Node3D,
	affordances: Array,
	home_anchor := ""
) -> bool:
	var normalized := object_id.strip_edges().to_lower()
	if normalized.is_empty() or node == null:
		return false
	var allowed: Array[String] = []
	for affordance in affordances:
		allowed.append(String(affordance).strip_edges().to_lower())
	_objects[normalized] = {
		"node": node,
		"affordances": allowed,
		"home_parent": node.get_parent(),
		"home_transform": node.global_transform,
		"home_anchor": String(home_anchor).strip_edges().to_lower(),
		"state": "resting",
	}
	return true


func perform(action_type: String, params: Dictionary) -> Dictionary:
	if not enabled:
		return _result(false, action_type, "disabled", "affordance_controller_disabled")
	var action := action_type.strip_edges().to_lower()
	if action in ["offer_hand", "hold_hand", "release_hand"]:
		return _perform_contact(action)
	var object_id := String(params.get("object", params.get("target", ""))).strip_edges().to_lower()
	if object_id.is_empty():
		return _result(false, action, "rejected", "missing_object")
	if not _objects.has(object_id):
		return _result(false, action, "rejected", "unknown_object:%s" % object_id)
	var record: Dictionary = _objects[object_id]
	var affordances: Array = record.get("affordances", [])
	if not affordances.has(action) and not (
		action == "use_object" and (affordances.has("use") or affordances.has("read") or affordances.has("drink"))
	):
		return _result(false, action, "rejected", "affordance_unavailable:%s:%s" % [object_id, action])
	match action:
		"reach", "inspect", "use_object":
			return _object_result(true, action, object_id, "accepted", "object_action_ready")
		"pick_up":
			return _pick_up(object_id)
		"put_down":
			return _put_down(object_id, params)
	return _result(false, action, "rejected", "unsupported_affordance_action:%s" % action)


func release_all() -> void:
	if not _held_object_id.is_empty():
		_put_down(_held_object_id, {})
	_contact_state = "released"
	contact_state_changed.emit(_contact_state)


func get_status() -> Dictionary:
	var states := {}
	for object_id in _objects.keys():
		var record: Dictionary = _objects[object_id]
		states[object_id] = {
			"state": record.get("state", "unknown"),
			"affordances": record.get("affordances", []),
		}
	return {
		"enabled": enabled,
		"bound": _right_hand_attachment != null,
		"held_object": _held_object_id,
		"contact_state": _contact_state,
		"objects": states,
		"raw_coordinate_input_allowed": false,
	}


func _pick_up(object_id: String) -> Dictionary:
	if _right_hand_attachment == null:
		return _object_result(false, "pick_up", object_id, "failed", "right_hand_attachment_unavailable")
	if not _held_object_id.is_empty() and _held_object_id != object_id:
		return _object_result(false, "pick_up", object_id, "rejected", "right_hand_occupied:%s" % _held_object_id)
	var record: Dictionary = _objects[object_id]
	var node := record.get("node") as Node3D
	if node == null:
		return _object_result(false, "pick_up", object_id, "failed", "object_node_missing")
	node.reparent(_right_hand_attachment, false)
	node.position = Vector3(0.0, 0.10, 0.02)
	node.rotation = Vector3.ZERO
	record["state"] = "held"
	_objects[object_id] = record
	_held_object_id = object_id
	object_state_changed.emit(object_id, {"state": "held", "held_by": "right_hand"})
	return _object_result(true, "pick_up", object_id, "accepted", "attached_to_right_hand")


func _put_down(object_id: String, params: Dictionary) -> Dictionary:
	if _held_object_id != object_id:
		return _object_result(false, "put_down", object_id, "rejected", "object_not_held")
	var record: Dictionary = _objects[object_id]
	var node := record.get("node") as Node3D
	var parent := record.get("home_parent") as Node
	if node == null or parent == null:
		return _object_result(false, "put_down", object_id, "failed", "object_home_missing")
	node.reparent(parent, true)
	var anchor_id := String(params.get("anchor", record.get("home_anchor", ""))).strip_edges().to_lower()
	if not anchor_id.is_empty() and _anchors.has(anchor_id):
		node.global_transform = (_anchors[anchor_id] as Node3D).global_transform
	else:
		node.global_transform = record.get("home_transform", node.global_transform)
	record["state"] = "resting"
	_objects[object_id] = record
	_held_object_id = ""
	object_state_changed.emit(object_id, {"state": "resting", "anchor": anchor_id})
	return _object_result(true, "put_down", object_id, "accepted", "returned_to_anchor:%s" % anchor_id)


func _perform_contact(action: String) -> Dictionary:
	match action:
		"offer_hand":
			_contact_state = "offered"
		"hold_hand":
			if _contact_state not in ["offered", "held"]:
				return _result(false, action, "rejected", "hand_must_be_offered_first")
			_contact_state = "held"
		"release_hand":
			_contact_state = "released"
	contact_state_changed.emit(_contact_state)
	var result := _result(true, action, "accepted", "contact_state:%s" % _contact_state)
	result["contact_state"] = _contact_state
	return result


func _object_result(
	accepted: bool,
	action: String,
	object_id: String,
	status: String,
	reason: String
) -> Dictionary:
	var result := _result(accepted, action, status, reason)
	result["object"] = object_id
	return result


func _result(accepted: bool, action: String, status: String, reason: String) -> Dictionary:
	return {
		"accepted": accepted,
		"type": action,
		"status": status,
		"reason": reason,
		"controller": "embodied_affordance",
	}


func _find_skeleton(node: Node) -> Skeleton3D:
	if node is Skeleton3D:
		return node as Skeleton3D
	for child in node.get_children():
		var found := _find_skeleton(child)
		if found != null:
			return found
	return null
