extends SceneTree

const InteractiveTrial = preload("res://scripts/embodied_v1/embodied_interactive_trial.gd")

var _failures: Array[String] = []


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var trial = InteractiveTrial.new()
	trial.call("_setup_overlay")
	var audio_label: Label = trial.get("_audio_label")
	var state_label: Label = trial.get("_state_label")
	_expect(audio_label != null, "physical voice status label exists")

	trial.call(
		"_on_audio_status_received",
		{
			"ok": true,
			"enabled": true,
			"state": "capturing",
			"pending_stt": false,
			"ai_busy": false,
		}
	)
	_expect(audio_label.text.contains("声を聞いています"), "capturing state is visible")
	_expect(state_label.text.contains("声を聞いています"), "main state confirms microphone reaction")

	trial.call(
		"_on_audio_status_received",
		{
			"ok": true,
			"enabled": true,
			"state": "transcribing",
			"pending_stt": true,
			"ai_busy": true,
		}
	)
	_expect(audio_label.text.contains("文字にしています"), "transcribing state is visible")

	trial.call(
		"_on_audio_utterance_completed",
		{
			"last_transcript": "ルミナ、聞こえていますか。",
			"last_answer": "聞こえています。確認完了です。",
			"ai_busy": true,
		}
	)
	var transcript := String(trial.get("_transcript_text"))
	_expect(transcript.contains("あなた（音声）\nルミナ、聞こえていますか。"), "recognized speech is shown")
	_expect(transcript.contains("ルミナ\n聞こえています。確認完了です。"), "complete answer is shown")

	trial.call(
		"_on_audio_status_received",
		{
			"ok": true,
			"enabled": true,
			"state": "listening",
			"pending_stt": false,
			"ai_busy": false,
		}
	)
	_expect(audio_label.text.contains("待機中"), "microphone returns to visible waiting state")
	_expect(state_label.text == "音声の返答が完了しました", "completion is explicitly visible")

	var report := {
		"status": "PASS" if _failures.is_empty() else "FAIL",
		"failures": _failures,
		"audio_label": audio_label.text,
		"state_label": state_label.text,
		"transcript": transcript,
	}
	print("AOGIRI_EMBODIED_PHYSICAL_VOICE_UI_TEST %s" % JSON.stringify(report))
	trial.free()
	quit(0 if _failures.is_empty() else 1)


func _expect(condition: bool, label: String) -> void:
	if not condition:
		_failures.append(label)
