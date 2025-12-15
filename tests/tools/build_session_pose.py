#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_session_pose.py

単一 BG クリップ用の pose_timeline (例: 1_0_5_test_pose_timeline.json)
から、セッション全体用の session_pose_timeline.v1.0 を生成する。

想定する入力フォーマット（例）:
[
  {
    "t_ms": 0.0,
    "yaw_deg": 1.8,
    "pitch_deg": 0.7,
    "roll_deg": 1.7,
    "scale": 0.99,
    "tx": -0.03,
    "ty": -0.24
  },
  ...
]

出力フォーマット（Session Pose Timeline v1.0）:
{
  "schema_version": "session_pose_timeline_v1.0",
  "session_id": "sess_real_01",
  "step_ms": 40,
  "frames": [
    {
      "t_ms": 0,
      "yaw": 1.8,
      "pitch": 0.7,
      "roll": 1.7,
      "bbox": { "x": 0.40, "y": 0.20, "w": 0.20, "h": 0.20 },
      "scale": 0.99,
      "tx": -0.03,
      "ty": -0.24
    },
    ...
  ]
}

※ bbox は現時点では「仮の矩形」を与えておき、後で DLC 側の情報に差し替える前提。
"""

from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


def load_json(path: Path) -> Any:
    txt = path.read_text(encoding="utf-8")
    return json.loads(txt)


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_frames(raw: Any) -> List[Dict[str, Any]]:
    """
    - ルートが list → そのまま
    - ルートが dict → "frames" or "timeline" を探す
    """
    if isinstance(raw, list):
        frames = raw
    elif isinstance(raw, dict):
        if "frames" in raw:
            frames = raw["frames"]
        elif "timeline" in raw:
            frames = raw["timeline"]
        else:
            raise ValueError("dict 形式ですが frames/timeline が見つかりません。")
    else:
        raise ValueError("pose_timeline のルートは list か dict を想定しています。")

    if not isinstance(frames, list) or not frames:
        raise ValueError("pose_timeline に有効な frames が見つかりません。")

    # t_ms 昇順にソート（float/str にも一応対応）
    frames = sorted(frames, key=lambda f: float(f.get("t_ms", 0.0)))
    return frames


def build_session_pose_frames(
    src_frames: Sequence[Dict[str, Any]],
    audio_ms: int,
    step_ms: int,
    loop: bool,
    bbox: Tuple[float, float, float, float],
) -> List[Dict[str, Any]]:
    """
    単一 BG クリップ用の src_frames をもとに、
    0〜audio_ms を step_ms 刻みでサンプリングして session frames を作る。

    - loop=True: src_frames をループ（(t//step_ms)%len）
    - loop=False: 最後のフレームをホールド
    """
    if not src_frames:
        raise ValueError("src_frames が空です。")

    n_src = len(src_frames)
    bx, by, bw, bh = bbox

    out: List[Dict[str, Any]] = []
    t = 0
    while t <= audio_ms:
        idx = int(t // step_ms)
        if loop:
            idx = idx % n_src
        else:
            if idx >= n_src:
                idx = n_src - 1

        base = src_frames[idx]

        # yaw/pitch/roll は yaw_deg などがあればそれを使う
        yaw = float(base.get("yaw", base.get("yaw_deg", 0.0)))
        pitch = float(base.get("pitch", base.get("pitch_deg", 0.0)))
        roll = float(base.get("roll", base.get("roll_deg", 0.0)))

        scale = base.get("scale", None)
        tx = base.get("tx", None)
        ty = base.get("ty", None)

        frame: Dict[str, Any] = {
            "t_ms": int(t),
            "yaw": yaw,
            "pitch": pitch,
            "roll": roll,
            "bbox": {
                "x": bx,
                "y": by,
                "w": bw,
                "h": bh,
            },
        }
        if scale is not None:
            frame["scale"] = float(scale)
        if tx is not None:
            frame["tx"] = float(tx)
        if ty is not None:
            frame["ty"] = float(ty)

        out.append(frame)
        t += step_ms

    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="単一 BG クリップ用 pose_timeline から session_pose_timeline.v1.0 を生成する"
    )
    ap.add_argument(
        "--src-pose",
        type=Path,
        required=True,
        help="入力 pose_timeline JSON (例: 1_0_5_test_pose_timeline.json)",
    )
    ap.add_argument(
        "--session-id",
        type=str,
        required=True,
        help="生成するセッションID (例: sess_real_01)",
    )
    ap.add_argument(
        "--audio-ms",
        type=int,
        required=True,
        help="セッション全体の長さ [ms] (TTS / mouth_timeline の audio_ms と揃える)",
    )
    ap.add_argument(
        "--step-ms",
        type=int,
        default=40,
        help="サンプリング間隔 [ms] (デフォルト: 40ms = 25fps)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        required=True,
        help="出力先 JSON パス (例: sessions/sess_real_01/pose_timeline.json)",
    )
    ap.add_argument(
        "--loop",
        action="store_true",
        help="audio_ms が src_pose の長さを超えた場合、src_frames をループさせる（デフォルト: ホールド）",
    )
    ap.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("X", "Y", "W", "H"),
        default=(0.40, 0.20, 0.20, 0.20),
        help="顔 bbox のデフォルト値（正規化座標）。DLC由来の値が使えるようになるまでは仮値でOK。",
    )

    args = ap.parse_args()

    raw = load_json(args.src_pose)
    src_frames = _extract_frames(raw)

    frames = build_session_pose_frames(
        src_frames=src_frames,
        audio_ms=args.audio_ms,
        step_ms=args.step_ms,
        loop=bool(args.loop),
        bbox=tuple(args.bbox),  # type: ignore[arg-type]
    )

    out_obj = {
        "schema_version": "session_pose_timeline_v1.0",
        "session_id": args.session_id,
        "step_ms": args.step_ms,
        "frames": frames,
        # clips は BG1本構成の場合は省略しても良い（必要になったら追加）
    }

    save_json(args.out, out_obj)
    print(f"[build_session_pose] wrote: {args.out}")


if __name__ == "__main__":
    main()
