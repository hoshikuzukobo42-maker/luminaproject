extends Node
class_name EmbodiedVMCInput

const LOOPBACK_ADDRESSES := ["127.0.0.1", "::1", "localhost"]
const HEAD_ADDRESS := "/VMC/Ext/Hmd/Pos"
const CONTROLLER_ADDRESS := "/VMC/Ext/Con/Pos"
const FORBIDDEN_BONE_ADDRESS := "/VMC/Ext/Bone/Pos"

@export var enabled := false
@export var bind_address := "127.0.0.1"
@export_range(1024, 65535, 1) var port := 39539

var _gateway: Node
var _udp: PacketPeerUDP
var _listener_started := false
var _last_timestamp_ms := -1
var _accepted_messages := 0
var _ignored_avatar_bone_messages := 0
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
		return _result(false, "vmc_input_disabled")
	if _gateway == null:
		return _result(false, "tracking_gateway_unavailable")
	if not LOOPBACK_ADDRESSES.has(bind_address.strip_edges().to_lower()):
		return _result(false, "non_loopback_bind_forbidden")
	if _listener_started:
		return _result(true, "already_listening")
	_udp = PacketPeerUDP.new()
	var error := _udp.bind(port, bind_address)
	if error != OK:
		_udp = null
		return _result(false, "udp_bind_failed:%s" % error_string(error))
	_listener_started = true
	set_process(true)
	return _result(true, "listening_localhost_only")


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
		var decoded := _decode_osc_packet(_udp.get_packet())
		if not bool(decoded.get("accepted", false)):
			_decode_errors += 1
			continue
		ingest_osc_message(
			String(decoded.get("address", "")),
			decoded.get("arguments", []),
			_next_timestamp()
		)


func ingest_osc_message(address: String, arguments: Array, timestamp_ms := -1) -> Dictionary:
	if not enabled:
		return _result(false, "vmc_input_disabled")
	if _gateway == null:
		return _result(false, "tracking_gateway_unavailable")
	if address == FORBIDDEN_BONE_ADDRESS:
		_ignored_avatar_bone_messages += 1
		return _result(false, "avatar_bone_control_ignored")
	if address not in [HEAD_ADDRESS, CONTROLLER_ADDRESS]:
		return _result(false, "vmc_address_not_allowlisted:%s" % address)
	if arguments.size() < 8:
		return _result(false, "vmc_pose_argument_count_invalid")
	var tracker_name := "head"
	if address == CONTROLLER_ADDRESS:
		var device_name := String(arguments[0]).strip_edges().to_lower()
		if "left" in device_name or device_name.ends_with("_l"):
			tracker_name = "left_hand"
		elif "right" in device_name or device_name.ends_with("_r"):
			tracker_name = "right_hand"
		else:
			return _result(false, "vmc_controller_hand_unknown:%s" % device_name)
	var resolved_timestamp := timestamp_ms if timestamp_ms >= 0 else _next_timestamp()
	_last_timestamp_ms = maxi(_last_timestamp_ms, resolved_timestamp)
	var frame := {
		"schema": "lumina.user_tracking.v1",
		"timestamp_ms": resolved_timestamp,
		"user_present": true,
		tracker_name: {
			"position": [float(arguments[1]), float(arguments[2]), float(arguments[3])],
			"rotation": [float(arguments[4]), float(arguments[5]), float(arguments[6]), float(arguments[7])],
			"confidence": 1.0,
			"tracked": true,
			"gesture": "none",
		},
	}
	var result: Dictionary = _gateway.call("submit_frame", "vmc", frame)
	if bool(result.get("accepted", false)):
		_accepted_messages += 1
	return result


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"listener_started": _listener_started,
		"bind_address": bind_address,
		"port": port,
		"localhost_only": true,
		"auto_start": false,
		"accepted_messages": _accepted_messages,
		"ignored_avatar_bone_messages": _ignored_avatar_bone_messages,
		"decode_errors": _decode_errors,
		"forwards_avatar_bones": false,
	}


func _decode_osc_packet(packet: PackedByteArray) -> Dictionary:
	if packet.is_empty():
		return {"accepted": false, "reason": "empty_packet"}
	var stream := StreamPeerBuffer.new()
	stream.big_endian = true
	stream.data_array = packet
	var address := _read_osc_string(stream)
	if address.is_empty() or not address.begins_with("/"):
		return {"accepted": false, "reason": "invalid_address"}
	var tags := _read_osc_string(stream)
	if not tags.begins_with(","):
		return {"accepted": false, "reason": "invalid_type_tags"}
	var arguments: Array = []
	for index in range(1, tags.length()):
		match tags[index]:
			"s":
				arguments.append(_read_osc_string(stream))
			"f":
				if stream.get_available_bytes() < 4:
					return {"accepted": false, "reason": "truncated_float"}
				arguments.append(stream.get_float())
			"i":
				if stream.get_available_bytes() < 4:
					return {"accepted": false, "reason": "truncated_int"}
				arguments.append(stream.get_32())
			_:
				return {"accepted": false, "reason": "unsupported_osc_type:%s" % tags[index]}
	return {"accepted": true, "address": address, "arguments": arguments}


func _read_osc_string(stream: StreamPeerBuffer) -> String:
	var bytes := PackedByteArray()
	while stream.get_available_bytes() > 0:
		var byte := stream.get_u8()
		if byte == 0:
			break
		bytes.append(byte)
	while stream.get_position() % 4 != 0 and stream.get_available_bytes() > 0:
		stream.get_u8()
	return bytes.get_string_from_utf8()


func _next_timestamp() -> int:
	var now := Time.get_ticks_msec()
	if now <= _last_timestamp_ms:
		now = _last_timestamp_ms + 1
	_last_timestamp_ms = now
	return now


func _result(accepted: bool, reason: String) -> Dictionary:
	return {
		"accepted": accepted,
		"status": "accepted" if accepted else "rejected",
		"reason": reason,
		"provider": "vmc",
	}
