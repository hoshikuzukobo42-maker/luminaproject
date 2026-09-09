extends Node
class_name LuminaLivingHubMemoryBridge

## Forwards Living Hub events to long-term memory when bridge is available.

signal event_notified(payload: Dictionary)
signal notify_failed(reason: String)

@export var bridge_base_url: String = "http://127.0.0.1:8765"
@export var enabled: bool = true

var _http: HTTPRequest


func _ready() -> void:
	_http = HTTPRequest.new()
	_http.name = "LivingHubMemoryHttp"
	add_child(_http)
	_http.request_completed.connect(_on_request_completed)


func notify_event(kind: String, summary: String, meta: Dictionary = {}) -> void:
	var payload := {
		"schema": "lumina.living_hub.memory_event.v1",
		"kind": kind,
		"summary": summary,
		"meta": meta,
		"at": Time.get_datetime_string_from_system(true) + "Z",
	}
	event_notified.emit(payload)
	if not enabled:
		return
	if _http.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		_http.cancel_request()
	var note := {
		"key": "living_hub_%s_%s" % [kind, str(Time.get_unix_time_from_system()).replace(".", "_")],
		"text": "[living_hub/%s] %s" % [kind, summary],
		"kind": "episodic",
		"tags": ["living_hub", kind],
		"meta": meta,
	}
	var body := JSON.stringify(note)
	var err := _http.request(
		bridge_base_url.rstrip("/") + "/memory/notes/remember",
		["Content-Type: application/json"],
		HTTPClient.METHOD_POST,
		body
	)
	if err != OK:
		notify_failed.emit("request_error_%s" % err)


func _on_request_completed(result: int, response_code: int, _headers: PackedStringArray, _body: PackedByteArray) -> void:
	if result != HTTPRequest.RESULT_SUCCESS or response_code >= 400:
		notify_failed.emit("http_%s_%s" % [result, response_code])
