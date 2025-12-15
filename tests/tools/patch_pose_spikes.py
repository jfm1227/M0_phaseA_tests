# tools/patch_pose_spikes.py
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

POSE_COLS = ["yaw_deg","pitch_deg","roll_deg","scale","tx","ty"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-json", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--fps", type=float, default=25.0)
    ap.add_argument("--max-dyaw-deg-per-frame", type=float, default=8.0)   # 例：8deg/40ms相当
    ap.add_argument("--max-dpitch-deg-per-frame", type=float, default=10.0)
    ap.add_argument("--max-droll-deg-per-frame", type=float, default=10.0)
    ap.add_argument("--clip-yaw-abs", type=float, default=60.0)            # clip張り付き検知にも使う
    ap.add_argument("--smooth-window", type=int, default=5)                # rolling median
    args = ap.parse_args()

    tl = json.loads(Path(args.in_json).read_text(encoding="utf-8"))
    df = pd.DataFrame(tl)
    df = df.sort_values("t_ms").reset_index(drop=True)

    # 1) クリップ張り付き（例：±60）を outlier候補に
    for col in ["yaw_deg"]:
        df.loc[df[col].abs() >= args.clip_yaw_abs - 1e-6, col] = np.nan

    # 2) 微分でスパイク検知（前後差が大きい点を NaN）
    def zap_by_diff(col: str, thr: float):
        x = df[col].astype(float)
        dx = x.diff().abs()
        df.loc[dx > thr, col] = np.nan

    zap_by_diff("yaw_deg", args.max_dyaw_deg_per_frame)
    zap_by_diff("pitch_deg", args.max_dpitch_deg_per_frame)
    zap_by_diff("roll_deg", args.max_droll_deg_per_frame)

    # 3) 線形補間（時間軸で）
    for col in POSE_COLS:
        if col in df.columns:
            df[col] = df[col].interpolate(limit_direction="both")

    # 4) 軽い平滑化（rolling median）
    w = max(1, int(args.smooth_window))
    if w >= 3:
        for col in POSE_COLS:
            if col in df.columns:
                df[col] = df[col].rolling(window=w, center=True, min_periods=1).median()

    out = df.to_dict(orient="records")
    Path(args.out_json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved patched timeline -> {args.out_json}")

if __name__ == "__main__":
    main()
