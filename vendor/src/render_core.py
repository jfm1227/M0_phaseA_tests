from __future__ import annotations
import os, json
from typing import Dict, Any
import numpy as np
import cv2

# -----------------------------
# 画像I/Oユーティリティ
# -----------------------------
def _load_rgba(path: str) -> np.ndarray:
    """PNGなどを BGRA で読む。アルファ無しなら255で補完。"""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    if img.shape[2] == 3:
        bgr = img
        a = np.full((img.shape[0], img.shape[1], 1), 255, dtype=np.uint8)
        img = np.concatenate([bgr, a], axis=2)
    return img


def _ensure_bgra(img: np.ndarray) -> np.ndarray:
    """BGR/BGRA/GRAY などを BGRA に揃える。"""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    elif img.shape[2] == 4:
        pass
    else:
        raise ValueError(f"Unsupported img shape: {img.shape}")
    return img


def _alpha_paste(canvas_bgra: np.ndarray, src_bgra: np.ndarray, cx: int, cy: int) -> None:
    """src をアルファブレンドで canvas に貼り付ける。両方 BGRA 前提。"""
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

    dst_crop[:, :, :3] = (src_crop[:, :, :3].astype(np.float32) * alpha +
                          dst_crop[:, :, :3].astype(np.float32) * inv).astype(np.uint8)

    dst_crop[:, :, 3:4] = np.clip(
        src_crop[:, :, 3:4].astype(np.float32) + dst_crop[:, :, 3:4].astype(np.float32) * inv,
        0, 255
    ).astype(np.uint8)

    canvas_bgra[dy0:dy1, dx0:dx1] = dst_crop


# -----------------------------
# 口形名 正規化
# -----------------------------
def normalize_mouth_label(mouth: str) -> str:
    if not mouth:
        return "closed"
    m = mouth.lower()
    if m in ("close", "mouth_close"):
        return "closed"
    return m


# -----------------------------
# atlas 読み込み（★expressionメタも素通し）
# -----------------------------
def load_atlas_index(atlas_json_path: str) -> Dict[str, Any]:
    """
    atlas.min.json の実体を内部形式に正規化する。

    - トップレベルに front/left30/right30/... がある旧形式もサポート
    - data["views"][view][mouth] で必ず参照できるようにする
    - expression_labels / expression_default などはそのまま返す
    """
    with open(atlas_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    views = data.get("views")
    if not isinstance(views, dict):
        views = {}
        for key, value in data.items():
            # front / left30 / right30 ... のようなビュー辞書を拾う
            if isinstance(value, dict) and "closed" in value:
                # mouthキーは小文字に統一
                views[key] = {str(m).lower(): path for m, path in value.items()}
        data["views"] = views
    else:
        # mouthキーを小文字に揃えておく
        norm_views = {}
        for vname, vdict in views.items():
            if isinstance(vdict, dict):
                norm_views[vname] = {str(m).lower(): path for m, path in vdict.items()}
        data["views"] = norm_views

    # view_rules はそのまま
    if "view_rules" not in data:
        data["view_rules"] = {}

    # fallback はそのまま
    if "fallback" not in data:
        data["fallback"] = {"view": "front", "mouth": "closed"}

    return data


# -----------------------------
# view 選択
# -----------------------------
def choose_view_from_yaw(yaw_deg: float, view_rules: Dict[str, Any]) -> str:
    left_max = float(view_rules.get("left30_max_yaw_deg", -12.0))
    right_min = float(view_rules.get("right30_min_yaw_deg", 12.0))
    if yaw_deg <= left_max:
        return "left30"
    if yaw_deg >= right_min:
        return "right30"
    return "front"


# -----------------------------
# sprite path 解決
# -----------------------------
def _resolve_base_sprite_path(atlas_idx: Dict[str, Any], view: str, mouth: str) -> tuple[str | None, bool]:
    """
    atlas_idx['views'][view][mouth] を引いて、相対パスを返す。
    見つからなければ fallback を使う。
    戻り値: (path_rel or None, used_fallback: bool)
    """
    views = atlas_idx.get("views", {})
    used_fallback = False

    view_dict = views.get(view)
    if isinstance(view_dict, dict):
        p = view_dict.get(mouth)
        if isinstance(p, str):
            return p, used_fallback

    # fallback view/mouth
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
    expression ラベルとベースPNGパスから、
    assets_dir/<expr>_<view>/<mouth_xxx.png> を導出する。

    - expression が None の場合や "normal" の場合は base_path_rel をそのまま返す
    - expression_labels に含まれないラベルなら無視して base_path_rel を返す
    """
    expr_default = str(atlas_idx.get("expression_default", "normal")).lower()
    expr = (expression or expr_default).lower()

    if expr in ("", "normal"):
        return base_path_rel

    labels = [str(x).lower() for x in atlas_idx.get("expression_labels", [])]
    if labels and expr not in labels:
        # 未知のラベル → normal と同じ扱い
        return base_path_rel

    base_name = os.path.basename(base_path_rel)
    expr_dir = f"{expr}_{view}"
    expr_path_rel = os.path.join(expr_dir, base_name)
    return expr_path_rel.replace("\\", "/")


# -----------------------------
# 変形（yaw/pitch/roll）
# -----------------------------
def pose_transform(src_bgra: np.ndarray, yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """
    最小版：roll のみ回転（例）。yaw/pitch は将来拡張。
    既存実装に合わせる（必要に応じてあなたの実装に置換OK）
    """
    # roll だけ反映する簡易実装（既存があるならそれを使ってOK）
    if abs(roll_deg) < 1e-6:
        return src_bgra

    h, w = src_bgra.shape[:2]
    center = (w / 2.0, h / 2.0)
    M = cv2.getRotationMatrix2D(center, roll_deg, 1.0)
    dst = cv2.warpAffine(src_bgra, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    return dst


# -----------------------------
# メインレンダラー
# -----------------------------
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

    # timeline index
    pose_idx = 0
    mouth_idx = 0

    fallback_frames = 0
    first_fallback_ms = None
    views_count: Dict[str, int] = {}

    prev_frame = None

    # helper: current pose/mouth by t_ms (hold-last)
    def _get_vals_at(t_ms: int, tl: list[dict[str, Any]], idx: int) -> tuple[dict[str, Any], int]:
        if not tl:
            return {}, idx
        while idx + 1 < len(tl) and int(tl[idx + 1].get("t_ms", 0)) <= t_ms:
            idx += 1
        return tl[idx], idx

    for i in range(total):
        ok, bgr = cap.read()
        if not ok:
            break

        step_ms = int(round(1000 / fps))
        t_ms = i * step_ms

        vals, pose_idx = _get_vals_at(t_ms, pose_timeline, pose_idx)

        # mouth/expression
        mvals = {}
        if mouth_timeline:
            mvals, mouth_idx = _get_vals_at(t_ms, mouth_timeline, mouth_idx)

        mouth = normalize_mouth_label(str(mvals.get("mouth", "closed")))
        expression = mvals.get("expression", None)

        # yaw/pitch/roll（deg）
        yaw = float(vals.get("yaw", vals.get("yaw_deg", 0.0)))
        pitch = float(vals.get("pitch", vals.get("pitch_deg", 0.0)))
        roll = float(vals.get("roll", vals.get("roll_deg", 0.0)))

        view = choose_view_from_yaw(yaw, view_rules)
        views = atlas_idx.get("views", {})
        views_count[view] = views_count.get(view, 0) + 1

        used_fallback = False
        src = None

        # 表情前提のベースPNGパスを解決
        base_path_rel, used_fallback_base = _resolve_base_sprite_path(atlas_idx, view, mouth)
        used_fallback = used_fallback or used_fallback_base

        if base_path_rel:
            # expression 用にパスを上書き
            expr_path_rel = _derive_expression_path(
                atlas_idx=atlas_idx,
                view=view,
                mouth=mouth,
                expression=expression,
                base_path_rel=base_path_rel,
            )

            # 実際の読み込み：まず expression 専用 → 無ければ normal(base) にフォールバック
            try:
                asset_path = os.path.join(assets_dir, expr_path_rel)
                src = _load_rgba(asset_path)
            except FileNotFoundError:
                try:
                    asset_path = os.path.join(assets_dir, base_path_rel)
                    src = _load_rgba(asset_path)
                    used_fallback = True  # 「表情」の意味ではフォールバック
                except FileNotFoundError:
                    src = None
        else:
            # baseもexpressionも読めなかった場合
            used_fallback = True

        frame = _ensure_bgra(bgr)

        if src is not None:
            # リサイズ（pose の scale を反映）
            # - target_h_ratio で「基準サイズ」を作り
            # - vals['scale']（例: 0.95〜1.05）で最終倍率を掛ける
            tgt_h_base = max(1, int(height * target_h_ratio))

            pose_scale = float(vals.get("scale", 1.0))
            # safety clamp（暴れ防止：必要なら調整）
            pose_scale = max(0.5, min(2.0, pose_scale))

            tgt_h = max(1, int(round(tgt_h_base * pose_scale)))

            # src→tgt のリサイズ倍率（ここは pose_scale とは別物）
            resize_ratio = tgt_h / src.shape[0]
            tgt_w = max(1, int(round(src.shape[1] * resize_ratio)))
            src_rs = cv2.resize(src, (tgt_w, tgt_h), interpolation=cv2.INTER_AREA)

            # ★ yaw/pitch/roll 変形をここで適用 ★
            src_rs = pose_transform(src_rs, yaw_deg=yaw, pitch_deg=pitch, roll_deg=roll)

            # paste position (apply tx/ty if present)
            tx = float(vals.get("tx", 0.0))
            ty = float(vals.get("ty", 0.0))

            # tx/ty clamp（画面外に飛ばないための安全策）
            max_tx = width * 0.45
            max_ty = height * 0.45
            tx = max(-max_tx, min(max_tx, tx))
            ty = max(-max_ty, min(max_ty, ty))

            # NOTE:
            # - current assumption: tx/ty are pixels
            # - if normalized later: cx += int(tx * width), cy += int(ty * height)
            cx = int(round((width // 2) + tx))
            cy = int(round((height * 0.58) + ty))

            _alpha_paste(frame, src_rs, cx, cy)

            # debug（必要なときだけ有効化してください）
            # if i % (fps * 2) == 0:
            #     print(f"[pose] t_ms={t_ms} yaw={yaw:.2f} tx={tx:.2f} ty={ty:.2f} scale={pose_scale:.3f} -> cx={cx} cy={cy} tgt_h={tgt_h}")

        if used_fallback:
            fallback_frames += 1
            if first_fallback_ms is None:
                first_fallback_ms = t_ms

        # ★ ここで per_frame_hook に BGRA フレームを渡す（M3.5 合成など）★
        if per_frame_hook is not None:
            frame = per_frame_hook(frame, t_ms, i)

        # クロスフェード（旧版互換）
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

    summary = {
        "fallback_frames": fallback_frames,
        "first_fallback_ms": first_fallback_ms,
        "views_count": views_count,
        "total_frames": int(total),
        "fps": int(fps),
        "out_mp4": out_mp4_path,
    }
    return summary


# ============================================
# M0 旧I/F互換ラッパー（bg_video不要版）
# ============================================
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
    背景MP4は読まず、width/height の空キャンバスにスプライトを貼ってMP4生成する。
    """

    atlas_idx = load_atlas_index(atlas_json_rel)
    view_rules = atlas_idx.get("view_rules", {})

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_mp4_path, fourcc, fps, (width, height))

    total_frames = int(round(duration_s * fps))
    step_ms = int(round(1000 / fps))  # t_msズレ対策（fps=25なら40ms）

    fallback_frames = 0
    first_fallback_ms = None
    views_count: Dict[str, int] = {}

    for i in range(total_frames):
        t_ms = i * step_ms
        vals = merged_value_fn(t_ms) or {}

        # --- mouth/expression ---
        mouth = normalize_mouth_label(str(vals.get("mouth", "closed")))
        expression = vals.get("expression", None)

        # --- pose (deg) ---
        yaw = float(vals.get("yaw", vals.get("yaw_deg", 0.0)))
        pitch = float(vals.get("pitch", vals.get("pitch_deg", 0.0)))
        roll = float(vals.get("roll", vals.get("roll_deg", 0.0)))

        view = choose_view_from_yaw(yaw, view_rules)
        views_count[view] = views_count.get(view, 0) + 1

        # --- sprite path resolve ---
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

            # try expression path -> fallback to base
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

        # --- background canvas (opaque black BGRA) ---
        frame = np.zeros((height, width, 4), dtype=np.uint8)
        frame[:, :, 3] = 255

        if src is not None:
            # --- scale (pose scale) ---
            target_h_ratio = float((transform_cfg or {}).get("target_h_ratio", 0.25))
            tgt_h_base = max(1, int(height * target_h_ratio))

            pose_scale = float(vals.get("scale", 1.0))
            pose_scale = max(0.5, min(2.0, pose_scale))  # clamp
            tgt_h = max(1, int(round(tgt_h_base * pose_scale)))

            resize_ratio = tgt_h / src.shape[0]
            tgt_w = max(1, int(round(src.shape[1] * resize_ratio)))
            src_rs = cv2.resize(src, (tgt_w, tgt_h), interpolation=cv2.INTER_AREA)

            # --- transform (yaw/pitch/roll) ---
            src_rs = pose_transform(src_rs, yaw_deg=yaw, pitch_deg=pitch, roll_deg=roll)

            # --- tx/ty (clamp) ---
            tx = float(vals.get("tx", 0.0))
            ty = float(vals.get("ty", 0.0))

            max_tx = width * 0.45
            max_ty = height * 0.45
            tx = max(-max_tx, min(max_tx, tx))
            ty = max(-max_ty, min(max_ty, ty))

            # paste center
            cy_base_ratio = float((transform_cfg or {}).get("cy_base_ratio", 0.58))
            cx = int(round((width // 2) + tx))
            cy = int(round((height * cy_base_ratio) + ty))

            _alpha_paste(frame, src_rs, cx, cy)

        if used_fallback:
            fallback_frames += 1
            if first_fallback_ms is None:
                first_fallback_ms = t_ms

        # write BGR
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
