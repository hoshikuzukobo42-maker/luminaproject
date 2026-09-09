extends Node
class_name EmbodiedVoicePlayer

signal segment_started(index: int, text: String)
signal segment_finished(index: int, text: String)
signal voice_finished(segment_count: int)
signal voice_failed(reason: String)

const DEFAULT_VISEMES := ["A", "I", "U", "E", "O"]

@export var enabled := false
@export_range(0.0, 1.5, 0.01) var viseme_strength := 0.88
@export_range(0.0, 0.30, 0.005) var silence_threshold := 0.04

var _face_controller: Node
var _audio_player: AudioStreamPlayer
var _queue: Array[Dictionary] = []
var _current_segment: Dictionary = {}
var _frames: Array = []
var _frame_sec := 1.0 / 75.0
var _viseme_names: Array[String] = ["A", "I", "U", "E", "O"]
var _completed_segments := 0
var _total_segments := 0
var _last_viseme := ""
var _visual_lipsync_available := false


func _ready() -> void:
	_audio_player = AudioStreamPlayer.new()
	_audio_player.name = "EmbodiedVoiceAudio"
	_audio_player.finished.connect(_on_audio_finished)
	add_child(_audio_player)


func _process(_delta: float) -> void:
	if not enabled or _current_segment.is_empty() or not _audio_player.playing:
		return
	apply_lipsync_at(_audio_player.get_playback_position())


func bind_face_controller(face_controller: Node) -> bool:
	_face_controller = face_controller
	_visual_lipsync_available = false
	if _face_controller != null and _face_controller.has_method("get_face_safety_status"):
		var safety: Dictionary = _face_controller.call("get_face_safety_status")
		_visual_lipsync_available = bool(safety.get("facial_deformation_enabled", false))
	return _face_controller != null and _face_controller.has_method("set_viseme")


func play_voice_payload(voice_payload: Dictionary) -> bool:
	if not enabled or _face_controller == null:
		return false
	var raw_segments = voice_payload.get("segments", [])
	if typeof(raw_segments) != TYPE_ARRAY or raw_segments.is_empty():
		return false
	_queue.clear()
	for raw_segment in raw_segments:
		if typeof(raw_segment) != TYPE_DICTIONARY:
			return false
		var segment: Dictionary = raw_segment
		if String(segment.get("audio_base64", "")).is_empty():
			return false
		_queue.append(segment.duplicate(true))
	_total_segments = _queue.size()
	_completed_segments = 0
	_start_next_segment()
	return true


func apply_lipsync_at(playback_seconds: float) -> void:
	if _frames.is_empty() or _face_controller == null or not _visual_lipsync_available:
		return
	var frame_index := clampi(int(round(playback_seconds / maxf(0.001, _frame_sec))), 0, _frames.size() - 1)
	var frame = _frames[frame_index]
	var level := 0.0
	var viseme_index := 0
	if typeof(frame) == TYPE_ARRAY:
		var values: Array = frame
		if values.size() > 0:
			level = float(values[0])
		if values.size() > 1:
			viseme_index = int(values[1])
	elif typeof(frame) == TYPE_DICTIONARY:
		var item: Dictionary = frame
		level = float(item.get("open", item.get("level", 0.0)))
		viseme_index = int(item.get("viseme_index", item.get("viseme", 0)))
	if level <= silence_threshold:
		_face_controller.call("clear_viseme", 0.035)
		_last_viseme = ""
		return
	viseme_index = clampi(viseme_index, 0, _viseme_names.size() - 1)
	var viseme := _viseme_names[viseme_index]
	_face_controller.call("set_viseme", viseme, clampf(level * viseme_strength, 0.0, 1.0), 0.035)
	_last_viseme = viseme


func stop() -> void:
	_queue.clear()
	_current_segment = {}
	_frames.clear()
	_audio_player.stop()
	if _face_controller != null and _face_controller.has_method("clear_viseme"):
		_face_controller.call("clear_viseme", 0.05)


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"playing": _audio_player.playing if _audio_player != null else false,
		"queued_segments": _queue.size(),
		"completed_segments": _completed_segments,
		"total_segments": _total_segments,
		"last_viseme": _last_viseme,
		"visual_lipsync_available": _visual_lipsync_available,
		"integrated_lipsync": _visual_lipsync_available,
		"audio_without_fake_mouth": not _visual_lipsync_available,
		"all_segments_are_queued": _total_segments > 0 and _completed_segments + _queue.size() + (0 if _current_segment.is_empty() else 1) == _total_segments,
	}


func _start_next_segment() -> void:
	if _queue.is_empty():
		_current_segment = {}
		_frames.clear()
		if _face_controller != null:
			_face_controller.call("clear_viseme", 0.08)
		voice_finished.emit(_completed_segments)
		return
	_current_segment = _queue.pop_front()
	var audio_bytes := Marshalls.base64_to_raw(String(_current_segment.get("audio_base64", "")))
	var stream := _audio_stream_wav_from_bytes(audio_bytes)
	if stream == null:
		voice_failed.emit("invalid_wav_segment:%s" % _completed_segments)
		stop()
		return
	var lipsync: Dictionary = _current_segment.get("lipsync", {})
	var raw_frames = lipsync.get("frames", [])
	_frames = raw_frames if typeof(raw_frames) == TYPE_ARRAY else []
	_frame_sec = float(lipsync.get("frame_sec", 0.0))
	if _frame_sec <= 0.0:
		_frame_sec = 1.0 / maxf(1.0, float(lipsync.get("fps", 75.0)))
	_viseme_names = []
	var raw_names = lipsync.get("viseme_names", [])
	if typeof(raw_names) == TYPE_ARRAY:
		for name in raw_names:
			_viseme_names.append(String(name).to_upper())
	if _viseme_names.is_empty():
		_viseme_names.assign(DEFAULT_VISEMES)
	_audio_player.stream = stream
	_audio_player.play()
	segment_started.emit(_completed_segments, String(_current_segment.get("text", "")))


func _on_audio_finished() -> void:
	var index := _completed_segments
	var text := String(_current_segment.get("text", ""))
	_completed_segments += 1
	segment_finished.emit(index, text)
	_start_next_segment()


func _audio_stream_wav_from_bytes(data: PackedByteArray) -> AudioStreamWAV:
	var info := _parse_wav_chunks(data)
	if info.is_empty() or int(info.format) != 1:
		return null
	var channels := int(info.channels)
	var bits := int(info.bits_per_sample)
	if channels < 1 or channels > 2 or bits not in [8, 16]:
		return null
	var stream := AudioStreamWAV.new()
	stream.mix_rate = int(info.sample_rate)
	stream.stereo = channels == 2
	stream.format = AudioStreamWAV.FORMAT_8_BITS if bits == 8 else AudioStreamWAV.FORMAT_16_BITS
	stream.data = data.slice(int(info.pcm_offset), int(info.pcm_offset) + int(info.pcm_size))
	return stream


func _parse_wav_chunks(data: PackedByteArray) -> Dictionary:
	if data.size() < 44 or _ascii_chunk(data, 0, 4) != "RIFF" or _ascii_chunk(data, 8, 4) != "WAVE":
		return {}
	var fmt_offset := -1
	var fmt_size := 0
	var pcm_offset := -1
	var pcm_size := 0
	var offset := 12
	while offset + 8 <= data.size():
		var chunk_id := _ascii_chunk(data, offset, 4)
		var chunk_size := _u32_le(data, offset + 4)
		var chunk_data_offset := offset + 8
		if chunk_data_offset + chunk_size > data.size():
			break
		if chunk_id == "fmt ":
			fmt_offset = chunk_data_offset
			fmt_size = chunk_size
		elif chunk_id == "data":
			pcm_offset = chunk_data_offset
			pcm_size = chunk_size
			break
		offset = chunk_data_offset + chunk_size + (chunk_size % 2)
	if fmt_offset < 0 or fmt_size < 16 or pcm_offset < 0 or pcm_size <= 0:
		return {}
	return {
		"format": _u16_le(data, fmt_offset),
		"channels": _u16_le(data, fmt_offset + 2),
		"sample_rate": _u32_le(data, fmt_offset + 4),
		"bits_per_sample": _u16_le(data, fmt_offset + 14),
		"pcm_offset": pcm_offset,
		"pcm_size": pcm_size,
	}


func _ascii_chunk(data: PackedByteArray, offset: int, length: int) -> String:
	if offset < 0 or offset + length > data.size():
		return ""
	return data.slice(offset, offset + length).get_string_from_ascii()


func _u16_le(data: PackedByteArray, offset: int) -> int:
	if offset + 2 > data.size():
		return 0
	return int(data[offset]) | (int(data[offset + 1]) << 8)


func _u32_le(data: PackedByteArray, offset: int) -> int:
	if offset + 4 > data.size():
		return 0
	return int(data[offset]) | (int(data[offset + 1]) << 8) | (int(data[offset + 2]) << 16) | (int(data[offset + 3]) << 24)
