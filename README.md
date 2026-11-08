# Lumina｜会話と行動をつなぐ3D AIキャラクター

**Lumina（ルミナ）は、人と会話し、3D空間の様子を見て行動するキャラクターを目指す、個人開発のプロジェクトです。** ローカルで動くAIと3D表現を組み合わせ、人と同じ仮想空間で過ごせる相棒を作っています。

会話、周囲の観察、移動や着席をつなぐことが開発の中心です。公開中の試作動画では、会話画面のある3D空間で、ルミナがソファへ移動して座る様子を見ることができます。

[![ソファに座るルミナの試作画面。クリックすると動画の説明ページを開きます](https://hoshikuzukobo42-maker.github.io/lumina-hp/lumina-sky-20261101-v2-poster.webp)](https://hoshikuzukobo42-maker.github.io/lumina-hp/development.html#demo-video)

[試作動画・開発状況を見る](https://hoshikuzukobo42-maker.github.io/lumina-hp/development.html#demo-video) ｜ [公式サイト](https://hoshikuzukobo42-maker.github.io/lumina-hp/) ｜ [技術的な開発状況](docs/STATUS.md) ｜ [お問い合わせ](https://hoshikuzukobo42-maker.github.io/lumina-hp/#contact)

## どのような仕組みを作っているか

| 分野 | 開発していること |
| --- | --- |
| 会話 | 話しかけた内容に応じて、言葉や音声で応答する仕組み |
| 観察 | キャラクターの眼の位置にあるカメラの画像から、周囲の対象を捉える仕組み |
| 行動 | 3D空間の深度や障害物情報を使い、対象への移動や到着確認につなぐ仕組み |
| 制御 | 人からの要求と自律的な行動を調整し、停止や結果の確認につなぐ仕組み |

公開ソースでは、主に **Python・Godot・ローカルのQwen3.5-4B** を使った構成を確認できます。移動には画像だけでなく、Godot側から得る深度・衝突情報も使います。各機能は開発・検証段階であり、任意の指示や環境での動作を保証するものではありません。

> **開発中の試作です。** 一般向けのアプリや体験版は配布していません。このリポジトリは、2026年9月9日までの実装を基にした、9月10日時点の選別したソースコードの公開版です。アバター、部屋、AIモデルなどは含まず、取得後すぐに展示環境を動かせるものではありません。

動画は、公開ソースより後の2026年11月1日に収録した試作です。音声はなく、録画用に１コマずつ記録したため、映像の再生時間は実際の所要時間と一致しません。このリポジトリだけで動画と同じ環境を再現できるわけではありません。

即応性、幅広い指示の理解、安全な割り込み、新しい環境での一連の動作確認などは今後の課題です。9月9日の固定画像を使った測定では、１回の画像推論に約35〜36秒を要しました。[測定条件と限界](docs/PERFORMANCE.md)

## 最初に見るもの

- [現在できていること・残っている課題](docs/STATUS.md)
- [認識・移動・自律行動の構成](docs/ARCHITECTURE.md)
- [実測した推論速度と測定の限界](docs/PERFORMANCE.md)
- [公開コピーの試験手順](docs/TESTING.md)
- [含めたもの・省略したもの](docs/PUBLIC_SCOPE.md)

## 主要なコード

| 領域 | 読み始める場所 |
| --- | --- |
| 画像から物体候補を作る | [visual_perception.py](lumina_next/visual_perception.py) |
| 指示を受けて観察・移動・到着確認を進める | [visual_navigation.py](lumina_next/visual_navigation.py) |
| 深度による位置推定・地図・経路計画 | [portable_navigation_core_v1.py](lumina_next/portable_navigation_core_v1.py) |
| 環境との共通インターフェース | [portable_navigation_contract_v1.py](lumina_next/portable_navigation_contract_v1.py) |
| Godotとの接続 | [godot_portable_navigation_adapter_v1.py](lumina_next/godot_portable_navigation_adapter_v1.py) |
| 眼カメラ・深度センサー | [眼カメラ](godot/scripts/si_lumina_eye_camera.gd)・[深度センサー](godot/scripts/si_portable_depth_sensor.gd) |
| 到着証拠を使った追加推論の省略判定 | [arrival_pose_epoch_fast_path_v1.py](lumina_next/arrival_pose_epoch_fast_path_v1.py) |
| 自律行動と人間の要求の調整 | [autonomy_supervisor.py](lumina_next/autonomy_supervisor.py) |
| 会話・推論・記憶の調整 | [orchestrator.py](lumina_next/orchestrator.py) |
| APIの入口 | [app.py](lumina_next/app.py) |

画像モデルが受け取る情報と、移動制御が使う深度・衝突情報は分けています。ただし、すべてが画像だけで動くわけではありません。Godotでは深度や衝突形状を環境側から取得します。また、コードには旧方式や過去の試験用モジュールも残っています。どの経路が現在の対象なのかは構成説明を参照してください。

## 技術的な到達点（2026年9月時点）

- 単一対象への対応した指示では、画像認識を残したまま不要な文章推論を省く経路を追加しました。
- 自律的な空間行動を、人間の要求と同じ視覚・移動サービスへ接続しました。
- 到着時の証拠が十分か、再確認が必要か、安全上停止すべきかを区別する処理を統合しました。
- 2026年9月9日の本体側関連試験は901件と補助256件が成功しました。別管理の既存8件の失敗があり、全プロジェクトの全試験成功ではありません。
- 同日の固定画像実測では、1回の画像推論におよそ35〜36秒かかりました。即応性を達成したという意味ではありません。

到着高速化と自律運転のフラグは、9月9日の反映確認時点では無効です。コードの配置、運転への投入、完成条件の達成を区別しています。新しい部屋での統合受入試験も残っています。

## 実行と公開範囲

コードはそのまま閲覧できます。一部の合成データによるオフライン試験は [試験手順](docs/TESTING.md) で確認できます。

2026年9月10日に、この公開コピーでも関連24スイートを再実行し、**899件と補助256件が成功、2件省略、失敗なし**を確認しました。省略は未同梱の実機記録と外部ツールに依存する確認です。最小4スイートは163件と補助107件が成功し、省略はありません。

Godotシーン、アバター、部屋素材、モデル重み、音声素材、実運転設定、会話・記憶データは含めていません。したがって、取得直後に展示環境が起動する構成ではありません。既存コード内に残る未同梱ファイルへの参照は、それらを公開したり利用許諾したりするものではありません。

公開するために本体を移動・上書きしたものではなく、別ディレクトリに作成したスナップショットです。抽出元と公開用ソースのハッシュ、保存先パスの匿名化有無は [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json) に記録しています。

## ライセンス

現在、プロジェクト全体のオープンソースライセンスは選定していません。公開閲覧できることと、任意の再利用・再配布を許諾することは別です。[ライセンスに関する注記](LICENSE_STATUS.md) を参照してください。

お仕事や企業利用に関するご相談は、[公式サイトのお問い合わせ案内](https://hoshikuzukobo42-maker.github.io/lumina-hp/#contact)をご覧ください。
