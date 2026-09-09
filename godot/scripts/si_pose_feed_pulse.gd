extends Node
class_name SiPoseFeedPulse

## Phase 2 main main: 5-second pose pulse to bridge.
##
## Usage:
##   var pulse = SiPoseFeedPulse.new()
##   pulse.bridge_url = "http://127.0.0.1:8000"
##   pulse.avatar = $LuminaAvatar
##   pulse.start()
##
## To stop: pulse.stop()
##
## Sends POST to {bridge_url}/avatar/pose-pulse with JSON:
##   {
##     "schema": "lumina.pose_pulse.v0",
##     "timestamp_iso": "2026-06-28T01:00:00Z",
##     "avatar_position": {"x":0.0, "y":0.0, "z":-4.45},
##     "avatar_rotation_y_degrees": 180.0,
##     "current_posture": "idle",
##     "movement_mode": "idle",
##     "facing_user": null,
##     "is_talking": false,
##     "is_moving": false,
##     "glb_profile_root": "res://assets/avatars/lumina_meshi_biped/animations_user_fixed_visual_20260624/"
##   }

@export var bridge_url: String = "http://127.0.0.1:8000"
@export var pulse_interval_seconds: float = 5.0
@export var enabled: bool = false  ## OFF by default; operator opt-in
@export var pose_endpoint: String = "/avatar/pose-pulse"

var avatar: Node = null  ## set externally to the avatar node (SIDemoAvatarController or similar)
var _timer: Timer = null
var _http: HTTPRequest = null


func _ready() -> void:
	_timer = Timer.new()
	_timer.wait_time = pulse_interval_seconds
	_timer.autostart = false
	_timer.one_shot = false
	_timer.timeout.connect(_on_pulse_tick)
	add_child(_timer)

	_http = HTTPRequest.new()
	add_child(_http)


func start() -> void:
	if not enabled:
		push_warning("[SiPoseFeedPulse] enabled=false; not starting")
		return
	if _timer:
		_timer.start()


func stop() -> void:
	if _timer:
		_timer.stop()


func _on_pulse_tick() -> void:
	var pulse_payload = _snapshot_pulse()
	var body = JSON.stringify(pulse_payload)
	if _http:
		_http.request(
			bridge_url + pose_endpoint,
			["Content-Type: application/json"],
			HTTPClient.METHOD_POST,
			body
		)


func _snapshot_pulse() -> Dictionary:
	var ts = Time.get_datetime_string_from_system(true) + "Z"
	var avatar_position = {"x": 0.0, "y": 0.0, "z": 0.0}
	var avatar_rotation_y = 0.0
	var current_posture = "unknown"
	var movement_mode = "unknown"
	var is_talking = false
	var is_moving = false
	var glb_profile_root = ""

	if avatar:
		if avatar is Node3D:
			var pos = (avatar as Node3D).global_position
			avatar_position = {"x": pos.x, "y": pos.y, "z": pos.z}
			avatar_rotation_y = rad_to_deg((avatar as Node3D).rotation.y)
		if avatar.has_meta("current_posture"):
			current_posture = String(avatar.get_meta("current_posture"))
		if avatar.has_meta("movement_mode"):
			movement_mode = String(avatar.get_meta("movement_mode"))
		if "is_talking" in avatar:
			is_talking = avatar.is_talking
		if "is_moving" in avatar:
			is_moving = avatar.is_moving
		if avatar.has_meta("glb_profile_root"):
			glb_profile_root = String(avatar.get_meta("glb_profile_root"))

	return {
		"schema": "lumina.pose_pulse.v0",
		"timestamp_iso": ts,
		"avatar_position": avatar_position,
		"avatar_rotation_y_degrees": avatar_rotation_y,
		"current_posture": current_posture,
		"movement_mode": movement_mode,
		"facing_user": null,
		"is_talking": is_talking,
		"is_moving": is_moving,
		"glb_profile_root": glb_profile_root,
	}
