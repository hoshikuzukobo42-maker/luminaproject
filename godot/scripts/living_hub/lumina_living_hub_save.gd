extends RefCounted
class_name LuminaLivingHubSave

## Versioned Living Hub save with backup restore on corruption.

const SCHEMA_VERSION := 2
const SAVE_DIR := "user://living_hub"
const SAVE_NAME := "living_hub_state.json"
const BACKUP_NAME := "living_hub_state.bak.json"

signal save_completed(path: String)
signal load_completed(data: Dictionary)
signal restore_from_backup(path: String)
signal save_failed(reason: String)


func default_state() -> Dictionary:
	return {
		"schema_version": SCHEMA_VERSION,
		"visit_count": 0,
		"last_visit_at": "",
		"unlocked_moments": [],
		"photo_moments": [],
		"shared_activity_history": [],
		"preferred_locations": [],
		"accessibility_settings": {
			"reduced_motion": false,
			"font_scale": 1.0,
			"ui_hidden": false,
		},
		"graphics_settings": {
			"quality": "high", # low | medium | high | ultra
			"sdfgi": true,
			"ssao": true,
			"ssil": true,
			"ssr": true,
			"shadows": true,
		},
		"audio_settings": {
			"master_db": 0.0,
			"bgm_db": -8.0,
			"sfx_db": -4.0,
			"ambience_db": -10.0,
			"mute": false,
		},
		"living_space": {
			"time_mode": "blue_hour",
			"weather": "clear",
			"music_track": "observatory",
			"photo_sequence": 0,
		},
		"session": {
			"last_zone": "lobby",
			"paused": false,
		},
		"onboarding": {
			"welcome_seen": false,
		},
	}


func save_path() -> String:
	return SAVE_DIR.path_join(SAVE_NAME)


func backup_path() -> String:
	return SAVE_DIR.path_join(BACKUP_NAME)


func ensure_dir() -> void:
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(SAVE_DIR))


func load_or_default() -> Dictionary:
	ensure_dir()
	var primary := _read_json(save_path())
	if primary.get("ok", false):
		var merged := _merge_defaults(primary.get("data", {}))
		load_completed.emit(merged)
		return merged
	var backup := _read_json(backup_path())
	if backup.get("ok", false):
		restore_from_backup.emit(backup_path())
		var restored := _merge_defaults(backup.get("data", {}))
		# Re-write primary from backup so next boot is healthy.
		save_state(restored)
		load_completed.emit(restored)
		return restored
	var fresh := default_state()
	load_completed.emit(fresh)
	return fresh


func save_state(state: Dictionary) -> bool:
	ensure_dir()
	var payload := _merge_defaults(state)
	payload["schema_version"] = SCHEMA_VERSION
	var text := JSON.stringify(payload, "\t")
	var primary := save_path()
	# Rotate previous primary to backup before overwrite.
	if FileAccess.file_exists(primary):
		var prev := FileAccess.get_file_as_string(primary)
		var bak := FileAccess.open(backup_path(), FileAccess.WRITE)
		if bak != null:
			bak.store_string(prev)
			bak.close()
	var file := FileAccess.open(primary, FileAccess.WRITE)
	if file == null:
		save_failed.emit("write_failed")
		return false
	file.store_string(text + "\n")
	file.close()
	# Verify round-trip.
	var verify := _read_json(primary)
	if not verify.get("ok", false):
		save_failed.emit("verify_failed")
		return false
	save_completed.emit(primary)
	return true


func record_visit(state: Dictionary) -> Dictionary:
	var next := _merge_defaults(state)
	next["visit_count"] = int(next.get("visit_count", 0)) + 1
	next["last_visit_at"] = Time.get_datetime_string_from_system(true) + "Z"
	return next


func unlock_moment(state: Dictionary, moment_id: String) -> Dictionary:
	var next := _merge_defaults(state)
	var moments: Array = next.get("unlocked_moments", []).duplicate()
	if moment_id != "" and not moments.has(moment_id):
		moments.append(moment_id)
	next["unlocked_moments"] = moments
	return next


func append_activity(state: Dictionary, activity: Dictionary) -> Dictionary:
	var next := _merge_defaults(state)
	var history: Array = next.get("shared_activity_history", []).duplicate()
	var entry := activity.duplicate(true)
	if not entry.has("at"):
		entry["at"] = Time.get_datetime_string_from_system(true) + "Z"
	history.append(entry)
	# Keep a bounded trail.
	if history.size() > 200:
		history = history.slice(history.size() - 200, history.size())
	next["shared_activity_history"] = history
	return next


func set_preferred_location(state: Dictionary, location_id: String) -> Dictionary:
	var next := _merge_defaults(state)
	var preferred: Array = next.get("preferred_locations", []).duplicate()
	preferred.erase(location_id)
	preferred.push_front(location_id)
	if preferred.size() > 12:
		preferred = preferred.slice(0, 12)
	next["preferred_locations"] = preferred
	return next


func append_photo_moment(state: Dictionary, moment: Dictionary) -> Dictionary:
	if not bool(moment.get("ok", false)):
		return _merge_defaults(state)
	var path := str(moment.get("path", "")).strip_edges()
	if path.is_empty():
		return _merge_defaults(state)
	var absolute_path := ProjectSettings.globalize_path(path) if path.begins_with("user://") or path.begins_with("res://") else path
	if not FileAccess.file_exists(absolute_path):
		return _merge_defaults(state)
	var next := _merge_defaults(state)
	var moments: Array = next.get("photo_moments", []).duplicate(true)
	moments.append(moment.duplicate(true))
	if moments.size() > 48:
		moments = moments.slice(moments.size() - 48, moments.size())
	next["photo_moments"] = moments
	return next


func set_living_space(state: Dictionary, living_space: Dictionary) -> Dictionary:
	var next := _merge_defaults(state)
	next["living_space"] = living_space.duplicate(true)
	return next


func _merge_defaults(raw: Dictionary) -> Dictionary:
	var base := default_state()
	for key in raw.keys():
		var value = raw[key]
		if value is Dictionary and base.get(key) is Dictionary:
			var nested: Dictionary = base[key].duplicate(true)
			for nested_key in value.keys():
				nested[nested_key] = value[nested_key]
			base[key] = nested
		else:
			base[key] = value
	if int(base.get("schema_version", 0)) < SCHEMA_VERSION:
		base["schema_version"] = SCHEMA_VERSION
	return base


func _read_json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {"ok": false, "reason": "missing"}
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return {"ok": false, "reason": "open_failed"}
	var text := file.get_as_text()
	file.close()
	var parsed: Variant = JSON.parse_string(text)
	if not (parsed is Dictionary):
		return {"ok": false, "reason": "parse_failed"}
	if not parsed.has("schema_version"):
		return {"ok": false, "reason": "schema_missing"}
	return {"ok": true, "data": parsed}
