extends Node
class_name EmbodiedReflexController

signal reflex_started(event: Dictionary, result: Dictionary)
signal cognitive_event(event: Dictionary)

@export var enabled := false
@export var touch_reactions_enabled := false
@export_range(0.2, 5.0, 0.1) var reaction_hold_seconds := 1.4

var _action_adapter: Node
var _face_controller: Node
var _recovery_remaining := 0.0
var _last_result: Dictionary = {}


func bind(action_adapter: Node, face_controller: Node) -> bool:
	_action_adapter = action_adapter
	_face_controller = face_controller
	return _action_adapter != null and _face_controller != null


func _process(delta: float) -> void:
	advance_reflex(delta)


func handle_touch(event: Dictionary) -> Dictionary:
	if not enabled:
		return _reject("reflex_controller_disabled")
	if not touch_reactions_enabled:
		return _reject("touch_reactions_disabled_by_preference")
	if String(event.get("event", "")) != "touch":
		return _reject("not_a_touch_event")
	if _action_adapter == null or not _action_adapter.has_method("dispatch_touch_reflex"):
		return _reject("action_adapter_unavailable")

	var started_at := Time.get_ticks_usec()
	var result: Dictionary = _action_adapter.call("dispatch_touch_reflex", event)
	var elapsed_ms := float(Time.get_ticks_usec() - started_at) / 1000.0
	result["reflex_latency_ms"] = snappedf(elapsed_ms, 0.01)
	result["latency_budget_ms"] = 100
	result["within_latency_budget"] = elapsed_ms <= 100.0
	result["llm_waited"] = false
	_last_result = result.duplicate(true)
	if bool(result.get("accepted", false)):
		_recovery_remaining = reaction_hold_seconds
		reflex_started.emit(event, result)
		var followup := event.duplicate(true)
		followup["type"] = "reflex_observed"
		followup["immediate_reaction"] = String(result.get("expression", ""))
		followup["requires_cognitive_followup"] = true
		followup["llm_waited_for_reflex"] = false
		cognitive_event.emit(followup)
	return result


func advance_reflex(delta: float) -> void:
	if _recovery_remaining <= 0.0:
		return
	_recovery_remaining = maxf(0.0, _recovery_remaining - delta)
	if _recovery_remaining <= 0.0 and _face_controller != null and _face_controller.has_method("set_expression"):
		_face_controller.call("set_expression", "neutral", 1.0, 0.20)


func get_last_result() -> Dictionary:
	return _last_result.duplicate(true)


func _reject(reason: String) -> Dictionary:
	var result := {
		"accepted": false,
		"status": "rejected",
		"reason": reason,
		"adapter": "embodied_v1_reflex",
		"llm_waited": false,
	}
	_last_result = result.duplicate(true)
	return result
