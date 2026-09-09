extends Node
class_name EmbodiedBehaviorController

signal behavior_selected(behavior_id: String, scores: Dictionary)

@export var enabled := false
@export_range(0.1, 2.0, 0.05) var decision_interval_seconds := 0.25
@export_range(3.0, 60.0, 1.0) var first_idle_seconds := 8.0
@export var lifestyle_activities_enabled := true

var _action_adapter: Node
var _plan_executor: Node
var _navigation_controller: Node
var _gaze_controller: Node
var _user_present := true
var _cognitive_busy := false
var _decision_elapsed := 0.0
var _idle_elapsed := 0.0
var _next_idle_seconds := 8.0
var _selection_index := 0
var _last_behavior := "do_nothing"
var _last_scores: Dictionary = {}


func bind(
	action_adapter: Node,
	plan_executor: Node,
	navigation_controller: Node,
	gaze_controller: Node
) -> bool:
	_action_adapter = action_adapter
	_plan_executor = plan_executor
	_navigation_controller = navigation_controller
	_gaze_controller = gaze_controller
	_next_idle_seconds = first_idle_seconds
	return (
		_action_adapter != null
		and _plan_executor != null
		and _navigation_controller != null
		and _gaze_controller != null
	)


func _process(delta: float) -> void:
	advance_behavior(delta)


func set_user_present(present: bool) -> void:
	_user_present = present
	if present:
		note_activity("user_present")


func set_cognitive_busy(busy: bool) -> void:
	_cognitive_busy = busy
	_idle_elapsed = 0.0
	if busy:
		_last_behavior = "do_nothing"


func note_activity(_reason := "activity") -> void:
	_idle_elapsed = 0.0
	_next_idle_seconds = first_idle_seconds


func advance_behavior(delta: float) -> void:
	if not enabled:
		return
	_decision_elapsed += delta
	_idle_elapsed += delta
	if _decision_elapsed < decision_interval_seconds:
		return
	_decision_elapsed = 0.0
	var busy := _is_busy()
	_last_scores = {
		"do_nothing": 1.0 if busy or _idle_elapsed < _next_idle_seconds else 0.15,
		"idle_breathing": 0.72 if not busy and _idle_elapsed >= _next_idle_seconds else 0.0,
		"idle_weight_shift": 0.70 if not busy and _idle_elapsed >= _next_idle_seconds else 0.0,
		"look_at_user": 0.64 if not busy and _user_present and _idle_elapsed >= _next_idle_seconds + 5.0 else 0.0,
		"look_around": 0.58 if not busy and _idle_elapsed >= _next_idle_seconds + 9.0 else 0.0,
		"window_pause": 0.46 if not busy and _idle_elapsed >= _next_idle_seconds and lifestyle_activities_enabled and _can_run_activity("window_pause") else 0.0,
		"browse_book": 0.42 if not busy and _idle_elapsed >= _next_idle_seconds and lifestyle_activities_enabled and _can_run_activity("browse_book") else 0.0,
	}
	if busy:
		# A long navigation or object plan must not immediately trigger another
		# autonomous action on completion.
		_idle_elapsed = 0.0
		_last_behavior = "do_nothing"
		return
	if _idle_elapsed < _next_idle_seconds:
		_last_behavior = "do_nothing"
		return
	var cycle := ["idle_breathing", "idle_weight_shift", "look_at_user", "look_around"]
	if lifestyle_activities_enabled:
		cycle.append_array(["window_pause", "browse_book"])
	var selected := "do_nothing"
	for _attempt in range(cycle.size()):
		var candidate := String(cycle[_selection_index % cycle.size()])
		_selection_index += 1
		if candidate == "look_at_user" and not _user_present:
			continue
		if not _can_run_activity(candidate):
			continue
		selected = candidate
		break
	var started := selected != "do_nothing" and _execute(selected)
	if not started:
		selected = "do_nothing"
	_last_behavior = selected
	_idle_elapsed = 0.0
	_next_idle_seconds = (
		30.0 + float((_selection_index * 7) % 16)
		if selected in ["window_pause", "browse_book"]
		else 7.0 + float((_selection_index * 3) % 6)
	)
	behavior_selected.emit(selected, _last_scores.duplicate(true))


func request_activity(activity_id: String) -> bool:
	var normalized := activity_id.strip_edges().to_lower()
	if not enabled or _is_busy() or not _can_run_activity(normalized):
		return false
	var started := _execute(normalized)
	if started:
		_last_behavior = normalized
		_idle_elapsed = 0.0
		_next_idle_seconds = 30.0 if normalized in ["window_pause", "browse_book"] else first_idle_seconds
		behavior_selected.emit(normalized, _last_scores.duplicate(true))
	return started


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"user_present": _user_present,
		"cognitive_busy": _cognitive_busy,
		"idle_elapsed": _idle_elapsed,
		"next_idle_seconds": _next_idle_seconds,
		"last_behavior": _last_behavior,
		"last_scores": _last_scores.duplicate(true),
		"has_do_nothing_candidate": true,
		"lifestyle_activities_enabled": lifestyle_activities_enabled,
		"autonomous_activity_ids": [
			"do_nothing",
			"idle_breathing",
			"idle_weight_shift",
			"look_at_user",
			"look_around",
			"window_pause",
			"browse_book",
		],
		"decision_hz": 1.0 / decision_interval_seconds,
	}


func _is_busy() -> bool:
	return (
		_cognitive_busy
		or (_plan_executor != null and bool(_plan_executor.call("is_running")))
		or (_navigation_controller != null and bool(_navigation_controller.call("is_moving")))
	)


func _can_run_activity(behavior_id: String) -> bool:
	if behavior_id in ["do_nothing", "idle_breathing", "idle_weight_shift", "look_around"]:
		return true
	if behavior_id == "look_at_user":
		return _user_present
	if _navigation_controller == null or not _navigation_controller.has_method("get_anchor_ids"):
		return false
	var anchor_ids: PackedStringArray = _navigation_controller.call("get_anchor_ids")
	if behavior_id == "window_pause":
		return anchor_ids.has("window_seat")
	if behavior_id == "browse_book":
		return anchor_ids.has("book") and anchor_ids.has("book_rest")
	return false


func _execute(behavior_id: String) -> bool:
	match behavior_id:
		"idle_breathing", "idle_weight_shift":
			var result: Dictionary = _action_adapter.call(
				"dispatch",
				{
					"action_id": "utility_%s_%d" % [behavior_id, Time.get_ticks_msec()],
					"type": "living_state",
					"source": "local_utility_ai",
					"params": {"state": behavior_id},
				}
			)
			return bool(result.get("accepted", false))
		"look_at_user":
			return bool(_gaze_controller.call("look_at", "user", 900))
		"look_around":
			var result: Dictionary = _action_adapter.call(
				"dispatch",
				{
					"action_id": "utility_look_around_%d" % Time.get_ticks_msec(),
					"type": "gesture",
					"source": "local_utility_ai",
					"params": {"name": "look_around"},
				}
			)
			return bool(result.get("accepted", false))
		"window_pause":
			return bool(_plan_executor.call("execute_plan", _window_pause_plan()))
		"browse_book":
			return bool(_plan_executor.call("execute_plan", _browse_book_plan()))
	return false


func _window_pause_plan() -> Dictionary:
	var stamp := Time.get_ticks_msec()
	var actions: Array[Dictionary] = []
	if String(_action_adapter.call("get_current_posture")) != "standing":
		actions.append({"action_id": "utility_stand_%d" % stamp, "type": "stand", "source": "local_utility_ai", "params": {}})
	actions.append({
		"action_id": "utility_window_move_%d" % stamp,
		"type": "move_to",
		"source": "local_utility_ai",
		"params": {"anchor": "window_seat", "speed": "walk", "timeout_ms": 10000, "stop_distance": 0.22},
	})
	actions.append({"action_id": "utility_window_sit_%d" % stamp, "type": "sit", "source": "local_utility_ai", "params": {"anchor": "window_seat"}})
	var anchor_ids: PackedStringArray = _navigation_controller.call("get_anchor_ids")
	if anchor_ids.has("window_view"):
		actions.append({"action_id": "utility_window_look_%d" % stamp, "type": "look_at", "source": "local_utility_ai", "params": {"target": "window_view", "duration_ms": 2600}})
	else:
		actions.append({"action_id": "utility_window_look_%d" % stamp, "type": "gesture", "source": "local_utility_ai", "params": {"name": "look_around"}})
	return {"goal": "quietly_spend_time_at_window", "source": "local_utility_ai", "actions": actions}


func _browse_book_plan() -> Dictionary:
	var stamp := Time.get_ticks_msec()
	var actions: Array[Dictionary] = []
	if String(_action_adapter.call("get_current_posture")) != "standing":
		actions.append({"action_id": "utility_book_stand_%d" % stamp, "type": "stand", "source": "local_utility_ai", "params": {}})
	actions.append_array([
		{"action_id": "utility_book_move_%d" % stamp, "type": "move_to", "source": "local_utility_ai", "params": {"anchor": "book", "speed": "walk", "timeout_ms": 10000, "stop_distance": 0.58}},
		{"action_id": "utility_book_reach_%d" % stamp, "type": "reach", "source": "local_utility_ai", "params": {"object": "book", "duration_ms": 850}},
		{"action_id": "utility_book_pick_%d" % stamp, "type": "pick_up", "source": "local_utility_ai", "params": {"object": "book", "duration_ms": 1800}},
		{"action_id": "utility_book_inspect_%d" % stamp, "type": "inspect", "source": "local_utility_ai", "params": {"object": "book", "duration_ms": 1800}},
		{"action_id": "utility_book_read_%d" % stamp, "type": "use_object", "source": "local_utility_ai", "params": {"object": "book", "use": "read", "duration_ms": 2200}},
		{"action_id": "utility_book_put_%d" % stamp, "type": "put_down", "source": "local_utility_ai", "params": {"object": "book", "anchor": "book_rest", "duration_ms": 1600}},
	])
	return {"goal": "browse_book_without_interrupting_user", "source": "local_utility_ai", "actions": actions}
