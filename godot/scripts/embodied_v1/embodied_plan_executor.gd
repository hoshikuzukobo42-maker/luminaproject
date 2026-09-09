extends Node
class_name EmbodiedPlanExecutor

signal plan_started(goal: String, action_count: int)
signal action_finished(result: Dictionary)
signal speech_action_requested(action: Dictionary)
signal plan_completed(ok: bool, results: Array[Dictionary])

@export var enabled := false
@export_range(0.1, 5.0, 0.1) var sit_transition_seconds := 1.2
@export_range(0.1, 5.0, 0.1) var stand_transition_seconds := 1.2

var _action_adapter: Node
var _navigation_controller: Node
var _queue: Array[Dictionary] = []
var _results: Array[Dictionary] = []
var _active_action: Dictionary = {}
var _active_dispatch_result: Dictionary = {}
var _waiting_for := ""
var _wait_remaining := 0.0
var _running := false


func bind(action_adapter: Node, navigation_controller: Node) -> bool:
	_action_adapter = action_adapter
	_navigation_controller = navigation_controller
	if _navigation_controller != null and _navigation_controller.has_signal("navigation_finished"):
		var callback := Callable(self, "_on_navigation_finished")
		if not _navigation_controller.navigation_finished.is_connected(callback):
			_navigation_controller.navigation_finished.connect(callback)
	return _action_adapter != null and _navigation_controller != null


func _process(delta: float) -> void:
	advance_executor(delta)


func execute_plan(plan: Dictionary) -> bool:
	if not enabled or _running or _action_adapter == null:
		return false
	var raw_actions = plan.get("actions", [])
	if typeof(raw_actions) != TYPE_ARRAY or raw_actions.is_empty():
		return false
	_queue.clear()
	_results.clear()
	for raw_action in raw_actions:
		if typeof(raw_action) != TYPE_DICTIONARY:
			return false
		_queue.append((raw_action as Dictionary).duplicate(true))
	_running = true
	plan_started.emit(String(plan.get("goal", "")), _queue.size())
	_dispatch_next()
	return true


func advance_executor(delta: float) -> void:
	if not _running or _wait_remaining <= 0.0:
		return
	_wait_remaining = maxf(0.0, _wait_remaining - delta)
	if _wait_remaining > 0.0:
		return
	if _waiting_for == "sit_transition":
		_action_adapter.call(
			"dispatch",
			{
				"action_id": "%s_hold" % String(_active_action.get("action_id", "sit")),
				"type": "living_state",
				"source": "local_executor",
				"params": {"state": "sit"},
			}
		)
	elif _waiting_for == "stand_transition":
		_action_adapter.call(
			"dispatch",
			{
				"action_id": "%s_idle" % String(_active_action.get("action_id", "stand")),
				"type": "living_state",
				"source": "local_executor",
				"params": {"state": "idle"},
			}
		)
	_complete_active(true, "completed", _waiting_for)


func cancel(reason := "cancelled") -> void:
	if not _running:
		return
	if _navigation_controller != null and _navigation_controller.has_method("stop"):
		_navigation_controller.call("stop", reason)
	_complete_plan(false)


func is_running() -> bool:
	return _running


func get_results() -> Array[Dictionary]:
	return _results.duplicate(true)


func _dispatch_next() -> void:
	_waiting_for = ""
	_wait_remaining = 0.0
	_active_action = {}
	_active_dispatch_result = {}
	if _queue.is_empty():
		_complete_plan(true)
		return
	_active_action = _queue.pop_front()
	var action_type := String(_active_action.get("type", ""))
	if action_type == "speak":
		speech_action_requested.emit(_active_action)
	var result: Dictionary = _action_adapter.call("dispatch", _active_action)
	_active_dispatch_result = result.duplicate(true)
	if not bool(result.get("accepted", false)):
		_results.append(result)
		action_finished.emit(result)
		_complete_plan(false)
		return
	match action_type:
		"move_to", "follow":
			_waiting_for = "navigation"
		"sit":
			_waiting_for = "sit_transition"
			_wait_remaining = sit_transition_seconds
		"stand":
			_waiting_for = "stand_transition"
			_wait_remaining = stand_transition_seconds
		_:
			var duration := maxf(0.0, float(result.get("duration_seconds", 0.0)))
			if duration > 0.0:
				_waiting_for = "timed_action"
				_wait_remaining = duration
			else:
				_complete_active(true, "completed", String(result.get("reason", "accepted")))


func _on_navigation_finished(action_id: String, ok: bool, status: String, detail: String) -> void:
	if not _running or _waiting_for != "navigation":
		return
	if action_id != String(_active_action.get("action_id", "")):
		return
	_complete_active(ok, status, detail)


func _complete_active(ok: bool, status: String, detail: String) -> void:
	var result := _active_dispatch_result.duplicate(true)
	result["accepted"] = ok
	result["action_id"] = String(_active_action.get("action_id", ""))
	result["type"] = String(_active_action.get("type", ""))
	result["status"] = status
	result["reason"] = detail
	var raw_params = _active_action.get("params", {})
	result["params"] = raw_params.duplicate(true) if typeof(raw_params) == TYPE_DICTIONARY else {}
	result["executor"] = "embodied_v1"
	_results.append(result)
	action_finished.emit(result)
	_active_action = {}
	_active_dispatch_result = {}
	_waiting_for = ""
	_wait_remaining = 0.0
	if ok:
		_dispatch_next()
	else:
		_complete_plan(false)


func _complete_plan(ok: bool) -> void:
	_running = false
	_queue.clear()
	_active_action = {}
	_active_dispatch_result = {}
	_waiting_for = ""
	_wait_remaining = 0.0
	plan_completed.emit(ok, _results.duplicate(true))
