extends RefCounted
class_name SIVRMAMotionSampler
const GLB_HEADER_SIZE := 12

var valid: bool = false
var duration: float = 0.0
var last_error: String = ""

var _gltf: Dictionary = {}
var _binary: PackedByteArray = PackedByteArray()
var _tracks: Dictionary = {}


func load_vrma(source_path: String) -> bool:
	valid = false
	duration = 0.0
	last_error = ""
	_tracks.clear()

	var file := FileAccess.open(source_path, FileAccess.READ)
	if not file:
		last_error = "Could not open VRMA: %s" % source_path
		return false

	var bytes := file.get_buffer(file.get_length())
	file.close()
	if bytes.size() < GLB_HEADER_SIZE or bytes.slice(0, 4).get_string_from_ascii() != "glTF":
		last_error = "VRMA is not a GLB file: %s" % source_path
		return false

	if bytes.size() < GLB_HEADER_SIZE:
		last_error = "VRMA is too small: %s" % source_path
		return false

	var json_chunk := ""
	var bin_chunk := PackedByteArray()
	var offset := GLB_HEADER_SIZE
	while offset + 8 <= bytes.size():
		var chunk_length := int(bytes.decode_u32(offset))
		if chunk_length < 0:
			last_error = "VRMA has invalid chunk length: %s" % source_path
			return false
		offset += 4
		var chunk_type := bytes.slice(offset, offset + 4).get_string_from_ascii()
		offset += 4
		if offset + chunk_length > bytes.size():
			last_error = "VRMA chunk exceeds file size: %s" % source_path
			return false
		var chunk_data := bytes.slice(offset, offset + chunk_length)
		offset += chunk_length
		if offset % 4 != 0:
			offset += 4 - (offset % 4)
		if chunk_type == "JSON":
			json_chunk = chunk_data.get_string_from_utf8().strip_edges()
		elif chunk_type.begins_with("BIN"):
			bin_chunk = chunk_data

	if json_chunk.is_empty() or bin_chunk.is_empty():
		last_error = "VRMA is missing JSON or BIN chunk: %s" % source_path
		return false

	var parsed: Variant = JSON.parse_string(json_chunk)
	if not (parsed is Dictionary):
		last_error = "VRMA JSON parse failed: %s" % source_path
		return false

	_gltf = parsed
	_binary = bin_chunk
	_build_tracks()
	if duration <= 0.0:
		duration = 0.0
	valid = not _tracks.is_empty()
	if not valid and last_error.is_empty():
		last_error = "No usable rotation tracks were found in VRMA: %s" % source_path
	return valid


func get_human_bone_keys() -> Array:
	return _tracks.keys()


func sample(time_sec: float) -> Dictionary:
	var result := {}
	if not valid or duration <= 0.0:
		return result
	var local_time := fposmod(time_sec, duration)
	for human_key in _tracks.keys():
		var track: Dictionary = _tracks[human_key]
		if not track.has("times") or not track.has("rotations"):
			continue
		var rotation := _sample_rotation(track, local_time)
		if not _is_finite_quat(rotation):
			continue
		result[human_key] = rotation
	return result


func _build_tracks() -> void:
	var extensions: Dictionary = _gltf.get("extensions", {})
	var vrma_ext: Dictionary = extensions.get("VRMC_vrm_animation", {})
	var humanoid: Dictionary = vrma_ext.get("humanoid", {})
	var human_bones: Dictionary = humanoid.get("humanBones", {})
	var node_to_human := {}
	for human_key in human_bones.keys():
		var info: Dictionary = human_bones[human_key]
		if info.has("node"):
			node_to_human[int(info["node"])] = String(human_key)

	var animations: Array = _gltf.get("animations", [])
	if animations.is_empty():
		last_error = "VRMA has no animations"
		return

	var animation: Dictionary = animations[0]
	var channels: Array = animation.get("channels", [])
	var samplers: Array = animation.get("samplers", [])
	for channel in channels:
		var channel_dict: Dictionary = channel
		var target: Dictionary = channel_dict.get("target", {})
		if String(target.get("path", "")) != "rotation":
			continue
		var node_index := int(target.get("node", -1))
		if not node_to_human.has(node_index):
			continue
		var sampler_index := int(channel_dict.get("sampler", -1))
		if sampler_index < 0 or sampler_index >= samplers.size():
			continue
		var sampler: Dictionary = samplers[sampler_index]
		var times := _read_accessor_floats(int(sampler.get("input", -1)), 1)
		var values := _read_accessor_floats(int(sampler.get("output", -1)), 4)
		if times.is_empty() or values.size() < times.size() * 4:
			continue
		if times.size() < 2:
			continue
		if not _times_are_monotonic(times):
			continue
		if times[0] >= times[times.size() - 1]:
			continue

		var rotations: Array[Quaternion] = []
		for i in range(times.size()):
			var base := i * 4
			var rotation := Quaternion(values[base], values[base + 1], values[base + 2], values[base + 3])
			if not _is_finite_quat(rotation):
				rotations.clear()
				break
			rotations.append(rotation.normalized())
		if rotations.is_empty():
			continue
		duration = maxf(duration, times[times.size() - 1])
		_tracks[node_to_human[node_index]] = {
			"times": times,
			"rotations": rotations,
		}


func _read_accessor_floats(accessor_index: int, expected_components: int) -> PackedFloat32Array:
	var values := PackedFloat32Array()
	var accessors: Array = _gltf.get("accessors", [])
	var buffer_views: Array = _gltf.get("bufferViews", [])
	if accessor_index < 0 or accessor_index >= accessors.size():
		return values

	var accessor: Dictionary = accessors[accessor_index]
	if int(accessor.get("componentType", 0)) != 5126:
		return values
	var component_count := _component_count_for_type(String(accessor.get("type", "")))
	if component_count <= 0 or component_count != expected_components:
		return values

	var buffer_view_index := int(accessor.get("bufferView", -1))
	if buffer_view_index < 0 or buffer_view_index >= buffer_views.size():
		return values
	var buffer_view: Dictionary = buffer_views[buffer_view_index]

	var count := int(accessor.get("count", 0))
	var byte_offset := int(buffer_view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
	var byte_stride := int(buffer_view.get("byteStride", component_count * 4))
	if byte_stride <= 0:
		return PackedFloat32Array()
	for i in range(count):
		var row_offset := byte_offset + i * byte_stride
		for component in range(component_count):
			var value_offset := row_offset + component * 4
			if value_offset + 4 > _binary.size():
				return PackedFloat32Array()
			var value := _binary.decode_float(value_offset)
			if not is_finite(value):
				return PackedFloat32Array()
			values.append(value)
	return values

func _times_are_monotonic(times: PackedFloat32Array) -> bool:
	for i in range(times.size() - 1):
		if times[i + 1] < times[i]:
			return false
	return true


func _sample_rotation(track: Dictionary, time_sec: float) -> Quaternion:
	var times: PackedFloat32Array = track["times"]
	var rotations: Array = track["rotations"]
	if times.size() == 0 or rotations.is_empty():
		return Quaternion.IDENTITY
	if times.size() == 1:
		return rotations[0]
	if time_sec <= times[0]:
		return rotations[0]
	for i in range(times.size() - 1):
		var t0 := times[i]
		var t1 := times[i + 1]
		if time_sec <= t1:
			var alpha := 0.0
			if t1 > t0:
				alpha = clampf((time_sec - t0) / (t1 - t0), 0.0, 1.0)
			return (rotations[i] as Quaternion).slerp(rotations[i + 1] as Quaternion, alpha).normalized()
	return rotations[rotations.size() - 1]


func _is_finite_quat(value: Quaternion) -> bool:
	return is_finite(value.x) and is_finite(value.y) and is_finite(value.z) and is_finite(value.w)


func _component_count_for_type(type_name: String) -> int:
	match type_name:
		"SCALAR":
			return 1
		"VEC2":
			return 2
		"VEC3":
			return 3
		"VEC4":
			return 4
		_:
			return 0
