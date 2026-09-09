extends Node
class_name EmbodiedRuntime

signal touch_classified(event: Dictionary)
signal reflex_completed(event: Dictionary, result: Dictionary)
signal cognitive_event_ready(event: Dictionary)
signal tracking_frame_ready(provider: String, frame: Dictionary)
signal tracking_contact_completed(provider: String, event: Dictionary, result: Dictionary)

const FaceController = preload("res://scripts/embodied_v1/embodied_face_controller.gd")
const Authority = preload("res://scripts/embodied_v1/embodied_authority.gd")
const ActionAdapter = preload("res://scripts/embodied_v1/embodied_action_adapter.gd")
const TouchClassifier = preload("res://scripts/embodied_v1/embodied_touch_classifier.gd")
const ReflexController = preload("res://scripts/embodied_v1/embodied_reflex_controller.gd")
const NavigationController = preload("res://scripts/embodied_v1/embodied_navigation_controller.gd")
const PlanExecutor = preload("res://scripts/embodied_v1/embodied_plan_executor.gd")
const TrackingGateway = preload("res://scripts/embodied_v1/embodied_tracking_gateway.gd")
const VMCInput = preload("res://scripts/embodied_v1/embodied_vmc_input.gd")
const OpenXRInput = preload("res://scripts/embodied_v1/embodied_openxr_input.gd")
const WebcamHandInput = preload("res://scripts/embodied_v1/embodied_webcam_hand_input.gd")
const GazeController = preload("res://scripts/embodied_v1/embodied_gaze_controller.gd")
const AffordanceController = preload("res://scripts/embodied_v1/embodied_affordance_controller.gd")
const BehaviorController = preload("res://scripts/embodied_v1/embodied_behavior_controller.gd")

@export var enabled := false
@export var touch_opt_in := false
@export var tracking_opt_in := false
@export var integrated_face_morphs_enabled := false

var _avatar_root: Node
var _face_controller: Node
var _authority: Node
var _action_adapter: Node
var _touch_classifier: Node
var _reflex_controller: Node
var _navigation_controller: Node
var _plan_executor: Node
var _tracking_gateway: Node
var _gaze_controller: Node
var _affordance_controller: Node
var _behavior_controller: Node
var _tracking_providers := {}
var _last_touch_event: Dictionary = {}
var _last_reflex_result: Dictionary = {}


func _ready() -> void:
	_ensure_components()
	_apply_enabled_state()


func bind_avatar(avatar_root: Node) -> bool:
	if avatar_root == null:
		return false
	_ensure_components()
	_avatar_root = avatar_root
	if _face_controller == null:
		_face_controller = FaceController.new()
		_face_controller.name = "EmbodiedFaceController"
		_face_controller.deterministic_seed = 9009
		_face_controller.synthetic_face_overlays_enabled = false
		_face_controller.integrated_face_morphs_enabled = integrated_face_morphs_enabled
		_face_controller.auto_blink_enabled = integrated_face_morphs_enabled
		_face_controller.set_process(false)
		avatar_root.add_child(_face_controller)
	else:
		_face_controller.integrated_face_morphs_enabled = integrated_face_morphs_enabled
		_face_controller.synthetic_face_overlays_enabled = false
	var face_bound := bool(_face_controller.call("bind_avatar", avatar_root))
	var adapter_bound := bool(_action_adapter.call("bind_avatar", avatar_root, _face_controller, _authority))
	var affordance_bound := bool(_affordance_controller.call("bind_avatar", avatar_root))
	_action_adapter.call("bind_affordances", _affordance_controller)
	_reflex_controller.call("bind", _action_adapter, _face_controller)
	_apply_enabled_state()
	# Face capability is intentionally not part of body-runtime binding.  The
	# accepted Aogiri source has no deforming face rig; body actions must remain
	# usable without pretending detached overlays are valid facial expressions.
	return adapter_bound and affordance_bound and face_bound


func set_enabled(next_enabled: bool) -> void:
	enabled = next_enabled
	_apply_enabled_state()


func set_touch_enabled(next_enabled: bool) -> void:
	touch_opt_in = next_enabled
	_apply_enabled_state()


func set_tracking_enabled(next_enabled: bool) -> void:
	tracking_opt_in = next_enabled
	_apply_enabled_state()


func set_tracking_provider_enabled(provider: String, next_enabled: bool) -> bool:
	_ensure_components()
	var normalized := provider.strip_edges().to_lower().replace("-", "_")
	if not bool(_tracking_gateway.call("set_provider_opt_in", normalized, next_enabled)):
		return false
	_apply_enabled_state()
	return true


func submit_tracking_frame(provider: String, frame: Dictionary) -> Dictionary:
	_ensure_components()
	return _tracking_gateway.call("submit_frame", provider, frame)


func submit_tracking_contact(provider: String, contact: Dictionary) -> Dictionary:
	_ensure_components()
	if not enabled:
		return _tracking_rejection(provider, "embodied_v1_disabled")
	if not touch_opt_in:
		return _tracking_rejection(provider, "touch_opt_in_required")
	var result: Dictionary = _tracking_gateway.call("submit_contact", provider, contact)
	if not bool(result.get("accepted", false)):
		return result
	var event: Dictionary = result.get("event", {})
	_handle_classified_touch(event)
	result["reflex"] = _last_reflex_result.duplicate(true)
	tracking_contact_completed.emit(provider, event.duplicate(true), _last_reflex_result.duplicate(true))
	return result


func submit_webcam_landmarks(frame: Dictionary) -> Dictionary:
	_ensure_components()
	return (_tracking_providers.get("webcam") as Node).call("submit_landmark_frame", frame)


func start_webcam_receiver() -> Dictionary:
	_ensure_components()
	return (_tracking_providers.get("webcam") as Node).call("start_local_receiver")


func stop_webcam_receiver() -> void:
	if _tracking_providers.has("webcam"):
		(_tracking_providers["webcam"] as Node).call("stop_local_receiver")


func bind_openxr_trackers(head: Node3D, left_hand: Node3D, right_hand: Node3D) -> bool:
	_ensure_components()
	return bool((_tracking_providers.get("openxr") as Node).call("bind_tracker_nodes", head, left_hand, right_hand))


func submit_openxr_snapshot(timestamp_ms := -1) -> Dictionary:
	_ensure_components()
	return (_tracking_providers.get("openxr") as Node).call("submit_snapshot", timestamp_ms)


func ingest_vmc_message(address: String, arguments: Array, timestamp_ms := -1) -> Dictionary:
	_ensure_components()
	return (_tracking_providers.get("vmc") as Node).call("ingest_osc_message", address, arguments, timestamp_ms)


func start_vmc_receiver() -> Dictionary:
	_ensure_components()
	return (_tracking_providers.get("vmc") as Node).call("start_local_receiver")


func stop_vmc_receiver() -> void:
	if _tracking_providers.has("vmc"):
		(_tracking_providers["vmc"] as Node).call("stop_local_receiver")


func bind_navigation(body: CharacterBody3D, agent: NavigationAgent3D, anchors: Dictionary) -> bool:
	_ensure_components()
	if _navigation_controller == null:
		_navigation_controller = NavigationController.new()
		_navigation_controller.name = "EmbodiedNavigationController"
		add_child(_navigation_controller)
	if not bool(_navigation_controller.call("bind", body, agent, _action_adapter)):
		return false
	for anchor_id in anchors.keys():
		var anchor = anchors[anchor_id]
		if anchor is Node3D:
			_navigation_controller.call("register_anchor", String(anchor_id), anchor)
	_action_adapter.call("bind_navigation", _navigation_controller)
	if not bool(_gaze_controller.call("bind", body, anchors)):
		return false
	_action_adapter.call("bind_gaze", _gaze_controller)
	for anchor_id in anchors.keys():
		var affordance_anchor = anchors[anchor_id]
		if affordance_anchor is Node3D:
			_affordance_controller.call("register_anchor", String(anchor_id), affordance_anchor)
	if _plan_executor == null:
		_plan_executor = PlanExecutor.new()
		_plan_executor.name = "EmbodiedPlanExecutor"
		add_child(_plan_executor)
	_plan_executor.call("bind", _action_adapter, _navigation_controller)
	if not bool(
		_behavior_controller.call(
			"bind",
			_action_adapter,
			_plan_executor,
			_navigation_controller,
			_gaze_controller
		)
	):
		return false
	_apply_enabled_state()
	return true


func bind_affordances(objects: Dictionary) -> bool:
	_ensure_components()
	var all_registered := true
	for object_id in objects.keys():
		var specification = objects[object_id]
		if typeof(specification) != TYPE_DICTIONARY:
			all_registered = false
			continue
		var spec: Dictionary = specification
		var node = spec.get("node")
		if not (node is Node3D):
			all_registered = false
			continue
		var affordances = spec.get("affordances", [])
		if typeof(affordances) != TYPE_ARRAY:
			affordances = []
		var registered := bool(
			_affordance_controller.call(
				"register_object",
				String(object_id),
				node,
				affordances,
				String(spec.get("home_anchor", ""))
			)
		)
		all_registered = all_registered and registered
	return all_registered


func dispatch_plan(plan: Dictionary) -> bool:
	if _plan_executor == null:
		return false
	if _behavior_controller != null:
		_behavior_controller.call("note_activity", "cognitive_plan")
	return bool(_plan_executor.call("execute_plan", plan))


func dispatch_intent(command: Dictionary) -> Dictionary:
	_ensure_components()
	if _behavior_controller != null and String(command.get("source", "")) != "local_utility_ai":
		_behavior_controller.call("note_activity", "external_intent")
	return _action_adapter.call("dispatch", command)


func set_user_present(present: bool) -> void:
	_ensure_components()
	_behavior_controller.call("set_user_present", present)


func set_cognitive_busy(busy: bool) -> void:
	_ensure_components()
	_behavior_controller.call("set_cognitive_busy", busy)


func begin_touch(zone: String, normalized_position: Vector2, timestamp_ms := -1) -> bool:
	_ensure_components()
	return bool(_touch_classifier.call("begin_touch", zone, normalized_position, timestamp_ms))


func update_touch(normalized_position: Vector2, timestamp_ms := -1) -> bool:
	return bool(_touch_classifier.call("update_touch", normalized_position, timestamp_ms))


func end_touch(normalized_position: Vector2, timestamp_ms := -1) -> Dictionary:
	return _touch_classifier.call("end_touch", normalized_position, timestamp_ms)


func advance_for_test(delta: float) -> void:
	if _face_controller != null:
		_face_controller.call("advance_face", delta)
	if _reflex_controller != null:
		_reflex_controller.call("advance_reflex", delta)
	if _navigation_controller != null:
		_navigation_controller.call("advance_navigation", delta)
	if _plan_executor != null:
		_plan_executor.call("advance_executor", delta)
	if _gaze_controller != null:
		_gaze_controller.call("advance_gaze", delta)
	if _behavior_controller != null:
		_behavior_controller.call("advance_behavior", delta)


func get_face_controller() -> Node:
	return _face_controller


func get_action_adapter() -> Node:
	return _action_adapter


func get_navigation_controller() -> Node:
	return _navigation_controller


func get_plan_executor() -> Node:
	return _plan_executor


func get_tracking_gateway() -> Node:
	return _tracking_gateway


func get_gaze_controller() -> Node:
	return _gaze_controller


func get_affordance_controller() -> Node:
	return _affordance_controller


func get_behavior_controller() -> Node:
	return _behavior_controller


func get_tracking_provider(provider: String) -> Node:
	var normalized := provider.strip_edges().to_lower().replace("-", "_")
	return _tracking_providers.get(normalized) as Node


func get_last_touch_event() -> Dictionary:
	return _last_touch_event.duplicate(true)


func get_last_reflex_result() -> Dictionary:
	return _last_reflex_result.duplicate(true)


func get_status() -> Dictionary:
	return {
		"enabled": enabled,
		"touch_opt_in": touch_opt_in,
		"touch_active": enabled and touch_opt_in,
		"tracking_opt_in": tracking_opt_in,
		"tracking_active": enabled and tracking_opt_in,
		"avatar_bound": _avatar_root != null,
		"authority_default": "deny",
		"single_model_multi_action": true,
		"integrated_face_morphs_enabled": integrated_face_morphs_enabled,
		"live_lumina_route_changed": false,
		"adapter": _action_adapter.call("get_status") if _action_adapter != null else {},
		"navigation": _navigation_controller.call("get_status") if _navigation_controller != null else {},
		"plan_executor_bound": _plan_executor != null,
		"face_capability": _face_controller.call("get_face_safety_status") if _face_controller != null else {},
		"gaze": _gaze_controller.call("get_status") if _gaze_controller != null else {},
		"affordances": _affordance_controller.call("get_status") if _affordance_controller != null else {},
		"behavior": _behavior_controller.call("get_status") if _behavior_controller != null else {},
		"tracking": _tracking_gateway.call("get_status") if _tracking_gateway != null else {},
		"tracking_providers": _tracking_provider_status(),
	}


func _ensure_components() -> void:
	if _authority == null:
		_authority = Authority.new()
		_authority.name = "EmbodiedAuthority"
		add_child(_authority)
	if _action_adapter == null:
		_action_adapter = ActionAdapter.new()
		_action_adapter.name = "EmbodiedActionAdapter"
		add_child(_action_adapter)
	if _touch_classifier == null:
		_touch_classifier = TouchClassifier.new()
		_touch_classifier.name = "EmbodiedTouchClassifier"
		_touch_classifier.touch_event.connect(_on_touch_event)
		add_child(_touch_classifier)
	if _reflex_controller == null:
		_reflex_controller = ReflexController.new()
		_reflex_controller.name = "EmbodiedReflexController"
		_reflex_controller.cognitive_event.connect(_on_cognitive_event)
		add_child(_reflex_controller)
	if _tracking_gateway == null:
		_tracking_gateway = TrackingGateway.new()
		_tracking_gateway.name = "EmbodiedTrackingGateway"
		_tracking_gateway.frame_accepted.connect(_on_tracking_frame)
		add_child(_tracking_gateway)
	if _gaze_controller == null:
		_gaze_controller = GazeController.new()
		_gaze_controller.name = "EmbodiedGazeController"
		add_child(_gaze_controller)
	if _affordance_controller == null:
		_affordance_controller = AffordanceController.new()
		_affordance_controller.name = "EmbodiedAffordanceController"
		add_child(_affordance_controller)
	if _behavior_controller == null:
		_behavior_controller = BehaviorController.new()
		_behavior_controller.name = "EmbodiedBehaviorController"
		add_child(_behavior_controller)
	if not _tracking_providers.has("vmc"):
		var vmc_input := VMCInput.new()
		vmc_input.name = "EmbodiedVMCInput"
		vmc_input.call("bind_gateway", _tracking_gateway)
		add_child(vmc_input)
		_tracking_providers["vmc"] = vmc_input
	if not _tracking_providers.has("openxr"):
		var openxr_input := OpenXRInput.new()
		openxr_input.name = "EmbodiedOpenXRInput"
		openxr_input.call("bind_gateway", _tracking_gateway)
		add_child(openxr_input)
		_tracking_providers["openxr"] = openxr_input
	if not _tracking_providers.has("webcam"):
		var webcam_input := WebcamHandInput.new()
		webcam_input.name = "EmbodiedWebcamHandInput"
		webcam_input.call("bind_gateway", _tracking_gateway)
		add_child(webcam_input)
		_tracking_providers["webcam"] = webcam_input


func _apply_enabled_state() -> void:
	if _authority != null:
		_authority.enabled = enabled
	if _action_adapter != null:
		_action_adapter.enabled = enabled
	if _face_controller != null:
		_face_controller.set_process(enabled)
	if _touch_classifier != null:
		_touch_classifier.enabled = enabled and touch_opt_in
	if _reflex_controller != null:
		_reflex_controller.enabled = enabled
		_reflex_controller.touch_reactions_enabled = enabled and touch_opt_in
		_reflex_controller.set_process(enabled and touch_opt_in)
	if _navigation_controller != null:
		_navigation_controller.enabled = enabled
		_navigation_controller.set_physics_process(enabled)
	if _plan_executor != null:
		_plan_executor.enabled = enabled
		_plan_executor.set_process(enabled)
	if _tracking_gateway != null:
		_tracking_gateway.enabled = enabled and tracking_opt_in
	if _gaze_controller != null:
		_gaze_controller.enabled = enabled
		_gaze_controller.set_process(enabled)
	if _affordance_controller != null:
		_affordance_controller.enabled = enabled
	if _behavior_controller != null:
		_behavior_controller.enabled = enabled
		_behavior_controller.set_process(enabled)
	for provider in _tracking_providers.keys():
		var provider_node := _tracking_providers[provider] as Node
		var provider_active := (
			enabled
			and tracking_opt_in
			and bool(_tracking_gateway.call("is_provider_opted_in", String(provider)))
		)
		provider_node.call("set_enabled", provider_active)


func _on_touch_event(event: Dictionary) -> void:
	_handle_classified_touch(event)


func _handle_classified_touch(event: Dictionary) -> void:
	_last_touch_event = event.duplicate(true)
	touch_classified.emit(event)
	_last_reflex_result = _reflex_controller.call("handle_touch", event)
	reflex_completed.emit(event, _last_reflex_result)


func _on_cognitive_event(event: Dictionary) -> void:
	cognitive_event_ready.emit(event)


func _on_tracking_frame(provider: String, frame: Dictionary) -> void:
	tracking_frame_ready.emit(provider, frame)


func _tracking_provider_status() -> Dictionary:
	var status := {}
	for provider in _tracking_providers.keys():
		var provider_node := _tracking_providers[provider] as Node
		status[provider] = provider_node.call("get_status")
	return status


func _tracking_rejection(provider: String, reason: String) -> Dictionary:
	return {
		"accepted": false,
		"status": "rejected",
		"reason": reason,
		"provider": provider.strip_edges().to_lower(),
	}
