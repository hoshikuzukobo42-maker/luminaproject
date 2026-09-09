extends RefCounted
class_name LuminaPresenceStuckGuard

## Detects locomotion stalls and proposes a safe recovery pose.

signal stuck_detected(detail: Dictionary)
signal recovered(detail: Dictionary)

var stuck_seconds := 2.0
var min_progress := 0.04
var max_recoveries_per_behavior := 3

var _enabled := true
var _moving := false
var _last_pos := Vector3.ZERO
var _stall_timer := 0.0
var _recovery_count := 0
var _last_reason := ""


func reset_behavior() -> void:
	_recovery_count = 0
	_stall_timer = 0.0
	_last_reason = ""
	_moving = false


func begin_move(position: Vector3) -> void:
	_moving = true
	_last_pos = position
	_stall_timer = 0.0


func end_move() -> void:
	_moving = false
	_stall_timer = 0.0


func observe(delta: float, position: Vector3, moving: bool) -> Dictionary:
	if not _enabled:
		return {"stuck": false}
	if not moving:
		_moving = false
		_stall_timer = 0.0
		_last_pos = position
		return {"stuck": false}
	if not _moving:
		begin_move(position)
		return {"stuck": false}
	var traveled := Vector3(position.x - _last_pos.x, 0.0, position.z - _last_pos.z).length()
	if traveled >= min_progress:
		_last_pos = position
		_stall_timer = 0.0
		return {"stuck": false}
	_stall_timer += delta
	if _stall_timer < stuck_seconds:
		return {"stuck": false, "stall_timer": _stall_timer}
	_recovery_count += 1
	_last_reason = "path_stall"
	var detail := {
		"stuck": true,
		"reason": _last_reason,
		"stall_timer": _stall_timer,
		"position": position,
		"recovery_index": _recovery_count,
		"exhausted": _recovery_count > max_recoveries_per_behavior,
	}
	stuck_detected.emit(detail)
	_stall_timer = 0.0
	_last_pos = position
	return detail


func mark_recovered(safe_position: Vector3, detail: Dictionary = {}) -> Dictionary:
	var payload := detail.duplicate(true)
	payload["recovered_to"] = safe_position
	payload["recovery_index"] = _recovery_count
	recovered.emit(payload)
	_stall_timer = 0.0
	_last_pos = safe_position
	return payload


func get_status() -> Dictionary:
	return {
		"enabled": _enabled,
		"moving": _moving,
		"stall_timer": _stall_timer,
		"recovery_count": _recovery_count,
		"last_reason": _last_reason,
		"max_recoveries_per_behavior": max_recoveries_per_behavior,
	}
