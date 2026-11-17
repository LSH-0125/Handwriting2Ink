"""사진 한 장 -> 종이 좌표계 획. OCR 없이 페이지 전체를 한 번에 처리한다.

  python page_strokes.py --input photo.jpg --output_dir out/ [--overwrite]

단계
  1. 종이 네 모서리 검출 -> 직사각형으로 펴기 (책상 제거 + 원근 보정)
     종이를 못 찾으면(스캔본, 종이가 화면을 다 채움) 원본 그대로 쓴다.
  2. 조명 평탄화 후 이진화 (그림자에 강함)
  3. 잉크답지 않은 성분 제거 (두꺼운 덩어리, 작은 점, 테두리)
  4. 세선화 -> 획 추출 -> 점 단순화
  5. 결과: crop_stroke_composite_strokes.json (page 메타 포함) + strokes_preview.png

기존 OCR crop 방식(ocr_layout.py + render_strokes.py)과 같은 출력 계약을 지킨다.
워커는 page 메타가 있으면 종이 전체를 GoodNotes 페이지에 맞춰 원본 배치를 재현한다.

파라미터 기본값은 experiments/extraction 의 비교 실험으로 정했다.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from img_io import imread_safe, imwrite_safe  # noqa: E402
from preprocess import (BinarizeParams, FilterParams, binarize, estimate_background,  # noqa: E402
                        filter_components, paper_quad, rectify, simplify_stroke, to_gray)
from skeletonizer import skeletonize_zhang  # noqa: E402
from stroke_extractor import extract_strokes  # noqa: E402

OUTPUT_NAME = "crop_stroke_composite_strokes.json"
PREVIEW_NAME = "strokes_preview.png"

# ---- 실험으로 정한 기본값 (experiments/extraction/README.md 참조) ----
DEFAULTS = dict(
    page_max=2000,          # 작업 해상도(긴 변)
    method="norm_otsu",     # 조명 평탄화 + Otsu
    bg_ksize=41,            # 배경 추정 창
    floor=0.12,             # 정규화 어두움 최소 임계
    # stageC: 18 은 굵은 펜 획까지 지워 F1 0.946, 24 이상이면 0.973 (손실 없음).
    # 32 는 그림자 덩어리만 거르는 안전장치로 남긴다.
    max_thickness=32.0,     # 이보다 두꺼운 성분은 잉크 아님(그림자/칠)
    min_area=6,             # stageC: 0~6 최고, 12 부터 작은 이미지에서 손실
    drop_border=False,
    smooth=5,               # 획 이동평균 (LSH 개선분)
    merge_angle=60,
    min_len=3,
    rdp=0.8,                # 점 단순화 허용 오차(px, 종이 좌표)
    max_seg=12.0,           # 단순화 후 최대 구간 길이
)


def extract_page(img: np.ndarray, p: dict) -> tuple[list[np.ndarray], dict]:
    """종이 좌표계 획 목록과 page 메타를 돌려준다."""
    meta = {"rectified": False}
    q = paper_quad(img) if p.get("rectify", True) else None
    if q is not None:
        img, _M = rectify(img, q)
        meta["rectified"] = True
        meta["quad"] = np.round(q, 1).tolist()

    H0, W0 = img.shape[:2]
    f = min(1.0, p["page_max"] / max(H0, W0))
    work = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img
    g = to_gray(work)

    bp = BinarizeParams(method=p["method"], bg_ksize=p["bg_ksize"], floor=p["floor"])
    bg = estimate_background(g, bp.bg_ksize) if bp.method.startswith("norm") else None
    binary = binarize(g, bp, bg)
    binary = filter_components(binary, FilterParams(min_area=p["min_area"], max_thickness=p["max_thickness"],
                                                    drop_border=p["drop_border"]))
    skel, _, _ = skeletonize_zhang(binary)
    with contextlib.redirect_stdout(io.StringIO()):
        strokes = extract_strokes(skel, min_stroke_length=p["min_len"], merge_angle=p["merge_angle"],
                                  image_gray=g, smooth_window=p["smooth"])
    out = []
    for s in strokes:
        if len(s) == 0:
            continue
        pts = s.astype(np.float32) / f
        if p["rdp"] > 0 or p["max_seg"] > 0:
            pts = simplify_stroke(pts, p["rdp"], p["max_seg"])
        out.append(pts)
    meta.update(width=float(W0), height=float(H0))
    return out, meta, binary, work


def render_preview(strokes, width: float, height: float, max_side: int = 1400) -> np.ndarray:
    s = max_side / max(width, height)
    canvas = np.full((int(height * s) + 1, int(width * s) + 1, 3), 255, np.uint8)
    for p in strokes:
        pts = np.round(p * s).astype(np.int32).reshape(-1, 1, 2)
        if len(pts) == 1:
            cv2.circle(canvas, (int(pts[0, 0, 0]), int(pts[0, 0, 1])), 1, (30, 30, 30), -1)
        else:
            cv2.polylines(canvas, [pts], False, (30, 30, 30), 1, cv2.LINE_AA)
    return canvas


def main() -> None:
    ap = argparse.ArgumentParser(description="사진 -> 종이 좌표계 획 (OCR 없음)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--no_rectify", action="store_true")
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k}", type=type(v) if not isinstance(v, bool) else lambda x: x.lower() == "true", default=v)
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / OUTPUT_NAME).exists() and not args.overwrite:
        raise FileExistsError(f"이미 결과가 있습니다: {out_dir / OUTPUT_NAME} (--overwrite)")

    img = imread_safe(args.input)
    if img is None:
        raise FileNotFoundError(f"이미지를 읽을 수 없습니다: {args.input}")

    params = {k: getattr(args, k) for k in DEFAULTS} | {"rectify": not args.no_rectify}
    t0 = time.time()
    strokes, meta, _binary, _work = extract_page(img, params)
    elapsed = time.time() - t0

    payload = {
        "input_path": str(Path(args.input).resolve()),
        "mode": "page",
        "page": {"width": meta["width"], "height": meta["height"], "rectified": meta["rectified"]},
        "coordinate_system": {
            "global_points": "page(종이) 좌표계 px. rectified=true 면 원근 보정된 종이 기준",
            "point_order": "[x, y]",
        },
        "params": params,
        "elapsed_sec": round(elapsed, 2),
        "total_stroke_count": len(strokes),
        "total_point_count": int(sum(len(s) for s in strokes)),
        "strokes": [{"global_points": np.round(s, 1).tolist()} for s in strokes],
    }
    (out_dir / OUTPUT_NAME).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    imwrite_safe(out_dir / PREVIEW_NAME, render_preview(strokes, meta["width"], meta["height"]))
    print(f"[page] 종이 보정={meta['rectified']}  크기={meta['width']:.0f}x{meta['height']:.0f}")
    print(f"[page] 획 {len(strokes)}  점 {payload['total_point_count']}  {elapsed:.1f}초")
    print(f"[page] 저장: {out_dir / OUTPUT_NAME}")


if __name__ == "__main__":
    main()
