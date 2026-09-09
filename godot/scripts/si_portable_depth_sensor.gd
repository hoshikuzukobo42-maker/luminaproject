extends RefCounted
class_name SIPortableDepthSensor

## Engine adapter for portable navigation depth observations.
##
## The visual model never receives this payload.  It is sampled from the
## active eye camera after the RGB frame has been rendered and is consumed only
## by the post-recognition grounding/mapping layer.  Normalized UV coordinates
## keep the payload independent from the RGB and ray-grid resolutions.

const SCHEMA_VERSION := "lumina.portable-depth-sparse-rays.v1"
const DEFAULT_SAMPLE_SIZE := Vector2i(24, 16)
const MAX_SAMPLE_SIZE := Vector2i(64, 48)
const SURFACE_ROLE_META := "si_portable_surface_role"
const SURFACE_ROLE_NAVIGATION_OBSTACLE := "navigation_obstacle"
const SURFACE_ROLE_WALKABLE_GROUND := "walkable_ground"
const SURFACE_ROLE_PHYSICAL_SURFACE := "physical_surface"
const SURFACE_ID_SEMANTICS_REVISION := "single_connected_component_world_pose_epoch_v2"

static var _surface_id_salt := PackedByteArray()


static func capture(
	camera: Camera3D,
	rgb_size: Vector2i,
	excluded_rids: Array[RID] = [],
	sample_size: Vector2i = DEFAULT_SAMPLE_SIZE,
	max_range_m: float = 30.0
) -> Dictionary:
	if camera == null or not is_instance_valid(camera) or camera.get_world_3d() == null:
		return _unavailable("camera_unavailable")
	if rgb_size.x <= 0 or rgb_size.y <= 0:
		return _unavailable("rgb_size_invalid")

	var width := clampi(sample_size.x, 1, MAX_SAMPLE_SIZE.x)
	var height := clampi(sample_size.y, 1, MAX_SAMPLE_SIZE.y)
	var ray_limit := clampf(max_range_m, 0.1, 1000.0)
	var space_state := camera.get_world_3d().direct_space_state
	var samples: Array = []
	var attempted := width * height
	# capture() is synchronous: physics pose and collision geometry cannot
	# advance between these rays.  Cache one expensive geometry digest per
	# collider/shape for this capture only; never carry it across frames/epochs.
	var capture_surface_identity_cache: Dictionary = {}

	for row in range(height):
		for column in range(width):
			var u := (float(column) + 0.5) / float(width)
			var v := (float(row) + 0.5) / float(height)
			var pixel := Vector2(u * float(rgb_size.x), v * float(rgb_size.y))
			var origin := camera.project_ray_origin(pixel)
			var direction := camera.project_ray_normal(pixel).normalized()
			if direction.length_squared() < 0.99:
				continue
			var query := PhysicsRayQueryParameters3D.create(origin, origin + direction * ray_limit)
			query.exclude = excluded_rids
			# Interaction/proximity Area3D nodes are often invisible triggers, not
			# physical surfaces.  Treat only collision bodies as metric depth solids.
			query.collide_with_areas = false
			query.collide_with_bodies = true
			var hit := space_state.intersect_ray(query)
			if hit.is_empty() or not hit.has("position"):
				continue
			var hit_position: Variant = hit["position"]
			if typeof(hit_position) != TYPE_VECTOR3:
				continue
			var distance_m := origin.distance_to(hit_position as Vector3)
			if distance_m < 0.001 or distance_m > ray_limit + 0.001:
				continue
			var sample := {
				"uv_norm": [u, v],
				"distance_m": snappedf(distance_m, 0.001),
				"confidence": 1.0,
			}
			var surface_identity := _surface_identity_for_hit_cached(
				hit, capture_surface_identity_cache
			)
			if not surface_identity.is_empty():
				sample["surface_id"] = surface_identity["surface_id"]
				sample["surface_role"] = surface_identity["surface_role"]
			samples.append(sample)

	return {
		"schema_version": SCHEMA_VERSION,
		"surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
		"available": not samples.is_empty(),
		"representation": "sparse_rays",
		"alignment": "registered_normalized_to_rgb",
		"measurement_model": "ray_range_m",
		"sample_width": width,
		"sample_height": height,
		"attempted_ray_count": attempted,
		"valid_ray_count": samples.size(),
		"max_range_m": ray_limit,
		"samples": samples,
		"source": "godot_physics_direct_space_state",
	}


static func _surface_id_for_hit(hit: Dictionary) -> String:
	## Stable only while one collision shape keeps the same world pose and
	## collision geometry in this Godot process.  A pose/geometry change creates
	## a new epoch id.  Neither names, paths nor raw engine ids cross the adapter
	## boundary; all engine-native material remains inside the salted digest.
	var collider_value: Variant = hit.get("collider", null)
	if (
		typeof(collider_value) != TYPE_OBJECT
		or not is_instance_valid(collider_value)
		or not (collider_value is CollisionObject3D)
	):
		return ""
	var collider := collider_value as CollisionObject3D
	var collider_instance_id := collider.get_instance_id()
	if collider_instance_id == 0:
		return ""
	var raw_shape_index: Variant = hit.get("shape", null)
	if typeof(raw_shape_index) != TYPE_INT:
		return ""
	var shape_index := int(raw_shape_index)
	if shape_index < 0:
		return ""
	var shape_owners := collider.get_shape_owners()
	var shape_owner_id := collider.shape_find_owner(shape_index)
	if shape_owner_id not in shape_owners:
		return ""
	var owner_geometry_epoch := _shape_owner_geometry_epoch(collider, shape_owner_id)
	if owner_geometry_epoch.is_empty():
		return ""
	if _surface_id_salt.is_empty():
		_surface_id_salt = Crypto.new().generate_random_bytes(32)
	if _surface_id_salt.is_empty():
		return ""
	# var_to_bytes retains the engine's full Transform3D scalar precision.  Do
	# not round: even sub-grid pose changes must invalidate continuity safely.
	var world_transform_epoch := var_to_bytes(collider.global_transform).hex_encode()
	if world_transform_epoch.is_empty():
		return ""
	var digest_material := "%s:%s:%d:%d:%s:%s" % [
		SURFACE_ID_SEMANTICS_REVISION,
		_surface_id_salt.hex_encode(),
		collider_instance_id,
		shape_index,
		world_transform_epoch,
		owner_geometry_epoch,
	]
	return "surface:" + digest_material.sha256_text()


static func _shape_owner_geometry_epoch(
	collider: CollisionObject3D, shape_owner_id: int
) -> String:
	## Hash the selected owner's local transform and every concrete Shape3D
	## storage property.  A CollisionShape3D owner contains one shape; owners
	## that expose an indivisible generated component may contain several.
	var shape_count := collider.shape_owner_get_shape_count(shape_owner_id)
	if shape_count <= 0:
		return ""
	var owner := collider.shape_owner_get_owner(shape_owner_id)
	if owner == null or not is_instance_valid(owner):
		return ""
	var chunks := PackedStringArray([
		"surface-geometry-epoch-v1",
		# Private generation tokens prevent a newly-created component with the
		# same shape index and numeric geometry from reusing an older id.
		"owner-token:%d" % owner.get_instance_id(),
		var_to_bytes(collider.shape_owner_get_transform(shape_owner_id)).hex_encode(),
		"disabled:%d" % int(collider.is_shape_owner_disabled(shape_owner_id)),
		"shape-count:%d" % shape_count,
	])
	for local_shape_index in range(shape_count):
		var shape := collider.shape_owner_get_shape(shape_owner_id, local_shape_index)
		if shape == null or not is_instance_valid(shape):
			return ""
		chunks.append("shape:%d:%s:%d" % [
			local_shape_index, shape.get_class(), shape.get_instance_id()
		])
		var property_names: Array[String] = []
		for raw_property in shape.get_property_list():
			if typeof(raw_property) != TYPE_DICTIONARY:
				continue
			var property: Dictionary = raw_property
			var usage := int(property.get("usage", 0))
			if (usage & PROPERTY_USAGE_STORAGE) == 0:
				continue
			var property_name := String(property.get("name", ""))
			if property_name.is_empty() or property_name in [
				"resource_name", "resource_path", "script"
			]:
				continue
			property_names.append(property_name)
		property_names.sort()
		for property_name in property_names:
			var property_value: Variant = shape.get(property_name)
			# Shape geometry is represented by scalar/vector/plane/packed-array
			# storage fields.  Refuse object/RID/callable values instead of hashing
			# unstable engine handles as if they were geometry.
			if typeof(property_value) in [
				TYPE_OBJECT, TYPE_RID, TYPE_CALLABLE, TYPE_SIGNAL
			]:
				continue
			var encoded_value := var_to_bytes(property_value).hex_encode()
			chunks.append("property:%s:%d:%s" % [
				property_name, typeof(property_value), encoded_value
			])
	return "|".join(chunks).sha256_text()


static func _surface_identity_for_hit(hit: Dictionary) -> Dictionary:
	var surface_id := _surface_id_for_hit(hit)
	if surface_id.is_empty():
		# A semantic role is meaningful only when it is tied to the same opaque
		# physical-surface identity.  Never emit a role by itself.
		return {}
	# Only this fixed semantic enum crosses the adapter boundary.  Arbitrary
	# collider metadata, names, paths and raw instance IDs never do.
	return {
		"surface_id": surface_id,
		"surface_role": _surface_role_for_hit(hit),
	}


static func _surface_identity_for_hit_cached(
	hit: Dictionary, capture_cache: Dictionary
) -> Dictionary:
	## This cache is deliberately capture-local.  A cross-capture cache keyed
	## only by engine handles could hide a pose/geometry epoch transition.
	var collider_value: Variant = hit.get("collider", null)
	var raw_shape_index: Variant = hit.get("shape", null)
	if (
		typeof(collider_value) != TYPE_OBJECT
		or not is_instance_valid(collider_value)
		or typeof(raw_shape_index) != TYPE_INT
	):
		return _surface_identity_for_hit(hit)
	var cache_key := "%d:%d" % [
		(collider_value as Object).get_instance_id(), int(raw_shape_index)
	]
	if capture_cache.has(cache_key):
		return (capture_cache[cache_key] as Dictionary).duplicate()
	var identity := _surface_identity_for_hit(hit)
	capture_cache[cache_key] = identity.duplicate()
	return identity


static func _surface_role_for_hit(hit: Dictionary) -> String:
	var collider_value: Variant = hit.get("collider", null)
	if typeof(collider_value) != TYPE_OBJECT or not is_instance_valid(collider_value):
		return SURFACE_ROLE_PHYSICAL_SURFACE
	var collider := collider_value as Object
	return _sanitize_surface_role(
		collider.get_meta(SURFACE_ROLE_META, SURFACE_ROLE_PHYSICAL_SURFACE)
	)


static func _sanitize_surface_role(raw_role: Variant) -> String:
	if typeof(raw_role) != TYPE_STRING:
		return SURFACE_ROLE_PHYSICAL_SURFACE
	var role := String(raw_role)
	if role in [
		SURFACE_ROLE_NAVIGATION_OBSTACLE,
		SURFACE_ROLE_WALKABLE_GROUND,
		SURFACE_ROLE_PHYSICAL_SURFACE,
	]:
		return role
	return SURFACE_ROLE_PHYSICAL_SURFACE


static func _unavailable(reason: String) -> Dictionary:
	return {
		"schema_version": SCHEMA_VERSION,
		"surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
		"available": false,
		"representation": "sparse_rays",
		"alignment": "registered_normalized_to_rgb",
		"measurement_model": "ray_range_m",
		"sample_width": 0,
		"sample_height": 0,
		"attempted_ray_count": 0,
		"valid_ray_count": 0,
		"max_range_m": 0.0,
		"samples": [],
		"source": "godot_physics_direct_space_state",
		"reason": reason,
	}
