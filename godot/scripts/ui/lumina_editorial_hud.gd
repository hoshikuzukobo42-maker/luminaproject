extends CanvasLayer
class_name LuminaEditorialHUD

## Self-contained editorial HUD for Lumina.
## Minimal ambient surface in play; bold architectural menu on demand.
## Instantiable without changing the main scene.

signal menu_opened
signal menu_closed
signal section_focused(section: String)
signal section_selected(section: String)
signal interaction_requested
signal welcome_dismissed
signal help_requested
signal settings_action(action: String)

const Palette = preload("res://scripts/ui/lumina_editorial_palette.gd")
## Internal section IDs (stable for callers). Display labels are Japanese.
const SECTIONS := ["TOGETHER", "MEMORY", "MOMENTS", "SETTINGS", "CLOSE", "RETURN"]
const SECTION_LABELS := {
	"TOGETHER": "いっしょに",
	"MEMORY": "きおく",
	"MOMENTS": "景色メモ",
	"SETTINGS": "設定",
	"CLOSE": "メニューを閉じる",
	"RETURN": "エレベーター付近へ移動",
}
const MENU_CLOSE_HINT := "閉じる: Esc または「メニューを閉じる」 · ↑↓で選ぶ · Enterで決定"
const PLAY_HINT_SHORT := "E 調べる · Esc メニュー · F1 説明 · F6 詰まり復帰"
const DEFAULT_CONTROL_HINT := PLAY_HINT_SHORT

@export var demo_mode: bool = false
@export var start_with_menu_open: bool = false
@export var font_scale: float = 1.0
@export var reduced_motion: bool = false
@export var ui_hidden: bool = false

var _root: Control
var _play_layer: Control
var _menu_layer: Control
var _menu_panel: Control
var _menu_clip: Control
var _menu_slide: Control
var _brand_root: Control
var _brand_letters: Array[Label] = []
var _section_labels: Array[Label] = []
var _amber_mark: ColorRect
var _photo_stack: Control
var _cut_arc: Control
var _settings_panel: Control
var _settings_body: VBoxContainer
var _section_summary_label: Label
var _menu_hint_label: Label
var _moment_records: Array = []

var _reticle_root: Control
var _reticle_dot: ColorRect
var _reticle_line: ColorRect
var _interaction_label: Label

var _subtitle_root: Control
var _subtitle_bar: ColorRect
var _speaker_label: Label
var _subtitle_label: Label

var _time_label: Label
var _hint_label: Label

var _welcome_root: Control
var _welcome_dim: ColorRect
var _welcome_panel: ColorRect
var _welcome_title: Label
var _welcome_body: Label
var _welcome_footer: Label
var _welcome_visible := false

var _menu_open := false
var _menu_progress := 0.0
var _selected_index := 0
var _interaction_visible := false
var _subtitle_visible := false
var _subtitle_source := ""
var _time_text := ""
var _tween: Tween
var _layout_size := Vector2.ZERO
var _font_scale_steps := [0.85, 1.0, 1.15, 1.3]
var _font_scale_index := 1
var _menu_dim: ColorRect

const MENU_FRACTION_16_9 := 0.30
const MENU_MAX_PX := 560.0
const MENU_MIN_PX := 320.0


func _ready() -> void:
	layer = 80
	process_mode = Node.PROCESS_MODE_ALWAYS
	_build()
	_apply_visibility()
	if start_with_menu_open:
		open_menu()
	else:
		_set_menu_progress(0.0, true)
	set_process(true)
	get_viewport().size_changed.connect(_on_viewport_size_changed)
	call_deferred("_relayout")


func _process(_delta: float) -> void:
	var sz := get_viewport().get_visible_rect().size
	if sz != _layout_size:
		_relayout()


func _unhandled_input(event: InputEvent) -> void:
	if _welcome_visible:
		if event is InputEventKey and event.pressed and not event.echo:
			if event.keycode in [KEY_ENTER, KEY_KP_ENTER, KEY_SPACE, KEY_ESCAPE]:
				hide_welcome_guide()
				get_viewport().set_input_as_handled()
		return
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_F1:
		help_requested.emit()
		get_viewport().set_input_as_handled()
		return
	if ui_hidden and not _menu_open:
		if _is_toggle_ui(event) or _is_open_menu(event):
			pass
		else:
			return
	if _is_toggle_ui(event):
		set_ui_hidden(not ui_hidden)
		get_viewport().set_input_as_handled()
		return
	if _is_open_menu(event):
		if _menu_open:
			close_menu()
		else:
			open_menu()
		get_viewport().set_input_as_handled()
		return
	if not _menu_open:
		# E / interact is owned by the walkable player controller — avoid double-fire.
		return
	if _is_nav_up(event):
		_move_selection(-1)
		get_viewport().set_input_as_handled()
	elif _is_nav_down(event):
		_move_selection(1)
		get_viewport().set_input_as_handled()
	elif _is_confirm(event):
		_activate_selection()
		get_viewport().set_input_as_handled()
	elif _is_cancel(event):
		close_menu()
		get_viewport().set_input_as_handled()


# --- Public API ------------------------------------------------------------

func show_interaction(label: String) -> void:
	_interaction_visible = true
	var text := label.strip_edges()
	if not text.is_empty() and not text.begins_with("[E]") and not text.begins_with("［E］"):
		text = "[E] %s" % text
	_interaction_label.text = text
	_reticle_root.visible = not ui_hidden and _interaction_visible
	_snap_or_slide(_reticle_root, true)


func hide_interaction() -> void:
	_interaction_visible = false
	_reticle_root.visible = false


func show_subtitle(speaker: String, text: String, source: String = "world") -> void:
	_subtitle_visible = true
	_subtitle_source = source.strip_edges() if not source.strip_edges().is_empty() else "world"
	_speaker_label.text = speaker.strip_edges()
	_subtitle_label.text = text.strip_edges()
	_subtitle_root.visible = not ui_hidden
	_fit_subtitle_bar()
	_snap_or_slide(_subtitle_root, true)


func hide_subtitle(source: String = "") -> void:
	var requested_source := source.strip_edges()
	if not requested_source.is_empty() and requested_source != _subtitle_source:
		return
	_subtitle_visible = false
	_subtitle_source = ""
	_subtitle_root.visible = false


func set_time_state(label: String) -> void:
	# Keep caller casing (Japanese status labels must not be uppercased).
	_time_text = label.strip_edges()
	_time_label.text = _time_text
	_time_label.visible = (not _time_text.is_empty()) and (not ui_hidden)


func open_menu() -> void:
	if _menu_open:
		return
	_menu_open = true
	_menu_layer.visible = true
	if _menu_hint_label != null:
		_menu_hint_label.visible = true
	if _hint_label != null:
		_hint_label.text = MENU_CLOSE_HINT
	# Default to CLOSE so Enter / mis-confirm won't teleport or change music.
	set_menu_section("CLOSE", true)
	# Concept keeps the ambient reticle visible beside the wall.
	_animate_menu(1.0)
	menu_opened.emit()


func close_menu() -> void:
	if not _menu_open:
		return
	_menu_open = false
	if _menu_hint_label != null:
		_menu_hint_label.visible = false
	if _hint_label != null and (_hint_label.text == MENU_CLOSE_HINT or _hint_label.text.strip_edges().is_empty()):
		_hint_label.text = DEFAULT_CONTROL_HINT
	_animate_menu(0.0)
	menu_closed.emit()


func set_menu_section(section: String, notify: bool = true) -> void:
	var key := section.strip_edges().to_upper()
	var idx := SECTIONS.find(key)
	if idx < 0:
		return
	_selected_index = idx
	_refresh_selection(true)
	if notify:
		# Focus only — activate/confirm emits section_selected separately.
		section_focused.emit(SECTIONS[_selected_index])
	_update_settings_visibility()


func set_status_text(text: String) -> void:
	if _section_summary_label != null:
		_section_summary_label.text = text.strip_edges()


func set_moments(moments: Array) -> void:
	_moment_records = moments.duplicate(true)
	if _photo_stack != null:
		_populate_photo_stack()


func set_font_scale(scale: float) -> void:
	font_scale = clampf(scale, 0.75, 1.5)
	# Keep cycle index aligned with the loaded/current value.
	var best_i := 1
	var best_d := 999.0
	for i in _font_scale_steps.size():
		var d := absf(float(_font_scale_steps[i]) - font_scale)
		if d < best_d:
			best_d = d
			best_i = i
	_font_scale_index = best_i
	_relayout()
	_refresh_settings_labels()


func set_ui_hidden(hidden: bool) -> void:
	ui_hidden = hidden
	_apply_visibility()
	_refresh_settings_labels()


func set_reduced_motion(enabled: bool) -> void:
	reduced_motion = enabled
	_refresh_settings_labels()


func is_menu_open() -> bool:
	return _menu_open


func is_welcome_visible() -> bool:
	return _welcome_visible


func get_selected_section() -> String:
	return SECTIONS[_selected_index]


func set_control_hint(text: String) -> void:
	if _hint_label == null:
		return
	_hint_label.text = text.strip_edges()
	if _hint_label.text.is_empty():
		_hint_label.text = DEFAULT_CONTROL_HINT
	_hint_label.visible = not ui_hidden


func show_welcome_guide(title: String, body: String, footer: String = "") -> void:
	if _welcome_root == null:
		return
	_welcome_title.text = title.strip_edges()
	_welcome_body.text = body.strip_edges()
	_welcome_footer.text = footer.strip_edges()
	if _welcome_footer.text.is_empty():
		_welcome_footer.text = "Enter / Space / Esc ではじめる"
	_welcome_visible = true
	_welcome_root.visible = true
	if _menu_open:
		close_menu()
	_relayout()


func hide_welcome_guide() -> void:
	if not _welcome_visible:
		return
	_welcome_visible = false
	if _welcome_root != null:
		_welcome_root.visible = false
	welcome_dismissed.emit()


# --- Build -----------------------------------------------------------------

func _build() -> void:
	_root = Control.new()
	_root.name = "EditorialRoot"
	_root.set_anchors_preset(Control.PRESET_FULL_RECT)
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_root)

	_play_layer = Control.new()
	_play_layer.name = "PlayLayer"
	_play_layer.set_anchors_preset(Control.PRESET_FULL_RECT)
	_play_layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_root.add_child(_play_layer)

	_build_reticle()
	_build_subtitle()
	_build_time()
	_build_hint()
	_build_welcome()
	_build_menu()


func _build_reticle() -> void:
	_reticle_root = Control.new()
	_reticle_root.name = "Reticle"
	_reticle_root.visible = false
	_reticle_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_play_layer.add_child(_reticle_root)

	_reticle_dot = ColorRect.new()
	_reticle_dot.size = Vector2(4, 4)
	_reticle_dot.color = Palette.RETICLE
	_reticle_root.add_child(_reticle_dot)

	_reticle_line = ColorRect.new()
	_reticle_line.size = Vector2(72, 1)
	_reticle_line.color = Color(Palette.STONE_IVORY.r, Palette.STONE_IVORY.g, Palette.STONE_IVORY.b, 0.75)
	_reticle_root.add_child(_reticle_line)

	_interaction_label = Label.new()
	_interaction_label.text = "[E] 調べる"
	_interaction_label.add_theme_color_override("font_color", Palette.STONE_IVORY)
	_interaction_label.add_theme_font_size_override("font_size", 14)
	_reticle_root.add_child(_interaction_label)


func _build_subtitle() -> void:
	_subtitle_root = Control.new()
	_subtitle_root.name = "Subtitle"
	_subtitle_root.visible = false
	_subtitle_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_play_layer.add_child(_subtitle_root)

	_subtitle_bar = ColorRect.new()
	_subtitle_bar.color = Palette.SUBTITLE_BAR
	_subtitle_root.add_child(_subtitle_bar)

	_speaker_label = Label.new()
	_speaker_label.add_theme_color_override("font_color", Palette.STONE_IVORY)
	_speaker_label.add_theme_font_size_override("font_size", 18)
	_subtitle_root.add_child(_speaker_label)

	_subtitle_label = Label.new()
	_subtitle_label.add_theme_color_override("font_color", Palette.STONE_IVORY_DIM)
	_subtitle_label.add_theme_font_size_override("font_size", 16)
	_subtitle_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_subtitle_root.add_child(_subtitle_label)


func _build_time() -> void:
	_time_label = Label.new()
	_time_label.name = "TimeState"
	_time_label.visible = false
	_time_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_time_label.add_theme_color_override("font_color", Palette.STONE_IVORY_DIM)
	_time_label.add_theme_font_size_override("font_size", 12)
	_play_layer.add_child(_time_label)


func _build_hint() -> void:
	_hint_label = Label.new()
	_hint_label.name = "ControlHint"
	_hint_label.text = DEFAULT_CONTROL_HINT
	_hint_label.add_theme_color_override("font_color", Color(Palette.STONE_IVORY_DIM.r, Palette.STONE_IVORY_DIM.g, Palette.STONE_IVORY_DIM.b, 0.72))
	_hint_label.add_theme_font_size_override("font_size", 12)
	_hint_label.visible = true
	_hint_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_play_layer.add_child(_hint_label)


func _build_welcome() -> void:
	_welcome_root = Control.new()
	_welcome_root.name = "WelcomeGuide"
	_welcome_root.visible = false
	_welcome_root.set_anchors_preset(Control.PRESET_FULL_RECT)
	_welcome_root.mouse_filter = Control.MOUSE_FILTER_STOP
	_root.add_child(_welcome_root)

	_welcome_dim = ColorRect.new()
	_welcome_dim.color = Color(0.04, 0.05, 0.07, 0.72)
	_welcome_dim.set_anchors_preset(Control.PRESET_FULL_RECT)
	_welcome_dim.mouse_filter = Control.MOUSE_FILTER_STOP
	_welcome_root.add_child(_welcome_dim)

	_welcome_panel = ColorRect.new()
	_welcome_panel.color = Color(0.08, 0.09, 0.11, 0.94)
	_welcome_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_welcome_root.add_child(_welcome_panel)

	_welcome_title = Label.new()
	_welcome_title.add_theme_color_override("font_color", Palette.STONE_IVORY)
	_welcome_title.add_theme_font_size_override("font_size", 28)
	_welcome_title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_welcome_panel.add_child(_welcome_title)

	_welcome_body = Label.new()
	_welcome_body.add_theme_color_override("font_color", Palette.STONE_IVORY_DIM)
	_welcome_body.add_theme_font_size_override("font_size", 15)
	_welcome_body.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_welcome_panel.add_child(_welcome_body)

	_welcome_footer = Label.new()
	_welcome_footer.add_theme_color_override("font_color", Color(Palette.STONE_IVORY.r, Palette.STONE_IVORY.g, Palette.STONE_IVORY.b, 0.7))
	_welcome_footer.add_theme_font_size_override("font_size", 13)
	_welcome_panel.add_child(_welcome_footer)


func _build_menu() -> void:
	_menu_layer = Control.new()
	_menu_layer.name = "MenuLayer"
	_menu_layer.set_anchors_preset(Control.PRESET_FULL_RECT)
	_menu_layer.mouse_filter = Control.MOUSE_FILTER_STOP
	_menu_layer.visible = false
	_root.add_child(_menu_layer)

	_menu_dim = ColorRect.new()
	_menu_dim.name = "MenuDim"
	_menu_dim.set_anchors_preset(Control.PRESET_FULL_RECT)
	_menu_dim.color = Color(0.03, 0.04, 0.06, 0.48)
	_menu_dim.mouse_filter = Control.MOUSE_FILTER_STOP
	_menu_dim.gui_input.connect(_on_menu_dim_gui_input)
	_menu_layer.add_child(_menu_dim)

	_menu_clip = Control.new()
	_menu_clip.name = "MenuClip"
	_menu_clip.clip_contents = true
	_menu_clip.mouse_filter = Control.MOUSE_FILTER_STOP
	_menu_layer.add_child(_menu_clip)

	_menu_slide = Control.new()
	_menu_slide.name = "MenuSlide"
	_menu_slide.set_anchors_preset(Control.PRESET_FULL_RECT)
	_menu_clip.add_child(_menu_slide)

	_menu_panel = Control.new()
	_menu_panel.name = "MenuPanel"
	_menu_panel.set_anchors_preset(Control.PRESET_FULL_RECT)
	_menu_slide.add_child(_menu_panel)

	var wall := ColorRect.new()
	wall.name = "Wall"
	wall.set_anchors_preset(Control.PRESET_FULL_RECT)
	wall.color = Palette.SUMIIRO
	_menu_panel.add_child(wall)

	var edge := ColorRect.new()
	edge.name = "LeftEdge"
	edge.color = Palette.LINE_IVORY
	edge.size = Vector2(1, 10)
	edge.position = Vector2(0, 0)
	_menu_panel.add_child(edge)
	edge.set_anchors_and_offsets_preset(Control.PRESET_LEFT_WIDE)
	edge.offset_right = 1
	edge.offset_left = 0

	_brand_root = Control.new()
	_brand_root.name = "BrandVertical"
	_menu_panel.add_child(_brand_root)
	_brand_letters.clear()
	for ch in ["L", "U", "M", "I", "N", "A"]:
		var lab := Label.new()
		lab.text = ch
		lab.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		lab.add_theme_color_override("font_color", Palette.STONE_IVORY)
		_brand_root.add_child(lab)
		_brand_letters.append(lab)

	_section_labels.clear()
	for i in SECTIONS.size():
		var lab := Label.new()
		lab.name = "Section_%s" % SECTIONS[i]
		lab.text = str(SECTION_LABELS.get(SECTIONS[i], SECTIONS[i]))
		lab.add_theme_color_override("font_color", Palette.STONE_IVORY)
		lab.mouse_filter = Control.MOUSE_FILTER_STOP
		lab.gui_input.connect(_on_section_gui_input.bind(i))
		_menu_panel.add_child(lab)
		_section_labels.append(lab)

	_menu_hint_label = Label.new()
	_menu_hint_label.name = "MenuCloseHint"
	_menu_hint_label.text = MENU_CLOSE_HINT
	_menu_hint_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_menu_hint_label.add_theme_color_override("font_color", Color(Palette.STONE_IVORY.r, Palette.STONE_IVORY.g, Palette.STONE_IVORY.b, 0.72))
	_menu_hint_label.add_theme_font_size_override("font_size", 13)
	_menu_hint_label.visible = false
	_menu_panel.add_child(_menu_hint_label)

	_amber_mark = ColorRect.new()
	_amber_mark.name = "AmberMark"
	_amber_mark.color = Palette.AMBER
	_amber_mark.size = Vector2(28, 2)
	_menu_panel.add_child(_amber_mark)

	_cut_arc = _ArcCut.new()
	_cut_arc.name = "CurvedCut"
	_menu_panel.add_child(_cut_arc)

	_photo_stack = Control.new()
	_photo_stack.name = "PhotoStack"
	_menu_panel.add_child(_photo_stack)
	_populate_photo_stack()

	_settings_panel = Control.new()
	_settings_panel.name = "SettingsPanel"
	_settings_panel.visible = false
	_menu_panel.add_child(_settings_panel)
	_build_settings()


func _build_settings() -> void:
	var rule := ColorRect.new()
	rule.color = Palette.LINE_IVORY
	rule.size = Vector2(180, 1)
	rule.position = Vector2(0, 0)
	_settings_panel.add_child(rule)

	var title := Label.new()
	title.text = "設定"
	title.position = Vector2(0, 10)
	title.add_theme_color_override("font_color", Palette.AMBER)
	title.add_theme_font_size_override("font_size", 14)
	_settings_panel.add_child(title)

	_settings_body = VBoxContainer.new()
	_settings_body.position = Vector2(0, 36)
	_settings_body.add_theme_constant_override("separation", 8)
	_settings_panel.add_child(_settings_body)

	_add_setting_button("文字サイズを変える", "font_cycle")
	_add_setting_button("画質を切り替える", "quality_cycle")
	_add_setting_button("動きを抑える オン/オフ", "reduced_motion")
	_add_setting_button("UIを隠す オン/オフ", "hide_ui")
	_add_setting_button("いまの景色を撮る", "photo")
	_add_setting_button("時刻を切り替える", "time")
	_add_setting_button("天候を切り替える", "weather")
	_add_setting_button("音楽を切り替える", "music")

	var tip := Label.new()
	tip.text = "Esc でメニューを閉じる"
	tip.add_theme_color_override("font_color", Palette.STONE_IVORY_DIM)
	tip.add_theme_font_size_override("font_size", 12)
	_settings_body.add_child(tip)


func _add_setting_button(label_text: String, action: String) -> void:
	var btn := Button.new()
	btn.name = "SettingBtn_%s" % action
	btn.text = label_text
	btn.alignment = HORIZONTAL_ALIGNMENT_LEFT
	btn.focus_mode = Control.FOCUS_NONE
	btn.add_theme_color_override("font_color", Palette.STONE_IVORY)
	btn.add_theme_color_override("font_hover_color", Palette.AMBER)
	btn.add_theme_font_size_override("font_size", 13)
	btn.pressed.connect(_on_setting_button_pressed.bind(action))
	_settings_body.add_child(btn)


func _on_setting_button_pressed(action: String) -> void:
	match action:
		"font_cycle":
			_cycle_font_scale()
			settings_action.emit("persist_accessibility")
		"quality_cycle":
			section_selected.emit("SETTINGS")
		"reduced_motion":
			_toggle_reduced_motion()
			settings_action.emit("persist_accessibility")
		"hide_ui":
			_toggle_ui_hidden_from_settings()
			settings_action.emit("persist_accessibility")
		_:
			settings_action.emit(action)
	_refresh_settings_labels()


func _add_setting_row(name_text: String, hint: String, _action: String) -> void:
	# Kept for compatibility with older refresh helpers.
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	var n := Label.new()
	n.name = "Name_%s" % name_text.replace(" ", "_")
	n.text = name_text
	n.add_theme_color_override("font_color", Palette.STONE_IVORY)
	n.add_theme_font_size_override("font_size", 13)
	n.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(n)
	var h := Label.new()
	h.name = "Hint_%s" % name_text.replace(" ", "_")
	h.text = hint
	h.add_theme_color_override("font_color", Palette.STONE_IVORY_DIM)
	h.add_theme_font_size_override("font_size", 12)
	row.add_child(h)
	_settings_body.add_child(row)


func _populate_photo_stack() -> void:
	if _photo_stack == null:
		return
	var previous_summary := ""
	if _section_summary_label != null and is_instance_valid(_section_summary_label):
		previous_summary = _section_summary_label.text
	# Immediate free avoids queue_free() frames with duplicate SectionSummary nodes.
	var stale: Array[Node] = []
	for child in _photo_stack.get_children():
		stale.append(child)
	for child in stale:
		_photo_stack.remove_child(child)
		child.free()
	_section_summary_label = null
	var specs := [
		{"rot": -8.0, "offset": Vector2(8, 18), "bands": [Color(0.10, 0.14, 0.22), Color(0.22, 0.24, 0.28), Color(0.08, 0.09, 0.11)]},
		{"rot": 6.0, "offset": Vector2(22, 92), "bands": [Color(0.18, 0.16, 0.14), Color(0.30, 0.28, 0.26), Color(0.12, 0.11, 0.10)]},
		{"rot": -4.0, "offset": Vector2(4, 168), "bands": [Color(0.16, 0.14, 0.12), Color(0.28, 0.22, 0.18), Color(0.10, 0.09, 0.08)]},
		{"rot": 10.0, "offset": Vector2(18, 244), "bands": [Color(0.08, 0.08, 0.10), Color(0.35, 0.35, 0.38), Color(0.14, 0.14, 0.16)]},
	]
	for i in specs.size():
		var frame := ColorRect.new()
		frame.name = "MomentFrame_%d" % i
		frame.size = Vector2(96, 118)
		frame.position = specs[i]["offset"]
		frame.rotation_degrees = specs[i]["rot"]
		frame.color = Palette.STONE_IVORY
		frame.clip_contents = true
		_photo_stack.add_child(frame)
		var record_index := _moment_records.size() - 1 - i
		var loaded_photo := false
		if record_index >= 0:
			var record: Dictionary = _moment_records[record_index]
			var stored_path := str(record.get("path", ""))
			var absolute_path := ProjectSettings.globalize_path(stored_path) if stored_path.begins_with("user://") or stored_path.begins_with("res://") else stored_path
			if not absolute_path.is_empty() and FileAccess.file_exists(absolute_path):
				var file_size := 0
				var probe := FileAccess.open(absolute_path, FileAccess.READ)
				if probe != null:
					file_size = int(probe.get_length())
					probe.close()
				# Skip tiny headless fallback PNGs; show placeholder bands instead.
				if file_size >= 20000:
					var image := Image.load_from_file(absolute_path)
					if image != null and not image.is_empty():
						var texture_rect := TextureRect.new()
						texture_rect.name = "MomentTexture"
						texture_rect.position = Vector2(6, 6)
						texture_rect.size = Vector2(84, 84)
						texture_rect.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
						texture_rect.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
						texture_rect.texture = ImageTexture.create_from_image(image)
						frame.add_child(texture_rect)
						loaded_photo = true
		if not loaded_photo:
			var bands: Array = specs[i]["bands"]
			for b in 3:
				var band := ColorRect.new()
				band.position = Vector2(6, 6 + b * 28)
				band.size = Vector2(84, 28)
				band.color = bands[b]
				frame.add_child(band)
		var grain := ColorRect.new()
		grain.position = Vector2(6, 94)
		grain.size = Vector2(84, 18)
		grain.color = Color(0.88, 0.85, 0.78, 1.0)
		frame.add_child(grain)
		# Tiny caption rule — not a closed card, just a mark.
		var mark := ColorRect.new()
		mark.position = Vector2(14, 100)
		mark.size = Vector2(28, 1)
		mark.color = Color(0.25, 0.22, 0.18, 0.8)
		frame.add_child(mark)
	_section_summary_label = Label.new()
	_section_summary_label.name = "SectionSummary"
	_section_summary_label.position = Vector2(4, 364)
	_section_summary_label.size = Vector2(170, 90)
	_section_summary_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_section_summary_label.add_theme_color_override("font_color", Palette.STONE_IVORY_DIM)
	_section_summary_label.add_theme_font_size_override("font_size", 11)
	if previous_summary.strip_edges() != "":
		_section_summary_label.text = previous_summary
	else:
		_section_summary_label.text = "%d 件の景色メモ" % _moment_records.size()
	_photo_stack.add_child(_section_summary_label)


# --- Layout ----------------------------------------------------------------

func _on_viewport_size_changed() -> void:
	_relayout()


func _relayout() -> void:
	var vp := get_viewport().get_visible_rect().size
	_layout_size = vp
	if vp.x < 2.0 or vp.y < 2.0:
		return

	var aspect := vp.x / vp.y
	var menu_w := _menu_width_for(vp, aspect)
	var play_w := vp.x - menu_w

	_menu_clip.position = Vector2(play_w, 0)
	_menu_clip.size = Vector2(menu_w, vp.y)
	_menu_slide.size = Vector2(menu_w, vp.y)
	_set_menu_progress(_menu_progress, true)

	var fs := font_scale
	# Monumental vertical brand — stacked glyphs, menu-only giant type.
	_brand_root.position = Vector2(18 * fs, 28 * fs)
	var letter_size := int(round(56.0 * fs))
	var letter_gap := 4.0 * fs
	var y_cursor := 0.0
	for lab in _brand_letters:
		lab.add_theme_font_size_override("font_size", letter_size)
		lab.position = Vector2(0, y_cursor)
		lab.size = Vector2(64 * fs, letter_size + 4)
		y_cursor += letter_size + letter_gap

	var section_x := 100.0 * fs
	var section_top := vp.y * 0.16
	var section_gap := 36.0 * fs
	for i in _section_labels.size():
		var lab := _section_labels[i]
		lab.add_theme_font_size_override("font_size", int(round(24.0 * fs)))
		lab.position = Vector2(section_x, section_top + i * section_gap)
		lab.size = Vector2(menu_w * 0.52, 34 * fs)

	_amber_mark.size = Vector2(26 * fs, 2)
	_refresh_selection(true)

	_cut_arc.position = Vector2(menu_w * 0.62, 0)
	_cut_arc.size = Vector2(menu_w * 0.40, vp.y)

	_photo_stack.position = Vector2(menu_w * 0.68, vp.y * 0.18)
	_photo_stack.scale = Vector2(clampf(menu_w / 420.0, 0.85, 1.25), clampf(menu_w / 420.0, 0.85, 1.25))

	_settings_panel.position = Vector2(section_x, section_top + SECTIONS.size() * section_gap + 18)
	_settings_panel.size = Vector2(menu_w * 0.52, 280)

	if _menu_hint_label != null:
		_menu_hint_label.position = Vector2(18 * fs, vp.y - 64 * fs)
		_menu_hint_label.size = Vector2(menu_w - 36 * fs, 48 * fs)
		_menu_hint_label.add_theme_font_size_override("font_size", int(round(12.0 * fs)))
		_menu_hint_label.visible = _menu_open

	# Play HUD placements — keep clear of menu intrusion zone.
	var reticle_pos := Vector2(vp.x * 0.5, vp.y * 0.48)
	if _menu_open:
		reticle_pos.x = vp.x * 0.42
	elif aspect > 2.0:
		reticle_pos.x = play_w * 0.48
	_reticle_dot.position = Vector2.ZERO
	_reticle_line.position = Vector2(10, 1.5)
	_interaction_label.position = Vector2(88, -8)
	_interaction_label.add_theme_font_size_override("font_size", int(round(14.0 * fs)))
	_reticle_root.position = reticle_pos

	_subtitle_root.position = Vector2(36 * fs, vp.y - 96 * fs)
	_speaker_label.position = Vector2(16, 10)
	_speaker_label.add_theme_font_size_override("font_size", int(round(18.0 * fs)))
	_subtitle_label.position = Vector2(16, 36)
	_subtitle_label.add_theme_font_size_override("font_size", int(round(16.0 * fs)))
	_subtitle_label.size = Vector2(min(play_w * 0.55, 520 * fs), 40 * fs)
	_fit_subtitle_bar()

	_time_label.position = Vector2(play_w - 260 * fs, vp.y - 48 * fs)
	_time_label.size = Vector2(220 * fs, 24)
	_time_label.add_theme_font_size_override("font_size", int(round(12.0 * fs)))

	_hint_label.position = Vector2(36 * fs, 24 * fs)
	_hint_label.size = Vector2(min(play_w - 72 * fs, 720 * fs), 40 * fs)
	_hint_label.add_theme_font_size_override("font_size", int(round(12.0 * fs)))

	if _welcome_root != null and _welcome_visible:
		var panel_w := minf(vp.x * 0.62, 640.0 * fs)
		var panel_h := minf(vp.y * 0.58, 420.0 * fs)
		_welcome_panel.size = Vector2(panel_w, panel_h)
		_welcome_panel.position = Vector2((vp.x - panel_w) * 0.5, (vp.y - panel_h) * 0.42)
		_welcome_title.position = Vector2(28 * fs, 28 * fs)
		_welcome_title.size = Vector2(panel_w - 56 * fs, 48 * fs)
		_welcome_title.add_theme_font_size_override("font_size", int(round(26.0 * fs)))
		_welcome_body.position = Vector2(28 * fs, 88 * fs)
		_welcome_body.size = Vector2(panel_w - 56 * fs, panel_h - 160 * fs)
		_welcome_body.add_theme_font_size_override("font_size", int(round(15.0 * fs)))
		_welcome_footer.position = Vector2(28 * fs, panel_h - 48 * fs)
		_welcome_footer.size = Vector2(panel_w - 56 * fs, 28 * fs)
		_welcome_footer.add_theme_font_size_override("font_size", int(round(13.0 * fs)))

	_update_settings_visibility()
	_refresh_settings_labels()


func _menu_width_for(vp: Vector2, aspect: float) -> float:
	var fraction := MENU_FRACTION_16_9
	if aspect >= 2.2:
		fraction = 0.22
	elif aspect >= 1.7:
		fraction = 0.30
	elif aspect >= 1.55:
		fraction = 0.32
	else:
		fraction = 0.36
	return clampf(vp.x * fraction, MENU_MIN_PX, MENU_MAX_PX)


func _fit_subtitle_bar() -> void:
	var w := maxf(220.0, _subtitle_label.size.x + 32.0)
	var h := 72.0 * font_scale
	_subtitle_bar.position = Vector2.ZERO
	_subtitle_bar.size = Vector2(w, h)


# --- Menu motion -----------------------------------------------------------

func _animate_menu(target: float) -> void:
	if _tween:
		_tween.kill()
	if reduced_motion:
		_set_menu_progress(target, true)
		if target <= 0.0:
			_menu_layer.visible = false
		return
	_menu_layer.visible = true
	_tween = create_tween()
	_tween.set_trans(Tween.TRANS_CUBIC)
	_tween.set_ease(Tween.EASE_OUT)
	_tween.tween_method(_set_menu_progress.bind(false), _menu_progress, target, 0.28)
	if target <= 0.0:
		_tween.tween_callback(func() -> void: _menu_layer.visible = false)


func _set_menu_progress(value: float, _instant: bool = false) -> void:
	_menu_progress = clampf(value, 0.0, 1.0)
	var menu_w := _menu_clip.size.x
	# Wipe / slide from right: panel starts off-screen right.
	_menu_slide.position = Vector2(menu_w * (1.0 - _menu_progress), 0.0)


func _snap_or_slide(node: Control, show: bool) -> void:
	if reduced_motion or not show:
		node.modulate = Color.WHITE
		return
	node.modulate = Color(1, 1, 1, 1)
	var start := node.position
	node.position = start + Vector2(0, 8)
	var t := create_tween()
	t.set_trans(Tween.TRANS_BACK)
	t.set_ease(Tween.EASE_OUT)
	t.tween_property(node, "position", start, 0.18)


# --- Selection -------------------------------------------------------------

func _move_selection(delta: int) -> void:
	var n := SECTIONS.size()
	_selected_index = (_selected_index + delta + n) % n
	_refresh_selection(false)
	section_focused.emit(SECTIONS[_selected_index])
	_update_settings_visibility()


func _refresh_selection(instant: bool) -> void:
	for i in _section_labels.size():
		var lab := _section_labels[i]
		var on := i == _selected_index
		lab.add_theme_color_override("font_color", Palette.STONE_IVORY if on else Palette.STONE_IVORY_DIM)
		if on:
			var target := Vector2(lab.position.x + lab.size.x + 12.0, lab.position.y + lab.size.y * 0.45)
			if instant or reduced_motion:
				_amber_mark.position = target
			else:
				var t := create_tween()
				t.set_trans(Tween.TRANS_QUAD)
				t.set_ease(Tween.EASE_OUT)
				t.tween_property(_amber_mark, "position", target, 0.12)


func _activate_selection() -> void:
	var section: String = SECTIONS[_selected_index]
	section_selected.emit(section)
	match section:
		"CLOSE":
			close_menu()
		"RETURN":
			close_menu()
		"SETTINGS":
			_update_settings_visibility()
		_:
			pass


func _on_menu_dim_gui_input(event: InputEvent) -> void:
	if not _menu_open:
		return
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		close_menu()
		get_viewport().set_input_as_handled()
	elif event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_RIGHT:
		close_menu()
		get_viewport().set_input_as_handled()


func _update_settings_visibility() -> void:
	_settings_panel.visible = _menu_open and SECTIONS[_selected_index] == "SETTINGS"


func _on_section_gui_input(event: InputEvent, index: int) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		_selected_index = index
		_refresh_selection(false)
		section_focused.emit(SECTIONS[_selected_index])
		_update_settings_visibility()
		# Click focuses only. CLOSE may activate immediately; dangerous actions need Enter.
		var section: String = SECTIONS[_selected_index]
		if section == "CLOSE":
			_activate_selection()
		# SETTINGS / TOGETHER / MEMORY / MOMENTS / RETURN: Enter confirms.


func _apply_visibility() -> void:
	_play_layer.visible = not ui_hidden
	if ui_hidden:
		_reticle_root.visible = false
		_subtitle_root.visible = false
		_time_label.visible = false
		if _hint_label != null:
			_hint_label.visible = false
	else:
		_reticle_root.visible = _interaction_visible
		_subtitle_root.visible = _subtitle_visible
		_time_label.visible = not _time_text.is_empty()
		if _hint_label != null:
			_hint_label.visible = true
	if ui_hidden and _menu_open:
		# Menu remains reachable for recovery.
		_menu_layer.visible = true
	if _welcome_root != null and _welcome_visible:
		_welcome_root.visible = true


func _refresh_settings_labels() -> void:
	if _settings_body == null:
		return
	for child in _settings_body.get_children():
		if child is Button:
			var btn := child as Button
			match btn.name:
				"SettingBtn_font_cycle":
					btn.text = "文字サイズを変える（いま %.0f%%）" % (font_scale * 100.0)
				"SettingBtn_reduced_motion":
					btn.text = "動きを抑える（いま %s）" % ("オン" if reduced_motion else "オフ")
				"SettingBtn_hide_ui":
					btn.text = "UIを隠す（いま %s）" % ("オン" if ui_hidden else "オフ")
		elif child is HBoxContainer and child.get_child_count() >= 2:
			var name_lab: Label = child.get_child(0)
			var hint_lab: Label = child.get_child(1)
			match name_lab.text:
				"文字サイズ":
					hint_lab.text = "%.0f%%  [ / ]" % (font_scale * 100.0)
				"動きを抑える":
					hint_lab.text = ("オン" if reduced_motion else "オフ") + "  R"
				"UIを隠す":
					hint_lab.text = ("オン" if ui_hidden else "オフ") + "  H"


func _cycle_font_scale() -> void:
	_font_scale_index = (_font_scale_index + 1) % _font_scale_steps.size()
	set_font_scale(_font_scale_steps[_font_scale_index])


func _toggle_reduced_motion() -> void:
	set_reduced_motion(not reduced_motion)


func _toggle_ui_hidden_from_settings() -> void:
	set_ui_hidden(not ui_hidden)


# --- Input helpers ---------------------------------------------------------

func _is_open_menu(event: InputEvent) -> bool:
	# Prefer InputMap action; Esc remains the keyboard fallback.
	# KEY_M is reserved for living-hub music cycle.
	if event.is_action_pressed("living_hub_menu"):
		return true
	if event is InputEventKey and event.pressed and not event.echo:
		return event.keycode == KEY_ESCAPE
	if event is InputEventJoypadButton and event.pressed:
		return event.button_index in [JOY_BUTTON_START, JOY_BUTTON_BACK]
	return false


func _is_cancel(event: InputEvent) -> bool:
	if event is InputEventKey and event.pressed and not event.echo:
		return event.keycode in [KEY_ESCAPE, KEY_BACKSPACE]
	if event is InputEventJoypadButton and event.pressed:
		return event.button_index == JOY_BUTTON_B
	return false


func _is_confirm(event: InputEvent) -> bool:
	if event is InputEventKey and event.pressed and not event.echo:
		# KEY_E is world-interact only; menu uses Enter / Space.
		return event.keycode in [KEY_ENTER, KEY_KP_ENTER, KEY_SPACE]
	if event is InputEventJoypadButton and event.pressed:
		return event.button_index == JOY_BUTTON_A
	return false


func _is_nav_up(event: InputEvent) -> bool:
	if event is InputEventKey and event.pressed and not event.echo:
		return event.keycode in [KEY_UP, KEY_W]
	if event is InputEventJoypadButton and event.pressed:
		return event.button_index == JOY_BUTTON_DPAD_UP
	if event is InputEventJoypadMotion and absf(event.axis_value) > 0.55:
		return event.axis == JOY_AXIS_LEFT_Y and event.axis_value < -0.55
	return false


func _is_nav_down(event: InputEvent) -> bool:
	if event is InputEventKey and event.pressed and not event.echo:
		return event.keycode in [KEY_DOWN, KEY_S]
	if event is InputEventJoypadButton and event.pressed:
		return event.button_index == JOY_BUTTON_DPAD_DOWN
	if event is InputEventJoypadMotion and absf(event.axis_value) > 0.55:
		return event.axis == JOY_AXIS_LEFT_Y and event.axis_value > 0.55
	return false


func _is_toggle_ui(event: InputEvent) -> bool:
	if event is InputEventKey and event.pressed and not event.echo:
		return event.keycode == KEY_H
	return false


func _input(event: InputEvent) -> void:
	# Settings hotkeys while menu SETTINGS is selected.
	if not _menu_open:
		return
	if event is InputEventKey and event.pressed and not event.echo:
		match event.keycode:
			KEY_BRACKETLEFT:
				_font_scale_index = maxi(0, _font_scale_index - 1)
				set_font_scale(_font_scale_steps[_font_scale_index])
				get_viewport().set_input_as_handled()
			KEY_BRACKETRIGHT:
				_font_scale_index = mini(_font_scale_steps.size() - 1, _font_scale_index + 1)
				set_font_scale(_font_scale_steps[_font_scale_index])
				get_viewport().set_input_as_handled()
			KEY_R:
				if SECTIONS[_selected_index] == "SETTINGS":
					_toggle_reduced_motion()
					get_viewport().set_input_as_handled()


class _ArcCut extends Control:
	const INK := Color(0.02, 0.022, 0.025, 1.0)
	const IVORY := Color(0.93, 0.90, 0.84, 0.35)

	func _ready() -> void:
		mouse_filter = Control.MOUSE_FILTER_IGNORE

	func _draw() -> void:
		var ink := INK
		var ivory := IVORY
		var w := size.x
		var h := size.y
		# Architectural curved cut — fill right wedge, stroke the arc.
		var points := PackedVector2Array()
		points.append(Vector2(w * 0.15, 0))
		var steps := 28
		for i in range(steps + 1):
			var t := float(i) / float(steps)
			var y := t * h
			var x := w * (0.15 + 0.55 * sin(t * PI * 0.72))
			points.append(Vector2(x, y))
		points.append(Vector2(w, h))
		points.append(Vector2(w, 0))
		draw_colored_polygon(points, ink)
		var stroke := PackedVector2Array()
		for i in range(steps + 1):
			var t := float(i) / float(steps)
			var y := t * h
			var x := w * (0.15 + 0.55 * sin(t * PI * 0.72))
			stroke.append(Vector2(x, y))
		draw_polyline(stroke, ivory, 1.0, true)
