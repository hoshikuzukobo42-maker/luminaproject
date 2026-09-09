extends Node
class_name EmbodiedAudioStatusClient

signal status_received(payload: Dictionary)
signal utterance_completed(payload: Dictionary)
signal request_failed(status_code: int, detail: String)

@export var enabled := false
@export var base_url := "http://127.0.0.1:8791"
@export_range(0.20, 5.0, 0.05) var poll_interval_seconds := 0.40
@export_range(1.0, 10.0, 0.5) var timeout_seconds := 2.0

var _http: HTTPRequest
var _output_http: HTTPRequest
var _timer: Timer
var _busy := false
var _baseline_ready := false
var _source_started_at_ms := 0
var _last_utterance_count := 0
var _last_payload: Dictionary = {}
var _failure_count := 0


func _ready() -> void:
	_http = HTTPRequest.new()
	_http.name = "EmbodiedAudioStatusHTTPRequest"
	_http.timeout = timeout_seconds
	_http.request_completed.connect(_on_request_completed)
	add_child(_http)
	_output_http = HTTPRequest.new()
	_output_http.name = "EmbodiedAudioOutputStateHTTPRequest"
	_output_http.timeout = timeout_seconds
	add_child(_output_http)
	_timer = Timer.new()
	_timer.name = "EmbodiedAudioStatusPollTimer"
	_timer.wait_time = maxf(0.20, poll_interval_seconds)
	_timer.one_shot = false
	_timer.timeout.connect(_on_poll_timeout)
	add_child(_timer)
	if enabled:
		start()


func start() -> void:
	enabled = true
	if _timer != null and _timer.is_stopped():
		_timer.start()
	call_deferred("request_status")


func stop() -> void:
	enabled = false
	_busy = false
	if _http != null:
		_http.cancel_request()
	if _output_http != null:
		_output_http.cancel_request()
	if _timer != null:
		_timer.stop()


func request_status() -> bool:
	if not enabled or _busy or _http == null:
		return false
	if not _is_local_url(base_url):
		request_failed.emit(0, "non_local_audio_status_rejected")
		return false
	var headers := PackedStringArray(["Accept: application/json"])
	var error := _http.request(
		base_url.rstrip("/") + "/health",
		headers,
		HTTPClient.METHOD_GET
	)
	if error != OK:
		_failure_count += 1
		request_failed.emit(0, "request_start_failed:%s" % error)
		return false
	_busy = true
	return true


func notify_output_started() -> bool:
	return _post_output_state("/output/start")


func notify_output_stopped() -> bool:
	return _post_output_state("/output/stop")


func accept_status_snapshot(payload: Dictionary) -> Dictionary:
	var snapshot := payload.duplicate(true)
	var started_at_ms := int(snapshot.get("started_at_ms", 0))
	var utterance_count := maxi(0, int(snapshot.get("utterance_count", 0)))
	var baseline_established := false
	var source_restarted := false
	var new_utterance := false

	if not _baseline_ready:
		_baseline_ready = true
		baseline_established = true
		_source_started_at_ms = started_at_ms
		_last_utterance_count = utterance_count
	elif started_at_ms != _source_started_at_ms or utterance_count < _last_utterance_count:
		source_restarted = true
		_source_started_at_ms = started_at_ms
		_last_utterance_count = utterance_count
	elif utterance_count > _last_utterance_count:
		new_utterance = true
		_last_utterance_count = utterance_count

	_last_payload = snapshot
	_failure_count = 0
	status_received.emit(snapshot)
	if new_utterance:
		utterance_completed.emit(snapshot)
	return {
		"baseline_established": baseline_established,
		"source_restarted": source_restarted,
		"new_utterance": new_utterance,
		"utterance_count": _last_utterance_count,
	}


func get_last_payload() -> Dictionary:
	return _last_payload.duplicate(true)


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"busy": _busy,
		"base_url": base_url,
		"localhost_only": _is_local_url(base_url),
		"baseline_ready": _baseline_ready,
		"source_started_at_ms": _source_started_at_ms,
		"last_utterance_count": _last_utterance_count,
		"failure_count": _failure_count,
	}


func _on_poll_timeout() -> void:
	request_status()


func _post_output_state(path: String) -> bool:
	if not enabled or _output_http == null:
		return false
	if not _is_local_url(base_url):
		request_failed.emit(0, "non_local_audio_output_state_rejected")
		return false
	if _output_http.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		_output_http.cancel_request()
	var headers := PackedStringArray(["Accept: application/json"])
	var error := _output_http.request(
		base_url.rstrip("/") + path,
		headers,
		HTTPClient.METHOD_POST
	)
	if error != OK:
		request_failed.emit(0, "output_state_request_failed:%s" % error)
		return false
	return true


func _on_request_completed(
	result: int,
	response_code: int,
	_headers: PackedStringArray,
	body: PackedByteArray
) -> void:
	_busy = false
	if result != HTTPRequest.RESULT_SUCCESS or response_code < 200 or response_code >= 300:
		_failure_count += 1
		request_failed.emit(response_code, "http_result:%s" % result)
		return
	var parsed = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		_failure_count += 1
		request_failed.emit(response_code, "response_not_json_object")
		return
	accept_status_snapshot(parsed as Dictionary)


func _is_local_url(value: String) -> bool:
	return value.begins_with("http://127.0.0.1:") or value.begins_with("http://localhost:")
