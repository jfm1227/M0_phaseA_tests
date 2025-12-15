contracts_session_timelines_v1.md

# Session Timelines Contracts v1

LLM (M1) / TTS (M2) / Mouth (M3') / Renderer (M0) / BG 合成 (M3.5) のうち、

- Session 全体の口型シーケンス
- Session 全体の顔向き・位置
- Session 全体の表情（emo）

をやり取りするための JSON I/F を定義する。

本書は **「セッション版パイプライン」(DaySession / M0/M3.5 統合フェーズ)** を対象とする。

---

## 1. 共通ルール

### 1.1 グローバル時間軸

- すべてのセッションタイムラインは、同じ時間軸 `t_ms` を共有する。
- 原点は `t_ms = 0` （セッション開始時刻）。
- TTS 側で定義される `audio_ms` が、セッション全体の長さのソース・オブ・トゥルース。

### 1.2 サンプリングステップ

- 原則として `step_ms = 40`（25fps）で固定する。
- 40ms グリッドに対して「値が変化する地点のみ疎に events を出す」設計を基本とする。

### 1.3 JSON 共通ヘッダ

各 Session Timeline は最低限、以下のフィールドを持つ：

```jsonc
{
  "schema_version": "<本書で定義するスキーマ名>",
  "session_id": "sess_real_01",
  "step_ms": 40,
  ...
}
2. Session Mouth Timeline v1.0
2.1 役割
モジュール:

produced by: M3'（DaySession strict2 パイプライン）

consumed by: M0（レンダラー）, チェックツール（check_mouth_and_vad.py など）

内容:

セッション全体の mouth6 シーケンス

発話単位のメタ情報（utt_id / emo_id / start_ms / end_ms）

VAD / audio_ms 等の補助情報

2.2 スキーマ
jsonc
コードをコピーする
{
  "schema_version": "session_mouth_timeline_v1.0",
  "session_id": "sess_real_01",

  // M0 が参照する音声パス（文字列）
  "audio": "session/sess_real_01.wav",

  // 40ms グリッド
  "step_ms": 40,

  // 疎な mouth_id イベント列
  "frames": [
    { "t_ms": 0,    "mouth_id": 0 },
    { "t_ms": 760,  "mouth_id": 5 },
    { "t_ms": 880,  "mouth_id": 2 }
    // ...
  ],

  // 発話単位メタ（LLM/TTS Contracts と接続）
  "utterances": [
    {
      "utt_id": "1_0_72",
      "utt_index": 0,
      "start_ms": 0,
      "end_ms": 36030,
      "audio_path": "in/audio/1_0_72.wav",
      "mouth_path": "out/dayC/attack40/1_0_72.mouth_timeline.attack40.i_v11.vad.json",
      "emo_id": "1_1",
      "llm_chunk_id": 0
    }
    // ...
  ],

  // VAD / 長さメタ（check_mouth_and_vad 互換用）
  "vad_meta": {
    "has_vad": true,
    "step_ms": 40,
    "audio_ms": 93080
  },

  // セッション全体の長さ
  "audio_ms": 93080
}
実例：sess_real_01.mouth_timeline.json はほぼ上記構造になっている。
sess_real_01.mouth_timeline


2.3 mouth_id の定義
mouth_id は 0..5 の int。

共通の Legend:

mouth_id	label	説明
0	close	閉口
1	a	開き広め a 系
2	i	横長 i 系
3	u	すぼみ u 系
4	e	半開 e 系
5	o	丸め o 系

M0 側では内部で ["close","a","i","u","e","o"] に変換して atlas を引く。

2.4 M0 側での扱い
m0_runner.py の mouth 読み込みロジックは、下記をサポートする：

root が配列：[{t_ms, mouth_id, ...}, ...]

root が dict で frames を持つ：{"audio": "...", "frames": [...], ...}

Session 版は後者の形式を採用する。

M0 は audio 文字列を基に音声パスを決定し、Timeline(frames) から各 t_ms で mouth_id を取得する。

2.5 チェックツールとの互換
tests/tools/check_mouth_and_vad.py は、以下のいずれかで VAD 情報を読むようにしておくと安全：

従来形式: meta["vad_meta"]

Session 形式: root["vad_meta"]

本 contracts では Session 形式（root.vad_meta / root.audio_ms）を標準 とし、
チェックツール側で両方読めるように拡張する（後方互換のため）。

3. Session Pose Timeline v1.0
3.1 役割
モジュール:

produced by: DLC→ETL build_session_pose.py

consumed by: M0, M3.5

内容:

セッション全体の顔の向き（yaw/pitch/roll）と位置（scale/tx/ty）

BG クリップとの対応関係（複数クリップ構成を許容）

3.2 スキーマ
jsonc
コードをコピーする
{
  "schema_version": "session_pose_timeline_v1.0",
  "session_id": "sess_real_01",
  "step_ms": 40,

  "frames": [
    {
      "t_ms": 0,
      "yaw_deg": 1.81,
      "pitch_deg": 0.74,
      "roll_deg": 1.78,
      "scale": 0.997,
      "tx": -0.0342,
      "ty": -0.2434,
      "clip_id": "bg_01"
    },
    {
      "t_ms": 40,
      "yaw_deg": 1.68,
      "pitch_deg": 0.88,
      "roll_deg": 1.68,
      "scale": 0.996,
      "tx": -0.0383,
      "ty": -0.2871,
      "clip_id": "bg_01"
    }
    // ...
  ],

  "clips": [
    {
      "clip_id": "bg_01",
      "video_path": "bg/session/bg_01.mp4",
      "pose_source": "pose/1_0_5_test_pose_timeline.json",
      "session_start_ms": 0,
      "session_end_ms": 30000
    }
    // BG が増えれば clip を追加
  ]
}
3.3 M0 側での扱い
既存の m0_runner は pose_timeline を読む際に、

dict で "timeline" を持つ形式

または root 配列
に対応している。

Session 版との整合のため、次の小さな拡張を入れる：

python
コードをコピーする
# 既存: "timeline" / list のチェックに加えて
elif isinstance(raw, dict) and "frames" in raw:
    frames = raw.get("frames") or []
    tmp_path = pose_path.parent / (pose_path.stem + ".frames_only.tmp.json")
    tmp_path.write_text(
        json.dumps(frames, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pose_tl = Timeline.load_json(str(tmp_path))
これにより、

旧形式（{"timeline":[...]} / [...]）は従来通り動作

Session 形式（{"frames":[...]}）も新たにサポートされる。

4. Session Expression Timeline v0.1
4.1 役割
モジュール:

produced by: M3（表情側） or その補助ロジック

consumed by: M0

内容:

表情（目・眉など）のタイムライン

atlas.min.json の expression_labels に準拠したラベルを持つ

4.2 スキーマ
jsonc
コードをコピーする
{
  "schema_version": "session_expression_timeline_v0.1",
  "session_id": "sess_real_01",
  "step_ms": 40,

  "timeline": [
    { "t_ms": 0,    "expression": "normal" },
    { "t_ms": 5000, "expression": "smile"  },
    { "t_ms": 8000, "expression": "angry"  },
    { "t_ms": 14000,"expression": "sad"    },
    { "t_ms": 16000,"expression": "blink"  }
    // ...
  ],

  "meta": {
    "source": {
      "utt_log": "utt_log.sess_real_01.jsonl",
      "driver": "M3_expression_v1"
    }
  }
}
4.3 Atlas との関係
atlas.min.json における expression_labels が 正解ラベルセット：

例: ["normal","smile","angry","sad","blink"]

M3 が出力する expression は、必ずこの集合のいずれかでなければならない。

存在しないラベルが来た場合、M0 側はフォールバック（normal など）に落とす。

4.4 将来の emo_id 拡張
将来的には、各 event に emo_id を追加してもよい：

jsonc
コードをコピーする
{ "t_ms": 5000, "expression": "smile", "emo_id": "1_1" }
この場合、

emo_id は LLM/TTS Contracts で定義される論理 ID

expression は atlas 上の物理スプライトラベル

v0.1 では expression による制御を必須とし、emo_id はオプショナル。

5. M0 Config 例（PhaseC-B → Session 版へのブリッジ）
5.1 抽象形（Contracts に載せるテンプレ）
yaml
コードをコピーする
# Example: M0 config.yaml (PhaseC-B / Session 版の基本形)

inputs:
  mouth_timeline: "timelines/{utt_or_session_id}.mouth_timeline.json"
  pose_timeline:  "timelines/{utt_or_session_id}.pose_timeline.json"
  # expression_timeline: "timelines/{utt_or_session_id}.expression_timeline.json"  # optional

audio:
  wav_path: "audio/{utt_or_session_id}.wav"
PhaseC-B 段階では {utt_id} ベース（例: 1_0_72）で運用。

Session フェーズでは {session_id}（例: sess_real_01）にスイッチ。

5.2 実例（PhaseC_B_1_0_72）
PhaseC-B で実際に使っている config の抜粋は次のようになる（要旨）：

yaml
コードをコピーする
# configs/phaseC_B_1_0_72.yaml

inputs:
  mouth_timeline: "../timelines/1_0_72.mouth_timeline.attack40.i_v11.vad.json"
  pose_timeline:  "../timelines/pose_timeline_yaw.json"

audio:
  wav_path: "../audio/1_0_72.wav"
意味:

1_0_72.mouth_timeline.attack40.i_v11.vad.json は M3' が実際に生成した mouth_timeline（strict2 VAD 済み）

pose_timeline_yaw.json は yaw テスト用の pose_timeline

1_0_72.wav は対応する音声ファイル

5.3 Session 版へのマッピング例
同じ構造を Session に拡張すると：

yaml
コードをコピーする
# Example: M0 config.yaml for Session

inputs:
  mouth_timeline: "timelines/sess_real_01.mouth_timeline.json"
  pose_timeline:  "timelines/sess_real_01.pose_timeline.json"
  # expression_timeline: "timelines/sess_real_01.expression_timeline.json"

audio:
  wav_path: "audio/sess_real_01.wav"
ここで：

sess_real_01.mouth_timeline.json は 2章の Session Mouth Timeline v1.0

sess_real_01.pose_timeline.json は 3章の Session Pose Timeline v1.0

sess_real_01.expression_timeline.json は 4章の Session Expression Timeline v0.1（将来的）


２点の微修正＆追記 です：

Session Pose Timeline v1.0 の仕様を、M3.5の想定にガッチリ合わせる

yaw/pitch/roll だけでなく、bbox / view / metrics まで含めた「正典」にする

M3.5 セッション統合 I/F を contracts 側に正式に生やす

bg_video / pose_timeline / m0_fg.dir などを “Session 合成リクエスト” として定義

前回の回答の設計からの「差分」だけ、整理しつつ書き直します。

1. Session Pose Timeline v1.0 を M3.5 仕様に揃える

前回は pose をかなりミニマルにしていましたが、
M3.5 の正式仕様が「t_ms + yaw/pitch/roll + bbox + (view / metrics …)」 なので、
これをそのまま Session 版の正典 にします。

1-1. 更新版スキーマ（v1.0）
{
  "schema_version": "session_pose_timeline_v1.0",
  "session_id": "sess_real_01",
  "step_ms": 40,

  "frames": [
    {
      "t_ms": 0,          // セッション内絶対時間（0ms 起点）

      // 向き（度数法）
      "yaw": 0.0,         // float
      "pitch": -2.0,      // float
      "roll": 0.0,        // float

      // 顔バウンディングボックス（canvas に対する正規化座標）
      "bbox": {
        "x": 0.41,        // 左上 x [0.0–1.0]
        "y": 0.22,        // 左上 y [0.0–1.0]
        "w": 0.18,        // 幅
        "h": 0.24         // 高さ
      },

      // 以下オプショナル（あれば使う）

      "cx": 0.52,         // 顔中心 x 正規化
      "cy": 0.48,         // 顔中心 y 正規化
      "scale": 1.02,      // 追加スケール
      "conf": 0.93,       // DLC 信頼度など
      "source": "dlc",    // 由来（dlc / hand / etc）
      "frame": 0,         // BG動画 or DLC の元フレーム番号

      // view バケツ（あれば view_offsets で使う）
      "view": "front",    // front / left30 / right30 / up15 / down15

      "metrics": {
        "face_eye_norm": 1.08  // 顔サイズの正規化指標など
      }
    }
  ],

  // BGクリップが複数になる将来用
  "clips": [
    {
      "clip_id": "bg_01",
      "video_path": "sessions/sess_real_01/bg.mp4",
      "pose_source": "pose/1_0_5_test_pose_timeline.json",
      "session_start_ms": 0,
      "session_end_ms": 93080
    }
  ]
}

1-2. 時間軸の定義（セッション vs BG）

contracts v1 では、t_ms は「セッション内絶対時間」として固定 します。

現状 BG が 1 本（bg.mp4）の場合は
BGの 0ms とセッションの 0ms を一致させる ので、実装上は今まで通りでOK。

将来 BG 複数クリップ時は clips[*].session_start_ms / end_ms で対応。

M3.5 側の現在の実装は

t_ms: 0ms 起点（セッション内絶対時間 or BG動画起点）

というゆるい定義でしたが、
「セッション＝BGの0msからスタート」が前提の今の運用なら、そのままセッション絶対時間扱いで問題ない ので、
contracts では一段「きちんと決める」方向に寄せています。

1-3. M0 / M3.5 から見た使用イメージ

M0：

yaw/pitch/roll で view バケツ判定（front/left30/right30/up15/down15）

必要があれば bbox や scale で顔サイズ・位置を補正

M3.5：

bbox で BGRA の貼り付け位置を決める

yaw/pitch/roll / view で view_offsets 計算

metrics.face_eye_norm / scale があればスケール補正に使う

→ つまり、1つの session_pose_timeline.json を M0 と M3.5 が共用する前提です。

2. M0 の fg 出力 & M3.5 入力 I/F の統合

前回の回答では「render_meta.json を提案」していましたが、
M3.5 の正式 I/F が

fg_dir 内の frame_000.png, frame_001.png, … を タイムライン順に 0,1,2,… で消費

という仕様なので、
v1 では render_meta は OPTIONAL（将来用）にして、ディレクトリ＋連番を正典にします。

2-1. M0 → M3.5：FG 出力の標準

M0 の FG 出力（セッション版）標準：

出力ディレクトリ：
sessions/{session_id}/m0_fg/

ファイル名パターン：
frame_%05d.png など（デフォルトは frame_%03d.png でもOK。contracts にはパターンも書いておく）

# 例: M0 config (session 版)
session:
  id: "sess_real_01"

paths:
  fg_dir: "sessions/sess_real_01/m0_fg"
  fg_pattern: "frame_%05d.png"


タイムラインとの対応：

session_pose_timeline.frames の長さ = fg フレーム数

インデックス k の frames[k] が frame_%05d(k) に対応

時間は frames[k].t_ms で決定

M3.5 は pose_timeline の順番で fg_dir のファイルを消費

2-2. M3.5 側の I/F（バッチ）

CLI 正式化：

python -m m3_5.cli \
  --pose_timeline  sessions/sess_real_01/pose_timeline.json \
  --fg_dir         sessions/sess_real_01/m0_fg \
  --bg_video       sessions/sess_real_01/bg.mp4 \
  --out_dir        sessions/sess_real_01/m3_5


--pose_timeline

Session Pose Timeline v1.0（さっき決めたもの）

--fg_dir

M0 が出した BGRA 連番 PNG

--bg_video

セッション用 BG 動画（25fps想定）

--out_dir

合成済み composite.mp4 と log.csv を吐くディレクトリ

出力：

composite.mp4（25fps 合成結果）

composite.log.csv（任意の internal log）

3. 「Session 合成リクエスト」 JSON の整備

M3 / M0 / M3.5 を繋ぐ上で、
「これ1枚あれば M3.5 が走れる」という JSON を定義しておくのが便利そうです。

たとえば session_render_request.json 的なもの：

{
  "schema_version": "session_render_request_v1.0",
  "session_id": "sess_real_01",

  "bg_video": "sessions/sess_real_01/bg.mp4",

  "pose_timeline": "sessions/sess_real_01/pose_timeline.json",

  "m0_fg": {
    "dir": "sessions/sess_real_01/m0_fg",
    "pattern": "frame_%05d.png"
  },

  "m3_5": {
    "out_dir": "sessions/sess_real_01/m3_5",
    "config": "configs/m3_5.default.yaml"
  }
}


contracts 的な意味づけ：

pose_timeline は Session Pose Timeline v1.0

m0_fg.dir は M0 出力（前景 BGRA 列）

bg_video はセッション BG 動画

m3_5.config はブレンド係数や安全マージン等のパラメタ群

将来的には、この JSON を元に

m3_5.cli に渡す CLI を自動生成するスクリプト

Jules / CI での自動検証シナリオ
…などを作れる。

4. いま修正すべきポイント（前回案からの差分）
4-1. Pose スキーマの修正

前回:

yaw_deg / pitch_deg / roll_deg / scale / tx / ty

→ 今回:

yaw / pitch / roll にリネーム（M3.5 に合わせる）

bbox {x,y,w,h} 追加

view / metrics / cx / cy / conf / frame をオプション追加

※ ETL 側（build_session_pose.py）は、当面は最低限：

t_ms

yaw/pitch/roll

bbox.{x,y,w,h}

が入っていれば OK という運用で良いと思います。
view / metrics は後からでも足せる。

4-2. M0 → M3.5 の「render_meta」構想

前回提案した render_meta.json は：

v1 では必須にしない

将来「fg フレームと t_ms のマッピングや、内部 view のログを残したくなった時」に、オプションとして導入する

現時点の正典は：

pose_timeline の順番 = fg frame の順番

frame index k の t_ms は frames[k].t_ms