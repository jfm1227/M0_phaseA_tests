# tools/extract_pose_segment.py
from __future__ import annotations
import json, argparse
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-json", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--t0", type=float, required=True, help="start sec")
    ap.add_argument("--t1", type=float, required=True, help="end sec")
    args = ap.parse_args()

    tl = json.loads(Path(args.in_json).read_text(encoding="utf-8"))
    # tl is list[{t_ms,...}]
    t0_ms = args.t0 * 1000.0
    t1_ms = args.t1 * 1000.0
    seg = [e for e in tl if t0_ms <= float(e["t_ms"]) <= t1_ms]

    Path(args.out_json).write_text(json.dumps(seg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(seg)} events -> {args.out_json}")

if __name__ == "__main__":
    main()
