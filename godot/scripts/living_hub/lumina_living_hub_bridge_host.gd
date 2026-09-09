extends Node
class_name LuminaLivingHubBridgeHost

## Composes demo_main's bridge WS avatar path onto Living Hub without editing VRM assets.
## Presence keeps driving LivingHubLuminaProxy; this host follows it with a SI avatar + receiver.

const ReceiverScript = preload("res://addons/si_godot_bridge/si_command_receiver.gd")
const LivingHubMotionControllerScript = preload("res://scripts/living_hub/lumina_living_hub_motion_controller.gd")
const VRMAdapterScript = preload("res://scripts/si_vrm_avatar_adapter.gd")
const GLBAdapterScript = preload("res://scripts/si_glb_avatar_adapter.gd")
const LivingHubCameraBridgeScript = preload("res://scripts/living_hub/lumina_living_hub_camera_bridge.gd")

## The locked Lumina Exhibit product route uses the qualified direct-GLB profile
## below. Legacy VRM defaults remain only for explicitly selected non-product
## compatibility routes and never override the locked product environment.
const DEFAULT_PRODUCT_VRM := "res://assets/avatars/lumina_product_avatar_20260818/lumina_product_avatar_v1.vrm"
const DEFAULT_PRODUCT_ACCEPTANCE := "res://assets/avatars/lumina_product_avatar_20260818/lumina_product_avatar_v1_preview.accepted.local.json"
const DEFAULT_ROLLBACK_VRM := "res://assets/vrm/stella.vrm"
const PRODUCT_SOURCE_SHA256 := "ad0934a5d4f74ef6902c6e0f84c1fe1cc078be341c36584af83e3a64ec2e3fda"
const DEFAULT_GLB_MANIFEST := "res://assets/avatars/lumina_meshi_biped/animations_fixed/aogiri_v052_v10_mouthfix_cleanface_20260901/lumina_animation_manifest.json"
const DEFAULT_GLB_PROFILE := "res://assets/avatars/lumina_meshi_biped/animations_fixed/aogiri_v052_v10_mouthfix_cleanface_20260901/"
const DEFAULT_GLB_CACHE := "res://assets/avatars/lumina_meshi_biped/generated_scene_cache/animations_fixed/aogiri_v052_v10_mouthfix_cleanface_20260901/"
const DEFAULT_GLB_POLICY := "res://assets/avatars/lumina_meshi_biped/animations_fixed/aogiri_v052_v10_mouthfix_cleanface_20260901/lumina_natural_animation_policy.json"

var _proxy: CharacterBody3D
var _presence: Node
var _greeting_anchor: Node3D
var _bridge_avatar: CharacterBody3D
var _receiver: Node
var _adapter: Node3D
var _camera_bridge: Node
var _ready_logged := false
var _last_proxy_position := Vector3.ZERO
var _have_proxy_position := false


func _ready() -> void:
	name = "LivingHubBridgeHost"
	if _env_truthy("LUMINA_LIVING_HUB_NO_BRIDGE_AVATAR"):
		print("LUMINA_LIVING_HUB_BRIDGE_HOST skipped (LUMINA_LIVING_HUB_NO_BRIDGE_AVATAR)")
		return
	call_deferred("_boot")


func _boot() -> void:
	for _i in range(240):
		_proxy = get_tree().current_scene.get_node_or_null("LivingHubLuminaProxy") as CharacterBody3D
		_presence = get_tree().current_scene.get_node_or_null("LuminaPresenceController")
		_greeting_anchor = get_tree().current_scene.find_child("ANCHOR_LOBBY_GREETING", true, false) as Node3D
		if _proxy != null and _presence != null and _greeting_anchor != null:
			break
		await get_tree().process_frame
	if _proxy == null:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: LivingHubLuminaProxy missing")
		return
	if _presence == null:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: LuminaPresenceController missing")
		return
	if _greeting_anchor == null:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: ANCHOR_LOBBY_GREETING missing")
		return

	_ensure_player_group()
	_bridge_avatar = _create_bridge_avatar()
	get_tree().current_scene.add_child(_bridge_avatar)
	_bridge_avatar.global_transform = _proxy.global_transform
	if not _bridge_avatar.has_method("bind_living_hub") or not bool(_bridge_avatar.call(
		"bind_living_hub",
		_presence,
		_proxy,
		_greeting_anchor
	)):
		push_error("LUMINA_LIVING_HUB_BRIDGE_HOST: Living Hub motion controller bind failed")
		_bridge_avatar.queue_free()
		_bridge_avatar = null
		return
	_last_proxy_position = _proxy.global_position
	_have_proxy_position = true
	_hide_proxy_mesh()
	_create_receiver()
	_ready_logged = true
	var avatar_path := ""
	if _adapter != null:
		if _avatar_backend() == "glb":
			avatar_path = str(_adapter.get("glb_manifest_path"))
		else:
			avatar_path = str(_adapter.get("vrm_scene_path"))
	print(
		"LUMINA_LIVING_HUB_BRIDGE_HOST ready bridge=%s avatar=%s backend=%s path=%s"
		% [_receiver.get("bridge_url") if _receiver else "", _bridge_avatar.name, _avatar_backend(), avatar_path]
	)


func _process(delta: float) -> void:
	if _proxy == null or _bridge_avatar == null:
		return
	# Presence is the sole locomotion authority. The visible conversation avatar
	# follows the proxy and derives Walk/Idle from measured Presence movement.
	var current_position := _proxy.global_position
	var speed := 0.0
	if _have_proxy_position and delta > 0.0:
		var flat_delta := current_position - _last_proxy_position
		flat_delta.y = 0.0
		speed = flat_delta.length() / delta
	_bridge_avatar.global_position = _proxy.global_position
	_bridge_avatar.rotation.y = _proxy.rotation.y
	if _bridge_avatar.has_method("observe_presence_motion"):
		_bridge_avatar.call("observe_presence_motion", current_position, speed, speed > 0.035)
	_last_proxy_position = current_position
	_have_proxy_position = true


func _ensure_player_group() -> void:
	var player := get_tree().current_scene.get_node_or_null("WalkableUserPlayer") as Node
	if player == null:
		# Lobby may name the player differently; fall back to first CharacterBody3D in group search.
		for n in get_tree().get_nodes_in_group("player"):
			return
		var candidates := get_tree().current_scene.find_children("*", "CharacterBody3D", true, false)
		for c in candidates:
			if String(c.name).contains("Player") or String(c.name).contains("User"):
				player = c
				break
	if player != null and not player.is_in_group("player"):
		player.add_to_group("player")
		print("LUMINA_LIVING_HUB_BRIDGE_HOST: added player group to %s" % player.name)


func _create_bridge_avatar() -> CharacterBody3D:
	var avatar := CharacterBody3D.new()
	avatar.name = "LivingHubBridgeAvatar"
	avatar.set_script(LivingHubMotionControllerScript)
	avatar.collision_layer = 0
	avatar.collision_mask = 0

	var body := MeshInstance3D.new()
	body.name = "Body"
	var capsule := CapsuleMesh.new()
	capsule.radius = 0.28
	capsule.height = 1.62
	body.mesh = capsule
	body.position = Vector3(0.0, 0.81, 0.0)
	body.visible = false
	avatar.add_child(body)

	_adapter = Node3D.new()
	var backend := _avatar_backend()
	if backend == "glb":
		_adapter.name = "SIGLBAvatarAdapter"
		_adapter.set_script(GLBAdapterScript)
	else:
		_adapter.name = "SIVRMAvatarAdapter"
		_adapter.set_script(VRMAdapterScript)

	# Properties MUST be set before add_child: adapter._ready() loads the model immediately.
	if backend == "glb":
		_adapter.set("glb_manifest_path", _env_or("LUMINA_AVATAR_GLB_MANIFEST_PATH", DEFAULT_GLB_MANIFEST))
		_adapter.set("glb_profile_root", _env_or("LUMINA_AVATAR_GLB_PROFILE_ROOT", DEFAULT_GLB_PROFILE))
		_adapter.set("glb_scene_cache_root", _env_or("LUMINA_AVATAR_GLB_SCENE_CACHE_ROOT", DEFAULT_GLB_CACHE))
		_adapter.set("natural_animation_policy_path", _env_or("LUMINA_AVATAR_GLB_NATURAL_POLICY_PATH", DEFAULT_GLB_POLICY))
		_adapter.set("default_state", "idle")
		_adapter.set("talk_state", "talk_standing")
		_adapter.set("target_glb_height", 1.64)
	else:
		_adapter.set("vrm_scene_path", _selected_vrm_path())
		_adapter.set("model_yaw_degrees", _env_float("LUMINA_AVATAR_VRM_YAW_DEGREES", 0.0))

	_adapter.set("model_scale", 1.0)
	_adapter.set("align_feet_to_origin", true)
	_adapter.set("enable_tts_audio", true)
	_adapter.set("tts_voice_url", _env_or("LUMINA_TTS_VOICE_URL", "http://127.0.0.1:5056/voice-lipsync"))
	_adapter.set("tts_speed", 0.96)
	_adapter.set("enable_simple_idle_pose", true)
	_adapter.set("enable_procedural_gaze_motion", true)
	_adapter.set("animation_asset_dir", "res://assets/animation")
	_adapter.set("gesture_wave_vrma_path", _env_or("LUMINA_VRM_GESTURE_WAVE_VRMA_PATH", "res://assets/animation/gesture_wave.vrma"))
	# Relative to adapter once parented under avatar (sibling of Body).
	_adapter.set("fallback_body_path", NodePath("../Body"))

	avatar.add_child(_adapter)

	if avatar.get("vrm_adapter_path") != null:
		avatar.set("vrm_adapter_path", avatar.get_path_to(_adapter))
	if avatar.get("body_mesh_path") != null:
		avatar.set("body_mesh_path", avatar.get_path_to(body))
	# Used only for compatibility/status; LivingHub movement is routed to Presence.
	if avatar.get("default_walk_speed") != null:
		avatar.set("default_walk_speed", 1.35)
	return avatar


func _create_receiver() -> void:
	if not _create_camera_bridge():
		push_error("LUMINA_LIVING_HUB_BRIDGE_HOST: camera bridge setup failed; SI receiver not started")
		return
	_receiver = Node.new()
	_receiver.name = "SICommandReceiver"
	_receiver.set_script(ReceiverScript)
	var bridge_url := OS.get_environment("LUMINA_GODOT_BRIDGE_URL").strip_edges()
	if bridge_url.is_empty():
		bridge_url = "ws://127.0.0.1:8765/ws/godot"
	_receiver.set("bridge_url", bridge_url)
	add_child(_receiver)
	_receiver.set("avatar_controller_path", _receiver.get_path_to(_bridge_avatar))
	_receiver.set("camera_controller_path", _receiver.get_path_to(_camera_bridge))
	_receiver.set("world_state_interval_sec", 0.5)
	_receiver.set("active_world_state_interval_sec", 0.25)
	_receiver.set("max_nearby_objects", 24)
	_receiver.set("max_visible_objects", 12)
	print(
		"LUMINA_LIVING_HUB_CAMERA_BRIDGE ready camera=%s controller=%s"
		% [_camera_bridge.call("get_camera_state").get("preset_label", ""), _receiver.get("camera_controller_path")]
	)


func _create_camera_bridge() -> bool:
	var player := get_tree().current_scene.get_node_or_null("WalkableUserPlayer") as Node3D
	if player == null:
		push_error("LUMINA_LIVING_HUB_BRIDGE_HOST: WalkableUserPlayer missing for camera bridge")
		return false
	var first_person_camera := player.find_child("FirstPersonCamera", true, false) as Camera3D
	if first_person_camera == null:
		push_error("LUMINA_LIVING_HUB_BRIDGE_HOST: FirstPersonCamera missing for camera bridge")
		return false
	_camera_bridge = Node.new()
	_camera_bridge.name = "LivingHubCameraBridge"
	_camera_bridge.set_script(LivingHubCameraBridgeScript)
	add_child(_camera_bridge)
	if not _camera_bridge.has_method("setup") or not _camera_bridge.has_method("get_camera_state"):
		push_error("LUMINA_LIVING_HUB_BRIDGE_HOST: LivingHub camera bridge API unavailable")
		_camera_bridge.queue_free()
		_camera_bridge = null
		return false
	if not bool(_camera_bridge.call("setup", first_person_camera, player, _bridge_avatar)):
		push_error("LUMINA_LIVING_HUB_BRIDGE_HOST: LivingHub camera bridge rejected setup")
		_camera_bridge.queue_free()
		_camera_bridge = null
		return false
	return true


func _hide_proxy_mesh() -> void:
	if _proxy == null:
		return
	var mesh := _proxy.get_node_or_null("ProxyMesh") as MeshInstance3D
	if mesh != null:
		mesh.visible = false


func _avatar_backend() -> String:
	var raw := OS.get_environment("LUMINA_AVATAR_BACKEND").strip_edges().to_lower()
	if raw in ["glb", "vrm"]:
		return raw
	return "vrm"


func _selected_vrm_path() -> String:
	## Explicit path remains the highest-priority operator override.
	var explicit_path := OS.get_environment("LUMINA_AVATAR_VRM_PATH").strip_edges()
	if not explicit_path.is_empty():
		print("LUMINA_LIVING_HUB_BRIDGE_HOST avatar_selection=explicit path=%s" % explicit_path)
		return explicit_path

	var rollback_path := _env_or("LUMINA_AVATAR_ROLLBACK_RESOURCE", DEFAULT_ROLLBACK_VRM)
	var product_enabled := OS.get_environment("LUMINA_AVATAR_PRODUCT_ENABLED").strip_edges().to_lower()
	if product_enabled in ["0", "false", "no", "off"]:
		print("LUMINA_LIVING_HUB_BRIDGE_HOST avatar_selection=rollback reason=product_disabled path=%s" % rollback_path)
		return rollback_path

	var product_path := _env_or("LUMINA_AVATAR_PRODUCT_RESOURCE", DEFAULT_PRODUCT_VRM)
	var preview_path := _product_preview_path(product_path)
	if (
		not preview_path.is_empty()
		and ResourceLoader.exists(preview_path, "PackedScene")
		and _product_preview_is_accepted(preview_path)
	):
		print("LUMINA_LIVING_HUB_BRIDGE_HOST avatar_selection=product_preview path=%s" % preview_path)
		return preview_path

	if _env_truthy("LUMINA_AVATAR_ALLOW_RAW_VRM"):
		push_warning(
			"LUMINA_LIVING_HUB_BRIDGE_HOST: raw product VRM explicitly enabled for canary only: %s"
			% product_path
		)
		return product_path

	push_warning(
		"LUMINA_LIVING_HUB_BRIDGE_HOST: product preview is unavailable; "
		+ "using rollback avatar. Expected: %s" % preview_path
	)
	return rollback_path


func _product_preview_is_accepted(preview_path: String) -> bool:
	## Bake success alone is not enough. The structural SI-adapter canary writes an
	## atomic local marker containing the accepted preview SHA-256. A stale marker
	## cannot activate a newly rebaked or corrupted preview.
	var marker_path := _env_or("LUMINA_AVATAR_PRODUCT_ACCEPTANCE_RESOURCE", DEFAULT_PRODUCT_ACCEPTANCE)
	var marker_abs := ProjectSettings.globalize_path(marker_path)
	if not FileAccess.file_exists(marker_abs):
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: product acceptance marker missing: %s" % marker_path)
		return false
	var marker_file := FileAccess.open(marker_abs, FileAccess.READ)
	if marker_file == null:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: product acceptance marker unreadable: %s" % marker_path)
		return false
	var parsed = JSON.parse_string(marker_file.get_as_text())
	marker_file.close()
	if typeof(parsed) != TYPE_DICTIONARY:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: product acceptance marker is invalid JSON")
		return false
	var marker: Dictionary = parsed
	if not bool(marker.get("structural_canary_ok", false)):
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: product structural canary is not accepted")
		return false
	if not bool(marker.get("visual_canary_ok", false)):
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: product visual canary is not accepted")
		return false
	var evidence_path := String(marker.get("visual_evidence_path", ""))
	var evidence_sha := String(marker.get("visual_evidence_sha256", ""))
	var evidence_bytes := int(marker.get("visual_evidence_bytes", 0))
	if evidence_path.is_empty() or evidence_sha.length() != 64 or evidence_bytes <= 0:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: visual evidence metadata is invalid")
		return false
	if not FileAccess.file_exists(evidence_path):
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: visual evidence is missing")
		return false
	var evidence_file := FileAccess.open(evidence_path, FileAccess.READ)
	if evidence_file == null or evidence_file.get_length() != evidence_bytes:
		if evidence_file != null:
			evidence_file.close()
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: visual evidence size mismatch")
		return false
	evidence_file.close()
	if FileAccess.get_sha256(evidence_path) != evidence_sha:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: visual evidence SHA mismatch")
		return false
	if String(marker.get("source_sha256", "")) != PRODUCT_SOURCE_SHA256:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: product acceptance source SHA mismatch")
		return false
	if String(marker.get("preview_resource_path", "")) != preview_path:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: product acceptance preview path mismatch")
		return false
	var preview_abs := ProjectSettings.globalize_path(preview_path)
	var expected_preview_sha := String(marker.get("preview_sha256", ""))
	if expected_preview_sha.is_empty() or FileAccess.get_sha256(preview_abs) != expected_preview_sha:
		push_warning("LUMINA_LIVING_HUB_BRIDGE_HOST: product preview SHA mismatch")
		return false
	print("LUMINA_LIVING_HUB_BRIDGE_HOST avatar_acceptance=pass sha256=%s" % expected_preview_sha)
	return true


func _product_preview_path(product_path: String) -> String:
	var normalized := product_path.strip_edges()
	var lower := normalized.to_lower()
	if lower.ends_with(".scn") or lower.ends_with(".tscn"):
		return normalized
	if lower.ends_with(".vrm"):
		return normalized.substr(0, normalized.length() - 4) + "_preview.scn"
	return ""


func _env_or(key: String, default_value: String) -> String:
	var raw := OS.get_environment(key).strip_edges()
	return raw if not raw.is_empty() else default_value


func _env_float(key: String, default_value: float) -> float:
	var raw := OS.get_environment(key).strip_edges()
	if raw.is_empty():
		return default_value
	return raw.to_float()


func _env_truthy(key: String) -> bool:
	return OS.get_environment(key).strip_edges().to_lower() in ["1", "true", "yes", "on"]
