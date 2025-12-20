from __future__ import annotations

import os
import json
from typing import Dict, Any, Tuple
import numpy as np
import cv2

# ============================================================
# Image I/O
# ============================================================

def _load_rgba(path: str) -> np.ndarray:
    """PNG などを BGRA で読む。アルファ無しなら 255 で補完。"""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    if img.ndim != 3:
        raise ValueError(f"Unsupported image ndim: {img.ndim} ({path})")
    if img.shape[2] == 3:
        bgr = img
        a = np.full((img.shape[0], img.shape[1], 1), 255, dtype=np.uint8)
        img = np.concatenate([bgr, a], axis=2)
    elif img.shape[2] != 4:
        raise ValueError(f"Unsupported channel count: {img.shape[2]} ({path})")
    return img


def _ensure_bgra(img: np.ndarray) -> np.ndarray:
    """BGR/BGRA/GRAY を BGRA に揃える。"""
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    if img.ndim == 3 and img.shape[2] == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    if img.ndim == 3 and img.shape[2] == 4:
        return img
    raise ValueError(f"Unsupported img shape: {img.shape}")


def _alpha_paste(canvas_bgra: np.ndarray, src_bgra: np.ndarray, cx: int, cy: int) -> None:
    """src をアルファブレンドで canvas に貼り付ける（両方 BGRA）。"""
    h, w = canvas_bgra.shape[:2]
    sh, sw = src_bgra.shape[:2]

    x0 = int(cx - sw // 2)
    y0 = int(cy - sh // 2)
    x1 = x0 + sw
    y1 = y0 + sh

    # 画面外クリップ
    sx0 = max(0, -x0)
    sy0 = max(0, -y0)
    dx0 = max(0, x0)
    dy0 = max(0, y0)
    sx1 = sw - max(0, x1 - w)
    sy1 = sh - max(0, y1 - h)
    dx1 = dx0 + (sx1 - sx0)
    dy1 = dy0 + (sy1 - sy0)

    if dx0 >= dx1 or dy0 >= dy1:
        return

    src_crop = src_bgra[sy0:sy1, sx0:sx1]
    dst_crop = canvas_bgra[dy0:dy1, dx0:dx1]

    alpha = src_crop[:, :, 3:4].astype(np.float32) / 255.0
    inv = 1.0 - alpha

    dst_crop[:, :, :3] = (
        src_crop[:, :, :3].astype(np.float32) * alpha +
        dst_crop[:, :, :3].astype(np.float32) * inv
    ).astype(np.uint8)

    dst_crop[:, :, 3:4] = np.clip(
        src_crop[:, :, 3:4].astype(np.float32) +
        dst_crop[:, :, 3:4].astype(np.float32) * inv,
        0, 255
    ).astype(np.uint8)

    canvas_bgra[dy0:dy1, dx0:dx1] = dst_crop


# ============================================================
# Mouth label normalize
# ============================================================

def normalize_mouth_label(mouth: str) -> str:
    if not mouth:
        return "closed"
    m = str(mouth).lower()
    if m in ("close", "mouth_close"):
        return "closed"
    return m


# ============================================================
# Atlas load (expression meta passthrough)
# ============================================================

def load_atlas_index(atlas_json_path: str) -> Dict[str, Any]:
    """
    atlas.min.json を内部形式に正規化する。

    - 旧形式（トップレベルに front/left30/...）もサポート
    - data["views"][view][mouth] で参照できる
    - expression_labels / expression_default などは素通し
    """
    with open(atlas_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    views = data.get("views")
    if not isinstance(views, dict):
        views = {}
        for key, value in data.items():
            if isinstance(value, dict) and "closed" in value:
                views[key] = {str(m).lower(): path for m, path in value.items()}
        data["views"] = views
    else:
        norm_views = {}
        for vname, vdict in views.items():
            if isinstance(vdict, dict):
                norm_views[vname] = {str(m).lower(): path for m, path in vdict.items()}
        data["views"] = norm_views

    if "view_rules" not in data:
        data["view_rules"] = {}
    if "fallback" not in data:
        data["fallback"] = {"view": "front", "mouth": "closed"}

    return data


# ============================================================
# View selection
# ============================================================

def choose_view_from_yaw(yaw_deg: float, view_rules: Dict[str, Any]) -> str:
    left_max = float(view_rules.get("left30_max_yaw_deg", -12.0))
    right_min = float(view_rules.get("right30_min_yaw_deg", 12.0))
    if yaw_deg <= left_max:
        return "left30"
    if yaw_deg >= right_min:
        return "right30"
    return "front"


# ============================================================
# Sprite path resolve (+expression derived path)
# ============================================================

def _resolve_base_sprite_path(atlas_idx: Dict[str, Any], view: str, mouth: str) -> Tuple[str | None, bool]:
    """
    atlas_idx['views'][view][mouth] を引く。無ければ fallback。
    returns: (path_rel or None, used_fallback)
    """
    views = atlas_idx.get("views", {})
    used_fallback = False

    view_dict = views.get(view)
    if isinstance(view_dict, dict):
        p = view_dict.get(mouth)
        if isinstance(p, str):
            return p, used_fallback

    fb = atlas_idx.get("fallback", {})
    fb_view = str(fb.get("view", "front"))
    fb_mouth = normalize_mouth_label(str(fb.get("mouth", "closed")))

    fb_view_dict = views.get(fb_view)
    if isinstance(fb_view_dict, dict):
        p = fb_view_dict.get(fb_mouth)
        if isinstance(p, str):
            used_fallback = True
            return p, used_fallback

    return None, True


def _derive_expression_path(
    atlas_idx: Dict[str, Any],
    view: str,
    mouth: str,
    expression: str | None,
    base_path_rel: str,
) -> str:
    """
    assets_dir/<expr>_<view>/<basename.png> を導出。
    - expression None / normal → base を返す
    - expression_labels に無いラベル → base を返す
    """
    expr_default = str(atlas_idx.get("expression_default", "normal")).lower()
    expr = (expression or expr_default)
    expr = str(expr).lower()

    if expr in ("", "normal"):
        return base_path_rel

    labels = [str(x).lower() for x in atlas_idx.get("expression_labels", [])]
    if labels and expr not in labels:
        return base_path_rel

    base_name = os.path.basename(base_path_rel)
    expr_dir = f"{expr}_{view}"
    expr_path_rel = os.path.join(expr_dir, base_name)
    return expr_path_rel.replace("\\", "/")


# ============================================================
# Pose transform: (center pivot) SCALE -> ROTATE, then caller TRANSLATE(tx/ty)
# ============================================================

def _warp_affine_keep_bounds(src: np.ndarray, M: np.ndarray) -> np.ndarray:
    """
    変換後にクリップしないよう、変換後の外接矩形サイズに合わせて出力サイズを拡張する。
    src: BGRA
    M: 2x3 affine mapping src -> dst (before bounds adjustment)
    """
    h, w = src.shape[:2]
    corners = np.array([
        [0, 0],
        [w, 0],
        [w, h],
        [0, h],
    ], dtype=np.float32).reshape(-1, 1, 2)

    warped = cv2.transform(corners, M)  # (4,1,2)
    xs = warped[:, 0, 0]
    ys = warped[:, 0, 1]
    min_x, max_x = float(xs.min()), float(xs.max())
    min_y, max_y = float(ys.min()), float(ys.max())

    out_w = int(np.ceil(max_x - min_x))
    out_h = int(np.ceil(max_y - min_y))
    out_w = max(1, out_w)
    out_h = max(1, out_h)

    # 平行移動を足して、出力座標を (0,0) 起点に寄せる
    M_adj = M.copy()
    M_adj[0, 2] -= min_x
    M_adj[1, 2] -= min_y

    dst = cv2.warpAffine(
        src,
        M_adj,
        (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    return dst


def pose_transform(src_bgra: np.ndarray, yaw_deg: float, pitch_deg: float, roll_deg: float, scale: float) -> np.ndarray:
    """
    ★順序固定★: pivot(center) 기준으로 SCALE -> ROLL(rotate).
    yaw/pitch は将来拡張（現時点は roll のみ適用）。
    """
    if src_bgra is None:
        return src_bgra

    # safety
    s = float(scale)
    if not np.isfinite(s):
        s = 1.0
    s = max(0.05, min(20.0, s))

    r = float(roll_deg)
    if not np.isfinite(r):
        r = 0.0

    if abs(r) < 1e-6 and abs(s - 1.0) < 1e-6:
        return src_bgra

    h, w = src_bgra.shape[:2]
    center = (w / 2.0, h / 2.0)

    # OpenCV の getRotationMatrix2D は (scale+rotation) を中心基準で作る
    M = cv2.getRotationMatrix2D(center, r, s)

    # クリップしないように bounds を調整
    return _warp_affine_keep_bounds(src_bgra, M)


# ============================================================
# Main renderer
# ============================================================

def render_video(
    pose_timeline: list[dict[str, Any]],
    mouth_timeline: list[dict[str, Any]] | None,
    atlas_json_path: str,
    assets_dir: str,
    bg_video_path: str,
    out_mp4_path: str,
    fps: int = 25,
    target_h_ratio: float = 0.25,
    crossfade_frames: int = 0,
    per_frame_hook=None,
) -> Dict[str, Any]:
    """
    pose_timeline: list of {t_ms, yaw/pitch/roll(deg), tx, ty, scale, ...}
    mouth_timeline: list of {t_ms, mouth, expression? ...}（無ければ closed 固定）
    """
    atlas_idx = load_atlas_index(atlas_json_path)
    view_rules = atlas_idx.get("view_rules", {})

    cap = cv2.VideoCapture(bg_video_path)
    if not cap.isOpened():
        raise FileNotFoundError(bg_video_path)

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_mp4_path, fourcc, fps, (width, height))

    pose_idx = 0
    mouth_idx = 0

    fallback_frames = 0
    first_fallback_ms = None
    views_count: Dict[str, int] = {}
    prev_frame = None

    def _get_vals_at(t_ms: int, tl: list[dict[str, Any]], idx: int) -> tuple[dict[str, Any], int]:
        if not tl:
            return {}, idx
        while idx + 1 < len(tl) and int(tl[idx + 1].get("t_ms", 0)) <= t_ms:
            idx += 1
        return tl[idx], idx

    step_ms = int(round(1000 / fps))

    for i in range(total):
        ok, bgr = cap.read()
        if not ok:
            break

        t_ms = i * step_ms
        vals, pose_idx = _get_vals_at(t_ms, pose_timeline, pose_idx)

        mvals = {}
        if mouth_timeline:
            mvals, mouth_idx = _get_vals_at(t_ms, mouth_timeline, mouth_idx)

        mouth = normalize_mouth_label(str(mvals.get("mouth", "closed")))
        expression = mvals.get("expression", None)

        yaw = float(vals.get("yaw", vals.get("yaw_deg", 0.0)))
        pitch = float(vals.get("pitch", vals.get("pitch_deg", 0.0)))
        roll = float(vals.get("roll", vals.get("roll_deg", 0.0)))

        view = choose_view_from_yaw(yaw, view_rules)
        views_count[view] = views_count.get(view, 0) + 1

        used_fallback = False
        src = None

        base_path_rel, used_fallback_base = _resolve_base_sprite_path(atlas_idx, view, mouth)
        used_fallback = used_fallback or used_fallback_base

        if base_path_rel:
            expr_path_rel = _derive_expression_path(
                atlas_idx=atlas_idx,
                view=view,
                mouth=mouth,
                expression=expression,
                base_path_rel=base_path_rel,
            )
            try:
                src = _load_rgba(os.path.join(assets_dir, expr_path_rel))
            except FileNotFoundError:
                try:
                    src = _load_rgba(os.path.join(assets_dir, base_path_rel))
                    used_fallback = True  # 表情が無く normal に落ちた
                except FileNotFoundError:
                    src = None
                    used_fallback = True
        else:
            used_fallback = True

        frame = _ensure_bgra(bgr)

        if src is not None:
            # ---- 基準サイズ（target_h_ratio）に対して、pose.scale を掛ける ----
            tgt_h_base = max(1, int(height * float(target_h_ratio)))

            pose_scale = float(vals.get("scale", 1.0))
            if not np.isfinite(pose_scale):
                pose_scale = 1.0
            pose_scale = max(0.2, min(5.0, pose_scale))  # clamp（必要なら調整）

            desired_h = max(1, int(round(tgt_h_base * pose_scale)))

            # 「scale→回転」を sprite 側で完結させるため、src 高さ基準で scale を作る
            scale_total = float(desired_h) / float(max(1, src.shape[0]))

            # ★中心基準で SCALE -> ROLL（tx/ty はこの後の貼り付けでのみ適用）★
            src_tf = pose_transform(src, yaw_deg=yaw, pitch_deg=pitch, roll_deg=roll, scale=scale_total)

            # ---- tx/ty は px 前提で、貼り付け中心にだけ反映（scaleに巻き込まれない）----
            tx = float(vals.get("tx", 0.0))
            ty = float(vals.get("ty", 0.0))
            if not np.isfinite(tx):
                tx = 0.0
            if not np.isfinite(ty):
                ty = 0.0

            max_tx = width * 0.45
            max_ty = height * 0.45
            tx = max(-max_tx, min(max_tx, tx))
            ty = max(-max_ty, min(max_ty, ty))

            cx = int(round((width * 0.5) + tx))
            cy = int(round((height * 0.58) + ty))

            _alpha_paste(frame, src_tf, cx, cy)

        if used_fallback:
            fallback_frames += 1
            if first_fallback_ms is None:
                first_fallback_ms = t_ms

        if per_frame_hook is not None:
            frame = per_frame_hook(frame, t_ms, i)

        # crossfade（旧版互換）
        if crossfade_frames > 0 and prev_frame is not None and i % (fps // 2 or 1) == 0:
            for k in range(crossfade_frames):
                a = (k + 1) / float(crossfade_frames + 1)
                mix = (prev_frame.astype(np.float32) * (1.0 - a) + frame.astype(np.float32) * a).astype(np.uint8)
                writer.write(mix[:, :, :3])
            prev_frame = frame.copy()
        else:
            writer.write(frame[:, :, :3])
            prev_frame = frame.copy()

    cap.release()
    writer.release()

    return {
        "fallback_frames": fallback_frames,
        "first_fallback_ms": first_fallback_ms,
        "views_count": views_count,
        "total_frames": int(total),
        "fps": int(fps),
        "out_mp4": out_mp4_path,
    }


# ============================================================
# Legacy wrapper (no background video)
# ============================================================

def render_video_legacy(
    out_mp4_path: str,
    width: int,
    height: int,
    fps: int,
    duration_s: int,
    crossfade_frames: int,
    merged_value_fn,
    *,
    assets_dir: str,
    atlas_json_rel: str,
    transform_cfg=None,
):
    """
    m0_runner.py が期待している旧 render_video I/F を満たす互換ラッパー。
    背景MP4は読まず、width/height の空キャンバスにスプライトを貼って MP4 生成。
    """
    atlas_idx = load_atlas_index(atlas_json_rel)
    view_rules = atlas_idx.get("view_rules", {})

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_mp4_path, fourcc, fps, (width, height))

    total_frames = int(round(duration_s * fps))
    step_ms = int(round(1000 / fps))

    fallback_frames = 0
    first_fallback_ms = None
    views_count: Dict[str, int] = {}

    target_h_ratio = float((transform_cfg or {}).get("target_h_ratio", 0.25))
    cy_base_ratio = float((transform_cfg or {}).get("cy_base_ratio", 0.58))

    for i in range(total_frames):
        t_ms = i * step_ms
        vals = merged_value_fn(t_ms) or {}

        mouth = normalize_mouth_label(str(vals.get("mouth", "closed")))
        expression = vals.get("expression", None)

        yaw = float(vals.get("yaw", vals.get("yaw_deg", 0.0)))
        pitch = float(vals.get("pitch", vals.get("pitch_deg", 0.0)))
        roll = float(vals.get("roll", vals.get("roll_deg", 0.0)))

        view = choose_view_from_yaw(yaw, view_rules)
        views_count[view] = views_count.get(view, 0) + 1

        used_fallback = False
        base_path_rel, used_fallback_base = _resolve_base_sprite_path(atlas_idx, view, mouth)
        used_fallback = used_fallback or used_fallback_base

        src = None
        if base_path_rel:
            expr_path_rel = _derive_expression_path(
                atlas_idx=atlas_idx,
                view=view,
                mouth=mouth,
                expression=expression,
                base_path_rel=base_path_rel,
            )
            try:
                src = _load_rgba(os.path.join(assets_dir, expr_path_rel))
            except FileNotFoundError:
                try:
                    src = _load_rgba(os.path.join(assets_dir, base_path_rel))
                    used_fallback = True
                except FileNotFoundError:
                    src = None
                    used_fallback = True
        else:
            used_fallback = True

        # opaque black BGRA
        frame = np.zeros((height, width, 4), dtype=np.uint8)
        frame[:, :, 3] = 255

        if src is not None:
            tgt_h_base = max(1, int(height * target_h_ratio))

            pose_scale = float(vals.get("scale", 1.0))
            if not np.isfinite(pose_scale):
                pose_scale = 1.0
            pose_scale = max(0.2, min(5.0, pose_scale))

            desired_h = max(1, int(round(tgt_h_base * pose_scale)))
            scale_total = float(desired_h) / float(max(1, src.shape[0]))

            src_tf = pose_transform(src, yaw_deg=yaw, pitch_deg=pitch, roll_deg=roll, scale=scale_total)

            tx = float(vals.get("tx", 0.0))
            ty = float(vals.get("ty", 0.0))
            if not np.isfinite(tx):
                tx = 0.0
            if not np.isfinite(ty):
                ty = 0.0

            max_tx = width * 0.45
            max_ty = height * 0.45
            tx = max(-max_tx, min(max_tx, tx))
            ty = max(-max_ty, min(max_ty, ty))

            cx = int(round((width * 0.5) + tx))
            cy = int(round((height * cy_base_ratio) + ty))

            _alpha_paste(frame, src_tf, cx, cy)

        if used_fallback:
            fallback_frames += 1
            if first_fallback_ms is None:
                first_fallback_ms = t_ms

        writer.write(frame[:, :, :3])

    writer.release()

    return {
        "fallback_frames": fallback_frames,
        "first_fallback_ms": first_fallback_ms,
        "views_count": views_count,
        "total_frames": total_frames,
        "fps": fps,
        "out_mp4": out_mp4_path,
    }
