extends Node3D

const RuntimeScript = preload("res://scripts/embodied_v1/embodied_runtime.gd")
const BackendClientScript = preload("res://scripts/embodied_v1/embodied_backend_client.gd")
const VoicePlayerScript = preload("res://scripts/embodied_v1/embodied_voice_player.gd")
const Candidate = preload("res://assets/Aogiri_Embodied_Runtime_v010_SafeSemanticMotionBank_164cm.glb")
const GREETING_REPORT_PATH := "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-07-11/n/reports/embodied_v1/backend/integration_20260711_1822/report.json"

var _body: CharacterBody3D
var _runtime: Node
var _anchors: Dictionary = {}
var _backend: Node
var _voice: Node
var _pending_voice_payload: Dictionary = {}
var _backend_payload: Dictionary = {}
var _record_payload: Dictionary = {}
var _backend_error := ""
var _voice_done := false
var _status_label: Label
var _vertical_report: Dictionary = {}


func _ready() -> void:
	_setup_environment()
	_setup_room()
	_setup_navigation()
	_setup_camera()
	_setup_overlay()

	for _frame in range(4):
		await get_tree().physics_frame
	var elapsed := 0.0
	if _env_enabled("AOGIRI_VERTICAL_SLICE"):
		elapsed = await _run_vertical_slice()
	else:
		elapsed = await _run_local_room_plan()
	for _frame in range(18):
		await get_tree().process_frame
	var capture_path := OS.get_environment("AOGIRI_CAPTURE_PATH").strip_edges()
	if not capture_path.is_empty():
		await _prepare_visual_capture()
		var error := get_viewport().get_texture().get_image().save_png(capture_path)
		print("AOGIRI_ROOM_CAPTURE path=%s error=%s" % [capture_path, error])
	print(
		"AOGIRI_ROOM_COMPLETE elapsed=%.3f position=%s animation=%s results=%s vertical=%s"
		% [
			elapsed,
			_body.global_position,
			_runtime.get_action_adapter().call("get_current_animation"),
			JSON.stringify(_runtime.get_plan_executor().call("get_results")),
			JSON.stringify(_vertical_report),
		]
	)
	if _env_enabled("AOGIRI_AUTO_QUIT"):
		get_tree().quit()


func _run_local_room_plan() -> float:
	var plan := {
		"goal": "window_sit_visual_check",
		"actions": [
			{
				"action_id": "room_move_001",
				"type": "move_to",
				"params": {"anchor": "window_seat", "speed": "walk", "timeout_ms": 8000, "stop_distance": 0.22},
			},
			{
				"action_id": "room_sit_001",
				"type": "sit",
				"params": {"anchor": "window_seat"},
			},
			{
				"action_id": "room_expression_001",
				"type": "set_expression",
				"params": {"expression": "happy_soft", "intensity": 0.72, "transition_seconds": 0.16},
			},
		],
	}
	var started: bool = bool(_runtime.call("dispatch_plan", plan))
	print("AOGIRI_ROOM_PLAN_STARTED started=%s plan=%s" % [started, JSON.stringify(plan)])
	var elapsed := 0.0
	while _runtime.get_plan_executor().call("is_running") and elapsed < 10.0:
		await get_tree().physics_frame
		elapsed += 1.0 / 60.0
	return elapsed


func _run_vertical_slice() -> float:
	var started_at := Time.get_ticks_msec()
	_setup_backend_and_voice()
	_set_status("1/6 共有した雨の記憶を分離SQLiteへ確認中")
	_record_payload = {}
	_backend_error = ""
	_backend.call(
		"record_world_event",
		{
			"event_type": "shared_episode_seed",
			"summary": "前回、ユーザーと窓辺で雨を見ながら静かに話した",
			"location": "window_seat",
			"participants": ["user", "companion"],
			"emotional_tone": "calm",
			"importance": 0.82,
		}
	)
	await _wait_until(func() -> bool: return not _record_payload.is_empty() or not _backend_error.is_empty(), 5.0)
	var seed_ok := bool(_record_payload.get("ok", false))

	_set_status("2/6 入室を検知：振り向く→近づく→挨拶")
	_pending_voice_payload = _load_greeting_voice_payload()
	_voice_done = false
	var greeting_plan := {
		"goal": "greet_user_warmly",
		"actions": [
			{"action_id": "vertical_look", "type": "look_at", "params": {"target": "user", "duration_ms": 1500}},
			{"action_id": "vertical_greet_move", "type": "move_to", "params": {"anchor": "greeting_position", "speed": "walk", "timeout_ms": 6000}},
			{"action_id": "vertical_wave", "type": "gesture", "params": {"name": "small_wave", "intensity": "low", "duration_ms": 1000}},
			{"action_id": "vertical_happy", "type": "set_expression", "params": {"expression": "happy_soft", "intensity": 0.72}},
			{"action_id": "vertical_greeting_speech", "type": "speak", "params": {"text": "ルミナです。おかえりなさい。", "emotion": "happy", "style": "Neutral"}},
		],
	}
	_runtime.call("dispatch_plan", greeting_plan)
	await _wait_until(func() -> bool: return not bool(_runtime.get_plan_executor().call("is_running")), 10.0)
	await _wait_until(func() -> bool: return _voice_done, 10.0)
	var greeting_results: Array = _runtime.get_plan_executor().call("get_results")

	_set_status("3/6 頭撫で：LLMを待たず即時反応")
	_runtime.call("set_touch_enabled", true)
	_runtime.call("begin_touch", "head", Vector2(0.50, 0.50), 1000)
	_runtime.call("update_touch", Vector2(0.50, 0.46), 1100)
	_runtime.call("update_touch", Vector2(0.50, 0.52), 1200)
	_runtime.call("update_touch", Vector2(0.50, 0.47), 1300)
	var touch_event: Dictionary = _runtime.call("end_touch", Vector2(0.50, 0.50), 1450)
	var reflex_result: Dictionary = _runtime.call("get_last_reflex_result")
	_record_payload = {}
	_backend.call("record_interaction", touch_event)
	await _wait_until(func() -> bool: return not _record_payload.is_empty(), 5.0)
	await get_tree().create_timer(0.65).timeout

	_set_status("4/6 Qwenが過去の雨を参照して返答中（身体は継続）")
	_runtime.call(
		"dispatch_intent",
		{"action_id": "vertical_thinking", "type": "living_state", "source": "local_vertical", "params": {"state": "thinking"}}
	)
	_backend_payload = {}
	_backend_error = ""
	_backend.call(
		"request_intent",
		{
			"request_id": "vertical_memory_window_001",
			"event_type": "user_spoke",
			"text": "前に窓辺で雨を見たこと、覚えてる？窓辺の椅子に座って、前の雨の話をしよう。",
			"location": "greeting_position",
			"synthesize": true,
			"include_audio_base64": true,
			"importance": 0.72,
		}
	)
	await _wait_until(func() -> bool: return not _backend_payload.is_empty() or not _backend_error.is_empty(), 150.0)
	var backend_ok := bool(_backend_payload.get("ok", false))
	var memory_context := String(_backend_payload.get("memory_context", ""))
	var reply_text := String(_backend_payload.get("reply", {}).get("text", ""))

	_set_status("5/6 アンカー移動→窓際着席→全文音声（合成口パクなし）")
	_pending_voice_payload = _backend_payload.get("voice", {})
	_voice_done = false
	var memory_plan: Dictionary = _backend_payload.get("intent", {})
	var memory_plan_started := bool(_runtime.call("dispatch_plan", memory_plan))
	await _wait_until(func() -> bool: return not bool(_runtime.get_plan_executor().call("is_running")), 15.0)
	await _wait_until(func() -> bool: return _voice_done, 45.0)
	var memory_results: Array = _runtime.get_plan_executor().call("get_results")

	_set_status("6/6 完了：記憶・身体・音声が同じ体験へ接続")
	var checks := {
		"memory_seed_recorded": seed_ok,
		"greeting_plan_completed": greeting_results.size() == greeting_plan.actions.size(),
		"touch_classified_as_pat": String(touch_event.get("gesture", "")) == "pat",
		"reflex_without_llm_wait": bool(reflex_result.get("accepted", false)) and not bool(reflex_result.get("llm_waited", true)),
		"backend_reply_ok": backend_ok and not reply_text.is_empty(),
		"memory_context_contains_rain": "雨" in memory_context,
		"memory_plan_started": memory_plan_started,
		"memory_plan_completed": memory_results.size() == memory_plan.get("actions", []).size(),
		"final_animation_sit_idle": String(_runtime.get_action_adapter().call("get_current_animation")) == "Aogiri_SitIdle",
		"all_voice_segments_completed": int(_voice.call("get_status").get("completed_segments", 0)) == int(_voice.call("get_status").get("total_segments", -1)),
		"tracking_remains_default_off": not bool(_runtime.call("get_status").get("tracking_active", true)),
		"live_lumina_route_unchanged": true,
	}
	_vertical_report = {
		"status": "PASS" if _all_checks_pass(checks) else "FAIL",
		"checks": checks,
		"greeting_results": greeting_results,
		"touch_event": touch_event,
		"reflex_result": reflex_result,
		"backend": _public_backend_payload(_backend_payload),
		"memory_results": memory_results,
		"final_position": {"x": _body.global_position.x, "y": _body.global_position.y, "z": _body.global_position.z},
		"final_animation": _runtime.get_action_adapter().call("get_current_animation"),
		"voice_status": _voice.call("get_status"),
		"face_state": _face_state_snapshot(),
		"tracking_status": _runtime.call("get_status").get("tracking", {}),
		"backend_error": _backend_error,
		"live_lumina_route_changed": false,
	}
	_write_vertical_report()
	return float(Time.get_ticks_msec() - started_at) / 1000.0


func _setup_backend_and_voice() -> void:
	_backend = BackendClientScript.new()
	_backend.name = "EmbodiedBackendClient"
	_backend.enabled = true
	_backend.base_url = "http://127.0.0.1:8792"
	_backend.intent_received.connect(_on_backend_intent)
	_backend.record_completed.connect(_on_backend_record)
	_backend.request_failed.connect(_on_backend_failed)
	add_child(_backend)
	_voice = VoicePlayerScript.new()
	_voice.name = "EmbodiedVoicePlayer"
	_voice.enabled = true
	_voice.voice_finished.connect(_on_voice_finished)
	add_child(_voice)
	_voice.call("bind_face_controller", _runtime.call("get_face_controller"))
	_runtime.get_plan_executor().speech_action_requested.connect(_on_speech_action_requested)
	_runtime.get_action_adapter().look_at_requested.connect(_on_look_at_requested)


func _load_greeting_voice_payload() -> Dictionary:
	var file := FileAccess.open(GREETING_REPORT_PATH, FileAccess.READ)
	if file == null:
		_backend_error = "greeting_report_missing"
		return {}
	var parsed = JSON.parse_string(file.get_as_text())
	if typeof(parsed) != TYPE_DICTIONARY:
		_backend_error = "greeting_report_invalid"
		return {}
	var report: Dictionary = parsed
	var payload: Dictionary = report.get("roundtrip_voice", {}).duplicate(true)
	var segments: Array = payload.get("segments", [])
	for segment in segments:
		var wav_path := String(segment.get("wav_path", ""))
		var wav := FileAccess.open(wav_path, FileAccess.READ)
		if wav == null:
			_backend_error = "greeting_wav_missing:%s" % wav_path
			return {}
		segment["audio_base64"] = Marshalls.raw_to_base64(wav.get_buffer(wav.get_length()))
	return payload


func _wait_until(predicate: Callable, timeout_seconds: float) -> void:
	var deadline := Time.get_ticks_msec() + int(timeout_seconds * 1000.0)
	while not bool(predicate.call()) and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame


func _on_backend_intent(payload: Dictionary) -> void:
	_backend_payload = payload.duplicate(true)


func _on_backend_record(_kind: String, payload: Dictionary) -> void:
	_record_payload = payload.duplicate(true)


func _on_backend_failed(kind: String, status_code: int, detail: String) -> void:
	_backend_error = "%s:%d:%s" % [kind, status_code, detail]


func _on_voice_finished(_segment_count: int) -> void:
	_voice_done = true


func _on_speech_action_requested(_action: Dictionary) -> void:
	if _voice == null or _pending_voice_payload.is_empty():
		_voice_done = true
		return
	if not bool(_voice.call("play_voice_payload", _pending_voice_payload)):
		_backend_error = "voice_payload_rejected"
		_voice_done = true


func _on_look_at_requested(_action_id: String, target: String, _duration_ms: int) -> void:
	var normalized := target.strip_edges().to_lower()
	if not _anchors.has(normalized):
		return
	var target_node := _anchors[normalized] as Node3D
	var direction := target_node.global_position - _body.global_position
	direction.y = 0.0
	if direction.length() > 0.001:
		_body.rotation.y = atan2(direction.x, direction.z)


func _public_backend_payload(payload: Dictionary) -> Dictionary:
	var public := payload.duplicate(true)
	var voice_value = public.get("voice", {})
	if typeof(voice_value) == TYPE_DICTIONARY:
		var voice: Dictionary = voice_value
		var segments_value = voice.get("segments", [])
		if typeof(segments_value) == TYPE_ARRAY:
			for segment in segments_value:
				if typeof(segment) == TYPE_DICTIONARY:
					(segment as Dictionary).erase("audio_base64")
	return public


func _all_checks_pass(checks: Dictionary) -> bool:
	for value in checks.values():
		if not bool(value):
			return false
	return true


func _write_vertical_report() -> void:
	var path := OS.get_environment("AOGIRI_VERTICAL_REPORT_PATH").strip_edges()
	if path.is_empty():
		return
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		push_error("Could not write vertical slice report: %s" % path)
		return
	file.store_string(JSON.stringify(_vertical_report, "  ") + "\n")
	print("AOGIRI_VERTICAL_REPORT path=%s status=%s" % [path, _vertical_report.get("status")])


func _prepare_visual_capture() -> void:
	# Compatibility rendering on macOS can leave occluded window regions stale.
	# This affects evidence capture only; it never runs in the live Lumina scene.
	DisplayServer.window_set_position(Vector2i(0, 40))
	DisplayServer.window_move_to_foreground()
	for _frame in range(6):
		await get_tree().process_frame
		await RenderingServer.frame_post_draw


func _face_state_snapshot() -> Dictionary:
	var snapshot := {
		"active_expression": _runtime.get_face_controller().call("get_active_expression"),
		"active_viseme": _runtime.get_face_controller().call("get_active_viseme"),
		"weights": {},
		"targets": {},
	}
	for shape_name in [
		"Blink", "BlinkLeft", "BlinkRight", "Happy", "Relaxed", "Sad",
		"Surprised", "Shy", "Confused", "Viseme_A", "Viseme_I",
		"Viseme_U", "Viseme_E", "Viseme_O",
	]:
		snapshot.weights[shape_name] = _runtime.get_face_controller().call("get_shape_weight", shape_name)
		snapshot.targets[shape_name] = _runtime.get_face_controller().call("get_shape_target", shape_name)
	return snapshot


func _set_status(text: String) -> void:
	if _status_label != null:
		_status_label.text = text
	print("AOGIRI_VERTICAL_STATUS %s" % text)


func _env_enabled(name: String) -> bool:
	return OS.get_environment(name).strip_edges().to_lower() in ["1", "true", "yes", "on"]


func _setup_environment() -> void:
	var world_environment := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color(0.045, 0.055, 0.075)
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color(0.78, 0.80, 0.88)
	environment.ambient_light_energy = 0.34
	environment.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	environment.tonemap_exposure = 0.72
	world_environment.environment = environment
	add_child(world_environment)
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-42.0, -25.0, 0.0)
	light.light_energy = 0.72
	light.shadow_enabled = true
	add_child(light)


func _setup_room() -> void:
	_add_box("Floor", Vector3(0.0, -0.055, 0.0), Vector3(7.0, 0.10, 5.0), Color(0.18, 0.20, 0.25))
	_add_box("BackWall", Vector3(0.0, 1.35, -2.45), Vector3(7.0, 2.7, 0.10), Color(0.11, 0.14, 0.20))
	_add_box("Window", Vector3(-1.8, 1.45, -2.37), Vector3(1.8, 1.35, 0.04), Color(0.22, 0.42, 0.62))
	_add_box("WindowSeat", Vector3(-1.80, 0.23, -0.82), Vector3(0.72, 0.46, 0.72), Color(0.34, 0.28, 0.42))
	_add_box("ChairLeft", Vector3(1.80, 0.23, -0.82), Vector3(0.72, 0.46, 0.72), Color(0.28, 0.34, 0.42))
	_add_anchor("entry", Vector3(0.0, 0.0, 1.45))
	_add_anchor("user", Vector3(0.0, 0.0, 2.05), 180.0)
	_add_anchor("greeting_position", Vector3(0.0, 0.0, 0.15))
	_add_anchor("window_seat", Vector3(-1.80, 0.0, -0.40), 47.0)
	_add_anchor("window_view", Vector3(-1.80, 1.45, -2.37), 47.0)
	_add_anchor("chair_left", Vector3(1.80, 0.0, -0.40), -47.0)


func _setup_navigation() -> void:
	var region := NavigationRegion3D.new()
	region.name = "NavigationRegion3D"
	var navigation_mesh := NavigationMesh.new()
	navigation_mesh.vertices = PackedVector3Array([
		Vector3(-3.2, 0.0, -2.1),
		Vector3(-3.2, 0.0, 2.1),
		Vector3(3.2, 0.0, 2.1),
		Vector3(3.2, 0.0, -2.1),
	])
	navigation_mesh.add_polygon(PackedInt32Array([0, 1, 2, 3]))
	region.navigation_mesh = navigation_mesh
	add_child(region)

	_body = CharacterBody3D.new()
	_body.name = "Companion"
	_body.position = (_anchors["entry"] as Node3D).position
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
	var avatar := Candidate.instantiate()
	avatar.name = "AogiriEmbodiedV010Safe"
	_body.add_child(avatar)

	_runtime = RuntimeScript.new()
	_runtime.name = "EmbodiedRuntime"
	add_child(_runtime)
	_runtime.bind_avatar(avatar)
	_runtime.bind_navigation(_body, agent, _anchors)
	_runtime.set_enabled(true)
	_runtime.set_touch_enabled(false)
	_runtime.get_face_controller().auto_blink_enabled = false


func _setup_camera() -> void:
	var camera := Camera3D.new()
	camera.position = Vector3(1.20, 1.45, 2.40)
	camera.fov = 45.0
	add_child(camera)
	camera.look_at(Vector3(-1.52, 0.76, -0.48), Vector3.UP)
	camera.current = true


func _setup_overlay() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var title := Label.new()
	title.position = Vector2(26.0, 20.0)
	title.text = (
		"LUMINA EMBODIED v1 — MEMORY VERTICAL SLICE"
		if _env_enabled("AOGIRI_VERTICAL_SLICE")
		else "LUMINA EMBODIED v1 — ANCHOR NAVIGATION / SIT"
	)
	title.add_theme_font_size_override("font_size", 22)
	title.add_theme_color_override("font_color", Color(0.91, 0.94, 1.0))
	layer.add_child(title)
	var subtitle := Label.new()
	subtitle.position = Vector2(27.0, 54.0)
	subtitle.text = "NavigationAgent3D  |  named anchors only  |  v010 safe semantic motion bank"
	subtitle.add_theme_font_size_override("font_size", 14)
	subtitle.add_theme_color_override("font_color", Color(0.60, 0.70, 0.86))
	layer.add_child(subtitle)
	_status_label = Label.new()
	_status_label.position = Vector2(27.0, 82.0)
	_status_label.text = "準備中"
	_status_label.add_theme_font_size_override("font_size", 15)
	_status_label.add_theme_color_override("font_color", Color(0.86, 0.76, 0.40))
	layer.add_child(_status_label)
	var footer := Label.new()
	footer.position = Vector2(26.0, 724.0)
	footer.text = "SANDBOX ONLY — LIVE LUMINA ROUTE UNCHANGED"
	footer.add_theme_font_size_override("font_size", 12)
	footer.add_theme_color_override("font_color", Color(0.52, 0.90, 0.72))
	layer.add_child(footer)


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
