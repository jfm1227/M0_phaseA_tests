#!/usr/bin/env python3
# tests/scripts/check_pose_and_affine.py
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _quantiles(vals: List[float], qs: List[float]) -> Dict[str, float]:
    if not vals:
        return {f"p{int(q*100)}": float("nan") for q in qs}
    vs = sorted(vals)
    n = len(vs)
    out = {}
    for q in qs:
        if n == 1:
            out[f"p{int(q*100)}"] = float(vs[0])
            continue
        pos = q * (n - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            out[f"p{int(q*100)}"] = float(vs[lo])
        else:
            w = pos - lo
            out[f"p{int(q*100)}"] = float(vs[lo] * (1.0 - w) + vs[hi] * w)
    return out

def _choose_view_with_pitch(yaw_deg: float, pitch_deg: float, rules: Dict[str, float]) -> str:
    """
    優先順位:
      1) pitch (up15 / down15)
      2) yaw (left30 / right30)
      3) front
    """
    # pitch rules
    up_max = float(rules.get("up15_max_pitch_deg", -15.0))
    down_min = float(rules.get("down15_min_pitch_deg", 15.0))
    
    if pitch_deg <= up_max:
        return "up15"
    if pitch_deg >= down_min:
        return "down15"

    # yaw rules
    left_max = float(rules.get("left30_max_yaw_deg", -12.0))
    right_min = float(rules.get("right30_min_yaw_deg", 12.0))
    
    if yaw_deg <= left_max:
        return "left30"
    if yaw_deg >= right_min:
        return "right30"
        
    return "front"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pose", required=True, help="pose_timeline json (list-root)")
    ap.add_argument("--atlas", required=True, help="atlas.min.json")
    ap.add_argument("--thr_front_deg", type=float, default=16.0)
    ap.add_argument("--step_ms", type=int, default=40)
    args = ap.parse_args()

    pose_path = Path(args.pose)
    atlas_path = Path(args.atlas)

    pose = _load_json(pose_path)
    if not isinstance(pose, list):
        raise SystemExit(f"pose root must be list: {pose_path}")

    atlas = _load_json(atlas_path)
    # デフォルトルールの定義
    rules = {
        "left30_max_yaw_deg": -12.0,
        "right30_min_yaw_deg": 12.0,
        "up15_max_pitch_deg": -15.0,
        "down15_min_pitch_deg": 15.0,
    }
    # atlas側に設定があれば上書き
    atlas_rules = (atlas.get("view_rules") or {})
    if isinstance(atlas_rules, dict):
        rules.update(atlas_rules)

    # gather series
    yaw = []
    pitch = []
    tx = []
    ty = []
    scale = []
    affine_present = 0
    affine_valid = 0

    views = {"front": 0, "left30": 0, "right30": 0, "up15": 0, "down15": 0}
    front_strict = 0  # |yaw| < thr_front_deg

    bad_affine = 0
    first_t_ms = None
    last_t_ms = None

    for fr in pose:
        if not isinstance(fr, dict):
            continue
        t_ms = fr.get("t_ms")
        if isinstance(t_ms, (int, float)):
            if first_t_ms is None:
                first_t_ms = int(t_ms)
            last_t_ms = int(t_ms)

        # Yaw & Pitch
        y = fr.get("yaw", 0.0)
        p = fr.get("pitch", 0.0)
        
        yaw_val = float(y)
        pitch_val = float(p)
        
        yaw.append(yaw_val)
        pitch.append(pitch_val)
        
        # View determination
        v = _choose_view_with_pitch(yaw_val, pitch_val, rules)
        views[v] += 1
        
        if abs(yaw_val) < args.thr_front_deg:
            front_strict += 1

        # Others
        x = fr.get("tx")
        if isinstance(x, (int, float)):
            tx.append(float(x))
        yv = fr.get("ty")
        if isinstance(yv, (int, float)):
            ty.append(float(yv))
        sc = fr.get("scale")
        if isinstance(sc, (int, float)):
            scale.append(float(sc))

        a3 = fr.get("affine3")
        if isinstance(a3, dict):
            affine_present += 1
            dst = a3.get("dst")
            ok = (
                isinstance(dst, list)
                and len(dst) == 3
                and all(isinstance(p, list) and len(p) == 2 for p in dst)
                and all(isinstance(c, (int, float)) for p in dst for c in p)
            )
            if ok:
                affine_valid += 1
            else:
                bad_affine += 1

    n = len(pose)
    dur_ms = (last_t_ms - first_t_ms) if (first_t_ms is not None and last_t_ms is not None) else None

    def _range(vals: List[float]) -> float:
        return (max(vals) - min(vals)) if vals else float("nan")

    yaw_q = _quantiles(yaw, [0.0, 0.5, 0.95, 1.0])
    pitch_q = _quantiles(pitch, [0.0, 0.5, 0.95, 1.0])
    tx_q  = _quantiles(tx,  [0.0, 0.5, 0.95, 1.0])
    ty_q  = _quantiles(ty,  [0.0, 0.5, 0.95, 1.0])
    sc_q  = _quantiles(scale,[0.0, 0.5, 0.95, 1.0])

    report = {
        "pose_path": str(pose_path),
        "frames_total": n,
        "t_ms_first": first_t_ms,
        "t_ms_last": last_t_ms,
        "duration_ms_from_pose": dur_ms,

        "yaw_deg": {
            "min": yaw_q["p0"],
            "p50": yaw_q["p50"],
            "p95": yaw_q["p95"],
            "max": yaw_q["p100"],
            "range": _range(yaw),
            "front_strict_ratio(|yaw|<thr_front_deg)": (front_strict / max(1, len(yaw))),
            "thr_front_deg": args.thr_front_deg,
        },
        "pitch_deg": {
            "min": pitch_q["p0"],
            "p50": pitch_q["p50"],
            "p95": pitch_q["p95"],
            "max": pitch_q["p100"],
            "range": _range(pitch),
        },
        "tx_px": {"min": tx_q["p0"], "p50": tx_q["p50"], "p95": tx_q["p95"], "max": tx_q["p100"], "range": _range(tx)},
        "ty_px": {"min": ty_q["p0"], "p50": ty_q["p50"], "p95": ty_q["p95"], "max": ty_q["p100"], "range": _range(ty)},
        "scale": {"min": sc_q["p0"], "p50": sc_q["p50"], "p95": sc_q["p95"], "max": sc_q["p100"], "range": _range(scale)},

        "affine3": {
            "present_frames": affine_present,
            "present_ratio": affine_present / max(1, n),
            "valid_frames": affine_valid,
            "valid_ratio": affine_valid / max(1, n),
            "bad_frames": bad_affine,
        },
        "views_by_atlas_rules": views,
        "views_ratio": {k: v / max(1, len(yaw)) for k, v in views.items()},
        "atlas_view_rules": rules,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())