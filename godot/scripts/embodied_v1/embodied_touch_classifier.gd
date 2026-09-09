extends Node
class_name EmbodiedTouchClassifier

signal touch_started(zone: String)
signal touch_event(event: Dictionary)

const ALLOWED_ZONES := ["head", "hair", "left_hand", "right_hand", "hand", "shoulder", "personal_space"]

@export var enabled := false
@export_range(50, 500, 10) var tap_max_duration_ms := 240
@export_range(0.001, 0.10, 0.001) var tap_max_path := 0.025
@export_range(150, 1200, 10) var pat_min_duration_ms := 280
@export_range(1, 8, 1) var pat_min_reversals := 2
@export_range(0.01, 0.50, 0.005) var stroke_min_path := 0.075
@export_range(200, 2000, 10) var hold_min_duration_ms := 700
@export_range(0.001, 0.10, 0.001) var hold_max_path := 0.035
@export_range(0.05, 2.0, 0.05) var poke_min_velocity := 0.35

var _active := false
var _zone := ""
var _started_at_ms := 0
var _last_at_ms := 0
var _start_position := Vector2.ZERO
var _last_position := Vector2.ZERO
var _last_direction := Vector2.ZERO
var _path_length := 0.0
var _reversals := 0
var _sample_count := 0


func begin_touch(zone: String, normalized_position: Vector2, timestamp_ms := -1) -> bool:
	if not enabled or _active:
		return false
	var normalized_zone := zone.strip_edges().to_lower()
	if not ALLOWED_ZONES.has(normalized_zone):
		return false
	var now := _resolve_time(timestamp_ms)
	_active = true
	_zone = normalized_zone
	_started_at_ms = now
	_last_at_ms = now
	_start_position = normalized_position
	_last_position = normalized_position
	_last_direction = Vector2.ZERO
	_path_length = 0.0
	_reversals = 0
	_sample_count = 1
	touch_started.emit(_zone)
	return true


func update_touch(normalized_position: Vector2, timestamp_ms := -1) -> bool:
	if not enabled or not _active:
		return false
	var now := maxi(_last_at_ms, _resolve_time(timestamp_ms))
	var segment := normalized_position - _last_position
	var segment_length := segment.length()
	if segment_length > 0.00001:
		var direction := segment / segment_length
		if _last_direction.length_squared() > 0.0 and direction.dot(_last_direction) < -0.35:
			_reversals += 1
		_last_direction = direction
		_path_length += segment_length
	_last_position = normalized_position
	_last_at_ms = now
	_sample_count += 1
	return true


func end_touch(normalized_position: Vector2, timestamp_ms := -1) -> Dictionary:
	if not enabled or not _active:
		return {}
	update_touch(normalized_position, timestamp_ms)
	var duration_ms := maxi(1, _last_at_ms - _started_at_ms)
	var duration_seconds := float(duration_ms) / 1000.0
	var velocity := _path_length / maxf(0.001, duration_seconds)
	var gesture := _classify(duration_ms, velocity)
	var confidence := _confidence_for(gesture, duration_ms, velocity)
	var event := {
		"event": "touch",
		"zone": _zone,
		"gesture": gesture,
		"duration_ms": duration_ms,
		"velocity": snappedf(velocity, 0.001),
		"confidence": snappedf(confidence, 0.01),
		"path_length": snappedf(_path_length, 0.001),
		"samples": _sample_count,
		"source": "mouse",
	}
	_active = false
	touch_event.emit(event)
	return event


func cancel_touch() -> void:
	_active = false
	_zone = ""


func is_touch_active() -> bool:
	return _active


func _classify(duration_ms: int, velocity: float) -> String:
	if duration_ms >= hold_min_duration_ms and _path_length <= hold_max_path:
		return "hold"
	if duration_ms >= pat_min_duration_ms and _reversals >= pat_min_reversals:
		return "pat"
	if duration_ms <= tap_max_duration_ms and _path_length <= tap_max_path:
		return "tap"
	if duration_ms <= tap_max_duration_ms and velocity >= poke_min_velocity:
		return "poke"
	if _path_length >= stroke_min_path:
		return "stroke"
	return "touch"


func _confidence_for(gesture: String, duration_ms: int, velocity: float) -> float:
	match gesture:
		"pat":
			return clampf(0.70 + 0.08 * float(_reversals - pat_min_reversals), 0.70, 0.96)
		"stroke":
			return clampf(0.68 + _path_length, 0.68, 0.94)
		"hold":
			return clampf(0.72 + float(duration_ms - hold_min_duration_ms) / 4000.0, 0.72, 0.96)
		"tap":
			return 0.88
		"poke":
			return clampf(0.72 + velocity * 0.08, 0.72, 0.94)
	return 0.55


func _resolve_time(timestamp_ms: int) -> int:
	return Time.get_ticks_msec() if timestamp_ms < 0 else timestamp_ms
