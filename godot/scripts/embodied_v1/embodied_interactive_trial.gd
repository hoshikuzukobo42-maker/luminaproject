extends Node3D

signal return_requested

const RuntimeScript = preload("res://scripts/embodied_v1/embodied_runtime.gd")
const BackendClientScript = preload("res://scripts/embodied_v1/embodied_backend_client.gd")
const VoicePlayerScript = preload("res://scripts/embodied_v1/embodied_voice_player.gd")
const AudioStatusClientScript = preload("res://scripts/embodied_v1/embodied_audio_status_client.gd")
const CANDIDATE_SOURCE_PATH := "res://assets/Aogiri_Embodied_Runtime_v010_SafeSemanticMotionBank_164cm.glb"
const V038_CANDIDATE_SOURCE_PATH := "res://assets/Aogiri_Embodied_Runtime_v038_SemanticMotionFace39_164cm.glb"
const FUTURE_ROOM_SCENE_PATH := "res://scenes/future_room_v008_glb_loader.tscn"

@export var backend_base_url := "http://127.0.0.1:8792"
@export var audio_status_base_url := "http://127.0.0.1:8791"
@export var return_scene_path := "res://scenes/demo_main.tscn"

var _body: CharacterBody3D
var _runtime: Node
var _backend: Node
var _event_backend: Node
var _voice: Node
var _audio_status_client: Node
var _camera: Camera3D
var _future_room: Node3D
var _anchors: Dictionary = {}

var _state_label: Label
var _backend_label: Label
var _audio_label: Label
var _transcript: RichTextLabel
var _input_field: LineEdit
var _send_button: Button
var _stop_button: Button
var _retry_button: Button
var _reconnect_button: Button
var _touch_button: Button
var _return_button: Button

var _backend_ready := false
var _health_success_count := 0
var _turn_busy := false
var _cancel_requested := false
var _plan_done := false
var _voice_done := false
var _voice_started := false
var _turn_completion_count := 0
var _pending_voice_payload: Dictionary = {}
var _last_backend_payload: Dictionary = {}
var _last_text := ""
var _last_reply := ""
var _last_error := ""
var _last_plan_ok := false
var _speech_posture := ""
var _transcript_text := ""
var _autotest_running := false
var _current_request_id := ""
var _request_sequence := 0
var _last_cancel_payload: Dictionary = {}
var _last_health_payload: Dictionary = {}
var _affordance_objects: Dictionary = {}
var _head_touch_area: Area3D
var _pointer_touch_active := false
var _last_pointer_touch_position := Vector2.ZERO
var _event_queue: Array[Dictionary] = []
var _entry_greeting_started := false
var _audio_playback_pending := false
var _audio_seen_busy := false
var _audio_last_state := ""


func _ready() -> void:
	_setup_room_environment()
	_setup_navigation()
	_setup_camera()
	_setup_overlay()
	_setup_backend_and_voice()
	_set_state("会話の準備をしています")
	_append_transcript("案内", "ルミナに話しかけてください。上部に聞き取り状態を表示し、認識した内容と返答を最後まで会話欄へ表示します。")
	call_deferred("_request_health")
	if _env_enabled("AOGIRI_PRESENTATION_CAPTURE_ONLY"):
		call_deferred("_run_presentation_capture_only")
	elif _env_enabled("AOGIRI_FULL_ACCEPTANCE_AUTOTEST"):
		call_deferred("_run_full_acceptance_autotest")
	elif _env_enabled("AOGIRI_INTERACTIVE_AUTOTEST"):
		call_deferred("_run_autotest")


func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_ESCAPE:
		if _input_field != null and _input_field.has_focus():
			_input_field.release_focus()
			_set_state("Esc をもう一度押すと通常の Lumina へ戻ります")
		else:
			_return_to_lumina()
		get_viewport().set_input_as_handled()
	elif event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		if event.pressed and _ray_hits_head(event.position):
			_begin_pointer_head_touch(event.position)
			get_viewport().set_input_as_handled()
		elif not event.pressed and _pointer_touch_active:
			_end_pointer_head_touch(event.position)
			get_viewport().set_input_as_handled()
	elif event is InputEventMouseMotion and _pointer_touch_active:
		_update_pointer_head_touch(event.position)
		get_viewport().set_input_as_handled()


func _process(delta: float) -> void:
	_update_camera(delta)
	_flush_event_queue()


func shutdown_for_host() -> void:
	if _turn_busy:
		_stop_turn()
	if _audio_status_client != null:
		_audio_status_client.call("notify_output_stopped")
		_audio_status_client.call("stop")
	if _voice != null:
		_voice.call("stop")
	if _pointer_touch_active:
		_end_pointer_head_touch(Vector2.ZERO)


func _request_health() -> void:
	if _backend == null or bool(_backend.call("is_busy")):
		return
	_backend_ready = false
	_refresh_controls()
	_set_state("会話サービスへ接続しています")
	if not bool(_backend.call("request_health")):
		_on_backend_failed("health", 0, "health_request_not_started")


func _submit_text(text: String) -> bool:
	var normalized := text.strip_edges()
	if normalized.is_empty():
		_set_state("メッセージを入力してください")
		return false
	if _turn_busy:
		_set_state("現在の返答が完了するまで待つか、停止してください")
		return false
	if not _backend_ready:
		_set_state("sidecar 未接続です。「再接続」を押してください")
		return false

	_last_text = normalized
	_last_reply = ""
	_last_error = ""
	_last_backend_payload = {}
	_pending_voice_payload = {}
	_plan_done = false
	_voice_done = false
	_voice_started = false
	_last_plan_ok = false
	_speech_posture = ""
	_request_sequence += 1
	_current_request_id = "interactive_%d_%d" % [Time.get_ticks_msec(), _request_sequence]
	_turn_busy = true
	_runtime.call("set_cognitive_busy", true)
	_append_transcript("あなた", normalized)
	_input_field.clear()
	_refresh_controls()
	_set_state("ルミナが考えています…")
	_runtime.call(
		"dispatch_intent",
		{
			"action_id": "interactive_thinking_%d" % Time.get_ticks_msec(),
			"type": "set_expression",
			"source": "embodied_interactive_trial",
			"params": {"expression": "neutral", "intensity": 0.58, "transition_seconds": 0.16},
		}
	)
	var payload := {
		"request_id": _current_request_id,
		"event_type": "user_spoke",
		"text": normalized,
		"location": _current_location(),
		"synthesize": true,
		"include_audio_base64": true,
		"importance": 0.55,
	}
	if not bool(_backend.call("request_intent", payload)):
		_fail_turn("intent_request_not_started")
		return false
	return true


func _stop_turn() -> void:
	if not _turn_busy:
		return
	_cancel_requested = true
	_backend.call("cancel_current", _current_request_id, "cancelled_by_user")
	var executor: Node = _runtime.call("get_plan_executor")
	if executor != null and bool(executor.call("is_running")):
		executor.call("cancel", "cancelled_by_user")
	if _audio_status_client != null:
		_audio_status_client.call("notify_output_stopped")
	_voice.call("stop")
	_restore_safe_posture()
	_cancel_requested = false
	_turn_busy = false
	_runtime.call("set_cognitive_busy", false)
	_plan_done = false
	_voice_done = false
	_last_error = "cancelled_by_user"
	_append_transcript("案内", "返答を停止しました。「もう一度」で同じ内容を送れます。")
	_set_state("停止しました")
	_refresh_controls()


func _retry_last() -> void:
	if _last_text.is_empty() or _turn_busy:
		return
	_submit_text(_last_text)


func _trigger_head_pat() -> void:
	if _runtime == null:
		return
	var now := Time.get_ticks_msec()
	_runtime.call("set_touch_enabled", true)
	_runtime.call("begin_touch", "head", Vector2(0.50, 0.50), now)
	_runtime.call("update_touch", Vector2(0.50, 0.46), now + 90)
	_runtime.call("update_touch", Vector2(0.50, 0.52), now + 180)
	_runtime.call("update_touch", Vector2(0.50, 0.47), now + 270)
	var event: Dictionary = _runtime.call("end_touch", Vector2(0.50, 0.50), now + 390)
	_runtime.call("set_touch_enabled", false)
	event["location"] = _current_location()
	event["source"] = "accessibility_head_pat_button"
	_queue_backend_event("interaction", event)
	var reflex: Dictionary = _runtime.call("get_last_reflex_result")
	if String(event.get("gesture", "")) == "pat" and bool(reflex.get("accepted", false)):
		_append_transcript("案内", "ルミナがすぐに反応しました。")
		_set_state("頭を撫でました")
	else:
		_set_state("反応を安全に終了しました")


func _setup_head_touch_zone() -> void:
	_head_touch_area = Area3D.new()
	_head_touch_area.name = "HeadTouchZone"
	_head_touch_area.position = Vector3(0.0, 1.49, 0.0)
	_head_touch_area.collision_layer = 1 << 7
	_head_touch_area.collision_mask = 0
	_head_touch_area.monitoring = false
	_head_touch_area.monitorable = true
	_head_touch_area.set_meta("embodied_touch_zone", "head")
	var collision := CollisionShape3D.new()
	var sphere := SphereShape3D.new()
	sphere.radius = 0.175
	collision.shape = sphere
	_head_touch_area.add_child(collision)
	_body.add_child(_head_touch_area)


func _ray_hits_head(screen_position: Vector2) -> bool:
	if _camera == null or _head_touch_area == null or not is_inside_tree():
		return false
	var origin := _camera.project_ray_origin(screen_position)
	var destination := origin + _camera.project_ray_normal(screen_position) * 20.0
	var query := PhysicsRayQueryParameters3D.create(origin, destination, 1 << 7)
	query.collide_with_areas = true
	query.collide_with_bodies = false
	var hit := get_world_3d().direct_space_state.intersect_ray(query)
	return not hit.is_empty() and hit.get("collider") == _head_touch_area


func _normalized_pointer_position(screen_position: Vector2) -> Vector2:
	var viewport_size := get_viewport().get_visible_rect().size
	return Vector2(
		clampf(screen_position.x / maxf(1.0, viewport_size.x), 0.0, 1.0),
		clampf(screen_position.y / maxf(1.0, viewport_size.y), 0.0, 1.0)
	)


func _begin_pointer_head_touch(screen_position: Vector2) -> void:
	if _runtime == null or _pointer_touch_active:
		return
	_pointer_touch_active = true
	_last_pointer_touch_position = screen_position
	_runtime.call("set_touch_enabled", true)
	_runtime.call(
		"begin_touch",
		"head",
		_normalized_pointer_position(screen_position),
		Time.get_ticks_msec()
	)
	_set_state("頭の上でゆっくり左右に動かすと撫でられます")


func _update_pointer_head_touch(screen_position: Vector2) -> void:
	if not _pointer_touch_active or _runtime == null:
		return
	_last_pointer_touch_position = screen_position
	_runtime.call(
		"update_touch",
		_normalized_pointer_position(screen_position),
		Time.get_ticks_msec()
	)


func _end_pointer_head_touch(screen_position: Vector2) -> void:
	if not _pointer_touch_active or _runtime == null:
		return
	var final_position := _last_pointer_touch_position if screen_position == Vector2.ZERO else screen_position
	var event: Dictionary = _runtime.call(
		"end_touch",
		_normalized_pointer_position(final_position),
		Time.get_ticks_msec()
	)
	_pointer_touch_active = false
	_runtime.call("set_touch_enabled", false)
	event["location"] = _current_location()
	event["source"] = "mouse_head_touch_zone"
	_queue_backend_event("interaction", event)
	var reflex: Dictionary = _runtime.call("get_last_reflex_result")
	if bool(reflex.get("accepted", false)):
		_append_transcript("案内", "頭への操作にルミナがすぐ反応しました。")
		_set_state("反応しました")
	else:
		_set_state("操作を安全に終了しました")


func _return_to_lumina() -> void:
	if _turn_busy:
		_stop_turn()
	if return_requested.has_connections():
		return_requested.emit()
	elif ResourceLoader.exists(return_scene_path):
		get_tree().change_scene_to_file(return_scene_path)
	elif _env_enabled("AOGIRI_INTERACTIVE_AUTOTEST"):
		get_tree().quit()
	else:
		_set_state("復帰先が見つかりません: %s" % return_scene_path)


func _on_backend_health(payload: Dictionary) -> void:
	_last_health_payload = payload.duplicate(true)
	_backend_ready = bool(payload.get("ready", false)) and String(payload.get("status", "")) == "ok"
	if _backend_ready:
		_health_success_count += 1
		_backend_label.text = "会話できます"
		_backend_label.add_theme_color_override("font_color", Color(0.48, 0.91, 0.69))
		if (
			not _entry_greeting_started
			and not _env_enabled("AOGIRI_INTERACTIVE_AUTOTEST")
			and not _env_enabled("AOGIRI_FULL_ACCEPTANCE_AUTOTEST")
			and not _env_enabled("AOGIRI_PRESENTATION_CAPTURE_ONLY")
		):
			_entry_greeting_started = true
			call_deferred("_submit_entry_greeting")
		else:
			_set_state("メッセージを入力してください")
	else:
		_backend_label.text = "会話サービスに接続できません"
		_backend_label.add_theme_color_override("font_color", Color(0.95, 0.50, 0.44))
		_set_state("少し待ってから再接続してください")
	_refresh_controls()


func _submit_entry_greeting() -> void:
	if _turn_busy or not _backend_ready:
		return
	_last_text = ""
	_last_reply = ""
	_last_error = ""
	_last_backend_payload = {}
	_pending_voice_payload = {}
	_plan_done = false
	_voice_done = false
	_voice_started = false
	_last_plan_ok = false
	_speech_posture = ""
	_current_request_id = "entry_%d" % Time.get_ticks_msec()
	_turn_busy = true
	_runtime.call("set_cognitive_busy", true)
	_append_transcript("案内", "ルミナが入室に気づきました。")
	_set_state("ルミナがこちらを見ています")
	_refresh_controls()
	var payload := {
		"request_id": _current_request_id,
		"session_id": "embodied-v1-room",
		"event_type": "user_entered",
		"text": "ユーザーが部屋に戻ってきました。短く自然に迎えてください。",
		"location": _current_location(),
		"synthesize": true,
		"include_audio_base64": true,
		"importance": 0.66,
		"record_shared_episode": true,
		"episode_summary": "ユーザーが部屋に入り、ルミナが気づいて近づき挨拶した",
	}
	if not bool(_backend.call("request_intent", payload)):
		_fail_turn("entry_greeting_request_not_started")


func _on_backend_intent(payload: Dictionary) -> void:
	if not _turn_busy:
		return
	_last_backend_payload = payload.duplicate(true)
	if not bool(payload.get("ok", false)):
		_fail_turn("backend_intent_not_ok")
		return
	var reply_value = payload.get("reply", {})
	if typeof(reply_value) != TYPE_DICTIONARY:
		_fail_turn("reply_not_object")
		return
	var reply: Dictionary = reply_value
	_last_reply = String(reply.get("text", "")).strip_edges()
	if _last_reply.is_empty():
		_fail_turn("reply_text_empty")
		return
	_append_transcript("ルミナ", _last_reply)
	_pending_voice_payload = payload.get("voice", {}).duplicate(true)
	var plan_value = payload.get("intent", {})
	if typeof(plan_value) != TYPE_DICTIONARY:
		_fail_turn("intent_plan_not_object")
		return
	_set_state("ルミナが動いています…")
	if not bool(_runtime.call("dispatch_plan", plan_value as Dictionary)):
		_fail_turn("intent_plan_not_started")


func _on_backend_failed(kind: String, status_code: int, detail: String) -> void:
	if _cancel_requested and status_code == 499:
		return
	var reason := "%s:%d:%s" % [kind, status_code, detail]
	if kind == "health":
		_backend_ready = false
		_last_error = reason
		_backend_label.text = "会話サービスに接続できません"
		_backend_label.add_theme_color_override("font_color", Color(0.95, 0.50, 0.44))
		_set_state("「再接続」を押してください")
		_refresh_controls()
		return
	if _turn_busy:
		_fail_turn(reason)


func _on_backend_cancel_completed(payload: Dictionary) -> void:
	_last_cancel_payload = payload.duplicate(true)


func _on_plan_started(goal: String, action_count: int) -> void:
	_set_state("動作を実行しています（%d段階）" % action_count)


func _on_plan_completed(ok: bool, _results: Array[Dictionary]) -> void:
	if not _turn_busy or _cancel_requested:
		return
	_last_plan_ok = ok
	_plan_done = true
	if not ok:
		_fail_turn("plan_execution_failed")
		return
	if not _voice_started:
		_voice_done = true
	_finish_turn_if_ready()


func _on_speech_action_requested(_action: Dictionary) -> void:
	if not _turn_busy:
		return
	_speech_posture = String(_runtime.call("get_action_adapter").call("get_current_posture"))
	if _pending_voice_payload.is_empty():
		_voice_done = true
		_last_error = "voice_payload_missing"
		return
	_voice_started = true
	if not bool(_voice.call("play_voice_payload", _pending_voice_payload)):
		_fail_turn("voice_payload_rejected")


func _on_voice_segment_started(index: int, _text: String) -> void:
	if index == 0 and _audio_status_client != null:
		_audio_status_client.call("notify_output_started")
	var status: Dictionary = _voice.call("get_status")
	_set_state("話しています… %d/%d" % [index + 1, int(status.get("total_segments", 0))])


func _on_voice_segment_finished(_index: int, _text: String) -> void:
	pass


func _on_voice_finished(_segment_count: int) -> void:
	if _audio_status_client != null:
		_audio_status_client.call("notify_output_stopped")
	_voice_done = true
	_finish_turn_if_ready()


func _on_voice_failed(reason: String) -> void:
	if _audio_status_client != null:
		_audio_status_client.call("notify_output_stopped")
	if _turn_busy:
		_fail_turn("voice_failed:%s" % reason)


func _finish_turn_if_ready() -> void:
	if not _turn_busy or not _plan_done or not _voice_done:
		return
	_turn_busy = false
	_runtime.call("set_cognitive_busy", false)
	_turn_completion_count += 1
	_set_state("返答が完了しました")
	_refresh_controls()
	if not _autotest_running and _input_field != null:
		_input_field.grab_focus()


func _fail_turn(reason: String) -> void:
	_last_error = reason
	_turn_busy = false
	_runtime.call("set_cognitive_busy", false)
	var executor: Node = _runtime.call("get_plan_executor")
	if executor != null and bool(executor.call("is_running")):
		executor.call("cancel", reason)
	if _audio_status_client != null:
		_audio_status_client.call("notify_output_stopped")
	_voice.call("stop")
	_restore_safe_posture()
	_append_transcript("案内", "返答を完了できませんでした。再接続または再試行してください。")
	_set_state("返答に失敗しました")
	_refresh_controls()


func _restore_safe_posture() -> void:
	var adapter: Node = _runtime.call("get_action_adapter")
	var posture := String(adapter.call("get_current_posture"))
	var state := "sit" if posture == "sitting" else "idle"
	_runtime.call(
		"dispatch_intent",
		{
			"action_id": "interactive_safe_%d" % Time.get_ticks_msec(),
			"type": "living_state",
			"source": "embodied_interactive_trial",
			"params": {"state": state},
		}
	)


func _current_location() -> String:
	if _body == null:
		return "room"
	var nearest := "room"
	var nearest_distance := INF
	for anchor_id in _anchors.keys():
		var anchor := _anchors[anchor_id] as Node3D
		var distance := _body.global_position.distance_to(anchor.global_position)
		if distance < nearest_distance:
			nearest_distance = distance
			nearest = String(anchor_id)
	return nearest


func _refresh_controls() -> void:
	if _send_button == null:
		return
	_send_button.disabled = _turn_busy or not _backend_ready
	_stop_button.disabled = not _turn_busy
	_retry_button.disabled = _turn_busy or not _backend_ready or _last_text.is_empty()
	_reconnect_button.disabled = _turn_busy
	_touch_button.disabled = _turn_busy
	_input_field.editable = not _turn_busy and _backend_ready


func _append_transcript(speaker: String, text: String) -> void:
	var normalized := text.strip_edges()
	if normalized.is_empty():
		return
	if not _transcript_text.is_empty():
		_transcript_text += "\n\n"
	_transcript_text += "%s\n%s" % [speaker, normalized]
	if _transcript != null:
		_transcript.text = _transcript_text
		call_deferred("_scroll_transcript_to_bottom")


func _scroll_transcript_to_bottom() -> void:
	if _transcript != null:
		_transcript.scroll_to_line(maxi(0, _transcript.get_line_count() - 1))


func _set_state(text: String) -> void:
	if _state_label != null:
		_state_label.text = text
	print("AOGIRI_INTERACTIVE_STATE %s" % text)


func _setup_backend_and_voice() -> void:
	_backend = BackendClientScript.new()
	_backend.name = "EmbodiedBackendClient"
	_backend.enabled = true
	var env_url := OS.get_environment("LUMINA_EMBODIED_BACKEND_URL").strip_edges()
	_backend.base_url = env_url if not env_url.is_empty() else backend_base_url
	_backend.health_received.connect(_on_backend_health)
	_backend.intent_received.connect(_on_backend_intent)
	_backend.cancel_completed.connect(_on_backend_cancel_completed)
	_backend.request_failed.connect(_on_backend_failed)
	add_child(_backend)
	_event_backend = BackendClientScript.new()
	_event_backend.name = "EmbodiedEventBackendClient"
	_event_backend.enabled = true
	_event_backend.base_url = _backend.base_url
	_event_backend.record_completed.connect(_on_event_backend_completed)
	_event_backend.request_failed.connect(_on_event_backend_failed)
	add_child(_event_backend)
	_audio_status_client = AudioStatusClientScript.new()
	_audio_status_client.name = "EmbodiedAudioStatusClient"
	_audio_status_client.enabled = true
	var audio_env_url := OS.get_environment("LUMINA_AUDIO_STATUS_URL").strip_edges()
	_audio_status_client.base_url = audio_env_url if not audio_env_url.is_empty() else audio_status_base_url
	_audio_status_client.status_received.connect(_on_audio_status_received)
	_audio_status_client.utterance_completed.connect(_on_audio_utterance_completed)
	_audio_status_client.request_failed.connect(_on_audio_status_failed)
	add_child(_audio_status_client)

	_voice = VoicePlayerScript.new()
	_voice.name = "EmbodiedVoicePlayer"
	_voice.enabled = true
	_voice.segment_started.connect(_on_voice_segment_started)
	_voice.segment_finished.connect(_on_voice_segment_finished)
	_voice.voice_finished.connect(_on_voice_finished)
	_voice.voice_failed.connect(_on_voice_failed)
	add_child(_voice)
	_voice.call("bind_face_controller", _runtime.call("get_face_controller"))

	var executor: Node = _runtime.call("get_plan_executor")
	executor.plan_started.connect(_on_plan_started)
	executor.action_finished.connect(_on_action_finished)
	executor.plan_completed.connect(_on_plan_completed)
	executor.speech_action_requested.connect(_on_speech_action_requested)


func _on_audio_status_received(payload: Dictionary) -> void:
	if _audio_label == null:
		return
	var service_ok := bool(payload.get("ok", false)) and bool(payload.get("enabled", false))
	var state := String(payload.get("state", "unknown"))
	var pending_stt := bool(payload.get("pending_stt", false))
	var ai_busy := bool(payload.get("ai_busy", false))
	var voice_active := state == "capturing" or pending_stt or ai_busy
	_audio_last_state = state
	if _runtime != null:
		_runtime.call("set_cognitive_busy", _turn_busy or voice_active or _audio_playback_pending)

	if not service_ok:
		_audio_label.text = "マイク: 利用できません（文字入力は使用できます）"
		_audio_label.add_theme_color_override("font_color", Color(0.95, 0.50, 0.44))
		return
	if state == "capturing":
		_audio_label.text = "マイク: 声を聞いています…"
		_audio_label.add_theme_color_override("font_color", Color(0.96, 0.78, 0.34))
		if not _turn_busy:
			_set_state("声を聞いています…")
	elif pending_stt or state == "transcribing":
		_audio_label.text = "マイク: 音声を文字にしています…"
		_audio_label.add_theme_color_override("font_color", Color(0.96, 0.78, 0.34))
		if not _turn_busy:
			_set_state("音声を文字にしています…")
	elif ai_busy:
		_audio_seen_busy = _audio_seen_busy or _audio_playback_pending
		_audio_label.text = "マイク: ルミナが返答しています…"
		_audio_label.add_theme_color_override("font_color", Color(0.62, 0.82, 1.0))
		if not _turn_busy:
			_set_state("音声の返答を再生しています…")
	else:
		_audio_label.text = "マイク: 待機中（そのまま話せます）"
		_audio_label.add_theme_color_override("font_color", Color(0.48, 0.91, 0.69))
		if _audio_playback_pending and (_audio_seen_busy or not ai_busy):
			_audio_playback_pending = false
			_audio_seen_busy = false
			if _runtime != null:
				_runtime.call("set_cognitive_busy", _turn_busy)
			if not _turn_busy:
				_set_state("音声の返答が完了しました")


func _on_audio_utterance_completed(payload: Dictionary) -> void:
	var transcript := String(payload.get("last_transcript", "")).strip_edges()
	var answer := String(payload.get("last_answer", "")).strip_edges()
	if not transcript.is_empty():
		_append_transcript("あなた（音声）", transcript)
	if not answer.is_empty():
		_append_transcript("ルミナ", answer)
	if transcript.is_empty() and answer.is_empty():
		_append_transcript("案内", "音声を検出しましたが、文字起こし結果を取得できませんでした。")
	_audio_playback_pending = bool(payload.get("ai_busy", false))
	_audio_seen_busy = _audio_playback_pending
	if _runtime != null:
		_runtime.call("set_cognitive_busy", _turn_busy or _audio_playback_pending)
	if not _turn_busy:
		_set_state("音声の返答を再生しています…" if _audio_playback_pending else "音声の返答が完了しました")


func _on_audio_status_failed(_status_code: int, _detail: String) -> void:
	if _audio_label == null:
		return
	_audio_label.text = "マイク: 再接続中（文字入力は使用できます）"
	_audio_label.add_theme_color_override("font_color", Color(0.95, 0.67, 0.38))


func _on_action_finished(result: Dictionary) -> void:
	var payload := result.duplicate(true)
	payload["location"] = _current_location()
	_queue_backend_event("action", payload)


func _queue_backend_event(kind: String, payload: Dictionary) -> void:
	if payload.is_empty():
		return
	_event_queue.append({"kind": kind, "payload": payload.duplicate(true)})
	_flush_event_queue()


func _flush_event_queue() -> void:
	if _event_backend == null or _event_queue.is_empty() or bool(_event_backend.call("is_busy")):
		return
	var item: Dictionary = _event_queue.pop_front()
	var kind := String(item.get("kind", ""))
	var payload: Dictionary = item.get("payload", {})
	var started := false
	match kind:
		"action":
			started = bool(_event_backend.call("record_action_result", payload))
		"interaction":
			started = bool(_event_backend.call("record_interaction", payload))
		"world":
			started = bool(_event_backend.call("record_world_event", payload))
	if not started:
		_event_queue.push_front(item)


func _on_event_backend_completed(_kind: String, _payload: Dictionary) -> void:
	call_deferred("_flush_event_queue")


func _on_event_backend_failed(kind: String, status_code: int, detail: String) -> void:
	push_warning("Embodied event record failed: %s:%d:%s" % [kind, status_code, detail])


func _setup_room_environment() -> void:
	if ResourceLoader.exists(FUTURE_ROOM_SCENE_PATH):
		var packed := load(FUTURE_ROOM_SCENE_PATH) as PackedScene
		if packed != null:
			_future_room = packed.instantiate() as Node3D
			if _future_room != null:
				_future_room.name = "EmbodiedFutureRoomV008"
				add_child(_future_room)
				_tune_future_room_for_embodied()
				call_deferred("_hide_future_room_markers")
	if _future_room == null:
		_setup_fallback_environment()
		_setup_fallback_room()
	_add_anchor("entry", Vector3(0.0, 0.0, 4.45), 0.0)
	_add_anchor("user", Vector3(0.0, 0.0, 5.25), 0.0)
	_add_anchor("greeting_position", Vector3(0.0, 0.0, 2.55), 0.0)
	_add_anchor("companion_idle", Vector3(0.0, 0.0, 0.20), 0.0)
	_add_anchor("window_seat", Vector3(-0.95, 0.0, -0.95), 0.0)
	_add_anchor("window_view", Vector3(-0.95, 1.45, -4.30), 0.0)
	_add_anchor("chair_left", Vector3(-5.62, 0.0, 2.20), 0.0)
	_add_anchor("book", Vector3(-1.62, 0.50, -0.56), -18.0)
	_add_anchor("book_rest", Vector3(-1.62, 0.50, -0.56), -18.0)
	_add_anchor("cup", Vector3(0.18, 0.56, 0.72), 8.0)
	_add_anchor("cup_rest", Vector3(0.18, 0.56, 0.72), 8.0)
	_setup_affordance_props()


func _setup_affordance_props() -> void:
	var book := Node3D.new()
	book.name = "EmbodiedBook"
	book.global_transform = (_anchors["book_rest"] as Node3D).global_transform
	add_child(book)
	var cover := MeshInstance3D.new()
	var cover_mesh := BoxMesh.new()
	cover_mesh.size = Vector3(0.25, 0.038, 0.18)
	var cover_material := StandardMaterial3D.new()
	cover_material.albedo_color = Color(0.20, 0.10, 0.38)
	cover_material.roughness = 0.68
	cover_mesh.material = cover_material
	cover.mesh = cover_mesh
	book.add_child(cover)
	var pages := MeshInstance3D.new()
	var pages_mesh := BoxMesh.new()
	pages_mesh.size = Vector3(0.225, 0.031, 0.158)
	var pages_material := StandardMaterial3D.new()
	pages_material.albedo_color = Color(0.94, 0.88, 0.72)
	pages_material.roughness = 0.92
	pages_mesh.material = pages_material
	pages.mesh = pages_mesh
	pages.position.y = 0.009
	book.add_child(pages)

	var cup := Node3D.new()
	cup.name = "EmbodiedCup"
	cup.global_transform = (_anchors["cup_rest"] as Node3D).global_transform
	add_child(cup)
	var cup_body := MeshInstance3D.new()
	var cup_mesh := CylinderMesh.new()
	cup_mesh.height = 0.115
	cup_mesh.top_radius = 0.052
	cup_mesh.bottom_radius = 0.045
	cup_mesh.radial_segments = 24
	var cup_material := StandardMaterial3D.new()
	cup_material.albedo_color = Color(0.28, 0.58, 0.82)
	cup_material.roughness = 0.52
	cup_mesh.material = cup_material
	cup_body.mesh = cup_mesh
	cup_body.position.y = 0.058
	cup.add_child(cup_body)
	var handle := MeshInstance3D.new()
	var handle_mesh := TorusMesh.new()
	handle_mesh.inner_radius = 0.017
	handle_mesh.outer_radius = 0.031
	handle_mesh.rings = 18
	handle_mesh.ring_segments = 10
	handle_mesh.material = cup_material
	handle.mesh = handle_mesh
	handle.position = Vector3(0.054, 0.060, 0.0)
	handle.rotation_degrees.z = 90.0
	cup.add_child(handle)

	_affordance_objects = {
		"book": {
			"node": book,
			"affordances": ["reach", "inspect", "pick_up", "put_down", "read"],
			"home_anchor": "book_rest",
		},
		"cup": {
			"node": cup,
			"affordances": ["reach", "inspect", "pick_up", "put_down", "drink"],
			"home_anchor": "cup_rest",
		},
	}


func _setup_fallback_environment() -> void:
	var world_environment := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color(0.40, 0.48, 0.62)
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color(0.88, 0.91, 0.98)
	environment.ambient_light_energy = 0.72
	environment.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	environment.tonemap_exposure = 0.92
	world_environment.environment = environment
	add_child(world_environment)
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-42.0, -25.0, 0.0)
	light.light_energy = 0.92
	light.shadow_enabled = true
	add_child(light)


func _setup_fallback_room() -> void:
	_add_box("Floor", Vector3(0.0, -0.055, 0.0), Vector3(16.0, 0.10, 12.0), Color(0.78, 0.82, 0.90))
	_add_box("BackWall", Vector3(0.0, 1.8, 5.9), Vector3(16.0, 3.6, 0.12), Color(0.48, 0.57, 0.72))
	_add_box("FallbackSeatLeft", Vector3(-2.35, 0.28, -0.25), Vector3(1.2, 0.56, 1.2), Color(0.56, 0.48, 0.72))
	_add_box("FallbackSeatRight", Vector3(2.35, 0.28, -0.25), Vector3(1.2, 0.56, 1.2), Color(0.48, 0.58, 0.72))


func _hide_future_room_markers() -> void:
	if _future_room == null:
		return
	for node in _walk_nodes(_future_room):
		if node is GeometryInstance3D and "marker" in node.name.to_lower():
			(node as GeometryInstance3D).visible = false


func _tune_future_room_for_embodied() -> void:
	if _future_room == null:
		return
	for node in _walk_nodes(_future_room):
		if node is WorldEnvironment:
			var environment := (node as WorldEnvironment).environment
			if environment != null:
				environment.background_color = Color(0.13, 0.18, 0.28, 1.0)
				environment.ambient_light_color = Color(0.70, 0.78, 0.92, 1.0)
				environment.ambient_light_energy = 0.48
				environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
				environment.tonemap_exposure = 0.72
		elif node is Light3D:
			var light := node as Light3D
			light.light_energy = clampf(light.light_energy * 0.34, 0.20, 1.15)
	var face_light := OmniLight3D.new()
	face_light.name = "EmbodiedFaceKeyLight"
	face_light.position = Vector3(-0.4, 2.35, 3.2)
	face_light.light_color = Color(1.0, 0.90, 0.82)
	face_light.light_energy = 0.78
	face_light.omni_range = 7.5
	add_child(face_light)


func _setup_navigation() -> void:
	var region := NavigationRegion3D.new()
	region.name = "NavigationRegion3D"
	var navigation_mesh := NavigationMesh.new()
	navigation_mesh.vertices = PackedVector3Array([
		Vector3(-7.4, 0.0, -5.5),
		Vector3(-7.4, 0.0, 5.5),
		Vector3(7.4, 0.0, 5.5),
		Vector3(7.4, 0.0, -5.5),
	])
	navigation_mesh.add_polygon(PackedInt32Array([0, 1, 2, 3]))
	region.navigation_mesh = navigation_mesh
	add_child(region)

	_body = CharacterBody3D.new()
	_body.name = "EmbodiedCompanion"
	_body.position = (_anchors["companion_idle"] as Node3D).position
	_body.collision_layer = 0
	_body.collision_mask = 0
	add_child(_body)
	var collision := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = 0.20
	capsule.height = 1.55
	collision.shape = capsule
	collision.position.y = 0.78
	_body.add_child(collision)
	var agent := NavigationAgent3D.new()
	agent.name = "NavigationAgent3D"
	_body.add_child(agent)
	_body.rotation_degrees.y = 0.0
	var avatar := _instantiate_candidate()
	if avatar == null:
		push_error("Embodied candidate could not be loaded from source GLB")
		return
	avatar.name = "AogiriEmbodiedV038Review" if _using_latest_motion_bank() else "AogiriEmbodiedV010Safe"
	_body.add_child(avatar)
	_setup_head_touch_zone()

	_runtime = RuntimeScript.new()
	_runtime.name = "EmbodiedRuntime"
	_runtime.integrated_face_morphs_enabled = _using_latest_motion_bank()
	add_child(_runtime)
	_runtime.call("bind_avatar", avatar)
	_runtime.call("bind_navigation", _body, agent, _anchors)
	_runtime.call("bind_affordances", _affordance_objects)
	_runtime.call("set_enabled", true)
	_runtime.call("set_touch_enabled", false)
	_runtime.call("set_tracking_enabled", false)


func _setup_camera() -> void:
	_camera = Camera3D.new()
	_camera.name = "EmbodiedPresentationCamera"
	_camera.fov = 47.0
	_camera.near = 0.05
	add_child(_camera)
	_camera.current = true
	_update_camera(1.0)


func _update_camera(delta: float) -> void:
	if _camera == null or _body == null:
		return
	var desired := Vector3(0.85, 1.68, 4.85)
	_camera.global_position = desired if delta >= 1.0 else _camera.global_position.lerp(desired, clampf(delta * 4.5, 0.0, 1.0))
	var focus := _body.global_position + Vector3(0.75, 0.84, 0.0)
	_camera.look_at(focus, Vector3.UP)
	var distance := _camera.global_position.distance_to(_body.global_position + Vector3(0.0, 0.84, 0.0))
	var target_fov := clampf(46.0 - distance * 3.0, 25.0, 40.0)
	_camera.fov = target_fov if delta >= 1.0 else lerpf(_camera.fov, target_fov, clampf(delta * 3.5, 0.0, 1.0))


func _setup_overlay() -> void:
	var layer := CanvasLayer.new()
	layer.layer = 80
	add_child(layer)
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	layer.add_child(root)

	var title := Label.new()
	title.position = Vector2(32.0, 24.0)
	title.text = "LUMINA — 体験モード"
	title.add_theme_font_size_override("font_size", 28)
	title.add_theme_color_override("font_color", Color(0.92, 0.95, 1.0))
	root.add_child(title)

	var guard := Label.new()
	guard.position = Vector2(34.0, 64.0)
	guard.text = "話す・歩く・座る・反応するルミナ"
	guard.add_theme_font_size_override("font_size", 16)
	guard.add_theme_color_override("font_color", Color(0.72, 0.84, 0.94))
	root.add_child(guard)

	var panel := PanelContainer.new()
	panel.set_anchor(SIDE_LEFT, 0.60)
	panel.set_anchor(SIDE_TOP, 0.035)
	panel.set_anchor(SIDE_RIGHT, 0.985)
	panel.set_anchor(SIDE_BOTTOM, 0.965)
	panel.custom_minimum_size = Vector2(520, 0)
	var panel_style := StyleBoxFlat.new()
	panel_style.bg_color = Color(0.025, 0.035, 0.052, 0.965)
	panel_style.border_color = Color(0.68, 0.82, 0.96, 0.34)
	panel_style.set_border_width_all(1)
	panel_style.set_corner_radius_all(10)
	panel.add_theme_stylebox_override("panel", panel_style)
	root.add_child(panel)

	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 24)
	margin.add_theme_constant_override("margin_top", 22)
	margin.add_theme_constant_override("margin_right", 24)
	margin.add_theme_constant_override("margin_bottom", 20)
	panel.add_child(margin)

	var column := VBoxContainer.new()
	column.add_theme_constant_override("separation", 12)
	margin.add_child(column)

	_backend_label = Label.new()
	_backend_label.text = "接続を確認しています"
	_backend_label.add_theme_font_size_override("font_size", 16)
	_backend_label.add_theme_color_override("font_color", Color(0.86, 0.74, 0.42))
	column.add_child(_backend_label)

	_audio_label = Label.new()
	_audio_label.text = "マイク: 接続を確認しています"
	_audio_label.add_theme_font_size_override("font_size", 16)
	_audio_label.add_theme_color_override("font_color", Color(0.86, 0.74, 0.42))
	column.add_child(_audio_label)

	_state_label = Label.new()
	_state_label.text = "準備中"
	_state_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_state_label.add_theme_font_size_override("font_size", 17)
	_state_label.add_theme_color_override("font_color", Color(0.90, 0.92, 0.96))
	column.add_child(_state_label)

	_transcript = RichTextLabel.new()
	_transcript.bbcode_enabled = false
	_transcript.fit_content = false
	_transcript.scroll_active = true
	_transcript.scroll_following = true
	_transcript.selection_enabled = true
	_transcript.custom_minimum_size = Vector2(0, 250)
	_transcript.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_transcript.add_theme_font_size_override("normal_font_size", 18)
	_transcript.add_theme_color_override("default_color", Color(0.88, 0.90, 0.94))
	column.add_child(_transcript)

	_input_field = LineEdit.new()
	_input_field.placeholder_text = "ルミナへ話しかける"
	_input_field.clear_button_enabled = true
	_input_field.custom_minimum_size = Vector2(0, 48)
	_input_field.add_theme_font_size_override("font_size", 17)
	_input_field.text_submitted.connect(func(text: String) -> void: _submit_text(text))
	column.add_child(_input_field)

	var primary_row := HBoxContainer.new()
	primary_row.add_theme_constant_override("separation", 8)
	column.add_child(primary_row)
	_send_button = _add_button(primary_row, "送信", func() -> void: _submit_text(_input_field.text), true)
	_stop_button = _add_button(primary_row, "停止", _stop_turn)
	_retry_button = _add_button(primary_row, "もう一度", _retry_last)
	_reconnect_button = _add_button(primary_row, "再接続", _request_health)

	var secondary_row := HBoxContainer.new()
	secondary_row.add_theme_constant_override("separation", 8)
	column.add_child(secondary_row)
	_touch_button = _add_button(secondary_row, "頭を撫でる", _trigger_head_pat)
	_return_button = _add_button(secondary_row, "通常の Lumina へ戻る", _return_to_lumina)
	_return_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL

	var footer := Label.new()
	footer.text = "上部のマイク表示を確認してそのまま話せます　／　頭を往復ドラッグ: 撫でる　／　Esc: 戻る"
	footer.add_theme_font_size_override("font_size", 14)
	footer.add_theme_color_override("font_color", Color(0.62, 0.68, 0.75))
	column.add_child(footer)
	_refresh_controls()


func _add_button(parent: Node, text: String, callback: Callable, primary := false) -> Button:
	var button := Button.new()
	button.text = text
	button.custom_minimum_size = Vector2(104, 44)
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.add_theme_font_size_override("font_size", 16)
	button.focus_mode = Control.FOCUS_NONE
	if primary:
		var normal := StyleBoxFlat.new()
		normal.bg_color = Color(0.16, 0.43, 0.39, 0.98)
		normal.set_corner_radius_all(7)
		button.add_theme_stylebox_override("normal", normal)
	button.pressed.connect(callback)
	parent.add_child(button)
	return button


func _instantiate_candidate() -> Node3D:
	var candidate_path := _candidate_source_path()
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	var error := document.append_from_file(candidate_path, state)
	if error != OK:
		push_error("Candidate GLB source load failed: %s error=%s" % [candidate_path, error])
		return null
	var generated := document.generate_scene(state)
	return generated as Node3D


func _candidate_source_path() -> String:
	return V038_CANDIDATE_SOURCE_PATH if _using_latest_motion_bank() else CANDIDATE_SOURCE_PATH


func _using_latest_motion_bank() -> bool:
	return _env_enabled("AOGIRI_USE_LATEST_MOTIONS") or _env_enabled("AOGIRI_USE_V033_MOTIONS")


func _expected_motion_name(latest_name: String, legacy_name: String) -> String:
	return latest_name if _using_latest_motion_bank() else legacy_name


func _walk_nodes(root: Node) -> Array[Node]:
	var output: Array[Node] = []
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		output.append(node)
		for child in node.get_children():
			stack.append(child)
	return output


func _add_anchor(anchor_id: String, position: Vector3, yaw_degrees := 0.0) -> void:
	var marker := Marker3D.new()
	marker.name = anchor_id
	marker.position = position
	marker.rotation_degrees.y = yaw_degrees
	add_child(marker)
	_anchors[anchor_id] = marker


func _add_box(name: String, position: Vector3, size: Vector3, color: Color) -> void:
	var body := StaticBody3D.new()
	body.name = name
	body.position = position
	var mesh_instance := MeshInstance3D.new()
	var box := BoxMesh.new()
	box.size = size
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = 0.78
	box.material = material
	mesh_instance.mesh = box
	body.add_child(mesh_instance)
	var collision := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = size
	collision.shape = shape
	body.add_child(collision)
	add_child(body)


func _on_look_at_requested(_action_id: String, target: String, _duration_ms: int) -> void:
	var normalized := target.strip_edges().to_lower()
	if not _anchors.has(normalized):
		return
	var target_node := _anchors[normalized] as Node3D
	var direction := target_node.global_position - _body.global_position
	direction.y = 0.0
	if direction.length() > 0.001:
		_body.rotation.y = atan2(direction.x, direction.z)


func _run_presentation_capture_only() -> void:
	for _frame in range(8):
		await get_tree().physics_frame
	_body.global_position = (_anchors["window_seat"] as Node3D).global_position
	_body.global_rotation.y = 0.0
	_runtime.call(
		"dispatch_intent",
		{
			"action_id": "presentation_sit",
			"type": "living_state",
			"source": "presentation_capture",
			"params": {"state": "sit"},
		}
	)
	_set_state("会話を始められます")
	_append_transcript("あなた", "ここで一緒に話そう。")
	_append_transcript("ルミナ", "うん。落ち着いて、最後までゆっくり話そう。")
	for _frame in range(30):
		await get_tree().process_frame
	var capture_path := OS.get_environment("AOGIRI_CAPTURE_PATH").strip_edges()
	if not capture_path.is_empty():
		await _save_capture(capture_path)
	print("AOGIRI_PRESENTATION_CAPTURE_ONLY position=%s animation=%s" % [
		_body.global_position,
		_runtime.call("get_action_adapter").call("get_current_animation"),
	])
	print("AOGIRI_PRESENTATION_SCREEN body=%s viewport=%s fov=%.2f camera=%s" % [
		_camera.unproject_position(_body.global_position + Vector3(0.0, 0.82, 0.0)),
		get_viewport().get_visible_rect().size,
		_camera.fov,
		_camera.global_position,
	])
	get_tree().quit()


func _run_autotest() -> void:
	_autotest_running = true
	var ready_deadline := Time.get_ticks_msec() + 12000
	while not _backend_ready and Time.get_ticks_msec() < ready_deadline:
		await get_tree().process_frame
	var test_text := OS.get_environment("AOGIRI_INTERACTIVE_TEST_TEXT").strip_edges()
	if test_text.is_empty():
		test_text = "窓辺の椅子に座って、前に一緒に見た雨を覚えているか、途中で切らず二文以上で最後まで話して。"
	var submitted := _submit_text(test_text)
	var turn_deadline := Time.get_ticks_msec() + 180000
	while _turn_busy and Time.get_ticks_msec() < turn_deadline:
		await get_tree().process_frame
	var first_health_count := _health_success_count
	if _backend_ready and not bool(_backend.call("is_busy")):
		_backend.call("request_health")
	var reconnect_deadline := Time.get_ticks_msec() + 12000
	while _health_success_count <= first_health_count and Time.get_ticks_msec() < reconnect_deadline:
		await get_tree().process_frame
	var voice_status: Dictionary = _voice.call("get_status")
	var runtime_status: Dictionary = _runtime.call("get_status")
	var reply_complete := bool(_last_backend_payload.get("reply", {}).get("complete", false))
	var checks := {
		"backend_ready": _backend_ready,
		"text_submission_started": submitted,
		"reply_non_empty": not _last_reply.is_empty(),
		"reply_marked_complete": reply_complete,
		"reply_long_enough_for_full_display": _last_reply.length() >= 40,
		"transcript_contains_full_reply": not _last_reply.is_empty() and _last_reply in _transcript_text,
		"plan_completed": _last_plan_ok and _plan_done,
		"speech_started_while_seated": _speech_posture == "sitting",
		"final_animation_sit_idle": String(_runtime.call("get_action_adapter").call("get_current_animation")) == _expected_motion_name("Lumina_Sit_Idle", "Aogiri_SitIdle"),
		"all_voice_segments_completed": int(voice_status.get("total_segments", 0)) > 0 and int(voice_status.get("completed_segments", 0)) == int(voice_status.get("total_segments", -1)),
		"reconnect_health_succeeded": _health_success_count > first_health_count,
		"tracking_remains_default_off": not bool(runtime_status.get("tracking_active", true)),
		"touch_returns_default_off": not bool(runtime_status.get("touch_active", true)),
		"live_lumina_route_unchanged": bool(runtime_status.get("live_lumina_route_changed", true)) == false,
		"rollback_target_configured": return_scene_path == "res://scenes/demo_main.tscn",
	}
	var report := {
		"status": "PASS" if _all_checks_pass(checks) else "FAIL",
		"checks": checks,
		"test_text": test_text,
		"reply": _last_reply,
		"reply_length": _last_reply.length(),
		"transcript": _transcript_text,
		"plan_results": _runtime.call("get_plan_executor").call("get_results"),
		"speech_posture": _speech_posture,
		"final_animation": _runtime.call("get_action_adapter").call("get_current_animation"),
		"voice_status": voice_status,
		"runtime_status": runtime_status,
		"health_success_count": _health_success_count,
		"last_error": _last_error,
		"turn_completion_count": _turn_completion_count,
		"live_lumina_route_changed": false,
	}
	_write_autotest_report(report)
	var capture_path := OS.get_environment("AOGIRI_CAPTURE_PATH").strip_edges()
	if not capture_path.is_empty():
		await _save_capture(capture_path)
	print("AOGIRI_INTERACTIVE_AUTOTEST status=%s checks=%s" % [report.status, JSON.stringify(checks)])
	_autotest_running = false
	get_tree().quit(0 if report.status == "PASS" else 1)


func _run_full_acceptance_autotest() -> void:
	_autotest_running = true
	var ready_deadline := Time.get_ticks_msec() + 12000
	while not _backend_ready and Time.get_ticks_msec() < ready_deadline:
		await get_tree().process_frame

	for _frame in range(3):
		await get_tree().physics_frame
	var head_screen_position := _camera.unproject_position(_head_touch_area.global_position)
	var pointer_ray_hits_head := _ray_hits_head(head_screen_position)
	if pointer_ray_hits_head:
		_begin_pointer_head_touch(head_screen_position)
		for offset in [-36.0, 36.0, -36.0, 36.0]:
			await get_tree().create_timer(0.09).timeout
			_update_pointer_head_touch(head_screen_position + Vector2(0.0, offset))
		await get_tree().create_timer(0.09).timeout
		_end_pointer_head_touch(head_screen_position)
	await get_tree().create_timer(0.15).timeout
	var pointer_touch_event: Dictionary = _runtime.call("get_last_touch_event")
	var touch_reflex: Dictionary = _runtime.call("get_last_reflex_result")
	var runtime_after_touch: Dictionary = _runtime.call("get_status")
	var head_pat_animation := String(_runtime.call("get_action_adapter").call("get_current_animation"))
	var face_controller: Node = _runtime.call("get_face_controller")
	var face_safety_status: Dictionary = face_controller.call("get_face_safety_status")
	var integrated_latest_face := _using_latest_motion_bank()
	var head_pat_face_weights := {}
	var head_pat_overlays_zero := true
	for shape_name in ["Blink", "BlinkLeft", "BlinkRight", "Happy", "Relaxed", "Sad", "Surprised", "Shy", "Confused", "Viseme_A", "Viseme_I", "Viseme_U", "Viseme_E", "Viseme_O"]:
		var weight := float(face_controller.call("get_shape_weight", shape_name))
		head_pat_face_weights[shape_name] = weight
		if absf(weight) > 0.001:
			head_pat_overlays_zero = false

	_last_cancel_payload = {}
	var cancel_text := OS.get_environment("AOGIRI_CANCEL_TEST_TEXT").strip_edges()
	if cancel_text.is_empty():
		cancel_text = "考えている途中で停止できるか確認します。窓辺に座り、今日の出来事を詳しく三文以上で話してください。"
	var cancel_request_started := _submit_text(cancel_text)
	var cancelled_request_id := _current_request_id
	if cancel_request_started:
		await get_tree().create_timer(0.35).timeout
	_stop_turn()
	var cancel_deadline := Time.get_ticks_msec() + 12000
	while _last_cancel_payload.is_empty() and Time.get_ticks_msec() < cancel_deadline:
		await get_tree().process_frame
	var stop_returned_ui_to_idle := not _turn_busy and _last_error == "cancelled_by_user"

	var health_after_cancel_before := _health_success_count
	if not bool(_backend.call("is_busy")):
		_backend.call("request_health")
	var health_after_cancel_deadline := Time.get_ticks_msec() + 12000
	while _health_success_count <= health_after_cancel_before and Time.get_ticks_msec() < health_after_cancel_deadline:
		await get_tree().process_frame
	var active_after_cancel := int(_last_health_payload.get("active_intents", -1))

	var retry_started := false
	if not _turn_busy and _backend_ready:
		retry_started = _submit_text(_last_text)
	var retry_deadline := Time.get_ticks_msec() + 180000
	while _turn_busy and Time.get_ticks_msec() < retry_deadline:
		await get_tree().process_frame

	var reconnect_before := _health_success_count
	if _backend_ready and not bool(_backend.call("is_busy")):
		_backend.call("request_health")
	var reconnect_deadline := Time.get_ticks_msec() + 12000
	while _health_success_count <= reconnect_before and Time.get_ticks_msec() < reconnect_deadline:
		await get_tree().process_frame

	var voice_status: Dictionary = _voice.call("get_status")
	var runtime_status: Dictionary = _runtime.call("get_status")
	var reply_complete := bool(_last_backend_payload.get("reply", {}).get("complete", false))
	var cancel_acknowledged := (
		bool(_last_cancel_payload.get("ok", false))
		and String(_last_cancel_payload.get("request_id", "")) == cancelled_request_id
		and bool(_last_cancel_payload.get("cancelled", false))
	)
	var checks := {
		"backend_ready": _backend_ready,
		"visible_head_zone_is_ray_pickable": pointer_ray_hits_head,
		"pointer_head_drag_classifies_as_pat": String(pointer_touch_event.get("zone", "")) == "head" and String(pointer_touch_event.get("gesture", "")) == "pat",
		"head_pat_reflex_accepted": bool(touch_reflex.get("accepted", false)),
		"head_pat_uses_independent_safe_reaction": head_pat_animation == _expected_motion_name("Lumina_React_Head_Pat", "Aogiri_HeadPatReaction"),
		"head_pat_reports_expected_expression": String(touch_reflex.get("expression", "")) == ("head_pat" if integrated_latest_face else "neutral"),
		"head_pat_face_state_is_safe": (
			(float(head_pat_face_weights.get("Relaxed", 0.0)) > 0.45 and float(head_pat_face_weights.get("Happy", 0.0)) > 0.12)
			if integrated_latest_face
			else head_pat_overlays_zero
		),
		"synthetic_face_overlays_disabled": not bool(face_safety_status.get("synthetic_face_overlays_enabled", true)),
		"automatic_blink_matches_face_mode": bool(face_safety_status.get("auto_blink_enabled", false)) == integrated_latest_face,
		"touch_disabled_after_head_pat": not bool(runtime_after_touch.get("touch_active", true)),
		"cancel_request_started": cancel_request_started,
		"stop_returned_ui_to_idle": stop_returned_ui_to_idle,
		"server_cancel_acknowledged": cancel_acknowledged,
		"server_active_intents_zero_after_cancel": active_after_cancel == 0,
		"retry_started": retry_started,
		"retry_completed": _turn_completion_count >= 1 and not _turn_busy,
		"retry_reply_non_empty": not _last_reply.is_empty(),
		"retry_reply_marked_complete": reply_complete,
		"retry_reply_long_enough_for_full_display": _last_reply.length() >= 40,
		"transcript_contains_full_retry_reply": not _last_reply.is_empty() and _last_reply in _transcript_text,
		"retry_plan_completed": _last_plan_ok and _plan_done,
		"all_voice_segments_completed": int(voice_status.get("total_segments", 0)) > 0 and int(voice_status.get("completed_segments", 0)) == int(voice_status.get("total_segments", -1)),
		"reconnect_health_succeeded": _health_success_count > reconnect_before,
		"tracking_remains_default_off": not bool(runtime_status.get("tracking_active", true)),
		"touch_returns_default_off": not bool(runtime_status.get("touch_active", true)),
		"live_lumina_route_unchanged": bool(runtime_status.get("live_lumina_route_changed", true)) == false,
	}
	var report := {
		"status": "PASS" if _all_checks_pass(checks) else "FAIL",
		"checks": checks,
		"cancelled_request_id": cancelled_request_id,
		"cancel_payload": _last_cancel_payload,
		"active_intents_after_cancel": active_after_cancel,
		"touch_reflex": touch_reflex,
		"pointer_touch_event": pointer_touch_event,
		"head_screen_position": head_screen_position,
		"head_pat_animation": head_pat_animation,
		"head_pat_face_weights": head_pat_face_weights,
		"face_safety_status": face_safety_status,
		"reply": _last_reply,
		"reply_length": _last_reply.length(),
		"transcript": _transcript_text,
		"voice_status": voice_status,
		"runtime_status": runtime_status,
		"health_success_count": _health_success_count,
		"last_error": _last_error,
		"turn_completion_count": _turn_completion_count,
	}
	_write_autotest_report(report)
	var capture_path := OS.get_environment("AOGIRI_CAPTURE_PATH").strip_edges()
	if not capture_path.is_empty():
		await _save_capture(capture_path)
	print("AOGIRI_FULL_ACCEPTANCE_AUTOTEST status=%s checks=%s" % [report.status, JSON.stringify(checks)])
	_autotest_running = false
	get_tree().quit(0 if report.status == "PASS" else 1)


func _write_autotest_report(report: Dictionary) -> void:
	var path := OS.get_environment("AOGIRI_INTERACTIVE_REPORT_PATH").strip_edges()
	if path.is_empty():
		return
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		push_error("Could not write interactive report: %s" % path)
		return
	file.store_string(JSON.stringify(report, "  ") + "\n")
	print("AOGIRI_INTERACTIVE_REPORT path=%s status=%s" % [path, report.status])


func _save_capture(path: String) -> void:
	DisplayServer.window_set_position(Vector2i(0, 40))
	DisplayServer.window_move_to_foreground()
	for _frame in range(8):
		await get_tree().process_frame
		await RenderingServer.frame_post_draw
	var error := get_viewport().get_texture().get_image().save_png(path)
	print("AOGIRI_INTERACTIVE_CAPTURE path=%s error=%s" % [path, error])


func _all_checks_pass(checks: Dictionary) -> bool:
	for value in checks.values():
		if not bool(value):
			return false
	return true


func _env_enabled(name: String) -> bool:
	return OS.get_environment(name).strip_edges().to_lower() in ["1", "true", "yes", "on"]
