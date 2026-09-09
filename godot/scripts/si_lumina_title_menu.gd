extends CanvasLayer
class_name SILuminaTitleMenu

const EMBODIED_TRIAL_SCENE_PATH := "res://embodied_v1/embodied_interactive_trial.tscn"
const EmbodiedTrialHostScript = preload("res://scripts/embodied_v1/embodied_trial_host.gd")

@export var bridge_base_url: String = "http://127.0.0.1:8765"
@export var player_path: NodePath
@export var control_panel_path: NodePath

var _root: Control
var _content_panel: PanelContainer
var _content_box: VBoxContainer
var _status_label: Label
var _player: Node
var _control_panel: CanvasLayer
var _health_request: HTTPRequest
var _menu_visible := true
var _current_page := ""
var _embodied_trial_host: Node


func _ready() -> void:
	layer = 96
	_resolve_runtime_nodes()
	if _control_panel:
		_set_control_panel_exhibit_mode(false)
	_set_player_input_enabled(false)
	_build_title_screen()
	_setup_health_watch()
	_set_page("home")
	call_deferred("_sync_initial_runtime_nodes")


func _input(event: InputEvent) -> void:
	if _embodied_trial_host != null:
		return
	if _is_text_entry_focused():
		return
	if event is InputEventKey and event.pressed and not event.echo:
		match event.keycode:
			KEY_ESCAPE:
				if _menu_visible:
					_start_exhibit()
				else:
					_show_menu()
				get_viewport().set_input_as_handled()
			KEY_ENTER, KEY_KP_ENTER, KEY_SPACE:
				if _menu_visible:
					_start_exhibit()
				get_viewport().set_input_as_handled()
			KEY_TAB:
				_toggle_operator_panel()
				get_viewport().set_input_as_handled()


func _build_title_screen() -> void:
	_root = Control.new()
	_root.name = "LuminaTitleMenuRoot"
	_root.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(_root)

	var background := ColorRect.new()
	background.name = "Background"
	background.set_anchors_preset(Control.PRESET_FULL_RECT)
	background.color = Color(0.010, 0.014, 0.016, 0.58)
	_root.add_child(background)

	var main := MarginContainer.new()
	main.set_anchors_preset(Control.PRESET_FULL_RECT)
	main.add_theme_constant_override("margin_left", 36)
	main.add_theme_constant_override("margin_top", 34)
	main.add_theme_constant_override("margin_right", 36)
	main.add_theme_constant_override("margin_bottom", 30)
	_root.add_child(main)

	var columns := HBoxContainer.new()
	columns.add_theme_constant_override("separation", 24)
	main.add_child(columns)

	var nav := VBoxContainer.new()
	nav.custom_minimum_size = Vector2(300, 0)
	nav.add_theme_constant_override("separation", 10)
	columns.add_child(nav)

	var title := Label.new()
	title.text = "LUMINA"
	title.add_theme_font_size_override("font_size", 42)
	title.add_theme_color_override("font_color", Color(0.98, 1.0, 0.96))
	nav.add_child(title)

	var subtitle := Label.new()
	subtitle.text = "AI Companion Exhibit"
	subtitle.add_theme_font_size_override("font_size", 16)
	subtitle.add_theme_color_override("font_color", Color(0.78, 0.86, 0.88))
	nav.add_child(subtitle)

	_add_nav_gap(nav, 12)
	_add_menu_button(nav, "展示開始", _start_exhibit, true)
	_add_menu_button(nav, "Embodied試験モード", func() -> void: _set_page("embodied_trial"))
	_add_menu_button(nav, "プロジェクト説明", func() -> void: _set_page("project"))
	_add_menu_button(nav, "操作方法", func() -> void: _set_page("controls"))
	_add_menu_button(nav, "ルミナについて", func() -> void: _set_page("character"))
	_add_menu_button(nav, "設定 / 権利表記", func() -> void: _set_page("settings"))
	_add_menu_button(nav, "操作パネル", _toggle_operator_panel)

	_add_nav_gap(nav, 10)
	_status_label = Label.new()
	_status_label.text = "Enter/Space/Esc: 展示開始 / E: 近づく / /: 話す / R: 正面"
	_status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status_label.add_theme_font_size_override("font_size", 14)
	_status_label.add_theme_color_override("font_color", Color(0.68, 0.72, 0.72))
	nav.add_child(_status_label)

	_content_panel = PanelContainer.new()
	var content_panel := _content_panel
	content_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	content_panel.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	content_panel.custom_minimum_size = Vector2(0, 360)
	var panel_style := StyleBoxFlat.new()
	panel_style.bg_color = Color(0.030, 0.034, 0.034, 0.76)
	panel_style.border_color = Color(0.88, 0.95, 0.90, 0.18)
	panel_style.set_border_width_all(1)
	panel_style.set_corner_radius_all(8)
	content_panel.add_theme_stylebox_override("panel", panel_style)
	columns.add_child(content_panel)

	var content_margin := MarginContainer.new()
	content_margin.add_theme_constant_override("margin_left", 28)
	content_margin.add_theme_constant_override("margin_top", 24)
	content_margin.add_theme_constant_override("margin_right", 28)
	content_margin.add_theme_constant_override("margin_bottom", 24)
	content_panel.add_child(content_margin)

	_content_box = VBoxContainer.new()
	_content_box.add_theme_constant_override("separation", 10)
	content_margin.add_child(_content_box)


func _add_menu_button(parent: Node, text: String, callback: Callable, primary: bool = false) -> void:
	var button := Button.new()
	button.text = text
	button.focus_mode = Control.FOCUS_NONE
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.custom_minimum_size = Vector2(0, 42)
	button.add_theme_font_size_override("font_size", 15)
	if primary:
		var normal := StyleBoxFlat.new()
		normal.bg_color = Color(0.16, 0.42, 0.38, 0.98)
		normal.set_corner_radius_all(8)
		var hover := StyleBoxFlat.new()
		hover.bg_color = Color(0.19, 0.50, 0.45, 1.0)
		hover.set_corner_radius_all(8)
		button.add_theme_stylebox_override("normal", normal)
		button.add_theme_stylebox_override("hover", hover)
		button.add_theme_color_override("font_color", Color(0.96, 1.0, 0.98))
	else:
		var normal := StyleBoxFlat.new()
		normal.bg_color = Color(0.06, 0.075, 0.075, 0.72)
		normal.border_color = Color(0.80, 0.88, 0.84, 0.08)
		normal.set_border_width_all(1)
		normal.set_corner_radius_all(8)
		var hover := StyleBoxFlat.new()
		hover.bg_color = Color(0.10, 0.13, 0.13, 0.86)
		hover.border_color = Color(0.80, 0.88, 0.84, 0.18)
		hover.set_border_width_all(1)
		hover.set_corner_radius_all(8)
		button.add_theme_stylebox_override("normal", normal)
		button.add_theme_stylebox_override("hover", hover)
		button.add_theme_color_override("font_color", Color(0.88, 0.92, 0.90))
	button.pressed.connect(callback)
	parent.add_child(button)


func _add_nav_gap(parent: Node, height: int) -> void:
	var spacer := Control.new()
	spacer.custom_minimum_size = Vector2(0, height)
	parent.add_child(spacer)


func _set_page(page: String) -> void:
	if _content_box == null:
		return
	var normalized_page := page
	if not normalized_page in ["home", "embodied_trial", "project", "controls", "character", "settings"]:
		normalized_page = "home"
	if _current_page == normalized_page:
		return
	_current_page = normalized_page
	for child in _content_box.get_children():
		child.queue_free()
	if _content_panel:
		_content_panel.visible = normalized_page != "home"
	if normalized_page == "home":
		return
	match normalized_page:
		"embodied_trial":
			_add_page_title("Embodied 試験モード")
			_add_body("最新の Aogiri GLB と、身体行動・全文会話・JP-Extra音声を一つの隔離シーンで試します。通常展示はこの画面で明示的に開始するまで変わりません。")
			_add_bullet_list([
				"テキスト返答は省略せずスクロール表示し、音声の全区間完了まで状態を表示します。",
				"歩く・窓辺へ座る・身体表現・発話を、許可済みアンカーとモーションだけで実行します。",
				"元GLBに変形可能な顔リグがないため顔はニュートラル固定です。巨大な目や口になる合成オーバーレイは使用しません。",
				"VMC / OpenXR / Webcam とタッチ入力は既定OFFです。タッチは専用ボタンを押した瞬間だけ有効になります。",
				"Esc または「通常の Lumina へ戻る」で現在の展示シーンへ復帰できます。",
			])
			_add_body("試験 sidecar（localhost:8792）が未起動でも通常展示には影響しません。開始後は画面の「再接続」から復旧できます。")
			_add_menu_button(_content_box, "試験モードを開始", _start_embodied_trial, true)
		"project":
			_add_page_title("プロジェクト")
			_add_body("AIが自分の意思で見て、考え、動き、会話する展示環境です。")
			_add_body("ルミナはGodot内の部屋で、ユーザーとの距離、見えている家具、直近の会話を手がかりに次の行動を選びます。")
			_add_bullet_list([
				"視界: ルミナ側の画面を要約して、返答や行動理由へつなげます。",
				"行動: 見る、うなずく、歩く、座れる家具だけに座る、周囲を見る。",
				"会話: マイクまたはチャット入力をLLMで判断し、TTSで発話します。",
			])
		"controls":
			_add_page_title("操作方法")
			_add_bullet_list([
				"矢印キー / WASD: 移動",
				"右ドラッグ: 視点操作",
				"E: ルミナへ近づいて向き合う",
				"/: チャット入力を開く",
				"R: 正面の一人称視点へ戻る",
				"V または C: 一人称 / 三人称の切り替え",
				"Tab: 操作者用パネルの表示切り替え",
				"Esc: タイトルへ戻る、またはタイトルから展示開始",
			])
			_add_body("来場者はEでルミナの前へ移動し、/で会話入力を開けます。操作に迷ったらRで正面の一人称視点へ戻せます。")
		"character":
			_add_page_title("ルミナ")
			_add_body("ルミナは、展示空間の中でユーザーを見て反応するAIアバターです。固定の案内役ではなく、いま見えているものと会話の流れから次の反応を選びます。")
			_add_bullet_list([
				"名前: ルミナ",
				"役割: 見て、考えて、自然に反応するAIコンパニオン",
				"現在の声: MOSS-TTS Local Transformer 1.7B",
			])
		"settings":
			_add_page_title("設定 / 権利表記")
			_add_license_scroll()
		_:
			return


func _add_page_title(text: String) -> void:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", 26)
	label.add_theme_color_override("font_color", Color(0.94, 0.98, 0.94))
	_content_box.add_child(label)


func _add_body(text: String) -> void:
	var label := Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_font_size_override("font_size", 16)
	label.add_theme_color_override("font_color", Color(0.84, 0.88, 0.86))
	_content_box.add_child(label)


func _add_bullet_list(items: Array[String]) -> void:
	for item in items:
		var label := Label.new()
		label.text = "• " + item
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		label.add_theme_font_size_override("font_size", 15)
		label.add_theme_color_override("font_color", Color(0.78, 0.84, 0.82))
		_content_box.add_child(label)


func _add_license_scroll() -> void:
	var scroll := ScrollContainer.new()
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_content_box.add_child(scroll)

	var text := Label.new()
	text.custom_minimum_size = Vector2(620, 0)
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	text.add_theme_font_size_override("font_size", 13)
	text.add_theme_color_override("font_color", Color(0.80, 0.84, 0.82))
	text.text = _rights_notice_text()
	scroll.add_child(text)


func _rights_notice_text() -> String:
	return "\n".join([
		"権利表記 / LICENSES AND CREDITS",
		"この画面に、本展示で使用しているソフトウェア、素材、音声、モデル、モーション、LLM、プロジェクト内実装の権利情報を記載します。",
		"下へスクロールして、すべての項目を確認できます。",
		"",
		"1. 展示本体 / Project Code",
		"対象: Godotシーン、Python Bridge、UI、会話制御、記憶処理、プロンプト、手続き型モーション補正、家具配置、タイトル画面、操作パネル",
		"権利: 本プロジェクト内実装",
		"備考: 外部ライブラリ、外部モデル、外部素材には下記の個別ライセンスが適用されます。",
		"",
		"2. Godot Engine",
		"対象: Godot Engine",
		"ライセンス: MIT License",
		"公式: https://godotengine.org/license/",
		"要件: Godot Engineを再配布する場合、著作権表示とMITライセンス文を保持する必要があります。",
		"MIT要旨: 使用、複製、変更、結合、公開、配布、サブライセンス、販売が許諾されます。ソフトウェアは無保証です。",
		"",
		"3. VRM addon for Godot",
		"対象: VRMモデルの読み込み、VRMメタデータ、MToon連携",
		"ライセンス: MIT License",
		"権利者表記: V-Sekai Contributors、VRM Consortium、Masataka SUMI、Godot Engine contributors ほか",
		"同梱規約: godot/addons/vrm/LICENSE",
		"要件: 再配布時は著作権表示とMITライセンス文を保持します。",
		"",
		"4. Godot-MToon-Shader",
		"対象: VRM/MToon表示用シェーダー",
		"ライセンス: MIT License",
		"権利者表記: V-Sekai Contributors、Masataka SUMI",
		"同梱規約: godot/addons/Godot-MToon-Shader/LICENSE",
		"要件: 再配布時は著作権表示とMITライセンス文を保持します。",
		"",
		"5. VRMキャラクターモデル",
		"対象: ルミナ用キャラクターVRM、ユーザー表示用VRM",
		"画面表示方針: 展示画面ではVRMファイル名を表示しません。",
		"確認済み条件: commercial use Allow / Redistribution Prohibited / Explicitly Licensed Person",
		"意味: 展示内での使用は許可条件内として扱いますが、VRMファイルそのものの再配布は禁止条件です。",
		"注意: 外部配布、商用公開、別イベント持ち出しの前に、各VRMのメタデータと配布元規約を再確認してください。",
		"",
		"6. Kenney Furniture Kit",
		"対象: テーブル、椅子、ソファ、植物、ランプ、棚、ラグ等の家具GLB素材",
		"ライセンス: Creative Commons CC0 1.0 Universal",
		"配布元: https://kenney.nl/assets/furniture-kit",
		"同梱規約: godot/assets/furniture/kenney/LICENSE_CC0.txt",
		"意味: パブリックドメイン提供。商用/非商用利用、改変、再配布が可能です。",
		"クレジット: 法的には不要ですが、展示の透明性のためKenney Furniture Kitを表記します。",
		"",
		"7. 部屋内の補助形状 / Procedural Room Assets",
		"対象: 壁、床、照明、家具ディテール、当たり判定、NavMesh補助、展示配置コード",
		"権利: 本プロジェクト内実装",
		"備考: 外部家具モデルが読み込めない場合のフォールバック形状も本プロジェクト内実装です。",
		"",
		"8. VRoid Project VRMA Motion Pack",
		"対象: VRMA公式モーション7種の一部。挨拶、Vサイン、回転、モデルポーズ、屈伸等",
		"配布元: https://booth.pm/ja/items/5512385",
		"権利者: ピクシブ株式会社 / VRoidプロジェクト",
		"同梱規約: godot/assets/animation/_sources/vroid_project_vrma_motion_pack/Readme_VRMA_MotionPack_JP.txt",
		"許諾: 禁止事項に違反しない範囲で利用、改変、個人/法人の商用利用が可能。",
		"必須クレジット: キャラクターアニメーション: ピクシブ株式会社 VRoidプロジェクト",
		"主な禁止: モーションまたは改変物を許可なく取り出せる状態で二次配布しない。違法行為、第三者権利侵害、性的/著しい暴力的コンテンツ等に使わない。",
		"",
		"9. fumi2kick VRMA motion pack 01",
		"対象: 挨拶、励まし、ポーズ等の追加VRMAモーション候補",
		"制作者: へすい/rerofumi (@rerofumi)",
		"ライセンス: CC0 / Public Domain",
		"同梱規約: godot/assets/animation/_sources/fumi2kick_motion_pack_01/extracted/README.txt",
		"許諾: 任意用途で利用可能。改変、再配布可能。制作者クレジット不要。",
		"クレジット: 法的には不要ですが、展示の透明性のため制作者名を表記します。",
		"",
		"10. abegen Arm Wave VRMA",
		"対象: 腕振りジェスチャ用VRMA",
		"配布元: https://booth.pm/ko/items/5819446",
		"同梱メモ: godot/assets/animation/_sources/abegen_arm_wave/SOURCE.md",
		"確認済み条件: VrmPosingDesktop/VRoidHub撮影用途のVRMAサンプルとして、報告不要で自由に使用可能と記載。",
		"注意: BOOTHページの最新版条件を確認してください。",
		"",
		"11. 手続き型モーション補正",
		"対象: Idle、Walk、Talk、LookAt、Sit/Stand補助、口パク同期補助、姿勢ガード",
		"権利: 本プロジェクト内実装",
		"備考: 外部モーション不足時に、骨の角度制限とブレンド処理で自然さを補うためのコードです。",
		"",
		"12. MOSS-TTS Local Transformer 1.7B",
		"対象: 現在の本番TTS音声生成モデル",
		"ライセンス: Apache License 2.0",
		"モデル: mlx-community/MOSS-TTS-Local-Transformer-MLX-8bit",
		"上流: OpenMOSS-Team/MOSS-TTS-Local-Transformer",
		"備考: 1.7B系モデルをローカル推論で使用します。MOSS-TTS-Nanoは使用していません。",
		"要件: Apache 2.0のライセンス文、著作権表示、NOTICEがある場合の保持、商標不許諾、無保証等に従います。",
		"",
		"13. MOSS Audio Tokenizer",
		"対象: MOSS-TTSの音声トークナイザー/デコーダ",
		"ライセンス: Apache License 2.0",
		"モデル: appautomaton/openmoss-audio-tokenizer-mlx",
		"備考: MOSS-TTSのWAV生成に使用します。",
		"要件: Apache 2.0のライセンス文、著作権表示、NOTICEがある場合の保持に従います。",
		"",
		"14. MLX / mlx-speech / mlx-audio",
		"対象: macOS上でMOSS-TTSをローカル推論するための実行ライブラリ",
		"ライセンス: 各Pythonパッケージの同梱ライセンスに従います。",
		"備考: 本展示ではMOSS-TTS 1.7Bを動かすため、mlx-speechを包んだローカル互換サーバーを使用します。",
		"",
		"15. Style-Bert-VITS2 / つくよみちゃん系モデル（同梱・検証用）",
		"対象: 旧TTS経路/検証用ファイル。現在の本番TTSでは使用していません。",
		"Style-Bert-VITS2ライセンス: AGPL-3.0 / LGPL-3.0（text/user_dict等）",
		"つくよみちゃん系モデル: 公式規約の最新版とモデルREADMEに従います。",
		"配布元: https://github.com/litagin02/Style-Bert-VITS2",
		"公式規約一覧: https://tyc.rei-yumesaki.net/about/terms/list/",
		"注意: 旧経路を有効化して外部公開/配布する場合は、AGPL義務と音声モデル規約を再確認してください。",
		"",
		"16. Japanese DeBERTa V2 large（同梱・検証用）",
		"対象: Style-Bert-VITS2旧経路で使用する日本語BERT系モデル",
		"ライセンス: CC-BY-SA-4.0",
		"モデルREADME: data/style_bert_vits2/bert/deberta-v2-large-japanese-char-wwm/README.md",
		"備考: 現在の本番TTSでは使用していません。",
		"",
		"17. VOICEVOX / VOICEVOX音声モデル（代替・検証用）",
		"対象: 代替TTS/検証用VOICEVOX関連ファイル。現在の本番TTSでは使用していません。",
		"同梱規約: vendor/voicevox_core_partial/models/TERMS.txt",
		"許諾: 商用・非商用問わず利用可能。アプリケーションに組み込んで再配布可能。",
		"要件: 作成音声を利用する際は各音声ライブラリの規約に従う。VOICEVOXを利用したことが分かるクレジット表記が必要。",
		"現在の本番TTS: MOSS-TTS Local Transformer 1.7B。VOICEVOXは代替/検証用として扱います。",
		"",
		"18. ONNX Runtime関連（VOICEVOX代替経路）",
		"対象: VOICEVOX代替検証で参照するONNX Runtime関連ファイル",
		"同梱規約: vendor/voicevox_core_partial/onnxruntime/TERMS.txt",
		"注意: 代替TTSを実配布に含める場合は、同梱規約と配布形態を再確認してください。",
		"",
		"19. Ollama",
		"対象: ローカルLLM実行環境",
		"利用: ローカルPC上でLLM推論を実行",
		"注意: Ollama本体および読み込むモデルのライセンスに従います。",
		"",
		"20. LLMモデル",
		"対象: 展示会話用ローカルLLM",
		"ベースモデルライセンス: Apache License 2.0（同梱モデルライセンス層で確認）",
		"同梱ライセンス: data/ollama_models/blobs/sha256-7339fa418c9ad3e8e12e74ad0fd26a9cc4be8703f9c110728a992b193be85cb2",
		"要件: Apache 2.0のライセンス文、著作権表示、NOTICEがある場合の保持、商標不許諾、無保証等に従います。",
		"注意: 追加学習・改変済みモデルを外部配布する場合、元モデルと追加学習データの規約確認が必要です。",
		"",
		"21. 会話履歴 / 記憶データ",
		"対象: 来場者との会話履歴、短期記憶、状態要約",
		"権利/管理: 本プロジェクト内データ",
		"注意: 展示運用では個人情報を入力しない、または保存データを展示後に確認/削除してください。",
		"",
		"22. 表示クレジットまとめ",
		"- Godot Engine: MIT License",
		"- VRM addon for Godot: MIT License / V-Sekai Contributors, VRM Consortium, Godot contributors",
		"- Godot-MToon-Shader: MIT License / V-Sekai Contributors, Masataka SUMI",
		"- Kenney Furniture Kit: CC0 1.0 Universal",
		"- キャラクターアニメーション: ピクシブ株式会社 VRoidプロジェクト",
		"- fumi2kick VRMA motion pack 01: CC0 / へすい/rerofumi",
		"- abegen Arm Wave VRMA: BOOTH配布条件に従う",
		"- MOSS-TTS Local Transformer 1.7B: Apache License 2.0 / OpenMOSS-Team / mlx-community conversion",
		"- MOSS Audio Tokenizer: Apache License 2.0",
		"- Style-Bert-VITS2 / つくよみちゃん系モデル: 同梱・検証用。現在の本番TTSでは未使用",
		"- VOICEVOX: 代替/検証用。現在の本番TTSでは未使用",
		"- LLM model: Apache License 2.0",
		"",
		"23. 配布前チェック",
		"- VRMファイルそのものは再配布しない。",
		"- VRMAモーションは、取り出せる状態での二次配布禁止条件があるものを含むため、配布形式を確認する。",
		"- MOSS-TTS関連はApache 2.0の表示義務とNOTICE有無を確認する。",
		"- 旧TTS経路を有効化する場合は、Style-Bert-VITS2のAGPL義務と各音声モデル規約を確認する。",
		"- LLMを外部配布する場合はApache 2.0と追加学習データの出所を確認する。",
		"- ライセンス本文は同梱規約ファイルと公式配布元を正とする。"
	])


func _start_embodied_trial() -> void:
	if OS.get_environment("LUMINA_DISABLE_EMBODIED_TRIAL").strip_edges().to_lower() in ["1", "true", "yes", "on"]:
		if _status_label:
			_status_label.text = "Embodied試験モードは環境キルスイッチで無効です"
		return
	if not ResourceLoader.exists(EMBODIED_TRIAL_SCENE_PATH):
		if _status_label:
			_status_label.text = "Embodied試験シーンが見つかりません（通常展示は変更なし）"
		return
	_resolve_runtime_nodes()
	_set_player_input_enabled(false)
	if _control_panel:
		_set_control_panel_exhibit_mode(false)
	if _status_label:
		_status_label.text = "体験モードを開いています"
	_menu_visible = false
	if _root:
		_root.visible = false
	_embodied_trial_host = EmbodiedTrialHostScript.new()
	_embodied_trial_host.name = "EmbodiedTrialHost"
	_embodied_trial_host.closed.connect(_on_embodied_trial_closed)
	add_child(_embodied_trial_host)


func _on_embodied_trial_closed() -> void:
	_embodied_trial_host = null
	_menu_visible = true
	if _root:
		_root.visible = true
	_set_page("home")
	_set_player_input_enabled(false)
	if _control_panel:
		_set_control_panel_exhibit_mode(false)
	if _status_label:
		_status_label.text = "体験モードを終了しました"


func _start_exhibit() -> void:
	_resolve_runtime_nodes()
	_menu_visible = false
	if _root:
		_root.visible = false
	_set_player_input_enabled(true)
	if _control_panel:
		_set_control_panel_exhibit_mode(true)
	_post_json("/sequence", {
		"label": "title_start",
		"wait_for_completion": false,
		"commands": [
			{"action": "camera_preset", "params": {"preset": "user_pov"}, "reason": "title_screen"},
			{"action": "look_at_user", "reason": "title_screen"},
		],
	}, "展示開始")


func _hide_for_external_demo() -> void:
	_resolve_runtime_nodes()
	_menu_visible = false
	if _root:
		_root.visible = false
	_set_player_input_enabled(true)
	_set_control_panel_exhibit_mode(true)


func _show_menu() -> void:
	_resolve_runtime_nodes()
	_menu_visible = true
	if _root:
		_root.visible = true
	_set_page("home")
	_set_player_input_enabled(false)
	if _control_panel:
		_set_control_panel_exhibit_mode(false)


func _toggle_operator_panel() -> void:
	_resolve_runtime_nodes()
	if _control_panel:
		if _control_panel.has_method("set_exhibit_mode") and not _control_panel.visible:
			_control_panel.call("set_exhibit_mode", true)
		if _control_panel.has_method("set_operator_panel_visible"):
			var next_visible := true
			if _control_panel.has_method("is_operator_panel_visible"):
				next_visible = not bool(_control_panel.call("is_operator_panel_visible"))
			_control_panel.call("set_operator_panel_visible", next_visible)
			_status_label.text = "操作パネル: %s" % ("表示" if next_visible else "非表示")
		else:
			_control_panel.visible = not _control_panel.visible
			_status_label.text = "操作パネル: %s" % ("表示" if _control_panel.visible else "非表示")


func _set_control_panel_exhibit_mode(enabled: bool) -> void:
	_resolve_runtime_nodes()
	if not _control_panel:
		return
	if _control_panel.has_method("set_exhibit_mode"):
		_control_panel.call("set_exhibit_mode", enabled)
	else:
		_control_panel.visible = enabled


func _set_player_input_enabled(enabled: bool) -> void:
	_resolve_runtime_nodes()
	if _player:
		_player.set("input_enabled", enabled)
		if _player.has_method("stop_keyboard_input"):
			_player.call("stop_keyboard_input")


func _is_text_entry_focused() -> bool:
	var viewport := get_viewport()
	if viewport == null:
		return false
	var focus_owner := viewport.gui_get_focus_owner()
	return focus_owner is LineEdit or focus_owner is TextEdit or focus_owner is SpinBox


func _resolve_runtime_nodes() -> void:
	if _player == null and player_path != NodePath():
		_player = get_node_or_null(player_path)
	if _control_panel == null and control_panel_path != NodePath():
		_control_panel = get_node_or_null(control_panel_path) as CanvasLayer


func _sync_initial_runtime_nodes() -> void:
	_resolve_runtime_nodes()
	if _menu_visible:
		_set_player_input_enabled(false)
		if _control_panel:
			_set_control_panel_exhibit_mode(false)


func _post_json(path: String, payload: Dictionary, label: String) -> void:
	var request := HTTPRequest.new()
	request.name = "TitleMenuRequest"
	add_child(request)
	request.request_completed.connect(func(_result: int, response_code: int, _headers: PackedStringArray, _body: PackedByteArray) -> void:
		if _status_label:
			_status_label.text = "%s: %s" % [label, "OK" if response_code >= 200 and response_code < 300 else "失敗"]
		request.queue_free()
	)
	var headers := PackedStringArray(["Content-Type: application/json"])
	var error := request.request(_url(path), headers, HTTPClient.METHOD_POST, JSON.stringify(payload))
	if error != OK and _status_label:
		_status_label.text = "%s: request failed" % label


func _setup_health_watch() -> void:
	_health_request = HTTPRequest.new()
	_health_request.name = "TitleMenuHealthRequest"
	add_child(_health_request)
	_health_request.request_completed.connect(_on_health_request_completed)

	var timer := Timer.new()
	timer.wait_time = 1.0
	timer.autostart = true
	timer.timeout.connect(_poll_health)
	add_child(timer)


func _poll_health() -> void:
	if not _menu_visible:
		return
	if not is_instance_valid(_health_request):
		return
	if _health_request.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		return
	_health_request.request(_url("/health"))


func _on_health_request_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if result != HTTPRequest.RESULT_SUCCESS or response_code < 200 or response_code >= 300:
		return
	var parsed: Variant = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		return
	var payload := parsed as Dictionary
	var sequences := payload.get("sequences", {}) as Dictionary
	var labels: Array[String] = []
	var active_items: Variant = sequences.get("active", [])
	if typeof(active_items) == TYPE_ARRAY:
		for item in active_items:
			if typeof(item) == TYPE_DICTIONARY:
				labels.append(String(item.get("label", "")))
	for label in labels:
		if label in ["final_demo", "motion_qa", "autonomy_showcase", "furniture_showcase", "presence_cycle"]:
			_hide_for_external_demo()
			return


func _url(path: String) -> String:
	var base := bridge_base_url
	if base.ends_with("/"):
		base = base.substr(0, base.length() - 1)
	return base + path
