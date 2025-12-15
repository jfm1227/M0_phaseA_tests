from __future__ import annotations
import argparse, os, json, shutil, time, subprocess
from typing import Dict, Any
from pathlib import Path

import yaml
import csv

# -----------------------------
# 基本ユーティリティ
# -----------------------------
def load_yaml(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    txt = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(txt) or {}
    # JSONも許容
    return json.loads(txt)

def deep_update(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base

def _safe_get_float(d: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in d:
            try:
                return float(d[k])
            except Exception:
                pass
    return float(default)

# -----------------------------
# エイリアス（フォルダ）作成
# -----------------------------
def _mk_tmp_assets_with_alias(src_assets: Path, exp_dir: Path, alias: Dict[str,str]) -> Path:
    """
    assets_dir配下に view名の別名（エイリアス）を用意する。
    例: {"left30": "down15", "right30": "up15"} → tmp_assets/left30 -> tmp_assets/down15
    symlinkが使えない環境ではコピーにフォールバック。
    """
    tmp = exp_dir / "tmp_assets"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src_assets, tmp, dirs_exist_ok=True)

    def link_or_copy(src: Path, dst: Path):
        if dst.exists():  # 既にあるなら触らない
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            rel = os.path.relpath(src, dst.parent)
            os.symlink(rel, dst)
        except Exception:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    # 片方向（dst→src）で作る
    for dst_name, src_name in alias.items():
        src_path = tmp / src_name
        dst_path = tmp / dst_name
        if src_path.exists():
            link_or_copy(src_path, dst_path)

    return tmp

# -----------------------------
# atlas 深度置換
# -----------------------------
def _json_deep_replace(obj, replace_map: Dict[str, str]):
    from collections.abc import Mapping, Sequence
    if isinstance(obj, Mapping):
        return {k: _json_deep_replace(v, replace_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_deep_replace(v, replace_map) for v in obj]
    if isinstance(obj, str):
        s = obj
        for old, new in replace_map.items():
            s = s.replace(old, new)
        return s
    return obj

def _rewrite_atlas_for_alias(base_atlas_path: Path, tmp_assets_dir: Path, view_alias: Dict[str, str]) -> Path:
    """
    atlas.min.json 内の全パス文字列に対し、view_aliasに基づく置換を施した
    「別名対応版atlas」を生成して返す。
    - 例: {"left30":"down15"} → "/left30/" を "/down15/" に
    """
    # 置換ルール作成（両方の表記に対応）
    # "/left30/" → "/down15/"、 "left30/" → "down15/"
    pairs = {}
    for dst, src in view_alias.items():
        pairs[f"/{dst}/"] = f"/{src}/"
        pairs[f"{dst}/"]  = f"{src}/"

    text = base_atlas_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        new_data = _json_deep_replace(data, pairs)
        out = tmp_assets_dir / "atlas.alias.json"
        out.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    except Exception:
        # JSONでないor読めない場合でも、単純置換でフォールバック
        for old, new in pairs.items():
            text = text.replace(old, new)
        out = tmp_assets_dir / "atlas.alias.json"
        out.write_text(text, encoding="utf-8")
        return out

# -----------------------------
# Timeline／レンダラー読み込み
# -----------------------------


def _import_timeline_and_render():
    """
    Timeline / render_video の import を環境に応じて切り替える。
    - パターンA: プロジェクト直下に src/ がある場合 → from src.timeline ...
    - パターンB: vendor/src/ 直下に m0_runner.py, timeline.py がある場合 → ローカルimport
    """
    try:
        # もともとの想定（プロジェクト root から python -m src.m0_runner の場合）
        from src.timeline import Timeline
        from src.render_core import render_video
    except ModuleNotFoundError:
        # 今回のように vendor/src/m0_runner.py を直接叩いた場合はこちら
        from timeline import Timeline
        from vendor.src.render_core import render_video_legacy as render_video
    return Timeline, render_video



# -----------------------------
# 値マージ・軸適用
# -----------------------------
def _build_merged_value_fn(mouth_tl, pose_tl, expr_tl,
                           value_key: str, thr_front: float, map_deg: float):
    """
    value_key が yaw 以外（pitch/roll）の場合、擬似yaw（±map_deg or 0）を注入して返す。

    ※M3' からは mouth_id(0..5) が来る想定なので、
      ここで mouth ラベル("close/a/i/u/e/o") に変換して M0 に渡す。
    """

    # 口ID→ラベル変換テーブル（M3'/contracts と揃える）
    MOUTH_LABELS = ["close", "a", "i", "u", "e", "o"]

    def merged_value(t_ms: int) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        vals.update(mouth_tl.value_at(t_ms))
        vals.update(pose_tl.value_at(t_ms))
        vals.update(expr_tl.value_at(t_ms))

        # --- mouth_id -> mouth ラベル変換 ---
        if "mouth_id" in vals and "mouth" not in vals:
            try:
                mid = int(vals["mouth_id"])
            except Exception:
                mid = 0
            if 0 <= mid < len(MOUTH_LABELS):
                vals["mouth"] = MOUTH_LABELS[mid]
            else:
                vals["mouth"] = "close"

        # --- ここから下は従来どおり（擬似yaw注入） ---
        if value_key == "yaw":
            return vals

        v = None
        if value_key == "pitch":
            v = _safe_get_float(vals, "pitch_deg", "pitch", default=None)
        elif value_key == "roll":
            v = _safe_get_float(vals, "roll_deg", "roll", default=None)

        if v is None:
            pseudo = 0.0
        else:
            pseudo = 0.0 if abs(v) <= thr_front else (map_deg if v > 0 else -map_deg)

        vals["yaw"] = pseudo
        vals["yaw_deg"] = pseudo
        return vals

    return merged_value


# -----------------------------
# audio パス解決ヘルパ
# -----------------------------
def _resolve_audio_path(audio_name: str | None,
                        cfg: Dict[str, Any],
                        assets_dir: Path,
                        mouth_path: Path,
                        config_path: Path) -> Path | None:
    """
    audio_name（例: "1_0_2.wav" や "audio/1_0_2.wav"）を
    - configファイルと同じディレクトリ
    - mouth_timeline.json と同じディレクトリ
    - assets_dir 配下
    の順で探索して、見つかれば絶対パスを返す。
    """
    if not audio_name:
        return None

    cand = Path(audio_name)
    if cand.is_absolute() and cand.exists():
        return cand

    for base in [config_path.parent, mouth_path.parent, assets_dir]:
        p = base / audio_name
        if p.exists():
            return p

    return None

# -----------------------------
# メイン
# -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)          # JSON or YAML
    ap.add_argument("--override", action="append", default=[])
    args = ap.parse_args()

    # 設定ロード
    cfg_path = Path(args.config).resolve()
    cfg = load_yaml(cfg_path)
    for o in args.override:
        cfg = deep_update(cfg, load_yaml(o))

    assets_dir = Path(cfg["io"]["assets_dir"]).resolve()
    out_dir    = Path(cfg["io"]["out_dir"]).resolve()
    exp_name   = cfg["io"]["exp_name"]

    width       = int(cfg["video"]["width"])
    height      = int(cfg["video"]["height"])
    fps         = int(cfg["video"]["fps"])
    duration_s  = int(cfg["video"]["duration_s"])
    crossfade   = int(cfg["render"]["crossfade_frames"])

    # メトリクス・切替設定（Option A：alias＋atlas書き換え）
    mconf       = cfg.get("metrics", {}) or {}
    value_key   = str(mconf.get("value_key", "yaw"))     # "yaw" / "pitch" / "roll"
    thr_front   = float(mconf.get("thr_front", 16.0))    # ±閾値[deg]
    zero_label  = str(mconf.get("zero_label", "front"))  # ラベル（ログ用）
    neg_label   = str(mconf.get("neg_label",  "left30"))
    pos_label   = str(mconf.get("pos_label",  "right30"))
    map_deg     = float(mconf.get("map_deg", 30.0))      # 擬似yawの±度数
    view_alias  = dict(mconf.get("view_alias", {}))      # {"left30":"down15","right30":"up15",...}

    # transform 設定（render_core へ透過）
    transform_cfg = cfg.get("transform")  # そのまま渡す（enabled: False なら render_core 側でno-op）

    # pitch/roll で alias 未指定なら、一般的な既定を補完
    if value_key != "yaw" and not view_alias:
        if value_key == "pitch":
            view_alias = {"left30": "down15", "right30": "up15", "front": "front"}
        else:
            view_alias = {"front": "front", "left30": "left30", "right30": "right30"}

    # パス解決ヘルパ
    def _abs_assets(p: str) -> str:
        return p if os.path.isabs(p) else str(assets_dir / p)

    # タイムライン読み込み
    Timeline, render_video = _import_timeline_and_render()
    inputs = cfg.get("inputs", {})


    mouth_path = None
    mouth_tl = Timeline([])  # デフォルト空タイムライン
    audio_name_from_mouth = None

    if "mouth_timeline" in inputs:
        mouth_path = Path(_abs_assets(inputs["mouth_timeline"]))

        # JSON を一度読み込んで形式を判定
        raw_txt = mouth_path.read_text(encoding="utf-8")
        raw = json.loads(raw_txt)

        # audio 名は dict 形式のときだけ拾う
        if isinstance(raw, dict):
            audio_name_from_mouth = raw.get("audio")

        # ルートが dict で "frames" キーを持つ M3'形式:
        #   { "audio": "...", "step_ms": 40, "frames": [ {...}, ... ] }
        # の場合は frames 部分だけを抜き出して Timeline に渡す
        if isinstance(raw, dict) and "frames" in raw:
            frames = raw.get("frames") or []
            # Timeline.load_json はファイルパス前提なので、一時ファイルに書き出してから読む
            tmp_path = mouth_path.parent / (mouth_path.stem + ".frames_only.tmp.json")
            tmp_path.write_text(
                json.dumps(frames, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            mouth_tl = Timeline.load_json(str(tmp_path))

        # ルートがすでに配列（従来形式）の場合は、そのまま Timeline.load_json に渡す
        elif isinstance(raw, list):
            mouth_tl = Timeline.load_json(str(mouth_path))

        else:
            # 想定外形式の場合はいったん空のまま（ログで気付けるようにしておくと尚良し）
            mouth_tl = Timeline([])


        # pose_timeline 読み込み（dummy/ETL両対応）
    if "pose_timeline" in inputs:
        pose_path = Path(_abs_assets(inputs["pose_timeline"]))
        raw_txt = pose_path.read_text(encoding="utf-8")
        raw = json.loads(raw_txt)

        if isinstance(raw, dict) and "timeline" in raw:
            # pose_timeline_yaw.json 形式:
            # { "meta": ..., "timeline": [ {...}, ... ] }
            frames = raw.get("timeline") or []
            tmp_path = pose_path.parent / (pose_path.stem + ".timeline_only.tmp.json")
            tmp_path.write_text(
                json.dumps(frames, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            pose_tl = Timeline.load_json(str(tmp_path))

        elif isinstance(raw, dict) and "frames" in raw:
            # ★ セッション版 pose_timeline 形式:
            # { "schema_version": ..., "session_id": ..., "frames": [ {...}, ... ] }
            frames = raw.get("frames") or []
            tmp_path = pose_path.parent / (pose_path.stem + ".frames_only.tmp.json")
            tmp_path.write_text(
                json.dumps(frames, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            pose_tl = Timeline.load_json(str(tmp_path))
            

        elif isinstance(raw, list):
            # 旧ETL形式：ルートがすでに配列
            pose_tl = Timeline.load_json(str(pose_path))

        else:
            # 想定外 → 空タイムラインでフォールバック
            pose_tl = Timeline([])
    else:
        pose_tl = Timeline([])

    # expression_timeline は今は使っていないが、将来 dict 形式になるかもしれないので
    if "expression_timeline" in inputs:
        expr_path = Path(_abs_assets(inputs["expression_timeline"]))
        raw_txt = expr_path.read_text(encoding="utf-8")
        raw = json.loads(raw_txt)

        if isinstance(raw, dict) and "timeline" in raw:
            frames = raw.get("timeline") or []
            tmp_path = expr_path.parent / (expr_path.stem + ".timeline_only.tmp.json")
            tmp_path.write_text(
                json.dumps(frames, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            expr_tl = Timeline.load_json(str(tmp_path))
        elif isinstance(raw, list):
            expr_tl = Timeline.load_json(str(expr_path))
        else:
            expr_tl = Timeline([])
    else:
        expr_tl = Timeline([])



    # audio 設定：config > mouth_timeline.json 内の "audio"
    audio_cfg = cfg.get("audio", {}) or {}
    audio_name_cfg = audio_cfg.get("wav_path")  # 例: "in/audio/1_0_2.wav" など
    audio_name = audio_name_cfg or audio_name_from_mouth
    audio_path = None
    if audio_name and mouth_path is not None:
        audio_path = _resolve_audio_path(audio_name, cfg, assets_dir, mouth_path, cfg_path)

    # 出力先
    exp_dir  = out_dir / exp_name
    exp_dir.mkdir(parents=True, exist_ok=True)
    raw_mp4  = exp_dir / "video_raw.mp4"   # レンダリング直後の無音動画
    final_mp4 = exp_dir / "demo.mp4"       # audio mux 後の最終動画

    # assets の有効ディレクトリ（alias適用）
    use_assets_dir = assets_dir
    if value_key != "yaw" and view_alias:
        use_assets_dir = _mk_tmp_assets_with_alias(assets_dir, exp_dir, view_alias)

    # atlas の有効パス（alias適用で深度置換）
    atlas_json_rel = cfg.get("atlas", {}).get("atlas_json", None)
    atlas_json_for_render = atlas_json_rel
    if atlas_json_rel and (value_key != "yaw") and view_alias:
        base_atlas = Path(atlas_json_rel)
        if not base_atlas.is_absolute():
            base_atlas = use_assets_dir / atlas_json_rel
        if base_atlas.exists():
            atlas_json_for_render = str(_rewrite_atlas_for_alias(base_atlas, use_assets_dir, view_alias))

# ★追加：aliasが無い通常ケースでも assets_dir を付ける
    if atlas_json_for_render:
        p = Path(atlas_json_for_render)
        if not p.is_absolute():
            atlas_json_for_render = str(use_assets_dir / atlas_json_for_render)


    # 値マージ関数（擬似yaw注入）
    merged_value = _build_merged_value_fn(mouth_tl, pose_tl, expr_tl,
                                          value_key=value_key, thr_front=thr_front, map_deg=map_deg)
    
        # -----------------------------
    # デバッグ: timeline CSV のダンプ
    # -----------------------------
    debug_cfg = cfg.get("debug", {})
    dump_csv_rel = debug_cfg.get("dump_timeline_csv")

    if dump_csv_rel:
        # out_dir/exp_name 配下に CSV を作る
        debug_root = out_dir / exp_name
        debug_path = debug_root / dump_csv_rel
        debug_path.parent.mkdir(parents=True, exist_ok=True)

        total_frames = fps * duration_s
        fieldnames = [
            "frame_index",
            "t_ms",
            "mouth",
            "mouth_id",
            "yaw",
            "yaw_deg",
            "pitch_deg",
            "roll_deg",
        ]

        with debug_path.open("w", newline="", encoding="utf-8") as f_csv:
            writer = csv.DictWriter(f_csv, fieldnames=fieldnames)
            writer.writeheader()

            for fi in range(total_frames):
                # M0内部で使っているのと同じ t_ms（1フレーム=1000/fps ms）
                t_ms = int(round(fi * 1000 / fps))
                vals = merged_value(t_ms)

                writer.writerow({
                    "frame_index": fi,
                    "t_ms": t_ms,
                    "mouth": vals.get("mouth"),
                    "mouth_id": vals.get("mouth_id"),
                    "yaw": vals.get("yaw"),
                    "yaw_deg": vals.get("yaw_deg"),
                    "pitch_deg": vals.get("pitch_deg"),
                    "roll_deg": vals.get("roll_deg"),
                })



    # -----------------------------
    # レンダリング本体
    # -----------------------------
    t0 = time.time()
    stats = render_video(
        str(raw_mp4),
        width, height, fps, duration_s, crossfade,
        merged_value,
        assets_dir=str(use_assets_dir),
        atlas_json_rel=atlas_json_for_render,
        transform_cfg=transform_cfg,
    )
    render_elapsed = round(time.time() - t0, 3)

    # -----------------------------
    # audio mux（ffmpeg）
    # -----------------------------
    mux_succeeded = False
    mux_error = None

    if audio_path and audio_path.exists():
        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", str(raw_mp4),
                "-i", str(audio_path),
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                str(final_mp4),
            ]
            # ログは捨てる（必要なら DEVNULL を外す）
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            mux_succeeded = True
        except Exception as e:
            mux_error = str(e)

    # audio が無い / mux 失敗 → 無音動画として demo.mp4 に移動
    if not mux_succeeded:
        try:
            shutil.move(str(raw_mp4), str(final_mp4))
        except Exception:
            # move失敗時は raw_mp4 が残るだけ（ログで判別）
            pass

    total_elapsed = round(time.time() - t0, 3)

    # -----------------------------
    # ログ
    # -----------------------------
    run_log = {
        "out_mp4": str(final_mp4),
        "raw_mp4": str(raw_mp4),
        "fps": fps,
        "duration_s": duration_s,
        "frames": int(duration_s * fps),
        "assets_dir": str(assets_dir),
        "assets_dir_effective": str(use_assets_dir),
        "exp_name": exp_name,
        "elapsed_s_render": render_elapsed,
        "elapsed_s_total": total_elapsed,
        "axis": value_key,
        "thr_front_deg": thr_front,
        "map_deg": map_deg,
        "labels": {"zero": zero_label, "neg": neg_label, "pos": pos_label},
        "view_alias": view_alias,
        "audio_name_cfg": audio_name_cfg,
        "audio_name_from_mouth": audio_name_from_mouth,
        "audio_path": str(audio_path) if audio_path else None,
        "audio_mux_succeeded": mux_succeeded,
        "audio_mux_error": mux_error,
    }
    if stats:
        run_log.update(stats)

    (exp_dir / "run.log.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")

    # summary.csv（簡易）
    summary_keys = ["exp_name", "duration_s", "elapsed_s_render", "elapsed_s_total", "fallback_frames", "first_fallback_ms"]
    with (exp_dir / "summary.csv").open("w", encoding="utf-8") as f:
        f.write("key,value\n")
        for k in summary_keys:
            if k in run_log:
                f.write(f"{k},{run_log[k]}\n")
        views = run_log.get("views", {})
        for name, count in views.items():
            f.write(f"views_{name},{count}\n")

if __name__ == "__main__":
    main()
