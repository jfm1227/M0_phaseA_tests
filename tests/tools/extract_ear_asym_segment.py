# tools/extract_ear_asym_segment.py
from __future__ import annotations
import argparse
import pandas as pd
import numpy as np

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--t0", type=float, required=True)
    ap.add_argument("--t1", type=float, required=True)
    ap.add_argument("--fps", type=float, default=25.0)
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    # DLC列名に合わせて調整（あなたのETLは ear_right_base / ear_left_base を想定）:contentReference[oaicite:3]{index=3}
    erx, elx = "ear_right_base_x", "ear_left_base_x"
    ery, ely = "ear_right_base_y", "ear_left_base_y"

    df["t_sec"] = df["frame"] / args.fps
    seg = df[(df["t_sec"] >= args.t0) & (df["t_sec"] <= args.t1)].copy()

    # 参考値：耳同士の距離と x差
    seg["ear_dx"] = seg[erx] - seg[elx]
    seg["ear_dy"] = seg[ery] - seg[ely]
    seg["ear_dist"] = np.hypot(seg["ear_dx"], seg["ear_dy"])

    keep = ["frame","t_sec", erx,ery,elx,ely,"ear_dx","ear_dy","ear_dist"]
    seg[keep].to_csv(args.out, index=False)
    print(f"saved {len(seg)} rows -> {args.out}")

if __name__ == "__main__":
    main()
