extends SceneTree

const AudioStatusClient = preload("res://scripts/embodied_v1/embodied_audio_status_client.gd")

var _failures: Array[String] = []
var _utterances: Array[Dictionary] = []
var _statuses: Array[Dictionary] = []
var _request_error := ""


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var client = AudioStatusClient.new()
	client.enabled = false
	client.status_received.connect(_on_status)
	client.utterance_completed.connect(_on_utterance)
	client.request_failed.connect(_on_error)
	root.add_child(client)
	await process_frame

	var first: Dictionary = client.accept_status_snapshot(
		{
			"ok": true,
			"started_at_ms": 1000,
			"utterance_count": 7,
			"state": "listening",
		}
	)
	_expect(bool(first.get("baseline_established", false)), "first snapshot establishes a baseline")
	_expect(_utterances.is_empty(), "existing utterance is not replayed on scene entry")

	var same: Dictionary = client.accept_status_snapshot(
		{
			"ok": true,
			"started_at_ms": 1000,
			"utterance_count": 7,
			"state": "capturing",
		}
	)
	_expect(not bool(same.get("new_utterance", true)), "same counter is not duplicated")

	var next: Dictionary = client.accept_status_snapshot(
		{
			"ok": true,
			"started_at_ms": 1000,
			"utterance_count": 8,
			"state": "listening",
			"last_transcript": "声で話しました",
			"last_answer": "聞こえています。確認完了です。",
		}
	)
	_expect(bool(next.get("new_utterance", false)), "incremented counter emits one utterance")
	_expect(_utterances.size() == 1, "one utterance signal is emitted")
	_expect(String(_utterances[0].get("last_transcript", "")) == "声で話しました", "transcript is preserved")
	_expect(String(_utterances[0].get("last_answer", "")).ends_with("確認完了です。"), "complete answer is preserved")

	var restarted: Dictionary = client.accept_status_snapshot(
		{
			"ok": true,
			"started_at_ms": 2000,
			"utterance_count": 0,
			"state": "calibrating",
		}
	)
	_expect(bool(restarted.get("source_restarted", false)), "daemon restart resets the cursor")
	_expect(_utterances.size() == 1, "restart does not replay stale text")

	client.accept_status_snapshot(
		{
			"ok": true,
			"started_at_ms": 2000,
			"utterance_count": 1,
			"state": "listening",
			"last_transcript": "再起動後の声",
			"last_answer": "再起動後も聞こえています。",
		}
	)
	_expect(_utterances.size() == 2, "first post-restart utterance is emitted")
	_expect(_statuses.size() == 5, "every valid snapshot updates visible status")

	client.base_url = "https://example.com"
	client.enabled = true
	_expect(not client.request_status(), "non-local audio status endpoint is rejected")
	_expect(_request_error == "0:non_local_audio_status_rejected", "localhost rejection is explicit")

	var report := {
		"status": "PASS" if _failures.is_empty() else "FAIL",
		"failures": _failures,
		"status_count": _statuses.size(),
		"utterance_count": _utterances.size(),
		"client": client.get_status(),
	}
	print("AOGIRI_EMBODIED_AUDIO_STATUS_CLIENT_TEST %s" % JSON.stringify(report))
	client.queue_free()
	await process_frame
	quit(0 if _failures.is_empty() else 1)


func _on_status(payload: Dictionary) -> void:
	_statuses.append(payload.duplicate(true))


func _on_utterance(payload: Dictionary) -> void:
	_utterances.append(payload.duplicate(true))


func _on_error(status_code: int, detail: String) -> void:
	_request_error = "%d:%s" % [status_code, detail]


func _expect(condition: bool, label: String) -> void:
	if not condition:
		_failures.append(label)
