#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_mouth_and_vad.py

- 検査①: mouth_timeline.json vs M0 debug CSV (timeline.csv) の整合性チェック
- 検査②: VAD vs mouth_open の整合性チェック + オフセット推定

使い方(例):
  python tools/check_mouth_and_vad.py \
      --mouth-json tests/timelines/1_0_72.mouth_timeline.i_test_v11.vad_aligned.json \
      --debug-csv tests/out/phaseC_B_1_0_72/debug/phaseC_B_1_0_72.timeline.csv \
      --check-mouth-vs-m0 \
      --check-vad \
      --max-lag-ms 800
"""

import argparse
import json
import csv
import math
from pathlib import Path
from collections import Counter
from typing import List, Dict, Any, Tuple, Optional


# -----------------------------
# 共通ユーティリティ
# -----------------------------
def load_mouth_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # frames がトップにあるケースと、トップがすでに list のケース両対応
    if isinstance(data, list):
        frames = data
        meta = {}
    else:
        frames = data.get("frames", [])
        meta = {k: v for k, v in data.items() if k != "frames"}
    if not frames:
        raise ValueError(f"mouth_json に frames が見つかりません: {path}")
    frames = sorted(frames, key=lambda x: x["t_ms"])
    return {"frames": frames, "meta": meta}


def build_piecewise_value_fn(
    frames: List[Dict[str, Any]],
    key: str,
):
    """
    M0 の Timeline.value_at と同じイメージ:
    - t_ms が昇順に並んだ frames を受け取り、
    - 任意の t_ms に対して「t <= t_ms の中で最後の値」を返す
    """
    times = [int(f["t_ms"]) for f in frames]
    vals = [f[key] for f in frames]
    if len(times) != len(vals):
        raise ValueError("times/vals の長さが不一致です")

    def value_at(t_ms: int):
        # 二分探索でもよいが、長さがそこまで大きくない前提で線形でもOK
        # ここでは分かりやすく線形検索＋早期breakにする
        last_val = vals[0]
        for tm, v in zip(times, vals):
            if tm > t_ms:
                break
            last_val = v
        return last_val

    return value_at


def load_debug_csv(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # 必須カラムチェック
            if "t_ms" not in r or "mouth_id" not in r:
                raise ValueError(f"debug CSV に t_ms / mouth_id がありません: {path}")
            # 型変換
            r["t_ms"] = int(r["t_ms"])
            r["mouth_id"] = int(r["mouth_id"]) if r["mouth_id"] != "" else None
            rows.append(r)
    if not rows:
        raise ValueError(f"debug CSV が空です: {path}")
    return rows


# -----------------------------
# 検査①: mouth_json vs M0 debug CSV
# -----------------------------
def check_mouth_vs_m0(
    mouth_json_path: Path,
    debug_csv_path: Path,
    tolerance_ms: int = 0,
) -> None:
    """
    - mouth_timeline.json の piecewise 定義と、
      M0 debug CSV (frameごとの mouth_id) の一致をチェック。
    - tolerance_ms > 0 の場合は、近傍フレームを許容するモードも追加可能（ここではまずは0）。
    """
    mj = load_mouth_json(mouth_json_path)
    frames = mj["frames"]
    meta = mj["meta"]
    mouth_value_at = build_piecewise_value_fn(frames, key="mouth_id")

    debug_rows = load_debug_csv(debug_csv_path)

    total = len(debug_rows)
    mismatch = 0
    counter_pairs = Counter()

    for row in debug_rows:
        t_ms = row["t_ms"]
        m0_mouth_id = row["mouth_id"]
        expected = mouth_value_at(t_ms)

        if m0_mouth_id != expected:
            mismatch += 1
            counter_pairs[(expected, m0_mouth_id)] += 1

    print("===== 検査①: mouth_timeline.json vs M0 debug CSV =====")
    print(f"- mouth_json: {mouth_json_path}")
    print(f"- debug_csv : {debug_csv_path}")
    print(f"- frame 数  : {total}")
    print(f"- 不一致数  : {mismatch} ({mismatch/total*100:.3f}%)")

    if mismatch == 0:
        print("→ ✅ すべてのフレームで mouth_id は mouth_timeline.json と一致しています。")
    else:
        print("→ ⚠ 一部フレームで mouth_id の不一致があります。上位の組み合わせ:")
        for (exp_id, m0_id), cnt in counter_pairs.most_common(10):
            print(f"  - expected={exp_id}, m0={m0_id}: {cnt} frames")

    # メタ情報も軽く表示
    audio_ms = meta.get("audio_ms")
    if audio_ms is not None:
        print(f"- audio_ms (meta): {audio_ms} ms ({audio_ms/1000:.3f} sec)")
    print()


# -----------------------------
# 検査②: VAD vs mouth_open + オフセット推定
# -----------------------------
def extract_vad_series(mouth_data: Dict[str, Any]) -> Tuple[List[int], int]:
    """
    mouth_json 内の VAD 情報を 0/1 シリーズとして取り出す。

    期待する構造（いずれか）:

    - セッション形式（今回の標準）:
        {
          "frames": [...],
          "meta": {
            "vad_meta": {
              "step_ms": 40,
              "mask": [... 0/1 ...]
            },
            "audio_ms": 93080,
            ...
          }
        }

    - 旧形式:
        {
          "frames": [...],
          "meta": {
            "vad_mask": [...],
            "vad": [...],
            ...
          }
        }

    将来の拡張で root["vad_meta"] を持つ mouth_json にも対応できるよう、
    必要に応じて mouth_data["vad_meta"] も見る。
    """
    meta = mouth_data.get("meta", {}) or {}

    # まず meta["vad_meta"] を見る（セッション形式／推奨）
    vad_meta = meta.get("vad_meta", {}) or {}
    step_ms = int(vad_meta.get("step_ms", 40))

    vad_series: Optional[List[int]] = None

    # 1) vad_meta.mask / vad_meta.vad
    if isinstance(vad_meta, dict):
        for key in ("mask", "vad"):
            if key in vad_meta:
                vad_series = vad_meta[key]
                break

    # 2) meta.vad_mask / meta.vad（旧形式）
    if vad_series is None:
        for key in ("vad_mask", "vad"):
            if key in meta:
                vad_series = meta[key]
                break

    # 3) 念のため mouth_data["vad_meta"] も見る（将来の互換用）
    if vad_series is None and "vad_meta" in mouth_data:
        root_vad_meta = mouth_data["vad_meta"]
        if isinstance(root_vad_meta, dict):
            for key in ("mask", "vad"):
                if key in root_vad_meta:
                    vad_series = root_vad_meta[key]
                    break

    if vad_series is None:
        raise ValueError(
            "mouth_json 内に VAD マスクが見つかりませんでした。"
            "extract_vad_series() 内の候補キーを mouth_json の構造に合わせて調整してください。"
        )

    # 0/1 の int シリーズに正規化
    vad_01 = [1 if bool(x) else 0 for x in vad_series]
    return vad_01, step_ms




def align_length(a: List[int], b: List[int]) -> Tuple[List[int], List[int]]:
    n = min(len(a), len(b))
    return a[:n], b[:n]


def cross_correlation_lag(a: List[int], b: List[int], max_lag_steps: int) -> Tuple[int, float]:
    """
    0/1 シリーズ a, b の間で、-max_lag_steps〜+max_lag_steps の範囲で
    相互相関が最大となるラグを探索する。
    戻り値: (best_lag_steps, best_score)
    - lag > 0 の意味: b が a より "lag ステップ遅れている" と見るか、
      あるいは a を未来にシフトするか…は後で解釈する。
    """
    if len(a) != len(b):
        raise ValueError("cross_correlation_lag: 長さが一致していません。")

    best_lag = 0
    best_score = -1.0

    n = len(a)
    for lag in range(-max_lag_steps, max_lag_steps + 1):
        score = 0.0
        for i in range(n):
            j = i + lag
            if j < 0 or j >= n:
                continue
            score += a[i] * b[j]
        if score > best_score:
            best_score = score
            best_lag = lag

    return best_lag, best_score


def check_vad_vs_mouth(
    mouth_json_path: Path,
    max_lag_ms: int = 800,
) -> None:
    """
    - mouth_json 内の VAD (voice_active) と mouth_open を比較。
    - FN = audio あり & mouth_close
    - FP = audio なし & mouth_open
    - cross-correlation で最適ラグを推定。
    """
    md = load_mouth_json(mouth_json_path)
    frames = md["frames"]
    meta = md["meta"]

    vad_01, step_ms = extract_vad_series(md)
    audio_ms = int(meta.get("audio_ms", step_ms * (len(vad_01) - 1)))

    mouth_open = build_mouth_open_series(frames, step_ms=step_ms, audio_ms=audio_ms)
    voice_active = vad_01

    mouth_open, voice_active = align_length(mouth_open, voice_active)
    n = len(mouth_open)

    # コンティンジェンシテーブル
    tp = tn = fp = fn = 0  # tp: audioあり&mouth開いてる
    for m, v in zip(mouth_open, voice_active):
        if v == 1 and m == 1:
            tp += 1
        elif v == 1 and m == 0:
            fn += 1  # audioあり mouth閉じ
        elif v == 0 and m == 1:
            fp += 1  # audioなし mouth開き
        elif v == 0 and m == 0:
            tn += 1

    print("===== 検査②: VAD vs mouth_open =====")
    print(f"- mouth_json: {mouth_json_path}")
    print(f"- step_ms   : {step_ms} ms")
    print(f"- audio_ms  : {audio_ms} ms ({audio_ms/1000:.3f} sec)")
    print(f"- steps     : {n}")
    print()
    print("コンティンジェンシ (voice_active / mouth_open):")
    print(f"  TP (1,1) audioあり&口開き   : {tp}")
    print(f"  FN (1,0) audioあり&口閉じ   : {fn}")
    print(f"  FP (0,1) audioなし&口開き   : {fp}")
    print(f"  TN (0,0) audioなし&口閉じ   : {tn}")
    if n > 0:
        print(f"  → FN率 (audioあり中で閉じてる割合): {fn / max(tp+fn,1):.3f}")
        print(f"  → FP率 (無音中で開いてる割合)    : {fp / max(fp+tn,1):.3f}")
    print()

    # オフセット推定
    max_lag_steps = max(1, int(round(max_lag_ms / step_ms)))
    lag_steps, score = cross_correlation_lag(voice_active, mouth_open, max_lag_steps)

    lag_ms = lag_steps * step_ms
    print("オフセット推定 (voice_active vs mouth_open の相互相関最大ラグ):")
    print(f"  - 探索範囲   : ±{max_lag_ms} ms (±{max_lag_steps} steps)")
    print(f"  - best_lag   : {lag_steps} steps ({lag_ms} ms)")
    print(f"  - best_score : {score}")
    print()
    print("ラグの解釈:")
    print("  ここでは voice_active[t] と mouth_open[t+lag] の積を最大化するラグを探索しています。")
    print("  → lag_ms > 0 : mouth_open が VAD（audio）より遅れている傾向。")
    print("  → lag_ms < 0 : mouth_open が audio より先行している傾向。")
    print()


# -----------------------------
# CLI
# -----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mouth-json", type=str, required=True,
                        help="mouth_timeline.json (vad_aligned版) のパス")
    parser.add_argument("--debug-csv", type=str,
                        help="M0 debug timeline CSV のパス")
    parser.add_argument("--check-mouth-vs-m0", action="store_true",
                        help="mouth_json vs M0 debug CSV の整合性チェックを行う")
    parser.add_argument("--check-vad", action="store_true",
                        help="VAD vs mouth_open のチェック + オフセット推定を行う")
    parser.add_argument("--max-lag-ms", type=int, default=800,
                        help="VAD vs mouth_open の相互相関で探索する最大ラグ(ms)")

    args = parser.parse_args()

    mouth_json_path = Path(args.mouth_json)
    debug_csv_path = Path(args.debug_csv) if args.debug_csv else None

    if args.check_mouth_vs_m0:
        if debug_csv_path is None:
            raise SystemExit("--check-mouth-vs-m0 には --debug-csv が必要です。")
        check_mouth_vs_m0(
            mouth_json_path=mouth_json_path,
            debug_csv_path=debug_csv_path,
            tolerance_ms=0,
        )

    if args.check_vad:
        check_vad_vs_mouth(
            mouth_json_path=mouth_json_path,
            max_lag_ms=args.max_lag_ms,
        )

    if not args.check_mouth_vs_m0 and not args.check_vad:
        print("何も指定されていません。--check-mouth-vs-m0 や --check-vad を指定してください。")


if __name__ == "__main__":
    main()
