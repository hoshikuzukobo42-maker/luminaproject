extends Node
class_name EmbodiedTrialHost

signal closed

const TrialScene = preload("res://embodied_v1/embodied_interactive_trial.tscn")

var _layer: CanvasLayer
var _container: SubViewportContainer
var _subviewport: SubViewport
var _trial: Node
var _saved_window_mode := DisplayServer.WINDOW_MODE_WINDOWED
var _saved_window_size := Vector2i.ZERO
var _saved_window_position := Vector2i.ZERO
var _saved_always_on_top := false
var _closing := false


func _ready() -> void:
	_save_window_state()
	_maximize_for_readability()
	_build_embedded_trial()


func close_trial() -> void:
	if _closing:
		return
	_closing = true
	if _trial != null and _trial.has_method("shutdown_for_host"):
		_trial.call("shutdown_for_host")
	_restore_window_state()
	closed.emit()
	queue_free()


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_ESCAPE:
		close_trial()
		get_viewport().set_input_as_handled()


func _build_embedded_trial() -> void:
	_layer = CanvasLayer.new()
	_layer.name = "EmbodiedTrialOverlayLayer"
	_layer.layer = 120
	add_child(_layer)

	var background := ColorRect.new()
	background.name = "EmbodiedTrialLoadingBackground"
	background.set_anchors_preset(Control.PRESET_FULL_RECT)
	background.color = Color(0.025, 0.035, 0.055, 1.0)
	background.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_layer.add_child(background)

	_container = SubViewportContainer.new()
	_container.name = "EmbodiedTrialViewportContainer"
	_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	_container.stretch = true
	_container.mouse_filter = Control.MOUSE_FILTER_STOP
	_layer.add_child(_container)

	_subviewport = SubViewport.new()
	_subviewport.name = "EmbodiedTrialViewport"
	_subviewport.size = _viewport_size()
	_subviewport.own_world_3d = true
	_subviewport.transparent_bg = false
	_subviewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	_subviewport.gui_embed_subwindows = true
	_subviewport.physics_object_picking = true
	_container.add_child(_subviewport)

	_trial = TrialScene.instantiate()
	_trial.name = "EmbodiedInteractiveTrialEmbedded"
	if _trial.has_signal("return_requested"):
		_trial.return_requested.connect(close_trial)
	_subviewport.add_child(_trial)
	get_viewport().size_changed.connect(_on_viewport_size_changed)


func _on_viewport_size_changed() -> void:
	if _subviewport != null:
		_subviewport.size = _viewport_size()


func _viewport_size() -> Vector2i:
	var size := get_viewport().get_visible_rect().size
	return Vector2i(maxi(960, int(size.x)), maxi(540, int(size.y)))


func _save_window_state() -> void:
	if DisplayServer.get_name().to_lower() == "headless":
		return
	_saved_window_mode = DisplayServer.window_get_mode()
	_saved_window_size = DisplayServer.window_get_size()
	_saved_window_position = DisplayServer.window_get_position()
	_saved_always_on_top = DisplayServer.window_get_flag(DisplayServer.WINDOW_FLAG_ALWAYS_ON_TOP)


func _maximize_for_readability() -> void:
	if DisplayServer.get_name().to_lower() == "headless":
		return
	DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_MAXIMIZED)
	DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_ALWAYS_ON_TOP, true)
	DisplayServer.window_move_to_foreground()


func _restore_window_state() -> void:
	if DisplayServer.get_name().to_lower() == "headless":
		return
	DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_ALWAYS_ON_TOP, _saved_always_on_top)
	DisplayServer.window_set_mode(_saved_window_mode)
	if _saved_window_mode == DisplayServer.WINDOW_MODE_WINDOWED and _saved_window_size != Vector2i.ZERO:
		DisplayServer.window_set_size(_saved_window_size)
		DisplayServer.window_set_position(_saved_window_position)
