extends Node3D

const ReceiverScript = preload("res://addons/si_godot_bridge/si_command_receiver.gd")
const WorldControllerScript = preload("res://addons/si_godot_bridge/si_world_command_controller.gd")
const DemoAvatarScript = preload("res://scripts/si_demo_avatar_controller.gd")
const VRMAdapterScript = preload("res://scripts/si_vrm_avatar_adapter.gd")
const GLBAdapterScript = preload("res://scripts/si_glb_avatar_adapter.gd")
const ProximityTriggerScript = preload("res://addons/si_godot_bridge/si_player_proximity_trigger.gd")
const FollowCameraScript = preload("res://scripts/si_follow_camera.gd")
const PlayerAvatarScript = preload("res://scripts/si_player_avatar_controller.gd")
const GodotControlPanelScript = preload("res://scripts/si_godot_control_panel.gd")
const LuminaTitleMenuScript = preload("res://scripts/si_lumina_title_menu.gd")
const LuminaEyeCameraScript = preload("res://scripts/si_lumina_eye_camera.gd")
const LuminaWorldTelemetryScript = preload("res://scripts/lumina_world_telemetry.gd")
const PortableDepthSensorScript = preload("res://scripts/si_portable_depth_sensor.gd")

const FLOOR_SIZE := Vector2(50.0, 50.0)
const NAVIGATION_EDGE_PADDING := 2.0
const WALL_WALK_TARGET_MARGIN := 0.85
const FUTURE_ROOM_V008_HALF_X := 7.55
const FUTURE_ROOM_V008_HALF_Z := 5.55
const FUTURE_ROOM_V008_LUMINA_POSITION := Vector3(0.0, 0.0, -4.45)
const FUTURE_ROOM_V008_USER_HOME_POSITION := Vector3(0.0, 0.0, -2.55)
const FUTURE_ROOM_V008_USER_NEAR_POSITION := Vector3(0.0, 0.0, -3.10)
const FUTURE_ROOM_V008_USER_OBSERVE_POSITION := Vector3(1.85, 0.0, -2.65)
const STAGE_FLOOR_COLOR := Color(0.78, 0.76, 0.69)
const STAGE_FLOOR_EDGE_COLOR := Color(0.61, 0.58, 0.52)
const STAGE_TRIM_COLOR := Color(0.53, 0.50, 0.45)
const STAGE_WALL_COLOR := Color(0.69, 0.71, 0.67)
const STAGE_CEILING_COLOR := Color(0.76, 0.74, 0.68)
const STAGE_ACCENT_WALL_COLOR := Color(0.43, 0.54, 0.58)
const STAGE_PANEL_COLOR := Color(0.61, 0.64, 0.60)
const STAGE_RUG_COLOR := Color(0.48, 0.37, 0.33)
const STAGE_RUG_INSET_COLOR := Color(0.62, 0.47, 0.39)
const STAGE_WARM_LIGHT_COLOR := Color(1.0, 0.90, 0.74)
const USER_PLAYER_MARKER_BASE_COLOR := Color(0.22, 0.52, 0.84)
const USER_PLAYER_MARKER_ACCENT_COLOR := Color(0.81, 0.90, 1.0)
const AI_AVATAR_VRM_PATH := "res://assets/vrm/stella.vrm"
const AI_AVATAR_STAGE10_VRM_CANDIDATE_PATH := "res://assets/avatars/lumina_vrm_candidate_20260704_stage3/lumina_usable_vrm0_stage8_kokoro_audio_sync_preview.tscn"
const AI_AVATAR_GLB_MANIFEST_PATH := "res://assets/avatars/lumina_meshi_biped/animations_fixed/aogiri_v051_enterprise_20260714/lumina_animation_manifest.json"
const AI_AVATAR_GLB_PROFILE_ROOT := "res://assets/avatars/lumina_meshi_biped/animations_fixed/aogiri_v051_enterprise_20260714/"
const AI_AVATAR_GLB_SCENE_CACHE_ROOT := "res://assets/avatars/lumina_meshi_biped/generated_scene_cache/animations_fixed/aogiri_v051_enterprise_20260714/"
const AI_AVATAR_GLB_NATURAL_POLICY_PATH := "res://assets/avatars/lumina_meshi_biped/animations_fixed/aogiri_v051_enterprise_20260714/lumina_natural_animation_policy.json"
const PLAYER_VRM_PATH := "res://assets/vrm/sena.vrm"
const LUMINA_EYE_CAPTURE_ENABLED := true
const LUMINA_EYE_CAPTURE_SIZE := Vector2i(512, 512)
const LUMINA_EYE_CAPTURE_INTERVAL_SECONDS := 1.0
const LUMINA_EYE_CAPTURE_DIR := "res://../artifacts/lumina_eye_capture"
const LUMINA_EYE_CAPTURE_DEFAULT_MODE := "binocular"
const LUMINA_BINOCULAR_EYE_EXTRA_FORWARD_OFFSET := 0.0
const LUMINA_MONOCULAR_EYE_EXTRA_FORWARD_OFFSET := 0.0
const LUMINA_SELF_RENDER_LAYER := 1 << 19
const LUMINA_EYE_CAPTURE_DEFAULT_FOV := 70.0
const LUMINA_EYE_CAPTURE_DEFAULT_FAR := 80.0
const DEFAULT_GAME_WINDOW_SIZE := Vector2i(1280, 720)
const DEFAULT_INTERACTION_MAX_FPS := 30
const DEFAULT_WORLD_STATE_INTERVAL_SEC := 1.5
const DEFAULT_ACTIVE_WORLD_STATE_INTERVAL_SEC := 0.2
const DEFAULT_TTS_VOLUME_DB := 6.0
const WALL_HEIGHT := 3.35
const USE_PROCEDURAL_EXHIBIT_FURNITURE := true
const USE_CLEAN_50M_WORLD_LAYOUT := true
const KENNEY_FURNITURE_LIBRARY := {
	"table": {
		"asset_path": "res://assets/furniture/kenney/tableCoffee.glb",
		"uniform_scale": 2.05,
	},
	"chair": {
		"asset_path": "res://assets/furniture/kenney/chairModernFrameCushion.glb",
		"uniform_scale": 2.25,
	},
	"sofa": {
		"asset_path": "res://assets/furniture/kenney/loungeSofa.glb",
		"uniform_scale": 1.95,
	},
	"plant": {
		"asset_path": "res://assets/furniture/kenney/pottedPlant.glb",
		"uniform_scale": 2.15,
	},
}
const SIT_FURNITURE_TYPES := {
	"chair": true,
	"sofa": true,
	"bench": true,
	"stool": true,
	"couch": true,
	"lounge": true,
}
const APPROACHABLE_FURNITURE_TYPES := {
	"chair": true,
	"sofa": true,
	"bench": true,
	"stool": true,
	"couch": true,
	"lounge": true,
	"table": true,
	"plant": true,
	"desk": true,
	"shelf": true,
	"console": true,
	"small_item": true,
	"soft_object": true,
	"landmark": true,
	"sign": true,
	"light": true,
	"art": true,
}

var _avatar: Node3D
var _world_environment: WorldEnvironment
var _world_controller: Node
var _receiver: Node
var _camera: Camera3D
var _lumina_eye_camera: Camera3D
var _lumina_left_eye_camera: Camera3D
var _lumina_right_eye_camera: Camera3D
var _lumina_eye_viewport: SubViewport
var _lumina_eye_viewport_camera: Camera3D
var _lumina_eye_capture_timer: Timer
var _lumina_eye_capture_latest_path := ""
var _lumina_eye_capture_latest_paths := {}
var _lumina_eye_capture_label := "binocular_eye"
var _lumina_eye_capture_mode := LUMINA_EYE_CAPTURE_DEFAULT_MODE
var _lumina_eye_capture_modes := []
var _lumina_eye_capture_pending_modes := []
var _lumina_eye_capture_source_name := ""
var _lumina_eye_capture_fov := LUMINA_EYE_CAPTURE_DEFAULT_FOV
var _lumina_eye_capture_far := LUMINA_EYE_CAPTURE_DEFAULT_FAR
var _lumina_binocular_eye_extra_forward_offset := LUMINA_BINOCULAR_EYE_EXTRA_FORWARD_OFFSET
var _lumina_monocular_eye_extra_forward_offset := LUMINA_MONOCULAR_EYE_EXTRA_FORWARD_OFFSET
var _lumina_eye_capture_debug_target_position: Variant = null
var _lumina_eye_capture_debug_target_alias := ""
var _lumina_eye_capture_debug_target_canonical := ""
var _lumina_eye_capture_busy := false
var _lumina_eye_capture_once := false
var _lumina_eye_capture_once_completed := false
var _lumina_eye_capture_quit_after_once := false
var _lumina_eye_capture_hidden_self_meshes: Array[Node] = []
var _lumina_eye_capture_sequence := 0
var _lumina_eye_capture_started_unix := 0.0
var _lumina_eye_capture_latest_metadata := {}
var _lumina_eye_capture_requested_usec := 0
var _lumina_eye_capture_previous_start_usec := 0
var _lumina_eye_capture_timing := {}
var _lumina_eye_capture_previous_timing := {}
var _lumina_eye_capture_portable_snapshot := {}
var _player_avatar: Node3D
var _navigation_region: NavigationRegion3D
var _proximity_trigger: Node
var _control_panel: CanvasLayer
var _title_menu: CanvasLayer


func _ready() -> void:
	Engine.max_fps = DEFAULT_INTERACTION_MAX_FPS
	_setup_runtime_window()
	_setup_environment()
	_setup_floor()
	_create_perimeter_collision_shell()
	if _future_room_v008_solo_enabled():
		_hide_future_room_v008_solo_stage_visuals()
	_create_navigation_region()

	var objects := Node3D.new()
	objects.name = "Objects"
	add_child(objects)

	_avatar = _create_avatar()
	_create_lumina_eye_camera(_avatar)
	_setup_lumina_eye_capture_viewport()
	_create_player()
	_setup_camera(_player_avatar)
	var future_room_v008_solo := _future_room_v008_solo_enabled()
	if future_room_v008_solo:
		print("FUTURE_ROOM_V008_SOLO_MODE old_50m_props_skipped=true")
	elif USE_CLEAN_50M_WORLD_LAYOUT:
		_create_clean_50m_world_layout(objects)
	else:
		_create_prop(objects, "Table", "table", Vector3(3.65, 0.0, 2.65), Vector3(1.6, 0.34, 1.05), Color(0.59, 0.45, 0.28))
		var chair_a := _create_prop(objects, "Chair_A", "chair", Vector3(2.42, 0.0, 2.22), Vector3(0.64, 0.52, 0.70), Color(0.42, 0.44, 0.48))
		chair_a.rotation_degrees.y = -18.0
		var chair_b := _create_prop(objects, "Chair_B", "chair", Vector3(4.58, 0.0, 3.12), Vector3(0.64, 0.52, 0.70), Color(0.38, 0.39, 0.40))
		chair_b.rotation_degrees.y = 158.0
		var sofa := _create_prop(objects, "Sofa", "sofa", Vector3(-5.85, 0.0, 0.45), Vector3(1.95, 0.58, 0.98), Color(0.39, 0.47, 0.60))
		sofa.rotation_degrees.y = 14.0
		_create_prop(objects, "SideTable", "table", Vector3(-7.08, 0.0, 1.16), Vector3(0.72, 0.44, 0.58), Color(0.42, 0.30, 0.20))
		_create_prop(objects, "Plant", "plant", Vector3(-7.18, 0.0, 3.28), Vector3(0.5, 1.05, 0.5), Color(0.24, 0.49, 0.33))
		_create_static_box(objects, "Rug", Vector3(0.0, 0.002, 0.15), Vector3(4.3, 0.012, 3.35), STAGE_RUG_COLOR)
		_create_static_box(objects, "RugInset", Vector3(0.0, 0.006, 0.15), Vector3(3.55, 0.012, 2.64), STAGE_RUG_INSET_COLOR)
		_create_static_box(objects, "SmallPlinth", Vector3(-4.45, 0.0, -1.25), Vector3(0.95, 0.2, 0.75), Color(0.41, 0.38, 0.34))
		_create_room_detail_props(objects)
		_create_room_completion_props(objects)
		_create_demo_space_expansion_props(objects)
		_create_closeup_model_detail_props(objects)
		_create_vrchat_scale_exploration_props(objects)
		_create_expanded_room_furnishing(objects)
		_create_far_wall_landmarks(objects)
	_create_wall_walk_targets(objects)
	_add_future_room_v007_candidate_if_requested(objects)
	_place_lumina_at_future_room_v007_marker_if_requested()
	_add_future_room_v008_candidate_if_requested(objects)
	if _future_room_v008_as_active_room_enabled():
		_create_future_room_v008_interaction_anchors(objects)
	_place_lumina_at_future_room_v008_marker_if_requested()
	_apply_lumina_eye_capture_debug_target(objects)

	_create_world_controller(objects)
	var disable_bridge_for_eye_capture := OS.get_environment("LUMINA_EYE_CAPTURE_DISABLE_BRIDGE").strip_edges().to_lower() in ["1", "true", "yes", "on"]
	if not disable_bridge_for_eye_capture:
		_create_receiver()
	if not disable_bridge_for_eye_capture:
		_create_player_proximity_trigger()
		_create_godot_control_panel()
		_create_title_menu()
	_setup_preintegration_telemetry(objects)


func _setup_preintegration_telemetry(objects: Node) -> void:
	var enabled := OS.get_environment("LUMINA_PREINTEGRATION_TELEMETRY").strip_edges().to_lower() in ["1", "true", "yes", "on"]
	if not enabled:
		return
	var telemetry := LuminaWorldTelemetryScript.new()
	telemetry.name = "LuminaPreintegrationWorldTelemetry"
	telemetry.capture_on_process = true
	var interval_raw := OS.get_environment("LUMINA_PREINTEGRATION_TELEMETRY_INTERVAL").strip_edges()
	if interval_raw.is_valid_float():
		telemetry.capture_interval_seconds = clampf(interval_raw.to_float(), 0.1, 60.0)
	telemetry.output_path = OS.get_environment("LUMINA_PREINTEGRATION_TELEMETRY_PATH").strip_edges()
	if telemetry.output_path.is_empty():
		telemetry.output_path = "user://lumina_preintegration/world_telemetry.jsonl"
	telemetry.room_id = "future_room_v008" if _future_room_v008_as_active_room_enabled() else "lumina_demo_room"
	telemetry.zone_id = "main_living_zone"
	add_child(telemetry)
	var navigation_agent: Node = _avatar.get_node_or_null("NavigationAgent3D") if _avatar != null else null
	telemetry.configure_runtime_nodes(
		_player_avatar,
		_avatar,
		navigation_agent,
		_navigation_region,
		_camera,
		self,
		self,
		objects
	)
	print("LUMINA_PREINTEGRATION_TELEMETRY_ENABLED path=", telemetry.output_path)


func _add_future_room_v007_candidate_if_requested(parent: Node) -> void:
	var enabled := OS.get_environment("LUMINA_ENABLE_FUTURE_ROOM_V007").strip_edges().to_lower() in ["1", "true", "yes", "on"]
	if not enabled:
		return

	var use_glb := OS.get_environment("LUMINA_FUTURE_ROOM_V007_USE_GLB").strip_edges().to_lower() in ["1", "true", "yes", "on"]
	var scene_path := "res://scenes/future_room_v007_glb_loader.tscn" if use_glb else "res://scenes/future_room_v007_preview.tscn"
	var packed := load(scene_path) as PackedScene
	if packed == null:
		push_warning("Future room v007 candidate scene could not be loaded: %s" % scene_path)
		return

	var instance := packed.instantiate() as Node3D
	if instance == null:
		push_warning("Future room v007 candidate scene is not a Node3D: %s" % scene_path)
		return

	instance.name = "FutureRoomV007Candidate_GLB" if use_glb else "FutureRoomV007Candidate_Procedural"
	instance.position = _get_future_room_v007_position()
	parent.add_child(instance)
	print("FUTURE_ROOM_V007_CANDIDATE_ADDED use_glb=", use_glb, " position=", instance.position)


func _get_future_room_v007_position() -> Vector3:
	var raw := OS.get_environment("LUMINA_FUTURE_ROOM_V007_POSITION").strip_edges()
	if raw == "":
		return Vector3(0.0, 0.0, -14.0)
	var parts := raw.split(",", false)
	if parts.size() != 3:
		push_warning("LUMINA_FUTURE_ROOM_V007_POSITION must be x,y,z. Using default candidate position.")
		return Vector3(0.0, 0.0, -14.0)
	return Vector3(parts[0].to_float(), parts[1].to_float(), parts[2].to_float())


func _place_lumina_at_future_room_v007_marker_if_requested() -> void:
	var enabled := OS.get_environment("LUMINA_FUTURE_ROOM_V007_PLACE_LUMINA").strip_edges().to_lower() in ["1", "true", "yes", "on"]
	if not enabled:
		return
	if _avatar == null:
		push_warning("Future room v007 Lumina placement requested, but _avatar is null.")
		return

	var room_origin := _get_future_room_v007_position()
	var marker_local := Vector3(0.0, 0.0, -2.55)
	var target := room_origin + marker_local
	var raw_target := OS.get_environment("LUMINA_FUTURE_ROOM_V007_LUMINA_POSITION").strip_edges()
	if raw_target != "":
		var parts := raw_target.split(",", false)
		if parts.size() == 3:
			target = Vector3(parts[0].to_float(), parts[1].to_float(), parts[2].to_float())
		else:
			push_warning("LUMINA_FUTURE_ROOM_V007_LUMINA_POSITION must be x,y,z. Using marker-based position.")

	_avatar.position = target
	_avatar.rotation_degrees.y = 180.0
	print("FUTURE_ROOM_V007_LUMINA_PLACED position=", _avatar.position, " rotation_y=", _avatar.rotation_degrees.y)


func _add_future_room_v008_candidate_if_requested(parent: Node) -> void:
	var enabled := _future_room_v008_as_active_room_enabled() or OS.get_environment("LUMINA_ENABLE_FUTURE_ROOM_V008").strip_edges().to_lower() in ["1", "true", "yes", "on"]
	if not enabled:
		return

	var scene_path := "res://scenes/future_room_v008_glb_loader.tscn"
	var packed := load(scene_path) as PackedScene
	if packed == null:
		push_warning("Future room v008 candidate scene could not be loaded: %s" % scene_path)
		return

	var instance := packed.instantiate() as Node3D
	if instance == null:
		push_warning("Future room v008 candidate scene is not a Node3D: %s" % scene_path)
		return

	instance.name = "FutureRoomV008Candidate_GLB"
	instance.position = _get_future_room_v008_position()
	parent.add_child(instance)
	print("FUTURE_ROOM_V008_CANDIDATE_ADDED position=", instance.position)


func _get_future_room_v008_position() -> Vector3:
	var raw := OS.get_environment("LUMINA_FUTURE_ROOM_V008_POSITION").strip_edges()
	if raw == "":
		if _future_room_v008_as_active_room_enabled():
			return Vector3.ZERO
		return Vector3(0.0, 0.0, -14.0)
	var parts := raw.split(",", false)
	if parts.size() != 3:
		push_warning("LUMINA_FUTURE_ROOM_V008_POSITION must be x,y,z. Using default candidate position.")
		return Vector3(0.0, 0.0, -14.0)
	return Vector3(parts[0].to_float(), parts[1].to_float(), parts[2].to_float())


func _place_lumina_at_future_room_v008_marker_if_requested() -> void:
	var explicit_place := OS.get_environment("LUMINA_FUTURE_ROOM_V008_PLACE_LUMINA").strip_edges().to_lower() in ["1", "true", "yes", "on"]
	var enabled := _future_room_v008_as_active_room_enabled() or explicit_place
	if not enabled:
		return
	if _avatar == null:
		push_warning("Future room v008 Lumina placement requested, but _avatar is null.")
		return

	var room_origin := _get_future_room_v008_position()
	var marker_local := FUTURE_ROOM_V008_LUMINA_POSITION
	var target := room_origin + marker_local
	var marker_source := "fallback_constant"
	var standing_marker := find_child("LuminaStandingMarker_V008_no_collision", true, false) as Node3D
	if standing_marker != null:
		target = standing_marker.global_position
		# The marker mesh floats slightly above the floor for visibility; keep the
		# avatar root on the room floor while using the marker's collision-safe XZ.
		target.y = room_origin.y + marker_local.y
		marker_source = String(standing_marker.get_path())
	var raw_target := OS.get_environment("LUMINA_FUTURE_ROOM_V008_LUMINA_POSITION").strip_edges()
	if raw_target != "":
		var parts := raw_target.split(",", false)
		if parts.size() == 3:
			target = Vector3(parts[0].to_float(), parts[1].to_float(), parts[2].to_float())
			marker_source = "env_override"
		else:
			push_warning("LUMINA_FUTURE_ROOM_V008_LUMINA_POSITION must be x,y,z. Using marker-based position.")

	_avatar.position = target
	_avatar.rotation_degrees.y = 180.0
	if is_instance_valid(_player_avatar) and _player_avatar.has_method("face_position"):
		_player_avatar.call("face_position", _avatar.global_position)
	print("FUTURE_ROOM_V008_LUMINA_PLACED position=", _avatar.position, " rotation_y=", _avatar.rotation_degrees.y, " source=", marker_source)


func _future_room_v008_solo_enabled() -> bool:
	var raw := OS.get_environment("LUMINA_FUTURE_ROOM_V008_SOLO").strip_edges().to_lower()
	if raw != "":
		return raw in ["1", "true", "yes", "on"]
	return _future_room_v008_as_active_room_enabled()


func _future_room_v008_as_active_room_enabled() -> bool:
	var disabled := OS.get_environment("LUMINA_DISABLE_FUTURE_ROOM_V008_ACTIVE_ROOM").strip_edges().to_lower()
	return not (disabled in ["1", "true", "yes", "on"])


func _future_room_v008_room_only_preserve_legacy_environment() -> bool:
	# ROOM_ONLY既定: trueで固定。v008は「部屋の見た目」だけを差し替え、
	# User/カメラ/移動範囲/anchorsは旧環境のまま維持する。Luminaだけは
	# v008家具との初期重なりを避けるため、既存standing markerのXZへ置く。
	# 旧v008挙動 (位置上書き等) に戻したい時だけ LUMINA_FUTURE_ROOM_V008_ROOM_ONLY=0 で明示無効化する。
	var raw := OS.get_environment("LUMINA_FUTURE_ROOM_V008_ROOM_ONLY").strip_edges().to_lower()
	if raw != "":
		return raw in ["1", "true", "yes", "on"]
	return true


func _hide_future_room_v008_solo_stage_visuals() -> void:
	var prefixes := [
		"Floor",
		"Wall",
		"Ceiling",
		"BackWall",
		"LeftWall",
		"RightWall",
	]
	var hidden_count := 0
	var stack: Array[Node] = [self]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		var geometry := node as GeometryInstance3D
		if geometry != null:
			var owner_name := _future_room_v008_solo_stage_visual_owner_name(geometry, prefixes)
			if not owner_name.is_empty():
					geometry.visible = false
					hidden_count += 1
		for child in node.get_children():
			stack.append(child as Node)
	print("FUTURE_ROOM_V008_SOLO_STAGE_VISUALS_HIDDEN count=", hidden_count)


func _future_room_v008_solo_stage_visual_owner_name(node: Node, prefixes: Array) -> String:
	var current: Node = node
	while current != null and current != self:
		for prefix in prefixes:
			if current.name.begins_with(str(prefix)):
				return current.name
		current = current.get_parent()
	return ""


func _create_clean_50m_world_layout(parent: Node) -> void:
	# The older 50m pass stacked several demo/test layers on top of each other.
	# This clean layout keeps the known target names, but places them in separated,
	# readable clusters so the world stops looking like a pile of validation props.
	_create_static_box(parent, "CleanMainWalkway_NorthSouth", Vector3(0.0, 0.018, 0.0), Vector3(2.15, 0.012, 43.0), Color(0.54, 0.51, 0.45))
	_create_static_box(parent, "CleanMainWalkway_EastWest", Vector3(0.0, 0.020, 0.0), Vector3(43.0, 0.012, 2.15), Color(0.56, 0.53, 0.47))
	_create_static_box(parent, "CleanCenterRug", Vector3(0.0, 0.024, 0.0), Vector3(7.4, 0.014, 5.4), STAGE_RUG_COLOR)
	_create_static_box(parent, "CleanCenterRugInset", Vector3(0.0, 0.036, 0.0), Vector3(6.4, 0.010, 4.4), STAGE_RUG_INSET_COLOR)

	var center_table := _create_prop(parent, "CenterConversationTable", "table", Vector3(0.0, 0.0, 0.9), Vector3(1.35, 0.42, 0.92), Color(0.48, 0.35, 0.22))
	_tag_spatial_object(center_table, "中央の会話テーブル", "center_home", ["center table", "conversation table", "中央テーブル", "テーブル"], "中央ホームエリアにある会話用のテーブル。")
	var center_chair_left := _create_prop(parent, "CenterVisitorChairLeft", "chair", Vector3(-1.85, 0.0, 0.35), Vector3(0.66, 0.52, 0.70), Color(0.34, 0.37, 0.40))
	_tag_spatial_object(center_chair_left, "中央左チェア", "center_home", ["center chair", "left chair", "中央左チェア", "中央の椅子", "椅子"], "中央の会話テーブル左側にある来客用の椅子。")
	center_chair_left.rotation_degrees.y = 68.0
	var center_chair_right := _create_prop(parent, "CenterVisitorChairRight", "chair", Vector3(1.85, 0.0, 0.35), Vector3(0.66, 0.52, 0.70), Color(0.34, 0.37, 0.40))
	_tag_spatial_object(center_chair_right, "中央右チェア", "center_home", ["center chair", "right chair", "中央右チェア", "中央の椅子", "椅子"], "中央の会話テーブル右側にある来客用の椅子。")
	center_chair_right.rotation_degrees.y = -68.0
	var welcome_board := _create_prop(parent, "CenterWelcomeBoardMarker", "sign", Vector3(0.0, 0.72, -3.15), Vector3(1.45, 0.92, 0.08), Color(0.55, 0.48, 0.36))
	_tag_spatial_object(welcome_board, "中央ウェルカムボード", "center_home", ["welcome board", "center board", "案内板", "ウェルカムボード"], "Luminaの中央ホームエリアにある小さな案内板。")
	var center_clock := _create_prop(parent, "CenterClockMarker", "landmark", Vector3(3.45, 1.17, -2.2), Vector3(0.62, 0.62, 0.08), Color(0.68, 0.62, 0.50))
	_tag_spatial_object(center_clock, "中央時計", "center_home", ["center clock", "clock", "時計"], "中央エリアの壁寄りにある丸い時計風の目印。")

	_create_static_box(parent, "ReadingAreaRug", Vector3(-16.4, 0.023, 0.0), Vector3(7.8, 0.014, 6.3), Color(0.48, 0.44, 0.38))
	var reading_chair := _create_prop(parent, "ReadingLoungeChair", "chair", Vector3(-15.25, 0.0, 0.75), Vector3(0.76, 0.58, 0.82), Color(0.40, 0.43, 0.48))
	reading_chair.rotation_degrees.y = 132.0
	_tag_spatial_object(reading_chair, "読書チェア", "reading_corner", ["reading chair", "読書チェア", "椅子"], "読書エリアにある落ち着いた椅子。")
	var reading_table := _create_prop(parent, "ReadingRoundTable", "table", Vector3(-16.9, 0.0, -0.55), Vector3(0.74, 0.42, 0.62), Color(0.43, 0.31, 0.20))
	_tag_spatial_object(reading_table, "読書丸テーブル", "reading_corner", ["reading table", "round table", "読書テーブル", "丸テーブル"], "読書チェアの前にある小さな丸テーブル。")
	var reading_lamp := _create_prop(parent, "ReadingWarmLampMarker", "plant", Vector3(-18.35, 0.0, 1.45), Vector3(0.34, 0.98, 0.34), Color(0.78, 0.66, 0.42))
	_tag_spatial_object(reading_lamp, "読書ランプ", "reading_corner", ["reading lamp", "reading light", "読書ランプ", "ランプ"], "読書丸テーブルの近くにある暖色のランプ。")
	var reading_books := _create_prop(parent, "ReadingBookStackMarker", "small_item", Vector3(-16.95, 0.46, -0.55), Vector3(0.42, 0.12, 0.30), Color(0.25, 0.30, 0.38))
	_tag_spatial_object(reading_books, "読書エリアの本", "reading_corner", ["book stack", "books", "本", "読書本"], "読書丸テーブルの上に重ねて置いた本。")
	var reading_shelf := _create_static_box(parent, "ReadingBookshelf", Vector3(-21.7, 1.05, 0.0), Vector3(0.42, 2.10, 3.20), Color(0.34, 0.25, 0.17))
	_tag_spatial_object(reading_shelf, "読書本棚", "reading_corner", ["bookshelf", "shelf", "本棚", "棚"], "読書エリアの壁側にある背の高い本棚。")

	_create_static_box(parent, "StudioAreaRug", Vector3(16.0, 0.023, -3.0), Vector3(8.4, 0.014, 6.4), Color(0.43, 0.39, 0.33))
	var studio_desk := _create_prop(parent, "StudioLongDesk", "table", Vector3(17.0, 0.0, -4.6), Vector3(2.45, 0.68, 0.92), Color(0.50, 0.35, 0.21))
	_tag_spatial_object(studio_desk, "スタジオ長机", "studio_corner", ["studio desk", "long desk", "作業机", "デスク"], "スタジオエリアにある作業机。")
	var studio_chair := _create_prop(parent, "StudioChair", "chair", Vector3(16.1, 0.0, -2.9), Vector3(0.70, 0.55, 0.75), Color(0.28, 0.31, 0.34))
	_tag_spatial_object(studio_chair, "スタジオチェア", "studio_corner", ["studio chair", "作業椅子", "スタジオチェア", "椅子", "チェア"], "スタジオ長机の手前にある作業用の椅子。")
	studio_chair.rotation_degrees.y = 180.0
	var studio_monitor := _create_prop(parent, "StudioMonitorMarker", "landmark", Vector3(17.15, 0.75, -5.22), Vector3(0.98, 0.56, 0.08), Color(0.10, 0.14, 0.17))
	_tag_spatial_object(studio_monitor, "スタジオモニター", "studio_corner", ["studio monitor", "monitor", "display", "スタジオモニター", "モニター"], "スタジオ長机の奥にある暗いモニター。")
	var studio_plant := _create_prop(parent, "StudioTallPlant", "plant", Vector3(20.6, 0.0, -1.3), Vector3(0.56, 1.20, 0.56), Color(0.20, 0.45, 0.30))
	_tag_spatial_object(studio_plant, "スタジオの植物", "studio_corner", ["studio plant", "観葉植物", "植物"], "スタジオエリアの端にある植物。")

	_create_static_box(parent, "RearLoungeRug", Vector3(0.0, 0.023, 17.4), Vector3(12.6, 0.014, 6.6), Color(0.40, 0.35, 0.39))
	var rear_sofa_left := _create_prop(parent, "RearLoungeSofaLeft", "sofa", Vector3(-4.3, 0.0, 17.35), Vector3(2.10, 0.56, 0.88), Color(0.36, 0.46, 0.55))
	rear_sofa_left.rotation_degrees.y = 26.0
	_tag_spatial_object(rear_sofa_left, "奥ラウンジ左ソファ", "rear_lounge", ["rear sofa", "left sofa", "奥ソファ", "ソファ"], "奥ラウンジにある左側のソファ。")
	var rear_sofa_right := _create_prop(parent, "RearLoungeSofaRight", "sofa", Vector3(4.3, 0.0, 17.35), Vector3(2.10, 0.56, 0.88), Color(0.39, 0.43, 0.50))
	_tag_spatial_object(rear_sofa_right, "奥ラウンジ右ソファ", "rear_lounge", ["rear sofa", "right sofa", "lounge sofa", "奥ソファ", "右ソファ", "ソファ"], "奥ラウンジにある右側のソファ。")
	rear_sofa_right.rotation_degrees.y = -26.0
	var rear_table := _create_prop(parent, "RearLoungeLowTable", "table", Vector3(0.0, 0.0, 17.25), Vector3(1.55, 0.38, 0.86), Color(0.45, 0.31, 0.20))
	_tag_spatial_object(rear_table, "奥ラウンジローテーブル", "rear_lounge", ["rear table", "low table", "奥テーブル", "ローテーブル"], "奥ラウンジの中央にあるローテーブル。")
	var rear_side_table := _create_prop(parent, "RearLoungeSideTable", "table", Vector3(-6.85, 0.0, 16.95), Vector3(0.58, 0.44, 0.58), Color(0.40, 0.29, 0.20))
	_tag_spatial_object(rear_side_table, "奥ラウンジサイドテーブル", "rear_lounge", ["rear side table", "side table", "奥サイドテーブル", "サイドテーブル"], "奥ラウンジ左ソファの横にある小さなサイドテーブル。")

	_create_static_box(parent, "FrontGalleryRunner", Vector3(0.0, 0.023, -17.6), Vector3(14.8, 0.014, 3.6), Color(0.38, 0.36, 0.34))
	var gallery_bench := _create_prop(parent, "FrontGalleryBench", "sofa", Vector3(0.0, 0.0, -15.9), Vector3(2.05, 0.50, 0.64), Color(0.39, 0.36, 0.32))
	gallery_bench.rotation_degrees.y = 180.0
	_tag_spatial_object(gallery_bench, "前ギャラリーベンチ", "front_gallery", ["gallery bench", "bench", "ギャラリーベンチ", "ベンチ"], "前ギャラリーにある短いベンチ。")
	var gallery_art := _create_prop(parent, "FrontGalleryWallArtMarker", "art", Vector3(0.0, 0.98, -24.72), Vector3(1.80, 0.95, 0.055), Color(0.48, 0.45, 0.38))
	_tag_spatial_object(gallery_art, "前ギャラリー壁アート", "front_gallery", ["gallery wall art", "wall art", "壁アート", "展示"], "前ギャラリーの壁側にある展示用の壁アート。")
	for i in range(3):
		var x := -4.0 + float(i) * 4.0
		_create_static_box(parent, "CleanGalleryPlinth%02d" % i, Vector3(x, 0.35, -18.45), Vector3(0.72, 0.70, 0.72), Color(0.57, 0.55, 0.50))

	var entry_bench := _create_prop(parent, "SouthEntryBench", "sofa", Vector3(-14.4, 0.0, -12.9), Vector3(1.75, 0.46, 0.62), Color(0.36, 0.39, 0.42))
	entry_bench.rotation_degrees.y = 8.0
	_tag_spatial_object(entry_bench, "南入口ベンチ", "south_entry", ["entry bench", "入口ベンチ", "ベンチ"], "南側入口寄りにある短いベンチ。")
	var entry_sign := _create_prop(parent, "SouthEntrySignMarker", "sign", Vector3(-16.4, 0.70, -15.3), Vector3(1.02, 0.70, 0.08), Color(0.58, 0.48, 0.34))
	_tag_spatial_object(entry_sign, "南入口サイン", "south_entry", ["south entry sign", "entry sign", "welcome sign", "入口サイン", "案内板"], "南入口ベンチの近くにある小さな案内サイン。")

	var north_plant := _create_prop(parent, "NorthTerraceTallPlant", "plant", Vector3(-18.8, 0.0, 15.4), Vector3(0.52, 1.22, 0.52), Color(0.18, 0.45, 0.29))
	_tag_spatial_object(north_plant, "北テラスの背の高い植物", "north_terrace", ["north terrace plant", "北テラス植物", "植物"], "北テラスの角にある背の高い植物。")
	var west_photo := _create_prop(parent, "WestMemoryPhotoPanelMarker", "art", Vector3(-24.72, 1.01, 8.4), Vector3(0.055, 0.88, 1.10), Color(0.54, 0.48, 0.39))
	_tag_spatial_object(west_photo, "西メモリー写真パネル", "west_memory", ["photo panel", "memory photo", "西写真パネル", "写真パネル"], "西メモリーエリアの壁にある写真のようなパネル。")
	var east_tool := _create_prop(parent, "EastToolShelf", "table", Vector3(22.0, 0.0, 7.8), Vector3(0.82, 0.72, 1.62), Color(0.38, 0.30, 0.22))
	east_tool.rotation_degrees.y = 90.0
	_tag_zone_partition(east_tool, "東ツール棚", "east_tool", ["tool shelf", "東棚", "ツール棚", "棚"], "東側の壁沿いにある小物置き棚。")
	_create_static_box(parent, "CleanNWBluePillar", Vector3(-23.45, 1.05, 23.45), Vector3(0.58, 2.10, 0.58), Color(0.18, 0.35, 0.70))
	_create_static_box(parent, "CleanSEYellowPillar", Vector3(23.45, 1.05, -23.45), Vector3(0.58, 2.10, 0.58), Color(0.76, 0.62, 0.18))
	_create_clean_50m_completion_layer(parent)
	_create_clean_50m_zone_partitions(parent)
	_create_final_life_zoning_layer(parent)
	_create_wall_landmark_rhythm(parent)
	_create_wall_walk_targets(parent)


func _tag_zone_partition(node: Node3D, display_name: String, area: String, aliases: Array, search_hint: String) -> void:
	node.add_to_group("si_addressable")
	node.set_meta("si_type", "partition")
	node.set_meta("si_can_approach", false)
	_tag_spatial_object(node, display_name, area, aliases, search_hint)
	node.set_meta("si_type", "partition")
	node.set_meta("si_can_approach", false)


func _create_clean_50m_zone_partitions(parent: Node) -> void:
	var center_wall_color := Color(0.72, 0.60, 0.43, 0.96)
	var reading_wall_color := Color(0.58, 0.46, 0.31, 0.96)
	var studio_wall_color := Color(0.30, 0.38, 0.50, 0.96)
	var lounge_wall_color := Color(0.44, 0.36, 0.48, 0.96)
	var gallery_wall_color := Color(0.54, 0.51, 0.46, 0.96)
	var terrace_wall_color := Color(0.34, 0.48, 0.39, 0.96)

	var center_nw := _create_static_box(parent, "CenterHomeLowRailNorthWest", Vector3(-3.2, 0.0, 5.15), Vector3(3.7, 0.62, 0.16), center_wall_color)
	_tag_zone_partition(center_nw, "中央ラウンジ北西の低い仕切り", "center_lounge", ["中央仕切り", "ラウンジ仕切り", "低い仕切り"], "中央ラウンジをゆるく区切る低い仕切り。中央通路は空いている。")
	var center_ne := _create_static_box(parent, "CenterHomeLowRailNorthEast", Vector3(3.2, 0.0, 5.15), Vector3(3.7, 0.62, 0.16), center_wall_color)
	_tag_zone_partition(center_ne, "中央ラウンジ北東の低い仕切り", "center_lounge", ["中央仕切り", "ラウンジ仕切り", "低い仕切り"], "中央ラウンジをゆるく区切る低い仕切り。中央通路は空いている。")
	var center_sw := _create_static_box(parent, "CenterHomeLowRailSouthWest", Vector3(-3.2, 0.0, -5.15), Vector3(3.7, 0.62, 0.16), center_wall_color)
	_tag_zone_partition(center_sw, "中央ラウンジ南西の低い仕切り", "center_lounge", ["中央仕切り", "ラウンジ仕切り", "低い仕切り"], "中央ラウンジをゆるく区切る低い仕切り。中央通路は空いている。")
	var center_se := _create_static_box(parent, "CenterHomeLowRailSouthEast", Vector3(3.2, 0.0, -5.15), Vector3(3.7, 0.62, 0.16), center_wall_color)
	_tag_zone_partition(center_se, "中央ラウンジ南東の低い仕切り", "center_lounge", ["中央仕切り", "ラウンジ仕切り", "低い仕切り"], "中央ラウンジをゆるく区切る低い仕切り。中央通路は空いている。")
	var center_w := _create_static_box(parent, "CenterHomeLowRailWest", Vector3(-5.15, 0.0, 0.0), Vector3(0.16, 0.62, 4.0), center_wall_color)
	_tag_zone_partition(center_w, "中央ラウンジ西側の低い仕切り", "center_lounge", ["中央西仕切り", "ラウンジ西仕切り"], "中央ラウンジの西側境界。南北の通路は空いている。")
	var center_e := _create_static_box(parent, "CenterHomeLowRailEast", Vector3(5.15, 0.0, 0.0), Vector3(0.16, 0.62, 4.0), center_wall_color)
	_tag_zone_partition(center_e, "中央ラウンジ東側の低い仕切り", "center_lounge", ["中央東仕切り", "ラウンジ東仕切り"], "中央ラウンジの東側境界。南北の通路は空いている。")

	var reading_back := _create_static_box(parent, "ReadingZoneBackHalfWall", Vector3(-16.5, 0.0, 4.35), Vector3(7.5, 1.08, 0.18), reading_wall_color)
	_tag_zone_partition(reading_back, "読書エリア奥の半壁", "reading_corner", ["読書エリアの壁", "読書仕切り", "本棚側の仕切り"], "読書エリアの奥を区切る半分の高さの壁。")
	var reading_south := _create_static_box(parent, "ReadingZoneSouthHalfWall", Vector3(-19.9, 0.0, -1.65), Vector3(0.18, 1.02, 5.2), reading_wall_color)
	_tag_zone_partition(reading_south, "読書エリア南側の半壁", "reading_corner", ["読書エリアの入口", "読書仕切り"], "読書エリアの入口感を作る半壁。中央側から入れる。")
	var reading_post := _create_prop(parent, "ReadingZoneSignPanel", "landmark", Vector3(-13.0, 0.0, 3.7), Vector3(0.24, 1.45, 1.25), Color(0.76, 0.62, 0.39))
	_tag_spatial_object(reading_post, "読書エリアの案内パネル", "reading_corner", ["読書サイン", "読書案内", "本のエリア"], "読書エリアの入口にある案内パネル。")

	var studio_back := _create_static_box(parent, "StudioZoneBackScreen", Vector3(16.2, 0.0, -6.2), Vector3(7.8, 1.35, 0.18), studio_wall_color)
	_tag_zone_partition(studio_back, "配信スタジオ奥のスクリーン仕切り", "studio", ["スタジオ仕切り", "配信エリアの壁", "青い仕切り"], "配信スタジオの奥を区切る青みのあるスクリーン。")
	var studio_north := _create_static_box(parent, "StudioZoneNorthSideScreen", Vector3(20.15, 0.0, -3.35), Vector3(0.18, 1.25, 5.2), studio_wall_color)
	_tag_zone_partition(studio_north, "配信スタジオ東側のスクリーン仕切り", "studio", ["スタジオ横仕切り", "配信エリアの横壁"], "配信スタジオの横を区切るスクリーン。中央側は開いている。")
	var studio_sign := _create_prop(parent, "StudioZoneSignPanel", "landmark", Vector3(12.2, 0.0, -5.45), Vector3(0.24, 1.45, 1.25), Color(0.38, 0.50, 0.68))
	_tag_spatial_object(studio_sign, "配信スタジオの案内パネル", "studio", ["スタジオサイン", "配信サイン", "作業エリア"], "配信スタジオの入口にある案内パネル。")

	var lounge_back := _create_static_box(parent, "RearLoungeBackHalfWall", Vector3(0.0, 0.0, 20.8), Vector3(10.0, 1.05, 0.18), lounge_wall_color)
	_tag_zone_partition(lounge_back, "奥ラウンジ背面の半壁", "rear_lounge", ["奥ラウンジの壁", "ソファ後ろの仕切り"], "奥ラウンジを落ち着いた小部屋のように見せる背面の半壁。")
	var lounge_left := _create_static_box(parent, "RearLoungeLeftHalfWall", Vector3(-5.2, 0.0, 17.4), Vector3(0.18, 1.0, 5.8), lounge_wall_color)
	_tag_zone_partition(lounge_left, "奥ラウンジ左側の半壁", "rear_lounge", ["奥ラウンジ左仕切り", "ソファ横の仕切り"], "奥ラウンジの左側境界。中央から出入りできる。")
	var lounge_right := _create_static_box(parent, "RearLoungeRightHalfWall", Vector3(5.2, 0.0, 17.4), Vector3(0.18, 1.0, 5.8), lounge_wall_color)
	_tag_zone_partition(lounge_right, "奥ラウンジ右側の半壁", "rear_lounge", ["奥ラウンジ右仕切り", "ソファ横の仕切り"], "奥ラウンジの右側境界。中央から出入りできる。")
	var lounge_sign := _create_prop(parent, "RearLoungeZoneSignPanel", "landmark", Vector3(4.35, 0.0, 13.25), Vector3(1.3, 1.35, 0.22), Color(0.57, 0.44, 0.60))
	_tag_spatial_object(lounge_sign, "奥ラウンジの案内パネル", "rear_lounge", ["奥ラウンジサイン", "ソファエリアの案内"], "奥ラウンジの入口にある案内パネル。")

	var gallery_left := _create_static_box(parent, "FrontGalleryDisplayWallLeft", Vector3(-6.9, 0.0, -18.55), Vector3(4.4, 1.55, 0.20), gallery_wall_color)
	_tag_zone_partition(gallery_left, "前方ギャラリー左の展示壁", "front_gallery", ["ギャラリー壁", "展示壁", "前方展示"], "前方ギャラリーを区切る展示用の壁。中央通路は開いている。")
	var gallery_right := _create_static_box(parent, "FrontGalleryDisplayWallRight", Vector3(6.9, 0.0, -18.55), Vector3(4.4, 1.55, 0.20), gallery_wall_color)
	_tag_zone_partition(gallery_right, "前方ギャラリー右の展示壁", "front_gallery", ["ギャラリー壁", "展示壁", "前方展示"], "前方ギャラリーを区切る展示用の壁。中央通路は開いている。")
	var gallery_sign := _create_prop(parent, "FrontGalleryZoneSignPanel", "landmark", Vector3(0.0, 0.0, -15.25), Vector3(1.6, 1.35, 0.22), Color(0.78, 0.72, 0.60))
	_tag_spatial_object(gallery_sign, "前方ギャラリーの案内パネル", "front_gallery", ["ギャラリーサイン", "展示エリアの案内"], "前方ギャラリーの入口にある案内パネル。")

	var terrace_west := _create_static_box(parent, "NorthTerracePlanterRailWest", Vector3(-7.4, 0.0, 23.2), Vector3(5.0, 0.70, 0.26), terrace_wall_color)
	_tag_zone_partition(terrace_west, "北テラス西側の植栽レール", "north_terrace", ["テラス仕切り", "植栽レール", "北側の緑"], "北テラスを区切る低い植栽レール。")
	var terrace_east := _create_static_box(parent, "NorthTerracePlanterRailEast", Vector3(7.4, 0.0, 23.2), Vector3(5.0, 0.70, 0.26), terrace_wall_color)
	_tag_zone_partition(terrace_east, "北テラス東側の植栽レール", "north_terrace", ["テラス仕切り", "植栽レール", "北側の緑"], "北テラスを区切る低い植栽レール。")
	var terrace_sign := _create_prop(parent, "NorthTerraceZoneSignPanel", "landmark", Vector3(0.0, 0.0, 21.8), Vector3(1.55, 1.25, 0.20), Color(0.48, 0.68, 0.48))
	_tag_spatial_object(terrace_sign, "北テラスの案内パネル", "north_terrace", ["テラスサイン", "北側エリアの案内"], "北テラスの入口にある案内パネル。")

	var west_memory := _create_static_box(parent, "WestMemoryAlcoveLowRail", Vector3(-23.0, 0.0, 7.8), Vector3(0.24, 0.92, 6.6), Color(0.46, 0.40, 0.34, 0.96))
	_tag_zone_partition(west_memory, "西側メモリーアルコーブの低い仕切り", "west_memory", ["西側仕切り", "写真パネルの仕切り", "メモリーエリア"], "西側の写真パネル周辺を小さなアルコーブに見せる仕切り。")
	var east_tool := _create_static_box(parent, "EastToolAlcoveLowRail", Vector3(23.0, 0.0, -7.8), Vector3(0.24, 0.92, 6.6), Color(0.39, 0.46, 0.52, 0.96))
	_tag_zone_partition(east_tool, "東側ツールアルコーブの低い仕切り", "east_tools", ["東側仕切り", "道具棚の仕切り", "ツールエリア"], "東側の道具棚周辺を小さなアルコーブに見せる仕切り。")


func _create_final_life_zoning_layer(parent: Node) -> void:
	# Final life layer: strengthen the 50m world as a readable living/exploration space
	# without blocking the already-verified walk targets. Objects here are mostly visual
	# and grounding-friendly landmarks.
	_create_static_box(parent, "FinalNorthSouthWarmRunner", Vector3(0.0, 0.043, 0.0), Vector3(1.28, 0.008, 42.0), Color(0.62, 0.55, 0.43, 0.82))
	_create_static_box(parent, "FinalEastWestWarmRunner", Vector3(0.0, 0.045, 0.0), Vector3(42.0, 0.008, 1.28), Color(0.62, 0.55, 0.43, 0.82))
	_create_static_box(parent, "FinalCenterConversationRingNorth", Vector3(0.0, 0.052, 3.58), Vector3(5.8, 0.010, 0.34), Color(0.86, 0.68, 0.42, 0.86))
	_create_static_box(parent, "FinalCenterConversationRingSouth", Vector3(0.0, 0.052, -3.58), Vector3(5.8, 0.010, 0.34), Color(0.86, 0.68, 0.42, 0.86))
	_create_static_box(parent, "FinalCenterConversationRingWest", Vector3(-3.58, 0.052, 0.0), Vector3(0.34, 0.010, 5.8), Color(0.86, 0.68, 0.42, 0.86))
	_create_static_box(parent, "FinalCenterConversationRingEast", Vector3(3.58, 0.052, 0.0), Vector3(0.34, 0.010, 5.8), Color(0.86, 0.68, 0.42, 0.86))

	var lumina_chair := _create_prop(parent, "CenterLuminaChairMarker", "chair", Vector3(0.0, 0.0, -1.95), Vector3(0.74, 0.58, 0.74), Color(0.58, 0.50, 0.42))
	lumina_chair.rotation_degrees.y = 180.0
	_tag_spatial_object(lumina_chair, "Luminaの中央席", "center_home", ["Lumina席", "ルミナの席", "中央席", "center lumina chair"], "中央会話エリアでLuminaが座る想定の椅子。")
	var guest_cushion_a := _create_prop(parent, "CenterGuestCushionLeftMarker", "chair", Vector3(-1.25, 0.0, -1.35), Vector3(0.72, 0.12, 0.58), Color(0.62, 0.38, 0.32))
	_tag_spatial_object(guest_cushion_a, "中央左クッション席", "center_home", ["左クッション", "ゲストクッション", "クッション席"], "中央会話エリアの左側にある低いクッション席。")
	var guest_cushion_b := _create_prop(parent, "CenterGuestCushionRightMarker", "chair", Vector3(1.25, 0.0, -1.35), Vector3(0.72, 0.12, 0.58), Color(0.62, 0.38, 0.32))
	_tag_spatial_object(guest_cushion_b, "中央右クッション席", "center_home", ["右クッション", "ゲストクッション", "クッション席"], "中央会話エリアの右側にある低いクッション席。")
	var center_sign := _create_prop(parent, "CenterConversationZoneSignPanel", "sign", Vector3(-3.85, 0.0, 2.95), Vector3(0.18, 1.08, 1.18), Color(0.78, 0.62, 0.38))
	center_sign.rotation_degrees.y = 90.0
	_tag_spatial_object(center_sign, "中央会話スペース案内パネル", "center_home", ["中央会話サイン", "会話スペース", "中央ラウンジ案内"], "中央の会話スペースを示す案内パネル。")

	var entry_arch := Node3D.new()
	entry_arch.name = "SouthEntryWelcomeArchMarker"
	entry_arch.position = Vector3(0.0, 0.0, -22.35)
	parent.add_child(entry_arch)
	_add_box_part(entry_arch, "LeftPost", Vector3(-1.7, 1.05, 0.0), Vector3(0.22, 2.1, 0.22), Color(0.55, 0.42, 0.30), 0.82)
	_add_box_part(entry_arch, "RightPost", Vector3(1.7, 1.05, 0.0), Vector3(0.22, 2.1, 0.22), Color(0.55, 0.42, 0.30), 0.82)
	_add_box_part(entry_arch, "TopBeam", Vector3(0.0, 2.10, 0.0), Vector3(3.65, 0.24, 0.24), Color(0.62, 0.48, 0.34), 0.82)
	_add_box_part(entry_arch, "WarmNamePlate", Vector3(0.0, 1.62, -0.13), Vector3(1.55, 0.46, 0.045), Color(0.86, 0.72, 0.44), 0.86)
	_tag_spatial_object(entry_arch, "南入口ウェルカムアーチ", "south_entry", ["入口アーチ", "ウェルカムアーチ", "南入口", "玄関"], "50m空間の南側入口を示す門型の目印。")
	_create_static_box(parent, "SouthEntryLongWelcomeMat", Vector3(0.0, 0.054, -20.65), Vector3(3.9, 0.010, 1.30), Color(0.42, 0.28, 0.22, 0.92))
	var entry_map := _create_prop(parent, "SouthEntryWorldMapBoardMarker", "sign", Vector3(2.85, 0.0, -20.2), Vector3(0.18, 1.25, 1.05), Color(0.36, 0.50, 0.54))
	entry_map.rotation_degrees.y = -72.0
	_tag_spatial_object(entry_map, "南入口ワールド地図ボード", "south_entry", ["入口地図", "ワールド地図", "フロアマップ", "入口マップ"], "入口から各区画の方向を確認するための地図ボード。")
	var umbrella_stand := _create_prop(parent, "SouthEntryUmbrellaStandMarker", "small_item", Vector3(-2.75, 0.0, -20.35), Vector3(0.34, 0.84, 0.34), Color(0.28, 0.31, 0.34))
	_tag_spatial_object(umbrella_stand, "南入口傘立て", "south_entry", ["傘立て", "アンブレラスタンド", "入口の傘"], "南入口に置いた細い傘立て。")

	_create_static_box(parent, "NorthPanoramaWindowWide", Vector3(0.0, 1.54, 24.76), Vector3(9.6, 1.52, 0.045), Color(0.38, 0.56, 0.68, 0.72))
	_create_static_box(parent, "NorthPanoramaCurtainLeft", Vector3(-5.35, 1.38, 24.70), Vector3(0.44, 1.82, 0.055), Color(0.73, 0.70, 0.62, 0.70))
	_create_static_box(parent, "NorthPanoramaCurtainRight", Vector3(5.35, 1.38, 24.70), Vector3(0.44, 1.82, 0.055), Color(0.73, 0.70, 0.62, 0.70))
	_create_static_box(parent, "NorthTerraceSunPatch", Vector3(0.0, 0.056, 19.65), Vector3(5.2, 0.009, 2.2), Color(0.86, 0.74, 0.48, 0.42))
	var daybed := _create_prop(parent, "NorthTerraceDaybedMarker", "sofa", Vector3(3.85, 0.0, 19.95), Vector3(1.85, 0.44, 0.72), Color(0.42, 0.54, 0.50))
	daybed.rotation_degrees.y = -10.0
	_tag_spatial_object(daybed, "北テラスのデイベッド", "north_terrace", ["デイベッド", "北テラスベッド", "窓辺ベッド", "横になれるベンチ"], "北テラスの窓辺に置いた低いデイベッド。")
	var telescope := _create_prop(parent, "NorthTerraceTelescopeMarker", "landmark", Vector3(-4.10, 0.0, 20.05), Vector3(0.28, 1.10, 0.28), Color(0.24, 0.28, 0.32))
	_tag_spatial_object(telescope, "北テラス望遠鏡", "north_terrace", ["望遠鏡", "テレスコープ", "北テラスの望遠鏡"], "北テラスの窓辺から外を見るための望遠鏡。")

	var storage_locker := _create_prop(parent, "EastStorageLockerMarker", "shelf", Vector3(22.55, 0.0, -13.35), Vector3(0.74, 1.85, 1.36), Color(0.34, 0.38, 0.40))
	storage_locker.rotation_degrees.y = 90.0
	_tag_spatial_object(storage_locker, "東倉庫ロッカー", "east_tool", ["倉庫ロッカー", "備品ロッカー", "東ロッカー", "ロッカー"], "東ツールエリアの奥にある備品用ロッカー。")
	var storage_boxes := _create_prop(parent, "EastStorageBoxStackMarker", "small_item", Vector3(21.3, 0.0, -12.55), Vector3(0.72, 0.62, 0.78), Color(0.55, 0.46, 0.34))
	_tag_spatial_object(storage_boxes, "東倉庫の箱", "east_tool", ["倉庫箱", "備品箱", "箱", "収納箱"], "東倉庫ロッカーの近くに積んだ備品箱。")
	var step_ladder := _create_prop(parent, "EastStorageStepLadderMarker", "landmark", Vector3(20.5, 0.0, -14.25), Vector3(0.56, 1.10, 0.30), Color(0.56, 0.52, 0.40))
	step_ladder.rotation_degrees.y = -12.0
	_tag_spatial_object(step_ladder, "東倉庫の脚立", "east_tool", ["脚立", "はしご", "ステップラダー", "倉庫の脚立"], "東倉庫側に立てかけた小さな脚立。")
	var broom := _create_prop(parent, "EastStorageBroomMarker", "small_item", Vector3(22.9, 0.0, -11.6), Vector3(0.18, 1.15, 0.18), Color(0.62, 0.48, 0.26))
	_tag_spatial_object(broom, "東倉庫の掃除道具", "east_tool", ["掃除道具", "ほうき", "モップ", "掃除"], "東倉庫側の壁際に置いた掃除道具。")

	var nw_globe := _create_prop(parent, "NorthWestGlobeMarker", "landmark", Vector3(-22.1, 0.0, 21.0), Vector3(0.48, 0.78, 0.48), Color(0.26, 0.44, 0.70))
	_tag_spatial_object(nw_globe, "北西の地球儀", "north_west_corner", ["地球儀", "北西地球儀", "青い地球儀"], "北西角の位置を示す地球儀風の目印。")
	var ne_blue_tree := _create_prop(parent, "NorthEastBlueTreeMarker", "plant", Vector3(22.0, 0.0, 21.1), Vector3(0.58, 1.45, 0.58), Color(0.20, 0.42, 0.62))
	_tag_spatial_object(ne_blue_tree, "北東の青い木", "north_east_corner", ["青い木", "北東の木", "青い植物"], "北東角の位置を示す青みのある木。")
	var sw_banner := _create_prop(parent, "SouthWestRelaxBannerMarker", "sign", Vector3(-22.2, 0.0, -21.2), Vector3(0.20, 1.35, 1.10), Color(0.68, 0.38, 0.36))
	sw_banner.rotation_degrees.y = 90.0
	_tag_spatial_object(sw_banner, "南西くつろぎバナー", "south_west_corner", ["くつろぎバナー", "南西バナー", "赤いバナー"], "南西角にあるくつろぎエリアの布バナー。")
	var se_locker_sign := _create_prop(parent, "SouthEastStorageSignMarker", "sign", Vector3(22.1, 0.0, -21.1), Vector3(0.20, 1.20, 1.00), Color(0.68, 0.58, 0.30))
	se_locker_sign.rotation_degrees.y = -90.0
	_tag_spatial_object(se_locker_sign, "南東備品サイン", "south_east_corner", ["備品サイン", "南東サイン", "倉庫サイン"], "南東角の備品・倉庫方向を示すサイン。")


func _create_clean_50m_completion_layer(parent: Node) -> void:
	# Keep this layer sparse and semantic: each object is a readable landmark or
	# search target, not filler. Names are intentionally aligned with Object Grounding.
	var room_map := _create_prop(parent, "CenterRoomMapMarker", "sign", Vector3(2.45, 0.18, -1.85), Vector3(0.82, 0.36, 0.18), Color(0.30, 0.42, 0.58))
	room_map.rotation_degrees.y = -18.0
	_tag_spatial_object(room_map, "部屋の案内図", "center_home", ["map", "案内図", "地図", "部屋の地図", "room map"], "50m空間の区画を示す中央の案内図。")
	var center_cushion := _create_prop(parent, "CenterFloorCushionMarker", "soft_object", Vector3(0.95, 0.02, -1.35), Vector3(0.82, 0.08, 0.62), Color(0.52, 0.36, 0.31))
	_tag_spatial_object(center_cushion, "中央クッション", "center_home", ["cushion", "クッション", "中央クッション", "座布団"], "中央ホームエリアの床に置いた低いクッション。")

	var window_bench := _create_prop(parent, "WindowBlueBenchMarker", "bench", Vector3(-22.0, 0.0, -6.4), Vector3(1.60, 0.45, 0.58), Color(0.24, 0.42, 0.66))
	window_bench.rotation_degrees.y = 90.0
	_tag_spatial_object(window_bench, "窓辺の青いベンチ", "window_area", ["blue bench", "青いベンチ", "窓辺のベンチ", "window bench"], "窓辺の光パネル近くにある青いベンチ。")
	var window_light_panel := _create_prop(parent, "WindowLightPanelMarker", "landmark", Vector3(-24.55, 0.36, -6.4), Vector3(0.06, 1.45, 1.60), Color(0.72, 0.80, 0.82))
	_tag_spatial_object(window_light_panel, "窓辺の光パネル", "window_area", ["light panel", "光パネル", "窓辺の光", "明るい板"], "窓辺の位置を示す明るい縦長パネル。")

	var reading_tea := _create_prop(parent, "ReadingTeaTrayMarker", "small_item", Vector3(-17.55, 0.42, -0.95), Vector3(0.48, 0.08, 0.34), Color(0.50, 0.36, 0.25))
	_tag_spatial_object(reading_tea, "読書トレイ", "reading_corner", ["tray", "トレイ", "お茶トレイ", "読書トレイ"], "読書テーブルの端に置いた小さなトレイ。")

	var studio_mic := _create_prop(parent, "StudioMicrophoneMarker", "small_item", Vector3(18.65, 0.70, -3.95), Vector3(0.12, 0.76, 0.12), Color(0.08, 0.09, 0.10))
	_tag_spatial_object(studio_mic, "スタジオマイク", "studio_corner", ["microphone", "mic", "マイク", "スタジオマイク"], "スタジオ机の近くに立つ細いマイク。")
	var studio_cable := _create_prop(parent, "StudioCableReelMarker", "small_item", Vector3(18.95, 0.03, -2.35), Vector3(0.48, 0.10, 0.48), Color(0.16, 0.16, 0.17))
	_tag_spatial_object(studio_cable, "スタジオケーブルリール", "studio_corner", ["cable reel", "ケーブル", "ケーブルリール", "コード"], "スタジオ足元にある丸いケーブルリール。")

	var rear_blanket := _create_prop(parent, "RearBlanketMarker", "soft_object", Vector3(-4.65, 0.48, 18.0), Vector3(1.05, 0.08, 0.54), Color(0.50, 0.34, 0.42))
	rear_blanket.rotation_degrees.y = 26.0
	_tag_spatial_object(rear_blanket, "奥ラウンジブランケット", "rear_lounge", ["blanket", "ブランケット", "毛布", "奥の布"], "奥ラウンジ左ソファにかけた小さな布。")
	var rear_low_shelf := _create_prop(parent, "RearLowShelfMarker", "shelf", Vector3(7.1, 0.0, 19.8), Vector3(1.35, 0.52, 0.42), Color(0.34, 0.25, 0.19))
	rear_low_shelf.rotation_degrees.y = -18.0
	_tag_spatial_object(rear_low_shelf, "奥ラウンジ低棚", "rear_lounge", ["low shelf", "低棚", "奥の棚", "ラウンジ棚"], "奥ラウンジの壁寄りに置いた低い棚。")
	var rear_left_plant := _create_prop(parent, "RearLoungeLeftPlant", "plant", Vector3(-7.9, 0.0, 20.2), Vector3(0.48, 1.05, 0.48), Color(0.18, 0.42, 0.28))
	_tag_spatial_object(rear_left_plant, "奥ラウンジ左の植物", "rear_lounge", ["rear plant", "奥の植物", "植物"], "奥ラウンジ左側の植物。")
	var rear_right_plant := _create_prop(parent, "RearLoungeRightPlant", "plant", Vector3(7.9, 0.0, 20.2), Vector3(0.48, 1.05, 0.48), Color(0.18, 0.42, 0.28))
	_tag_spatial_object(rear_right_plant, "奥ラウンジ右の植物", "rear_lounge", ["rear plant", "奥の植物", "植物"], "奥ラウンジ右側の植物。")

	var gallery_sign := _create_prop(parent, "GalleryGuideSignMarker", "sign", Vector3(-6.4, 0.28, -17.4), Vector3(0.72, 0.56, 0.12), Color(0.54, 0.46, 0.34))
	_tag_spatial_object(gallery_sign, "ギャラリー案内サイン", "front_gallery", ["gallery sign", "ギャラリー案内", "展示案内", "案内サイン"], "前ギャラリーの展示案内サイン。")
	var gallery_sculpture := _create_prop(parent, "GallerySmallSculptureMarker", "art", Vector3(3.95, 0.70, -18.45), Vector3(0.36, 0.54, 0.36), Color(0.64, 0.61, 0.55))
	_tag_spatial_object(gallery_sculpture, "小さな展示彫刻", "front_gallery", ["sculpture", "彫刻", "小さな彫刻", "展示彫刻"], "前ギャラリーの台に置いた小さな展示彫刻。")
	var gallery_left_plant := _create_prop(parent, "GalleryLeftPlant", "plant", Vector3(-7.8, 0.0, -15.9), Vector3(0.46, 1.00, 0.46), Color(0.18, 0.40, 0.27))
	_tag_spatial_object(gallery_left_plant, "ギャラリー左の植物", "front_gallery", ["gallery plant", "植物", "ギャラリー植物"], "前ギャラリー左側の植物。")
	var gallery_right_plant := _create_prop(parent, "GalleryRightPlant", "plant", Vector3(7.8, 0.0, -15.9), Vector3(0.46, 1.00, 0.46), Color(0.18, 0.40, 0.27))
	_tag_spatial_object(gallery_right_plant, "ギャラリー右の植物", "front_gallery", ["gallery plant", "植物", "ギャラリー植物"], "前ギャラリー右側の植物。")

	var north_table := _create_prop(parent, "NorthTerraceRoundTable", "table", Vector3(-16.9, 0.0, 15.6), Vector3(0.78, 0.42, 0.68), Color(0.42, 0.31, 0.22))
	_tag_spatial_object(north_table, "北テラス丸テーブル", "north_terrace", ["terrace table", "north table", "北テーブル", "北テラス"], "北テラスの植物近くにある小さな丸テーブル。")
	var north_chair := _create_prop(parent, "NorthTerraceChair", "chair", Vector3(-15.45, 0.0, 14.75), Vector3(0.62, 0.50, 0.66), Color(0.32, 0.35, 0.36))
	north_chair.rotation_degrees.y = -42.0
	_tag_spatial_object(north_chair, "北テラスチェア", "north_terrace", ["terrace chair", "北テラス椅子", "椅子"], "北テラス丸テーブルの近くにある椅子。")
	var north_planter := _create_prop(parent, "NorthTerracePlanterMarker", "plant", Vector3(-20.4, 0.0, 17.2), Vector3(0.84, 0.52, 0.46), Color(0.20, 0.46, 0.30))
	_tag_spatial_object(north_planter, "北テラスプランター", "north_terrace", ["planter", "プランター", "north terrace planter"], "北テラスにある低いプランター。")
	var north_lantern := _create_prop(parent, "NorthTerraceLanternMarker", "light", Vector3(-16.25, 0.44, 16.25), Vector3(0.26, 0.34, 0.26), Color(0.78, 0.60, 0.34))
	_tag_spatial_object(north_lantern, "北テラスランタン", "north_terrace", ["lantern", "ランタン", "北のランタン", "テラスランタン"], "北テラス丸テーブル近くの小さなランタン。")
	var north_tile := _create_prop(parent, "NorthTerraceTileMarker", "landmark", Vector3(-17.25, 0.018, 14.15), Vector3(1.05, 0.026, 0.72), Color(0.58, 0.56, 0.50))
	_tag_spatial_object(north_tile, "北テラス床タイル", "north_terrace", ["tile", "タイル", "床タイル", "北テラスのタイル"], "北テラスの足元にある床タイル目印。")

	var west_console := _create_prop(parent, "WestMemoryConsole", "console", Vector3(-22.2, 0.0, 8.35), Vector3(0.58, 0.62, 1.55), Color(0.36, 0.27, 0.20))
	west_console.rotation_degrees.y = 90.0
	_tag_spatial_object(west_console, "西メモリーコンソール", "west_memory", ["memory console", "west console", "西コンソール", "西側棚"], "西側の壁沿いにある細長いコンソール棚。")
	var west_plant := _create_prop(parent, "WestMemoryPlant", "plant", Vector3(-21.8, 0.0, 10.55), Vector3(0.46, 1.05, 0.46), Color(0.18, 0.42, 0.28))
	_tag_spatial_object(west_plant, "西メモリーの植物", "west_memory", ["memory plant", "西の植物", "植物"], "西メモリー区画にある植物。")
	var west_album := _create_prop(parent, "WestMemoryAlbumMarker", "small_item", Vector3(-22.18, 0.64, 8.72), Vector3(0.34, 0.08, 0.44), Color(0.48, 0.36, 0.28))
	_tag_spatial_object(west_album, "西メモリーアルバム", "west_memory", ["album", "アルバム", "写真アルバム", "メモリーアルバム"], "西メモリーコンソールの上に置いたアルバム。")
	var west_lamp := _create_prop(parent, "WestMemoryLampMarker", "light", Vector3(-22.18, 0.64, 7.82), Vector3(0.24, 0.32, 0.24), Color(0.70, 0.56, 0.36))
	_tag_spatial_object(west_lamp, "西メモリー小ランプ", "west_memory", ["small lamp", "小ランプ", "メモリーランプ", "西のランプ"], "西メモリーコンソールの小さな灯り。")

	var east_stool := _create_prop(parent, "EastToolStool", "stool", Vector3(20.75, 0.0, 7.0), Vector3(0.46, 0.42, 0.46), Color(0.34, 0.33, 0.30))
	_tag_spatial_object(east_stool, "東ツールスツール", "east_tool", ["stool", "スツール", "東の椅子"], "東ツール棚の近くにある小さなスツール。")
	var east_boxes := _create_prop(parent, "EastToolBoxStackMarker", "small_item", Vector3(21.45, 0.0, 9.55), Vector3(0.62, 0.42, 0.52), Color(0.42, 0.33, 0.24))
	_tag_spatial_object(east_boxes, "東ツール箱", "east_tool", ["tool box", "box stack", "道具箱", "箱"], "東ツール棚のそばに積んだ道具箱。")
	var east_manual := _create_prop(parent, "EastToolManualMarker", "small_item", Vector3(22.02, 0.74, 7.25), Vector3(0.36, 0.06, 0.48), Color(0.68, 0.62, 0.48))
	_tag_spatial_object(east_manual, "東ツール説明書", "east_tool", ["manual", "説明書", "ツール説明書", "手順書"], "東ツール棚の上にある薄い説明書。")
	var east_meter := _create_prop(parent, "EastToolMeterMarker", "small_item", Vector3(22.02, 0.78, 8.22), Vector3(0.30, 0.22, 0.18), Color(0.22, 0.26, 0.28))
	_tag_spatial_object(east_meter, "東ツールメーター", "east_tool", ["meter", "メーター", "計器", "ツールメーター"], "東ツール棚の上にある小さな計器。")

	var entry_side_table := _create_prop(parent, "SouthEntrySideTable", "table", Vector3(-16.2, 0.0, -13.05), Vector3(0.56, 0.42, 0.50), Color(0.40, 0.30, 0.22))
	_tag_spatial_object(entry_side_table, "南入口サイドテーブル", "south_entry", ["entry side table", "入口テーブル", "サイドテーブル"], "南入口ベンチの横にある小さなテーブル。")
	var entry_lamp := _create_prop(parent, "SouthEntryLamp", "light", Vector3(-16.2, 0.42, -13.05), Vector3(0.24, 0.42, 0.24), Color(0.72, 0.56, 0.34))
	_tag_spatial_object(entry_lamp, "南入口ランプ", "south_entry", ["entry lamp", "入口ランプ", "ランプ"], "南入口サイドテーブルの上にある小さなランプ。")
	var entry_coat := _create_prop(parent, "SouthEntryCoatStandMarker", "landmark", Vector3(-13.1, 0.0, -14.8), Vector3(0.30, 1.35, 0.30), Color(0.28, 0.22, 0.18))
	_tag_spatial_object(entry_coat, "南入口コートスタンド", "south_entry", ["coat stand", "コートスタンド", "入口のスタンド", "ハンガー"], "南入口側の細いコートスタンド。")
	var entry_mat := _create_prop(parent, "SouthEntryFloorMatMarker", "landmark", Vector3(-14.65, 0.018, -11.6), Vector3(1.45, 0.026, 0.72), Color(0.42, 0.37, 0.34))
	_tag_spatial_object(entry_mat, "南入口マット", "south_entry", ["floor mat", "マット", "入口マット", "玄関マット"], "南入口の足元にあるマット。")

	var nw_corner := _create_prop(parent, "NorthWestCornerBluePillarMarker", "landmark", Vector3(-23.45, 0.0, 23.45), Vector3(0.50, 1.95, 0.50), Color(0.18, 0.35, 0.70))
	_tag_spatial_object(nw_corner, "北西の青い柱", "north_west_corner", ["blue pillar", "青い柱", "北西の柱", "北西コーナー"], "50m空間の北西角を示す青い柱。")
	var ne_corner := _create_prop(parent, "NorthEastCornerGreenPillarMarker", "landmark", Vector3(23.45, 0.0, 23.45), Vector3(0.50, 1.95, 0.50), Color(0.22, 0.55, 0.30))
	_tag_spatial_object(ne_corner, "北東の緑の柱", "north_east_corner", ["green pillar", "緑の柱", "北東の柱", "北東コーナー"], "50m空間の北東角を示す緑の柱。")
	var sw_corner := _create_prop(parent, "SouthWestCornerRedPillarMarker", "landmark", Vector3(-23.45, 0.0, -23.45), Vector3(0.50, 1.95, 0.50), Color(0.68, 0.24, 0.20))
	_tag_spatial_object(sw_corner, "南西の赤い柱", "south_west_corner", ["red pillar", "赤い柱", "南西の柱", "南西コーナー"], "50m空間の南西角を示す赤い柱。")
	var se_corner := _create_prop(parent, "SouthEastCornerYellowPillarMarker", "landmark", Vector3(23.45, 0.0, -23.45), Vector3(0.50, 1.95, 0.50), Color(0.76, 0.62, 0.18))
	_tag_spatial_object(se_corner, "南東の黄色い柱", "south_east_corner", ["yellow pillar", "黄色い柱", "南東の柱", "南東コーナー"], "50m空間の南東角を示す黄色い柱。")


func _create_navigation_region() -> void:
	_navigation_region = NavigationRegion3D.new()
	_navigation_region.name = "NavigationRegion3D"
	var nav_mesh := NavigationMesh.new()
	var half_x := FLOOR_SIZE.x * 0.5 + NAVIGATION_EDGE_PADDING
	var half_z := FLOOR_SIZE.y * 0.5 + NAVIGATION_EDGE_PADDING
	if _future_room_v008_as_active_room_enabled() and not _future_room_v008_room_only_preserve_legacy_environment():
		half_x = FUTURE_ROOM_V008_HALF_X
		half_z = FUTURE_ROOM_V008_HALF_Z
	nav_mesh.set_vertices(PackedVector3Array([
		Vector3(-half_x, 0.02, -half_z),
		Vector3(half_x, 0.02, -half_z),
		Vector3(half_x, 0.02, half_z),
		Vector3(-half_x, 0.02, half_z),
	]))
	nav_mesh.add_polygon(PackedInt32Array([0, 1, 2, 3]))
	_navigation_region.navigation_mesh = nav_mesh
	add_child(_navigation_region)


func _setup_environment() -> void:
	_setup_render_quality()
	_world_environment = WorldEnvironment.new()
	_world_environment.name = "WorldEnvironment"
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color(0.50, 0.55, 0.57)
	environment.ambient_light_color = Color(0.86, 0.83, 0.76)
	environment.ambient_light_energy = 0.34
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	_set_object_property_if_present(environment, "tonemap_mode", 3)
	_set_object_property_if_present(environment, "tonemap_exposure", 0.82)
	_set_object_property_if_present(environment, "tonemap_white", 1.18)
	_set_object_property_if_present(environment, "ssao_enabled", true)
	_set_object_property_if_present(environment, "ssao_radius", 1.55)
	_set_object_property_if_present(environment, "ssao_intensity", 1.35)
	_set_object_property_if_present(environment, "ssao_power", 1.35)
	_set_object_property_if_present(environment, "ssao_detail", 0.55)
	_set_object_property_if_present(environment, "adjustment_enabled", true)
	_set_object_property_if_present(environment, "adjustment_brightness", 1.0)
	_set_object_property_if_present(environment, "adjustment_contrast", 1.04)
	_set_object_property_if_present(environment, "adjustment_saturation", 1.0)
	_world_environment.environment = environment
	add_child(_world_environment)

	var light := DirectionalLight3D.new()
	light.name = "Sun"
	light.rotation_degrees = Vector3(-46.0, 22.0, 0.0)
	light.light_color = Color(1.0, 0.95, 0.86)
	light.light_energy = 0.82
	_set_object_property_if_present(light, "shadow_enabled", true)
	_set_object_property_if_present(light, "light_angular_distance", 0.38)
	_set_object_property_if_present(light, "shadow_bias", 0.035)
	_set_object_property_if_present(light, "shadow_normal_bias", 1.1)
	_set_object_property_if_present(light, "directional_shadow_mode", 2)
	_set_object_property_if_present(light, "directional_shadow_blend_splits", true)
	add_child(light)

	var fill_light := OmniLight3D.new()
	fill_light.name = "StageFillLight"
	fill_light.position = Vector3(-1.35, 2.1, -2.15)
	fill_light.light_color = STAGE_WARM_LIGHT_COLOR
	fill_light.light_energy = 0.30
	fill_light.omni_range = 5.4
	_set_object_property_if_present(fill_light, "shadow_enabled", true)
	_set_object_property_if_present(fill_light, "shadow_bias", 0.05)
	add_child(fill_light)

	var face_light := OmniLight3D.new()
	face_light.name = "FaceSoftLight"
	face_light.position = Vector3(-0.55, 1.8, -2.35)
	face_light.light_color = Color(1.0, 0.93, 0.84)
	face_light.light_energy = 0.24
	face_light.omni_range = 3.4
	add_child(face_light)

	var rim_light := OmniLight3D.new()
	rim_light.name = "BackRimLight"
	rim_light.position = Vector3(0.0, 2.35, 2.65)
	rim_light.light_color = Color(0.74, 0.86, 1.0)
	rim_light.light_energy = 0.22
	rim_light.omni_range = 4.0
	add_child(rim_light)


func _setup_render_quality() -> void:
	var viewport := get_viewport()
	_set_object_property_if_present(viewport, "msaa_3d", 2)


func _setup_runtime_window() -> void:
	if DisplayServer.get_name().to_lower() == "headless":
		return
	DisplayServer.window_set_title("Lumina")
	DisplayServer.window_set_size(DEFAULT_GAME_WINDOW_SIZE)
	var screen := DisplayServer.window_get_current_screen()
	var screen_position := DisplayServer.screen_get_position(screen)
	var screen_size := DisplayServer.screen_get_size(screen)
	var centered_offset := Vector2i(
		maxi(0, int((screen_size.x - DEFAULT_GAME_WINDOW_SIZE.x) * 0.5)),
		maxi(0, int((screen_size.y - DEFAULT_GAME_WINDOW_SIZE.y) * 0.5))
	)
	DisplayServer.window_set_position(screen_position + centered_offset)


func _set_object_property_if_present(target: Object, property_name: String, value) -> bool:
	if target == null:
		return false
	for property_info in target.get_property_list():
		if String(property_info.get("name", "")) == property_name:
			target.set(property_name, value)
			return true
	return false


func _setup_camera(target: Node3D) -> void:
	var camera := Camera3D.new()
	camera.name = "Camera3D"
	camera.set_script(FollowCameraScript)
	camera.position = Vector3(0.12, 1.58, -3.2)
	camera.current = true
	camera.set("target_path", target.get_path())
	if _avatar:
		camera.set("avatar_target_path", _avatar.get_path())
	camera.add_to_group("si_camera_controller")
	camera.set("target_height", 1.22)
	camera.set("distance", 3.65)
	camera.set("height", 1.42)
	camera.set("side_offset", 0.18)
	camera.set("front_bias", 1.0)
	camera.set("start_in_first_person", false)
	camera.set("startup_preset", "full_front")
	camera.set("first_person_eye_height", 1.50)
	camera.set("first_person_fov_deg", 62.0)
	camera.set("third_person_distance", 4.1)
	camera.set("third_person_height", 1.55)
	camera.set("third_person_pitch_deg", 12.0)
	camera.set("third_person_side_offset", 0.0)
	camera.set("third_person_yaw_offset_deg", 0.0)
	camera.set("player_mouse_look_enabled", true)
	camera.set("player_mouse_yaw_speed", 0.14)
	camera.set("player_mouse_pitch_speed", 0.10)
	if _is_route_b_candidate_preview():
		camera.near = 0.05
		camera.set("min_distance", 0.95)
		camera.set("camera_collision_min_distance", 0.95)
		camera.set("startup_preset", "full_front")
		print("[DemoMain] route_b candidate camera: full_front + near=0.05 min_distance=0.95")
	var camera_half_floor := FLOOR_SIZE * 0.5
	if _future_room_v008_as_active_room_enabled() and not _future_room_v008_room_only_preserve_legacy_environment():
		camera_half_floor = Vector2(FUTURE_ROOM_V008_HALF_X, FUTURE_ROOM_V008_HALF_Z)
	var camera_edge_margin := 0.65
	var camera_height_limit := maxf(3.15, maxf(FLOOR_SIZE.x, FLOOR_SIZE.y) * 0.28)
	if _future_room_v008_as_active_room_enabled() and not _future_room_v008_room_only_preserve_legacy_environment():
		camera_height_limit = 4.2
	camera.set("max_distance", maxf(14.0, maxf(FLOOR_SIZE.x, FLOOR_SIZE.y) * 0.45))
	if _future_room_v008_as_active_room_enabled() and not _future_room_v008_room_only_preserve_legacy_environment():
		camera.set("max_distance", 7.5)
	camera.set("camera_bounds_enabled", true)
	camera.set("camera_min_x", -camera_half_floor.x + camera_edge_margin)
	camera.set("camera_max_x", camera_half_floor.x - camera_edge_margin)
	camera.set("camera_min_z", -camera_half_floor.y + camera_edge_margin)
	camera.set("camera_max_z", camera_half_floor.y - camera_edge_margin)
	camera.set("camera_min_y", 0.35)
	camera.set("camera_max_y", camera_height_limit)
	camera.set("camera_collision_enabled", true)
	add_child(camera)
	camera.look_at(target.global_position + Vector3.UP * 1.22, Vector3.UP)
	_camera = camera
	if _is_route_b_candidate_preview():
		call_deferred("_maybe_run_route_b_visual_capture")


func _create_lumina_eye_camera(target: Node3D) -> void:
	if not target:
		return
	var skeleton := _find_first_skeleton(target)
	if skeleton == null:
		push_warning("Lumina eye camera was not created because no Skeleton3D was found under the avatar.")
		return
	var left_eye_bone := _find_skeleton_bone_name(
		skeleton,
		["J_Adj_L_FaceEye", "leftEye", "LeftEye", "Left Eye", "eye.L", "Eye_L", "左目"]
	)
	var right_eye_bone := _find_skeleton_bone_name(
		skeleton,
		["J_Adj_R_FaceEye", "rightEye", "RightEye", "Right Eye", "eye.R", "Eye_R", "右目"]
	)
	if left_eye_bone.is_empty() and right_eye_bone.is_empty():
		# Route B / eye-less candidates: fall back to head bone instead of aborting.
		var head_bone := _find_skeleton_bone_name(
			skeleton,
			["Head", "head", "J_Bip_C_Head", "mixamorig:Head"]
		)
		if not head_bone.is_empty():
			_lumina_left_eye_camera = _create_lumina_eye_camera_for_bone(
				target,
				skeleton,
				head_bone,
				"LuminaHeadEyeFallbackAttachment",
				"LuminaHeadEyeFallbackCamera",
				"head_fallback"
			)
			_lumina_eye_camera = _lumina_left_eye_camera
			print("[LuminaEyeCamera] fallback head camera attached to bone=", head_bone)
			return
		if _is_route_b_candidate_preview():
			print("[LuminaEyeCamera] route_b candidate: eye camera disabled; using follow camera")
			return
		push_warning("Lumina eye camera was not created because leftEye/rightEye bones were not found.")
		return
	if not left_eye_bone.is_empty():
		_lumina_left_eye_camera = _create_lumina_eye_camera_for_bone(
			target,
			skeleton,
			left_eye_bone,
			"LuminaLeftEyeAttachment",
			"LuminaLeftEyeCamera",
			"left"
		)
		_lumina_eye_camera = _lumina_left_eye_camera
		print("[LuminaEyeCamera] left eye camera attached to bone=", left_eye_bone)
	if not right_eye_bone.is_empty():
		_lumina_right_eye_camera = _create_lumina_eye_camera_for_bone(
			target,
			skeleton,
			right_eye_bone,
			"LuminaRightEyeAttachment",
			"LuminaRightEyeCamera",
			"right"
		)
		print("[LuminaEyeCamera] right eye camera attached to bone=", right_eye_bone)
	if _lumina_eye_camera == null:
		_lumina_eye_camera = _lumina_right_eye_camera


func _is_route_b_candidate_preview() -> bool:
	# Detect env-only candidate paths without embedding the candidate folder literal
	# (validator forbids wiring that folder name into live demo defaults).
	var path := _lumina_vrm_scene_path().to_lower()
	return path.contains("route_b") and path.contains("candidate")


func _maybe_run_route_b_visual_capture() -> void:
	var enabled := OS.get_environment("LUMINA_ROUTE_B_VISUAL_CAPTURE").strip_edges().to_lower()
	if enabled not in ["1", "true", "yes", "on"]:
		return
	call_deferred("_run_route_b_visual_capture_sequence")


func _run_route_b_visual_capture_sequence() -> void:
	var out_dir := OS.get_environment("LUMINA_ROUTE_B_VISUAL_CAPTURE_DIR").strip_edges()
	if out_dir.is_empty():
		out_dir = ProjectSettings.globalize_path("res://../reports/avatar_route_b_visual_fix_20260718")
	DirAccess.make_dir_recursive_absolute(out_dir)
	var adapter := _avatar.get_node_or_null("SIVRMAvatarAdapter") if _avatar else null
	var shots := [
		{"name": "01_startup", "preset": "full_front", "expression": "neutral", "talk": false},
		{"name": "02_front", "preset": "full_front", "expression": "neutral", "talk": false},
		{"name": "03_closeup", "preset": "upper_front", "expression": "neutral", "talk": false},
		{"name": "04_talk", "preset": "upper_front", "expression": "neutral", "talk": true},
		{"name": "05_neutral", "preset": "full_front", "expression": "neutral", "talk": false},
		{"name": "06_expression_a", "preset": "upper_front", "expression": "a", "talk": false},
	]
	print("[RouteBVisualCapture] begin dir=", out_dir)
	# Route B visual faces +Z; yaw=0 for full_front face view.
	if adapter != null:
		var model_root: Node3D = adapter.get_node_or_null("AvatarModelRoot") as Node3D
		if model_root != null:
			model_root.rotation_degrees.y = 0.0
			adapter.set("model_yaw_degrees", 0.0)
			print("[RouteBVisualCapture] forced model_yaw=0 for front capture")
	for shot in shots:
		if _camera != null and _camera.has_method("_apply_preset_by_label"):
			_camera.call("_apply_preset_by_label", String(shot["preset"]))
		if adapter != null and adapter.has_method("set_expression"):
			adapter.call("set_expression", String(shot["expression"]), 1.0)
		if adapter != null and bool(shot["talk"]):
			# Drive mouth open via expression A as talk proxy when TTS is unavailable.
			adapter.call("set_expression", "a", 0.85)
		await get_tree().create_timer(0.55).timeout
		for _i in range(8):
			await get_tree().process_frame
		var image := get_viewport().get_texture().get_image()
		var path := "%s/%s.png" % [out_dir, String(shot["name"])]
		var err := image.save_png(path)
		var ok := err == OK
		# Reject empty/black failure captures as success evidence.
		var mean_luma := 0.0
		if ok and image != null and image.get_width() > 0:
			var sample_count := 0
			for y in range(0, image.get_height(), maxi(1, int(image.get_height() / 32))):
				for x in range(0, image.get_width(), maxi(1, int(image.get_width() / 32))):
					var c := image.get_pixel(x, y)
					mean_luma += (c.r + c.g + c.b) / 3.0
					sample_count += 1
			if sample_count > 0:
				mean_luma /= float(sample_count)
		if not ok or mean_luma < 0.02:
			print("[RouteBVisualCapture] FAIL shot=%s err=%s luma=%.4f path=%s" % [String(shot["name"]), err, mean_luma, path])
		else:
			print("[RouteBVisualCapture] OK shot=%s luma=%.4f path=%s" % [String(shot["name"]), mean_luma, path])
	print("[RouteBVisualCapture] done")
	if OS.get_environment("LUMINA_ROUTE_B_VISUAL_CAPTURE_QUIT").strip_edges().to_lower() in ["1", "true", "yes", "on"]:
		get_tree().quit()


func _create_lumina_eye_camera_for_bone(
	target: Node3D,
	skeleton: Skeleton3D,
	bone_name: String,
	attachment_name: String,
	camera_name: String,
	eye_label: String
) -> Camera3D:
	var attachment := BoneAttachment3D.new()
	attachment.name = attachment_name
	attachment.bone_name = bone_name
	skeleton.add_child(attachment)
	var eye_camera := Camera3D.new()
	eye_camera.name = camera_name
	eye_camera.current = false
	eye_camera.fov = 70.0
	eye_camera.near = 0.01
	eye_camera.far = LUMINA_EYE_CAPTURE_DEFAULT_FAR
	eye_camera.set_script(LuminaEyeCameraScript)
	eye_camera.set("eye_label", eye_label)
	eye_camera.set("eye_forward_offset", 0.0)
	attachment.add_child(eye_camera)
	eye_camera.set("target_controller_path", eye_camera.get_path_to(target))
	return eye_camera


func _setup_lumina_eye_capture_viewport() -> void:
	if not LUMINA_EYE_CAPTURE_ENABLED:
		return
	if DisplayServer.get_name().to_lower() == "headless":
		print("[LuminaEyeCapture] viewport skipped: headless display cannot capture rendered eye image.")
		return
	var source_camera := _get_primary_lumina_eye_camera()
	if source_camera == null:
		push_warning("Lumina eye capture viewport was not created because no eye camera exists.")
		return
	_lumina_eye_viewport = SubViewport.new()
	_lumina_eye_viewport.name = "LuminaEyeCaptureViewport"
	_lumina_eye_viewport.size = LUMINA_EYE_CAPTURE_SIZE
	_lumina_eye_viewport.disable_3d = false
	_lumina_eye_viewport.transparent_bg = false
	_lumina_eye_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	_lumina_eye_viewport.world_3d = get_world_3d()
	add_child(_lumina_eye_viewport)

	_lumina_eye_viewport_camera = Camera3D.new()
	_lumina_eye_viewport_camera.name = "LuminaEyeCaptureCamera"
	_lumina_eye_viewport_camera.current = true
	_lumina_eye_viewport_camera.fov = source_camera.fov
	_lumina_eye_viewport_camera.near = source_camera.near
	_lumina_eye_viewport_camera.far = source_camera.far
	_lumina_eye_viewport_camera.cull_mask = 1048575 & ~LUMINA_SELF_RENDER_LAYER
	_lumina_eye_viewport.add_child(_lumina_eye_viewport_camera)
	_lumina_eye_viewport_camera.make_current()
	if _lumina_eye_viewport.get_camera_3d() != _lumina_eye_viewport_camera:
		push_error("Lumina eye capture viewport rejected its dedicated camera.")
		_lumina_eye_viewport.queue_free()
		_lumina_eye_viewport = null
		_lumina_eye_viewport_camera = null
		return

	var capture_dir := _lumina_eye_capture_dir_absolute()
	DirAccess.make_dir_recursive_absolute(capture_dir)
	_configure_lumina_eye_capture_from_env()
	var env_label := OS.get_environment("LUMINA_EYE_CAPTURE_LABEL").strip_edges()
	if not env_label.is_empty():
		_lumina_eye_capture_label = _sanitize_lumina_eye_capture_label(env_label)
	print("[LuminaEyeCapture] viewport ready modes=", _lumina_eye_capture_modes, " fov=", _lumina_eye_capture_fov, " source=", source_camera.name, " dir=", capture_dir)

	_lumina_eye_capture_timer = Timer.new()
	_lumina_eye_capture_timer.name = "LuminaEyeCaptureTimer"
	_lumina_eye_capture_timer.wait_time = maxf(0.5, LUMINA_EYE_CAPTURE_INTERVAL_SECONDS)
	var interval_raw := OS.get_environment("LUMINA_EYE_CAPTURE_INTERVAL_SECONDS")
	if interval_raw.is_valid_float():
		_lumina_eye_capture_timer.wait_time = clampf(interval_raw.to_float(), 0.5, 60.0)
	_lumina_eye_capture_timer.autostart = not _lumina_eye_capture_once
	_lumina_eye_capture_timer.timeout.connect(_request_lumina_eye_capture)
	add_child(_lumina_eye_capture_timer)
	call_deferred("_request_lumina_eye_capture")


func _configure_lumina_eye_capture_from_env() -> void:
	var env_mode := OS.get_environment("LUMINA_EYE_CAPTURE_MODE").strip_edges().to_lower()
	if env_mode.is_empty():
		env_mode = LUMINA_EYE_CAPTURE_DEFAULT_MODE
	match env_mode:
		"left", "left_eye":
			_lumina_eye_capture_modes = ["left_eye"]
		"right", "right_eye":
			_lumina_eye_capture_modes = ["right_eye"]
		"all", "triple", "stereo", "both", "compare":
			_lumina_eye_capture_modes = ["left_eye", "right_eye", "binocular"]
		_:
			_lumina_eye_capture_modes = ["binocular"]
	_lumina_eye_capture_mode = str(_lumina_eye_capture_modes[0])
	_lumina_eye_capture_label = _label_for_lumina_eye_capture_mode(_lumina_eye_capture_mode)
	var env_fov := OS.get_environment("LUMINA_EYE_CAPTURE_FOV").strip_edges()
	if env_fov.is_valid_float():
		_lumina_eye_capture_fov = clampf(env_fov.to_float(), 35.0, 100.0)
	var env_far := OS.get_environment("LUMINA_EYE_CAPTURE_FAR").strip_edges()
	if env_far.is_valid_float():
		_lumina_eye_capture_far = clampf(env_far.to_float(), 8.0, 140.0)
	var env_binocular_offset := OS.get_environment("LUMINA_BINOCULAR_EYE_FORWARD_OFFSET").strip_edges()
	if env_binocular_offset.is_valid_float():
		_lumina_binocular_eye_extra_forward_offset = clampf(env_binocular_offset.to_float(), 0.0, 0.45)
	var env_monocular_offset := OS.get_environment("LUMINA_MONOCULAR_EYE_FORWARD_OFFSET").strip_edges()
	if env_monocular_offset.is_valid_float():
		_lumina_monocular_eye_extra_forward_offset = clampf(env_monocular_offset.to_float(), 0.0, 0.35)
	var env_target := OS.get_environment("LUMINA_EYE_CAPTURE_TARGET").strip_edges()
	var env_once := OS.get_environment("LUMINA_EYE_CAPTURE_ONCE").strip_edges().to_lower()
	_lumina_eye_capture_once = (not env_target.is_empty()) or env_once in ["1", "true", "yes", "on"]
	var env_no_quit := OS.get_environment("LUMINA_EYE_CAPTURE_NO_QUIT").strip_edges().to_lower()
	_lumina_eye_capture_quit_after_once = _lumina_eye_capture_once and not (env_no_quit in ["1", "true", "yes", "on"])


func _label_for_lumina_eye_capture_mode(mode: String) -> String:
	match mode:
		"left", "left_eye":
			return "left_eye"
		"right", "right_eye":
			return "right_eye"
		_:
			return "binocular_eye"


func _get_primary_lumina_eye_camera() -> Camera3D:
	if _lumina_left_eye_camera != null:
		return _lumina_left_eye_camera
	if _lumina_eye_camera != null:
		return _lumina_eye_camera
	return _lumina_right_eye_camera


func _tts_volume_db_from_env() -> float:
	var raw := OS.get_environment("LUMINA_TTS_VOLUME_DB").strip_edges()
	if raw == "":
		return DEFAULT_TTS_VOLUME_DB
	if not raw.is_valid_float():
		push_warning("LUMINA_TTS_VOLUME_DB is not a number: %s" % raw)
		return DEFAULT_TTS_VOLUME_DB
	return clampf(float(raw), -36.0, 12.0)


func _lumina_eye_capture_dir_absolute() -> String:
	var env_dir := OS.get_environment("LUMINA_EYE_CAPTURE_DIR").strip_edges()
	if not env_dir.is_empty():
		if env_dir.begins_with("res://") or env_dir.begins_with("user://"):
			return ProjectSettings.globalize_path(env_dir)
		return env_dir
	return ProjectSettings.globalize_path(LUMINA_EYE_CAPTURE_DIR)


func _get_lumina_eye_capture_camera() -> Camera3D:
	if _lumina_eye_capture_mode in ["right", "right_eye"] and _lumina_right_eye_camera != null:
		return _lumina_right_eye_camera
	if _lumina_eye_capture_mode in ["left", "left_eye"] and _lumina_left_eye_camera != null:
		return _lumina_left_eye_camera
	return _get_primary_lumina_eye_camera()


func _apply_lumina_eye_capture_transform(source_camera: Camera3D) -> void:
	_lumina_eye_capture_source_name = source_camera.name if source_camera != null else ""
	if _lumina_eye_capture_mode == "binocular" and _lumina_left_eye_camera != null and _lumina_right_eye_camera != null:
		var left_transform := _lumina_left_eye_camera.global_transform.orthonormalized()
		var right_transform := _lumina_right_eye_camera.global_transform.orthonormalized()
		var merged_transform := left_transform
		merged_transform.origin = left_transform.origin.lerp(right_transform.origin, 0.5)
		var left_quat := left_transform.basis.get_rotation_quaternion()
		var right_quat := right_transform.basis.get_rotation_quaternion()
		merged_transform.basis = Basis(left_quat.slerp(right_quat, 0.5))
		_lumina_eye_viewport_camera.global_transform = merged_transform
		var forward := -merged_transform.basis.z
		if forward.length() > 0.01:
			forward = forward.normalized()
			_lumina_eye_viewport_camera.global_position = merged_transform.origin + forward * _lumina_binocular_eye_extra_forward_offset
			_lumina_eye_viewport_camera.look_at(_lumina_eye_viewport_camera.global_position + forward, merged_transform.basis.y)
		_lumina_eye_capture_source_name = "binocular:%s+%s" % [_lumina_left_eye_camera.name, _lumina_right_eye_camera.name]
		return
	_lumina_eye_viewport_camera.global_transform = source_camera.global_transform.orthonormalized()
	var mono_forward := -source_camera.global_transform.basis.z
	if mono_forward.length() > 0.01:
		mono_forward = mono_forward.normalized()
		_lumina_eye_viewport_camera.global_position = source_camera.global_position + mono_forward * _lumina_monocular_eye_extra_forward_offset
		_lumina_eye_viewport_camera.look_at(_lumina_eye_viewport_camera.global_position + mono_forward, source_camera.global_transform.basis.y.normalized())


func _request_lumina_eye_capture() -> void:
	if _lumina_eye_capture_busy:
		return
	if _lumina_eye_capture_once and _lumina_eye_capture_once_completed:
		return
	if _lumina_eye_viewport == null or _lumina_eye_viewport_camera == null:
		print("[LuminaEyeCapture] skipped: viewport missing")
		return
	_lumina_eye_capture_pending_modes = _lumina_eye_capture_modes.duplicate()
	_lumina_eye_capture_requested_usec = Time.get_ticks_usec()
	_lumina_eye_capture_busy = true
	_capture_next_lumina_eye_mode()


func _capture_next_lumina_eye_mode() -> void:
	if _lumina_eye_capture_pending_modes.is_empty():
		_lumina_eye_capture_busy = false
		if _lumina_eye_capture_once:
			_lumina_eye_capture_once_completed = true
			if _lumina_eye_capture_timer != null:
				_lumina_eye_capture_timer.stop()
			if _lumina_eye_capture_quit_after_once:
				call_deferred("_quit_after_lumina_eye_capture_once")
		return
	_lumina_eye_capture_mode = str(_lumina_eye_capture_pending_modes.pop_front())
	_lumina_eye_capture_label = _label_for_lumina_eye_capture_mode(_lumina_eye_capture_mode)
	var source_camera := _get_lumina_eye_capture_camera()
	if source_camera == null:
		print("[LuminaEyeCapture] skipped: source camera missing mode=", _lumina_eye_capture_mode)
		call_deferred("_capture_next_lumina_eye_mode")
		return
	var setup_started_usec := Time.get_ticks_usec()
	_apply_lumina_eye_capture_transform(source_camera)
	if _lumina_eye_capture_debug_target_position != null:
		var debug_target_position := _lumina_eye_capture_debug_target_position as Vector3
		if debug_target_position.distance_to(_lumina_eye_viewport_camera.global_position) > 0.05:
			_lumina_eye_viewport_camera.look_at(debug_target_position, Vector3.UP)
	_lumina_eye_viewport_camera.fov = _lumina_eye_capture_fov
	_lumina_eye_viewport_camera.near = source_camera.near
	_lumina_eye_viewport_camera.far = maxf(source_camera.far, _lumina_eye_capture_far)
	_lumina_eye_viewport_camera.make_current()
	if _lumina_eye_viewport.get_camera_3d() != _lumina_eye_viewport_camera:
		push_error("Lumina eye capture aborted because the dedicated camera lost viewport ownership.")
		_lumina_eye_capture_busy = false
		return
	_hide_lumina_eye_capture_self_meshes()
	_lumina_eye_capture_sequence += 1
	_lumina_eye_capture_started_unix = Time.get_unix_time_from_system()
	_lumina_eye_capture_portable_snapshot = _lumina_eye_portable_navigation_snapshot()
	if not _lumina_eye_capture_portable_snapshot.is_empty():
		_lumina_eye_capture_portable_snapshot["source_frame_id"] = "%s-%d" % [OS.get_process_id(), _lumina_eye_capture_sequence]
	var capture_started_usec := Time.get_ticks_usec()
	_lumina_eye_capture_timing = {
		"schema_version": 1, "clock": "godot_ticks_usec_process_relative",
		"frame_id": "%s-%d" % [OS.get_process_id(), _lumina_eye_capture_sequence],
		"capture_started_unix": _lumina_eye_capture_started_unix,
		"requested_usec": _lumina_eye_capture_requested_usec,
		"capture_started_usec": capture_started_usec,
		"inter_capture_start_ms": float(capture_started_usec - _lumina_eye_capture_previous_start_usec) / 1000.0 if _lumina_eye_capture_previous_start_usec > 0 else null,
		"stages_ms": {"camera_setup": float(capture_started_usec - setup_started_usec) / 1000.0},
	}
	_lumina_eye_capture_previous_start_usec = capture_started_usec
	_lumina_eye_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	call_deferred("_save_lumina_eye_capture_after_draw")


func _save_lumina_eye_capture_after_draw() -> void:
	# macOS may suppress the automatic render loop when the spectator window is
	# occluded. Eye sensing must still produce fresh frames in that state.
	_lumina_eye_timing_stage("capture_to_deferred", int(_lumina_eye_capture_timing.get("capture_started_usec", Time.get_ticks_usec())))
	var stage_started_usec := Time.get_ticks_usec()
	_lumina_eye_viewport_camera.force_update_transform()
	_lumina_eye_timing_stage("force_update_transform", stage_started_usec)
	stage_started_usec = Time.get_ticks_usec()
	RenderingServer.force_sync()
	_lumina_eye_timing_stage("pre_draw_sync", stage_started_usec)
	stage_started_usec = Time.get_ticks_usec()
	RenderingServer.force_draw(false)
	_lumina_eye_timing_stage("force_draw", stage_started_usec)
	stage_started_usec = Time.get_ticks_usec()
	RenderingServer.force_sync()
	_lumina_eye_timing_stage("post_draw_sync", stage_started_usec)
	if _lumina_eye_viewport == null:
		print("[LuminaEyeCapture] save skipped: viewport missing")
		_restore_lumina_eye_capture_self_meshes()
		_lumina_eye_capture_busy = false
		return
	var active_render_camera := _lumina_eye_viewport.get_camera_3d()
	if active_render_camera != _lumina_eye_viewport_camera:
		print("[LuminaEyeCapture] save skipped: dedicated camera is not active")
		_restore_lumina_eye_capture_self_meshes()
		_lumina_eye_capture_busy = false
		return
	var texture := _lumina_eye_viewport.get_texture()
	if texture == null:
		print("[LuminaEyeCapture] save skipped: texture missing")
		_restore_lumina_eye_capture_self_meshes()
		_lumina_eye_capture_busy = false
		return
	stage_started_usec = Time.get_ticks_usec()
	var image := texture.get_image()
	_lumina_eye_timing_stage("get_image", stage_started_usec)
	_restore_lumina_eye_capture_self_meshes()
	if image == null or image.is_empty():
		print("[LuminaEyeCapture] save skipped: image empty")
		_lumina_eye_capture_busy = false
		return
	stage_started_usec = Time.get_ticks_usec()
	var capture_dir := _lumina_eye_capture_dir_absolute()
	DirAccess.make_dir_recursive_absolute(capture_dir)
	_lumina_eye_timing_stage("capture_directory", stage_started_usec)
	# Eight owned ring slots bound disk usage; old timestamped evidence is never
	# deleted. Publish latest.json only after the new image and metadata exist.
	var path := "%s/lumina_%s_slot_%02d.png" % [capture_dir, _lumina_eye_capture_label, _lumina_eye_capture_sequence % 8]
	var temporary_path := path + ".tmp.png"
	stage_started_usec = Time.get_ticks_usec()
	var err := image.save_png(temporary_path)
	_lumina_eye_timing_stage("png_save", stage_started_usec)
	if err == OK:
		stage_started_usec = Time.get_ticks_usec()
		err = DirAccess.rename_absolute(temporary_path, path)
		_lumina_eye_timing_stage("png_rename", stage_started_usec)
	if err == OK:
		_lumina_eye_capture_latest_path = path
		_lumina_eye_capture_latest_paths[_lumina_eye_capture_label] = path
		print("[LuminaEyeCapture] saved=", path)
		_save_lumina_eye_capture_metadata(path)
	else:
		print("[LuminaEyeCapture] save failed err=", err, " path=", path)
	call_deferred("_capture_next_lumina_eye_mode")


func _hide_lumina_eye_capture_self_meshes() -> void:
	# A private render layer excludes self only from the eye viewport. Never
	# toggle shared-world visibility: the spectator must not see flashing.
	if _avatar != null:
		_hide_lumina_eye_capture_geometry_under(_avatar)
	if _lumina_eye_capture_hide_player_enabled() and _player_avatar != null:
		_hide_lumina_eye_capture_geometry_under(_player_avatar)


func _lumina_eye_capture_hide_player_enabled() -> bool:
	return OS.get_environment("LUMINA_EYE_CAPTURE_HIDE_PLAYER").strip_edges().to_lower() in ["1", "true", "yes", "on"]


func _hide_lumina_eye_capture_geometry_under(root: Node) -> void:
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		if node is GeometryInstance3D:
			var geometry := node as GeometryInstance3D
			geometry.layers = LUMINA_SELF_RENDER_LAYER
		for child in node.get_children():
			stack.append(child as Node)


func _restore_lumina_eye_capture_self_meshes() -> void:
	pass # Visibility is never changed by an eye capture.


func _quit_after_lumina_eye_capture_once() -> void:
	await get_tree().process_frame
	print("[LuminaEyeCapture] one-shot capture complete; quitting Godot.")
	get_tree().quit()


func get_lumina_eye_capture_state() -> Dictionary:
	var source_camera := _get_lumina_eye_capture_camera()
	return {
		"enabled": LUMINA_EYE_CAPTURE_ENABLED,
		"latest_path": _lumina_eye_capture_latest_path,
		"latest_paths": _lumina_eye_capture_latest_paths,
		"latest_metadata": _lumina_eye_capture_latest_metadata,
		"latest_manifest": _lumina_eye_capture_dir_absolute().path_join("latest.json"),
		"label": _lumina_eye_capture_label,
		"mode": _lumina_eye_capture_mode,
		"modes": _lumina_eye_capture_modes,
		"source_camera": _lumina_eye_capture_source_name if not _lumina_eye_capture_source_name.is_empty() else (source_camera.name if source_camera != null else ""),
		"fov": _lumina_eye_capture_fov,
		"binocular_forward_offset": _lumina_binocular_eye_extra_forward_offset,
		"monocular_forward_offset": _lumina_monocular_eye_extra_forward_offset,
		"capture_size": {
			"width": LUMINA_EYE_CAPTURE_SIZE.x,
			"height": LUMINA_EYE_CAPTURE_SIZE.y,
		},
	}


func get_lumina_eye_pose() -> Dictionary:
	var left := _get_primary_lumina_eye_camera()
	if left == null:
		return {}
	var pose := left.global_transform.orthonormalized()
	if _lumina_right_eye_camera != null and _lumina_left_eye_camera != null:
		var right_pose := _lumina_right_eye_camera.global_transform.orthonormalized()
		pose.origin = pose.origin.lerp(right_pose.origin, 0.5)
		pose.basis = Basis(pose.basis.get_rotation_quaternion().slerp(right_pose.basis.get_rotation_quaternion(), 0.5))
	var source := "animated_eye_bones" if _lumina_left_eye_camera != null and _lumina_right_eye_camera != null else "single_eye_or_head_bone_fallback"
	return {"position": pose.origin, "forward": -pose.basis.z, "up": pose.basis.y,
		"source": source, "timestamp_unix": Time.get_unix_time_from_system()}


func _sanitize_lumina_eye_capture_label(raw_label: String) -> String:
	var label := raw_label.to_lower()
	for ch in [" ", "/", "\\", ":", "*", "?", "\"", "<", ">", "|"]:
		label = label.replace(ch, "_")
	if label.is_empty():
		return "left_eye"
	return label



func _save_lumina_eye_capture_metadata(image_path: String) -> void:
	if _lumina_eye_viewport_camera == null:
		return
	var stage_started_usec := Time.get_ticks_usec()
	var objects := get_node_or_null("Objects") as Node3D
	var visible_items := []
	var all_items := []
	if objects != null:
		for child in objects.get_children():
			var item := child as Node3D
			if item == null:
				continue
			var sample_position := item.global_position + Vector3.UP * 0.9
			var to_item := sample_position - _lumina_eye_viewport_camera.global_position
			var distance := to_item.length()
			var forward := (-_lumina_eye_viewport_camera.global_transform.basis.z).normalized()
			var angle_deg := 180.0
			var in_front := false
			var side := "center"
			if distance > 0.001:
				var direction := to_item.normalized()
				var forward_dot := clampf(forward.dot(direction), -1.0, 1.0)
				angle_deg = rad_to_deg(acos(forward_dot))
				in_front = forward_dot > 0.0
				var right := _lumina_eye_viewport_camera.global_transform.basis.x.normalized()
				var side_dot := right.dot(direction)
				if side_dot < -0.18:
					side = "left"
				elif side_dot > 0.18:
					side = "right"
			var visible := _lumina_eye_viewport_camera.is_position_in_frustum(sample_position)
			var display_name := str(item.name)
			if item.has_meta("si_display_name"):
				display_name = str(item.get_meta("si_display_name"))
			var item_data := {
				"name": str(item.name),
				"display_name": display_name,
				"type": str(item.get_meta("si_type", "")) if item.has_meta("si_type") else "",
				"area": str(item.get_meta("si_area", "")) if item.has_meta("si_area") else "",
				"aliases": item.get_meta("si_aliases", []) if item.has_meta("si_aliases") else [],
				"search_hint": str(item.get_meta("si_search_hint", "")) if item.has_meta("si_search_hint") else "",
				"position": _lumina_eye_capture_vector3_dict(item.global_position),
				"sample_position": _lumina_eye_capture_vector3_dict(sample_position),
				"distance": distance,
				"angle_deg": angle_deg,
				"in_front": in_front,
				"side": side,
				"visible": visible,
			}
			all_items.append(item_data)
			if visible:
				visible_items.append(item_data)
	_lumina_eye_timing_stage("visibility_geometry", stage_started_usec)
	var metadata := {
		"schema_version": 2,
		"frame_id": "%s-%d" % [OS.get_process_id(), _lumina_eye_capture_sequence],
		"timestamp_unix": _lumina_eye_capture_started_unix,
		"captured_unix": _lumina_eye_capture_started_unix,
		"saved_unix": Time.get_unix_time_from_system(),
		"image_path": image_path,
		"image_sha256": _lumina_eye_timed_image_hash(image_path),
		"capture_mode": _lumina_eye_capture_mode,
		"capture_label": _lumina_eye_capture_label,
		"capture_source": _lumina_eye_capture_source_name,
		"debug_target": OS.get_environment("LUMINA_EYE_CAPTURE_TARGET"),
		"debug_target_alias": _lumina_eye_capture_debug_target_alias,
		"debug_target_canonical": _lumina_eye_capture_debug_target_canonical,
		"fov": _lumina_eye_viewport_camera.fov,
		"monocular_forward_offset": _lumina_monocular_eye_extra_forward_offset,
		"binocular_forward_offset": _lumina_binocular_eye_extra_forward_offset,
		"camera_position": _lumina_eye_capture_vector3_dict(_lumina_eye_viewport_camera.global_position),
		"camera_forward": _lumina_eye_capture_vector3_dict((-_lumina_eye_viewport_camera.global_transform.basis.z).normalized()),
		"camera_right": _lumina_eye_capture_vector3_dict(_lumina_eye_viewport_camera.global_transform.basis.x.normalized()),
		"camera_up": _lumina_eye_capture_vector3_dict(_lumina_eye_viewport_camera.global_transform.basis.y.normalized()),
		"aspect": float(LUMINA_EYE_CAPTURE_SIZE.x) / float(LUMINA_EYE_CAPTURE_SIZE.y),
		"width": LUMINA_EYE_CAPTURE_SIZE.x,
		"height": LUMINA_EYE_CAPTURE_SIZE.y,
		"pose_source": get_lumina_eye_pose().get("source", "unavailable"),
		"render_source": "dedicated_eye_subviewport",
		"render_viewport_instance_id": _lumina_eye_viewport.get_instance_id(),
		"render_camera_instance_id": _lumina_eye_viewport_camera.get_instance_id(),
		"active_camera_instance_id": _lumina_eye_viewport.get_camera_3d().get_instance_id() if _lumina_eye_viewport.get_camera_3d() != null else 0,
		"self_exclusion": "private_camera_render_layer_no_visibility_mutation",
		"evaluator_geometry": _lumina_eye_timed_geometry_evaluator(objects),
		"visible_items": visible_items,
		"all_items": all_items,
		"basis": "capture_camera_frustum",
	}
	metadata["portable_depth"] = _lumina_eye_portable_depth()
	if not _lumina_eye_capture_portable_snapshot.is_empty():
		metadata["portable_navigation"] = _lumina_eye_capture_portable_snapshot.duplicate(true)
	# The current manifest cannot contain its own completed write duration
	# without rewriting it. Include the previous completed frame, while the
	# existing metadata log below records this frame after atomic publication.
	metadata["capture_timing"] = _lumina_eye_capture_timing.duplicate(true)
	metadata["previous_capture_timing"] = _lumina_eye_capture_previous_timing.duplicate(true)
	var metadata_path := image_path.get_basename() + ".json"
	stage_started_usec = Time.get_ticks_usec()
	var file := FileAccess.open(metadata_path, FileAccess.WRITE)
	if file == null:
		push_warning("Lumina eye capture metadata could not be written: %s" % metadata_path)
		return
	file.store_string(JSON.stringify(metadata, "	"))
	file.close()
	_lumina_eye_timing_stage("sidecar_write", stage_started_usec)
	metadata["capture_timing"] = _lumina_eye_capture_timing.duplicate(true)
	_lumina_eye_capture_latest_metadata = metadata
	var manifest_path := _lumina_eye_capture_dir_absolute().path_join("latest.json")
	var publish_started_usec := Time.get_ticks_usec()
	stage_started_usec = publish_started_usec
	var manifest_file := FileAccess.open(manifest_path + ".tmp", FileAccess.WRITE)
	var manifest_error := ERR_FILE_CANT_OPEN
	if manifest_file != null:
		manifest_file.store_string(JSON.stringify(metadata, "\t"))
		manifest_file.close()
		_lumina_eye_timing_stage("manifest_write", stage_started_usec)
		stage_started_usec = Time.get_ticks_usec()
		manifest_error = DirAccess.rename_absolute(manifest_path + ".tmp", manifest_path)
		_lumina_eye_timing_stage("manifest_rename", stage_started_usec)
	_lumina_eye_timing_stage("manifest_publish", publish_started_usec)
	_lumina_eye_capture_timing["finished_usec"] = Time.get_ticks_usec()
	_lumina_eye_capture_timing["finished_unix"] = Time.get_unix_time_from_system()
	_lumina_eye_capture_timing["manifest_error"] = manifest_error
	_lumina_eye_capture_timing["total_capture_ms"] = float(int(_lumina_eye_capture_timing["finished_usec"]) - int(_lumina_eye_capture_timing["capture_started_usec"])) / 1000.0
	_lumina_eye_capture_previous_timing = _lumina_eye_capture_timing.duplicate(true)
	print("[LuminaEyeCapture] metadata=", metadata_path, " timing=", JSON.stringify(_lumina_eye_capture_previous_timing))


func _lumina_eye_timing_stage(stage: String, started_usec: int) -> void:
	_lumina_eye_capture_timing["stages_ms"][stage] = maxf(0.0, float(Time.get_ticks_usec() - started_usec) / 1000.0)


func _lumina_eye_portable_depth() -> Dictionary:
	var started_usec := Time.get_ticks_usec()
	var excluded: Array[RID] = []
	if _avatar is CollisionObject3D:
		excluded.append((_avatar as CollisionObject3D).get_rid())
	var sample_size := Vector2i(24, 16)
	var width_raw := OS.get_environment("LUMINA_PORTABLE_DEPTH_WIDTH").strip_edges()
	var height_raw := OS.get_environment("LUMINA_PORTABLE_DEPTH_HEIGHT").strip_edges()
	if width_raw.is_valid_int():
		sample_size.x = clampi(width_raw.to_int(), 1, 64)
	if height_raw.is_valid_int():
		sample_size.y = clampi(height_raw.to_int(), 1, 48)
	var result: Dictionary = PortableDepthSensorScript.capture(
		_lumina_eye_viewport_camera,
		LUMINA_EYE_CAPTURE_SIZE,
		excluded,
		sample_size,
		minf(_lumina_eye_capture_far, 30.0)
	)
	_lumina_eye_timing_stage("portable_depth", started_usec)
	return result


func _lumina_eye_portable_navigation_snapshot() -> Dictionary:
	if not is_instance_valid(_avatar) or not _avatar.has_method("get_state_snapshot"):
		return {}
	var state_value: Variant = _avatar.call("get_state_snapshot")
	if not (state_value is Dictionary):
		return {}
	var snapshot_value: Variant = (state_value as Dictionary).get("portable_navigation", null)
	if not (snapshot_value is Dictionary):
		return {}
	return (snapshot_value as Dictionary).duplicate(true)


func _lumina_eye_timed_image_hash(image_path: String) -> String:
	var started_usec := Time.get_ticks_usec()
	var result := FileAccess.get_sha256(image_path)
	_lumina_eye_timing_stage("png_hash_read", started_usec)
	return result


func _lumina_eye_timed_geometry_evaluator(objects: Node3D) -> Dictionary:
	var started_usec := Time.get_ticks_usec()
	var result := _lumina_eye_geometry_evaluator(objects)
	_lumina_eye_timing_stage("evaluator_geometry", started_usec)
	return result


func _lumina_eye_geometry_evaluator(objects: Node3D) -> Dictionary:
	# Simulator ground truth for post-VLM grounding; NEVER include in VLM input.
	var result := {"basis": "simulation_geometry_not_visual_recognition", "objects": []}
	if objects == null:
		return result
	var room := objects.get_node_or_null("FutureRoomV008Candidate_GLB")
	var visual_prefixes := _lumina_anchor_visual_prefixes()
	var excluded: Array[RID] = []
	if _avatar is CollisionObject3D:
		excluded.append((_avatar as CollisionObject3D).get_rid())
	for raw_item in objects.get_children():
		var item := raw_item as Node3D
		if item == null or not item.is_in_group("si_addressable"):
			continue
		var meshes: Array = item.find_children("*", "MeshInstance3D", true, false)
		if room != null and visual_prefixes.has(str(item.name)):
			for mesh in room.find_children("*", "MeshInstance3D", true, false):
				for prefix in visual_prefixes[str(item.name)]:
					if str(mesh.name).begins_with(prefix):
						meshes.append(mesh)
		var has_bounds := false
		var bounds := AABB()
		for raw_mesh in meshes:
			var mesh := raw_mesh as MeshInstance3D
			if mesh == null or not mesh.is_visible_in_tree():
				continue
			var mesh_bounds: AABB = mesh.global_transform * mesh.get_aabb()
			bounds = bounds.merge(mesh_bounds) if has_bounds else mesh_bounds
			has_bounds = true
		if not has_bounds:
			continue # Invisible semantic anchors without verified meshes are ineligible.
		var center := bounds.get_center()
		var screen := _lumina_eye_viewport_camera.unproject_position(center) / Vector2(LUMINA_EYE_CAPTURE_SIZE)
		var in_front := not _lumina_eye_viewport_camera.is_position_behind(center)
		var min_uv := Vector2(INF, INF)
		var max_uv := Vector2(-INF, -INF)
		var visible_samples := 0
		var sampled := 0
		var center_occluded := false
		var center_occluder := ""
		for corner_index in range(-1, 8):
			var corner := center if corner_index < 0 else bounds.get_endpoint(corner_index).lerp(center, 0.1)
			if _lumina_eye_viewport_camera.is_position_behind(corner):
				continue
			var uv := _lumina_eye_viewport_camera.unproject_position(corner) / Vector2(LUMINA_EYE_CAPTURE_SIZE)
			min_uv = min_uv.min(uv)
			max_uv = max_uv.max(uv)
			var query := PhysicsRayQueryParameters3D.create(_lumina_eye_viewport_camera.global_position, corner)
			query.exclude = excluded
			var hit := get_world_3d().direct_space_state.intersect_ray(query)
			var blocked := not hit.is_empty() and not bounds.grow(0.04).has_point(hit.position)
			if corner_index < 0:
				center_occluded = blocked
				if blocked and hit.collider is Node:
					center_occluder = str(hit.collider.get_path())
			if uv.x >= 0 and uv.x <= 1 and uv.y >= 0 and uv.y <= 1:
				sampled += 1
				visible_samples += int(not blocked)
		var intersects_view := in_front and max_uv.x >= 0.0 and min_uv.x <= 1.0 and max_uv.y >= 0.0 and min_uv.y <= 1.0
		if not min_uv.is_finite() or not max_uv.is_finite():
			min_uv = Vector2.ZERO
			max_uv = Vector2.ZERO
		result.objects.append({"id": str(item.name), "name": str(item.name),
			"instance_id": item.get_instance_id(),
			"type": str(item.get_meta("si_type", "")), "world_position": _lumina_eye_capture_vector3_dict(item.global_position),
			"visual_center": _lumina_eye_capture_vector3_dict(center), "projected_center": [screen.x, screen.y],
			"projected_bbox": [clampf(min_uv.x, 0.0, 1.0), clampf(min_uv.y, 0.0, 1.0), clampf(max_uv.x, 0.0, 1.0), clampf(max_uv.y, 0.0, 1.0)],
			"in_frustum": intersects_view, "occluded": visible_samples == 0,
			"center_occluded": center_occluded, "center_occluder": center_occluder,
			"visible_ray_samples": visible_samples, "ray_sample_count": sampled,
			"occlusion_basis": "physics_center_and_8_bounds_rays_not_per_pixel", "visual_mesh_count": meshes.size()})
	return result


func _lumina_anchor_visual_prefixes() -> Dictionary:
	return {
		"Table": ["v008ROOM_LOUNGE_COFFEE_TABLE_"],
		"Sofa": ["v008ROOM_LOUNGE_SOFA_back_segment_"],
		"Chair_A": ["v008ROOM_LOUNGE_SOFA_left_segment_"],
		"Chair_B": ["v008ROOM_LOUNGE_SOFA_right_segment_"],
		"Plant": ["v008ROOM_PLANT_front_left_entry_corner_"],
		"PlantFrontRight": ["v008ROOM_PLANT_front_right_entry_corner_"],
		"PlantBackLeft": ["v008ROOM_PLANT_back_left_window_corner_"],
		"PlantBackRight": ["v008ROOM_PLANT_back_right_window_corner_"],
		"SideTable": ["v008ROOM_LEFT_READING_desk_against_wall"],
	}


func _lumina_eye_capture_vector3_dict(value: Vector3) -> Dictionary:
	return {"x": value.x, "y": value.y, "z": value.z}

func _apply_lumina_eye_capture_debug_target(objects: Node3D) -> void:
	var target_name := OS.get_environment("LUMINA_EYE_CAPTURE_TARGET").strip_edges()
	if target_name.is_empty() or _avatar == null:
		return
	var target_position := Vector3.ZERO
	var found := false
	var normalized := target_name.to_lower()
	_lumina_eye_capture_debug_target_alias = target_name
	_lumina_eye_capture_debug_target_canonical = normalized
	match normalized:
		"user", "player":
			if _player_avatar != null:
				target_position = _player_avatar.global_position + Vector3.UP * 1.45
				_lumina_eye_capture_debug_target_canonical = "user"
				found = true
		"table":
			var table_position: Variant = _resolve_eye_capture_target_position(objects, "Table", 0.9)
			if table_position != null:
				target_position = table_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "table"
				found = true
		"front_table_width_roi":
			var table_width_position: Variant = _resolve_eye_capture_target_position(objects, "Table", 1.05)
			if table_width_position != null:
				target_position = table_width_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "table"
				found = true
		"tabletop_remote_candidate", "remote_phone_disambiguation_roi", "box_remote_occlusion_roi", "cup_vase_disambiguation_roi", "book_open_closed_roi":
			var tabletop_position: Variant = _resolve_eye_capture_target_position(objects, "Table", 1.18)
			if tabletop_position != null:
				target_position = tabletop_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "table"
				found = true
		"table_backside_once", "directional_roi_right":
			var table_back_position: Variant = _resolve_eye_capture_target_position(objects, "Table", 1.0, Vector3(0.45, 0.0, 0.0))
			if table_back_position != null:
				target_position = table_back_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "table"
				found = true
		"chair":
			var chair_position: Variant = _resolve_eye_capture_target_position(objects, "Chair_A", 0.9)
			if chair_position != null:
				target_position = chair_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "chair"
				found = true
		"visible_chair_count_wide", "nearest_furniture_route_short":
			var chair_wide_position: Variant = _resolve_eye_capture_target_position(objects, "Chair_A", 0.95)
			if chair_wide_position != null:
				target_position = chair_wide_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "chair"
				found = true
		"sofa":
			var sofa_position: Variant = _resolve_eye_capture_target_position(objects, "Sofa", 0.9)
			if sofa_position != null:
				target_position = sofa_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "sofa"
				found = true
		"front_sofa_backrest_roi":
			var sofa_back_position: Variant = _resolve_eye_capture_target_position(objects, "Sofa", 1.28)
			if sofa_back_position != null:
				target_position = sofa_back_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "sofa"
				found = true
		"front_sofa_floor_roi":
			var sofa_floor_position: Variant = _resolve_eye_capture_target_position(objects, "Sofa", 0.18, Vector3(0.0, 0.0, 0.55))
			if sofa_floor_position != null:
				target_position = sofa_floor_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "sofa"
				found = true
		"visible_sofa_count_wide", "ambiguous_multi_object_front", "back_sofa_turn_once", "directional_roi_left", "plush_cushion_disambiguation_roi", "behind_referent_short_turn":
			var sofa_wide_position: Variant = _resolve_eye_capture_target_position(objects, "Sofa", 0.9)
			if sofa_wide_position != null:
				target_position = sofa_wide_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "sofa"
				found = true
		"plant":
			var plant_position: Variant = _resolve_eye_capture_target_position(objects, "Plant", 0.9)
			if plant_position != null:
				target_position = plant_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "plant"
				found = true
		"plant_room_wide":
			var plant_room_position: Variant = _resolve_eye_capture_target_position(objects, "Plant", 0.95)
			if plant_room_position != null:
				target_position = plant_room_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "plant"
				found = true
		"plant_chair_relation_roi":
			var relation_position: Variant = _resolve_eye_capture_midpoint_position(objects, "Plant", "Chair_A", 0.95)
			if relation_position != null:
				target_position = relation_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "plant"
				found = true
		"sidetable":
			var side_table_position: Variant = _resolve_eye_capture_target_position(objects, "SideTable", 0.9)
			if side_table_position != null:
				target_position = side_table_position as Vector3
				_lumina_eye_capture_debug_target_canonical = "table"
				found = true
		_:
			var custom_position: Variant = _resolve_eye_capture_target_position(objects, target_name, 0.9)
			if custom_position != null:
				target_position = custom_position as Vector3
				_lumina_eye_capture_debug_target_canonical = target_name
				found = true
	if not found:
		push_warning("Lumina eye capture debug target was not found: %s" % target_name)
		return
	if _avatar.has_method("look_at_position"):
		_lumina_eye_capture_debug_target_position = target_position
		_avatar.call("look_at_position", target_position)
		print("[LuminaEyeCapture] debug target=", target_name, " canonical=", _lumina_eye_capture_debug_target_canonical, " position=", target_position)
		call_deferred("_request_lumina_eye_capture_after_debug_settle")


func _request_lumina_eye_capture_after_debug_settle() -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	await get_tree().create_timer(0.35).timeout
	_request_lumina_eye_capture()


func _resolve_eye_capture_target_position(objects: Node3D, node_name: String, height_offset: float = 0.9, world_offset: Vector3 = Vector3.ZERO) -> Variant:
	var node := _find_eye_capture_target_node(objects, node_name)
	if node == null:
		return null
	return node.global_position + Vector3.UP * height_offset + world_offset


func _resolve_eye_capture_midpoint_position(objects: Node3D, first_node_name: String, second_node_name: String, height_offset: float = 0.9) -> Variant:
	var first_node := _find_eye_capture_target_node(objects, first_node_name)
	var second_node := _find_eye_capture_target_node(objects, second_node_name)
	if first_node == null or second_node == null:
		return null
	return (first_node.global_position + second_node.global_position) * 0.5 + Vector3.UP * height_offset


func _find_eye_capture_target_node(objects: Node3D, target_name: String) -> Node3D:
	if objects == null:
		return null
	var direct := objects.get_node_or_null(target_name) as Node3D
	if direct != null:
		return direct
	var normalized_target := _normalize_lumina_target_name(target_name)
	if normalized_target.is_empty():
		return null
	var fuzzy_candidates: Array = []
	for child in objects.get_children():
		var item := child as Node3D
		if item == null:
			continue
		var candidates := [str(item.name)]
		if item.has_meta("si_display_name"):
			candidates.append(str(item.get_meta("si_display_name")))
		if item.has_meta("si_type"):
			candidates.append(str(item.get_meta("si_type")))
		if item.has_meta("si_area"):
			candidates.append(str(item.get_meta("si_area")))
			fuzzy_candidates.append(str(item.get_meta("si_area")))
		if item.has_meta("si_aliases"):
			var aliases: Variant = item.get_meta("si_aliases")
			if typeof(aliases) == TYPE_ARRAY:
				for alias in aliases:
					candidates.append(str(alias))
					fuzzy_candidates.append(str(alias))
		for candidate in candidates:
			var normalized_candidate := _normalize_lumina_target_name(candidate)
			if normalized_candidate == normalized_target:
				return item
	for child in objects.get_children():
		var item := child as Node3D
		if item == null:
			continue
		var candidates := []
		if item.has_meta("si_display_name"):
			candidates.append(str(item.get_meta("si_display_name")))
		if item.has_meta("si_area"):
			candidates.append(str(item.get_meta("si_area")))
		if item.has_meta("si_aliases"):
			var aliases: Variant = item.get_meta("si_aliases")
			if typeof(aliases) == TYPE_ARRAY:
				for alias in aliases:
					candidates.append(str(alias))
		for candidate in candidates:
			var normalized_candidate := _normalize_lumina_target_name(candidate)
			if normalized_candidate.length() >= 4 and normalized_candidate.contains(normalized_target):
				return item
	return null


func _normalize_lumina_target_name(value: String) -> String:
	return String(value).strip_edges().to_lower().replace("　", "").replace(" ", "").replace("_", "").replace("-", "")


func _find_first_skeleton(root: Node) -> Skeleton3D:
	var stack: Array[Node] = [root]
	while stack.size() > 0:
		var node: Node = stack.pop_back()
		var skeleton := node as Skeleton3D
		if skeleton != null:
			return skeleton
		for child in node.get_children():
			stack.append(child)
	return null


func _find_skeleton_bone_name(skeleton: Skeleton3D, candidates: Array[String]) -> String:
	for candidate in candidates:
		if skeleton.find_bone(candidate) >= 0:
			return candidate
	for i in range(skeleton.get_bone_count()):
		var bone_name := skeleton.get_bone_name(i)
		var normalized := bone_name.to_lower()
		for candidate in candidates:
			var normalized_candidate := candidate.to_lower()
			if normalized == normalized_candidate or normalized.contains(normalized_candidate):
				return bone_name
	return ""


func _setup_floor() -> void:
	var floor := MeshInstance3D.new()
	floor.name = "Floor"
	var plane := PlaneMesh.new()
	plane.size = FLOOR_SIZE
	floor.mesh = plane
	floor.material_override = _build_stage_material(STAGE_FLOOR_COLOR, 0.72)
	floor.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	add_child(floor)
	_create_floor_surface_detail()

	_create_static_box(
		self,
		"FloorEdgeNorth",
		Vector3(0.0, 0.02, FLOOR_SIZE.y * 0.5),
		Vector3(FLOOR_SIZE.x, 0.08, 0.22),
		STAGE_FLOOR_EDGE_COLOR
	)
	_create_static_box(
		self,
		"FloorEdgeSouth",
		Vector3(0.0, 0.02, -FLOOR_SIZE.y * 0.5),
		Vector3(FLOOR_SIZE.x, 0.08, 0.22),
		STAGE_FLOOR_EDGE_COLOR
	)
	_create_static_box(
		self,
		"FloorEdgeWest",
		Vector3(-FLOOR_SIZE.x * 0.5, 0.02, 0.0),
		Vector3(0.22, 0.08, FLOOR_SIZE.y),
		STAGE_FLOOR_EDGE_COLOR
	)
	_create_static_box(
		self,
		"FloorEdgeEast",
		Vector3(FLOOR_SIZE.x * 0.5, 0.02, 0.0),
		Vector3(0.22, 0.08, FLOOR_SIZE.y),
		STAGE_FLOOR_EDGE_COLOR
	)

	# Room shell primitives stay at the floor edge so the forward camera sees a complete small stage,
	# while the center remains open for the AI avatar and the existing command targets.
	_create_static_box(
		self,
		"WallLeft",
		Vector3(-FLOOR_SIZE.x * 0.5, 0.0, 0.15),
		Vector3(0.18, WALL_HEIGHT, FLOOR_SIZE.y),
		STAGE_WALL_COLOR
	)
	_create_static_box(
		self,
		"WallRight",
		Vector3(FLOOR_SIZE.x * 0.5, 0.0, 0.15),
		Vector3(0.18, WALL_HEIGHT, FLOOR_SIZE.y),
		STAGE_WALL_COLOR
	)
	_create_static_box(
		self,
		"WallBack",
		Vector3(0.0, 0.0, FLOOR_SIZE.y * 0.5),
		Vector3(FLOOR_SIZE.x, WALL_HEIGHT, 0.18),
		STAGE_WALL_COLOR
	)
	_create_static_box(
		self,
		"CeilingPanel",
		Vector3(0.0, WALL_HEIGHT, 0.0),
		Vector3(FLOOR_SIZE.x, 0.08, FLOOR_SIZE.y),
		STAGE_CEILING_COLOR
	)
	_create_static_box(
		self,
		"BackWallAccent",
		Vector3(0.0, 0.72, FLOOR_SIZE.y * 0.5 - 0.2),
		Vector3(4.35, 1.72, 0.06),
		STAGE_ACCENT_WALL_COLOR
	)
	_create_static_box(
		self,
		"BackWallAccentBottomTrim",
		Vector3(0.0, 0.62, FLOOR_SIZE.y * 0.5 - 0.24),
		Vector3(4.55, 0.08, 0.055),
		STAGE_TRIM_COLOR
	)
	_create_static_box(
		self,
		"BackWallAccentLeftTrim",
		Vector3(-2.22, 0.72, FLOOR_SIZE.y * 0.5 - 0.24),
		Vector3(0.08, 1.72, 0.055),
		STAGE_TRIM_COLOR
	)
	_create_static_box(
		self,
		"BackWallAccentRightTrim",
		Vector3(2.22, 0.72, FLOOR_SIZE.y * 0.5 - 0.24),
		Vector3(0.08, 1.72, 0.055),
		STAGE_TRIM_COLOR
	)
	_create_static_box(
		self,
		"BackWallSoftLightBand",
		Vector3(0.0, 2.34, FLOOR_SIZE.y * 0.5 - 0.25),
		Vector3(3.65, 0.07, 0.05),
		STAGE_WARM_LIGHT_COLOR
	)
	_create_static_box(
		self,
		"BackWallPanelLeft",
		Vector3(-3.45, 0.88, FLOOR_SIZE.y * 0.5 - 0.23),
		Vector3(1.2, 1.46, 0.05),
		STAGE_PANEL_COLOR
	)
	_create_static_box(
		self,
		"BackWallPanelRight",
		Vector3(3.45, 0.88, FLOOR_SIZE.y * 0.5 - 0.23),
		Vector3(1.2, 1.46, 0.05),
		STAGE_PANEL_COLOR
	)
	_create_static_box(
		self,
		"BackWallUpperTrim",
		Vector3(0.0, 2.6, FLOOR_SIZE.y * 0.5 - 0.22),
		Vector3(FLOOR_SIZE.x * 0.92, 0.14, 0.06),
		STAGE_TRIM_COLOR
	)
	_create_static_box(
		self,
		"LeftWallBaseTrim",
		Vector3(-FLOOR_SIZE.x * 0.5 + 0.08, 0.08, 0.15),
		Vector3(0.12, 0.16, FLOOR_SIZE.y * 0.96),
		STAGE_TRIM_COLOR
	)
	_create_static_box(
		self,
		"RightWallBaseTrim",
		Vector3(FLOOR_SIZE.x * 0.5 - 0.08, 0.08, 0.15),
		Vector3(0.12, 0.16, FLOOR_SIZE.y * 0.96),
		STAGE_TRIM_COLOR
	)
	_create_static_box(
		self,
		"BackWallBaseTrim",
		Vector3(0.0, 0.08, FLOOR_SIZE.y * 0.5 - 0.22),
		Vector3(FLOOR_SIZE.x * 0.96, 0.16, 0.06),
		STAGE_TRIM_COLOR
	)
	_create_room_shell_completion()


func _create_floor_surface_detail() -> void:
	var line_color := Color(0.62, 0.60, 0.55)
	for i in range(1, 12):
		var z := -FLOOR_SIZE.y * 0.5 + float(i) * (FLOOR_SIZE.y / 12.0)
		_create_static_box(
			self,
			"FloorBoardLineZ%02d" % i,
			Vector3(0.0, 0.004, z),
			Vector3(FLOOR_SIZE.x * 0.94, 0.006, 0.012),
			line_color
		)
	for i in range(1, 8):
		var x := -FLOOR_SIZE.x * 0.5 + float(i) * (FLOOR_SIZE.x / 8.0)
		_create_static_box(
			self,
			"FloorBoardLineX%02d" % i,
			Vector3(x, 0.005, 0.0),
			Vector3(0.010, 0.006, FLOOR_SIZE.y * 0.90),
			Color(0.70, 0.68, 0.62)
		)
	var grid_step := 5.0
	var half_x := FLOOR_SIZE.x * 0.5
	var half_z := FLOOR_SIZE.y * 0.5
	var grid_count_x := int(floor(half_x / grid_step))
	var grid_count_z := int(floor(half_z / grid_step))
	var major_grid_color := Color(0.48, 0.46, 0.40)
	var center_grid_color := Color(0.32, 0.42, 0.46)
	for i in range(-grid_count_x, grid_count_x + 1):
		var x := float(i) * grid_step
		var is_center := absf(x) < 0.001
		_create_static_box(
			self,
			"FloorScaleGridX%02d" % (i + grid_count_x),
			Vector3(x, 0.012, 0.0),
			Vector3(0.070 if is_center else 0.040, 0.010, FLOOR_SIZE.y * 0.96),
			center_grid_color if is_center else major_grid_color
		)
	for i in range(-grid_count_z, grid_count_z + 1):
		var z := float(i) * grid_step
		var is_center := absf(z) < 0.001
		_create_static_box(
			self,
			"FloorScaleGridZ%02d" % (i + grid_count_z),
			Vector3(0.0, 0.013, z),
			Vector3(FLOOR_SIZE.x * 0.96, 0.010, 0.070 if is_center else 0.040),
			center_grid_color if is_center else major_grid_color
		)


func _create_room_shell_completion() -> void:
	var half_x := FLOOR_SIZE.x * 0.5
	var half_z := FLOOR_SIZE.y * 0.5
	var ceiling_trim_y := WALL_HEIGHT - 0.05
	for corner in [
		{"name": "BackLeftCornerPost", "x": -half_x + 0.11, "z": half_z - 0.11},
		{"name": "BackRightCornerPost", "x": half_x - 0.11, "z": half_z - 0.11},
		{"name": "FrontLeftCornerPost", "x": -half_x + 0.11, "z": -half_z + 0.11},
		{"name": "FrontRightCornerPost", "x": half_x - 0.11, "z": -half_z + 0.11},
	]:
		_create_static_box(
			self,
			String(corner["name"]),
			Vector3(float(corner["x"]), 0.0, float(corner["z"])),
			Vector3(0.18, WALL_HEIGHT, 0.18),
			Color(0.50, 0.47, 0.41)
		)
	_create_static_box(self, "CeilingTrimBack", Vector3(0.0, ceiling_trim_y, half_z - 0.16), Vector3(FLOOR_SIZE.x * 0.96, 0.12, 0.10), STAGE_TRIM_COLOR)
	_create_static_box(self, "CeilingTrimFront", Vector3(0.0, ceiling_trim_y, -half_z + 0.16), Vector3(FLOOR_SIZE.x * 0.96, 0.12, 0.10), STAGE_TRIM_COLOR)
	_create_static_box(self, "CeilingTrimLeft", Vector3(-half_x + 0.16, ceiling_trim_y, 0.0), Vector3(0.10, 0.12, FLOOR_SIZE.y * 0.96), STAGE_TRIM_COLOR)
	_create_static_box(self, "CeilingTrimRight", Vector3(half_x - 0.16, ceiling_trim_y, 0.0), Vector3(0.10, 0.12, FLOOR_SIZE.y * 0.96), STAGE_TRIM_COLOR)
	_create_static_box(self, "LeftWallMidRail", Vector3(-half_x + 0.09, 1.68, 0.0), Vector3(0.07, 0.10, FLOOR_SIZE.y * 0.86), Color(0.56, 0.53, 0.48))
	_create_static_box(self, "RightWallMidRail", Vector3(half_x - 0.09, 1.68, 0.0), Vector3(0.07, 0.10, FLOOR_SIZE.y * 0.86), Color(0.56, 0.53, 0.48))
	for z in [-3.25, -1.10, 1.10, 3.25]:
		_create_static_box(self, "LeftWallInsetPanel", Vector3(-half_x + 0.105, 0.92, z), Vector3(0.055, 1.26, 1.18), Color(0.62, 0.64, 0.60))
		_create_static_box(self, "RightWallInsetPanel", Vector3(half_x - 0.105, 0.92, z), Vector3(0.055, 1.26, 1.18), Color(0.62, 0.64, 0.60))
	_create_static_box(self, "CeilingLightPanelCenter", Vector3(0.0, WALL_HEIGHT + 0.045, 0.15), Vector3(1.62, 0.018, 0.42), Color(0.96, 0.88, 0.68))
	_create_static_box(self, "CeilingLightPanelBack", Vector3(0.0, WALL_HEIGHT + 0.045, 3.35), Vector3(1.32, 0.018, 0.34), Color(0.90, 0.82, 0.62))


func _create_perimeter_collision_shell() -> void:
	var half_x := FLOOR_SIZE.x * 0.5
	var half_z := FLOOR_SIZE.y * 0.5
	var thickness := 0.22
	_create_collision_box("WallCollisionLeft", Vector3(-half_x, 0.0, 0.0), Vector3(thickness, WALL_HEIGHT, FLOOR_SIZE.y))
	_create_collision_box("WallCollisionRight", Vector3(half_x, 0.0, 0.0), Vector3(thickness, WALL_HEIGHT, FLOOR_SIZE.y))
	_create_collision_box("WallCollisionBack", Vector3(0.0, 0.0, half_z), Vector3(FLOOR_SIZE.x, WALL_HEIGHT, thickness))
	_create_collision_box("WallCollisionFront", Vector3(0.0, 0.0, -half_z), Vector3(FLOOR_SIZE.x, 1.15, thickness))


func _create_collision_box(node_name: String, position: Vector3, size: Vector3) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = node_name
	body.position = position
	add_child(body)
	var collision := CollisionShape3D.new()
	collision.name = "%sShape" % node_name
	var shape := BoxShape3D.new()
	shape.size = size
	collision.shape = shape
	collision.position = Vector3(0.0, size.y * 0.5, 0.0)
	body.add_child(collision)
	return body


func _lumina_avatar_backend() -> String:
	if _lumina_stage10_vrm_candidate_enabled():
		return "vrm"
	var raw := OS.get_environment("LUMINA_AVATAR_BACKEND").strip_edges().to_lower()
	if raw == "":
		return "glb"
	if raw == "vrm":
		return "vrm"
	if raw == "glb":
		return "glb"
	return "glb"


func _lumina_stage10_vrm_candidate_enabled() -> bool:
	return OS.get_environment("LUMINA_STAGE10_VRM_CANDIDATE").strip_edges().to_lower() in ["1", "true", "yes", "on"]


func _lumina_vrm_scene_path() -> String:
	var raw := OS.get_environment("LUMINA_AVATAR_VRM_PATH").strip_edges()
	if raw != "":
		return raw
	if _lumina_stage10_vrm_candidate_enabled():
		return AI_AVATAR_STAGE10_VRM_CANDIDATE_PATH
	return AI_AVATAR_VRM_PATH


func _lumina_vrm_yaw_degrees() -> float:
	var raw := OS.get_environment("LUMINA_AVATAR_VRM_YAW_DEGREES").strip_edges()
	if raw.is_valid_float():
		return raw.to_float()
	# Route B candidate visual faces +Z (opposite of Stella's -Z convention).
	var path := OS.get_environment("LUMINA_AVATAR_VRM_PATH").strip_edges().to_lower()
	if path.contains("route_b") and path.contains("candidate"):
		return 0.0
	return 180.0


func _lumina_env_string(env_name: String, fallback: String) -> String:
	var raw := OS.get_environment(env_name).strip_edges()
	if raw != "":
		return raw
	return fallback


func _lumina_glb_manifest_path() -> String:
	var raw := OS.get_environment("LUMINA_AVATAR_GLB_MANIFEST_PATH").strip_edges()
	if raw != "":
		return raw
	return AI_AVATAR_GLB_MANIFEST_PATH


func _lumina_glb_profile_root() -> String:
	var raw := OS.get_environment("LUMINA_AVATAR_GLB_PROFILE_ROOT").strip_edges()
	if raw == "":
		raw = OS.get_environment("LUMINA_GLB_PROFILE_ROOT").strip_edges()
	if raw != "":
		return raw
	return AI_AVATAR_GLB_PROFILE_ROOT


func _lumina_glb_scene_cache_root() -> String:
	var raw := OS.get_environment("LUMINA_AVATAR_GLB_SCENE_CACHE_ROOT").strip_edges()
	if raw == "":
		raw = OS.get_environment("LUMINA_GLB_SCENE_CACHE_ROOT").strip_edges()
	if raw != "":
		return raw
	return AI_AVATAR_GLB_SCENE_CACHE_ROOT


func _lumina_glb_natural_policy_path() -> String:
	var raw := OS.get_environment("LUMINA_AVATAR_GLB_NATURAL_POLICY_PATH").strip_edges()
	if raw == "":
		raw = OS.get_environment("LUMINA_GLB_NATURAL_POLICY_PATH").strip_edges()
	if raw != "":
		return raw
	return AI_AVATAR_GLB_NATURAL_POLICY_PATH


func _lumina_glb_yaw_degrees() -> float:
	var raw := OS.get_environment("LUMINA_AVATAR_GLB_YAW_DEGREES").strip_edges()
	if raw.is_valid_float():
		return raw.to_float()
	return 180.0


func _create_avatar() -> CharacterBody3D:
	var avatar := CharacterBody3D.new()
	avatar.name = "LuminaAvatar"
	avatar.set_script(DemoAvatarScript)
	avatar.position = Vector3.ZERO

	var body := MeshInstance3D.new()
	body.name = "Body"
	var capsule := CapsuleMesh.new()
	capsule.radius = 0.32
	capsule.height = 1.35
	body.mesh = capsule
	body.position = Vector3(0.0, 0.78, 0.0)
	avatar.add_child(body)

	var collision := CollisionShape3D.new()
	collision.name = "CollisionShape3D"
	var shape := CapsuleShape3D.new()
	shape.radius = 0.32
	shape.height = 1.35
	collision.shape = shape
	collision.position = Vector3(0.0, 0.78, 0.0)
	avatar.add_child(collision)

	var adapter := Node3D.new()
	var avatar_backend := _lumina_avatar_backend()
	if avatar_backend == "glb":
		adapter.name = "SIGLBAvatarAdapter"
		adapter.set_script(GLBAdapterScript)
	else:
		adapter.name = "SIVRMAvatarAdapter"
		adapter.set_script(VRMAdapterScript)
	avatar.add_child(adapter)
	if avatar_backend == "glb":
		adapter.set("glb_manifest_path", _lumina_glb_manifest_path())
		adapter.set("glb_profile_root", _lumina_glb_profile_root())
		adapter.set("glb_scene_cache_root", _lumina_glb_scene_cache_root())
		adapter.set("natural_animation_policy_path", _lumina_glb_natural_policy_path())
		adapter.set("default_state", "idle")
		adapter.set("walk_state", "walk")
		adapter.set("talk_state", "talk_standing")
		adapter.set("sit_state", "sit_idle")
		adapter.set("stand_state", "stand_up_primary")
		adapter.set("target_glb_height", 1.64)
		adapter.set("model_yaw_degrees", _lumina_glb_yaw_degrees())
	else:
		adapter.set("vrm_scene_path", _lumina_vrm_scene_path())
		adapter.set("model_yaw_degrees", _lumina_vrm_yaw_degrees())
	adapter.set("fallback_body_path", adapter.get_path_to(body))
	adapter.set("model_scale", 1.0)
	adapter.set("align_feet_to_origin", true)
	adapter.set("enable_realistic_model_shading", true)
	adapter.set("enable_simple_idle_pose", true)
	adapter.set("enable_vrma_idle_motion", false)
	adapter.set("idle_vrma_path", "res://assets/animation/idle_loop.vrma")
	adapter.set("vrma_idle_blend", 0.0)
	adapter.set("vrma_idle_playback_speed", 1.0)
	adapter.set("enable_vrma_motion_bank", true)
	adapter.set("animation_asset_dir", "res://assets/animation")
	adapter.set("walk_vrma_path", _lumina_env_string("LUMINA_VRM_WALK_VRMA_PATH", "res://assets/animation/walk_loop.vrma"))
	adapter.set("talk_vrma_path", _lumina_env_string("LUMINA_VRM_TALK_VRMA_PATH", "res://assets/animation/talk_loop.vrma"))
	adapter.set("enable_vrma_talk_motion", false)
	adapter.set("gesture_wave_vrma_path", _lumina_env_string("LUMINA_VRM_GESTURE_WAVE_VRMA_PATH", "res://assets/animation/gesture_wave.vrma"))
	adapter.set("gesture_point_vrma_path", _lumina_env_string("LUMINA_VRM_GESTURE_POINT_VRMA_PATH", ""))
	adapter.set("sit_vrma_path", _lumina_env_string("LUMINA_VRM_SIT_VRMA_PATH", "res://assets/animation/sit_loop.vrma"))
	adapter.set("stand_vrma_path", _lumina_env_string("LUMINA_VRM_STAND_VRMA_PATH", "res://assets/animation/stand_loop.vrma"))
	adapter.set("enable_fumi2kick_motion_audition", true)
	adapter.set("enable_vroid_official_motion_audition", true)
	adapter.set("vrma_motion_blend", 0.86)
	adapter.set("vrma_motion_playback_speed", 1.0)
	adapter.set("idle_pose_blend_speed", 5.5)
	adapter.set("idle_spine_pitch_deg", -1.2)
	adapter.set("idle_breath_amount", 0.005)
	adapter.set("idle_breath_speed", 1.45)
	adapter.set("idle_spine_wave_deg", 0.22)
	adapter.set("idle_neck_pitch_deg", 0.35)
	adapter.set("idle_head_bob_deg", 0.08)
	adapter.set("idle_head_roll_deg", 0.05)
	adapter.set("idle_weight_shift_deg", 0.22)
	adapter.set("idle_weight_shift_speed", 0.38)
	adapter.set("idle_shoulder_pitch_deg", 84.0)
	adapter.set("idle_shoulder_forward_deg", 66.0)
	adapter.set("idle_forearm_pitch_deg", -6.0)
	adapter.set("idle_arm_sway_deg", 0.09)
	adapter.set("shoulder_guard_deg", 110.0)
	adapter.set("talk_spine_pitch_deg", 1.7)
	adapter.set("talk_spine_wave_deg", 1.0)
	adapter.set("talk_chest_pitch_deg", 3.6)
	adapter.set("talk_chest_wave_deg", 0.65)
	adapter.set("talk_neck_pitch_deg", 1.4)
	adapter.set("talk_head_pitch_deg", 0.45)
	adapter.set("talk_wave_hz", 3.8)
	adapter.set("talk_wave_deg", 0.72)
	adapter.set("talk_root_bob_amount", 0.0045)
	adapter.set("talk_pose_scale", 0.50)
	adapter.set("talk_posture_blend_speed", 6.4)
	adapter.set("talk_shoulder_motion_deg", 3.2)
	adapter.set("talk_forearm_motion_deg", 2.0)
	adapter.set("talk_hand_motion_deg", 1.4)
	adapter.set("gesture_point_shoulder_deg", -38.0)
	adapter.set("gesture_point_hand_deg", -8.0)
	adapter.set("sit_hip_bend_deg", -62.0)
	adapter.set("sit_knee_bend_deg", 88.0)
	adapter.set("sit_root_drop_amount", 0.36)
	adapter.set("sit_spine_pitch_deg", -6.0)
	adapter.set("sit_arm_rest_deg", 18.0)
	adapter.set("enable_procedural_idle_motion", true)
	adapter.set("enable_procedural_walk_motion", true)
	adapter.set("walk_bob_amount", 0.024)
	adapter.set("walk_step_cycle_hz", 1.78)
	adapter.set("walk_root_roll_deg", 0.62)
	adapter.set("walk_root_pitch_deg", 0.55)
	adapter.set("walk_spine_pitch_deg", 2.2)
	adapter.set("walk_spine_wave_deg", 0.72)
	adapter.set("walk_chest_wave_deg", 0.55)
	adapter.set("walk_shoulder_pitch_deg", 84.0)
	adapter.set("walk_shoulder_swing_deg", 3.8)
	adapter.set("walk_forearm_pitch_deg", 2.4)
	adapter.set("walk_forearm_swing_deg", 2.0)
	adapter.set("walk_neck_pitch_deg", 0.8)
	adapter.set("walk_head_pitch_deg", 0.3)
	adapter.set("walk_leg_swing_deg", 16.0)
	adapter.set("walk_knee_bend_deg", 12.0)
	adapter.set("walk_foot_lift_deg", 5.2)
	adapter.set("walk_reference_speed", 2.25)
	adapter.set("walk_speed_ratio_min", 0.55)
	adapter.set("walk_speed_ratio_max", 1.6)
	adapter.set("enable_tts_audio", true)
	adapter.set("tts_voice_url", "http://127.0.0.1:5056/voice-lipsync")
	adapter.set("tts_speed", 0.96)
	adapter.set("tts_volume_db", _tts_volume_db_from_env())
	adapter.set("lipsync_shape_strength", 0.88)
	adapter.set("lipsync_follow_speed", 88.0)
	adapter.set("lipsync_silence_threshold", 0.014)
	adapter.set("lipsync_profile_fps", 75.0)
	adapter.set("lipsync_volume_scale", 1.12)
	adapter.set("lipsync_time_offset_sec", -0.015)
	adapter.set("enable_procedural_gaze_motion", true)
	adapter.set("gaze_blend_speed", 7.0)
	adapter.set("gaze_idle_scan_deg", 1.7)
	adapter.set("gaze_micro_saccade_deg", 0.25)

	avatar.set("body_mesh_path", avatar.get_path_to(body))
	avatar.set("vrm_adapter_path", avatar.get_path_to(adapter))
	avatar.set("default_gesture_duration", 1.45)
	avatar.set("user_look_target_height_offset", 1.50)
	avatar.set("user_attention_hold_seconds", 600.0)
	avatar.set("user_attention_soft_expression", "relaxed")
	avatar.set("user_attention_soft_expression_intensity", 0.16)
	var navigation_agent := NavigationAgent3D.new()
	navigation_agent.name = "NavigationAgent3D"
	avatar.add_child(navigation_agent)
	avatar.set("navigation_agent_path", avatar.get_path_to(navigation_agent))
	add_child(avatar)
	return avatar


func _create_player() -> Node3D:
	var player := CharacterBody3D.new()
	player.name = "UserPlayer"
	player.set_script(PlayerAvatarScript)
	player.set("eye_height", 1.50)
	var player_half_floor := FLOOR_SIZE * 0.5
	var player_edge_margin := 0.75
	if _future_room_v008_as_active_room_enabled() and not _future_room_v008_room_only_preserve_legacy_environment():
		player_half_floor = Vector2(FUTURE_ROOM_V008_HALF_X, FUTURE_ROOM_V008_HALF_Z)
		player_edge_margin = 0.0
	player.set("min_x", -player_half_floor.x + player_edge_margin)
	player.set("max_x", player_half_floor.x - player_edge_margin)
	player.set("min_z", -player_half_floor.y + player_edge_margin)
	player.set("max_z", player_half_floor.y - player_edge_margin)
	player.position = Vector3(0.0, 0.0, -2.05)
	var initial_player_direction := Vector3.ZERO - player.position
	if _future_room_v008_as_active_room_enabled() and not _future_room_v008_room_only_preserve_legacy_environment():
		player.position = FUTURE_ROOM_V008_USER_HOME_POSITION
		player.set("home_position", FUTURE_ROOM_V008_USER_HOME_POSITION)
		player.set("near_lumina_position", FUTURE_ROOM_V008_USER_NEAR_POSITION)
		player.set("observe_sofa_position", FUTURE_ROOM_V008_USER_OBSERVE_POSITION)
		initial_player_direction = FUTURE_ROOM_V008_LUMINA_POSITION - player.position
	player.rotation.y = atan2(-initial_player_direction.x, -initial_player_direction.z)
	player.add_to_group("player")

	var collision := CollisionShape3D.new()
	collision.name = "PlayerCollision"
	var collision_shape := CapsuleShape3D.new()
	collision_shape.radius = 0.30
	collision_shape.height = 1.48
	collision.shape = collision_shape
	collision.position = Vector3(0.0, 0.82, 0.0)
	player.add_child(collision)

	var visual_root := Node3D.new()
	visual_root.name = "PlayerAvatarVisual"
	player.add_child(visual_root)

	var ring_outline := MeshInstance3D.new()
	ring_outline.name = "FloorMarkerOutline"
	var ring_outline_mesh := CylinderMesh.new()
	ring_outline_mesh.top_radius = 0.42
	ring_outline_mesh.bottom_radius = 0.42
	ring_outline_mesh.height = 0.024
	ring_outline_mesh.radial_segments = 48
	ring_outline.material_override = _build_user_marker_material(USER_PLAYER_MARKER_ACCENT_COLOR, 0.4, 0.08)
	ring_outline.mesh = ring_outline_mesh
	ring_outline.position = Vector3(0.0, 0.012, 0.0)
	ring_outline.scale = Vector3(1.0, 0.15, 1.0)
	ring_outline.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	visual_root.add_child(ring_outline)

	var floor_marker := MeshInstance3D.new()
	floor_marker.name = "FloorMarker"
	var ring := CylinderMesh.new()
	ring.top_radius = 0.32
	ring.bottom_radius = 0.32
	ring.height = 0.018
	ring.radial_segments = 48
	floor_marker.mesh = ring
	floor_marker.position = Vector3(0.0, 0.018, 0.0)
	floor_marker.material_override = _build_user_marker_material(USER_PLAYER_MARKER_BASE_COLOR, 0.34, 0.12)
	floor_marker.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	visual_root.add_child(floor_marker)

	var fallback_body := MeshInstance3D.new()
	fallback_body.name = "PlayerFallbackBody"
	var fallback_capsule := CapsuleMesh.new()
	fallback_capsule.radius = 0.22
	fallback_capsule.height = 1.18
	fallback_body.mesh = fallback_capsule
	fallback_body.position = Vector3(0.0, 0.86, 0.0)
	fallback_body.material_override = _build_user_marker_material(Color(0.18, 0.38, 0.62), 0.62, 0.05)
	visual_root.add_child(fallback_body)

	var adapter := Node3D.new()
	adapter.name = "PlayerVRMAvatarAdapter"
	adapter.set_script(VRMAdapterScript)
	visual_root.add_child(adapter)
	adapter.set("vrm_scene_path", PLAYER_VRM_PATH)
	adapter.set("fallback_body_path", adapter.get_path_to(fallback_body))
	adapter.set("model_scale", 1.0)
	adapter.set("model_yaw_degrees", 180.0)
	adapter.set("align_feet_to_origin", true)
	adapter.set("enable_realistic_model_shading", true)
	adapter.set("enable_tts_audio", false)
	adapter.set("enable_simple_idle_pose", true)
	adapter.set("enable_vrma_idle_motion", false)
	adapter.set("enable_vrma_motion_bank", false)
	adapter.set("idle_pose_blend_speed", 5.2)
	adapter.set("idle_spine_pitch_deg", -0.8)
	adapter.set("idle_breath_amount", 0.004)
	adapter.set("idle_breath_speed", 1.35)
	adapter.set("idle_weight_shift_deg", 0.18)
	adapter.set("idle_weight_shift_speed", 0.32)
	adapter.set("idle_shoulder_pitch_deg", 84.0)
	adapter.set("idle_shoulder_forward_deg", 66.0)
	adapter.set("idle_forearm_pitch_deg", -6.0)
	adapter.set("idle_arm_sway_deg", 0.15)
	adapter.set("enable_procedural_idle_motion", true)
	adapter.set("enable_procedural_walk_motion", true)
	adapter.set("walk_bob_amount", 0.02)
	adapter.set("walk_step_cycle_hz", 1.75)
	adapter.set("walk_root_roll_deg", 0.6)
	adapter.set("walk_root_pitch_deg", 0.35)
	adapter.set("walk_spine_pitch_deg", 1.6)
	adapter.set("walk_spine_wave_deg", 0.55)
	adapter.set("walk_chest_wave_deg", 0.45)
	adapter.set("walk_shoulder_swing_deg", 2.6)
	adapter.set("walk_forearm_swing_deg", 1.4)
	adapter.set("walk_leg_swing_deg", 11.0)
	adapter.set("walk_knee_bend_deg", 8.0)
	adapter.set("walk_foot_lift_deg", 3.6)
	adapter.set("walk_reference_speed", 2.2)

	player.set("visual_root_path", NodePath("PlayerAvatarVisual"))
	player.set("vrm_adapter_path", NodePath("PlayerAvatarVisual/PlayerVRMAvatarAdapter"))
	add_child(player)

	_player_avatar = player
	return player


func _build_user_marker_material(base_color: Color, roughness: float, emission_energy: float) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = base_color
	material.roughness = clampf(roughness, 0.2, 1.0)
	material.metallic = 0.0
	material.emission = base_color
	material.emission_energy = emission_energy
	return material


func _create_prop(parent: Node, node_name: String, object_type: String, position: Vector3, size: Vector3, color: Color) -> Node3D:
	var prop := Node3D.new()
	prop.name = node_name
	prop.position = position
	prop.add_to_group("si_addressable")
	var canonical_type := String(object_type).strip_edges().to_lower()
	var display_name := String(node_name).strip_edges()
	prop.set_meta("si_type", canonical_type)
	prop.set_meta("si_display_name", display_name if not display_name.is_empty() else canonical_type)
	prop.set_meta("si_can_sit", _is_sittable_type(canonical_type, display_name))
	prop.set_meta("si_can_approach", _is_approachable_type(canonical_type))
	prop.set_meta("si_addressable_radius", _addressable_radius_from_size(size))
	prop.set_meta("si_addressable_clearance", 0.15)
	parent.add_child(prop)
	var navigation_center_y := prop.global_position.y + size.y * 0.5
	var is_navigation_furniture := canonical_type in [
		"table", "chair", "sofa", "bench", "stool", "couch", "lounge",
		"plant", "desk", "shelf", "console", "landmark", "sign", "small_item",
	]
	if (
		is_navigation_furniture
		and size.y >= 0.16
		and navigation_center_y - size.y * 0.5 <= 0.45
		and navigation_center_y + size.y * 0.5 >= -0.12
	):
		prop.add_to_group("si_navigation_obstacle")
		prop.set_meta("si_navigation_half_extents", Vector2(size.x / 2.0, size.z / 2.0))
		prop.set_meta("si_navigation_height", size.y)
		prop.set_meta("si_navigation_center_y", navigation_center_y)

	if USE_PROCEDURAL_EXHIBIT_FURNITURE and _build_procedural_furniture_prop(prop, canonical_type, size, color):
		return prop

	if _build_kenney_furniture_prop(prop, canonical_type, size, color):
		_add_kenney_readability_details(prop, object_type, size, color)
		return prop

	match canonical_type:
		"table":
			_build_table_prop(prop, size, color)
		"chair":
			_build_chair_prop(prop, size, color)
		"sofa":
			_build_sofa_prop(prop, size, color)
		"plant":
			_build_plant_prop(prop, size, color)
		_:
			_add_box_part(prop, "Mesh", Vector3(0.0, size.y / 2.0, 0.0), size, color)
	return prop


func _tag_spatial_object(node: Node3D, display_name: String, area: String, aliases: Array = [], search_hint: String = "") -> Node3D:
	if node == null:
		return node
	var safe_display_name := String(display_name).strip_edges()
	var safe_area := String(area).strip_edges()
	var safe_hint := String(search_hint).strip_edges()
	var normalized_aliases := []
	for alias in aliases:
		var alias_text := String(alias).strip_edges()
		if not alias_text.is_empty() and not normalized_aliases.has(alias_text):
			normalized_aliases.append(alias_text)
	if not safe_display_name.is_empty():
		node.set_meta("si_display_name", safe_display_name)
	if not safe_area.is_empty():
		node.set_meta("si_area", safe_area)
	if not normalized_aliases.is_empty():
		node.set_meta("si_aliases", normalized_aliases)
	if not safe_hint.is_empty():
		node.set_meta("si_search_hint", safe_hint)
	if node.is_in_group("si_addressable"):
		var node_type := String(node.get_meta("si_type", "")).strip_edges().to_lower()
		if not ["wall", "path", "rug"].has(node_type):
			node.set_meta("si_can_approach", true)
	return node


func _build_procedural_furniture_prop(prop: Node3D, object_type: String, size: Vector3, color: Color) -> bool:
	match object_type:
		"table":
			_build_table_prop(prop, size, color)
			return true
		"chair":
			_build_chair_prop(prop, size, color)
			return true
		"sofa":
			_build_sofa_prop(prop, size, color)
			return true
		"plant":
			_build_plant_prop(prop, size, color)
			return true
	return false


func _is_sittable_type(object_type: String, display_name: String = "") -> bool:
	var normalized_type := String(object_type).strip_edges().to_lower()
	if normalized_type.is_empty():
		return false
	if normalized_type in ["table", "plant", "wall", "rug", "path", "light", "art"]:
		return false
	if SIT_FURNITURE_TYPES.get(normalized_type, false):
		return true
	var normalized_label := ("%s %s" % [display_name.to_lower(), normalized_type]).strip_edges().to_lower()
	var seat_keywords := ["chair", "sofa", "bench", "couch", "stool", "lounge", "seat", "椅子", "ベンチ", "ソファ", "イス", "いす"]
	for keyword in seat_keywords:
		if normalized_label.contains(keyword):
			return true
	return false


func _is_approachable_type(object_type: String) -> bool:
	var normalized_type := String(object_type).strip_edges().to_lower()
	return APPROACHABLE_FURNITURE_TYPES.get(normalized_type, false)


func _build_kenney_furniture_prop(prop: Node3D, object_type: String, size: Vector3, color: Color) -> bool:
	var key := object_type.to_lower()
	if not KENNEY_FURNITURE_LIBRARY.has(key):
		return false

	var spec: Dictionary = KENNEY_FURNITURE_LIBRARY[key]
	var asset_path := String(spec.get("asset_path", ""))
	if asset_path.is_empty():
		return false

	var packed := load(asset_path) as PackedScene
	if packed == null:
		push_warning("[furniture] Kenney load failed: %s" % asset_path)
		return false

	var raw_instance := packed.instantiate()
	if raw_instance == null:
		push_warning("[furniture] Kenney instantiation failed: %s" % asset_path)
		return false

	var instance := raw_instance as Node3D
	if instance == null:
		raw_instance.queue_free()
		push_warning("[furniture] Kenney root node is not Node3D: %s" % asset_path)
		return false

	prop.add_child(instance)
	if _fit_and_position_kenney_instance(instance, size, spec):
		_apply_kenney_base_material(instance, object_type, color)
		return true

	instance.queue_free()
	push_warning("[furniture] Kenney model has no usable mesh bounds: %s" % asset_path)
	return false


func _apply_kenney_base_material(instance: Node3D, object_type: String, color: Color) -> void:
	var key := object_type.to_lower()
	var tint := color
	match key:
		"table":
			tint = Color(0.47, 0.32, 0.18)
		"chair":
			tint = Color(0.36, 0.31, 0.27)
		"sofa":
			tint = Color(0.34, 0.43, 0.55)
	var material := _build_furniture_material(tint, 0.82)
	var stack: Array[Node] = [instance]
	while stack.size() > 0:
		var current := stack.pop_back() as Node
		for child in current.get_children():
			stack.append(child)
		var geometry := current as GeometryInstance3D
		if geometry != null:
			geometry.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
			if key != "plant":
				geometry.material_override = material.duplicate() as Material


func _add_kenney_readability_details(prop: Node3D, object_type: String, size: Vector3, color: Color) -> void:
	match object_type:
		"table":
			var top_y := 0.68
			var wood_dark := Color(0.28, 0.18, 0.10)
			var wood_light := Color(0.62, 0.42, 0.24)
			_add_wood_grain(prop, "KenneyTableGrain", size, top_y + 0.035, wood_dark, wood_light)
			_add_box_part(prop, "KenneyTableBookPages", Vector3(-0.35, top_y + 0.050, -0.12), Vector3(0.42, 0.020, 0.28), Color(0.88, 0.84, 0.73), 0.86, Vector3(0.0, 10.0, 0.0))
			_add_box_part(prop, "KenneyTableBookCover", Vector3(-0.35, top_y + 0.073, -0.12), Vector3(0.45, 0.014, 0.31), Color(0.18, 0.30, 0.43), 0.78, Vector3(0.0, 10.0, 0.0))
			_add_cylinder_part(prop, "KenneyTableSaucer", Vector3(0.36, top_y + 0.046, 0.15), 0.13, 0.13, 0.016, Color(0.78, 0.75, 0.67), 0.62)
			_add_cylinder_part(prop, "KenneyTableCup", Vector3(0.36, top_y + 0.126, 0.15), 0.068, 0.057, 0.145, Color(0.86, 0.83, 0.75), 0.58)
			_add_cylinder_part(prop, "KenneyCupCoffee", Vector3(0.36, top_y + 0.205, 0.15), 0.051, 0.051, 0.010, Color(0.19, 0.12, 0.08), 0.66)
		"chair":
			var cushion_color := Color(0.56, 0.50, 0.43)
			var frame_color := Color(0.22, 0.17, 0.13)
			_add_box_part(prop, "KenneyChairSeatCushion", Vector3(0.0, 0.48, 0.04), Vector3(size.x * 0.78, 0.050, size.z * 0.58), cushion_color, 0.94)
			_add_box_part(prop, "KenneyChairBackCushion", Vector3(0.0, 0.82, -size.z * 0.36), Vector3(size.x * 0.72, 0.30, 0.040), _shift_color(cushion_color, -0.08, -0.07, -0.06), 0.94, Vector3(6.0, 0.0, 0.0))
			_add_box_part(prop, "KenneyChairSeatFrontEdge", Vector3(0.0, 0.43, size.z * 0.36), Vector3(size.x * 0.85, 0.050, 0.045), frame_color, 0.86)
		"sofa":
			var fabric := Color(0.42, 0.52, 0.65)
			var seam := Color(0.16, 0.20, 0.27)
			_add_box_part(prop, "KenneySofaSeatLeftPad", Vector3(-size.x * 0.23, 0.45, -0.08), Vector3(size.x * 0.38, 0.075, size.z * 0.54), fabric, 0.96)
			_add_box_part(prop, "KenneySofaSeatRightPad", Vector3(size.x * 0.23, 0.45, -0.08), Vector3(size.x * 0.38, 0.075, size.z * 0.54), fabric, 0.96)
			_add_box_part(prop, "KenneySofaCenterSeam", Vector3(0.0, 0.50, -0.08), Vector3(0.022, 0.020, size.z * 0.58), seam, 0.98)
			_add_box_part(prop, "KenneySofaThrowPillowLeft", Vector3(-size.x * 0.33, 0.72, size.z * 0.11), Vector3(0.31, 0.28, 0.075), Color(0.76, 0.58, 0.39), 0.94, Vector3(-8.0, 0.0, 8.0))
			_add_box_part(prop, "KenneySofaThrowPillowRight", Vector3(size.x * 0.25, 0.71, size.z * 0.10), Vector3(0.29, 0.26, 0.075), Color(0.37, 0.54, 0.50), 0.94, Vector3(-7.0, 0.0, -7.0))
		"plant":
			_add_cylinder_part(prop, "KenneyPlantSoil", Vector3(0.0, 0.58, 0.0), size.x * 0.24, size.x * 0.24, 0.018, Color(0.16, 0.10, 0.07), 0.95)


func _add_kenney_scene_model(parent: Node3D, asset_path: String, uniform_scale: float) -> bool:
	var packed := load(asset_path) as PackedScene
	if packed == null:
		push_warning("[furniture] Kenney detail load failed: %s" % asset_path)
		return false

	var raw_instance := packed.instantiate()
	if raw_instance == null:
		push_warning("[furniture] Kenney detail instantiation failed: %s" % asset_path)
		return false

	var instance := raw_instance as Node3D
	if instance == null:
		raw_instance.queue_free()
		push_warning("[furniture] Kenney detail root node is not Node3D: %s" % asset_path)
		return false

	parent.add_child(instance)
	if _fit_and_position_kenney_instance(instance, Vector3.ONE, {"uniform_scale": uniform_scale}):
		return true

	instance.queue_free()
	push_warning("[furniture] Kenney detail model has no usable mesh bounds: %s" % asset_path)
	return false


func _fit_and_position_kenney_instance(instance: Node3D, target_size: Vector3, spec: Dictionary) -> bool:
	var bounds := _compute_node3d_aabb(instance)
	if bounds.size.x <= 0.0001 or bounds.size.y <= 0.0001 or bounds.size.z <= 0.0001:
		return false

	var min_scale := 0.05
	var max_scale := 5.0
	if spec.has("uniform_scale"):
		var uniform_scale := clampf(float(spec.get("uniform_scale")), min_scale, max_scale)
		instance.scale = Vector3.ONE * uniform_scale
	else:
		var scale := Vector3(
			target_size.x / bounds.size.x,
			target_size.y / bounds.size.y,
			target_size.z / bounds.size.z
		)
		instance.scale = Vector3(
			clampf(scale.x, min_scale, max_scale),
			clampf(scale.y, min_scale, max_scale),
			clampf(scale.z, min_scale, max_scale)
		)

	var center := bounds.position + bounds.size * 0.5
	instance.position = Vector3(
		-center.x * instance.scale.x,
		-bounds.position.y * instance.scale.y,
		-center.z * instance.scale.z
	)
	return true


func _compute_node3d_aabb(node: Node3D) -> AABB:
	var bounds: AABB
	var initialized := false
	var stack: Array[Node] = [node]

	while stack.size() > 0:
		var current := stack.pop_back() as Node
		for child in current.get_children():
			stack.append(child)

		var geometry := current as GeometryInstance3D
		if geometry == null:
			continue
		var raw_aabb := geometry.get_aabb()
		if raw_aabb.size == Vector3.ZERO:
			continue

		var transformed_aabb := _transform_aabb_to_parent(
			raw_aabb,
			node.global_transform.affine_inverse() * geometry.global_transform
		)
		if not initialized:
			bounds = transformed_aabb
			initialized = true
		else:
			bounds = bounds.merge(transformed_aabb)

	return bounds if initialized else AABB(Vector3.ZERO, Vector3.ZERO)


func _transform_aabb_to_parent(aabb: AABB, transform: Transform3D) -> AABB:
	var min_x := 1e20
	var min_y := 1e20
	var min_z := 1e20
	var max_x := -1e20
	var max_y := -1e20
	var max_z := -1e20

	var points := [
		aabb.position,
		aabb.position + Vector3(aabb.size.x, 0.0, 0.0),
		aabb.position + Vector3(0.0, aabb.size.y, 0.0),
		aabb.position + Vector3(0.0, 0.0, aabb.size.z),
		aabb.position + Vector3(aabb.size.x, aabb.size.y, 0.0),
		aabb.position + Vector3(aabb.size.x, 0.0, aabb.size.z),
		aabb.position + Vector3(0.0, aabb.size.y, aabb.size.z),
		aabb.position + aabb.size,
	]
	for point in points:
		var point_position: Vector3 = point
		var p: Vector3 = transform * point_position
		if p.x < min_x:
			min_x = p.x
		if p.x > max_x:
			max_x = p.x
		if p.y < min_y:
			min_y = p.y
		if p.y > max_y:
			max_y = p.y
		if p.z < min_z:
			min_z = p.z
		if p.z > max_z:
			max_z = p.z

	return AABB(
		Vector3(min_x, min_y, min_z),
		Vector3(max_x - min_x, max_y - min_y, max_z - min_z)
	)


func _build_table_prop(prop: Node, size: Vector3, color: Color) -> void:
	var wood_dark := _shift_color(color, -0.12, -0.10, -0.08)
	var wood_mid := _shift_color(color, -0.02, -0.01, 0.01)
	var wood_light := _shift_color(color, 0.10, 0.08, 0.04)
	var paper_color := Color(0.88, 0.84, 0.73)
	var top_y := 0.68
	var top_thickness := 0.055
	var leg_height := top_y - top_thickness * 0.5
	var leg_radius := 0.025
	_add_box_part(prop, "Mesh", Vector3(0.0, top_y, 0.0), Vector3(size.x, top_thickness, size.z), wood_light, 0.72)
	_add_box_part(prop, "TableTopWarmPatch", Vector3(-0.18, top_y + 0.032, -0.05), Vector3(size.x * 0.68, 0.007, size.z * 0.55), _shift_color(wood_light, 0.055, 0.035, -0.005), 0.7)
	_add_box_part(prop, "TableTopCoolPatch", Vector3(0.34, top_y + 0.034, 0.18), Vector3(size.x * 0.32, 0.006, size.z * 0.30), wood_mid, 0.74)
	_add_wood_grain(prop, "TableGrain", size, top_y + 0.036, wood_dark, wood_light)
	_add_box_part(prop, "FrontRoundedEdge", Vector3(0.0, top_y - 0.006, -size.z * 0.5 - 0.018), Vector3(size.x + 0.055, 0.034, 0.036), wood_dark, 0.76)
	_add_box_part(prop, "BackRoundedEdge", Vector3(0.0, top_y - 0.006, size.z * 0.5 + 0.018), Vector3(size.x + 0.055, 0.034, 0.036), wood_dark, 0.76)
	_add_box_part(prop, "LeftRoundedEdge", Vector3(-size.x * 0.5 - 0.018, top_y - 0.006, 0.0), Vector3(0.036, 0.034, size.z + 0.055), wood_dark, 0.76)
	_add_box_part(prop, "RightRoundedEdge", Vector3(size.x * 0.5 + 0.018, top_y - 0.006, 0.0), Vector3(0.036, 0.034, size.z + 0.055), wood_dark, 0.76)
	_add_box_part(prop, "FrontChamferHighlight", Vector3(0.0, top_y + 0.018, -size.z * 0.5 - 0.039), Vector3(size.x + 0.04, 0.010, 0.010), wood_light, 0.7)
	_add_capsule_part(prop, "TableFrontBevelRound", Vector3(0.0, top_y + 0.018, -size.z * 0.5 - 0.035), 0.018, size.x + 0.030, _shift_color(wood_light, -0.02, -0.01, 0.0), 0.74, Vector3(0.0, 0.0, 90.0))
	_add_capsule_part(prop, "TableBackBevelRound", Vector3(0.0, top_y + 0.018, size.z * 0.5 + 0.035), 0.018, size.x + 0.030, _shift_color(wood_light, -0.04, -0.03, -0.01), 0.78, Vector3(0.0, 0.0, 90.0))
	_add_capsule_part(prop, "TableLeftBevelRound", Vector3(-size.x * 0.5 - 0.035, top_y + 0.018, 0.0), 0.018, size.z + 0.030, _shift_color(wood_light, -0.04, -0.03, -0.01), 0.78, Vector3(90.0, 0.0, 0.0))
	_add_capsule_part(prop, "TableRightBevelRound", Vector3(size.x * 0.5 + 0.035, top_y + 0.018, 0.0), 0.018, size.z + 0.030, _shift_color(wood_light, -0.04, -0.03, -0.01), 0.78, Vector3(90.0, 0.0, 0.0))
	for x in [-size.x * 0.5 - 0.033, size.x * 0.5 + 0.033]:
		for z in [-size.z * 0.5 - 0.033, size.z * 0.5 + 0.033]:
			_add_sphere_part(prop, "TableCornerSoftCap", Vector3(x, top_y + 0.018, z), Vector3(0.020, 0.012, 0.020), _shift_color(wood_light, -0.03, -0.02, -0.01), 0.78)
	for x in [-size.x * 0.5 + 0.18, size.x * 0.5 - 0.18]:
		for z in [-size.z * 0.5 + 0.16, size.z * 0.5 - 0.16]:
			_add_cylinder_part(prop, "TableLeg", Vector3(x, leg_height * 0.5, z), leg_radius, leg_radius * 1.22, leg_height, wood_dark, 0.82)
			_add_cylinder_part(prop, "TableLegFootCap", Vector3(x, 0.035, z), leg_radius * 1.45, leg_radius * 1.62, 0.025, Color(0.12, 0.09, 0.065), 0.72)
	_add_box_part(prop, "UnderTableRailFront", Vector3(0.0, 0.55, -size.z * 0.5 + 0.13), Vector3(size.x - 0.34, 0.034, 0.030), wood_dark, 0.86)
	_add_box_part(prop, "UnderTableRailBack", Vector3(0.0, 0.55, size.z * 0.5 - 0.13), Vector3(size.x - 0.34, 0.034, 0.030), wood_dark, 0.86)
	_add_box_part(prop, "UnderTableRailLeft", Vector3(-size.x * 0.5 + 0.17, 0.54, 0.0), Vector3(0.030, 0.032, size.z - 0.32), wood_dark, 0.86)
	_add_box_part(prop, "UnderTableRailRight", Vector3(size.x * 0.5 - 0.17, 0.54, 0.0), Vector3(0.030, 0.032, size.z - 0.32), wood_dark, 0.86)
	_add_box_part(prop, "UnderTableDrawerFace", Vector3(0.0, 0.492, -size.z * 0.5 - 0.020), Vector3(size.x * 0.54, 0.090, 0.026), _shift_color(wood_dark, 0.04, 0.03, 0.02), 0.82)
	_add_cylinder_part(prop, "UnderTableDrawerHandle", Vector3(0.0, 0.495, -size.z * 0.5 - 0.042), 0.012, 0.012, size.x * 0.24, Color(0.10, 0.075, 0.055), 0.62, Vector3(0.0, 0.0, 90.0))
	for x in [-size.x * 0.31, size.x * 0.31]:
		_add_cylinder_part(prop, "TableCornerScrewCap", Vector3(x, top_y + 0.044, -size.z * 0.35), 0.014, 0.014, 0.004, _shift_color(wood_dark, 0.06, 0.05, 0.035), 0.8)
	_add_box_part(prop, "TableBookPages", Vector3(-0.32, top_y + 0.046, -0.12), Vector3(0.43, 0.020, 0.29), paper_color, 0.86, Vector3(0.0, 10.0, 0.0))
	_add_box_part(prop, "TableBookCover", Vector3(-0.32, top_y + 0.069, -0.12), Vector3(0.45, 0.014, 0.31), Color(0.18, 0.30, 0.43), 0.78, Vector3(0.0, 10.0, 0.0))
	_add_box_part(prop, "BookSpine", Vector3(-0.54, top_y + 0.075, -0.08), Vector3(0.024, 0.024, 0.27), Color(0.11, 0.18, 0.28), 0.8, Vector3(0.0, 10.0, 0.0))
	_add_box_part(prop, "BookPageLines", Vector3(-0.18, top_y + 0.083, -0.245), Vector3(0.22, 0.006, 0.010), Color(0.67, 0.62, 0.53), 0.92, Vector3(0.0, 10.0, 0.0))
	_add_cylinder_part(prop, "TableSaucer", Vector3(0.43, top_y + 0.042, 0.18), 0.13, 0.13, 0.016, Color(0.78, 0.75, 0.67), 0.62)
	_add_cylinder_part(prop, "SaucerInnerRing", Vector3(0.43, top_y + 0.053, 0.18), 0.085, 0.085, 0.007, Color(0.68, 0.65, 0.58), 0.7)
	_add_cylinder_part(prop, "TableCup", Vector3(0.43, top_y + 0.122, 0.18), 0.068, 0.057, 0.145, Color(0.86, 0.83, 0.75), 0.58)
	_add_cylinder_part(prop, "CupCoffee", Vector3(0.43, top_y + 0.201, 0.18), 0.051, 0.051, 0.010, Color(0.19, 0.12, 0.08), 0.66)
	_add_capsule_part(prop, "CupHandle", Vector3(0.507, top_y + 0.124, 0.18), 0.012, 0.10, Color(0.86, 0.83, 0.75), 0.62)
	_add_cylinder_part(prop, "SmallPlate", Vector3(0.06, top_y + 0.043, -0.25), 0.108, 0.108, 0.018, Color(0.82, 0.79, 0.70), 0.72)
	_add_box_part(prop, "PlateSnack", Vector3(0.065, top_y + 0.066, -0.25), Vector3(0.11, 0.020, 0.062), Color(0.63, 0.42, 0.25), 0.78, Vector3(0.0, -16.0, 0.0))


func _build_chair_prop(prop: Node, size: Vector3, color: Color) -> void:
	var frame_color := Color(0.25, 0.20, 0.16)
	var frame_light := Color(0.42, 0.31, 0.22)
	var cushion_color := Color(0.58, 0.52, 0.44)
	var cushion_shadow := Color(0.42, 0.38, 0.34)
	var seat_y := 0.43
	var seat_size := Vector3(size.x * 0.88, 0.085, size.z * 0.72)
	var back_z := -seat_size.z * 0.5 - 0.02
	_add_box_part(prop, "Mesh", Vector3(0.0, seat_y, 0.04), seat_size, cushion_color, 0.9)
	_add_box_part(prop, "SeatInsetShadow", Vector3(0.0, seat_y + 0.047, 0.04), Vector3(seat_size.x * 0.72, 0.006, seat_size.z * 0.58), _shift_color(cushion_color, -0.07, -0.06, -0.05), 0.96)
	_add_box_part(prop, "SeatFrontLip", Vector3(0.0, seat_y - 0.006, seat_size.z * 0.5 + 0.078), Vector3(seat_size.x + 0.045, 0.055, 0.052), frame_color, 0.86)
	_add_box_part(prop, "SeatBackLip", Vector3(0.0, seat_y - 0.006, -seat_size.z * 0.5 - 0.004), Vector3(seat_size.x + 0.035, 0.050, 0.042), frame_color, 0.86)
	_add_box_part(prop, "SeatSideLipLeft", Vector3(-seat_size.x * 0.5 - 0.026, seat_y - 0.002, 0.04), Vector3(0.040, 0.052, seat_size.z + 0.035), frame_color, 0.86)
	_add_box_part(prop, "SeatSideLipRight", Vector3(seat_size.x * 0.5 + 0.026, seat_y - 0.002, 0.04), Vector3(0.040, 0.052, seat_size.z + 0.035), frame_color, 0.86)
	_add_capsule_part(prop, "SeatFrontSoftPiping", Vector3(0.0, seat_y + 0.055, seat_size.z * 0.5 + 0.048), 0.025, seat_size.x * 0.84, _shift_color(cushion_color, 0.04, 0.035, 0.025), 0.96, Vector3(0.0, 0.0, 90.0))
	_add_capsule_part(prop, "SeatLeftSoftPiping", Vector3(-seat_size.x * 0.5 + 0.010, seat_y + 0.055, 0.04), 0.016, seat_size.z * 0.64, _shift_color(cushion_color, -0.02, -0.02, -0.015), 0.97, Vector3(90.0, 0.0, 0.0))
	_add_capsule_part(prop, "SeatRightSoftPiping", Vector3(seat_size.x * 0.5 - 0.010, seat_y + 0.055, 0.04), 0.016, seat_size.z * 0.64, _shift_color(cushion_color, -0.02, -0.02, -0.015), 0.97, Vector3(90.0, 0.0, 0.0))
	for z in [-0.08, 0.08]:
		_add_box_part(prop, "SeatFabricSubtleThread", Vector3(0.0, seat_y + 0.054, z + 0.04), Vector3(seat_size.x * 0.62, 0.006, 0.008), _shift_color(cushion_color, -0.10, -0.08, -0.05), 0.99)
	for x in [-seat_size.x * 0.20, seat_size.x * 0.20]:
		_add_cylinder_part(prop, "SeatButtonDimple", Vector3(x, seat_y + 0.058, 0.04), 0.022, 0.022, 0.004, _shift_color(cushion_color, -0.12, -0.10, -0.07), 0.98)
	_add_box_part(prop, "BackPanelCloth", Vector3(0.0, 0.77, back_z + 0.014), Vector3(seat_size.x * 0.78, 0.37, 0.032), cushion_shadow, 0.92, Vector3(5.0, 0.0, 0.0))
	_add_box_part(prop, "BackTopRail", Vector3(0.0, 1.03, back_z - 0.03), Vector3(seat_size.x + 0.08, 0.065, 0.062), frame_color, 0.82, Vector3(5.0, 0.0, 0.0))
	_add_box_part(prop, "BackBottomRail", Vector3(0.0, 0.55, back_z + 0.01), Vector3(seat_size.x + 0.04, 0.052, 0.052), frame_color, 0.86, Vector3(5.0, 0.0, 0.0))
	_add_capsule_part(prop, "BackClothTopSoftRoll", Vector3(0.0, 0.96, back_z - 0.010), 0.018, seat_size.x * 0.70, _shift_color(cushion_shadow, 0.08, 0.07, 0.05), 0.97, Vector3(0.0, 0.0, 90.0))
	_add_capsule_part(prop, "BackClothLowerCrease", Vector3(0.0, 0.66, back_z + 0.020), 0.010, seat_size.x * 0.62, _shift_color(cushion_shadow, -0.09, -0.08, -0.06), 0.99, Vector3(0.0, 0.0, 90.0))
	for x in [-seat_size.x * 0.36, -seat_size.x * 0.12, seat_size.x * 0.12, seat_size.x * 0.36]:
		_add_box_part(prop, "BackVerticalSlat", Vector3(x, 0.79, back_z - 0.04), Vector3(0.032, 0.43, 0.036), frame_light, 0.84, Vector3(5.0, 0.0, 0.0))
		_add_sphere_part(prop, "BackSlatTopPeg", Vector3(x, 1.005, back_z - 0.040), Vector3(0.018, 0.012, 0.018), _shift_color(frame_light, 0.05, 0.04, 0.02), 0.8, Vector3(5.0, 0.0, 0.0))
	var leg_height := seat_y - 0.05
	for x in [-seat_size.x * 0.5 + 0.08, seat_size.x * 0.5 - 0.08]:
		for z in [-seat_size.z * 0.5 + 0.08, seat_size.z * 0.5 - 0.08]:
			_add_cylinder_part(prop, "ChairLeg", Vector3(x, leg_height * 0.5, z + 0.04), 0.020, 0.030, leg_height, frame_color, 0.84)
			_add_cylinder_part(prop, "ChairFootPad", Vector3(x, 0.030, z + 0.04), 0.032, 0.036, 0.026, Color(0.095, 0.075, 0.055), 0.72)
	_add_cylinder_part(prop, "ChairCrossbarFront", Vector3(0.0, 0.19, seat_size.z * 0.5 + 0.04), 0.013, 0.013, seat_size.x - 0.14, frame_color, 0.9, Vector3(0.0, 0.0, 90.0))
	_add_cylinder_part(prop, "ChairCrossbarBack", Vector3(0.0, 0.19, -seat_size.z * 0.5 + 0.12), 0.013, 0.013, seat_size.x - 0.14, frame_color, 0.9, Vector3(0.0, 0.0, 90.0))
	_add_cylinder_part(prop, "ChairLeftSideCrossbar", Vector3(-seat_size.x * 0.5 + 0.08, 0.18, 0.04), 0.011, 0.011, seat_size.z - 0.18, frame_color, 0.9, Vector3(90.0, 0.0, 0.0))
	_add_cylinder_part(prop, "ChairRightSideCrossbar", Vector3(seat_size.x * 0.5 - 0.08, 0.18, 0.04), 0.011, 0.011, seat_size.z - 0.18, frame_color, 0.9, Vector3(90.0, 0.0, 0.0))


func _build_sofa_prop(prop: Node, size: Vector3, color: Color) -> void:
	var fabric := Color(0.45, 0.52, 0.61)
	var fabric_light := Color(0.60, 0.66, 0.72)
	var fabric_dark := Color(0.31, 0.37, 0.46)
	var seam_color := Color(0.18, 0.22, 0.28)
	var seat_y := 0.30
	var seat_size := Vector3(size.x, 0.20, size.z * 0.76)
	_add_box_part(prop, "Mesh", Vector3(0.0, seat_y, -0.05), seat_size, fabric_dark, 0.92)
	_add_capsule_part(prop, "SofaFrontRoll", Vector3(0.0, seat_y + 0.09, -seat_size.z * 0.5 - 0.08), 0.095, size.x * 0.92, fabric_light, 0.94, Vector3(0.0, 0.0, 90.0))
	_add_box_part(prop, "SeatCushionLeftPad", Vector3(-size.x * 0.255, seat_y + 0.155, -0.08), Vector3(size.x * 0.43, 0.105, seat_size.z * 0.82), fabric, 0.96)
	_add_box_part(prop, "SeatCushionRightPad", Vector3(size.x * 0.255, seat_y + 0.155, -0.08), Vector3(size.x * 0.43, 0.105, seat_size.z * 0.82), fabric, 0.96)
	_add_box_part(prop, "SeatLeftTopInset", Vector3(-size.x * 0.255, seat_y + 0.212, -0.08), Vector3(size.x * 0.35, 0.010, seat_size.z * 0.63), fabric_light, 0.98)
	_add_box_part(prop, "SeatRightTopInset", Vector3(size.x * 0.255, seat_y + 0.212, -0.08), Vector3(size.x * 0.35, 0.010, seat_size.z * 0.63), fabric_light, 0.98)
	_add_box_part(prop, "SeatCenterSeam", Vector3(0.0, seat_y + 0.224, -0.08), Vector3(0.020, 0.022, seat_size.z * 0.87), seam_color, 0.98)
	_add_capsule_part(prop, "SeatLeftFrontBulge", Vector3(-size.x * 0.255, seat_y + 0.232, -seat_size.z * 0.5 - 0.030), 0.030, size.x * 0.37, fabric_light, 0.98, Vector3(0.0, 0.0, 90.0))
	_add_capsule_part(prop, "SeatRightFrontBulge", Vector3(size.x * 0.255, seat_y + 0.232, -seat_size.z * 0.5 - 0.030), 0.030, size.x * 0.37, fabric_light, 0.98, Vector3(0.0, 0.0, 90.0))
	for x in [-size.x * 0.38, -size.x * 0.13, size.x * 0.13, size.x * 0.38]:
		_add_box_part(prop, "SofaSeatFineWrinkle", Vector3(x, seat_y + 0.231, -0.17), Vector3(0.020, 0.008, seat_size.z * 0.36), _shift_color(fabric, -0.08, -0.07, -0.05), 0.99, Vector3(0.0, 0.0, 2.0))
	for x in [-size.x * 0.255, size.x * 0.255]:
		for z in [-0.20, 0.08]:
			_add_cylinder_part(prop, "SofaSeatButtonDimple", Vector3(x, seat_y + 0.236, z), 0.022, 0.022, 0.005, _shift_color(fabric, -0.12, -0.10, -0.08), 0.99)
	for x in [-size.x * 0.255, size.x * 0.255]:
		_add_cylinder_part(prop, "SeatFrontPiping", Vector3(x, seat_y + 0.218, -seat_size.z * 0.5 - 0.02), 0.014, 0.014, size.x * 0.39, seam_color, 0.98, Vector3(0.0, 0.0, 90.0))
		_add_cylinder_part(prop, "SeatBackPiping", Vector3(x, seat_y + 0.218, seat_size.z * 0.36), 0.011, 0.011, size.x * 0.38, seam_color, 0.98, Vector3(0.0, 0.0, 90.0))
	_add_box_part(prop, "BackRest", Vector3(0.0, 0.64, size.z * 0.36), Vector3(size.x * 1.02, 0.64, 0.15), fabric_dark, 0.9, Vector3(-4.0, 0.0, 0.0))
	_add_capsule_part(prop, "BackTopRoll", Vector3(0.0, 0.97, size.z * 0.32), 0.09, size.x * 0.96, fabric_dark, 0.92, Vector3(0.0, 0.0, 90.0))
	_add_box_part(prop, "BackCushionLeft", Vector3(-size.x * 0.25, 0.68, size.z * 0.22), Vector3(size.x * 0.43, 0.40, 0.11), fabric, 0.96, Vector3(-4.0, 0.0, 0.0))
	_add_box_part(prop, "BackCushionRight", Vector3(size.x * 0.25, 0.68, size.z * 0.22), Vector3(size.x * 0.43, 0.40, 0.11), fabric, 0.96, Vector3(-4.0, 0.0, 0.0))
	_add_box_part(prop, "BackCenterSeam", Vector3(0.0, 0.69, size.z * 0.16), Vector3(0.03, 0.34, 0.045), seam_color, 0.98, Vector3(-4.0, 0.0, 0.0))
	for x in [-size.x * 0.25, size.x * 0.25]:
		_add_capsule_part(prop, "BackCushionUpperSoftRoll", Vector3(x, 0.88, size.z * 0.17), 0.022, size.x * 0.36, _shift_color(fabric, 0.08, 0.07, 0.06), 0.98, Vector3(0.0, 0.0, 90.0))
		_add_capsule_part(prop, "BackCushionLowerFold", Vector3(x, 0.53, size.z * 0.19), 0.012, size.x * 0.34, _shift_color(fabric, -0.10, -0.09, -0.07), 0.99, Vector3(0.0, 0.0, 90.0))
	_add_cylinder_part(prop, "BackLeftPiping", Vector3(-size.x * 0.48, 0.69, size.z * 0.16), 0.010, 0.010, 0.36, seam_color, 0.98, Vector3(0.0, 0.0, 0.0))
	_add_cylinder_part(prop, "BackRightPiping", Vector3(size.x * 0.48, 0.69, size.z * 0.16), 0.010, 0.010, 0.36, seam_color, 0.98, Vector3(0.0, 0.0, 0.0))
	_add_box_part(prop, "LeftArmRest", Vector3(-size.x * 0.5 - 0.09, 0.44, -0.04), Vector3(0.20, 0.52, seat_size.z + 0.18), fabric_dark, 0.88)
	_add_box_part(prop, "RightArmRest", Vector3(size.x * 0.5 + 0.09, 0.44, -0.04), Vector3(0.20, 0.52, seat_size.z + 0.18), fabric_dark, 0.88)
	_add_capsule_part(prop, "LeftArmTopRoll", Vector3(-size.x * 0.5 - 0.09, 0.72, -0.04), 0.085, seat_size.z + 0.14, fabric_light, 0.92, Vector3(90.0, 0.0, 0.0))
	_add_capsule_part(prop, "RightArmTopRoll", Vector3(size.x * 0.5 + 0.09, 0.72, -0.04), 0.085, seat_size.z + 0.14, fabric_light, 0.92, Vector3(90.0, 0.0, 0.0))
	_add_cylinder_part(prop, "LeftArmFrontPipe", Vector3(-size.x * 0.5 - 0.09, 0.45, -seat_size.z * 0.5 - 0.16), 0.012, 0.012, 0.45, seam_color, 0.98)
	_add_cylinder_part(prop, "RightArmFrontPipe", Vector3(size.x * 0.5 + 0.09, 0.45, -seat_size.z * 0.5 - 0.16), 0.012, 0.012, 0.45, seam_color, 0.98)
	_add_box_part(prop, "SofaThrowPillowLeft", Vector3(-size.x * 0.34, 0.62, size.z * 0.06), Vector3(0.29, 0.28, 0.08), Color(0.76, 0.58, 0.39), 0.94, Vector3(-8.0, 0.0, 8.0))
	_add_box_part(prop, "SofaThrowPillowRight", Vector3(size.x * 0.28, 0.61, size.z * 0.05), Vector3(0.27, 0.26, 0.08), Color(0.37, 0.54, 0.50), 0.94, Vector3(-7.0, 0.0, -7.0))
	_add_box_part(prop, "PillowLeftSeam", Vector3(-size.x * 0.34, 0.62, size.z * 0.011), Vector3(0.24, 0.018, 0.010), Color(0.56, 0.42, 0.29), 0.98, Vector3(-8.0, 0.0, 8.0))
	_add_box_part(prop, "PillowRightSeam", Vector3(size.x * 0.28, 0.61, size.z * 0.004), Vector3(0.22, 0.016, 0.010), Color(0.25, 0.38, 0.36), 0.98, Vector3(-7.0, 0.0, -7.0))
	for x in [-size.x * 0.38, size.x * 0.38]:
		for z in [-size.z * 0.32, size.z * 0.28]:
			_add_cylinder_part(prop, "SofaRoundFoot", Vector3(x, 0.075, z), 0.030, 0.040, 0.15, Color(0.13, 0.10, 0.08), 0.72)


func _build_plant_prop(prop: Node, size: Vector3, color: Color) -> void:
	var pot_color := Color(0.45, 0.31, 0.22)
	var pot_trim := Color(0.62, 0.47, 0.34)
	var stem_color := Color(0.28, 0.22, 0.14)
	var leaf_dark := _shift_color(color, -0.06, -0.03, -0.05)
	var leaf_light := _shift_color(color, 0.10, 0.13, 0.03)
	_add_cylinder_part(prop, "PlantStandTop", Vector3(0.0, 0.22, 0.0), size.x * 0.30, size.x * 0.30, 0.035, Color(0.20, 0.17, 0.14), 0.78)
	for x in [-0.14, 0.14]:
		for z in [-0.14, 0.14]:
			_add_cylinder_part(prop, "PlantStandLeg", Vector3(x, 0.105, z), 0.010, 0.016, 0.21, Color(0.20, 0.17, 0.14), 0.82)
	_add_cylinder_part(prop, "Mesh", Vector3(0.0, 0.42, 0.0), size.x * 0.30, size.x * 0.22, 0.40, pot_color, 0.78)
	_add_cylinder_part(prop, "PotLowerBand", Vector3(0.0, 0.25, 0.0), size.x * 0.235, size.x * 0.25, 0.045, Color(0.31, 0.21, 0.15), 0.82)
	_add_cylinder_part(prop, "PotRim", Vector3(0.0, 0.64, 0.0), size.x * 0.36, size.x * 0.36, 0.075, pot_trim, 0.75)
	_add_cylinder_part(prop, "Soil", Vector3(0.0, 0.687, 0.0), size.x * 0.27, size.x * 0.27, 0.018, Color(0.16, 0.10, 0.07), 0.95)
	_add_cylinder_part(prop, "MainStem", Vector3(0.0, 1.04, 0.0), 0.022, 0.034, 0.70, stem_color, 0.86)
	_add_cylinder_part(prop, "BranchLeft", Vector3(-0.12, 1.10, 0.02), 0.012, 0.018, 0.46, stem_color, 0.88, Vector3(0.0, 0.0, 35.0))
	_add_cylinder_part(prop, "BranchRight", Vector3(0.14, 1.17, -0.02), 0.012, 0.018, 0.50, stem_color, 0.88, Vector3(0.0, 0.0, -34.0))
	_add_cylinder_part(prop, "BranchBack", Vector3(0.0, 1.18, 0.13), 0.010, 0.016, 0.44, stem_color, 0.88, Vector3(30.0, 0.0, 0.0))
	_add_cylinder_part(prop, "BranchFront", Vector3(0.02, 1.10, -0.14), 0.010, 0.016, 0.40, stem_color, 0.88, Vector3(-30.0, 0.0, 0.0))
	_add_leaf_part(prop, "LeafTop", Vector3(0.00, 1.50, 0.0), Vector3(0.15, 0.040, 0.31), leaf_light, Vector3(-12.0, 0.0, 0.0))
	_add_leaf_part(prop, "LeafLeftLow", Vector3(-0.29, 1.12, 0.02), Vector3(0.18, 0.036, 0.36), leaf_dark, Vector3(-15.0, 0.0, 38.0))
	_add_leaf_part(prop, "LeafLeftHigh", Vector3(-0.24, 1.33, -0.04), Vector3(0.15, 0.035, 0.31), leaf_light, Vector3(-8.0, -24.0, 46.0))
	_add_leaf_part(prop, "LeafRightLow", Vector3(0.30, 1.21, -0.02), Vector3(0.18, 0.036, 0.36), leaf_dark, Vector3(-14.0, 8.0, -39.0))
	_add_leaf_part(prop, "LeafRightHigh", Vector3(0.25, 1.38, 0.04), Vector3(0.15, 0.035, 0.32), leaf_light, Vector3(-10.0, 25.0, -46.0))
	_add_leaf_part(prop, "LeafFront", Vector3(0.00, 1.23, -0.30), Vector3(0.15, 0.036, 0.33), leaf_light, Vector3(28.0, 0.0, 0.0))
	_add_leaf_part(prop, "LeafBack", Vector3(0.03, 1.31, 0.31), Vector3(0.15, 0.036, 0.33), leaf_dark, Vector3(-28.0, 0.0, 0.0))
	_add_leaf_part(prop, "LeafFrontLeft", Vector3(-0.16, 1.38, -0.22), Vector3(0.13, 0.032, 0.28), leaf_light, Vector3(20.0, -26.0, 28.0))
	_add_leaf_part(prop, "LeafBackRight", Vector3(0.19, 1.44, 0.22), Vector3(0.13, 0.032, 0.28), leaf_dark, Vector3(-20.0, 28.0, -28.0))
	_add_leaf_part(prop, "LeafFrontRightLow", Vector3(0.19, 1.04, -0.22), Vector3(0.12, 0.030, 0.26), leaf_dark, Vector3(30.0, 20.0, -28.0))
	_add_leaf_part(prop, "LeafBackLeftLow", Vector3(-0.20, 1.05, 0.22), Vector3(0.12, 0.030, 0.26), leaf_light, Vector3(-30.0, -20.0, 28.0))
	_add_leaf_part(prop, "LeafLeftMidSmall", Vector3(-0.32, 1.25, -0.12), Vector3(0.11, 0.028, 0.24), leaf_light, Vector3(-4.0, -36.0, 52.0))
	_add_leaf_part(prop, "LeafRightMidSmall", Vector3(0.32, 1.31, 0.12), Vector3(0.11, 0.028, 0.24), leaf_dark, Vector3(-8.0, 36.0, -52.0))


func _create_room_detail_props(parent: Node) -> void:
	var shelf := Node3D.new()
	shelf.name = "Bookshelf"
	shelf.position = Vector3(-4.78, 0.0, 5.04)
	parent.add_child(shelf)
	if not _add_kenney_scene_model(shelf, "res://assets/furniture/kenney/bookcaseOpen.glb", 2.0):
		var shelf_wood := Color(0.34, 0.24, 0.16)
		_add_box_part(shelf, "ShelfFrameLeft", Vector3(-0.42, 0.72, 0.0), Vector3(0.07, 1.35, 0.32), shelf_wood, 0.82)
		_add_box_part(shelf, "ShelfFrameRight", Vector3(0.42, 0.72, 0.0), Vector3(0.07, 1.35, 0.32), shelf_wood, 0.82)
		_add_box_part(shelf, "ShelfTop", Vector3(0.0, 1.39, 0.0), Vector3(0.92, 0.08, 0.34), shelf_wood, 0.82)
		_add_box_part(shelf, "ShelfBottom", Vector3(0.0, 0.05, 0.0), Vector3(0.92, 0.10, 0.34), shelf_wood, 0.82)
		for y in [0.42, 0.78, 1.12]:
			_add_box_part(shelf, "ShelfBoard", Vector3(0.0, y, 0.0), Vector3(0.88, 0.045, 0.32), shelf_wood, 0.84)
		for x in [-0.30, -0.20, -0.08, 0.08, 0.22, 0.33]:
			_add_box_part(shelf, "ShelfBook", Vector3(x, 0.30, -0.03), Vector3(0.07, 0.42 + absf(x) * 0.10, 0.21), Color(0.18 + absf(x), 0.27, 0.40), 0.78)
		for x in [-0.28, -0.13, 0.06, 0.25]:
			_add_box_part(shelf, "ShelfUpperBook", Vector3(x, 0.98, -0.03), Vector3(0.10, 0.32 + absf(x) * 0.12, 0.21), Color(0.55, 0.32 + absf(x) * 0.2, 0.22), 0.78)

	var lamp := Node3D.new()
	lamp.name = "FloorLamp"
	lamp.position = Vector3(5.08, 0.0, 2.70)
	parent.add_child(lamp)
	if not _add_kenney_scene_model(lamp, "res://assets/furniture/kenney/lampRoundFloor.glb", 1.9):
		_add_cylinder_part(lamp, "LampBase", Vector3(0.0, 0.035, 0.0), 0.18, 0.20, 0.07, Color(0.22, 0.20, 0.18), 0.58)
		_add_cylinder_part(lamp, "LampPole", Vector3(0.0, 0.82, 0.0), 0.025, 0.03, 1.58, Color(0.23, 0.22, 0.20), 0.52)
		_add_cylinder_part(lamp, "LampShade", Vector3(0.0, 1.62, 0.0), 0.25, 0.36, 0.34, Color(0.88, 0.78, 0.58), 0.66)
	var lamp_light := OmniLight3D.new()
	lamp_light.name = "LampWarmLight"
	lamp_light.position = Vector3(0.0, 1.55, 0.0)
	lamp_light.light_color = Color(1.0, 0.82, 0.55)
	lamp_light.light_energy = 0.28
	lamp_light.omni_range = 2.2
	lamp.add_child(lamp_light)

	var frame := Node3D.new()
	frame.name = "PictureFrame"
	frame.position = Vector3(2.80, 1.36, FLOOR_SIZE.y * 0.5 - 0.275)
	parent.add_child(frame)
	_add_box_part(frame, "FrameBack", Vector3(0.0, 0.0, 0.0), Vector3(0.76, 0.54, 0.035), Color(0.74, 0.70, 0.59), 0.9)
	_add_box_part(frame, "FrameTop", Vector3(0.0, 0.29, -0.02), Vector3(0.84, 0.055, 0.055), Color(0.27, 0.22, 0.17), 0.78)
	_add_box_part(frame, "FrameBottom", Vector3(0.0, -0.29, -0.02), Vector3(0.84, 0.055, 0.055), Color(0.27, 0.22, 0.17), 0.78)
	_add_box_part(frame, "FrameLeft", Vector3(-0.42, 0.0, -0.02), Vector3(0.055, 0.58, 0.055), Color(0.27, 0.22, 0.17), 0.78)
	_add_box_part(frame, "FrameRight", Vector3(0.42, 0.0, -0.02), Vector3(0.055, 0.58, 0.055), Color(0.27, 0.22, 0.17), 0.78)


func _create_room_completion_props(parent: Node) -> void:
	var lounge_rug := Node3D.new()
	lounge_rug.name = "LoungeAreaRug"
	lounge_rug.position = Vector3(-5.85, 0.0, 0.44)
	parent.add_child(lounge_rug)
	_add_box_part(lounge_rug, "LoungeRugBase", Vector3(0.0, 0.014, 0.0), Vector3(2.85, 0.018, 1.85), Color(0.36, 0.33, 0.31), 0.96, Vector3(0.0, 14.0, 0.0))
	_add_box_part(lounge_rug, "LoungeRugInset", Vector3(0.0, 0.028, 0.0), Vector3(2.32, 0.012, 1.38), Color(0.54, 0.47, 0.40), 0.96, Vector3(0.0, 14.0, 0.0))
	_add_box_part(lounge_rug, "LoungeRugFrontLine", Vector3(0.0, 0.038, -0.64), Vector3(1.86, 0.010, 0.030), Color(0.72, 0.62, 0.48), 0.98, Vector3(0.0, 14.0, 0.0))

	var console := Node3D.new()
	console.name = "BackWallConsoleCabinet"
	console.position = Vector3(0.0, 0.0, FLOOR_SIZE.y * 0.5 - 0.52)
	parent.add_child(console)
	var cabinet := Color(0.32, 0.23, 0.16)
	var cabinet_light := Color(0.53, 0.39, 0.26)
	_add_box_part(console, "ConsoleBody", Vector3(0.0, 0.38, 0.0), Vector3(2.45, 0.55, 0.32), cabinet, 0.78)
	_add_box_part(console, "ConsoleTop", Vector3(0.0, 0.685, -0.02), Vector3(2.58, 0.07, 0.42), cabinet_light, 0.70)
	_add_box_part(console, "ConsoleKickPlate", Vector3(0.0, 0.09, -0.01), Vector3(2.32, 0.10, 0.30), Color(0.20, 0.14, 0.10), 0.82)
	for x in [-0.78, 0.0, 0.78]:
		_add_box_part(console, "ConsoleDrawer", Vector3(x, 0.46, -0.185), Vector3(0.68, 0.24, 0.045), Color(0.45, 0.32, 0.21), 0.76)
		_add_cylinder_part(console, "ConsoleHandle", Vector3(x, 0.46, -0.225), 0.020, 0.020, 0.28, Color(0.13, 0.10, 0.08), 0.62, Vector3(0.0, 0.0, 90.0))
	_add_cylinder_part(console, "ConsoleVase", Vector3(-0.92, 0.84, -0.04), 0.07, 0.11, 0.22, Color(0.60, 0.55, 0.47), 0.58)
	_add_leaf_part(console, "ConsoleLeafA", Vector3(-0.98, 1.03, -0.04), Vector3(0.08, 0.020, 0.22), Color(0.27, 0.45, 0.32), Vector3(-24.0, 0.0, 34.0))
	_add_leaf_part(console, "ConsoleLeafB", Vector3(-0.86, 1.05, -0.03), Vector3(0.08, 0.020, 0.22), Color(0.34, 0.55, 0.38), Vector3(-18.0, 0.0, -34.0))
	_add_box_part(console, "ConsoleSmallBook", Vector3(0.76, 0.745, -0.04), Vector3(0.42, 0.034, 0.22), Color(0.20, 0.30, 0.42), 0.78, Vector3(0.0, -8.0, 0.0))

	var left_window := Node3D.new()
	left_window.name = "LeftWallWindow"
	left_window.position = Vector3(-FLOOR_SIZE.x * 0.5 + 0.14, 1.62, -1.55)
	parent.add_child(left_window)
	_add_box_part(left_window, "WindowGlass", Vector3(0.0, 0.0, 0.0), Vector3(0.035, 0.76, 1.18), Color(0.44, 0.55, 0.60), 0.42)
	_add_box_part(left_window, "WindowTop", Vector3(-0.018, 0.43, 0.0), Vector3(0.08, 0.065, 1.32), Color(0.26, 0.20, 0.15), 0.78)
	_add_box_part(left_window, "WindowBottom", Vector3(-0.018, -0.43, 0.0), Vector3(0.08, 0.065, 1.32), Color(0.26, 0.20, 0.15), 0.78)
	_add_box_part(left_window, "WindowLeft", Vector3(-0.018, 0.0, -0.66), Vector3(0.08, 0.84, 0.055), Color(0.26, 0.20, 0.15), 0.78)
	_add_box_part(left_window, "WindowRight", Vector3(-0.018, 0.0, 0.66), Vector3(0.08, 0.84, 0.055), Color(0.26, 0.20, 0.15), 0.78)
	_add_box_part(left_window, "WindowCenterRail", Vector3(-0.026, 0.0, 0.0), Vector3(0.065, 0.76, 0.036), Color(0.26, 0.20, 0.15), 0.78)
	_add_box_part(left_window, "CurtainBack", Vector3(-0.04, 0.0, -0.78), Vector3(0.035, 0.92, 0.12), Color(0.54, 0.45, 0.42), 0.94)
	_add_box_part(left_window, "CurtainFront", Vector3(-0.04, 0.0, 0.78), Vector3(0.035, 0.92, 0.12), Color(0.54, 0.45, 0.42), 0.94)

	var display_shelf := Node3D.new()
	display_shelf.name = "RightWallDisplayShelf"
	display_shelf.position = Vector3(FLOOR_SIZE.x * 0.5 - 0.22, 1.22, -1.85)
	parent.add_child(display_shelf)
	for y in [-0.34, 0.02, 0.38]:
		_add_box_part(display_shelf, "RightWallShelfBoard", Vector3(0.0, y, 0.0), Vector3(0.28, 0.045, 1.25), Color(0.35, 0.25, 0.17), 0.82)
	for z in [-0.42, -0.18, 0.10, 0.36]:
		_add_box_part(display_shelf, "ShelfObjectBook", Vector3(-0.11, -0.18, z), Vector3(0.10, 0.30, 0.09), Color(0.28 + absf(z) * 0.35, 0.30, 0.42), 0.84)
	_add_cylinder_part(display_shelf, "ShelfSmallPot", Vector3(-0.11, 0.20, 0.34), 0.08, 0.065, 0.13, Color(0.50, 0.38, 0.29), 0.78)


func _create_demo_space_expansion_props(parent: Node) -> void:
	var left_walkway := Node3D.new()
	left_walkway.name = "DemoExpandedLeftWalkway"
	left_walkway.position = Vector3(-4.05, 0.0, -2.65)
	parent.add_child(left_walkway)
	_add_box_part(left_walkway, "WalkwayMatBase", Vector3(0.0, 0.018, 0.0), Vector3(2.45, 0.012, 3.15), Color(0.50, 0.47, 0.41), 0.96, Vector3(0.0, -8.0, 0.0))
	_add_box_part(left_walkway, "WalkwayMatLineA", Vector3(0.0, 0.030, -0.92), Vector3(1.95, 0.006, 0.026), Color(0.70, 0.64, 0.52), 0.98, Vector3(0.0, -8.0, 0.0))
	_add_box_part(left_walkway, "WalkwayMatLineB", Vector3(0.0, 0.031, 0.92), Vector3(1.95, 0.006, 0.026), Color(0.70, 0.64, 0.52), 0.98, Vector3(0.0, -8.0, 0.0))

	var right_walkway := Node3D.new()
	right_walkway.name = "DemoExpandedRightWalkway"
	right_walkway.position = Vector3(4.65, 0.0, -2.15)
	parent.add_child(right_walkway)
	_add_box_part(right_walkway, "RightWalkwayMatBase", Vector3(0.0, 0.018, 0.0), Vector3(2.70, 0.012, 2.55), Color(0.44, 0.43, 0.38), 0.96, Vector3(0.0, 10.0, 0.0))
	_add_box_part(right_walkway, "RightWalkwayInset", Vector3(0.0, 0.031, 0.0), Vector3(2.18, 0.006, 1.92), Color(0.58, 0.53, 0.44), 0.98, Vector3(0.0, 10.0, 0.0))

	var rear_planter := Node3D.new()
	rear_planter.name = "ExpandedRearPlanter"
	rear_planter.position = Vector3(6.85, 0.0, 5.85)
	parent.add_child(rear_planter)
	_add_box_part(rear_planter, "PlanterBox", Vector3(0.0, 0.18, 0.0), Vector3(1.45, 0.36, 0.42), Color(0.32, 0.24, 0.18), 0.82)
	_add_cylinder_part(rear_planter, "PlanterStemA", Vector3(-0.38, 0.68, 0.0), 0.014, 0.020, 0.62, Color(0.22, 0.42, 0.25), 0.9, Vector3(0.0, 0.0, 8.0))
	_add_cylinder_part(rear_planter, "PlanterStemB", Vector3(0.08, 0.74, 0.0), 0.014, 0.020, 0.74, Color(0.20, 0.38, 0.24), 0.9, Vector3(0.0, 0.0, -7.0))
	_add_cylinder_part(rear_planter, "PlanterStemC", Vector3(0.42, 0.66, 0.0), 0.014, 0.020, 0.58, Color(0.24, 0.44, 0.27), 0.9, Vector3(0.0, 0.0, -12.0))
	_add_leaf_part(rear_planter, "PlanterLeafA", Vector3(-0.50, 0.98, -0.08), Vector3(0.12, 0.030, 0.28), Color(0.31, 0.56, 0.34), Vector3(-18.0, -20.0, 36.0))
	_add_leaf_part(rear_planter, "PlanterLeafB", Vector3(0.12, 1.13, 0.06), Vector3(0.14, 0.034, 0.32), Color(0.34, 0.61, 0.38), Vector3(-16.0, 14.0, -30.0))
	_add_leaf_part(rear_planter, "PlanterLeafC", Vector3(0.54, 0.91, -0.06), Vector3(0.11, 0.030, 0.26), Color(0.27, 0.49, 0.31), Vector3(-14.0, 26.0, -36.0))

	var open_space_marker := Node3D.new()
	open_space_marker.name = "DemoOpenMovementAreaMarker"
	open_space_marker.position = Vector3(0.0, 0.0, -2.35)
	parent.add_child(open_space_marker)
	_add_box_part(open_space_marker, "OpenAreaSubtleCenter", Vector3(0.0, 0.010, 0.0), Vector3(2.85, 0.006, 1.75), Color(0.74, 0.71, 0.63), 0.98)
	_add_box_part(open_space_marker, "OpenAreaSubtleFront", Vector3(0.0, 0.014, -0.78), Vector3(2.35, 0.004, 0.018), Color(0.62, 0.59, 0.52), 0.98)


func _create_closeup_model_detail_props(parent: Node) -> void:
	var table_detail := Node3D.new()
	table_detail.name = "ForegroundTableCloseupDetails"
	table_detail.position = Vector3(3.65, 0.0, 2.65)
	parent.add_child(table_detail)
	_add_box_part(table_detail, "TabletBody", Vector3(-0.24, 0.735, -0.32), Vector3(0.48, 0.024, 0.30), Color(0.055, 0.060, 0.070), 0.48, Vector3(0.0, -12.0, 0.0))
	_add_box_part(table_detail, "TabletScreenGlass", Vector3(-0.24, 0.753, -0.32), Vector3(0.41, 0.006, 0.24), Color(0.10, 0.17, 0.19), 0.42, Vector3(0.0, -12.0, 0.0))
	_add_box_part(table_detail, "TabletScreenGlow", Vector3(-0.24, 0.758, -0.32), Vector3(0.28, 0.004, 0.10), Color(0.38, 0.58, 0.62), 0.46, Vector3(0.0, -12.0, 0.0))
	_add_cylinder_part(table_detail, "TabletHomeButton", Vector3(-0.02, 0.762, -0.405), 0.018, 0.018, 0.006, Color(0.14, 0.15, 0.16), 0.54)
	_add_cylinder_part(table_detail, "StylusPen", Vector3(0.30, 0.758, -0.20), 0.008, 0.008, 0.40, Color(0.08, 0.08, 0.075), 0.50, Vector3(0.0, 0.0, 90.0))
	_add_cylinder_part(table_detail, "ChargingCableLongRun", Vector3(-0.59, 0.720, -0.11), 0.006, 0.006, 0.55, Color(0.045, 0.045, 0.045), 0.92, Vector3(90.0, 0.0, 0.0))
	_add_cylinder_part(table_detail, "ChargingCableTurn", Vector3(-0.51, 0.720, 0.16), 0.006, 0.006, 0.34, Color(0.045, 0.045, 0.045), 0.92, Vector3(0.0, 0.0, 90.0))
	_add_box_part(table_detail, "StickyNoteStack", Vector3(-0.04, 0.765, 0.22), Vector3(0.25, 0.030, 0.18), Color(0.92, 0.82, 0.42), 0.84, Vector3(0.0, 8.0, 0.0))
	_add_box_part(table_detail, "StickyNoteTop", Vector3(-0.04, 0.787, 0.22), Vector3(0.22, 0.006, 0.15), Color(0.98, 0.90, 0.54), 0.88, Vector3(0.0, 8.0, 0.0))
	for z in [0.17, 0.22, 0.27]:
		_add_box_part(table_detail, "StickyNoteLine", Vector3(-0.04, 0.792, z), Vector3(0.14, 0.004, 0.006), Color(0.64, 0.57, 0.36), 0.94, Vector3(0.0, 8.0, 0.0))

	var headphones := Node3D.new()
	headphones.name = "TableHeadphones"
	headphones.position = Vector3(-0.60, 0.0, 0.20)
	headphones.rotation_degrees.y = -10.0
	table_detail.add_child(headphones)
	_add_sphere_part(headphones, "HeadphoneLeftPad", Vector3(-0.07, 0.772, 0.00), Vector3(0.058, 0.022, 0.075), Color(0.035, 0.036, 0.038), 0.62, Vector3(0.0, 0.0, 12.0))
	_add_sphere_part(headphones, "HeadphoneRightPad", Vector3(0.13, 0.772, 0.01), Vector3(0.058, 0.022, 0.075), Color(0.035, 0.036, 0.038), 0.62, Vector3(0.0, 0.0, -12.0))
	_add_sphere_part(headphones, "HeadphoneLeftCushion", Vector3(-0.07, 0.779, 0.00), Vector3(0.041, 0.016, 0.055), Color(0.10, 0.11, 0.12), 0.82, Vector3(0.0, 0.0, 12.0))
	_add_sphere_part(headphones, "HeadphoneRightCushion", Vector3(0.13, 0.779, 0.01), Vector3(0.041, 0.016, 0.055), Color(0.10, 0.11, 0.12), 0.82, Vector3(0.0, 0.0, -12.0))
	_add_capsule_part(headphones, "HeadphoneBandLeft", Vector3(-0.02, 0.820, -0.06), 0.008, 0.18, Color(0.045, 0.046, 0.048), 0.58, Vector3(45.0, 0.0, 56.0))
	_add_capsule_part(headphones, "HeadphoneBandRight", Vector3(0.08, 0.820, -0.06), 0.008, 0.18, Color(0.045, 0.046, 0.048), 0.58, Vector3(45.0, 0.0, -56.0))
	_add_capsule_part(headphones, "HeadphoneTopBridge", Vector3(0.03, 0.858, -0.09), 0.007, 0.19, Color(0.050, 0.052, 0.055), 0.58, Vector3(90.0, 0.0, 90.0))

	var camera_prop := Node3D.new()
	camera_prop.name = "TableCameraProp"
	camera_prop.position = Vector3(0.33, 0.0, 0.08)
	camera_prop.rotation_degrees.y = 18.0
	table_detail.add_child(camera_prop)
	_add_box_part(camera_prop, "CameraBody", Vector3(0.0, 0.758, 0.0), Vector3(0.30, 0.15, 0.13), Color(0.070, 0.075, 0.078), 0.52)
	_add_box_part(camera_prop, "CameraTopPlate", Vector3(-0.04, 0.850, 0.0), Vector3(0.18, 0.045, 0.10), Color(0.12, 0.12, 0.11), 0.50)
	_add_cylinder_part(camera_prop, "CameraLensOuter", Vector3(0.0, 0.758, -0.088), 0.070, 0.082, 0.070, Color(0.025, 0.027, 0.030), 0.42, Vector3(90.0, 0.0, 0.0))
	_add_cylinder_part(camera_prop, "CameraLensGlass", Vector3(0.0, 0.758, -0.130), 0.052, 0.052, 0.010, Color(0.045, 0.075, 0.090), 0.36, Vector3(90.0, 0.0, 0.0))
	_add_cylinder_part(camera_prop, "CameraShutterButton", Vector3(-0.095, 0.883, -0.012), 0.023, 0.023, 0.010, Color(0.18, 0.17, 0.15), 0.46)
	_add_box_part(camera_prop, "CameraGrip", Vector3(0.142, 0.755, 0.010), Vector3(0.042, 0.13, 0.092), Color(0.030, 0.032, 0.034), 0.72)
	_add_box_part(camera_prop, "CameraSideScreen", Vector3(-0.126, 0.764, 0.030), Vector3(0.010, 0.072, 0.074), Color(0.08, 0.12, 0.13), 0.46)
	_add_cylinder_part(camera_prop, "CameraModeDial", Vector3(0.070, 0.887, 0.018), 0.030, 0.030, 0.012, Color(0.13, 0.13, 0.12), 0.44)
	for x in [-0.028, 0.000, 0.028]:
		_add_box_part(camera_prop, "LensHighlightMark", Vector3(x, 0.799, -0.137), Vector3(0.010, 0.006, 0.004), Color(0.32, 0.44, 0.48), 0.38)

	var sofa_detail := Node3D.new()
	sofa_detail.name = "SofaCloseupFabricDetails"
	sofa_detail.position = Vector3(-3.65, 0.0, 0.35)
	sofa_detail.rotation_degrees.y = 14.0
	parent.add_child(sofa_detail)
	_add_box_part(sofa_detail, "ThrowBlanketMain", Vector3(-0.36, 0.555, -0.23), Vector3(0.68, 0.018, 0.48), Color(0.58, 0.35, 0.31), 0.98, Vector3(0.0, 4.0, -2.0))
	_add_box_part(sofa_detail, "ThrowBlanketFoldFront", Vector3(-0.37, 0.585, -0.49), Vector3(0.70, 0.035, 0.040), Color(0.44, 0.23, 0.22), 0.98, Vector3(0.0, 4.0, -2.0))
	_add_box_part(sofa_detail, "ThrowBlanketFoldLeft", Vector3(-0.70, 0.574, -0.22), Vector3(0.042, 0.030, 0.42), Color(0.49, 0.28, 0.25), 0.98, Vector3(0.0, 4.0, -2.0))
	for x in [-0.58, -0.42, -0.26, -0.10]:
		_add_box_part(sofa_detail, "BlanketRib", Vector3(x, 0.595, -0.23), Vector3(0.018, 0.010, 0.42), Color(0.68, 0.43, 0.36), 0.98, Vector3(0.0, 4.0, -2.0))
	_add_box_part(sofa_detail, "SofaMagazinePages", Vector3(0.52, 0.552, -0.27), Vector3(0.32, 0.018, 0.24), Color(0.88, 0.84, 0.74), 0.92, Vector3(0.0, -17.0, 0.0))
	_add_box_part(sofa_detail, "SofaMagazineCover", Vector3(0.52, 0.574, -0.27), Vector3(0.34, 0.012, 0.25), Color(0.18, 0.42, 0.46), 0.82, Vector3(0.0, -17.0, 0.0))
	_add_box_part(sofa_detail, "SofaMagazineTitleStrip", Vector3(0.52, 0.584, -0.34), Vector3(0.26, 0.006, 0.022), Color(0.78, 0.73, 0.62), 0.88, Vector3(0.0, -17.0, 0.0))

	var plant_detail := Node3D.new()
	plant_detail.name = "PlantPebbleDetails"
	plant_detail.position = Vector3(-4.78, 0.0, 2.55)
	parent.add_child(plant_detail)
	for pebble_position in [
		Vector3(-0.070, 0.705, -0.030),
		Vector3(0.030, 0.706, -0.080),
		Vector3(0.090, 0.705, 0.020),
		Vector3(-0.018, 0.708, 0.086),
		Vector3(-0.112, 0.704, 0.052),
	]:
		_add_sphere_part(plant_detail, "PotPebble", pebble_position, Vector3(0.024, 0.010, 0.020), Color(0.38, 0.34, 0.29), 0.94)

	var shelf_detail := Node3D.new()
	shelf_detail.name = "ShelfCloseupDecorDetails"
	shelf_detail.position = Vector3(FLOOR_SIZE.x * 0.5 - 0.22, 1.22, -1.85)
	parent.add_child(shelf_detail)
	_add_box_part(shelf_detail, "SmallPhotoFrameBack", Vector3(-0.12, 0.43, -0.20), Vector3(0.035, 0.30, 0.24), Color(0.12, 0.10, 0.08), 0.74, Vector3(0.0, 0.0, -6.0))
	_add_box_part(shelf_detail, "SmallPhotoFrameFace", Vector3(-0.145, 0.43, -0.20), Vector3(0.012, 0.23, 0.18), Color(0.65, 0.60, 0.49), 0.88, Vector3(0.0, 0.0, -6.0))
	_add_sphere_part(shelf_detail, "CeramicDecorSphere", Vector3(-0.12, 0.48, 0.31), Vector3(0.080, 0.080, 0.080), Color(0.54, 0.62, 0.60), 0.55)
	_add_cylinder_part(shelf_detail, "SmallCandle", Vector3(-0.12, 0.475, 0.07), 0.045, 0.045, 0.11, Color(0.84, 0.80, 0.69), 0.58)
	_add_cylinder_part(shelf_detail, "CandleWick", Vector3(-0.12, 0.540, 0.07), 0.004, 0.004, 0.030, Color(0.12, 0.10, 0.08), 0.74)


func _create_vrchat_scale_exploration_props(objects: Node) -> void:
	_create_static_box(objects, "ExplorationNorthWalkway", Vector3(0.0, 0.004, -7.15), Vector3(14.8, 0.012, 1.55), Color(0.66, 0.63, 0.56))
	_create_static_box(objects, "ExplorationEastWalkway", Vector3(8.35, 0.005, 0.0), Vector3(1.45, 0.012, 12.4), Color(0.61, 0.60, 0.54))
	_create_static_box(objects, "ExplorationRearTerraceFloor", Vector3(6.75, 0.006, 7.65), Vector3(5.2, 0.014, 3.4), Color(0.56, 0.58, 0.52))
	_create_static_box(objects, "ExplorationAlcoveFloor", Vector3(-8.65, 0.006, 7.25), Vector3(3.2, 0.014, 2.4), Color(0.58, 0.55, 0.48))
	_create_static_box(objects, "ExplorationAlcovePartition", Vector3(-7.05, 1.06, 6.25), Vector3(0.16, 2.12, 2.75), Color(0.45, 0.48, 0.45))
	_create_static_box(objects, "ExplorationHalfOutdoorRailA", Vector3(6.75, 0.58, 9.35), Vector3(5.2, 1.16, 0.13), Color(0.38, 0.36, 0.31))
	_create_static_box(objects, "ExplorationHalfOutdoorRailB", Vector3(9.35, 0.58, 7.65), Vector3(0.13, 1.16, 3.4), Color(0.38, 0.36, 0.31))

	var work_desk := _create_prop(objects, "WorkDesk", "table", Vector3(7.35, 0.0, -6.15), Vector3(2.15, 0.72, 0.95), Color(0.50, 0.36, 0.23))
	work_desk.rotation_degrees.y = -6.0
	var work_chair := _create_prop(objects, "WorkDeskChair", "chair", Vector3(7.08, 0.0, -4.92), Vector3(0.68, 0.54, 0.72), Color(0.33, 0.35, 0.37))
	work_chair.rotation_degrees.y = 176.0
	var rear_console := _create_prop(objects, "RearSearchConsole", "table", Vector3(-8.85, 0.0, 7.22), Vector3(1.72, 0.62, 0.72), Color(0.42, 0.30, 0.20))
	rear_console.rotation_degrees.y = 90.0
	var terrace_table := _create_prop(objects, "TerraceSmallTable", "table", Vector3(5.65, 0.0, 7.55), Vector3(1.05, 0.42, 0.78), Color(0.46, 0.34, 0.24))
	terrace_table.rotation_degrees.y = 18.0
	var terrace_bench := _create_prop(objects, "TerraceBench", "sofa", Vector3(7.55, 0.0, 8.20), Vector3(1.85, 0.52, 0.72), Color(0.34, 0.43, 0.51))
	terrace_bench.rotation_degrees.y = -14.0
	_create_prop(objects, "WindowTallPlant", "plant", Vector3(-9.25, 0.0, -6.38), Vector3(0.55, 1.18, 0.55), Color(0.20, 0.45, 0.29))
	_create_prop(objects, "AlcovePlant", "plant", Vector3(-9.62, 0.0, 8.14), Vector3(0.46, 0.95, 0.46), Color(0.26, 0.49, 0.32))

	var candidate_markers := Node3D.new()
	candidate_markers.name = "SmallObjectCandidatePoints"
	objects.add_child(candidate_markers)
	_add_box_part(candidate_markers, "SP01_LowTableRemoteCandidate", Vector3(3.92, 0.78, 2.30), Vector3(0.18, 0.018, 0.11), Color(0.12, 0.14, 0.16), 0.68, Vector3(0.0, 12.0, 0.0))
	_add_box_part(candidate_markers, "SP02_WorkDeskWalletCandidate", Vector3(7.70, 0.82, -6.42), Vector3(0.20, 0.020, 0.14), Color(0.18, 0.13, 0.08), 0.70, Vector3(0.0, -10.0, 0.0))
	_add_box_part(candidate_markers, "SP03_SofaSideKeyCandidate", Vector3(-6.72, 0.54, 0.84), Vector3(0.10, 0.020, 0.06), Color(0.55, 0.48, 0.24), 0.74, Vector3(0.0, 18.0, 0.0))
	_add_box_part(candidate_markers, "SP04_AlcoveLowObjectCandidate", Vector3(-8.95, 0.68, 7.45), Vector3(0.16, 0.020, 0.12), Color(0.16, 0.15, 0.14), 0.76, Vector3(0.0, -22.0, 0.0))
	_add_box_part(candidate_markers, "SP05_TerraceTableCupCandidate", Vector3(5.44, 0.54, 7.72), Vector3(0.12, 0.10, 0.12), Color(0.82, 0.78, 0.66), 0.62)


func _add_wood_grain(parent: Node, part_name_prefix: String, size: Vector3, top_y: float, dark_color: Color, light_color: Color) -> void:
	var fine_dark := _shift_color(dark_color, 0.08, 0.06, 0.03)
	var fine_light := _shift_color(light_color, -0.03, -0.02, -0.01)
	_add_box_part(parent, part_name_prefix + "Long01", Vector3(-0.20, top_y, -size.z * 0.32), Vector3(size.x * 0.72, 0.004, 0.010), fine_dark, 0.92, Vector3(0.0, 2.0, 0.0))
	_add_box_part(parent, part_name_prefix + "Long02", Vector3(0.12, top_y + 0.001, -size.z * 0.22), Vector3(size.x * 0.58, 0.004, 0.008), fine_light, 0.92, Vector3(0.0, -3.0, 0.0))
	_add_box_part(parent, part_name_prefix + "Long03", Vector3(-0.18, top_y + 0.002, -size.z * 0.10), Vector3(size.x * 0.82, 0.004, 0.009), fine_dark, 0.92, Vector3(0.0, 1.0, 0.0))
	_add_box_part(parent, part_name_prefix + "Long04", Vector3(0.20, top_y + 0.001, size.z * 0.02), Vector3(size.x * 0.70, 0.004, 0.007), fine_light, 0.92, Vector3(0.0, 3.5, 0.0))
	_add_box_part(parent, part_name_prefix + "Long05", Vector3(-0.08, top_y + 0.002, size.z * 0.15), Vector3(size.x * 0.78, 0.004, 0.010), fine_dark, 0.92, Vector3(0.0, -2.0, 0.0))
	_add_box_part(parent, part_name_prefix + "Long06", Vector3(0.28, top_y + 0.001, size.z * 0.29), Vector3(size.x * 0.52, 0.004, 0.008), fine_light, 0.92, Vector3(0.0, 2.5, 0.0))
	_add_box_part(parent, part_name_prefix + "Short01", Vector3(-size.x * 0.36, top_y + 0.002, size.z * 0.27), Vector3(size.x * 0.18, 0.004, 0.009), fine_dark, 0.94, Vector3(0.0, -4.0, 0.0))
	_add_box_part(parent, part_name_prefix + "Short02", Vector3(size.x * 0.34, top_y + 0.002, -size.z * 0.29), Vector3(size.x * 0.20, 0.004, 0.008), fine_dark, 0.94, Vector3(0.0, 4.0, 0.0))
	_add_cylinder_part(parent, part_name_prefix + "KnotOuter", Vector3(size.x * 0.18, top_y + 0.001, -size.z * 0.03), 0.075, 0.075, 0.005, fine_dark, 0.95)
	_add_cylinder_part(parent, part_name_prefix + "KnotInner", Vector3(size.x * 0.18, top_y + 0.005, -size.z * 0.03), 0.043, 0.043, 0.004, light_color, 0.95)


func _add_box_part(parent: Node, part_name: String, position: Vector3, size: Vector3, color: Color, roughness: float = 0.82, rotation_degrees_value: Vector3 = Vector3.ZERO) -> MeshInstance3D:
	var mesh := MeshInstance3D.new()
	mesh.name = part_name
	var box := BoxMesh.new()
	box.size = size
	mesh.mesh = box
	mesh.position = position
	mesh.rotation_degrees = rotation_degrees_value
	mesh.material_override = _build_furniture_material(color, roughness)
	mesh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	parent.add_child(mesh)
	return mesh


func _add_capsule_part(parent: Node, part_name: String, position: Vector3, radius: float, height: float, color: Color, roughness: float = 0.82, rotation_degrees_value: Vector3 = Vector3.ZERO) -> MeshInstance3D:
	var mesh := MeshInstance3D.new()
	mesh.name = part_name
	var capsule := CapsuleMesh.new()
	capsule.radius = radius
	capsule.height = height
	capsule.radial_segments = 24
	capsule.rings = 8
	mesh.mesh = capsule
	mesh.position = position
	mesh.rotation_degrees = rotation_degrees_value
	mesh.material_override = _build_furniture_material(color, roughness)
	mesh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	parent.add_child(mesh)
	return mesh


func _add_cylinder_part(parent: Node, part_name: String, position: Vector3, top_radius: float, bottom_radius: float, height: float, color: Color, roughness: float = 0.82, rotation_degrees_value: Vector3 = Vector3.ZERO) -> MeshInstance3D:
	var mesh := MeshInstance3D.new()
	mesh.name = part_name
	var cylinder := CylinderMesh.new()
	cylinder.top_radius = top_radius
	cylinder.bottom_radius = bottom_radius
	cylinder.height = height
	cylinder.radial_segments = 24
	mesh.mesh = cylinder
	mesh.position = position
	mesh.rotation_degrees = rotation_degrees_value
	mesh.material_override = _build_furniture_material(color, roughness)
	mesh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	parent.add_child(mesh)
	return mesh


func _add_sphere_part(parent: Node, part_name: String, position: Vector3, scale_value: Vector3, color: Color, roughness: float = 0.82, rotation_degrees_value: Vector3 = Vector3.ZERO) -> MeshInstance3D:
	var mesh := MeshInstance3D.new()
	mesh.name = part_name
	var sphere := SphereMesh.new()
	sphere.radius = 1.0
	sphere.height = 2.0
	sphere.radial_segments = 24
	sphere.rings = 12
	mesh.mesh = sphere
	mesh.position = position
	mesh.scale = scale_value
	mesh.rotation_degrees = rotation_degrees_value
	mesh.material_override = _build_furniture_material(color, roughness)
	mesh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	parent.add_child(mesh)
	return mesh


func _add_leaf_part(parent: Node, part_name: String, position: Vector3, scale_value: Vector3, color: Color, rotation_degrees_value: Vector3) -> MeshInstance3D:
	var mesh := MeshInstance3D.new()
	mesh.name = part_name
	var leaf := SphereMesh.new()
	leaf.radius = 1.0
	leaf.height = 2.0
	leaf.radial_segments = 24
	leaf.rings = 8
	mesh.mesh = leaf
	mesh.position = position
	mesh.scale = scale_value
	mesh.rotation_degrees = rotation_degrees_value
	mesh.material_override = _build_furniture_material(color, 0.9)
	mesh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	parent.add_child(mesh)
	return mesh


func _build_furniture_material(base_color: Color, roughness: float) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = base_color
	material.roughness = clampf(roughness, 0.34, 0.98)
	material.metallic = 0.0
	_set_object_property_if_present(material, "metallic_specular", 0.36)
	_set_object_property_if_present(material, "specular", 0.36)
	_set_object_property_if_present(material, "clearcoat_enabled", true)
	_set_object_property_if_present(material, "clearcoat", 0.08)
	_set_object_property_if_present(material, "clearcoat_roughness", 0.72)
	return material


func _shift_color(base_color: Color, red_delta: float, green_delta: float, blue_delta: float) -> Color:
	return Color(
		clampf(base_color.r + red_delta, 0.0, 1.0),
		clampf(base_color.g + green_delta, 0.0, 1.0),
		clampf(base_color.b + blue_delta, 0.0, 1.0),
		base_color.a
	)


func _create_expanded_room_furnishing(parent: Node) -> void:
	_create_reading_corner(parent)
	_create_studio_corner(parent)
	_create_rear_lounge(parent)
	_create_front_gallery(parent)
	_create_perimeter_landmark_clusters(parent)
	_create_wall_landmark_rhythm(parent)
	_create_soft_area_links(parent)
	_create_life_layer_props(parent)


func _create_life_layer_props(parent: Node) -> void:
	# 50m空間を「ただ広い床」ではなく、記憶・探索・会話の手がかりがある部屋として使うための生活レイヤー。
	# 中央ハブ、各ゾーン、遠距離壁際の目印を増やし、Object Grounding と対応させる。
	_create_static_box(parent, "CenterConversationRug", Vector3(0.0, 0.018, 0.0), Vector3(6.2, 0.010, 6.2), Color(0.38, 0.30, 0.20, 0.42))
	var center_clock := _create_prop(parent, "CentralRoomClockMarker", "table", Vector3(0.0, 0.0, -3.25), Vector3(0.35, 0.90, 0.18), Color(0.88, 0.78, 0.54, 1.0))
	_tag_spatial_object(center_clock, "中央時計", "center_hub", ["clock", "時計", "中央時計", "時間", "time"], "部屋の中心近くにある小さな時計。中央ハブの目印になる。")
	var welcome_board := _create_prop(parent, "CenterWelcomeBoardMarker", "table", Vector3(-2.55, 0.0, -2.10), Vector3(0.90, 0.42, 0.18), Color(0.52, 0.68, 0.62, 1.0))
	welcome_board.rotation_degrees.y = 25.0
	_tag_spatial_object(welcome_board, "中央ウェルカムボード", "center_hub", ["welcome board", "ウェルカムボード", "案内板", "中央の案内板"], "中央ハブに置かれた低い案内板。初見のユーザーが部屋の中心を把握しやすい。")
	var room_map := _create_prop(parent, "CenterRoomMapMarker", "table", Vector3(2.35, 0.0, -1.90), Vector3(0.80, 0.35, 0.18), Color(0.28, 0.45, 0.62, 1.0))
	room_map.rotation_degrees.y = -22.0
	_tag_spatial_object(room_map, "部屋の案内図", "center_hub", ["map", "案内図", "地図", "部屋の地図", "room map"], "50m空間の区画を思い出すための案内図。")
	var center_cushion := _create_prop(parent, "CenterFloorCushionMarker", "chair", Vector3(0.85, 0.0, 1.65), Vector3(0.52, 0.18, 0.52), Color(0.72, 0.42, 0.34, 1.0))
	_tag_spatial_object(center_cushion, "中央クッション", "center_hub", ["cushion", "クッション", "中央クッション", "座布団"], "中央に置いた柔らかい目印。会話の起点として使える。")

	var window_bench := _create_prop(parent, "WindowBlueBenchMarker", "sofa", Vector3(-9.60, 0.0, -8.20), Vector3(1.25, 0.40, 0.36), Color(0.30, 0.48, 0.82, 1.0))
	window_bench.rotation_degrees.y = 8.0
	_tag_spatial_object(window_bench, "窓辺の青いベンチ", "window_area", ["blue bench", "青いベンチ", "窓辺のベンチ", "window bench"], "窓辺の近くに置いた青いベンチ。窓側の距離感を示す目印。")
	var window_light_panel := _create_prop(parent, "WindowLightPanelMarker", "table", Vector3(-12.20, 0.0, -9.60), Vector3(0.32, 0.95, 0.12), Color(0.94, 0.84, 0.58, 1.0))
	_tag_spatial_object(window_light_panel, "窓辺の光パネル", "window_area", ["light panel", "光パネル", "窓辺の光", "明るい板"], "窓辺にある縦長の光の目印。視界確認時の明るいランドマーク。")

	var reading_books := _create_prop(parent, "ReadingBookStackMarker", "table", Vector3(-15.70, 0.0, -15.15), Vector3(0.40, 0.16, 0.30), Color(0.58, 0.33, 0.24, 1.0))
	_tag_spatial_object(reading_books, "読書本の束", "reading_corner", ["book stack", "本の束", "本", "読書本", "積まれた本"], "読書コーナーの小さな本の束。小物だが既知配置なので探せる対象。")
	var reading_tray := _create_prop(parent, "ReadingTeaTrayMarker", "table", Vector3(-17.10, 0.0, -13.95), Vector3(0.44, 0.08, 0.34), Color(0.68, 0.54, 0.38, 1.0))
	_tag_spatial_object(reading_tray, "読書トレイ", "reading_corner", ["tray", "トレイ", "お茶トレイ", "読書トレイ"], "読書席のそばにある低いトレイ。")

	var studio_mic := _create_prop(parent, "StudioMicrophoneMarker", "plant", Vector3(16.35, 0.0, -15.40), Vector3(0.24, 0.62, 0.24), Color(0.18, 0.20, 0.24, 1.0))
	_tag_spatial_object(studio_mic, "スタジオマイク", "studio_corner", ["microphone", "mic", "マイク", "スタジオマイク"], "スタジオデスク付近の細いマイク。声や配信の話題につながる。")
	var studio_reel := _create_prop(parent, "StudioCableReelMarker", "table", Vector3(18.05, 0.0, -13.65), Vector3(0.42, 0.34, 0.42), Color(0.30, 0.31, 0.34, 1.0))
	_tag_spatial_object(studio_reel, "スタジオケーブルリール", "studio_corner", ["cable reel", "ケーブル", "ケーブルリール", "コード"], "スタジオ側の足元にあるケーブルリール。")

	var rear_blanket := _create_prop(parent, "RearBlanketMarker", "sofa", Vector3(-3.20, 0.0, 17.85), Vector3(0.82, 0.16, 0.44), Color(0.62, 0.38, 0.52, 1.0))
	_tag_spatial_object(rear_blanket, "奥ラウンジブランケット", "rear_lounge", ["blanket", "ブランケット", "毛布", "奥の布"], "奥ラウンジのソファ近くに置いたブランケット。")
	var rear_shelf := _create_prop(parent, "RearLowShelfMarker", "table", Vector3(4.10, 0.0, 18.40), Vector3(1.05, 0.28, 0.34), Color(0.38, 0.27, 0.18, 1.0))
	_tag_spatial_object(rear_shelf, "奥ラウンジ低棚", "rear_lounge", ["low shelf", "低棚", "奥の棚", "ラウンジ棚"], "奥ラウンジ側にある低い棚。")

	var gallery_sign := _create_prop(parent, "GalleryGuideSignMarker", "table", Vector3(10.20, 0.0, 16.55), Vector3(0.78, 0.38, 0.16), Color(0.82, 0.62, 0.38, 1.0))
	gallery_sign.rotation_degrees.y = -18.0
	_tag_spatial_object(gallery_sign, "ギャラリー案内サイン", "front_gallery", ["gallery sign", "ギャラリー案内", "展示案内", "案内サイン"], "ギャラリー区画の案内サイン。展示物の近くにある。")
	var gallery_sculpture := _create_prop(parent, "GallerySmallSculptureMarker", "plant", Vector3(13.65, 0.0, 18.10), Vector3(0.36, 0.52, 0.36), Color(0.78, 0.78, 0.72, 1.0))
	_tag_spatial_object(gallery_sculpture, "小さな展示彫刻", "front_gallery", ["sculpture", "彫刻", "小さな彫刻", "展示彫刻"], "ギャラリーに置かれた小さな彫刻。")

	var terrace_lantern := _create_prop(parent, "NorthTerraceLanternMarker", "plant", Vector3(17.90, 0.0, 6.65), Vector3(0.28, 0.62, 0.28), Color(0.92, 0.70, 0.38, 1.0))
	_tag_spatial_object(terrace_lantern, "北テラスランタン", "north_terrace", ["lantern", "ランタン", "北のランタン", "テラスランタン"], "北テラスのあたたかい色のランタン。")
	var terrace_tiles := _create_prop(parent, "NorthTerraceTileMarker", "table", Vector3(19.20, 0.0, 4.35), Vector3(0.70, 0.08, 0.45), Color(0.46, 0.52, 0.50, 1.0))
	_tag_spatial_object(terrace_tiles, "北テラス床タイル", "north_terrace", ["tile", "タイル", "床タイル", "北テラスのタイル"], "北テラスの足元にある床タイルの目印。")

	var memory_album := _create_prop(parent, "WestMemoryAlbumMarker", "table", Vector3(-19.10, 0.0, 5.20), Vector3(0.50, 0.12, 0.38), Color(0.52, 0.34, 0.58, 1.0))
	_tag_spatial_object(memory_album, "西メモリーアルバム", "west_memory", ["album", "アルバム", "写真アルバム", "メモリーアルバム"], "西メモリー区画のコンソール近くに置いたアルバム。")
	var memory_lamp := _create_prop(parent, "WestMemoryLampMarker", "plant", Vector3(-20.65, 0.0, 3.80), Vector3(0.22, 0.48, 0.22), Color(0.90, 0.72, 0.46, 1.0))
	_tag_spatial_object(memory_lamp, "西メモリー小ランプ", "west_memory", ["small lamp", "小ランプ", "メモリーランプ", "西のランプ"], "西メモリー区画の小さな灯り。")

	var tool_manual := _create_prop(parent, "EastToolManualMarker", "table", Vector3(19.20, 0.0, -2.20), Vector3(0.44, 0.08, 0.34), Color(0.72, 0.72, 0.64, 1.0))
	_tag_spatial_object(tool_manual, "東ツール説明書", "east_tool", ["manual", "説明書", "ツール説明書", "手順書"], "東のツール棚にある薄い説明書。")
	var tool_meter := _create_prop(parent, "EastToolMeterMarker", "table", Vector3(20.45, 0.0, -4.20), Vector3(0.36, 0.26, 0.20), Color(0.26, 0.44, 0.50, 1.0))
	_tag_spatial_object(tool_meter, "東ツールメーター", "east_tool", ["meter", "メーター", "計器", "ツールメーター"], "東ツール区画の小さな計器。")

	var entry_coat := _create_prop(parent, "SouthEntryCoatStandMarker", "plant", Vector3(-7.80, 0.0, 20.70), Vector3(0.30, 0.86, 0.30), Color(0.32, 0.25, 0.18, 1.0))
	_tag_spatial_object(entry_coat, "南入口コートスタンド", "south_entry", ["coat stand", "コートスタンド", "入口のスタンド", "ハンガー"], "南入口側に立つ細いコートスタンド。")
	var entry_mat := _create_prop(parent, "SouthEntryFloorMatMarker", "table", Vector3(-5.80, 0.0, 21.85), Vector3(1.10, 0.06, 0.60), Color(0.42, 0.28, 0.22, 1.0))
	_tag_spatial_object(entry_mat, "南入口マット", "south_entry", ["floor mat", "マット", "入口マット", "玄関マット"], "南入口の足元にあるマット。")

	var nw_marker := _create_prop(parent, "NorthWestCornerBluePillarMarker", "plant", Vector3(-22.10, 0.0, -22.10), Vector3(0.34, 0.98, 0.34), Color(0.22, 0.42, 0.82, 1.0))
	_tag_spatial_object(nw_marker, "北西の青い柱", "north_west_corner", ["blue pillar", "青い柱", "北西の柱", "北西コーナー"], "北西角の位置を示す青い柱。")
	var ne_marker := _create_prop(parent, "NorthEastCornerGreenPillarMarker", "plant", Vector3(22.10, 0.0, -22.10), Vector3(0.34, 0.98, 0.34), Color(0.24, 0.62, 0.42, 1.0))
	_tag_spatial_object(ne_marker, "北東の緑の柱", "north_east_corner", ["green pillar", "緑の柱", "北東の柱", "北東コーナー"], "北東角の位置を示す緑の柱。")
	var sw_marker := _create_prop(parent, "SouthWestCornerRedPillarMarker", "plant", Vector3(-22.10, 0.0, 22.10), Vector3(0.34, 0.98, 0.34), Color(0.74, 0.28, 0.28, 1.0))
	_tag_spatial_object(sw_marker, "南西の赤い柱", "south_west_corner", ["red pillar", "赤い柱", "南西の柱", "南西コーナー"], "南西角の位置を示す赤い柱。")
	var se_marker := _create_prop(parent, "SouthEastCornerYellowPillarMarker", "plant", Vector3(22.10, 0.0, 22.10), Vector3(0.34, 0.98, 0.34), Color(0.86, 0.72, 0.24, 1.0))
	_tag_spatial_object(se_marker, "南東の黄色い柱", "south_east_corner", ["yellow pillar", "黄色い柱", "南東の柱", "南東コーナー"], "南東角の位置を示す黄色い柱。")


func _create_reading_corner(parent: Node) -> void:
	_create_static_box(parent, "ReadingCornerRug", Vector3(-16.5, 0.018, -2.0), Vector3(8.4, 0.014, 7.2), Color(0.40, 0.47, 0.42))
	_create_static_box(parent, "ReadingCornerRugInset", Vector3(-16.5, 0.031, -2.0), Vector3(7.7, 0.010, 6.5), Color(0.52, 0.58, 0.50))
	var chair := _create_prop(parent, "ReadingLoungeChair", "chair", Vector3(-15.2, 0.0, -1.15), Vector3(0.82, 0.62, 0.86), Color(0.40, 0.45, 0.50))
	chair.rotation_degrees.y = 28.0
	_tag_spatial_object(chair, "読書チェア", "reading_corner", ["reading", "reading chair", "読書", "椅子", "チェア", "ラウンジチェア"], "読書エリアの椅子。近くに丸テーブルとフロアランプがある。")
	var side_table := _create_prop(parent, "ReadingRoundTable", "table", Vector3(-13.65, 0.0, -0.95), Vector3(0.74, 0.42, 0.62), Color(0.43, 0.31, 0.20))
	side_table.rotation_degrees.y = -10.0
	_tag_spatial_object(side_table, "読書丸テーブル", "reading_corner", ["reading table", "round table", "読書テーブル", "丸テーブル", "テーブル"], "読書チェアの前にある小さな丸テーブル。")
	var shelf := Node3D.new()
	shelf.name = "ReadingBookshelf"
	shelf.position = Vector3(-20.95, 0.0, -2.35)
	parent.add_child(shelf)
	_add_box_part(shelf, "ShelfBody", Vector3(0.0, 1.0, 0.0), Vector3(0.46, 2.0, 3.15), Color(0.34, 0.25, 0.17), 0.82)
	for z in [-1.18, -0.42, 0.34, 1.10]:
		_add_box_part(shelf, "ShelfBoard", Vector3(-0.02, 0.58 + (z + 1.18) * 0.38, z), Vector3(0.50, 0.045, 0.64), Color(0.47, 0.34, 0.22), 0.84)
	for z in [-1.34, -1.08, -0.78, -0.50, -0.18, 0.08, 0.36, 0.64, 0.94, 1.24]:
		_add_box_part(shelf, "BookSpine", Vector3(-0.28, 0.78 + fmod(absf(z), 0.7), z), Vector3(0.055, 0.46, 0.16), Color(0.30 + fmod(absf(z), 0.3), 0.22, 0.20 + fmod(absf(z), 0.24)), 0.92)
	var lamp := Node3D.new()
	lamp.name = "ReadingFloorLamp"
	lamp.position = Vector3(-13.9, 0.0, 1.65)
	parent.add_child(lamp)
	_add_cylinder_part(lamp, "LampPole", Vector3(0.0, 0.95, 0.0), 0.025, 0.025, 1.90, Color(0.19, 0.17, 0.14), 0.72)
	_add_cylinder_part(lamp, "LampBase", Vector3(0.0, 0.04, 0.0), 0.28, 0.34, 0.08, Color(0.18, 0.15, 0.12), 0.70)
	_add_cylinder_part(lamp, "LampShade", Vector3(0.0, 1.92, 0.0), 0.42, 0.30, 0.42, STAGE_WARM_LIGHT_COLOR, 0.88)
	var reading_plant := _create_prop(parent, "ReadingCornerPlant", "plant", Vector3(-19.5, 0.0, 1.55), Vector3(0.52, 1.08, 0.52), Color(0.22, 0.47, 0.31))
	_tag_spatial_object(reading_plant, "読書エリアの植物", "reading_corner", ["reading plant", "plant", "読書植物", "植物"], "読書エリアの端にある背の低い植物。")
	var reading_lamp_marker := _create_prop(parent, "ReadingWarmLampMarker", "plant", Vector3(-18.7, 0.0, -4.85), Vector3(0.30, 0.92, 0.30), Color(0.78, 0.66, 0.42))
	_tag_spatial_object(reading_lamp_marker, "読書ランプ", "reading_corner", ["reading lamp", "reading light", "読書ランプ", "読書ライト", "ランプ"], "読書丸テーブルの近くにある暖色の小さなランプ。")


func _create_studio_corner(parent: Node) -> void:
	_create_static_box(parent, "StudioAreaRug", Vector3(15.6, 0.019, -4.4), Vector3(8.7, 0.014, 7.4), Color(0.43, 0.39, 0.33))
	var long_desk := _create_prop(parent, "StudioLongDesk", "table", Vector3(17.5, 0.0, -5.65), Vector3(2.85, 0.70, 1.02), Color(0.50, 0.35, 0.21))
	long_desk.rotation_degrees.y = -6.0
	_tag_spatial_object(long_desk, "スタジオ長机", "studio_corner", ["studio", "desk", "long desk", "作業机", "長机", "机", "デスク"], "スタジオエリアの作業机。モニター、ノート、カップが近くにある。")
	var chair := _create_prop(parent, "StudioChair", "chair", Vector3(16.95, 0.0, -4.15), Vector3(0.70, 0.55, 0.75), Color(0.28, 0.31, 0.34))
	chair.rotation_degrees.y = 172.0
	_tag_spatial_object(chair, "スタジオチェア", "studio_corner", ["studio chair", "作業椅子", "椅子", "チェア"], "スタジオ机の手前にある椅子。")
	_create_static_box(parent, "StudioMonitorPanel", Vector3(17.65, 0.96, -5.95), Vector3(0.96, 0.54, 0.06), Color(0.09, 0.11, 0.13))
	_create_static_box(parent, "StudioMonitorGlow", Vector3(17.65, 0.96, -5.985), Vector3(0.80, 0.40, 0.025), Color(0.26, 0.39, 0.46))
	_create_static_box(parent, "StudioNotebook", Vector3(16.86, 0.78, -5.35), Vector3(0.52, 0.028, 0.38), Color(0.18, 0.20, 0.22))
	_create_static_box(parent, "StudioCup", Vector3(18.38, 0.83, -5.35), Vector3(0.16, 0.18, 0.16), Color(0.82, 0.78, 0.66))
	var studio_monitor_marker := _create_prop(parent, "StudioMonitorMarker", "table", Vector3(17.65, 0.0, -6.28), Vector3(0.72, 0.52, 0.10), Color(0.12, 0.18, 0.21))
	_tag_spatial_object(studio_monitor_marker, "スタジオモニター", "studio_corner", ["studio monitor", "monitor", "display", "スタジオモニター", "モニター", "画面"], "スタジオ長机の奥にある暗いモニター。")
	var studio_plant := _create_prop(parent, "StudioTallPlant", "plant", Vector3(20.95, 0.0, -2.15), Vector3(0.56, 1.20, 0.56), Color(0.20, 0.45, 0.30))
	_tag_spatial_object(studio_plant, "スタジオの植物", "studio_corner", ["studio plant", "plant", "観葉植物", "植物"], "スタジオエリアの壁側にある植物。")
	var wall_shelf := Node3D.new()
	wall_shelf.name = "StudioWallShelf"
	wall_shelf.position = Vector3(22.78, 0.0, -6.1)
	parent.add_child(wall_shelf)
	for y in [0.95, 1.45, 1.95]:
		_add_box_part(wall_shelf, "ShelfPlank", Vector3(0.0, y, 0.0), Vector3(0.34, 0.055, 2.35), Color(0.44, 0.32, 0.22), 0.84)
	for z in [-0.72, 0.05, 0.82]:
		_add_box_part(wall_shelf, "StorageBox", Vector3(-0.20, 1.12, z), Vector3(0.28, 0.32, 0.34), Color(0.53, 0.48, 0.38), 0.90)


func _create_rear_lounge(parent: Node) -> void:
	_create_static_box(parent, "RearLoungeRug", Vector3(0.0, 0.020, 18.6), Vector3(13.8, 0.014, 7.6), Color(0.40, 0.35, 0.39))
	_create_static_box(parent, "RearLoungeRugWarmInset", Vector3(0.0, 0.033, 18.6), Vector3(12.6, 0.010, 6.6), Color(0.58, 0.43, 0.35))
	var sofa_left := _create_prop(parent, "RearLoungeSofaLeft", "sofa", Vector3(-4.75, 0.0, 18.15), Vector3(2.25, 0.58, 0.92), Color(0.36, 0.46, 0.55))
	sofa_left.rotation_degrees.y = 24.0
	_tag_spatial_object(sofa_left, "奥ラウンジ左ソファ", "rear_lounge", ["rear sofa", "left sofa", "lounge sofa", "奥ソファ", "左ソファ", "ソファ"], "奥ラウンジにある左側のソファ。")
	var sofa_right := _create_prop(parent, "RearLoungeSofaRight", "sofa", Vector3(4.75, 0.0, 18.15), Vector3(2.25, 0.58, 0.92), Color(0.39, 0.43, 0.50))
	sofa_right.rotation_degrees.y = -24.0
	_tag_spatial_object(sofa_right, "奥ラウンジ右ソファ", "rear_lounge", ["rear sofa", "right sofa", "lounge sofa", "奥ソファ", "右ソファ", "ソファ"], "奥ラウンジにある右側のソファ。")
	var low_table := _create_prop(parent, "RearLoungeLowTable", "table", Vector3(0.0, 0.0, 18.10), Vector3(1.65, 0.40, 0.92), Color(0.45, 0.31, 0.20))
	low_table.rotation_degrees.y = 90.0
	_tag_spatial_object(low_table, "奥ラウンジローテーブル", "rear_lounge", ["rear table", "low table", "coffee table", "奥テーブル", "ローテーブル", "テーブル"], "奥ラウンジの中央にあるローテーブル。左右にソファがある。")
	_create_static_box(parent, "RearLoungeBook", Vector3(-0.28, 0.50, 18.08), Vector3(0.40, 0.035, 0.26), Color(0.18, 0.24, 0.30))
	_create_static_box(parent, "RearLoungeTray", Vector3(0.46, 0.51, 18.02), Vector3(0.42, 0.028, 0.30), Color(0.56, 0.42, 0.25))
	var rear_side_table := _create_prop(parent, "RearLoungeSideTable", "table", Vector3(-7.05, 0.0, 18.15), Vector3(0.58, 0.44, 0.58), Color(0.40, 0.29, 0.20))
	_tag_spatial_object(rear_side_table, "奥ラウンジサイドテーブル", "rear_lounge", ["rear side table", "lounge side table", "side table", "奥サイドテーブル", "サイドテーブル"], "奥ラウンジ左ソファの横にある小さなサイドテーブル。")
	var rear_left_plant := _create_prop(parent, "RearLoungeLeftPlant", "plant", Vector3(-8.2, 0.0, 21.1), Vector3(0.55, 1.18, 0.55), Color(0.20, 0.46, 0.29))
	_tag_spatial_object(rear_left_plant, "奥ラウンジ左植物", "rear_lounge", ["rear plant", "left plant", "奥植物", "植物"], "奥ラウンジ左側の植物。")
	var rear_right_plant := _create_prop(parent, "RearLoungeRightPlant", "plant", Vector3(8.2, 0.0, 21.1), Vector3(0.55, 1.18, 0.55), Color(0.24, 0.50, 0.32))
	_tag_spatial_object(rear_right_plant, "奥ラウンジ右植物", "rear_lounge", ["rear plant", "right plant", "奥植物", "植物"], "奥ラウンジ右側の植物。")
	_create_static_box(parent, "RearLoungeWallArtWide", Vector3(0.0, 1.55, 24.72), Vector3(4.4, 1.16, 0.055), Color(0.36, 0.49, 0.56))
	_create_static_box(parent, "RearLoungeWallArtWarmStripe", Vector3(0.0, 1.55, 24.68), Vector3(3.7, 0.12, 0.030), STAGE_WARM_LIGHT_COLOR)
	var rear_wall_panel := _create_prop(parent, "RearLoungeWallPanelMarker", "table", Vector3(0.0, 0.0, 24.15), Vector3(1.20, 0.78, 0.12), Color(0.30, 0.42, 0.48))
	_tag_spatial_object(rear_wall_panel, "奥ラウンジ壁パネル", "rear_lounge", ["rear wall panel", "lounge panel", "wall art", "奥壁パネル", "壁パネル"], "奥ラウンジの後ろ壁にある横長の壁面パネル。")


func _create_front_gallery(parent: Node) -> void:
	_create_static_box(parent, "FrontGalleryRunner", Vector3(0.0, 0.021, -18.7), Vector3(18.4, 0.014, 4.4), Color(0.38, 0.36, 0.34))
	for i in range(5):
		var x := -8.0 + float(i) * 4.0
		_create_static_box(parent, "GalleryPlinth%02d" % i, Vector3(x, 0.35, -19.1), Vector3(0.78, 0.70, 0.78), Color(0.57, 0.55, 0.50))
		_add_sphere_part(parent, "GalleryObject%02d" % i, Vector3(x, 0.86, -19.1), Vector3(0.20, 0.28, 0.20), Color(0.38 + float(i) * 0.06, 0.42, 0.50 - float(i) * 0.03), 0.82)
		_create_static_box(parent, "GalleryWallPanel%02d" % i, Vector3(x, 1.42, -24.72), Vector3(1.22, 0.88, 0.055), Color(0.42, 0.46 + float(i) * 0.025, 0.48))
	var bench := _create_prop(parent, "FrontGalleryBench", "sofa", Vector3(0.0, 0.0, -16.15), Vector3(2.10, 0.50, 0.66), Color(0.39, 0.36, 0.32))
	bench.rotation_degrees.y = 180.0
	_tag_spatial_object(bench, "前ギャラリーベンチ", "front_gallery", ["front gallery", "gallery bench", "bench", "前ベンチ", "ギャラリー", "ベンチ"], "前ギャラリーの展示台近くにあるベンチ。")
	var gallery_art := _create_prop(parent, "FrontGalleryWallArtMarker", "table", Vector3(0.0, 0.0, -23.85), Vector3(1.30, 0.82, 0.12), Color(0.48, 0.45, 0.38))
	_tag_spatial_object(gallery_art, "前ギャラリー壁アート", "front_gallery", ["front gallery art", "gallery wall art", "wall art", "前ギャラリーアート", "壁アート", "展示"], "前ギャラリーの壁側にある展示用の壁アート。")
	var gallery_left_plant := _create_prop(parent, "GalleryLeftPlant", "plant", Vector3(-11.6, 0.0, -17.1), Vector3(0.48, 1.00, 0.48), Color(0.23, 0.48, 0.32))
	_tag_spatial_object(gallery_left_plant, "ギャラリー左植物", "front_gallery", ["gallery plant", "left plant", "前植物", "植物"], "前ギャラリー左側の植物。")
	var gallery_right_plant := _create_prop(parent, "GalleryRightPlant", "plant", Vector3(11.6, 0.0, -17.1), Vector3(0.48, 1.00, 0.48), Color(0.23, 0.48, 0.32))
	_tag_spatial_object(gallery_right_plant, "ギャラリー右植物", "front_gallery", ["gallery plant", "right plant", "前植物", "植物"], "前ギャラリー右側の植物。")


func _create_perimeter_landmark_clusters(parent: Node) -> void:
	_create_static_box(parent, "NorthTerraceRunner", Vector3(-14.0, 0.022, 14.8), Vector3(7.2, 0.012, 2.0), Color(0.50, 0.47, 0.40))
	var terrace_table := _create_prop(parent, "NorthTerraceRoundTable", "table", Vector3(-15.1, 0.0, 15.0), Vector3(0.82, 0.44, 0.82), Color(0.42, 0.31, 0.22))
	_tag_spatial_object(terrace_table, "北テラス丸テーブル", "north_terrace", ["north terrace", "terrace table", "北テラス", "丸テーブル", "テーブル"], "北側の壁寄りにある小さな丸テーブル。")
	var terrace_chair := _create_prop(parent, "NorthTerraceChair", "chair", Vector3(-13.55, 0.0, 15.65), Vector3(0.66, 0.54, 0.70), Color(0.35, 0.40, 0.44))
	terrace_chair.rotation_degrees.y = -62.0
	_tag_spatial_object(terrace_chair, "北テラスチェア", "north_terrace", ["north terrace chair", "terrace chair", "北テラス椅子", "椅子"], "北テラス丸テーブルのそばにある椅子。")
	var terrace_plant := _create_prop(parent, "NorthTerraceTallPlant", "plant", Vector3(-18.5, 0.0, 16.7), Vector3(0.52, 1.22, 0.52), Color(0.18, 0.45, 0.29))
	_tag_spatial_object(terrace_plant, "北テラスの背の高い植物", "north_terrace", ["north terrace plant", "terrace plant", "北テラス植物", "植物"], "北テラスの角にある背の高い植物。")
	var terrace_planter := _create_prop(parent, "NorthTerracePlanterMarker", "plant", Vector3(-17.15, 0.0, 14.12), Vector3(0.70, 0.46, 0.36), Color(0.24, 0.48, 0.31))
	_tag_spatial_object(terrace_planter, "北テラスプランター", "north_terrace", ["north terrace planter", "terrace planter", "planter", "北テラスプランター", "プランター"], "北テラス丸テーブルの近くにある低いプランター。")

	_create_static_box(parent, "WestMemoryRunner", Vector3(-20.6, 0.022, 8.7), Vector3(2.0, 0.012, 7.0), Color(0.43, 0.45, 0.39))
	var memory_table := _create_prop(parent, "WestMemoryConsole", "table", Vector3(-22.1, 0.0, 8.6), Vector3(0.86, 0.66, 1.80), Color(0.40, 0.29, 0.19))
	memory_table.rotation_degrees.y = 90.0
	_tag_spatial_object(memory_table, "西メモリーコンソール", "west_memory", ["west memory", "memory console", "west console", "西側棚", "西コンソール", "テーブル"], "西側の壁沿いにある細長いコンソール棚。")
	_create_static_box(parent, "WestMemoryFrameA", Vector3(-24.72, 1.50, 7.15), Vector3(0.052, 0.92, 1.05), Color(0.43, 0.39, 0.34))
	_create_static_box(parent, "WestMemoryFrameB", Vector3(-24.72, 1.50, 9.85), Vector3(0.052, 0.92, 1.05), Color(0.50, 0.46, 0.39))
	var memory_photo := _create_prop(parent, "WestMemoryPhotoPanelMarker", "table", Vector3(-23.95, 0.0, 7.15), Vector3(0.12, 0.72, 0.82), Color(0.54, 0.48, 0.39))
	memory_photo.rotation_degrees.y = 90.0
	_tag_spatial_object(memory_photo, "西メモリー写真パネル", "west_memory", ["west memory photo", "photo panel", "memory photo", "西写真パネル", "写真パネル"], "西メモリーコンソールの近くにある写真のような壁パネル。")
	var memory_plant := _create_prop(parent, "WestMemoryPlant", "plant", Vector3(-22.0, 0.0, 11.9), Vector3(0.50, 1.04, 0.50), Color(0.21, 0.48, 0.31))
	_tag_spatial_object(memory_plant, "西メモリー植物", "west_memory", ["west memory plant", "west plant", "西植物", "植物"], "西メモリー棚の近くにある植物。")

	_create_static_box(parent, "EastToolRunner", Vector3(20.5, 0.022, 8.2), Vector3(2.0, 0.012, 7.2), Color(0.44, 0.42, 0.37))
	var tool_shelf := _create_prop(parent, "EastToolShelf", "table", Vector3(22.05, 0.0, 8.25), Vector3(0.82, 0.72, 1.82), Color(0.38, 0.30, 0.22))
	tool_shelf.rotation_degrees.y = 90.0
	_tag_spatial_object(tool_shelf, "東ツール棚", "east_tool", ["east tool shelf", "tool shelf", "east shelf", "東棚", "ツール棚", "棚"], "東側の壁沿いにある小物置き棚。")
	_create_static_box(parent, "EastToolBoxA", Vector3(22.05, 0.82, 7.65), Vector3(0.40, 0.22, 0.34), Color(0.55, 0.48, 0.35))
	_create_static_box(parent, "EastToolBoxB", Vector3(22.05, 0.84, 8.72), Vector3(0.34, 0.24, 0.42), Color(0.36, 0.43, 0.50))
	var tool_box_stack := _create_prop(parent, "EastToolBoxStackMarker", "table", Vector3(21.55, 0.0, 9.62), Vector3(0.52, 0.52, 0.62), Color(0.50, 0.43, 0.32))
	tool_box_stack.rotation_degrees.y = 90.0
	_tag_spatial_object(tool_box_stack, "東ツール箱", "east_tool", ["east tool box", "tool box", "box stack", "東ツール箱", "道具箱", "箱"], "東ツール棚のそばに積まれた道具箱。")
	var tool_stool := _create_prop(parent, "EastToolStool", "chair", Vector3(20.35, 0.0, 7.15), Vector3(0.52, 0.48, 0.52), Color(0.32, 0.34, 0.35))
	_tag_spatial_object(tool_stool, "東ツールスツール", "east_tool", ["east stool", "tool stool", "スツール", "椅子"], "東ツール棚の近くにある小さなスツール。")

	_create_static_box(parent, "SouthEntryRunner", Vector3(-14.0, 0.022, -13.2), Vector3(7.6, 0.012, 2.15), Color(0.48, 0.43, 0.39))
	var entry_table := _create_prop(parent, "SouthEntrySideTable", "table", Vector3(-16.8, 0.0, -13.45), Vector3(0.86, 0.45, 0.72), Color(0.44, 0.32, 0.22))
	_tag_spatial_object(entry_table, "南入口サイドテーブル", "south_entry", ["south entry", "entry table", "入口テーブル", "サイドテーブル", "テーブル"], "南側入口寄りにあるサイドテーブル。")
	var entry_bench := _create_prop(parent, "SouthEntryBench", "sofa", Vector3(-13.5, 0.0, -12.7), Vector3(1.85, 0.46, 0.62), Color(0.36, 0.39, 0.42))
	entry_bench.rotation_degrees.y = 8.0
	_tag_spatial_object(entry_bench, "南入口ベンチ", "south_entry", ["south entry bench", "entry bench", "入口ベンチ", "ベンチ", "ソファ"], "南側入口寄りにある短いベンチ。")
	var entry_lamp := _create_prop(parent, "SouthEntryLamp", "plant", Vector3(-18.8, 0.0, -12.15), Vector3(0.42, 1.04, 0.42), Color(0.62, 0.54, 0.36))
	_tag_spatial_object(entry_lamp, "南入口ランプ", "south_entry", ["south entry lamp", "entry lamp", "入口ランプ", "ランプ"], "南入口サイドテーブルの近くにある目印のランプ。")
	var entry_sign := _create_prop(parent, "SouthEntrySignMarker", "table", Vector3(-16.05, 0.0, -15.05), Vector3(0.90, 0.62, 0.10), Color(0.58, 0.48, 0.34))
	_tag_spatial_object(entry_sign, "南入口サイン", "south_entry", ["south entry sign", "entry sign", "welcome sign", "南入口サイン", "入口サイン", "案内板"], "南入口ベンチの近くにある小さな案内サイン。")


func _create_wall_landmark_rhythm(parent: Node) -> void:
	for z in [-17.5, -10.0, -2.5, 5.0, 12.5, 20.0]:
		_create_static_box(parent, "LeftWallLightPanel", Vector3(-24.78, 1.55, z), Vector3(0.045, 0.88, 1.22), Color(0.74, 0.67, 0.50))
		_create_static_box(parent, "RightWallLightPanel", Vector3(24.78, 1.55, z), Vector3(0.045, 0.88, 1.22), Color(0.74, 0.67, 0.50))
	for x in [-18.0, -10.0, -2.0, 6.0, 14.0, 22.0]:
		_create_static_box(parent, "BackWallSlimPanel", Vector3(x, 1.48, 24.70), Vector3(1.10, 0.78, 0.050), Color(0.48, 0.53, 0.51))
		_create_static_box(parent, "FrontWallSlimPanel", Vector3(x, 1.38, -24.70), Vector3(1.04, 0.72, 0.050), Color(0.50, 0.48, 0.45))


func _create_soft_area_links(parent: Node) -> void:
	_create_static_box(parent, "CenterToRearRunner", Vector3(0.0, 0.019, 10.1), Vector3(2.0, 0.012, 13.6), Color(0.55, 0.51, 0.43))
	_create_static_box(parent, "CenterToFrontRunner", Vector3(0.0, 0.019, -10.6), Vector3(2.0, 0.012, 11.8), Color(0.52, 0.48, 0.42))
	_create_static_box(parent, "LeftReadingPath", Vector3(-10.8, 0.019, -1.0), Vector3(9.2, 0.012, 1.7), Color(0.54, 0.54, 0.48))
	_create_static_box(parent, "RightStudioPath", Vector3(10.8, 0.019, -3.8), Vector3(9.2, 0.012, 1.7), Color(0.54, 0.52, 0.47))


func _create_far_wall_landmarks(parent: Node) -> void:
	var half_x := FLOOR_SIZE.x * 0.5
	var half_z := FLOOR_SIZE.y * 0.5
	var back_bench := _create_prop(parent, "FarBackBench", "sofa", Vector3(half_x - 4.2, 0.0, half_z - 2.25), Vector3(2.10, 0.56, 0.82), Color(0.32, 0.43, 0.54))
	back_bench.rotation_degrees.y = -12.0
	var back_plant := _create_prop(parent, "FarBackPlant", "plant", Vector3(-half_x + 3.25, 0.0, half_z - 2.15), Vector3(0.52, 1.18, 0.52), Color(0.19, 0.45, 0.28))
	back_plant.rotation_degrees.y = 18.0
	var left_table := _create_prop(parent, "LeftWallReadingTable", "table", Vector3(-half_x + 2.35, 0.0, -half_z + 5.8), Vector3(1.25, 0.46, 0.82), Color(0.44, 0.31, 0.20))
	left_table.rotation_degrees.y = 90.0
	var right_plant := _create_prop(parent, "RightWallGalleryPlant", "plant", Vector3(half_x - 2.3, 0.0, -half_z + 5.6), Vector3(0.50, 1.08, 0.50), Color(0.23, 0.48, 0.31))
	right_plant.rotation_degrees.y = -20.0
	var front_bench := _create_prop(parent, "FrontEdgeBench", "sofa", Vector3(-half_x + 4.0, 0.0, -half_z + 2.1), Vector3(1.95, 0.52, 0.72), Color(0.37, 0.42, 0.48))
	front_bench.rotation_degrees.y = 174.0


func _create_wall_walk_targets(parent: Node) -> void:
	var half_x := FLOOR_SIZE.x * 0.5
	var half_z := FLOOR_SIZE.y * 0.5
	var margin := WALL_WALK_TARGET_MARGIN
	if _future_room_v008_as_active_room_enabled() and not _future_room_v008_room_only_preserve_legacy_environment():
		half_x = FUTURE_ROOM_V008_HALF_X
		half_z = FUTURE_ROOM_V008_HALF_Z
		margin = 0.55
	_create_walk_target_marker(parent, "BackWallWalkTarget", Vector3(0.0, 0.0, half_z - margin), Color(0.95, 0.72, 0.38))
	_create_walk_target_marker(parent, "FrontEdgeWalkTarget", Vector3(0.0, 0.0, -half_z + margin), Color(0.95, 0.72, 0.38))
	_create_walk_target_marker(parent, "LeftWallWalkTarget", Vector3(-half_x + margin, 0.0, 0.0), Color(0.95, 0.72, 0.38))
	_create_walk_target_marker(parent, "RightWallWalkTarget", Vector3(half_x - margin, 0.0, 0.0), Color(0.95, 0.72, 0.38))
	_create_walk_target_marker(parent, "BackLeftCornerWalkTarget", Vector3(-half_x + margin, 0.0, half_z - margin), Color(0.82, 0.68, 0.98))
	_create_walk_target_marker(parent, "BackRightCornerWalkTarget", Vector3(half_x - margin, 0.0, half_z - margin), Color(0.82, 0.68, 0.98))
	_create_walk_target_marker(parent, "FrontLeftCornerWalkTarget", Vector3(-half_x + margin, 0.0, -half_z + margin), Color(0.82, 0.68, 0.98))
	_create_walk_target_marker(parent, "FrontRightCornerWalkTarget", Vector3(half_x - margin, 0.0, -half_z + margin), Color(0.82, 0.68, 0.98))


func _create_walk_target_marker(parent: Node, node_name: String, position: Vector3, color: Color) -> Node3D:
	var marker := Node3D.new()
	marker.name = node_name
	marker.position = position
	parent.add_child(marker)

	var visual := MeshInstance3D.new()
	visual.name = "%sVisual" % node_name
	var mesh := CylinderMesh.new()
	mesh.top_radius = 0.22
	mesh.bottom_radius = 0.22
	mesh.height = 0.024
	visual.mesh = mesh
	visual.position = Vector3(0.0, 0.018, 0.0)
	visual.material_override = _build_stage_material(color, 0.76)
	marker.add_child(visual)
	return marker


func _create_future_room_v008_interaction_anchors(parent: Node) -> void:
	_create_future_room_v008_anchor(parent, "Table", "table", "中央テーブル", "central_conversation", Vector3(0.0, 0.0, -0.25), ["coffee table", "conversation table", "table", "机", "テーブル"], "conversation table in the active v008 room")
	_create_future_room_v008_anchor(parent, "Sofa", "sofa", "中央ソファ", "central_lounge", Vector3(0.0, 0.0, 0.95), ["sofa", "couch", "ソファ"], "sofa in the active v008 room")
	_create_future_room_v008_anchor(parent, "Chair_A", "chair", "左ソファ席", "central_lounge", Vector3(-2.35, 0.0, -0.25), ["left seat", "left chair", "左席", "椅子"], "left sitting anchor in the active v008 room")
	_create_future_room_v008_anchor(parent, "Chair_B", "chair", "右ソファ席", "central_lounge", Vector3(2.35, 0.0, -0.25), ["right seat", "right chair", "右席", "椅子"], "right sitting anchor in the active v008 room")
	_create_future_room_v008_anchor(parent, "Plant", "plant", "観葉植物", "left_reading", Vector3(-7.05, 0.0, 2.70), ["plant", "green", "植物", "観葉植物"], "plant anchor in the active v008 room")
	_create_future_room_v008_anchor(parent, "PlantFrontRight", "plant", "入口側右の観葉植物", "entry_right", Vector3.ZERO, ["front right plant", "入口側右の植物", "植物", "観葉植物"], "existing imported front-right plant; ground target bound from actual mesh")
	_create_future_room_v008_anchor(parent, "PlantBackLeft", "plant", "窓側左の観葉植物", "window_left", Vector3.ZERO, ["back left plant", "窓側左の植物", "植物", "観葉植物"], "existing imported back-left plant; ground target bound from actual mesh")
	_create_future_room_v008_anchor(parent, "PlantBackRight", "plant", "窓側右の観葉植物", "window_right", Vector3.ZERO, ["back right plant", "窓側右の植物", "植物", "観葉植物"], "existing imported back-right plant; ground target bound from actual mesh")
	_create_future_room_v008_anchor(parent, "SideTable", "table", "サイドテーブル", "left_reading", Vector3(-6.55, 0.0, -2.25), ["side table", "small table", "サイドテーブル"], "side table anchor in the active v008 room")
	# The imported GLB has a flipped Z convention relative to old design notes.
	# Bind invisible command anchors to actual loaded geometry, never move it.
	var room := parent.get_node_or_null("FutureRoomV008Candidate_GLB")
	if room == null:
		return
	var prefixes := _lumina_anchor_visual_prefixes()
	for anchor_name in prefixes:
		var anchor := parent.get_node_or_null(anchor_name) as Node3D
		var has_bounds := false
		var bounds := AABB()
		for raw_mesh in room.find_children("*", "MeshInstance3D", true, false):
			var mesh := raw_mesh as MeshInstance3D
			for prefix in prefixes[anchor_name]:
				if str(mesh.name).begins_with(prefix):
					var mesh_bounds: AABB = mesh.global_transform * mesh.get_aabb()
					bounds = bounds.merge(mesh_bounds) if has_bounds else mesh_bounds
					has_bounds = true
		if anchor != null and has_bounds:
			var center := bounds.get_center()
			anchor.global_position = Vector3(center.x, 0.0, center.z)
			anchor.set_meta("si_anchor_basis", "actual_imported_visual_bounds")
			anchor.set_meta("si_addressable_radius", maxf(bounds.size.x, bounds.size.z) * 0.5)
			anchor.set_meta("si_addressable_half_extents", Vector2(bounds.size.x * 0.5, bounds.size.z * 0.5))
			anchor.set_meta("si_addressable_bounds_center", center)
			print("V008_ANCHOR_BOUND ", anchor_name, " position=", anchor.global_position)


func _create_future_room_v008_anchor(parent: Node, node_name: String, object_type: String, display_name: String, area: String, position: Vector3, aliases: Array, search_hint: String) -> Node3D:
	var anchor := Node3D.new()
	anchor.name = node_name
	anchor.position = position
	anchor.add_to_group("si_addressable")
	anchor.set_meta("si_type", object_type)
	anchor.set_meta("si_display_name", display_name)
	anchor.set_meta("si_area", area)
	anchor.set_meta("si_aliases", aliases)
	anchor.set_meta("si_search_hint", search_hint)
	anchor.set_meta("si_can_approach", true)
	anchor.set_meta("si_can_sit", object_type == "chair" or object_type == "sofa")
	anchor.set_meta("si_addressable_radius", 0.65)
	anchor.set_meta("si_addressable_clearance", 0.75)
	parent.add_child(anchor)
	return anchor


func _create_static_box(parent: Node, node_name: String, position: Vector3, size: Vector3, color: Color) -> MeshInstance3D:
	var prop := Node3D.new()
	prop.name = node_name
	prop.position = position
	parent.add_child(prop)

	var mesh := BoxMesh.new()
	mesh.size = size
	var mesh_instance := MeshInstance3D.new()
	mesh_instance.name = "Mesh"
	mesh_instance.mesh = mesh
	mesh_instance.position = Vector3(0.0, size.y / 2.0, 0.0)
	mesh_instance.material_override = _build_stage_material(color, 0.9)
	mesh_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	prop.add_child(mesh_instance)
	var navigation_center_y := mesh_instance.global_position.y
	if (
		size.y >= 0.16
		and navigation_center_y - size.y * 0.5 <= 0.45
		and navigation_center_y + size.y * 0.5 >= -0.12
	):
		mesh_instance.add_to_group("si_navigation_obstacle")
		mesh_instance.set_meta("si_navigation_half_extents", Vector2(size.x / 2.0, size.z / 2.0))
		mesh_instance.set_meta("si_navigation_height", size.y)
		mesh_instance.set_meta("si_navigation_center_y", navigation_center_y)
	return mesh_instance


func _build_stage_material(base_color: Color, roughness: float) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = base_color
	material.roughness = clampf(roughness, 0.38, 1.0)
	material.metallic = 0.0
	_set_object_property_if_present(material, "metallic_specular", 0.28)
	_set_object_property_if_present(material, "specular", 0.28)
	_set_object_property_if_present(material, "uv1_triplanar", true)
	return material


func _addressable_radius_from_size(size: Vector3) -> float:
	var half_x := absf(size.x * 0.5)
	var half_z := absf(size.z * 0.5)
	return maxf(0.2, maxf(half_x, half_z))


func _create_world_controller(objects: Node) -> void:
	_world_controller = Node.new()
	_world_controller.name = "SIWorldCommandController"
	_world_controller.set_script(WorldControllerScript)
	add_child(_world_controller)
	_world_controller.set("world_environment_path", _world_controller.get_path_to(_world_environment))
	_world_controller.set("spawn_parent_path", _world_controller.get_path_to(objects))


func _create_receiver() -> void:
	_receiver = Node.new()
	_receiver.name = "SICommandReceiver"
	_receiver.set_script(ReceiverScript)
	var bridge_url := OS.get_environment("LUMINA_GODOT_BRIDGE_URL")
	if bridge_url.is_empty():
		bridge_url = "ws://127.0.0.1:8765/ws/godot"
	_receiver.set("bridge_url", bridge_url)
	add_child(_receiver)
	_receiver.set("avatar_controller_path", _receiver.get_path_to(_avatar))
	_receiver.set("camera_controller_path", _camera.get_path())
	_receiver.set("world_controller_path", _receiver.get_path_to(_world_controller))
	_receiver.set("world_state_interval_sec", DEFAULT_WORLD_STATE_INTERVAL_SEC)
	_receiver.set("active_world_state_interval_sec", DEFAULT_ACTIVE_WORLD_STATE_INTERVAL_SEC)
	_receiver.set("max_nearby_objects", 32)
	_receiver.set("max_visible_objects", 12)
	print_debug(
		"SICommandReceiver configured: bridge=%s avatar=%s camera=%s world=%s"
		% [bridge_url, _receiver.get("avatar_controller_path"), _receiver.get("camera_controller_path"), _receiver.get("world_controller_path")]
	)


func _create_player_proximity_trigger() -> void:
	if _receiver == null:
		print("SIPlayerProximityTrigger skipped: command receiver unavailable.")
		return
	if _avatar == null:
		print("SIPlayerProximityTrigger skipped: avatar unavailable.")
		return
	_proximity_trigger = Node.new()
	_proximity_trigger.name = "SIPlayerProximityTrigger"
	_proximity_trigger.set_script(ProximityTriggerScript)
	add_child(_proximity_trigger)
	_proximity_trigger.set("command_receiver_path", _proximity_trigger.get_path_to(_receiver))
	_proximity_trigger.set("avatar_path", _proximity_trigger.get_path_to(_avatar))
	_proximity_trigger.set("trigger_distance", 2.8)
	_proximity_trigger.set("stable_contact_time_sec", 0.5)
	_proximity_trigger.set("cooldown_sec", 12.0)
	_proximity_trigger.set("greeting_intent", "ユーザーが近づいてきたので、発話せずに顔を向けて軽く反応してください。")
	_proximity_trigger.set("fallback_greeting_text", "")
	_proximity_trigger.set("force_local_greeting_when_offline", false)


func _create_godot_control_panel() -> void:
	_control_panel = CanvasLayer.new()
	_control_panel.name = "SIGodotControlPanel"
	_control_panel.set_script(GodotControlPanelScript)
	add_child(_control_panel)
	_control_panel.set("player_path", _control_panel.get_path_to(_player_avatar))
	_control_panel.set("avatar_path", _control_panel.get_path_to(_avatar))


func _create_title_menu() -> void:
	_title_menu = CanvasLayer.new()
	_title_menu.name = "LuminaTitleMenu"
	_title_menu.set_script(LuminaTitleMenuScript)
	add_child(_title_menu)
	_title_menu.set("player_path", _title_menu.get_path_to(_player_avatar))
	_title_menu.set("control_panel_path", _title_menu.get_path_to(_control_panel))
	if _control_panel:
		_control_panel.set("title_menu_path", _control_panel.get_path_to(_title_menu))
