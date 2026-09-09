extends Node
class_name EmbodiedWebcamHandInput

const FRAME_FIELDS := ["schema", "timestamp_ms", "user_present", "left_hand", "right_hand"]
const HAND_FIELDS := ["landmarks", "confidence", "gesture", "tracked"]
const PALM_LANDMARKS := [0, 5, 9, 13, 17]
const GESTURE_ALIASES := {
	"": "none",
	"none": "none",
	"open_palm": "open_palm",
	"closed_fist": "closed_fist",
	"pinch": "pinch",
	"point": "point",
	"pointing_up": "point",
	"thumb_up": "thumb_up",
	"thumbs_up": "thumb_up",
	"victory": "victory",
	"offer_hand": "offer_hand",
	"high_five": "high_five",
}
const LOOPBACK_ADDRESSES := ["127.0.0.1", "::1", "localhost"]

@export var enabled := false
@export var bind_address := "127.0.0.1"
@export_range(1024, 65535, 1) var port := 8793

var _gateway: Node
var _udp: PacketPeerUDP
var _listener_started := false
var _submitted_frames := 0
var _rejected_frames := 0
var _decode_errors := 0


func _ready() -> void:
	set_process(false)


func bind_gateway(gateway: Node) -> bool:
	_gateway = gateway
	return _gateway != null and _gateway.has_method("submit_frame")


func set_enabled(next_enabled: bool) -> void:
	enabled = next_enabled
	set_process(enabled and _listener_started)
	if not enabled:
		stop_local_receiver()


func start_local_receiver() -> Dictionary:
	if not enabled:
		return _reject("webcam_input_disabled")
	if _gateway == null:
		return _reject("tracking_gateway_unavailable")
	if not LOOPBACK_ADDRESSES.has(bind_address.strip_edges().to_lower()):
		return _reject("non_loopback_bind_forbidden")
	if _listener_started:
		return {
			"accepted": true,
			"status": "accepted",
			"reason": "already_listening",
			"provider": "webcam",
		}
	_udp = PacketPeerUDP.new()
	var error := _udp.bind(port, bind_address)
	if error != OK:
		_udp = null
		return _reject("udp_bind_failed:%s" % error_string(error))
	_listener_started = true
	set_process(true)
	return {
		"accepted": true,
		"status": "accepted",
		"reason": "listening_localhost_only",
		"provider": "webcam",
	}


func stop_local_receiver() -> void:
	if _udp != null:
		_udp.close()
	_udp = null
	_listener_started = false
	set_process(false)


func _process(_delta: float) -> void:
	if not enabled or _udp == null:
		return
	while _udp.get_available_packet_count() > 0:
		var text := _udp.get_packet().get_string_from_utf8()
		var parsed = JSON.parse_string(text)
		if typeof(parsed) != TYPE_DICTIONARY:
			_decode_errors += 1
			continue
		var result := submit_landmark_frame(parsed)
		if not bool(result.get("accepted", false)):
			_decode_errors += 1


func submit_landmark_frame(raw_frame: Dictionary) -> Dictionary:
	if not enabled:
		return _reject("webcam_input_disabled")
	if _gateway == null:
		return _reject("tracking_gateway_unavailable")
	for raw_key in raw_frame.keys():
		var key := String(raw_key)
		if not FRAME_FIELDS.has(key):
			return _reject("webcam_frame_field_not_allowlisted:%s" % key)
	var frame := {
		"schema": "lumina.user_tracking.v1",
		"timestamp_ms": int(raw_frame.get("timestamp_ms", -1)),
		"user_present": bool(raw_frame.get("user_present", true)),
	}
	for hand_name in ["left_hand", "right_hand"]:
		if not raw_frame.has(hand_name):
			continue
		var hand_result := _normalize_hand(raw_frame[hand_name], hand_name)
		if not bool(hand_result.get("accepted", false)):
			return _reject(String(hand_result.get("reason", "invalid_hand")))
		frame[hand_name] = hand_result["tracker"]
	var result: Dictionary = _gateway.call("submit_frame", "webcam", frame)
	if bool(result.get("accepted", false)):
		_submitted_frames += 1
	else:
		_rejected_frames += 1
	return result


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"listener_started": _listener_started,
		"bind_address": bind_address,
		"port": port,
		"localhost_only": true,
		"auto_start": false,
		"submitted_frames": _submitted_frames,
		"rejected_frames": _rejected_frames,
		"decode_errors": _decode_errors,
		"expected_landmarks_per_hand": 21,
		"stores_raw_frames": false,
		"accepts_raw_images": false,
		"camera_auto_start": false,
		"controls_companion_skeleton": false,
	}


func _normalize_hand(raw_hand: Variant, hand_name: String) -> Dictionary:
	if typeof(raw_hand) != TYPE_DICTIONARY:
		return {"accepted": false, "reason": "webcam_hand_must_be_dictionary:%s" % hand_name}
	var hand: Dictionary = raw_hand
	for raw_key in hand.keys():
		var key := String(raw_key)
		if not HAND_FIELDS.has(key):
			return {"accepted": false, "reason": "webcam_hand_field_not_allowlisted:%s.%s" % [hand_name, key]}
	var raw_landmarks = hand.get("landmarks", [])
	if typeof(raw_landmarks) != TYPE_ARRAY or raw_landmarks.size() != 21:
		return {"accepted": false, "reason": "webcam_landmark_count_invalid:%s" % hand_name}
	var landmarks: Array = raw_landmarks
	var palm := Vector3.ZERO
	for landmark_index in PALM_LANDMARKS:
		var point_result := _parse_landmark(landmarks[landmark_index], hand_name, landmark_index)
		if not bool(point_result.get("accepted", false)):
			return point_result
		palm += point_result["point"] as Vector3
	palm /= float(PALM_LANDMARKS.size())
	var confidence := float(hand.get("confidence", 1.0))
	if not is_finite(confidence) or confidence < 0.0 or confidence > 1.0:
		return {"accepted": false, "reason": "webcam_confidence_invalid:%s" % hand_name}
	var raw_gesture := String(hand.get("gesture", "none")).strip_edges().to_lower().replace(" ", "_")
	if not GESTURE_ALIASES.has(raw_gesture):
		return {"accepted": false, "reason": "webcam_gesture_unsupported:%s.%s" % [hand_name, raw_gesture]}
	return {
		"accepted": true,
		"tracker": {
			"position": [(palm.x - 0.5) * 2.0, (0.5 - palm.y) * 2.0, palm.z],
			"confidence": confidence,
			"tracked": bool(hand.get("tracked", true)),
			"gesture": String(GESTURE_ALIASES[raw_gesture]),
		},
	}


func _parse_landmark(raw_point: Variant, hand_name: String, index: int) -> Dictionary:
	if typeof(raw_point) != TYPE_ARRAY or raw_point.size() != 3:
		return {"accepted": false, "reason": "webcam_landmark_invalid:%s.%d" % [hand_name, index]}
	var point := Vector3(float(raw_point[0]), float(raw_point[1]), float(raw_point[2]))
	if not is_finite(point.x) or not is_finite(point.y) or not is_finite(point.z):
		return {"accepted": false, "reason": "webcam_landmark_not_finite:%s.%d" % [hand_name, index]}
	if point.x < 0.0 or point.x > 1.0 or point.y < 0.0 or point.y > 1.0 or absf(point.z) > 2.0:
		return {"accepted": false, "reason": "webcam_landmark_out_of_bounds:%s.%d" % [hand_name, index]}
	return {"accepted": true, "point": point}


func _reject(reason: String) -> Dictionary:
	_rejected_frames += 1
	return {
		"accepted": false,
		"status": "rejected",
		"reason": reason,
		"provider": "webcam",
	}
