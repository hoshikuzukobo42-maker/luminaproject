extends CanvasLayer
class_name SIGodotControlPanel

signal chat_rendered(correlation_id: String, rendered_text: String)

@export var bridge_base_url: String = "http://127.0.0.1:8765"
@export var player_path: NodePath
@export var avatar_path: NodePath
@export var title_menu_path: NodePath

var _status_label: Label
var _detail_label: Label
var _mind_label: Label
var _thought_strip: PanelContainer
var _thought_strip_label: Label
var _chat_input: LineEdit
var _panel: PanelContainer
var _hud_panel: PanelContainer
var _hud_status_label: Label
var _hud_mode_label: Label
var _hud_prompt_label: Label
var _quick_bar: PanelContainer
var _details_scroll: ScrollContainer
var _details_container: VBoxContainer
var _details_toggle_button: Button
var _player: Node
var _avatar: Node3D
var _title_menu: CanvasLayer
var _status_request: HTTPRequest
var _refresh_timer: Timer
var _details_visible := false
var _operator_panel_visible := false
var _hud_visible := true
var _quick_bar_enabled := false
var _thought_strip_enabled := false
var _chat_request_sequence := 0
var _chat_session_nonce := ""

const DEFAULT_HUD_PROMPT := "E: 近づく  /: 話す  R: 正面  Tab: 操作"


func _ready() -> void:
	layer = 64
	_chat_session_nonce = Crypto.new().generate_random_bytes(8).hex_encode()
	if _chat_session_nonce.is_empty():
		_chat_session_nonce = "%d_%d" % [int(Time.get_unix_time_from_system() * 1000.0), get_instance_id()]
	_player = get_node_or_null(player_path)
	_avatar = get_node_or_null(avatar_path) as Node3D
	_build_panel()
	_setup_status_refresh()
	call_deferred("_resolve_runtime_nodes")


func _input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		_release_chat_focus_if_clicked_outside(event.position)


func _unhandled_input(event: InputEvent) -> void:
	if _is_text_entry_focused():
		if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_ESCAPE:
			if _chat_input:
				_chat_input.release_focus()
			_stop_player_keyboard_input()
			get_viewport().set_input_as_handled()
		return
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_TAB:
		set_operator_panel_visible(not _operator_panel_visible)
		get_viewport().set_input_as_handled()
	elif event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_SLASH:
		_focus_chat()
		get_viewport().set_input_as_handled()
	elif event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_E:
		_approach_and_face_lumina()
		get_viewport().set_input_as_handled()
	elif event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_R:
		_return_to_interaction_view()
		get_viewport().set_input_as_handled()


func set_exhibit_mode(enabled: bool) -> void:
	_resolve_runtime_nodes()
	visible = enabled
	_hud_visible = enabled
	set_operator_panel_visible(false)
	_quick_bar_enabled = enabled
	_thought_strip_enabled = false
	_apply_hud_visibility()
	_apply_panel_layout()


func set_operator_panel_visible(enabled: bool) -> void:
	_operator_panel_visible = enabled
	if _panel:
		_panel.visible = enabled
	_apply_panel_layout()


func is_operator_panel_visible() -> bool:
	return _operator_panel_visible


func _resolve_runtime_nodes() -> void:
	if _player == null and player_path != NodePath():
		_player = get_node_or_null(player_path)
	if _avatar == null and avatar_path != NodePath():
		_avatar = get_node_or_null(avatar_path) as Node3D
	if _title_menu == null and title_menu_path != NodePath():
		_title_menu = get_node_or_null(title_menu_path) as CanvasLayer


func _build_panel() -> void:
	var root := Control.new()
	root.name = "GodotControlPanelRoot"
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.resized.connect(_apply_panel_layout)
	add_child(root)

	_panel = PanelContainer.new()
	var panel := _panel
	panel.name = "Panel"
	panel.visible = _operator_panel_visible
	panel.mouse_filter = Control.MOUSE_FILTER_STOP
	_apply_panel_layout()
	var panel_style := StyleBoxFlat.new()
	panel_style.bg_color = Color(0.030, 0.034, 0.034, 0.84)
	panel_style.border_color = Color(0.85, 0.92, 0.88, 0.12)
	panel_style.set_border_width_all(1)
	panel_style.set_corner_radius_all(8)
	panel.add_theme_stylebox_override("panel", panel_style)
	root.add_child(panel)
	_build_game_hud(root)
	_build_quick_bar(root)
	_build_thought_strip(root)

	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 14)
	margin.add_theme_constant_override("margin_top", 12)
	margin.add_theme_constant_override("margin_right", 14)
	margin.add_theme_constant_override("margin_bottom", 12)
	panel.add_child(margin)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	margin.add_child(box)

	var title := Label.new()
	title.text = "メニュー"
	title.add_theme_font_size_override("font_size", 18)
	title.add_theme_color_override("font_color", Color(1.0, 0.98, 0.92))
	box.add_child(title)

	_status_label = Label.new()
	_status_label.text = "状態: 確認中"
	_status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status_label.add_theme_font_size_override("font_size", 13)
	_status_label.add_theme_color_override("font_color", Color(0.88, 0.95, 1.0))
	box.add_child(_status_label)

	_detail_label = Label.new()
	_detail_label.text = "静かな生活モードが標準です。"
	_detail_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_detail_label.max_lines_visible = 2
	_detail_label.add_theme_font_size_override("font_size", 12)
	_detail_label.add_theme_color_override("font_color", Color(0.88, 0.90, 0.88))
	box.add_child(_detail_label)

	_mind_label = Label.new()
	_mind_label.text = "意思: 状態取得中"
	_mind_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_mind_label.max_lines_visible = 1
	_mind_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_mind_label.add_theme_font_size_override("font_size", 12)
	_mind_label.add_theme_color_override("font_color", Color(0.92, 0.93, 0.90))
	box.add_child(_mind_label)

	_add_separator(box)
	_add_section_label(box, "操作")

	var primary_grid := GridContainer.new()
	primary_grid.columns = 3
	primary_grid.add_theme_constant_override("h_separation", 6)
	primary_grid.add_theme_constant_override("v_separation", 6)
	box.add_child(primary_grid)
	_add_button(primary_grid, "展示開始", _final_demo)
	_add_button(primary_grid, "こちらを見る", _look_at_user)
	_add_button(primary_grid, "自律開始", _start_quiet_life)
	_add_button(primary_grid, "自律停止", _stop_life)
	_add_button(primary_grid, "一人称", _user_pov)
	_add_button(primary_grid, "三人称", _third_person)
	_add_button(primary_grid, "リセット", _stage_reset)
	_add_button(primary_grid, "発話停止", _mute_loop)
	var emergency_button := _add_button(primary_grid, "緊急停止", _emergency_stop)
	_style_emergency_button(emergency_button)

	_details_toggle_button = Button.new()
	_details_toggle_button.text = "詳細操作を表示" if not _details_visible else "詳細操作を隠す"
	_details_toggle_button.focus_mode = Control.FOCUS_NONE
	_details_toggle_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_details_toggle_button.custom_minimum_size = Vector2(0.0, 34.0)
	_details_toggle_button.add_theme_font_size_override("font_size", 13)
	_details_toggle_button.pressed.connect(_toggle_details)
	box.add_child(_details_toggle_button)

	_details_scroll = ScrollContainer.new()
	_details_scroll.visible = _details_visible
	_details_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_details_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_details_scroll.custom_minimum_size = Vector2(0.0, 180.0)
	box.add_child(_details_scroll)

	_details_container = VBoxContainer.new()
	_details_container.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_details_container.add_theme_constant_override("separation", 14)
	_details_scroll.add_child(_details_container)

	_add_section_label(_details_container, "対話")

	_chat_input = LineEdit.new()
	_chat_input.placeholder_text = "話す/お願いする 例: ソファに行って / こちらを見て"
	_chat_input.text = "ソファに行って"
	_chat_input.focus_mode = Control.FOCUS_CLICK
	_chat_input.focus_entered.connect(_stop_player_keyboard_input)
	_chat_input.focus_exited.connect(_stop_player_keyboard_input)
	_chat_input.text_submitted.connect(func(_text: String) -> void: _send_chat())
	_chat_input.add_theme_font_size_override("font_size", 16)
	_chat_input.custom_minimum_size = Vector2(0.0, 44.0)
	_details_container.add_child(_chat_input)
	_add_button(_details_container, "チャット送信", _send_chat)

	_add_separator(_details_container)
	_add_section_label(_details_container, "カメラ")

	var camera_row := HBoxContainer.new()
	camera_row.add_theme_constant_override("separation", 8)
	_details_container.add_child(camera_row)
	_add_button(camera_row, "全身", func() -> void: _camera_preset("full_front", "全身"))
	_add_button(camera_row, "顔", _camera_face_pair)
	_add_button(camera_row, "三人称", func() -> void: _camera_preset("player_third_person", "三人称"))
	_add_button(camera_row, "自律", func() -> void: _camera_preset("autonomy_audience", "自律"))

	_add_separator(_details_container)
	_add_section_label(_details_container, "ユーザー位置")

	var row_a := HBoxContainer.new()
	row_a.add_theme_constant_override("separation", 8)
	_details_container.add_child(row_a)
	_add_button(row_a, "近く", _player_near)
	_add_button(row_a, "正面", _player_home)
	_add_button(row_a, "ソファ", _player_sofa)

	var row_b := HBoxContainer.new()
	row_b.add_theme_constant_override("separation", 8)
	_details_container.add_child(row_b)
	_add_button(row_b, "前", func() -> void: _player_move(Vector3(0.0, 0.0, -0.45)))
	_add_button(row_b, "左", func() -> void: _player_move(Vector3(-0.45, 0.0, 0.0)))
	_add_button(row_b, "後", func() -> void: _player_move(Vector3(0.0, 0.0, 0.45)))
	_add_button(row_b, "右", func() -> void: _player_move(Vector3(0.45, 0.0, 0.0)))

	var hint := Label.new()
	hint.text = "WASD/矢印で移動、右ドラッグで視点、Eで近づく、Rで正面、/で会話、V/Cで視点、Tabで閉じる。"
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	hint.add_theme_font_size_override("font_size", 13)
	hint.add_theme_color_override("font_color", Color(0.78, 0.82, 0.80))
	_details_container.add_child(hint)
	_apply_panel_layout()


func _build_game_hud(root: Control) -> void:
	_hud_panel = PanelContainer.new()
	_hud_panel.name = "LuminaGameHud"
	var hud_style := StyleBoxFlat.new()
	hud_style.bg_color = Color(0.028, 0.032, 0.038, 0.52)
	hud_style.border_color = Color(0.95, 0.98, 0.92, 0.18)
	hud_style.set_border_width_all(1)
	hud_style.set_corner_radius_all(10)
	_hud_panel.add_theme_stylebox_override("panel", hud_style)
	root.add_child(_hud_panel)

	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 10)
	margin.add_theme_constant_override("margin_top", 7)
	margin.add_theme_constant_override("margin_right", 10)
	margin.add_theme_constant_override("margin_bottom", 7)
	_hud_panel.add_child(margin)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 4)
	margin.add_child(box)

	var title := Label.new()
	title.text = "LUMINA"
	title.add_theme_font_size_override("font_size", 19)
	title.add_theme_color_override("font_color", Color(0.96, 1.0, 0.95))
	box.add_child(title)

	_hud_status_label = Label.new()
	_hud_status_label.text = "Bridge確認中"
	_hud_status_label.add_theme_font_size_override("font_size", 13)
	_hud_status_label.add_theme_color_override("font_color", Color(0.88, 0.95, 0.98))
	box.add_child(_hud_status_label)

	_hud_mode_label = Label.new()
	_hud_mode_label.text = "視点: user_pov"
	_hud_mode_label.add_theme_font_size_override("font_size", 12)
	_hud_mode_label.add_theme_color_override("font_color", Color(0.78, 0.84, 0.82))
	box.add_child(_hud_mode_label)

	_hud_prompt_label = Label.new()
	_hud_prompt_label.text = DEFAULT_HUD_PROMPT
	_hud_prompt_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_hud_prompt_label.max_lines_visible = 2
	_hud_prompt_label.add_theme_font_size_override("font_size", 12)
	_hud_prompt_label.add_theme_color_override("font_color", Color(0.62, 0.74, 0.78))
	box.add_child(_hud_prompt_label)


func _build_quick_bar(root: Control) -> void:
	_quick_bar = PanelContainer.new()
	_quick_bar.name = "LuminaQuickBar"
	var quick_style := StyleBoxFlat.new()
	quick_style.bg_color = Color(0.045, 0.047, 0.044, 0.74)
	quick_style.border_color = Color(1.0, 1.0, 1.0, 0.16)
	quick_style.set_border_width_all(1)
	quick_style.set_corner_radius_all(10)
	_quick_bar.add_theme_stylebox_override("panel", quick_style)
	root.add_child(_quick_bar)

	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 10)
	margin.add_theme_constant_override("margin_top", 8)
	margin.add_theme_constant_override("margin_right", 10)
	margin.add_theme_constant_override("margin_bottom", 8)
	_quick_bar.add_child(margin)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	margin.add_child(row)
	_add_quick_button(row, "近づく", _approach_and_face_lumina)
	_add_quick_button(row, "見る", _look_at_user)
	_add_quick_button(row, "話す", _focus_chat)
	_add_quick_button(row, "正面", _return_to_interaction_view)
	_add_quick_button(row, "タイトル", _return_to_title)
	_add_quick_button(row, "停止", _emergency_stop)


func _build_thought_strip(root: Control) -> void:
	_thought_strip = PanelContainer.new()
	_thought_strip.name = "LuminaThoughtStrip"
	var strip_style := StyleBoxFlat.new()
	strip_style.bg_color = Color(0.04, 0.05, 0.05, 0.66)
	strip_style.border_color = Color(1.0, 1.0, 1.0, 0.18)
	strip_style.set_border_width_all(1)
	strip_style.set_corner_radius_all(8)
	_thought_strip.add_theme_stylebox_override("panel", strip_style)
	root.add_child(_thought_strip)

	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 15)
	margin.add_theme_constant_override("margin_top", 12)
	margin.add_theme_constant_override("margin_right", 15)
	margin.add_theme_constant_override("margin_bottom", 12)
	_thought_strip.add_child(margin)

	_thought_strip_label = Label.new()
	_thought_strip_label.text = "ルミナ: 状態取得中"
	_thought_strip_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_thought_strip_label.max_lines_visible = 3
	_thought_strip_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_thought_strip_label.add_theme_font_size_override("font_size", 18)
	_thought_strip_label.add_theme_color_override("font_color", Color(0.94, 0.96, 0.93))
	margin.add_child(_thought_strip_label)


func _add_separator(parent: Node) -> void:
	var separator := HSeparator.new()
	parent.add_child(separator)


func _add_section_label(parent: Node, text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", 14)
	label.add_theme_color_override("font_color", Color(0.78, 0.90, 1.0))
	parent.add_child(label)
	return label


func _add_button(parent: Node, text: String, callback: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.focus_mode = Control.FOCUS_NONE
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.custom_minimum_size = Vector2(0.0, 38.0)
	button.add_theme_font_size_override("font_size", 13)
	button.pressed.connect(callback)
	parent.add_child(button)
	return button


func _add_quick_button(parent: Node, text: String, callback: Callable) -> Button:
	var button := _add_button(parent, text, callback)
	button.custom_minimum_size = Vector2(78.0, 42.0)
	button.add_theme_font_size_override("font_size", 15)
	var normal := StyleBoxFlat.new()
	normal.bg_color = Color(0.14, 0.19, 0.18, 0.86)
	normal.set_corner_radius_all(8)
	var hover := StyleBoxFlat.new()
	hover.bg_color = Color(0.18, 0.27, 0.25, 0.94)
	hover.set_corner_radius_all(8)
	var pressed := StyleBoxFlat.new()
	pressed.bg_color = Color(0.10, 0.14, 0.14, 0.94)
	pressed.set_corner_radius_all(8)
	button.add_theme_stylebox_override("normal", normal)
	button.add_theme_stylebox_override("hover", hover)
	button.add_theme_stylebox_override("pressed", pressed)
	button.add_theme_color_override("font_color", Color(0.94, 0.98, 0.94))
	return button


func _style_emergency_button(button: Button) -> void:
	if button == null:
		return
	button.custom_minimum_size = Vector2(0.0, 38.0)
	button.add_theme_font_size_override("font_size", 13)
	button.add_theme_color_override("font_color", Color(1.0, 0.96, 0.94))
	button.add_theme_color_override("font_hover_color", Color(1.0, 1.0, 1.0))
	var normal := StyleBoxFlat.new()
	normal.bg_color = Color(0.58, 0.12, 0.09, 0.92)
	normal.set_corner_radius_all(8)
	var hover := StyleBoxFlat.new()
	hover.bg_color = Color(0.72, 0.16, 0.12, 0.98)
	hover.set_corner_radius_all(8)
	var pressed := StyleBoxFlat.new()
	pressed.bg_color = Color(0.42, 0.08, 0.07, 0.98)
	pressed.set_corner_radius_all(8)
	button.add_theme_stylebox_override("normal", normal)
	button.add_theme_stylebox_override("hover", hover)
	button.add_theme_stylebox_override("pressed", pressed)


func _setup_status_refresh() -> void:
	_status_request = HTTPRequest.new()
	_status_request.name = "StatusRequest"
	add_child(_status_request)
	_status_request.request_completed.connect(_on_status_request_completed)

	_refresh_timer = Timer.new()
	_refresh_timer.wait_time = 1.0
	_refresh_timer.autostart = true
	_refresh_timer.timeout.connect(_refresh_status)
	add_child(_refresh_timer)
	_refresh_status()


func _toggle_details() -> void:
	_details_visible = not _details_visible
	if _details_scroll:
		_details_scroll.visible = _details_visible
	if _details_toggle_button:
		_details_toggle_button.text = "詳細操作を隠す" if _details_visible else "詳細操作を表示"
	_apply_panel_layout()


func _apply_panel_layout() -> void:
	if not _panel:
		return
	var viewport_size := get_viewport().get_visible_rect().size
	var margin_right := 18.0
	var top := 72.0
	var bottom_margin := 24.0
	var width = clamp(viewport_size.x * 0.22, 318.0, 380.0)
	var collapsed_height = min(viewport_size.y - top - bottom_margin, 352.0)
	var expanded_height = min(viewport_size.y - top - bottom_margin, 620.0)
	var height = expanded_height if _details_visible else collapsed_height
	_panel.anchor_left = 1.0
	_panel.anchor_top = 0.0
	_panel.anchor_right = 1.0
	_panel.anchor_bottom = 0.0
	_panel.offset_left = -margin_right - width
	_panel.offset_top = top
	_panel.offset_right = -margin_right
	_panel.offset_bottom = top + height
	if _hud_panel:
		var hud_width = clamp(viewport_size.x * 0.19, 260.0, 350.0)
		var hud_height := 102.0
		_hud_panel.anchor_left = 0.0
		_hud_panel.anchor_top = 0.0
		_hud_panel.anchor_right = 0.0
		_hud_panel.anchor_bottom = 0.0
		_hud_panel.offset_left = 12.0
		_hud_panel.offset_top = 12.0
		_hud_panel.offset_right = 12.0 + hud_width
		_hud_panel.offset_bottom = 12.0 + hud_height
	if _quick_bar:
		var quick_width = min(viewport_size.x - 36.0, 590.0)
		var quick_height := 60.0
		_quick_bar.anchor_left = 0.5
		_quick_bar.anchor_top = 1.0
		_quick_bar.anchor_right = 0.5
		_quick_bar.anchor_bottom = 1.0
		_quick_bar.offset_left = -quick_width * 0.5
		_quick_bar.offset_top = -18.0 - quick_height
		_quick_bar.offset_right = quick_width * 0.5
		_quick_bar.offset_bottom = -18.0
	if _thought_strip:
		var strip_width = clamp(viewport_size.x * 0.4, 420.0, 600.0)
		var strip_height := 118.0
		_thought_strip.anchor_left = 0.0
		_thought_strip.anchor_top = 1.0
		_thought_strip.anchor_right = 0.0
		_thought_strip.anchor_bottom = 1.0
		_thought_strip.offset_left = 24.0
		_thought_strip.offset_top = -92.0 - strip_height
		_thought_strip.offset_right = 24.0 + strip_width
		_thought_strip.offset_bottom = -92.0
	_apply_hud_visibility()


func _apply_hud_visibility() -> void:
	var hud_active := _hud_visible and visible
	if _hud_panel:
		_hud_panel.visible = hud_active
	if _quick_bar:
		_quick_bar.visible = hud_active and _quick_bar_enabled
	if _thought_strip:
		_thought_strip.visible = hud_active and _thought_strip_enabled


func _stage_reset() -> void:
	_post_json("/routine/stage_reset", {}, "準備リセット")


func _final_demo() -> void:
	_post_json("/routine/final_demo", {}, "展示開始")


func _start_quiet_life() -> void:
	_post_json("/exhibit-life/start", {
		"enable_loop": true,
		"run_intro": true,
		"intro_speech": false,
		"allow_speech": false,
		"interval_seconds": 20,
		"mode": "life",
		"use_llm": true,
	}, "自律開始")


func _stop_life() -> void:
	_post_json("/exhibit-life/stop", {}, "自律停止")


func _mute_loop() -> void:
	_post_json("/autonomy-loop", {"allow_speech": false}, "発話停止")


func _emergency_stop() -> void:
	_post_json("/exhibit-life/stop", {}, "緊急: 自律停止")
	_post_json("/autonomy-loop", {"allow_speech": false}, "緊急: 発話停止")
	_post_json("/command", {"action": "stop", "reason": "godot_control_panel_emergency"}, "緊急停止")


func _look_at_user() -> void:
	_post_json("/command", {"action": "look_at_user", "reason": "godot_control_panel"}, "こちらを見る")


func _user_pov() -> void:
	_post_json("/sequence", {
		"label": "godot_panel_user_pov",
		"wait_for_completion": false,
		"commands": [
			{"action": "camera_preset", "params": {"preset": "user_pov"}, "reason": "godot_control_panel"},
			{"action": "look_at_user", "reason": "godot_control_panel"},
		],
		}, "一人称")


func _approach_and_face_lumina() -> void:
	_resolve_runtime_nodes()
	var moved := false
	if _player != null and (_player is Node3D) and _avatar != null:
		var player_node := _player as Node3D
		if player_node.global_position.distance_to(_avatar.global_position) > 2.35:
			if _player.has_method("teleport_near_lumina"):
				_player.call("teleport_near_lumina")
				moved = true
	elif _player != null and _player.has_method("teleport_near_lumina"):
		_player.call("teleport_near_lumina")
		moved = true
	_face_player_to_avatar()
	_post_json("/sequence", {
		"label": "godot_panel_approach_lumina",
		"wait_for_completion": false,
		"commands": [
			{"action": "camera_preset", "params": {"preset": "user_pov"}, "reason": "godot_control_panel_interact"},
			{"action": "look_at_user", "reason": "godot_control_panel_interact"},
		],
	}, "近づく")
	_set_detail("近づく: %s / /で話せます" % ("ルミナ前へ移動" if moved else "向き合い直し"))


func _third_person() -> void:
	_post_json("/command", {
		"action": "camera_preset",
		"params": {"preset": "player_third_person"},
		"reason": "godot_control_panel",
	}, "三人称")


func _send_chat() -> void:
	var text := _chat_input.text.strip_edges() if _chat_input else ""
	if text.is_empty():
		_set_detail("対話: 入力が空です")
		return
	_chat_request_sequence += 1
	var correlation_id := "turn_godot_chat_%s_%06d" % [_chat_session_nonce, _chat_request_sequence]
	_post_json("/visitor-chat", {
		"request_id": correlation_id,
		"trace_contract": "p1-06",
		"ui_submitted_at_ms": Time.get_ticks_msec(),
		"text": text,
		"source": "godot_panel",
		"send": true,
		"look_at_user": true,
		"gesture": true,
		"timeout_seconds": 30.0,
		}, "チャット")


func _focus_chat() -> void:
	set_operator_panel_visible(true)
	if not _details_visible:
		_details_visible = true
		if _details_scroll:
			_details_scroll.visible = true
		if _details_toggle_button:
			_details_toggle_button.text = "詳細操作を隠す"
		_apply_panel_layout()
	if _chat_input:
		_chat_input.grab_focus()
		_chat_input.caret_column = _chat_input.text.length()
	_set_detail("チャット: 入力できます")


func _camera_preset(preset: String, label: String) -> void:
	_post_json("/command", {
		"action": "camera_preset",
		"params": {"preset": preset},
		"reason": "godot_control_panel",
	}, "カメラ %s" % label)


func _camera_face_pair() -> void:
	_post_json("/sequence", {
		"label": "godot_panel_face_pair",
		"wait_for_completion": false,
			"commands": [
				{
					"action": "camera_set",
					"params": {"target_role": "avatar", "distance": 2.0, "pitch": 10.0, "height": 1.42, "yaw": 0.0, "relative_to_target": true, "fov": 40.0},
					"reason": "godot_control_panel",
				},
			{"action": "look_at_user", "reason": "godot_control_panel"},
		],
	}, "カメラ 顔")


func _player_near() -> void:
	_resolve_runtime_nodes()
	if _player and _player.has_method("teleport_near_lumina"):
		_player.call("teleport_near_lumina")
		_face_player_to_avatar()
		_set_detail("ユーザー: ルミナの近く")


func _player_home() -> void:
	_resolve_runtime_nodes()
	if _player and _player.has_method("teleport_home"):
		_player.call("teleport_home")
		_face_player_to_avatar()
		_set_detail("ユーザー: 正面")


func _return_to_interaction_view() -> void:
	_player_home()
	_post_json("/sequence", {
		"label": "godot_panel_return_interaction_view",
		"wait_for_completion": false,
		"commands": [
			{"action": "camera_preset", "params": {"preset": "user_pov"}, "reason": "godot_control_panel_return"},
			{"action": "look_at_user", "reason": "godot_control_panel_return"},
		],
	}, "正面")


func _return_to_title() -> void:
	_resolve_runtime_nodes()
	if _title_menu and _title_menu.has_method("_show_menu"):
		_title_menu.call("_show_menu")
		_set_detail("タイトルへ戻りました")
	else:
		_return_to_interaction_view()


func _player_sofa() -> void:
	_resolve_runtime_nodes()
	if _player and _player.has_method("teleport_observe_sofa"):
		_player.call("teleport_observe_sofa")
		_face_player_to_avatar()
		_set_detail("ユーザー: ソファ側")


func _player_move(offset: Vector3) -> void:
	_resolve_runtime_nodes()
	if _player and _player.has_method("move_by"):
		_player.call("move_by", offset)
		_face_player_to_avatar()
		_set_detail("ユーザーを移動しました")


func _face_player_to_avatar() -> void:
	_resolve_runtime_nodes()
	if not _player or not _avatar:
		return
	if _player.has_method("face_position"):
		_player.call("face_position", _avatar.global_position)


func _refresh_status() -> void:
	if not is_instance_valid(_status_request):
		return
	if _status_request.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		return
	var error := _status_request.request(_url("/experience-status"))
	if error != OK:
		_status_label.text = "状態: Bridge未接続"
		if _hud_status_label:
			_hud_status_label.text = "Bridge未接続"


func _post_json(path: String, payload: Dictionary, label: String) -> void:
	var request := HTTPRequest.new()
	request.name = "CommandRequest"
	add_child(request)
	var body := JSON.stringify(payload)
	var headers := PackedStringArray(["Content-Type: application/json"])
	request.request_completed.connect(_on_command_request_completed.bind(request, label))
	var error := request.request(_url(path), headers, HTTPClient.METHOD_POST, body)
	if error != OK:
		request.queue_free()
		_set_detail("%s: 送信失敗" % label)
	else:
		if label == "チャット":
			_set_detail("チャット: AI応答待ち...")
		else:
			_set_detail("%s: 送信しました" % label)


func _on_status_request_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if result != HTTPRequest.RESULT_SUCCESS or response_code < 200 or response_code >= 300:
		_status_label.text = "状態: Bridge未接続"
		if _hud_status_label:
			_hud_status_label.text = "Bridge未接続"
		return
	var parsed: Variant = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		_status_label.text = "状態: 応答解析エラー"
		if _hud_status_label:
			_hud_status_label.text = "応答解析エラー"
		return
	var status := parsed as Dictionary
	var visible := status.get("current_visible_state", {}) as Dictionary
	var life := status.get("life_mode", {}) as Dictionary
	var mind := visible.get("mind", {}) as Dictionary
	var visual_search := visible.get("visual_search", {}) as Dictionary
	var search_label := ""
	if bool(visual_search.get("active", false)) or bool(visual_search.get("search_required", false)):
		search_label = " / 探索=%s" % _compact_panel_text(
			String(visual_search.get("looking_for", visual_search.get("target_name", ""))),
			16
		)
	_status_label.text = "状態: %s / 視点: %s" % [
		String(status.get("experience_status", "unknown")),
		String(visible.get("camera_preset", "unknown")),
	]
	if _hud_status_label:
		_hud_status_label.text = "状態: %s" % String(status.get("experience_status", "unknown"))
	if _hud_mode_label:
		_hud_mode_label.text = "視点: %s / 自律: %s" % [
			String(visible.get("camera_preset", "unknown")),
			String(life.get("loop_mode", "off")),
		]
	_detail_label.text = "自律=%s / 発話=%s / 視線=%s%s" % [
		String(life.get("loop_mode", "off")),
		"ON" if bool(life.get("allow_speech", false)) else "OFF",
		String(visible.get("look_attention_state", "none")),
		search_label,
	]
	if _mind_label:
		_mind_label.text = "見た: %s\n考えた: %s\n次: %s" % [
			_compact_panel_text(String(mind.get("saw", visible.get("vision_summary", "取得中"))), 44),
			_compact_panel_text(String(mind.get("thought", mind.get("intent", "-"))), 34),
			_compact_panel_text(String(mind.get("next_action", "-")), 30),
		]
	if _thought_strip_label:
		_thought_strip_label.text = "見た: %s\n考えた: %s\n次: %s" % [
			_compact_panel_text(String(mind.get("saw", visible.get("vision_summary", "取得中"))), 54),
			_compact_panel_text(String(mind.get("thought", mind.get("intent", "-"))), 46),
			_compact_panel_text(String(mind.get("next_action", "-")), 42),
		]


func _on_command_request_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray, request: HTTPRequest, label: String) -> void:
	if is_instance_valid(request):
		request.queue_free()
	if result != HTTPRequest.RESULT_SUCCESS:
		_set_detail("%s: 失敗" % label)
		return
	var ok := response_code >= 200 and response_code < 300
	var suffix := ""
	if not ok:
		suffix = " %s" % body.get_string_from_utf8().left(80)
	var detail_text := "%s: %s%s" % [label, "OK" if ok else "エラー", suffix]
	var correlation_id := ""
	if ok and label == "チャット":
		var parsed: Variant = JSON.parse_string(body.get_string_from_utf8())
		if typeof(parsed) == TYPE_DICTIONARY:
			var data := parsed as Dictionary
			correlation_id = String(data.get("request_id", "")).strip_edges()
			var reply := String(data.get("reply_text", data.get("reply", ""))).strip_edges()
			var model := String(data.get("route_model", data.get("model", ""))).strip_edges()
			var provider_status := String(data.get("provider_status", "")).strip_edges()
			var ai_label := model if not model.is_empty() else provider_status
			if not reply.is_empty():
				detail_text = "AI(%s): %s" % [ai_label, _compact_panel_text(reply, 120)]
			else:
				detail_text = "チャット: OK（返答本文なし）"
	_set_detail(detail_text)
	if ok and label == "チャット" and not correlation_id.is_empty():
		_post_ui_render_ack(correlation_id, detail_text)
		chat_rendered.emit(correlation_id, detail_text)
	if label != "チャット":
		_refresh_status()


func _post_ui_render_ack(correlation_id: String, rendered_text: String) -> void:
	var request := HTTPRequest.new()
	request.name = "P106UIRenderAck"
	add_child(request)
	request.request_completed.connect(
		func(_result: int, _response_code: int, _headers: PackedStringArray, _body: PackedByteArray) -> void:
			if is_instance_valid(request):
				request.queue_free()
	)
	var payload := {
		"correlation_id": correlation_id,
		"trace_contract": "p1-06",
		"render_target": "godot_control_panel.detail_or_hud_label",
		"rendered_text_hash": rendered_text.sha256_text(),
		"visible": (_detail_label != null and _detail_label.is_visible_in_tree()) or (_hud_prompt_label != null and _hud_prompt_label.is_visible_in_tree()),
		"rendered_at_ms": Time.get_ticks_msec(),
	}
	var error := request.request(
		_url("/trace/ui-render"),
		PackedStringArray(["Content-Type: application/json"]),
		HTTPClient.METHOD_POST,
		JSON.stringify(payload)
	)
	if error != OK:
		request.queue_free()


func _set_detail(text: String) -> void:
	if _detail_label:
		_detail_label.text = text
	if _hud_prompt_label:
		if text.strip_edges().is_empty():
			_hud_prompt_label.text = DEFAULT_HUD_PROMPT
		else:
			_hud_prompt_label.text = _compact_panel_text(text, 54)


func _compact_panel_text(value: String, limit: int) -> String:
	var text := value.strip_edges()
	if text.length() <= limit:
		return text
	return text.left(max(0, limit - 1)) + "..."


func _release_chat_focus_if_clicked_outside(position: Vector2) -> void:
	if not _chat_input or not _chat_input.has_focus():
		return
	if _chat_input.get_global_rect().has_point(position):
		return
	_chat_input.release_focus()
	_stop_player_keyboard_input()


func _is_text_entry_focused() -> bool:
	var viewport := get_viewport()
	if viewport == null:
		return false
	var focus_owner := viewport.gui_get_focus_owner()
	return focus_owner is LineEdit or focus_owner is TextEdit or focus_owner is SpinBox


func _stop_player_keyboard_input() -> void:
	if _player and _player.has_method("stop_keyboard_input"):
		_player.call("stop_keyboard_input")


func _url(path: String) -> String:
	var base := bridge_base_url
	if base.ends_with("/"):
		base = base.substr(0, base.length() - 1)
	return base + path
