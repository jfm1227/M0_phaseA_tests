#!/usr/bin/env python3
import json
from pathlib import Path

def main():
    src = Path("tests/timelines/pose_timeline_fixed.json")
    dst = Path("tests/timelines/pose_timeline_m0_3.json")

    STEP_MS = 40  # 25fps grid
    FLIP_YAW = True  # ★ここが今回のポイント（符号反転）

    data = json.loads(src.read_text(encoding="utf-8"))
    assert "frames" in data and isinstance(data["frames"], list), "src must be dict with 'frames' list"
    frames = data["frames"]
    assert len(frames) > 0, "src has no frames"

    # src sanity
    sample = frames[0]
    for k in ["t_sec", "yaw_deg"]:
        assert k in sample, f"src frame missing key: {k}. keys={list(sample.keys())}"

    ys = [float(fr["yaw_deg"]) for fr in frames if "yaw_deg" in fr]
    print(f"[src yaw_deg] min={min(ys):.3f} max={max(ys):.3f}")

    out = []
    for fr in frames:
        t_sec = float(fr.get("t_sec", 0.0))
        t_ms = int(round((t_sec * 1000.0) / STEP_MS) * STEP_MS)

        yaw   = float(fr.get("yaw_deg", 0.0))
        pitch = float(fr.get("pitch_deg", 0.0))
        roll  = float(fr.get("roll_deg", 0.0))

        if FLIP_YAW:
            yaw = -yaw

        scale = float(fr.get("scale", 1.0))
        tx    = float(fr.get("tx", 0.0))
        ty    = float(fr.get("ty", 0.0))

        out.append({
            "t_ms": t_ms,

            # M0 canonical keys (deg)
            "yaw": yaw, "pitch": pitch, "roll": roll,

            # Optional redundant keys (deg) for debug/compat
            "yaw_deg": yaw, "pitch_deg": pitch, "roll_deg": roll,

            "scale": scale,
            "tx": tx,
            "ty": ty,
        })

    out.sort(key=lambda x: x["t_ms"])

    # Dedup t_ms (keep first)
    dedup = []
    seen = set()
    for it in out:
        if it["t_ms"] in seen:
            continue
        seen.add(it["t_ms"])
        dedup.append(it)
    out = dedup

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # quick check: max |yaw|
    m = max(out, key=lambda x: abs(x["yaw"])) if out else None
    if m:
        print(f"[out] frames={len(out)} t_end_ms={out[-1]['t_ms']}")
        print(f"[out] max|yaw|={m['yaw']:.3f} deg at t_ms={m['t_ms']}")

    print("[OK] wrote:", dst)

if __name__ == "__main__":
    main()
