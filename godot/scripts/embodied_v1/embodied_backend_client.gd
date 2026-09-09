extends Node
class_name EmbodiedBackendClient

signal health_received(payload: Dictionary)
signal intent_received(payload: Dictionary)
signal record_completed(kind: String, payload: Dictionary)
signal cancel_completed(payload: Dictionary)
signal request_failed(kind: String, status_code: int, detail: String)

@export var enabled := false
@export var base_url := "http://127.0.0.1:8792"
@export_range(1.0, 180.0, 1.0) var timeout_seconds := 150.0

var _http: HTTPRequest
var _cancel_http: HTTPRequest
var _request_kind := ""
var _busy := false
var _last_payload: Dictionary = {}


func _ready() -> void:
	_create_primary_http()
	_cancel_http = HTTPRequest.new()
	_cancel_http.name = "EmbodiedBackendCancelHTTPRequest"
	_cancel_http.timeout = 10.0
	_cancel_http.request_completed.connect(_on_cancel_request_completed)
	add_child(_cancel_http)


func _create_primary_http() -> void:
	_http = HTTPRequest.new()
	_http.name = "EmbodiedBackendHTTPRequest"
	_http.timeout = timeout_seconds
	_http.request_completed.connect(_on_request_completed)
	add_child(_http)


func request_health() -> bool:
	return _send("health", HTTPClient.METHOD_GET, "/health", {})


func request_intent(payload: Dictionary) -> bool:
	return _send("intent", HTTPClient.METHOD_POST, "/intent", payload)


func record_interaction(payload: Dictionary) -> bool:
	return _send("interaction", HTTPClient.METHOD_POST, "/interaction", payload)


func record_world_event(payload: Dictionary) -> bool:
	return _send("world_event", HTTPClient.METHOD_POST, "/world/event", payload)


func record_action_result(payload: Dictionary) -> bool:
	return _send("action_result", HTTPClient.METHOD_POST, "/action/result", payload)


func is_busy() -> bool:
	return _busy


func cancel_current(request_id := "", reason := "cancelled_by_user") -> bool:
	if not _busy or _http == null:
		return false
	var kind := _request_kind
	# HTTPRequest may emit the cancelled request's completion on a later frame.
	# Reusing that node for health/retry lets the stale callback overwrite the
	# new request kind. Detach and retire it before creating a clean transport.
	var retired_http := _http
	var callback := Callable(self, "_on_request_completed")
	if retired_http.request_completed.is_connected(callback):
		retired_http.request_completed.disconnect(callback)
	retired_http.cancel_request()
	retired_http.queue_free()
	_http = null
	_create_primary_http()
	_request_kind = ""
	_busy = false
	request_failed.emit(kind, 499, reason)
	var normalized_request_id := String(request_id).strip_edges()
	if not normalized_request_id.is_empty() and _cancel_http != null:
		var headers := PackedStringArray(["Accept: application/json", "Content-Type: application/json"])
		_cancel_http.request(
			base_url.rstrip("/") + "/cancel",
			headers,
			HTTPClient.METHOD_POST,
			JSON.stringify({"request_id": normalized_request_id, "reason": reason})
		)
	return true


func get_last_payload() -> Dictionary:
	return _last_payload.duplicate(true)


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"busy": _busy,
		"base_url": base_url,
		"localhost_only": base_url.begins_with("http://127.0.0.1:") or base_url.begins_with("http://localhost:"),
		"live_lumina_route_changed": false,
	}


func _send(kind: String, method: HTTPClient.Method, path: String, payload: Dictionary) -> bool:
	if not enabled or _busy or _http == null:
		return false
	if not (base_url.begins_with("http://127.0.0.1:") or base_url.begins_with("http://localhost:")):
		request_failed.emit(kind, 0, "non_local_backend_rejected")
		return false
	var headers := PackedStringArray(["Accept: application/json", "Content-Type: application/json"])
	var body := "" if method == HTTPClient.METHOD_GET else JSON.stringify(payload)
	var error := _http.request(base_url.rstrip("/") + path, headers, method, body)
	if error != OK:
		request_failed.emit(kind, 0, "request_start_failed:%s" % error)
		return false
	_request_kind = kind
	_busy = true
	return true


func _on_request_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var kind := _request_kind
	_request_kind = ""
	_busy = false
	if result != HTTPRequest.RESULT_SUCCESS or response_code < 200 or response_code >= 300:
		request_failed.emit(kind, response_code, "http_result:%s" % result)
		return
	var parsed = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		request_failed.emit(kind, response_code, "response_not_json_object")
		return
	_last_payload = (parsed as Dictionary).duplicate(true)
	match kind:
		"health":
			health_received.emit(_last_payload)
		"intent":
			intent_received.emit(_last_payload)
		_:
			record_completed.emit(kind, _last_payload)


func _on_cancel_request_completed(
	result: int,
	response_code: int,
	_headers: PackedStringArray,
	body: PackedByteArray
) -> void:
	if result != HTTPRequest.RESULT_SUCCESS or response_code < 200 or response_code >= 300:
		cancel_completed.emit({"ok": false, "status_code": response_code, "http_result": result})
		return
	var parsed = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		cancel_completed.emit({"ok": false, "error": "response_not_json_object"})
		return
	cancel_completed.emit((parsed as Dictionary).duplicate(true))
