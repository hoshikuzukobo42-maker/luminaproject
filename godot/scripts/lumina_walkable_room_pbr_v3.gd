extends Node

const REVISION := "pbr_v4_balanced_forward_plus"


func _ready() -> void:
	call_deferred("_apply_forward_plus_environment")


func _apply_forward_plus_environment() -> void:
	var root := get_tree().current_scene
	if root == null:
		return
	var world_environment := _find_first_class(root, "WorldEnvironment") as WorldEnvironment
	if world_environment == null:
		world_environment = WorldEnvironment.new()
		world_environment.name = "PBR World Environment"
		root.add_child(world_environment)
	if world_environment.environment == null:
		world_environment.environment = Environment.new()
	var environment := world_environment.environment
	_set_if_available(environment, "background_mode", Environment.BG_COLOR)
	_set_if_available(environment, "background_color", Color("08131b"))
	_set_if_available(environment, "background_energy_multiplier", 0.58)
	_set_if_available(environment, "ambient_light_source", Environment.AMBIENT_SOURCE_COLOR)
	_set_if_available(environment, "ambient_light_color", Color("8299aa"))
	_set_if_available(environment, "ambient_light_energy", 0.62)
	_set_if_available(environment, "reflected_light_source", Environment.REFLECTION_SOURCE_BG)
	_set_if_available(environment, "tonemap_mode", Environment.TONE_MAPPER_AGX)
	_set_if_available(environment, "tonemap_exposure", 1.62)
	_set_if_available(environment, "tonemap_agx_contrast", 1.08)
	_set_if_available(environment, "adjustment_enabled", true)
	_set_if_available(environment, "adjustment_brightness", 1.08)
	_set_if_available(environment, "adjustment_contrast", 1.03)
	_set_if_available(environment, "adjustment_saturation", 1.02)
	_set_if_available(environment, "ssao_enabled", true)
	_set_if_available(environment, "ssao_radius", 1.05)
	_set_if_available(environment, "ssao_intensity", 1.35)
	_set_if_available(environment, "ssao_power", 1.15)
	_set_if_available(environment, "ssao_detail", 0.72)
	_set_if_available(environment, "ssil_enabled", true)
	_set_if_available(environment, "ssil_radius", 2.2)
	_set_if_available(environment, "ssil_intensity", 0.82)
	_set_if_available(environment, "ssil_sharpness", 0.86)
	_set_if_available(environment, "ssr_enabled", true)
	_set_if_available(environment, "ssr_max_steps", 72)
	_set_if_available(environment, "ssr_fade_in", 0.18)
	_set_if_available(environment, "ssr_fade_out", 1.45)
	_set_if_available(environment, "sdfgi_enabled", true)
	_set_if_available(environment, "sdfgi_use_occlusion", true)
	_set_if_available(environment, "sdfgi_read_sky_light", true)
	_set_if_available(environment, "sdfgi_energy", 0.72)
	_set_if_available(environment, "sdfgi_bounce_feedback", 0.34)
	_set_if_available(environment, "sdfgi_cascades", 4)
	_set_if_available(environment, "sdfgi_min_cell_size", 0.2)
	_set_if_available(environment, "sdfgi_max_distance", 32.0)
	_set_if_available(environment, "glow_enabled", true)
	_set_if_available(environment, "glow_normalized", true)
	_set_if_available(environment, "glow_intensity", 0.10)
	_set_if_available(environment, "glow_bloom", 0.015)
	_configure_geometry_and_materials(root)
	_configure_existing_lights(root)
	_add_room_reflection_probe(root)
	_add_room_architectural_lights(root)
	var renderer := RenderingServer.get_current_rendering_method()
	print("lumina_pbr=ready revision=%s renderer=%s balanced_exposure=true material_tuning=true" % [REVISION, renderer])


func _configure_geometry_and_materials(node: Node) -> void:
	for child in node.get_children():
		if child is GeometryInstance3D:
			var geometry := child as GeometryInstance3D
			geometry.gi_mode = GeometryInstance3D.GI_MODE_STATIC
			var lower_name := str(child.name).to_lower()
			if "panorama" in lower_name or "bay tower" in lower_name or "tower light" in lower_name:
				geometry.gi_mode = GeometryInstance3D.GI_MODE_DISABLED
			if child is MeshInstance3D:
				_tune_mesh_materials(child as MeshInstance3D)
		_configure_geometry_and_materials(child)


func _tune_mesh_materials(mesh_instance: MeshInstance3D) -> void:
	if mesh_instance.mesh == null:
		return
	for surface in range(mesh_instance.mesh.get_surface_count()):
		var source := mesh_instance.mesh.surface_get_material(surface)
		if source == null or not source is BaseMaterial3D:
			continue
		var material := source.duplicate() as BaseMaterial3D
		var key := (str(source.resource_name) + " " + str(mesh_instance.name)).to_lower()
		if "floor" in key or "marble" in key:
			_set_if_available(material, "roughness", 0.48)
			_set_if_available(material, "metallic", 0.02)
		elif "plaster" in key or "ceiling" in key:
			_set_if_available(material, "roughness", 0.72)
			_set_if_available(material, "metallic", 0.0)
		elif "walnut" in key or "wood" in key:
			_set_if_available(material, "roughness", 0.56)
			_set_if_available(material, "metallic", 0.0)
		elif "woven" in key or "fabric" in key or "rug" in key:
			_set_if_available(material, "roughness", 0.86)
			_set_if_available(material, "metallic", 0.0)
		elif "bronze" in key:
			_set_if_available(material, "roughness", 0.38)
			_set_if_available(material, "metallic", 0.72)
		elif "glass" in key:
			_set_if_available(material, "roughness", 0.12)
			_set_if_available(material, "metallic", 0.0)
		elif "light" in key or "display" in key or "screen" in key:
			_set_if_available(material, "emission_energy_multiplier", 0.75)
		mesh_instance.set_surface_override_material(surface, material)


func _configure_existing_lights(node: Node) -> void:
	for child in node.get_children():
		if child is Light3D:
			var light := child as Light3D
			light.shadow_enabled = true
			_set_if_available(light, "shadow_bias", 0.025)
			_set_if_available(light, "shadow_normal_bias", 0.95)
			_set_if_available(light, "light_volumetric_fog_energy", 0.36)
		_configure_existing_lights(child)


func _add_room_reflection_probe(root: Node) -> void:
	if root.get_node_or_null("PBR Room Reflection Probe") != null:
		return
	var probe := ReflectionProbe.new()
	probe.name = "PBR Room Reflection Probe"
	probe.position = Vector3(0.0, 1.55, 0.0)
	probe.size = Vector3(13.4, 3.1, 9.4)
	probe.box_projection = true
	probe.interior = true
	probe.enable_shadows = true
	probe.intensity = 0.68
	probe.max_distance = 18.0
	probe.update_mode = ReflectionProbe.UPDATE_ONCE
	root.add_child(probe)


func _add_room_architectural_lights(root: Node) -> void:
	if root.get_node_or_null("PBR Room Architectural Lights") != null:
		return
	var rig := Node3D.new()
	rig.name = "PBR Room Architectural Lights"
	root.add_child(rig)
	_add_spot(rig, "Reading pool", Vector3(-4.9, 2.95, -0.4), Vector3(-5.0, 0.72, -2.1), Color("ffd7ab"), 5.80, 7.0, 46.0)
	_add_spot(rig, "Lounge pool", Vector3(0.0, 3.05, 0.5), Vector3(0.0, 0.62, -0.4), Color("ffe1bd"), 5.20, 6.8, 52.0)
	_add_spot(rig, "Studio pool", Vector3(5.0, 2.95, -0.4), Vector3(5.0, 0.78, -2.25), Color("b8d9ff"), 5.40, 7.0, 44.0)
	_add_omni(rig, "Central bounced fill", Vector3(0.0, 2.35, 1.1), Color("ffe5c9"), 4.20, 7.5)
	var moon := DirectionalLight3D.new()
	moon.name = "Window moonlight"
	moon.rotation_degrees = Vector3(-38.0, -52.0, 0.0)
	moon.light_color = Color("9bbbe0")
	moon.light_energy = 0.58
	moon.shadow_enabled = true
	moon.directional_shadow_max_distance = 24.0
	rig.add_child(moon)


func _add_spot(parent: Node3D, light_name: String, source_position: Vector3, target: Vector3, color: Color, energy: float, range_value: float, angle: float) -> void:
	var light := SpotLight3D.new()
	light.name = light_name
	light.position = source_position
	light.light_color = color
	light.light_energy = energy
	light.spot_range = range_value
	light.spot_angle = angle
	light.spot_angle_attenuation = 0.82
	light.shadow_enabled = true
	light.light_size = 0.28
	light.shadow_bias = 0.025
	parent.add_child(light)
	light.look_at(target, Vector3.UP)


func _add_omni(parent: Node3D, light_name: String, source_position: Vector3, color: Color, energy: float, range_value: float) -> void:
	var light := OmniLight3D.new()
	light.name = light_name
	light.position = source_position
	light.light_color = color
	light.light_energy = energy
	light.omni_range = range_value
	light.omni_attenuation = 1.55
	light.shadow_enabled = true
	light.light_size = 0.32
	light.shadow_bias = 0.025
	parent.add_child(light)


func _find_first_class(node: Node, query_class: StringName) -> Node:
	if node.is_class(query_class):
		return node
	for child in node.get_children():
		var found := _find_first_class(child, query_class)
		if found != null:
			return found
	return null


func _set_if_available(target: Object, property_name: StringName, value: Variant) -> void:
	for property in target.get_property_list():
		if property.name == property_name:
			target.set(property_name, value)
			return
