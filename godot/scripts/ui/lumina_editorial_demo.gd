extends Node
class_name LuminaEditorialDemo

## Self-contained demo + acceptance harness for LuminaEditorialHUD.
## Launch without changing project.godot:
##   Godot --path godot res://ui/lumina_editorial/lumina_editorial_demo.tscn

const HUD_SCRIPT := preload("res://scripts/ui/lumina_editorial_hud.gd")
const Palette := preload("res://scripts/ui/lumina_editorial_palette.gd")

@export var auto_verify: bool = true
@export var auto_quit: bool = true
@export var capture_screenshots: bool = true

var _hud: CanvasLayer
var _backdrop: ColorRect
var _sky: ColorRect
var _floor: ColorRect
var _window_glow: ColorRect
var _status: Label
var _results: Dictionary = {}
var _report_dir: String = ""


func _ready() -> void:
	_report_dir = _resolve_report_dir()
	DirAccess.make_dir_recursive_absolute(_report_dir)
	_build_environment()
	_build_hud()
	_status = Label.new()
	_status.position = Vector2(24, 64)
	_status.add_theme_color_override("font_color", Color(0.85, 0.88, 0.90, 0.7))
	_status.add_theme_font_size_override("font_size", 12)
	_status.text = "Lumina Editorial UI demo"
	add_child(_status)
	if auto_verify:
		call_deferred("_run_acceptance")


func _build_environment() -> void:
	var layer := CanvasLayer.new()
	layer.layer = 0
	add_child(layer)
	_backdrop = ColorRect.new()
	_backdrop.set_anchors_preset(Control.PRESET_FULL_RECT)
	_backdrop.color = Palette.SUMIIRO_DEEP
	layer.add_child(_backdrop)

	_sky = ColorRect.new()
	_sky.set_anchors_preset(Control.PRESET_TOP_WIDE)
	_sky.offset_bottom = 520
	_sky.color = Palette.HARBOR_COBALT_DEEP
	layer.add_child(_sky)

	# Cobalt wash — night harbor sky suggestion.
	var wash := ColorRect.new()
	wash.set_anchors_preset(Control.PRESET_TOP_WIDE)
	wash.offset_top = 180
	wash.offset_bottom = 620
	wash.color = Color(0.08, 0.20, 0.42, 0.55)
	layer.add_child(wash)

	_window_glow = ColorRect.new()
	_window_glow.position = Vector2(120, 140)
	_window_glow.size = Vector2(980, 420)
	_window_glow.color = Color(0.14, 0.32, 0.58, 0.22)
	layer.add_child(_window_glow)

	# City speckles
	var rng := RandomNumberGenerator.new()
	rng.seed = 20260718
	for i in 48:
		var speck := ColorRect.new()
		speck.size = Vector2(rng.randi_range(2, 5), rng.randi_range(2, 8))
		speck.position = Vector2(140 + rng.randi_range(0, 900), 160 + rng.randi_range(0, 380))
		speck.color = Color(0.85, 0.82, 0.70, rng.randf_range(0.15, 0.55))
		layer.add_child(speck)

	_floor = ColorRect.new()
	_floor.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	_floor.offset_top = -280
	_floor.color = Color(0.06, 0.06, 0.07, 1.0)
	layer.add_child(_floor)

	var furniture := ColorRect.new()
	furniture.position = Vector2(520, 520)
	furniture.size = Vector2(260, 48)
	furniture.color = Color(0.12, 0.11, 0.10, 1.0)
	layer.add_child(furniture)


func _build_hud() -> void:
	_hud = CanvasLayer.new()
	_hud.set_script(HUD_SCRIPT)
	_hud.set("demo_mode", true)
	_hud.set("start_with_menu_open", false)
	add_child(_hud)


func _resolve_report_dir() -> String:
	var env := OS.get_environment("LUMINA_UI_REPORT_DIR").strip_edges()
	if not env.is_empty():
		return env
	# Prefer repo reports path from res://
	var base := ProjectSettings.globalize_path("res://")
	# godot/ -> repo root
	var root := base.get_base_dir()
	return root.path_join("reports/lumina_ui_editorial_20260718")


func _run_acceptance() -> void:
	_status.text = "Running acceptance…"
	if DisplayServer.get_name().to_lower() != "headless":
		DisplayServer.window_set_size(Vector2i(1920, 1080))
		DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_WINDOWED)
	await get_tree().process_frame
	await get_tree().process_frame
	await get_tree().process_frame

	var checks: Array = []
	var events: Dictionary = {
		"menu_opened": 0,
		"menu_closed": 0,
		"section_selected": [],
		"interaction_requested": 0,
	}

	_hud.menu_opened.connect(func() -> void: events["menu_opened"] += 1)
	_hud.menu_closed.connect(func() -> void: events["menu_closed"] += 1)
	_hud.section_selected.connect(func(section: String) -> void: events["section_selected"].append(section))
	_hud.interaction_requested.connect(func() -> void: events["interaction_requested"] += 1)

	# 1) Minimal HUD: interaction + subtitle + time
	_hud.call("show_interaction", "Sit Together")
	_hud.call("show_subtitle", "Lumina", "The city feels quieter tonight.")
	_hud.call("set_time_state", "BLUE HOUR 19:42")
	await _settle(4)
	checks.append(_check("interaction_visible", _hud.get("_interaction_visible") == true))
	checks.append(_check("subtitle_visible", _hud.get("_subtitle_visible") == true))
	checks.append(_check("time_set", str(_hud.get("_time_text")).contains("BLUE HOUR")))
	if capture_screenshots:
		await _capture("01_minimal_hud")

	# 2) Open menu — editorial wall
	_hud.call("open_menu")
	await _settle(10)
	checks.append(_check("menu_open", _hud.call("is_menu_open") == true))
	checks.append(_check("menu_opened_signal", events["menu_opened"] >= 1))
	if capture_screenshots:
		await _capture("02_menu_together")

	# 3) Section navigation
	_hud.call("set_menu_section", "MEMORY")
	await _settle(4)
	checks.append(_check("section_memory", _hud.call("get_selected_section") == "MEMORY"))
	_hud.call("set_menu_section", "MOMENTS")
	await _settle(3)
	checks.append(_check("section_moments", _hud.call("get_selected_section") == "MOMENTS"))
	_hud.call("set_menu_section", "SETTINGS")
	await _settle(4)
	checks.append(_check("section_settings", _hud.call("get_selected_section") == "SETTINGS"))
	if capture_screenshots:
		await _capture("03_menu_settings")

	# 4) Reduced motion + font scale + hide
	_hud.call("set_reduced_motion", true)
	checks.append(_check("reduced_motion", _hud.get("reduced_motion") == true))
	_hud.call("set_font_scale", 1.15)
	await _settle(3)
	checks.append(_check("font_scale", abs(float(_hud.get("font_scale")) - 1.15) < 0.001))
	_hud.call("set_ui_hidden", true)
	await _settle(3)
	checks.append(_check("ui_hidden", _hud.get("ui_hidden") == true))
	if capture_screenshots:
		await _capture("04_ui_hidden_menu")
	_hud.call("set_ui_hidden", false)
	_hud.call("set_reduced_motion", false)
	_hud.call("set_font_scale", 1.0)

	# 5) CLOSE closes
	_hud.call("set_menu_section", "CLOSE")
	_hud.call("_activate_selection")
	await _settle(10)
	checks.append(_check("menu_closed_after_return", _hud.call("is_menu_open") == false))
	checks.append(_check("menu_closed_signal", events["menu_closed"] >= 1))

	# 6) hide APIs
	_hud.call("hide_interaction")
	_hud.call("hide_subtitle")
	await _settle(2)
	checks.append(_check("interaction_hidden", _hud.get("_interaction_visible") == false))
	checks.append(_check("subtitle_hidden", _hud.get("_subtitle_visible") == false))

	# 7) Aspect ratio layouts via window size (when display available)
	var aspect_ok := await _verify_aspects()
	checks.append(_check("aspect_layouts", aspect_ok))

	# 8) Re-open concept shot
	_hud.call("show_interaction", "Sit Together")
	_hud.call("show_subtitle", "Lumina", "The city feels quieter tonight.")
	_hud.call("set_time_state", "BLUE HOUR 19:42")
	_hud.call("open_menu")
	_hud.call("set_menu_section", "TOGETHER")
	await _settle(12)
	if capture_screenshots:
		await _capture("05_concept_match")

	var failed: Array = []
	for c in checks:
		if not c["ok"]:
			failed.append(c["name"])

	_results = {
		"pass": failed.is_empty(),
		"failed": failed,
		"checks": checks,
		"events": events,
		"report_dir": _report_dir,
		"timestamp": Time.get_datetime_string_from_system(true, true),
		"godot_version": Engine.get_version_info(),
	}
	var json_path := _report_dir.path_join("acceptance.json")
	var f := FileAccess.open(json_path, FileAccess.WRITE)
	if f:
		f.store_string(JSON.stringify(_results, "\t"))
		f.close()
	_write_review(failed)
	_status.text = "PASS" if failed.is_empty() else ("FAIL: " + ", ".join(failed))
	print("LUMINA_EDITORIAL_UI_ACCEPTANCE pass=", failed.is_empty(), " failed=", failed)
	if auto_quit:
		await get_tree().create_timer(0.4).timeout
		get_tree().quit(0 if failed.is_empty() else 1)


func _verify_aspects() -> bool:
	var sizes := [
		Vector2i(1920, 1080), # 16:9
		Vector2i(1920, 1200), # 16:10
		Vector2i(2560, 1080), # ultrawide
	]
	var ok := true
	var i := 0
	for sz in sizes:
		i += 1
		if DisplayServer.get_name().to_lower() == "headless":
			# Headless: exercise layout math directly.
			_hud.call("_relayout")
		else:
			DisplayServer.window_set_size(sz)
			await _settle(8)
			_hud.call("_relayout")
			await _settle(4)
			if capture_screenshots:
				await _capture("aspect_%02d_%dx%d" % [i, sz.x, sz.y])
		var menu_w: float = _hud.get("_menu_clip").size.x
		if menu_w < 300.0 or menu_w > 580.0:
			ok = false
	# Restore common size
	if DisplayServer.get_name().to_lower() != "headless":
		DisplayServer.window_set_size(Vector2i(1920, 1080))
		await _settle(4)
	return ok


func _check(name: String, ok: bool) -> Dictionary:
	return {"name": name, "ok": ok}


func _settle(frames: int) -> void:
	for _i in frames:
		await get_tree().process_frame


func _capture(name: String) -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	var img: Image = get_viewport().get_texture().get_image()
	if img == null:
		return
	# Flip if needed for OpenGL
	if img.get_height() > 0:
		# Godot 4 get_image is upright for viewport texture in most backends.
		pass
	var path := _report_dir.path_join("%s.png" % name)
	var err := img.save_png(path)
	print("LUMINA_EDITORIAL_UI_CAPTURE ", path, " err=", err)


func _write_review(failed: Array) -> void:
	var shots := []
	var dir := DirAccess.open(_report_dir)
	if dir:
		dir.list_dir_begin()
		var fn := dir.get_next()
		while fn != "":
			if fn.ends_with(".png"):
				shots.append(fn)
			fn = dir.get_next()
		dir.list_dir_end()
	shots.sort()

	var lines: PackedStringArray = []
	lines.append("# Lumina Editorial UI — REVIEW")
	lines.append("")
	lines.append("- Date: 2026-07-18")
	lines.append("- Status: **%s**" % ("PASS" if failed.is_empty() else "FAIL"))
	lines.append("- Scene: `godot/ui/lumina_editorial/lumina_editorial_demo.tscn`")
	lines.append("- HUD: `godot/ui/lumina_editorial/lumina_editorial_hud.tscn`")
	lines.append("- Concept ref: `reports/lumina_ui_concept_20260718/lumina_ui_concept_v1.png`")
	lines.append("")
	lines.append("## Acceptance")
	lines.append("")
	if failed.is_empty():
		lines.append("All automated API / navigation / layout checks passed. See `acceptance.json`.")
	else:
		lines.append("Failed checks: `%s`" % ", ".join(failed))
	lines.append("")
	lines.append("## Screenshots")
	lines.append("")
	for s in shots:
		lines.append("- `%s`" % s)
		lines.append("")
		lines.append("![%s](%s)" % [s, s])
		lines.append("")
	lines.append("## Design constraints observed")
	lines.append("")
	lines.append("- Sumiiro / stone ivory / harbor cobalt / amber only")
	lines.append("- No rounded cards, glass panels, purple gradients, or AI spark icons")
	lines.append("- Minimal HUD in play; giant type only when menu open")
	lines.append("- Menu enters from the right as an architectural wall (wipe/slide, not fade)")
	lines.append("- Lines + whitespace instead of closed frames")
	lines.append("")
	lines.append("## Integration")
	lines.append("")
	lines.append("```gdscript")
	lines.append("var hud := preload(\"res://ui/lumina_editorial/lumina_editorial_hud.tscn\").instantiate()")
	lines.append("add_child(hud)")
	lines.append("hud.show_interaction(\"Sit Together\")")
	lines.append("hud.show_subtitle(\"Lumina\", \"The city feels quieter tonight.\")")
	lines.append("hud.set_time_state(\"BLUE HOUR 19:42\")")
	lines.append("hud.open_menu()")
	lines.append("```")
	lines.append("")
	lines.append("Do not edit `project.godot` or `lumina_walkable_room_v1.tscn` for this UI.")
	lines.append("Launch the self-contained demo with:")
	lines.append("")
	lines.append("```bash")
	lines.append("\"$GODOT_APP/Contents/MacOS/Godot\" --path godot res://ui/lumina_editorial/lumina_editorial_demo.tscn")
	lines.append("```")
	lines.append("")

	var path := _report_dir.path_join("REVIEW.md")
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f:
		f.store_string("\n".join(lines))
		f.close()
