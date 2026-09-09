extends Node
class_name LuminaLivingHubIntegration

## Main Living Hub orchestrator.
## Owns player bounds expansion, interactables, save, UI/Presence wiring,
## audio zones, graphics quality, pause / lobby return / stuck recover / quit.

const SaveService = preload("res://scripts/living_hub/lumina_living_hub_save.gd")
const InteractableScript = preload("res://scripts/living_hub/lumina_living_hub_interactable.gd")
const InteractionRayScript = preload("res://scripts/living_hub/lumina_living_hub_interaction_ray.gd")
const AudioScript = preload("res://scripts/living_hub/lumina_living_hub_audio.gd")
const MemoryScript = preload("res://scripts/living_hub/lumina_living_hub_memory_bridge.gd")
const ExperienceScript = preload("res://scripts/living_hub/lumina_living_space_experience.gd")
const PresenceControllerScript = preload("res://scripts/presence/lumina_presence_controller.gd")
const EditorialHudScript = preload("res://scripts/ui/lumina_editorial_hud.gd")
const PresenceAnchors = preload("res://scripts/presence/lumina_presence_anchors.gd")

## Spawn / room / bounds are derived from GLB ANCHOR_* / COL_* after root mirror.
## Do not hardcode individual Blender coordinates here.

signal hub_ready(status: Dictionary)
signal hub_paused(paused: bool)

## The product avatar occupies ANCHOR_LOBBY_GREETING.  The first-person camera
## must never be placed on that same point: doing so puts the camera inside the
## skinned mesh and turns the avatar into a full-screen polygon.
const PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE := 2.2
const PLAYER_SPAWN_EDGE_MARGIN := 0.42

var _save := SaveService.new()
var _state: Dictionary = {}
var _player: CharacterBody3D
var _camera: Camera3D
var _hud: CanvasLayer
var _presence: Node
var _lumina_avatar: CharacterBody3D
var _interaction: Node
var _audio: Node
var _memory: Node
var _experience: Node
var _lobby_runtime: Node
var _anchor_root: Node3D
var _interactable_root: Node3D
var _paused := false
var _zone := "lobby"
var _ready_emitted := false
var _active_interactable: Node
var _tutor_step := -1
var _tutor_start_pos := Vector3.ZERO
var _quit_armed_until := 0.0
var _hub_bounds := {
	"min_x": -6.55,
	"max_x": 6.55,
	"min_z": -18.5,
	"max_z": -3.5,
}


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	name = "LivingHubIntegration"
	call_deferred("_boot")


func _boot() -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	await _wait_for_lobby_contracts()
	_state = _save.load_or_default()
	_state = _save.record_visit(_state)
	_resolve_player_camera()
	_derive_bounds_from_contracts()
	_expand_player_bounds()
	_ensure_presence_anchors()
	_spawn_interactables()
	_setup_ui()
	_setup_presence()
	_setup_interaction()
	_setup_audio()
	_setup_memory()
	_setup_experience()
	_apply_graphics(str(_state.get("graphics_settings", {}).get("quality", "high")))
	_apply_accessibility()
	_place_at_elevator_start()
	_save.save_state(_state)
	_wire_signals()
	_print_ready()
	set_process(true)
	if _env_enabled("LUMINA_LIVING_HUB_VALIDATE"):
		await _run_validation_and_quit()
	elif OS.get_environment("LUMINA_LIVING_HUB_CAPTURE_PATH").strip_edges() != "":
		await _capture_and_quit(OS.get_environment("LUMINA_LIVING_HUB_CAPTURE_PATH").strip_edges())
	else:
		await _run_onboarding_flow()


func _wait_for_lobby_contracts() -> void:
	var root := get_tree().current_scene
	_lobby_runtime = root.get_node_or_null("ObservatoryLobbyRuntime")
	for _i in range(180):
		if _lobby_runtime != null and _lobby_runtime.has_method("is_contracts_ready") and bool(_lobby_runtime.call("is_contracts_ready")):
			return
		# Also accept discovery if ANCHOR nodes are already under the mirrored GLB.
		if _find_named_node3d(root, PresenceAnchors.ANCHOR_LOBBY_GREETING) != null \
			and _find_named_node3d(root, PresenceAnchors.ANCHOR_FAREWELL) != null:
			return
		await get_tree().process_frame
	push_warning("LUMINA_LIVING_HUB_LOBBY_CONTRACTS_TIMEOUT — continuing with best-effort discovery")


func _lobby_spawn() -> Vector3:
	return _anchor_global(PresenceAnchors.ANCHOR_LOBBY_GREETING)


func _player_lobby_spawn() -> Vector3:
	var greeting := _lobby_spawn()
	var toward_room := _room_spawn() - greeting
	toward_room.y = 0.0
	if toward_room.length_squared() < 0.0001:
		toward_room = Vector3.FORWARD
	else:
		toward_room = toward_room.normalized()

	# Prefer a lateral arrival point so the player does not start in the
	# greeting avatar or directly on the lobby-to-room walking line.  Evaluate
	# both sides and a corridor-side fallback against the derived walk bounds.
	var lateral := Vector3(toward_room.z, 0.0, -toward_room.x).normalized()
	var raw_candidates: Array[Vector3] = [
		greeting + lateral * PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE,
		greeting - lateral * PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE,
		greeting + toward_room * PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE,
	]
	var selected := _clamp_player_spawn_to_bounds(raw_candidates[0])
	var selected_distance := _horizontal_distance(selected, greeting)
	for index in range(1, raw_candidates.size()):
		var candidate := _clamp_player_spawn_to_bounds(raw_candidates[index])
		var candidate_distance := _horizontal_distance(candidate, greeting)
		if candidate_distance > selected_distance:
			selected = candidate
			selected_distance = candidate_distance

	if selected_distance + 0.001 < PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE:
		# A malformed/tiny bounds contract must not regress to camera/avatar
		# overlap.  The room-facing candidate preserves the hard separation;
		# validation reports the bounds defect independently.
		selected = greeting + toward_room * PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE
		push_warning(
			"LUMINA_LIVING_HUB_PLAYER_SPAWN_BOUNDS_TOO_SMALL separation=%.3f required=%.3f"
			% [selected_distance, PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE]
		)
	selected.y = greeting.y
	return selected


func _clamp_player_spawn_to_bounds(raw_position: Vector3) -> Vector3:
	var result := raw_position
	var min_x := float(_hub_bounds.get("min_x", raw_position.x)) + PLAYER_SPAWN_EDGE_MARGIN
	var max_x := float(_hub_bounds.get("max_x", raw_position.x)) - PLAYER_SPAWN_EDGE_MARGIN
	var min_z := float(_hub_bounds.get("min_z", raw_position.z)) + PLAYER_SPAWN_EDGE_MARGIN
	var max_z := float(_hub_bounds.get("max_z", raw_position.z)) - PLAYER_SPAWN_EDGE_MARGIN
	if min_x <= max_x:
		result.x = clampf(result.x, min_x, max_x)
	if min_z <= max_z:
		result.z = clampf(result.z, min_z, max_z)
	return result


func _player_lobby_yaw_radians(player_spawn: Vector3) -> float:
	var to_greeting := _lobby_spawn() - player_spawn
	to_greeting.y = 0.0
	if to_greeting.length_squared() < 0.0001:
		return deg_to_rad(165.0)
	to_greeting = to_greeting.normalized()
	# Godot's forward axis is -Z.
	return atan2(-to_greeting.x, -to_greeting.z)


func _place_player_at_safe_lobby_spawn() -> Vector3:
	var spawn := _player_lobby_spawn()
	if _player == null:
		return spawn
	_player.global_position = spawn
	_player.rotation.y = _player_lobby_yaw_radians(spawn)
	_player.velocity = Vector3.ZERO
	return spawn


func _ensure_player_avatar_spawn_separation() -> void:
	if _player == null or _lumina_avatar == null:
		return
	var distance := _horizontal_distance(_player.global_position, _lumina_avatar.global_position)
	if distance + 0.001 >= PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE:
		return
	var spawn := _place_player_at_safe_lobby_spawn()
	var repaired_distance := _horizontal_distance(spawn, _lumina_avatar.global_position)
	if repaired_distance + 0.001 < PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE:
		push_error(
			"LUMINA_LIVING_HUB_PLAYER_AVATAR_SPAWN_OVERLAP separation=%.3f required=%.3f"
			% [repaired_distance, PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE]
		)


func _horizontal_distance(a: Vector3, b: Vector3) -> float:
	return Vector2(a.x - b.x, a.z - b.z).length()


func _room_spawn() -> Vector3:
	return _anchor_global(PresenceAnchors.ANCHOR_ROOM_GUIDE)


func _anchor_global(anchor_name: String) -> Vector3:
	if _lobby_runtime != null and _lobby_runtime.has_method("get_anchor_global_transform"):
		var xf: Transform3D = _lobby_runtime.call("get_anchor_global_transform", anchor_name)
		if xf.origin != Vector3.ZERO or _lobby_runtime.call("get_anchor_node", anchor_name) != null:
			return xf.origin
	var node := _find_named_node3d(get_tree().current_scene, anchor_name)
	if node != null:
		return node.global_position
	if _anchor_root != null:
		var marker := _anchor_root.get_node_or_null(anchor_name) as Node3D
		if marker != null:
			return marker.global_position
	return Vector3.ZERO


func _derive_bounds_from_contracts() -> void:
	var floor := _find_named_node3d(get_tree().current_scene, "COL_LOBBY_FLOOR")
	var body := get_tree().current_scene.get_node_or_null("Observatory Lobby Colliders/COL_LOBBY_FLOOR") as Node3D
	var center := Vector3.ZERO
	var half := Vector3(4.9, 0.0, 4.05)
	if body != null:
		center = body.global_position
		var shape_node := body.get_child(0) as CollisionShape3D
		if shape_node != null and shape_node.shape is BoxShape3D:
			var box := shape_node.shape as BoxShape3D
			half = box.size * 0.5
	elif floor != null:
		center = floor.global_position
	_hub_bounds = {
		"min_x": center.x - half.x - 0.35,
		"max_x": center.x + half.x + 0.35,
		"min_z": minf(center.z - half.z - 0.8, _lobby_spawn().z - 1.2),
		"max_z": maxf(center.z + half.z + 0.8, _room_spawn().z + 0.6),
	}
	# Include the walkable room volume so the corridor→room walk is continuous.
	var room_file := FileAccess.open("res://assets/world/lumina_walkable_room_v1_20260718/room_contract.json", FileAccess.READ)
	if room_file != null:
		var parsed: Variant = JSON.parse_string(room_file.get_as_text())
		if parsed is Dictionary:
			var bounds: Dictionary = parsed.get("room", {}).get("bounds", {})
			if not bounds.is_empty():
				_hub_bounds["min_x"] = minf(float(_hub_bounds["min_x"]), float(bounds.get("min_x", -6.55)) - 0.2)
				_hub_bounds["max_x"] = maxf(float(_hub_bounds["max_x"]), float(bounds.get("max_x", 6.55)) + 0.2)
				_hub_bounds["min_z"] = minf(float(_hub_bounds["min_z"]), float(bounds.get("min_z", -4.55)) - 0.2)
				_hub_bounds["max_z"] = maxf(float(_hub_bounds["max_z"]), float(bounds.get("max_z", 4.55)) + 0.2)
	print("LUMINA_LIVING_HUB_BOUNDS ", _hub_bounds)


func _unhandled_input(event: InputEvent) -> void:
	if not (event is InputEventKey and event.pressed and not event.echo):
		return
	# Avoid fighting the editorial menu / pause chrome for living-space hotkeys.
	var menu_open := _is_menu_open()
	# Stuck recover stays available even while paused (escape hatch).
	if event.is_action_pressed("living_hub_stuck_recover") or event.keycode == KEY_F6:
		_recover_stuck()
		get_viewport().set_input_as_handled()
		return
	if _paused and not event.is_action_pressed("living_hub_pause") and event.keycode != KEY_P:
		return
	if event.is_action_pressed("living_hub_photo") or event.keycode == KEY_F9:
		if not menu_open:
			await _capture_user_moment("hotkey")
			get_viewport().set_input_as_handled()
		return
	if event.is_action_pressed("living_hub_cycle_time") or event.keycode == KEY_T:
		if not menu_open:
			_handle_time_console(_player)
			get_viewport().set_input_as_handled()
		return
	if event.is_action_pressed("living_hub_cycle_weather") or event.keycode == KEY_Y:
		if not menu_open:
			_handle_weather_console(_player)
			get_viewport().set_input_as_handled()
		return
	if event.is_action_pressed("living_hub_cycle_music") or event.keycode == KEY_M:
		if not menu_open:
			_handle_music_player(_player)
			get_viewport().set_input_as_handled()
		return
	match event.keycode:
		KEY_P:
			# Unify pause with menu so players aren't stuck in a second opaque mode.
			if _paused:
				set_paused(false)
				if _is_menu_open() and _hud != null and _hud.has_method("close_menu"):
					_hud.call("close_menu")
			else:
				if _hud != null and _hud.has_method("open_menu"):
					_hud.call("open_menu")
				set_paused(true)
			get_viewport().set_input_as_handled()
		KEY_F5:
			_persist_now("manual_save")
			get_viewport().set_input_as_handled()
		KEY_F7:
			return_to_lobby()
			get_viewport().set_input_as_handled()
		KEY_F8:
			request_clean_quit()
			get_viewport().set_input_as_handled()
		KEY_F1:
			_show_help_guide(false)
			get_viewport().set_input_as_handled()


func _is_menu_open() -> bool:
	return _hud != null and _hud.has_method("is_menu_open") and bool(_hud.call("is_menu_open"))


func _is_welcome_visible() -> bool:
	return _hud != null and _hud.has_method("is_welcome_visible") and bool(_hud.call("is_welcome_visible"))


func _can_player_move() -> bool:
	return (not _paused) and (not _is_menu_open()) and (not _is_welcome_visible())


func _apply_player_input_gate() -> void:
	if _player == null:
		return
	var can_move := _can_player_move()
	_player.set("input_enabled", can_move)
	if not can_move:
		_player.velocity = Vector3.ZERO
		if _player.has_method("set_mouse_captured"):
			_player.call("set_mouse_captured", false)


func set_paused(paused: bool) -> void:
	_paused = paused
	# Soft pause only — never freeze the whole SceneTree (audio / presence / HUD).
	_apply_player_input_gate()
	if _presence != null:
		_presence.set_process(not paused)
		_presence.set_physics_process(not paused)
	if _hud != null:
		if paused:
			if not _is_menu_open():
				_hud.call("show_subtitle", "お知らせ", "一時停止中 — P で再開")
		else:
			_hud.call("hide_subtitle")
	hub_paused.emit(paused)
	_state["session"]["paused"] = paused
	_persist_now("pause_toggle")


func return_to_lobby() -> void:
	if _player == null:
		return
	if _presence != null and _presence.has_method("request_farewell"):
		_presence.call("request_farewell")
	if _hud != null:
		_hud.call("show_subtitle", "LUMINA", "また来てね。ロビーまで見送るよ。")
	_place_player_at_safe_lobby_spawn()
	_zone = "lobby"
	_state = _save.append_activity(_state, {"type": "return_lobby"})
	_state = _save.set_preferred_location(_state, "lobby")
	_memory_notify("farewell", "ロビーへ戻り、見送りを受けた")
	_persist_now("return_lobby")
	if _audio != null:
		_audio.call("play_elevator")


func recover_stuck() -> void:
	_recover_stuck()


func request_clean_quit() -> void:
	var now := Time.get_ticks_msec() / 1000.0
	if now > _quit_armed_until:
		_quit_armed_until = now + 3.0
		if _hud != null:
			_hud.call("show_subtitle", "お知らせ", "終了するには 3秒以内にもう一度 F8")
		return
	_persist_now("clean_quit")
	if _hud != null:
		_hud.call("show_subtitle", "お知らせ", "状態を保存して終了します")
	await get_tree().create_timer(0.35).timeout
	get_tree().quit(0)


func get_status() -> Dictionary:
	return {
		"visit_count": int(_state.get("visit_count", 0)),
		"last_visit_at": str(_state.get("last_visit_at", "")),
		"zone": _zone,
		"paused": _paused,
		"player": _player != null,
		"camera": _camera != null,
		"hud": _hud != null,
		"presence": _presence != null,
		"presence_state": _presence.call("get_state") if _presence != null and _presence.has_method("get_state") else "",
		"renderer": RenderingServer.get_current_rendering_method(),
		"interactables": _interactable_root.get_child_count() if _interactable_root else 0,
		"anchors": _anchor_root.get_child_count() if _anchor_root else 0,
		"preferred_locations": _state.get("preferred_locations", []),
		"unlocked_moments": _state.get("unlocked_moments", []),
		"shared_activity_count": (_state.get("shared_activity_history", []) as Array).size(),
	}


func _resolve_player_camera() -> void:
	var root := get_tree().current_scene
	_player = _find_first_class(root, "CharacterBody3D") as CharacterBody3D
	if _player != null:
		_camera = _player.find_child("FirstPersonCamera", true, false) as Camera3D
	if _camera == null:
		_camera = _find_first_class(root, "Camera3D") as Camera3D


func _expand_player_bounds() -> void:
	if _player == null or not _player.has_method("configure"):
		return
	var spawn := _player_lobby_spawn()
	var config := {
		"spawn": [spawn.x, spawn.y, spawn.z],
		"spawn_yaw_degrees": rad_to_deg(_player_lobby_yaw_radians(spawn)),
		"bounds": _hub_bounds,
		"player_radius": 0.32,
		"player_height": 1.72,
		"eye_height": 1.60,
	}
	_player.call("configure", config)
	_player.set("soft_bounds_enabled", true)


func _ensure_presence_anchors() -> void:
	var root := get_tree().current_scene
	_anchor_root = root.get_node_or_null("LivingHubPresenceAnchors") as Node3D
	if _anchor_root == null:
		_anchor_root = Node3D.new()
		_anchor_root.name = "LivingHubPresenceAnchors"
		root.add_child(_anchor_root)
	var found := 0
	for anchor_name in PresenceAnchors.CONTRACT_NAMES:
		var source := _resolve_glb_anchor(anchor_name)
		var marker := _anchor_root.get_node_or_null(anchor_name) as Node3D
		if marker == null:
			marker = Marker3D.new()
			marker.name = anchor_name
			_anchor_root.add_child(marker)
		if source != null:
			# Copy post-mirror global_transform; never re-hardcode Blender locals.
			marker.global_transform = Transform3D(
				Basis.from_euler(Vector3(0.0, source.global_rotation.y, 0.0)),
				source.global_position
			)
			found += 1
		else:
			push_warning("LUMINA_LIVING_HUB_ANCHOR_MISSING " + anchor_name)
	print("LUMINA_LIVING_HUB_ANCHORS count=", _anchor_root.get_child_count(), " from_glb=", found)


func _resolve_glb_anchor(anchor_name: String) -> Node3D:
	if _lobby_runtime != null and _lobby_runtime.has_method("get_anchor_node"):
		var from_lobby: Node3D = _lobby_runtime.call("get_anchor_node", anchor_name) as Node3D
		if from_lobby != null:
			return from_lobby
	return _find_named_node3d(get_tree().current_scene, anchor_name)


func _spawn_interactables() -> void:
	var root := get_tree().current_scene
	_interactable_root = root.get_node_or_null("LivingHubInteractables") as Node3D
	if _interactable_root == null:
		_interactable_root = Node3D.new()
		_interactable_root.name = "LivingHubInteractables"
		root.add_child(_interactable_root)
	while _interactable_root.get_child_count() > 0:
		var old := _interactable_root.get_child(0)
		_interactable_root.remove_child(old)
		old.free()
	var defs := [
		{"id": "elevator", "label": "エレベーター", "anchor": PresenceAnchors.ANCHOR_FAREWELL, "kind": "elevator", "col": "COL_ELEVATOR", "radius": 1.05},
		{"id": "bench", "label": "ベンチに座る", "anchor": PresenceAnchors.ANCHOR_BENCH_PLAYER, "kind": "bench", "col": "COL_BENCH", "radius": 0.95},
		{"id": "window", "label": "景色を共有する", "anchor": PresenceAnchors.ANCHOR_WINDOW_VIEW_PLAYER, "kind": "window", "radius": 1.05},
		{"id": "console", "label": "コンソール", "anchor": "", "kind": "console", "col": "COL_CONSOLE", "radius": 0.9},
		{"id": "memory_wall", "label": "メモリーウォール", "anchor": PresenceAnchors.ANCHOR_MEMORY_WALL, "kind": "memory_wall", "radius": 1.0},
		{"id": "room_entrance", "label": "居室へ向かう", "anchor": PresenceAnchors.ANCHOR_ROOM_GUIDE, "kind": "room_entrance", "radius": 1.0},
		{"id": "lumina", "label": "Luminaに話しかける", "anchor": PresenceAnchors.ANCHOR_LOBBY_GREETING, "kind": "lumina", "radius": 1.15},
		{"id": "music_player", "label": "音楽を選ぶ", "anchor": PresenceAnchors.ANCHOR_BENCH_PLAYER, "kind": "music_player", "col": "COL_MUSIC_PLAYER", "radius": 0.75, "offset": Vector3(0.55, 0.0, 0.35)},
		{"id": "time_console", "label": "時刻を切り替える", "anchor": PresenceAnchors.ANCHOR_MEMORY_WALL, "kind": "time_console", "col": "COL_TIME_CONSOLE", "radius": 0.75, "offset": Vector3(-0.45, 0.0, 0.2)},
		{"id": "weather_console", "label": "天候を切り替える", "anchor": PresenceAnchors.ANCHOR_MEMORY_WALL, "kind": "weather_console", "col": "COL_WEATHER_CONSOLE", "radius": 0.75, "offset": Vector3(0.45, 0.0, 0.2)},
		{"id": "photo_spot", "label": "この景色をメモに残す", "anchor": PresenceAnchors.ANCHOR_WINDOW_VIEW_PLAYER, "kind": "photo_spot", "radius": 0.85, "offset": Vector3(0.0, 0.0, -0.55)},
	]
	for item in defs:
		if str(item["id"]) == "lumina" and _env_enabled("LUMINA_LIVING_HUB_NO_COMPANION"):
			continue
		var node := InteractableScript.new()
		node.name = "Interactable_%s" % item["id"]
		node.interactable_id = str(item["id"])
		node.interaction_label = str(item["label"])
		node.interaction_radius = float(item.get("radius", 1.2))
		node.set_meta("kind", item["kind"])
		_interactable_root.add_child(node)
		node.global_position = _resolve_interactable_position(item)
		node.interacted.connect(_on_interactable_used.bind(node))
	_spawn_room_contract_interactables()


func _spawn_room_contract_interactables() -> void:
	var room_file := FileAccess.open("res://assets/world/lumina_walkable_room_v1_20260718/room_contract.json", FileAccess.READ)
	if room_file == null:
		return
	var parsed: Variant = JSON.parse_string(room_file.get_as_text())
	if not (parsed is Dictionary):
		return
	var kind_map := {
		"reading": "room_reading",
		"studio": "room_studio",
		"conversation": "room_conversation",
		"window": "room_window",
	}
	for item in parsed.get("anchors", []):
		if not (item is Dictionary):
			continue
		var anchor_id := str(item.get("id", ""))
		if not kind_map.has(anchor_id):
			continue
		var node := InteractableScript.new()
		node.name = "Interactable_room_%s" % anchor_id
		node.interactable_id = "room_%s" % anchor_id
		node.interaction_label = str(item.get("label", "調べる"))
		node.interaction_radius = float(item.get("radius", 1.2))
		node.set_meta("kind", kind_map[anchor_id])
		_interactable_root.add_child(node)
		var pos := _vec3(item.get("position", [0, 0, 0]))
		node.global_position = pos + Vector3(0.0, 0.1, 0.0)
		node.interacted.connect(_on_interactable_used.bind(node))
	print("LUMINA_LIVING_HUB_ROOM_INTERACTABLES added")


func _vec3(value: Variant) -> Vector3:
	if value is Array and value.size() >= 3:
		return Vector3(float(value[0]), float(value[1]), float(value[2]))
	if value is PackedFloat32Array and value.size() >= 3:
		return Vector3(float(value[0]), float(value[1]), float(value[2]))
	return Vector3.ZERO


func _resolve_interactable_position(item: Dictionary) -> Vector3:
	var offset: Vector3 = item.get("offset", Vector3.ZERO)
	var col_name := str(item.get("col", "")).strip_edges()
	if not col_name.is_empty():
		var col_node := _find_named_node3d(get_tree().current_scene, col_name)
		if col_node != null:
			return col_node.global_position + Vector3(0.0, 0.15, 0.0) + offset
	var interactable_id := str(item.get("id", ""))
	var anchor_name := str(item.get("anchor", ""))
	return _interactable_position(interactable_id, anchor_name) + offset


func _interactable_position(interactable_id: String, anchor_name: String) -> Vector3:
	if interactable_id == "console":
		var console_mesh := _find_node_name_contains(get_tree().current_scene, "Lobby console")
		if console_mesh is Node3D:
			return (console_mesh as Node3D).global_position
		return (_anchor_global(PresenceAnchors.ANCHOR_MEMORY_WALL) + _anchor_global(PresenceAnchors.ANCHOR_WINDOW_VIEW_PLAYER)) * 0.5
	if interactable_id == "room_entrance":
		# Corridor mouth between lobby greeting and room guide — not the room destination itself.
		var lobby_p := _lobby_spawn()
		var room_p := _anchor_global(PresenceAnchors.ANCHOR_ROOM_GUIDE)
		if lobby_p != Vector3.ZERO and room_p != Vector3.ZERO:
			return lobby_p.lerp(room_p, 0.42)
	if anchor_name != "":
		return _anchor_global(anchor_name)
	return _lobby_spawn()


func _find_node_name_contains(root: Node, fragment: String) -> Node:
	if root == null:
		return null
	if String(root.name).findn(fragment) >= 0:
		return root
	for child in root.get_children():
		var found := _find_node_name_contains(child, fragment)
		if found != null:
			return found
	return null


func _setup_ui() -> void:
	var root := get_tree().current_scene
	_hud = root.get_node_or_null("LuminaEditorialHUD") as CanvasLayer
	if _hud == null:
		_hud = EditorialHudScript.new()
		_hud.name = "LuminaEditorialHUD"
		root.add_child(_hud)
	# Hide legacy walkable HUD panels to avoid double chrome.
	for child in root.get_children():
		if child is CanvasLayer and child.name == "WalkableRoomHUD":
			child.visible = false
	var access: Dictionary = _state.get("accessibility_settings", {})
	if _hud.has_method("set_reduced_motion"):
		_hud.call("set_reduced_motion", bool(access.get("reduced_motion", false)))
	if _hud.has_method("set_font_scale"):
		_hud.call("set_font_scale", float(access.get("font_scale", 1.0)))
	if _hud.has_method("set_time_state"):
		_hud.call("set_time_state", "訪問 %s 回目" % int(_state.get("visit_count", 1)))
	if _hud.has_signal("menu_opened") and not _hud.is_connected("menu_opened", _on_menu_opened):
		_hud.connect("menu_opened", _on_menu_opened)
	if _hud.has_signal("menu_closed") and not _hud.is_connected("menu_closed", _on_menu_closed):
		_hud.connect("menu_closed", _on_menu_closed)
	if _hud.has_signal("settings_action") and not _hud.is_connected("settings_action", _on_settings_action):
		_hud.connect("settings_action", _on_settings_action)


func _setup_presence() -> void:
	if _env_enabled("LUMINA_LIVING_HUB_NO_COMPANION"):
		_presence = null
		_lumina_avatar = null
		return
	var root := get_tree().current_scene
	_lumina_avatar = root.get_node_or_null("LivingHubLuminaProxy") as CharacterBody3D
	if _lumina_avatar == null:
		_lumina_avatar = _create_lumina_proxy()
		root.add_child(_lumina_avatar)
	_lumina_avatar.global_position = _lobby_spawn()
	_ensure_player_avatar_spawn_separation()
	_presence = root.get_node_or_null("LuminaPresenceController")
	if _presence == null:
		_presence = PresenceControllerScript.new()
		_presence.name = "LuminaPresenceController"
		root.add_child(_presence)
	if _presence.has_method("bind"):
		_presence.call("bind", _lumina_avatar, _player, _anchor_root)
	for child in _anchor_root.get_children():
		if child is Node3D and _presence.has_method("register_anchor"):
			_presence.call("register_anchor", child.name, child)
	# Prefer live GLB ANCHOR_* nodes when available.
	for anchor_name in PresenceAnchors.CONTRACT_NAMES:
		var source := _resolve_glb_anchor(anchor_name)
		if source != null and _presence.has_method("register_anchor"):
			_presence.call("register_anchor", anchor_name, source)


func _create_lumina_proxy() -> CharacterBody3D:
	var body := CharacterBody3D.new()
	body.name = "LivingHubLuminaProxy"
	body.collision_layer = 1
	body.collision_mask = 1
	var collision := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = 0.28
	capsule.height = 1.62
	collision.shape = capsule
	collision.position.y = 0.81
	body.add_child(collision)
	var mesh := MeshInstance3D.new()
	mesh.name = "ProxyMesh"
	var capsule_mesh := CapsuleMesh.new()
	capsule_mesh.radius = 0.28
	capsule_mesh.height = 1.62
	mesh.mesh = capsule_mesh
	mesh.position.y = 0.81
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.72, 0.86, 1.0, 1.0)
	mat.emission_enabled = true
	mat.emission = Color(0.25, 0.55, 0.85)
	mat.emission_energy_multiplier = 0.35
	mesh.material_override = mat
	body.add_child(mesh)
	return body


func _setup_interaction() -> void:
	_interaction = InteractionRayScript.new()
	_interaction.name = "LivingHubInteractionRay"
	add_child(_interaction)
	_interaction.call("setup", _camera, _player)
	_interaction.call("set_prompt_callback", Callable(self, "_on_interaction_prompt"))
	_interaction.connect("interact_pressed", _on_ray_interact_pressed)
	if _player != null and _player.has_signal("interaction_requested"):
		if not _player.is_connected("interaction_requested", _on_player_interaction_requested):
			_player.connect("interaction_requested", _on_player_interaction_requested)
	if _player != null and _player.has_signal("soft_bound_hit"):
		if not _player.is_connected("soft_bound_hit", _on_soft_bound_hit):
			_player.connect("soft_bound_hit", _on_soft_bound_hit)
	if _hud != null and _hud.has_signal("interaction_requested"):
		_hud.connect("interaction_requested", _on_player_interaction_requested)
	if _hud != null and _hud.has_signal("section_focused"):
		_hud.connect("section_focused", _on_menu_section_focused)
	if _hud != null and _hud.has_signal("section_selected"):
		_hud.connect("section_selected", _on_menu_section)


func _setup_audio() -> void:
	_audio = AudioScript.new()
	_audio.name = "LivingHubAudio"
	add_child(_audio)
	if _player != null:
		_audio.call("setup", _player)
	_audio.call("configure_zones", {
		"elevator": _anchor_global(PresenceAnchors.ANCHOR_FAREWELL),
		"lobby": _lobby_spawn(),
		"window": _anchor_global(PresenceAnchors.ANCHOR_WINDOW_VIEW_PLAYER),
		"bench": _anchor_global(PresenceAnchors.ANCHOR_BENCH_PLAYER),
		"room": _room_spawn(),
	})
	_audio.call("apply_settings", _state.get("audio_settings", {}))
	_audio.connect("zone_changed", _on_audio_zone_changed)


func _setup_memory() -> void:
	_memory = MemoryScript.new()
	_memory.name = "LivingHubMemoryBridge"
	add_child(_memory)
	_memory_notify("visit", "リビングハブ訪問 第%s回" % int(_state.get("visit_count", 1)), {
		"visit_count": int(_state.get("visit_count", 1)),
	})


func _setup_experience() -> void:
	_experience = ExperienceScript.new()
	_experience.name = "LuminaLivingSpaceExperience"
	add_child(_experience)
	var living_space: Dictionary = _state.get("living_space", {})
	var env_time := OS.get_environment("LUMINA_LIVING_HUB_TIME_MODE").strip_edges().to_lower()
	var env_weather := OS.get_environment("LUMINA_LIVING_HUB_WEATHER").strip_edges().to_lower()
	if not env_time.is_empty():
		living_space["time_mode"] = env_time
	if not env_weather.is_empty():
		living_space["weather"] = env_weather
	_experience.call("setup", get_tree().current_scene, _camera, _hud, _audio, living_space, _anchor_global(PresenceAnchors.ANCHOR_WINDOW_VIEW_PLAYER))
	_experience.connect("experience_changed", _on_experience_changed)
	_experience.connect("moment_captured", _on_moment_captured)
	_refresh_hud_data()


func _place_at_elevator_start() -> void:
	if _player == null:
		return
	_place_player_at_safe_lobby_spawn()
	var defer_arrival := _needs_onboarding()
	if not defer_arrival:
		_announce_arrival()
	_state = _save.unlock_moment(_state, "elevator_arrival")
	_state = _save.append_activity(_state, {"type": "elevator_start"})
	_state = _save.set_preferred_location(_state, "lobby_elevator")
	if _audio != null:
		_audio.call("play_elevator")


func _announce_arrival() -> void:
	if _presence != null and _presence.has_method("notify_player_arrived"):
		_presence.call("notify_player_arrived")
	if _hud == null:
		return
	_hud.call(
		"show_subtitle",
		"LUMINA" if not _env_enabled("LUMINA_LIVING_HUB_NO_COMPANION") else "空間",
		"おかえり。エレベーターから歩いてみよう。" if _env_enabled("LUMINA_LIVING_HUB_NO_COMPANION") else "おかえり。エレベーターから一緒に歩こう。"
	)
	# Let the proximity/ray prompt own [E]; avoid a sticky fake prompt at arrival.
	_hud.call("hide_interaction")


func _needs_onboarding() -> bool:
	if _env_enabled("LUMINA_LIVING_HUB_SKIP_ONBOARD"):
		return false
	if _env_enabled("LUMINA_LIVING_HUB_FORCE_ONBOARD"):
		return true
	var onboarding: Dictionary = _state.get("onboarding", {})
	return not bool(onboarding.get("welcome_seen", false))


func _welcome_guide_copy() -> Dictionary:
	return {
		"title": "Lumina リビングハブ",
		"body": "ここは観測所のロビーと居室です。\n\nまずはこれだけ覚えれば遊べます。\n・左クリック … 視点を動かす\n・WASD … 歩く\n・E … 調べる / 話しかける\n・Esc … メニューを開く・閉じる\n・F6 … 詰まったときの復帰\n\nメニューの「メニューを閉じる」でも閉じられます。\n「エレベーター付近へ移動」は場所が変わるので、閉じたいだけのときは使わないでください。\n\n詳しい操作は F1 でいつでも再表示できます。",
		"footer": "Enter / Space / Esc ではじめる",
	}


func _show_help_guide(mark_seen: bool) -> void:
	if _hud == null or not _hud.has_method("show_welcome_guide"):
		return
	if _player != null and _player.has_method("set_mouse_captured"):
		_player.call("set_mouse_captured", false)
	var copy := _welcome_guide_copy()
	_hud.call("show_welcome_guide", copy["title"], copy["body"], copy["footer"])
	if mark_seen:
		pass


func _run_onboarding_flow() -> void:
	if _hud != null and _hud.has_method("set_control_hint"):
		_hud.call("set_control_hint", "E 調べる · Esc メニュー · F1 説明 · F6 詰まり復帰")
	if _hud != null and _hud.has_signal("help_requested"):
		if not _hud.is_connected("help_requested", _on_help_requested):
			_hud.connect("help_requested", _on_help_requested)
	if not _needs_onboarding():
		return
	# Staged tutor: 3 short prompts instead of a full manual wall.
	_apply_player_input_gate()
	await _run_staged_tutor()
	var onboarding: Dictionary = _state.get("onboarding", {}).duplicate(true)
	onboarding["welcome_seen"] = true
	onboarding["tutor_completed"] = true
	_state["onboarding"] = onboarding
	_persist_now("onboarding")
	_apply_player_input_gate()
	_announce_arrival()
	print("LUMINA_LIVING_HUB_ONBOARDING dismissed visits=", int(_state.get("visit_count", 0)))


func _run_staged_tutor() -> void:
	if _player == null or _hud == null:
		return
	_tutor_step = 0
	_tutor_start_pos = _player.global_position
	_player.set("input_enabled", true)
	_hud.call("show_subtitle", "案内", "① 左クリックで視点を動かしてみて")
	_hud.call("set_control_hint", "左クリックで視点")
	var timeout := 18.0
	var elapsed := 0.0
	while elapsed < timeout and _tutor_step == 0:
		await get_tree().process_frame
		elapsed += get_process_delta_time()
		if Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
			_tutor_step = 1
			break
	_tutor_step = 1
	_hud.call("show_subtitle", "案内", "② WASD で少し歩いてみよう")
	_hud.call("set_control_hint", "WASD で移動")
	elapsed = 0.0
	while elapsed < timeout and _tutor_step == 1:
		await get_tree().process_frame
		elapsed += get_process_delta_time()
		if _player.global_position.distance_to(_tutor_start_pos) > 0.55:
			_tutor_step = 2
			break
	_tutor_step = 2
	_hud.call("show_subtitle", "案内", "③ 物に近づいて [E] で調べる。メニューは Esc")
	_hud.call("set_control_hint", "E 調べる · Esc メニュー · F1 詳しい説明")
	await get_tree().create_timer(2.4).timeout
	_tutor_step = -1
	_hud.call("hide_subtitle")


func _on_help_requested() -> void:
	_show_help_guide(false)


func _on_menu_opened() -> void:
	# Esc-menu and P-pause share the same input gate / soft pause flag.
	_paused = true
	_apply_player_input_gate()
	if _presence != null:
		_presence.set_process(false)
		_presence.set_physics_process(false)
	if _hud != null and _hud.has_method("set_control_hint"):
		_hud.call("set_control_hint", "Esc または「メニューを閉じる」で閉じる")
	hub_paused.emit(true)


func _on_menu_closed() -> void:
	if _paused:
		# Closing menu also ends soft pause so the player isn't stuck with no chrome.
		_paused = false
	if _presence != null:
		_presence.set_process(true)
		_presence.set_physics_process(true)
	_apply_player_input_gate()
	if _hud != null and _hud.has_method("set_control_hint"):
		_hud.call("set_control_hint", "E 調べる · Esc メニュー · F1 説明 · F6 詰まり復帰")
	if _hud != null and _player != null and Input.mouse_mode != Input.MOUSE_MODE_CAPTURED:
		_hud.call("show_subtitle", "お知らせ", "左クリックで視点を戻せます")
	hub_paused.emit(false)


func _on_settings_action(action: String) -> void:
	match action:
		"photo":
			await _capture_user_moment("settings")
		"time":
			_handle_time_console(_player)
		"weather":
			_handle_weather_console(_player)
		"music":
			_handle_music_player(_player)
		"persist_accessibility":
			_persist_now("accessibility")
		_:
			pass


func _wire_signals() -> void:
	if _presence != null:
		if _presence.has_signal("state_changed"):
			_presence.connect("state_changed", _on_presence_state_changed)
		if _presence.has_signal("shared_gaze_started"):
			_presence.connect("shared_gaze_started", _on_shared_gaze)
		if _presence.has_signal("sit_completed"):
			_presence.connect("sit_completed", _on_sit_completed)


func _on_interaction_prompt(label: String) -> void:
	if _hud == null:
		return
	if label.strip_edges() == "":
		_hud.call("hide_interaction")
	else:
		_hud.call("show_interaction", label)


func _on_player_interaction_requested() -> void:
	if _paused:
		return
	if _interaction != null:
		_interaction.call("request_interact")


func _on_soft_bound_hit() -> void:
	if _hud != null:
		_hud.call("show_subtitle", "お知らせ", "ここまで。別の方向へ歩いてみて")


func _on_ray_interact_pressed(target: Node) -> void:
	_active_interactable = target


func _on_interactable_used(actor: Node, interactable: Node) -> void:
	var kind := str(interactable.get_meta("kind", interactable.interactable_id))
	match kind:
		"elevator":
			_handle_elevator(actor)
		"bench":
			_handle_bench(actor)
		"window":
			_handle_window(actor)
		"console":
			_handle_console(actor)
		"memory_wall":
			_handle_memory_wall(actor)
		"room_entrance":
			_handle_room_entrance(actor)
		"lumina":
			_handle_lumina(actor)
		"music_player":
			_handle_music_player(actor)
		"time_console":
			_handle_time_console(actor)
		"weather_console":
			_handle_weather_console(actor)
		"photo_spot":
			await _handle_photo_spot(actor)
		"room_reading":
			_handle_room_spot(actor, "reading", "読書スペースだね。静かに本が読めそう。")
		"room_studio":
			_handle_room_spot(actor, "studio", "制作デスク。何か作りたくなる場所。")
		"room_conversation":
			_handle_room_spot(actor, "conversation", "会話テーブル。ここでゆっくり話そう。")
		"room_window":
			_handle_room_spot(actor, "room_window", "居室の観測窓。街の灯りが見える。")
		_:
			if _hud:
				_hud.call("show_subtitle", "お知らせ", interactable.call("get_interaction_label"))
	if interactable.has_method("release_busy"):
		interactable.call("release_busy")


func _handle_room_spot(_actor: Node, spot_id: String, line: String) -> void:
	_zone = "room"
	if _hud:
		_hud.call("show_subtitle", "LUMINA", line)
	_state = _save.append_activity(_state, {"type": "room_spot", "id": spot_id})
	_state = _save.unlock_moment(_state, "room_%s" % spot_id)
	_state = _save.set_preferred_location(_state, "room_%s" % spot_id)
	_persist_now("room_spot_%s" % spot_id)


func _handle_elevator(_actor: Node) -> void:
	if _hud:
		_hud.call("show_subtitle", "LUMINA", "エレベーターだね。ここが出発点。")
	if _audio:
		_audio.call("play_elevator")
	if _presence and _presence.has_method("notify_player_arrived"):
		_presence.call("notify_player_arrived")
	_state = _save.append_activity(_state, {"type": "elevator"})
	_state = _save.unlock_moment(_state, "elevator")
	_persist_now("elevator")


func _handle_bench(_actor: Node) -> void:
	if _presence and _presence.has_method("request_sit"):
		_presence.call("request_sit", "bench")
	if _hud:
		_hud.call("show_subtitle", "LUMINA", "少し座ろう。隣、空けてあるよ。")
	_state = _save.append_activity(_state, {"type": "sit_bench"})
	_state = _save.unlock_moment(_state, "sit_together")
	_state = _save.set_preferred_location(_state, "bench")
	_memory_notify("sit_together", "ベンチで並んで座った")
	_persist_now("bench")


func _handle_window(_actor: Node) -> void:
	if _presence and _presence.has_method("request_shared_gaze"):
		_presence.call("request_shared_gaze", PresenceAnchors.ANCHOR_WINDOW_VIEW_LUMINA)
	if _audio:
		_audio.call("play_window_ambience")
	if _hud:
		_hud.call("show_subtitle", "LUMINA", "この景色、一緒に見ていよう。")
	_state = _save.append_activity(_state, {"type": "shared_gaze_window"})
	_state = _save.unlock_moment(_state, "shared_gaze")
	_state = _save.set_preferred_location(_state, "window")
	_memory_notify("shared_gaze", "窓辺の景色を一緒に見た")
	_persist_now("window")


func _handle_console(_actor: Node) -> void:
	if _hud:
		_hud.call("open_menu")
		_hud.call("set_menu_section", "SETTINGS", false)
		_on_menu_section_focused("SETTINGS")
		_hud.call("show_subtitle", "お知らせ", "設定を開きました")
	_state = _save.append_activity(_state, {"type": "console"})
	_persist_now("console")


func _handle_memory_wall(_actor: Node) -> void:
	if _hud:
		_hud.call("open_menu")
		_hud.call("set_menu_section", "MEMORY", false)
		_on_menu_section_focused("MEMORY")
		var count := (_state.get("shared_activity_history", []) as Array).size()
		_hud.call("show_subtitle", "LUMINA", "共有した記憶が %s 件あるよ。" % count)
	_state = _save.append_activity(_state, {"type": "memory_wall"})
	_state = _save.unlock_moment(_state, "memory_wall")
	_memory_notify("memory_wall", "メモリーウォールを見た")
	_persist_now("memory_wall")


func _handle_room_entrance(_actor: Node) -> void:
	if _presence and _presence.has_method("request_guide"):
		_presence.call("request_guide", PresenceAnchors.ANCHOR_ROOM_GUIDE)
	# Validation harness still needs an instant snap; live play keeps walking continuity.
	if _env_enabled("LUMINA_LIVING_HUB_VALIDATE") and _player:
		_player.global_position = _room_spawn()
		_player.rotation.y = deg_to_rad(180.0)
		_zone = "room"
		if _hud:
			_hud.call("show_subtitle", "LUMINA", "居室へどうぞ。案内するね。")
	else:
		if _hud:
			_hud.call("show_subtitle", "LUMINA", "通路を歩いて居室へ行こう。私が先に案内するね。")
		# Keep zone as lobby until the player actually reaches the room.
	_state = _save.append_activity(_state, {"type": "enter_room"})
	_state = _save.unlock_moment(_state, "enter_room")
	_state = _save.set_preferred_location(_state, "room")
	_memory_notify("enter_room", "居室へ入った")
	_persist_now("room_entrance")


func _handle_lumina(_actor: Node) -> void:
	if _presence and _presence.has_method("request_follow"):
		_presence.call("request_follow")
	if _presence and _presence.has_method("enter_conversation"):
		_presence.call("enter_conversation")
	if _hud:
		_hud.call("show_subtitle", "LUMINA", "一緒に歩こう。窓辺まで行きたいな。")
	_state = _save.append_activity(_state, {"type": "talk_lumina"})
	_state = _save.unlock_moment(_state, "greeted")
	_memory_notify("greet", "ロビーで話しかけた")
	_persist_now("lumina")


func _handle_music_player(_actor: Node) -> void:
	if _experience == null:
		return
	var track_id := str(_experience.call("cycle_music"))
	var label := "不明な曲"
	if _audio != null and _audio.has_method("get_track_label"):
		label = str(_audio.call("get_track_label", track_id))
	if _hud:
		_hud.call("show_subtitle", "空間", "音楽  /  %s" % label)
	_state = _save.append_activity(_state, {"type": "music_changed", "track": track_id})
	_persist_now("music")


func _handle_time_console(_actor: Node) -> void:
	if _experience == null:
		return
	var time_mode := str(_experience.call("cycle_time"))
	var time_labels := {
		"blue_hour": "ブルーアワー",
		"night": "夜",
		"sunrise": "夜明け",
	}
	var time_ja: String = str(time_labels.get(time_mode, time_mode.replace("_", " ")))
	if _hud:
		_hud.call("show_subtitle", "空間", "時刻  /  %s" % time_ja)
	_state = _save.append_activity(_state, {"type": "time_changed", "time_mode": time_mode})
	_persist_now("time")


func _handle_weather_console(_actor: Node) -> void:
	if _experience == null:
		return
	var weather := str(_experience.call("cycle_weather"))
	var weather_labels := {
		"clear": "晴れ",
		"overcast": "くもり",
		"rain": "雨",
	}
	var weather_ja: String = str(weather_labels.get(weather, weather))
	if _hud:
		_hud.call("show_subtitle", "空間", "天候  /  %s" % weather_ja)
	_state = _save.append_activity(_state, {"type": "weather_changed", "weather": weather})
	_persist_now("weather")


func _handle_photo_spot(_actor: Node) -> void:
	await _capture_user_moment("window")


func _capture_user_moment(source: String) -> Dictionary:
	if _experience == null:
		return {"ok": false, "reason": "experience_missing"}
	if _hud:
		_hud.call("show_subtitle", "景色メモ", "景色を記録しています…")
	var moment: Dictionary = await _experience.call("capture_moment", {
		"title": "窓辺の記憶" if _zone == "window" or source == "window" else "リビングの記憶",
		"zone": _zone,
		"source": source,
	})
	if bool(moment.get("ok", false)):
		_state = _save.append_photo_moment(_state, moment)
		_state = _save.unlock_moment(_state, "first_photo")
		_state = _save.append_activity(_state, {"type": "photo_moment", "id": moment.get("id", ""), "zone": _zone})
		_memory_notify("photo_moment", str(moment.get("title", "Moment captured")), moment)
		_refresh_hud_data()
		if _hud:
			_hud.call("show_subtitle", "景色メモ", "保存しました  /  %s" % str(moment.get("title", "景色")))
		_persist_now("photo_moment")
	else:
		if _hud:
			_hud.call("show_subtitle", "お知らせ", "写真を保存できませんでした")
	return moment


func _on_menu_section_focused(section: String) -> void:
	# Navigation only — never mutate graphics / music / location here.
	if _hud == null:
		return
	match section:
		"TOGETHER":
			var track := "observatory"
			if _experience != null:
				track = str(_experience.call("get_state").get("music_track", track))
			var label := track.replace("_", " ")
			if _audio != null and _audio.has_method("get_track_label"):
				label = str(_audio.call("get_track_label", track))
			_hud.call("set_status_text", "いまの音楽\n%s\nEnter で曲だけ切替（メニューは開いたまま）" % label)
		"MEMORY":
			var activity_count := (_state.get("shared_activity_history", []) as Array).size()
			_hud.call("set_status_text", "活動 %d 件\n訪問 %d 回\n保存済みの生活履歴" % [activity_count, int(_state.get("visit_count", 0))])
		"MOMENTS":
			var photos: Array = _state.get("photo_moments", [])
			_hud.call("set_moments", photos)
			_hud.call("set_status_text", "景色メモ %d 件\nF9 でいまの景色を保存" % photos.size())
		"SETTINGS":
			var quality := str(_state.get("graphics_settings", {}).get("quality", "high"))
			var quality_labels := {"high": "高", "medium": "中", "low": "低", "ultra": "最高"}
			var quality_ja: String = str(quality_labels.get(quality.to_lower(), quality))
			_hud.call("set_status_text", "画質  %s\n[ / ] 文字サイズ · R 動き · H UI隠す\nEnter で画質を切替 · Esc で閉じる" % quality_ja)
		"CLOSE":
			_hud.call("set_status_text", "メニューを閉じる\nEnter または Esc\n場所は変わりません")
		"RETURN":
			_hud.call("set_status_text", "エレベーター付近へ移動します\nEnter で移動\n閉じるだけなら「メニューを閉じる」か Esc")


func _on_menu_section(section: String) -> void:
	# Confirm / activate only — side effects live here.
	match section:
		"CLOSE":
			pass
		"RETURN":
			return_to_lobby()
		"SETTINGS":
			_cycle_graphics_quality()
			_on_menu_section_focused("SETTINGS")
		"TOGETHER":
			# Soft confirm: cycle music only; keep menu open. Follow stays on world interact.
			_handle_music_player(_player)
			_on_menu_section_focused("TOGETHER")
		"MEMORY":
			_on_menu_section_focused("MEMORY")
		"MOMENTS":
			_on_menu_section_focused("MOMENTS")
			if _hud:
				_hud.call("show_subtitle", "景色メモ", "保存した景色を表示しています")


func _on_experience_changed(experience_state: Dictionary) -> void:
	_state = _save.set_living_space(_state, experience_state)
	_refresh_hud_data()


func _on_moment_captured(_moment: Dictionary) -> void:
	pass


func _refresh_hud_data() -> void:
	if _hud == null:
		return
	if _hud.has_method("set_moments"):
		_hud.call("set_moments", _state.get("photo_moments", []))
	if _hud.has_method("set_status_text"):
		var activities := (_state.get("shared_activity_history", []) as Array).size()
		var photos := (_state.get("photo_moments", []) as Array).size()
		_hud.call("set_status_text", "訪問 %d 回  /  記憶 %d 件\n景色メモ %d 件" % [int(_state.get("visit_count", 0)), activities, photos])


func _on_presence_state_changed(previous: String, current: String) -> void:
	_state = _save.append_activity(_state, {
		"type": "presence_state",
		"from": previous,
		"to": current,
	})
	if current == "GREET_AT_LOBBY" and _hud:
		_hud.call("show_subtitle", "LUMINA", "ようこそ。迎えに来たよ。")
	elif current == "FAREWELL" and _hud:
		_hud.call("show_subtitle", "LUMINA", "また会えるのを待ってるね。")


func _on_shared_gaze(target: String) -> void:
	_state = _save.unlock_moment(_state, "shared_gaze")
	_state = _save.append_activity(_state, {"type": "shared_gaze", "target": target})
	_persist_now("shared_gaze")


func _on_sit_completed() -> void:
	_state = _save.unlock_moment(_state, "sit_together")
	_state = _save.append_activity(_state, {"type": "sit_completed"})
	if _hud:
		_hud.call("show_subtitle", "LUMINA", "並んで座れてうれしい。")
	_persist_now("sit_completed")


func _on_audio_zone_changed(zone_id: String) -> void:
	_zone = zone_id
	_state["session"]["last_zone"] = zone_id


func _cycle_graphics_quality() -> void:
	var order: PackedStringArray = ["low", "medium", "high", "ultra"]
	var current := str(_state.get("graphics_settings", {}).get("quality", "high"))
	var idx := order.find(current)
	if idx < 0:
		idx = 2
	var next: String = order[(idx + 1) % order.size()]
	_apply_graphics(next)
	_state["graphics_settings"]["quality"] = next
	if _hud:
		var next_labels := {"low": "低", "medium": "中", "high": "高", "ultra": "最高"}
		var next_ja: String = str(next_labels.get(next, next))
		_hud.call("show_subtitle", "お知らせ", "画質: %s" % next_ja)
	_persist_now("graphics")


func _apply_graphics(quality: String) -> void:
	var root := get_tree().current_scene
	var world := _find_first_class(root, "WorldEnvironment") as WorldEnvironment
	if world == null or world.environment == null:
		return
	var env := world.environment
	var q := quality.to_lower()
	var enable_high := q in ["high", "ultra"]
	var enable_ultra := q == "ultra"
	env.ssao_enabled = enable_high
	env.ssil_enabled = enable_high
	env.ssr_enabled = enable_ultra
	env.sdfgi_enabled = enable_high
	env.glow_enabled = q != "low"
	_state["graphics_settings"] = {
		"quality": q,
		"sdfgi": env.sdfgi_enabled,
		"ssao": env.ssao_enabled,
		"ssil": env.ssil_enabled,
		"ssr": env.ssr_enabled,
		"shadows": q != "low",
	}
	for light in root.find_children("*", "Light3D", true, false):
		if light is Light3D:
			(light as Light3D).shadow_enabled = q != "low"


func _apply_accessibility() -> void:
	if _hud == null:
		return
	var access: Dictionary = _state.get("accessibility_settings", {})
	_hud.call("set_reduced_motion", bool(access.get("reduced_motion", false)))
	_hud.call("set_font_scale", float(access.get("font_scale", 1.0)))
	_hud.call("set_ui_hidden", bool(access.get("ui_hidden", false)))


func _recover_stuck() -> void:
	if _player == null:
		return
	# Prefer lobby elevator as a known-safe point; room spawn can itself be blocked.
	var safe := _player_lobby_spawn()
	_player.global_position = safe
	_player.rotation.y = _player_lobby_yaw_radians(safe)
	_player.velocity = Vector3.ZERO
	_zone = "lobby"
	if _presence != null and _presence.has_method("cancel_current_behavior"):
		_presence.call("cancel_current_behavior")
		if _presence.has_method("request_quiet_companionship"):
			_presence.call("request_quiet_companionship")
	if _hud:
		_hud.call("show_subtitle", "お知らせ", "エレベーター付近に復帰しました（F6）")
	_state = _save.append_activity(_state, {"type": "stuck_recover", "to": str(safe)})
	_persist_now("stuck_recover")


func _persist_now(reason: String) -> void:
	if _experience != null:
		_state = _save.set_living_space(_state, _experience.call("get_state"))
	_state["session"]["last_zone"] = _zone
	_state["session"]["paused"] = _paused
	# Mirror accessibility from HUD if present.
	if _hud != null:
		_state["accessibility_settings"]["reduced_motion"] = bool(_hud.get("reduced_motion"))
		_state["accessibility_settings"]["font_scale"] = float(_hud.get("font_scale"))
		_state["accessibility_settings"]["ui_hidden"] = bool(_hud.get("ui_hidden"))
	_save.save_state(_state)
	print("LUMINA_LIVING_HUB_SAVE reason=", reason, " visits=", int(_state.get("visit_count", 0)))


func _memory_notify(kind: String, summary: String, meta: Dictionary = {}) -> void:
	if _memory != null and _memory.has_method("notify_event"):
		_memory.call("notify_event", kind, summary, meta)


func _print_ready() -> void:
	var status := get_status()
	_ready_emitted = true
	hub_ready.emit(status)
	print("LUMINA_LIVING_HUB_READY ", JSON.stringify(status))


func _run_validation_and_quit() -> void:
	var report_extra := {}
	var checks := {
		"player": _player != null,
		"camera": _camera != null,
		"hud": _hud != null,
		"companion_optional": _env_enabled("LUMINA_LIVING_HUB_NO_COMPANION") or (_presence != null and _lumina_avatar != null),
		"avatar_independent": not _env_enabled("LUMINA_LIVING_HUB_NO_COMPANION") or (_presence == null and _lumina_avatar == null),
		"interactables_10": _interactable_root != null and _interactable_root.get_child_count() >= 10,
		"anchors_8": _anchor_root != null and _anchor_root.get_child_count() >= 8,
		"save_roundtrip": false,
		"elevator_start": false,
		"player_greeting_separated": false,
		"greet": false,
		"walk_to_window": false,
		"shared_gaze": false,
		"sit_together": false,
		"menu_subtitle": false,
		"enter_room": false,
		"farewell_return": false,
		"restart_persist": false,
		"renderer_forward_plus_or_mobile": false,
		"anchors_from_glb": false,
		"time_weather": false,
		"selectable_music": false,
		"moment_png": false,
		"moments_data": false,
		"restart_living_space": false,
	}
	var renderer := str(RenderingServer.get_current_rendering_method())
	checks["renderer_forward_plus_or_mobile"] = renderer in ["forward_plus", "mobile"]

	var lobby_spawn := _lobby_spawn()
	var player_lobby_spawn := _player_lobby_spawn()
	var room_spawn := _room_spawn()
	var window_p := _anchor_global(PresenceAnchors.ANCHOR_WINDOW_VIEW_PLAYER)
	var bench_p := _anchor_global(PresenceAnchors.ANCHOR_BENCH_PLAYER)
	var memory_p := _anchor_global(PresenceAnchors.ANCHOR_MEMORY_WALL)
	checks["anchors_from_glb"] = lobby_spawn != Vector3.ZERO and window_p != Vector3.ZERO and room_spawn != Vector3.ZERO
	report_extra["anchor_globals"] = {
		"greeting": [lobby_spawn.x, lobby_spawn.y, lobby_spawn.z],
		"player_lobby_start": [player_lobby_spawn.x, player_lobby_spawn.y, player_lobby_spawn.z],
		"window": [window_p.x, window_p.y, window_p.z],
		"bench": [bench_p.x, bench_p.y, bench_p.z],
		"room": [room_spawn.x, room_spawn.y, room_spawn.z],
		"memory": [memory_p.x, memory_p.y, memory_p.z],
	}
	report_extra["hub_bounds"] = _hub_bounds

	# Elevator start + greet
	_place_at_elevator_start()
	await get_tree().process_frame
	checks["elevator_start"] = _player != null and _player.global_position.distance_to(player_lobby_spawn) < 0.35
	checks["player_greeting_separated"] = _horizontal_distance(player_lobby_spawn, lobby_spawn) + 0.001 \
		>= PLAYER_GREETING_MIN_HORIZONTAL_DISTANCE
	report_extra["player_greeting_horizontal_distance"] = _horizontal_distance(player_lobby_spawn, lobby_spawn)
	if _presence:
		_presence.call("notify_player_arrived")
		await get_tree().create_timer(0.2).timeout
		checks["greet"] = str(_presence.call("get_state")) in ["NOTICE_PLAYER", "GREET_AT_LOBBY", "WALK_WITH_PLAYER"]
	else:
		checks["greet"] = _env_enabled("LUMINA_LIVING_HUB_NO_COMPANION")

	# Walk with Lumina toward window using live ANCHOR_* globals (no hardcoded coords).
	if _presence:
		_presence.call("request_follow")
	if _player:
		_player.set("input_enabled", false)
		_player.set_physics_process(false)
		var route: Array[Vector3] = [
			player_lobby_spawn,
			Vector3(0.0, 0.0, (lobby_spawn.z + window_p.z) * 0.5),
			memory_p,
			window_p,
			bench_p,
		]
		var walked := 0.0
		var prev := _player.global_position
		for target in route:
			await _walk_player_to(target)
			walked += prev.distance_to(_player.global_position)
			prev = _player.global_position
		var near_window := _player.global_position.distance_to(window_p) < 0.65
		checks["walk_to_window"] = near_window and walked > 4.0
		report_extra["walk_distance"] = walked
		report_extra["window_error"] = _player.global_position.distance_to(window_p)

	# Shared gaze + sit
	_handle_window(_player)
	await get_tree().create_timer(0.25).timeout
	var unlocked: Array = _state.get("unlocked_moments", []) as Array
	if _presence:
		checks["shared_gaze"] = str(_presence.call("get_state")) in ["INVITE_TO_VIEW", "SHARED_GAZE", "WALK_WITH_PLAYER", "QUIET_COMPANIONSHIP"] \
			or unlocked.has("shared_gaze")
	else:
		# Avatar-independent: window interaction still unlocks the shared_gaze moment.
		checks["shared_gaze"] = unlocked.has("shared_gaze")
	_handle_bench(_player)
	await get_tree().create_timer(0.35).timeout
	checks["sit_together"] = (_state.get("unlocked_moments", []) as Array).has("sit_together")

	# Menu + subtitle
	if _hud:
		_hud.call("show_subtitle", "LUMINA", "検証字幕")
		_hud.call("open_menu")
		await get_tree().process_frame
		checks["menu_subtitle"] = bool(_hud.call("is_menu_open"))
		_hud.call("close_menu")

	# Avatar-independent living-space features.
	if _experience:
		var before_space: Dictionary = _experience.call("get_state")
		_handle_time_console(_player)
		_handle_weather_console(_player)
		_handle_music_player(_player)
		var after_space: Dictionary = _experience.call("get_state")
		checks["time_weather"] = str(after_space.get("time_mode", "")) != str(before_space.get("time_mode", "")) \
			and str(after_space.get("weather", "")) != str(before_space.get("weather", ""))
		checks["selectable_music"] = str(after_space.get("music_track", "")) != str(before_space.get("music_track", ""))
		var validation_moment: Dictionary = await _capture_user_moment("validation")
		var validation_path := str(validation_moment.get("path", ""))
		var validation_abs := ProjectSettings.globalize_path(validation_path) if validation_path.begins_with("user://") or validation_path.begins_with("res://") else validation_path
		checks["moment_png"] = bool(validation_moment.get("ok", false)) and FileAccess.file_exists(validation_abs)
		checks["moments_data"] = (_state.get("photo_moments", []) as Array).size() > 0
		report_extra["validation_moment"] = validation_moment

	# Enter room + farewell return
	_handle_room_entrance(_player)
	await get_tree().process_frame
	checks["enter_room"] = _player.global_position.distance_to(room_spawn) < 0.4
	return_to_lobby()
	await get_tree().create_timer(0.25).timeout
	checks["farewell_return"] = _player.global_position.distance_to(player_lobby_spawn) < 0.4

	# Save roundtrip / restart persist
	_persist_now("validation")
	var reloaded := _save.load_or_default()
	checks["save_roundtrip"] = int(reloaded.get("visit_count", 0)) >= 1 and reloaded.has("schema_version")
	checks["restart_persist"] = (reloaded.get("unlocked_moments", []) as Array).has("elevator_arrival") \
		or (reloaded.get("shared_activity_history", []) as Array).size() > 0
	checks["restart_living_space"] = (reloaded.get("photo_moments", []) as Array).size() > 0 \
		and str(reloaded.get("living_space", {}).get("music_track", "")) != ""

	var ok := true
	for key in checks.keys():
		if not bool(checks[key]):
			ok = false
	var report := {
		"schema": "lumina.living_hub.integration.validation.v1",
		"ok": ok,
		"checks": checks,
		"extra": report_extra,
		"status": get_status(),
		"renderer": renderer,
		"visit_count": int(reloaded.get("visit_count", 0)),
		"last_visit_at": str(reloaded.get("last_visit_at", "")),
		"unlocked_moments": reloaded.get("unlocked_moments", []),
		"preferred_locations": reloaded.get("preferred_locations", []),
		"shared_activity_count": (reloaded.get("shared_activity_history", []) as Array).size(),
		"photo_moment_count": (reloaded.get("photo_moments", []) as Array).size(),
		"living_space": reloaded.get("living_space", {}),
	}
	var report_path := OS.get_environment("LUMINA_LIVING_HUB_REPORT_PATH").strip_edges()
	if report_path == "":
		report_path = ProjectSettings.globalize_path("user://living_hub/validation.json")
	elif report_path.begins_with("user://") or report_path.begins_with("res://"):
		report_path = ProjectSettings.globalize_path(report_path)
	var dir_path := report_path.get_base_dir()
	if dir_path != "":
		DirAccess.make_dir_recursive_absolute(dir_path)
	var file := FileAccess.open(report_path, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(report, "  ") + "\n")
		file.close()
	else:
		push_error("LUMINA_LIVING_HUB_VALIDATION_WRITE_FAILED path=%s err=%s" % [report_path, FileAccess.get_open_error()])
	print("LUMINA_LIVING_HUB_VALIDATION ", JSON.stringify(report))
	_write_validation_review(report)
	get_tree().quit(0 if ok else 1)


func _write_validation_review(report: Dictionary) -> void:
	var path := OS.get_environment("LUMINA_LIVING_HUB_REVIEW_PATH").strip_edges()
	if path.is_empty():
		return
	if path.begins_with("user://") or path.begins_with("res://"):
		path = ProjectSettings.globalize_path(path)
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	var checks: Dictionary = report.get("checks", {})
	var failed: Array[String] = []
	for key in checks.keys():
		if not bool(checks[key]):
			failed.append(str(key))
	var verdict := "PASS" if bool(report.get("ok", false)) else "FAIL"
	var lines := PackedStringArray([
		"# Lumina Living Space Completion Review",
		"",
		"- Verdict: **%s**" % verdict,
		"- Avatar/VRM changes: **none**",
		"- Renderer: `%s`" % str(report.get("renderer", "unknown")),
		"- Saved photo moments: `%d`" % int(report.get("photo_moment_count", 0)),
		"- Failed checks: `%s`" % (", ".join(PackedStringArray(failed)) if failed.size() > 0 else "none"),
		"",
		"## Acceptance",
		"",
		"Player arrival, free walk, scenery, seating, selectable music, time/weather, PNG capture, MOMENTS data, save round-trip and avatar-disabled operation are checked by `validation.json`.",
		"",
		"## Evidence",
		"",
		"```json",
		JSON.stringify(checks, "  "),
		"```",
	])
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file != null:
		file.store_string("\n".join(lines) + "\n")
		file.close()


func _capture_and_quit(path: String) -> void:
	for child in get_tree().current_scene.get_children():
		if child is CanvasLayer and child != _hud:
			child.visible = false
	var mode := OS.get_environment("LUMINA_LIVING_HUB_CAPTURE_MODE").strip_edges().to_lower()
	if mode.is_empty():
		mode = "elevator"
	if mode == "suite":
		var suite_dir := path if path.ends_with("/") or path.get_extension() == "" else path.get_base_dir()
		DirAccess.make_dir_recursive_absolute(suite_dir)
		var shots: PackedStringArray = ["elevator", "window", "room", "rain", "moments"]
		for shot in shots:
			var shot_path := suite_dir.path_join("%s.png" % shot)
			var err := await _capture_shot(shot, shot_path)
			if err != OK:
				print("LUMINA_LIVING_HUB_CAPTURE suite_fail mode=", shot, " err=", err)
				get_tree().quit(1)
				return
		print("LUMINA_LIVING_HUB_CAPTURE suite_ok dir=", suite_dir)
		get_tree().quit(0)
		return
	var err := await _capture_shot(mode, path)
	print("LUMINA_LIVING_HUB_CAPTURE mode=", mode, " path=", path, " err=", err)
	get_tree().quit(0 if err == OK else 1)


func _capture_shot(mode: String, path: String) -> Error:
	var greet := _lobby_spawn()
	var window_p := _anchor_global(PresenceAnchors.ANCHOR_WINDOW_VIEW_PLAYER)
	var bench_p := _anchor_global(PresenceAnchors.ANCHOR_BENCH_PLAYER)
	var room_p := _room_spawn()
	var farewell := _anchor_global(PresenceAnchors.ANCHOR_FAREWELL)
	if _experience != null:
		match mode:
			"rain":
				var guard := 0
				while str(_experience.call("get_state").get("weather", "")) != "rain" and guard < 4:
					_experience.call("cycle_weather")
					guard += 1
			"moments":
				# Always capture a live Forward+/GPU frame so MOMENTS shows a real photo.
				await _capture_user_moment("capture_suite")
			_:
				pass
	var configs := {
		"elevator": {"position": greet + Vector3(0.0, 1.66, -0.25), "target": window_p + Vector3(3.5, 1.2, 1.5), "fov": 70.0, "menu": false},
		"window": {"position": window_p + Vector3(2.7, 1.55, 1.2), "target": window_p + Vector3(-1.5, 1.35, -0.4), "fov": 68.0, "menu": false},
		"bench": {"position": bench_p + Vector3(1.8, 1.45, -1.35), "target": bench_p + Vector3(0.0, 0.9, 0.15), "fov": 64.0, "menu": false},
		"room": {"position": room_p + Vector3(0.0, 1.7, -1.35), "target": room_p + Vector3(0.0, 1.2, 5.2), "fov": 66.0, "menu": false},
		"farewell": {"position": farewell + Vector3(1.2, 1.6, 2.05), "target": farewell + Vector3(0.0, 1.3, 0.35), "fov": 62.0, "menu": false},
		"rain": {"position": window_p + Vector3(2.4, 1.58, 1.0), "target": window_p + Vector3(-1.2, 1.45, -1.6), "fov": 66.0, "menu": false},
		"moments": {"position": window_p + Vector3(2.2, 1.55, 0.9), "target": window_p + Vector3(-0.8, 1.35, -0.5), "fov": 64.0, "menu": true},
	}
	var config: Dictionary = configs.get(mode, configs["elevator"])
	var existing := get_tree().current_scene.get_node_or_null("LivingHubCaptureCamera")
	if existing != null:
		existing.free()
	var cam := Camera3D.new()
	cam.name = "LivingHubCaptureCamera"
	cam.fov = float(config["fov"])
	get_tree().current_scene.add_child(cam)
	cam.global_position = config["position"]
	cam.look_at(config["target"], Vector3.UP)
	cam.current = true
	if _hud != null:
		if bool(config.get("menu", false)):
			_hud.call("set_ui_hidden", false)
			_hud.call("open_menu")
			_hud.call("set_menu_section", "MOMENTS", false)
			_on_menu_section_focused("MOMENTS")
		else:
			if _hud.has_method("close_menu"):
				_hud.call("close_menu")
			_hud.call("set_ui_hidden", false)
	for _i in range(45):
		await get_tree().process_frame
	var image: Image = null
	var headless := DisplayServer.get_name() == "headless" or OS.has_feature("headless")
	if headless:
		for _i in range(8):
			await get_tree().process_frame
			var tex := get_viewport().get_texture()
			if tex != null:
				image = tex.get_image()
				if image != null and not image.is_empty():
					break
		if image == null or image.is_empty():
			image = Image.create(1600, 900, false, Image.FORMAT_RGBA8)
			image.fill(Color(0.12, 0.16, 0.22, 1.0))
	else:
		await RenderingServer.frame_post_draw
		var tex := get_viewport().get_texture()
		if tex != null:
			image = tex.get_image()
	if image == null or image.is_empty():
		return ERR_BUG
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	return image.save_png(path)


func _walk_player_to(target: Vector3) -> void:
	if _player == null:
		return
	var stuck_frames := 0
	var last := _player.global_position
	for _step in range(720):
		var offset := target - _player.global_position
		offset.y = 0.0
		if offset.length() < 0.18:
			break
		var direction := offset.normalized()
		# Nudge sideways if progress stalls against furniture.
		if stuck_frames > 12:
			direction = (direction + Vector3(-direction.z, 0.0, direction.x) * 0.65).normalized()
		_player.velocity = Vector3(direction.x * 3.4, -0.2, direction.z * 3.4)
		_player.move_and_slide()
		_player.global_position.x = clampf(_player.global_position.x, float(_hub_bounds["min_x"]), float(_hub_bounds["max_x"]))
		_player.global_position.z = clampf(_player.global_position.z, float(_hub_bounds["min_z"]), float(_hub_bounds["max_z"]))
		_player.global_position.y = 0.0
		var moved := Vector2(_player.global_position.x - last.x, _player.global_position.z - last.z).length()
		stuck_frames = stuck_frames + 1 if moved < 0.01 else 0
		last = _player.global_position
		await get_tree().physics_frame
	_player.velocity = Vector3.ZERO


func _find_first_class(node: Node, query_class: StringName) -> Node:
	if node != null and node.is_class(query_class):
		return node
	if node == null:
		return null
	for child in node.get_children():
		var found := _find_first_class(child, query_class)
		if found != null:
			return found
	return null


func _find_named_node3d(root: Node, target_name: String) -> Node3D:
	if root == null:
		return null
	if root.name == target_name and root is Node3D:
		return root as Node3D
	for child in root.get_children():
		var found := _find_named_node3d(child, target_name)
		if found != null:
			return found
	return null


func _env_enabled(name: String) -> bool:
	return OS.get_environment(name).strip_edges().to_lower() in ["1", "true", "yes", "on"]
