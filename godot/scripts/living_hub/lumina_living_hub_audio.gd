extends Node
class_name LuminaLivingHubAudio

## Zone ambience, layered music, footsteps, elevator cue and weather beds.

signal zone_changed(zone_id: String)
signal music_changed(track_id: String)

const ZONE_LOBBY := "lobby"
const ZONE_ELEVATOR := "elevator"
const ZONE_WINDOW := "window"
const ZONE_ROOM := "room"
const ZONE_BENCH := "bench"
const MUSIC_TRACKS: PackedStringArray = ["observatory", "city_lights", "quiet_room"]
const MUSIC_LABELS := {
	"observatory": "観測所",
	"city_lights": "街の灯",
	"quiet_room": "静かな部屋",
}

var _player: Node3D
var _current_zone := ""
var _selected_track := "observatory"
var _weather := "clear"
var _bgm_a: AudioStreamPlayer
var _bgm_b: AudioStreamPlayer
var _sfx: AudioStreamPlayer
var _ambience: AudioStreamPlayer
var _foot_timer := 0.0
var _last_player_pos := Vector3.ZERO
var _crossfade_tween: Tween
var _using_a := true
var _stream_cache: Dictionary = {}
var _settings := {
	"master_db": 0.0,
	"bgm_db": -12.0,
	"sfx_db": -6.0,
	"ambience_db": -15.0,
	"mute": false,
}
var _zones := {
	ZONE_ELEVATOR: {"center": Vector3(0.0, 0.0, -16.85), "radius": 1.8},
	ZONE_LOBBY: {"center": Vector3(0.0, 0.0, -14.0), "radius": 6.5},
	ZONE_WINDOW: {"center": Vector3(-2.7, 0.0, -13.55), "radius": 2.4},
	ZONE_BENCH: {"center": Vector3(-2.9, 0.0, -13.75), "radius": 2.0},
	ZONE_ROOM: {"center": Vector3(0.0, 0.0, -1.0), "radius": 8.0},
}


func setup(player: Node3D) -> void:
	_player = player
	_last_player_pos = player.global_position if player else Vector3.ZERO
	_ensure_players()
	apply_settings(_settings)
	set_weather(_weather)
	_play_zone_bed(ZONE_LOBBY, true)


func configure_zones(centers: Dictionary) -> void:
	for zone_id in centers.keys():
		if _zones.has(zone_id):
			_zones[zone_id]["center"] = centers[zone_id]


func apply_settings(settings: Dictionary) -> void:
	for key in settings.keys():
		_settings[key] = settings[key]
	var mute := bool(_settings.get("mute", false))
	AudioServer.set_bus_volume_db(0, -80.0 if mute else float(_settings.get("master_db", 0.0)))
	if _bgm_a:
		_bgm_a.volume_db = float(_settings.get("bgm_db", -12.0))
	if _bgm_b:
		_bgm_b.volume_db = float(_settings.get("bgm_db", -12.0))
	if _sfx:
		_sfx.volume_db = float(_settings.get("sfx_db", -6.0))
	if _ambience:
		_ambience.volume_db = float(_settings.get("ambience_db", -15.0))


func set_music_track(track_id: String, instant: bool = false) -> String:
	if not MUSIC_TRACKS.has(track_id):
		track_id = MUSIC_TRACKS[0]
	var already_playing := (_bgm_a != null and _bgm_a.playing) or (_bgm_b != null and _bgm_b.playing)
	if _selected_track == track_id and already_playing and not instant:
		music_changed.emit(_selected_track)
		return _selected_track
	_selected_track = track_id
	_play_zone_bed(_current_zone if not _current_zone.is_empty() else ZONE_LOBBY, instant)
	music_changed.emit(_selected_track)
	return _selected_track


func cycle_music_track() -> String:
	var index := MUSIC_TRACKS.find(_selected_track)
	_selected_track = MUSIC_TRACKS[(maxi(index, 0) + 1) % MUSIC_TRACKS.size()]
	_play_zone_bed(_current_zone if not _current_zone.is_empty() else ZONE_LOBBY, false)
	music_changed.emit(_selected_track)
	return _selected_track


func get_current_track() -> String:
	return _selected_track


func get_track_label(track_id: String = "") -> String:
	var key := track_id if not track_id.is_empty() else _selected_track
	return str(MUSIC_LABELS.get(key, "不明な曲"))


func set_weather(weather: String) -> void:
	_weather = weather if weather in ["clear", "overcast", "rain"] else "clear"
	_ensure_players()
	_ambience.stream = _get_weather_stream(_weather)
	_ambience.volume_db = float(_settings.get("ambience_db", -15.0))
	_ambience.play()


func play_elevator() -> void:
	_play_sfx_tone(180.0, 0.55, -9.0)
	_play_zone_bed(ZONE_ELEVATOR, false)


func play_window_ambience() -> void:
	_play_zone_bed(ZONE_WINDOW, false)


func play_footstep() -> void:
	_play_sfx_tone(90.0, 0.05, float(_settings.get("sfx_db", -6.0)) - 8.0)


func get_current_zone() -> String:
	return _current_zone


func _process(delta: float) -> void:
	if _player == null or not is_instance_valid(_player):
		return
	var zone := _detect_zone(_player.global_position)
	if zone != _current_zone:
		_current_zone = zone
		zone_changed.emit(zone)
		_play_zone_bed(zone, false)
		if zone == ZONE_ELEVATOR:
			_play_sfx_tone(180.0, 0.35, -12.0)
	_update_footsteps(delta)


func _detect_zone(pos: Vector3) -> String:
	var best := ZONE_ROOM
	var best_score := INF
	for zone_id in _zones.keys():
		var conf: Dictionary = _zones[zone_id]
		var center: Vector3 = conf["center"]
		var dist := Vector2(pos.x - center.x, pos.z - center.z).length()
		if dist <= float(conf["radius"]) and dist < best_score:
			best = str(zone_id)
			best_score = dist
	var elev: Vector3 = _zones[ZONE_ELEVATOR]["center"]
	if Vector2(pos.x - elev.x, pos.z - elev.z).length() < 1.6:
		return ZONE_ELEVATOR
	return best


func _update_footsteps(delta: float) -> void:
	var moved := Vector2(_player.global_position.x - _last_player_pos.x, _player.global_position.z - _last_player_pos.z).length()
	_last_player_pos = _player.global_position
	if moved < 0.01:
		_foot_timer = 0.0
		return
	_foot_timer += delta
	var interval := 0.42 if moved < 0.05 else 0.28
	if _foot_timer >= interval:
		_foot_timer = 0.0
		play_footstep()


func _ensure_players() -> void:
	if _bgm_a == null:
		_bgm_a = AudioStreamPlayer.new()
		_bgm_a.name = "LivingHubBgmA"
		add_child(_bgm_a)
	if _bgm_b == null:
		_bgm_b = AudioStreamPlayer.new()
		_bgm_b.name = "LivingHubBgmB"
		add_child(_bgm_b)
	if _sfx == null:
		_sfx = AudioStreamPlayer.new()
		_sfx.name = "LivingHubSfx"
		add_child(_sfx)
	if _ambience == null:
		_ambience = AudioStreamPlayer.new()
		_ambience.name = "LivingHubWeatherAmbience"
		add_child(_ambience)


func _play_zone_bed(zone_id: String, instant: bool) -> void:
	_ensure_players()
	var incoming := _bgm_b if _using_a else _bgm_a
	var outgoing := _bgm_a if _using_a else _bgm_b
	incoming.stream = _get_music_stream(_selected_track, zone_id)
	incoming.volume_db = -80.0
	incoming.play()
	if _crossfade_tween != null:
		_crossfade_tween.kill()
	var target_db := float(_settings.get("bgm_db", -12.0))
	if instant:
		incoming.volume_db = target_db
		outgoing.stop()
		_using_a = not _using_a
		return
	_crossfade_tween = create_tween()
	_crossfade_tween.set_parallel(true)
	_crossfade_tween.tween_property(incoming, "volume_db", target_db, 1.4)
	_crossfade_tween.tween_property(outgoing, "volume_db", -80.0, 1.4)
	_crossfade_tween.chain().tween_callback(func() -> void:
		outgoing.stop()
		_using_a = not _using_a
	)


func _get_music_stream(track_id: String, zone_id: String) -> AudioStreamWAV:
	var cache_key := "music_%s_%s" % [track_id, zone_id]
	if _stream_cache.has(cache_key):
		return _stream_cache[cache_key]
	var roots := {
		"observatory": [110.0, 164.0, 220.0],
		"city_lights": [130.0, 195.0, 260.0],
		"quiet_room": [98.0, 147.0, 196.0],
	}
	var zone_factor := 1.0
	match zone_id:
		ZONE_ELEVATOR:
			zone_factor = 0.84
		ZONE_WINDOW:
			zone_factor = 1.18
		ZONE_BENCH:
			zone_factor = 0.94
		ZONE_ROOM:
			zone_factor = 0.90
	var chord: Array = roots.get(track_id, roots["observatory"])
	var stream := _make_chord_stream(float(chord[0]) * zone_factor, float(chord[1]) * zone_factor, float(chord[2]) * zone_factor)
	_stream_cache[cache_key] = stream
	return stream


func _make_chord_stream(a: float, b: float, c: float) -> AudioStreamWAV:
	var sample_rate := 12000
	var duration := 6.0
	var frames := int(sample_rate * duration)
	var data := PackedByteArray()
	data.resize(frames * 2)
	for i in range(frames):
		var t := float(i) / float(sample_rate)
		var pulse := 0.78 + sin(TAU * 0.083333 * t) * 0.12
		var sample_value := sin(TAU * a * t) * 0.085 + sin(TAU * b * t + 0.7) * 0.052 + sin(TAU * c * t + 1.8) * 0.034
		sample_value += sin(TAU * (a * 0.5) * t + sin(t * 0.3) * 0.12) * 0.045
		_write_pcm16(data, i, sample_value * pulse)
	var stream := AudioStreamWAV.new()
	stream.format = AudioStreamWAV.FORMAT_16_BITS
	stream.mix_rate = sample_rate
	stream.stereo = false
	stream.loop_mode = AudioStreamWAV.LOOP_FORWARD
	stream.loop_begin = 0
	stream.loop_end = frames
	stream.data = data
	return stream


func _get_weather_stream(weather: String) -> AudioStreamWAV:
	var cache_key := "weather_%s" % weather
	if _stream_cache.has(cache_key):
		return _stream_cache[cache_key]
	var sample_rate := 12000
	var frames := sample_rate * 4
	var data := PackedByteArray()
	data.resize(frames * 2)
	var rng := RandomNumberGenerator.new()
	rng.seed = 0x4C554D49 + weather.hash()
	var filtered := 0.0
	for i in range(frames):
		var t := float(i) / float(sample_rate)
		var raw := rng.randf_range(-1.0, 1.0)
		filtered = lerpf(filtered, raw, 0.08)
		var sample_value := sin(TAU * 55.0 * t) * 0.018
		if weather == "rain":
			sample_value += raw * 0.045 + filtered * 0.11
		elif weather == "overcast":
			sample_value += filtered * 0.035
		else:
			sample_value += sin(TAU * 0.25 * t) * 0.008
		_write_pcm16(data, i, sample_value)
	var stream := AudioStreamWAV.new()
	stream.format = AudioStreamWAV.FORMAT_16_BITS
	stream.mix_rate = sample_rate
	stream.stereo = false
	stream.loop_mode = AudioStreamWAV.LOOP_FORWARD
	stream.loop_begin = 0
	stream.loop_end = frames
	stream.data = data
	_stream_cache[cache_key] = stream
	return stream


func _play_sfx_tone(freq: float, duration: float, volume_db: float) -> void:
	_ensure_players()
	_sfx.stream = _make_tone_stream(freq, duration)
	_sfx.volume_db = volume_db
	_sfx.play()


func _make_tone_stream(freq: float, duration: float) -> AudioStreamWAV:
	var sample_rate := 12000
	var frames := int(sample_rate * duration)
	var data := PackedByteArray()
	data.resize(frames * 2)
	for i in range(frames):
		var t := float(i) / float(sample_rate)
		var envelope := minf(1.0, float(i) / 280.0) * minf(1.0, float(frames - i) / 420.0)
		_write_pcm16(data, i, sin(TAU * freq * t) * envelope * 0.14)
	var stream := AudioStreamWAV.new()
	stream.format = AudioStreamWAV.FORMAT_16_BITS
	stream.mix_rate = sample_rate
	stream.stereo = false
	stream.data = data
	return stream


func _write_pcm16(data: PackedByteArray, frame: int, value: float) -> void:
	var sample := int(clampf(value, -1.0, 1.0) * 32767.0)
	data[frame * 2] = sample & 0xFF
	data[frame * 2 + 1] = (sample >> 8) & 0xFF
