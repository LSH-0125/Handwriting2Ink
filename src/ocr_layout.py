"""
OCR 기반 text/shape 레이아웃 분리
=======================================

PaddleOCR mobile 모델을 사용해 텍스트 박스를 검출하고,
OCR 결과를 제외한 전경 영역을 도형(shape) 후보로 분리합니다.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(PROJECT_ROOT / ".paddlex_cache"))
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".mplconfig"))
os.environ.setdefault("XDG_CACHE_HOME", str(PROJECT_ROOT / ".xdg_cache"))

import cv2
import numpy as np
from paddleocr import PaddleOCR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PaddleOCR 기반 text/shape 레이아웃 분리"
    )
    parser.add_argument("--input", required=True, help="입력 이미지 경로")
    parser.add_argument(
        "--output_dir",
        default=None,
        help="결과 저장 디렉토리 (기본: pilot_outputs/<입력파일명>)",
    )
    parser.add_argument(
        "--resize_max",
        type=int,
        default=1600,
        help="긴 변 기준 최대 리사이즈 길이. 0 이하면 원본 유지",
    )
    parser.add_argument(
        "--save_crops",
        action="store_true",
        help="text/shape crop 이미지를 함께 저장",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="디버그 마스크와 중간 산출물을 추가 저장",
    )
    return parser.parse_args()


def ensure_runtime_dirs() -> None:
    for name in (".paddlex_cache", ".mplconfig", ".xdg_cache"):
        (PROJECT_ROOT / name).mkdir(parents=True, exist_ok=True)


def resolve_output_dir(input_path: Path, output_dir_arg: str | None) -> Path:
    if output_dir_arg:
        output_dir = Path(output_dir_arg)
    else:
        output_dir = PROJECT_ROOT / "pilot_outputs" / input_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def load_image(path: Path, resize_max: int) -> tuple[np.ndarray, float]:
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(f"이미지를 로드할 수 없습니다: {path}")

    scale = 1.0
    if resize_max and resize_max > 0:
        h, w = image.shape[:2]
        scale = min(1.0, resize_max / max(h, w))
        if scale < 1.0:
            image = cv2.resize(
                image,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )
    return image, scale


def create_ocr_engine() -> PaddleOCR:
    return PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_det_limit_side_len=1600,
        text_det_box_thresh=0.35,
        text_rec_score_thresh=0.0,
    )


def to_int_polygon(points: Iterable[Iterable[float]]) -> list[list[int]]:
    polygon = []
    for x, y in points:
        polygon.append([int(round(float(x))), int(round(float(y)))])
    return polygon


def polygon_to_bbox(polygon: list[list[int]]) -> list[int]:
    pts = np.array(polygon, dtype=np.int32)
    x, y, w, h = cv2.boundingRect(pts)
    return [int(x), int(y), int(w), int(h)]


def pad_bbox(bbox: list[int], pad: int, width: int, height: int) -> list[int]:
    x, y, w, h = bbox
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(width, x + w + pad)
    y1 = min(height, y + h + pad)
    return [x0, y0, x1 - x0, y1 - y0]


def bbox_intersects(a: list[int], b: list[int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw < bx
        or bx + bw < ax
        or ay + ah < by
        or by + bh < ay
    )


def overlap_ratio(start_a: int, end_a: int, start_b: int, end_b: int) -> float:
    overlap = max(0, min(end_a, end_b) - max(start_a, start_b))
    length = max(1, min(end_a - start_a, end_b - start_b))
    return overlap / float(length)


def gap_distance(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    if end_a < start_b:
        return start_b - end_a
    if end_b < start_a:
        return start_a - end_b
    return 0


def should_merge_text_boxes(a: list[int], b: list[int], median_height: int) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    a_x1, a_y1 = ax + aw, ay + ah
    b_x1, b_y1 = bx + bw, by + bh

    vertical_overlap = overlap_ratio(ay, a_y1, by, b_y1)
    horizontal_overlap = overlap_ratio(ax, a_x1, bx, b_x1)
    horizontal_gap = gap_distance(ax, a_x1, bx, b_x1)
    vertical_gap = gap_distance(ay, a_y1, by, b_y1)

    same_line = vertical_overlap >= 0.45 and horizontal_gap <= max(18, int(median_height * 1.6))
    stacked_piece = horizontal_overlap >= 0.55 and vertical_gap <= max(10, int(median_height * 0.7))
    return same_line or stacked_piece


def extract_text_regions(ocr_result: object, width: int, height: int) -> tuple[list[dict], list[dict], int]:
    result_json = getattr(ocr_result, "json", {})
    result_data = result_json.get("res", result_json)
    dt_polys = result_data.get("dt_polys", [])
    rec_texts = result_data.get("rec_texts", [])
    rec_scores = result_data.get("rec_scores", [])

    raw_regions: list[dict] = []
    heights: list[int] = []
    for idx, polygon_like in enumerate(dt_polys):
        polygon = to_int_polygon(polygon_like)
        bbox = polygon_to_bbox(polygon)
        heights.append(max(1, bbox[3]))
        raw_regions.append(
            {
                "id": idx,
                "type": "text",
                "source": "ocr_raw",
                "bbox": bbox,
                "polygon": polygon,
                "score": float(rec_scores[idx]) if idx < len(rec_scores) else None,
                "text": rec_texts[idx] if idx < len(rec_texts) else "",
            }
        )

    if not raw_regions:
        return [], [], 12

    median_height = int(np.median(heights))
    adjacency = [[] for _ in raw_regions]
    for i in range(len(raw_regions)):
        for j in range(i + 1, len(raw_regions)):
            if should_merge_text_boxes(
                raw_regions[i]["bbox"], raw_regions[j]["bbox"], median_height
            ):
                adjacency[i].append(j)
                adjacency[j].append(i)

    merged_regions: list[dict] = []
    visited = [False] * len(raw_regions)
    for idx in range(len(raw_regions)):
        if visited[idx]:
            continue
        stack = [idx]
        component = []
        visited[idx] = True
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    stack.append(neighbor)

        x0 = min(raw_regions[i]["bbox"][0] for i in component)
        y0 = min(raw_regions[i]["bbox"][1] for i in component)
        x1 = max(raw_regions[i]["bbox"][0] + raw_regions[i]["bbox"][2] for i in component)
        y1 = max(raw_regions[i]["bbox"][1] + raw_regions[i]["bbox"][3] for i in component)
        bbox = pad_bbox([x0, y0, x1 - x0, y1 - y0], max(4, median_height // 5), width, height)
        polygon = [
            [bbox[0], bbox[1]],
            [bbox[0] + bbox[2], bbox[1]],
            [bbox[0] + bbox[2], bbox[1] + bbox[3]],
            [bbox[0], bbox[1] + bbox[3]],
        ]
        merged_text = " ".join(
            raw_regions[i]["text"].strip() for i in component if raw_regions[i]["text"].strip()
        )
        scores = [raw_regions[i]["score"] for i in component if raw_regions[i]["score"] is not None]
        merged_regions.append(
            {
                "id": len(merged_regions),
                "type": "text",
                "source": "ocr_merged",
                "bbox": bbox,
                "polygon": polygon,
                "score": float(np.mean(scores)) if scores else None,
                "text": merged_text,
                "raw_ids": component,
            }
        )

    return raw_regions, merged_regions, median_height


def build_text_mask(shape: tuple[int, int], regions: list[dict], dilation_size: int) -> tuple[np.ndarray, np.ndarray]:
    height, width = shape
    raw_mask = np.zeros((height, width), dtype=np.uint8)
    for region in regions:
        polygon = np.array(region["polygon"], dtype=np.int32)
        cv2.fillPoly(raw_mask, [polygon], 255)

    if dilation_size % 2 == 0:
        dilation_size += 1
    kernel = np.ones((max(3, dilation_size), max(3, dilation_size)), dtype=np.uint8)
    dilated = cv2.dilate(raw_mask, kernel, iterations=1)
    return raw_mask, dilated


def build_content_roi_mask(
    shape: tuple[int, int], merged_text_regions: list[dict], pad: int
) -> np.ndarray:
    height, width = shape
    mask = np.zeros((height, width), dtype=np.uint8)
    if not merged_text_regions:
        mask[:] = 255
        return mask

    x0 = min(region["bbox"][0] for region in merged_text_regions)
    y0 = min(region["bbox"][1] for region in merged_text_regions)
    x1 = max(region["bbox"][0] + region["bbox"][2] for region in merged_text_regions)
    y1 = max(region["bbox"][1] + region["bbox"][3] for region in merged_text_regions)
    bbox = pad_bbox([x0, y0, x1 - x0, y1 - y0], pad, width, height)
    x, y, w, h = bbox
    mask[y : y + h, x : x + w] = 255
    return mask


def build_foreground_mask(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    otsu_mask = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    kernel_size = max(15, ((max(image.shape[:2]) // 50) | 1))
    blackhat_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
    )
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, blackhat_kernel)
    blackhat = cv2.GaussianBlur(blackhat, (5, 5), 0)
    blackhat_mask = cv2.threshold(
        blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]
    adaptive_mask = cv2.adaptiveThreshold(
        blackhat,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        -3,
    )
    mask = cv2.bitwise_and(otsu_mask, cv2.bitwise_or(blackhat_mask, adaptive_mask))
    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def build_document_mask(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (9, 9), 0)
    bright_mask = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]
    bright_mask = cv2.morphologyEx(
        bright_mask, cv2.MORPH_CLOSE, np.ones((7, 7), dtype=np.uint8)
    )
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        bright_mask, connectivity=8
    )
    if num_labels <= 1:
        return np.ones_like(bright_mask) * 255

    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_index = int(np.argmax(areas)) + 1
    largest_area = int(stats[largest_index, cv2.CC_STAT_AREA])
    if largest_area < image.shape[0] * image.shape[1] * 0.15:
        return np.ones_like(bright_mask) * 255

    document_mask = np.zeros_like(bright_mask)
    document_mask[labels == largest_index] = 255
    document_mask = cv2.morphologyEx(
        document_mask, cv2.MORPH_CLOSE, np.ones((9, 9), dtype=np.uint8)
    )
    return document_mask


def extract_shape_regions(shape_mask: np.ndarray) -> list[dict]:
    height, width = shape_mask.shape[:2]
    grouped_mask = cv2.morphologyEx(
        shape_mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8)
    )
    grouped_mask = cv2.morphologyEx(
        grouped_mask, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8)
    )
    grouped_mask = cv2.dilate(grouped_mask, np.ones((3, 3), dtype=np.uint8), iterations=1)
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(grouped_mask, connectivity=8)
    min_area = max(120, int(height * width * 0.00025))

    regions: list[dict] = []
    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area < min_area:
            continue
        if min(w, h) < 8:
            continue
        if w * h <= 0:
            continue
        fill_ratio = area / float(w * h)
        if fill_ratio > 0.98 and (w > width * 0.9 or h > height * 0.9):
            continue
        bbox = [int(x), int(y), int(w), int(h)]
        polygon = [
            [bbox[0], bbox[1]],
            [bbox[0] + bbox[2], bbox[1]],
            [bbox[0] + bbox[2], bbox[1] + bbox[3]],
            [bbox[0], bbox[1] + bbox[3]],
        ]
        regions.append(
            {
                "id": len(regions),
                "type": "shape",
                "source": "cv_component",
                "bbox": bbox,
                "polygon": polygon,
                "score": None,
                "text": "",
                "area": int(area),
                "fill_ratio": round(fill_ratio, 4),
            }
        )
    return regions


def draw_overlay(image: np.ndarray, raw_text_regions: list[dict], merged_text_regions: list[dict], shape_regions: list[dict]) -> np.ndarray:
    overlay = image.copy()
    for region in raw_text_regions:
        polygon = np.array(region["polygon"], dtype=np.int32)
        cv2.polylines(overlay, [polygon], True, (255, 120, 0), 2, lineType=cv2.LINE_AA)

    for region in merged_text_regions:
        x, y, w, h = region["bbox"]
        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 200, 0), 2, lineType=cv2.LINE_AA)

    for region in shape_regions:
        x, y, w, h = region["bbox"]
        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 0, 255), 2, lineType=cv2.LINE_AA)

    legend = [
        ("raw OCR polygon", (255, 120, 0)),
        ("merged text region", (0, 200, 0)),
        ("shape region", (0, 0, 255)),
    ]
    y = 24
    for label, color in legend:
        cv2.rectangle(overlay, (16, y - 12), (32, y + 4), color, -1)
        cv2.putText(
            overlay,
            label,
            (40, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (40, 40, 40),
            1,
            cv2.LINE_AA,
        )
        y += 24
    return overlay


def save_mask(path: Path, mask: np.ndarray) -> None:
    cv2.imwrite(str(path), mask)


def save_crops(image: np.ndarray, regions: list[dict], region_type: str, output_dir: Path) -> None:
    crops_dir = output_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    for index, region in enumerate(regions, start=1):
        x, y, w, h = region["bbox"]
        crop = image[y : y + h, x : x + w]
        cv2.imwrite(str(crops_dir / f"{region_type}_{index:03d}.png"), crop)


def save_regions_json(
    output_dir: Path,
    input_path: Path,
    scale: float,
    raw_text_regions: list[dict],
    merged_text_regions: list[dict],
    shape_regions: list[dict],
) -> None:
    payload = {
        "input_path": str(input_path),
        "scale": scale,
        "regions": raw_text_regions + merged_text_regions + shape_regions,
    }
    with open(output_dir / "regions.json", "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {input_path}")

    ensure_runtime_dirs()
    output_dir = resolve_output_dir(input_path, args.output_dir)
    image, scale = load_image(input_path, args.resize_max)
    height, width = image.shape[:2]

    print(f"[OCR 파일럿] 입력: {input_path}")
    print(f"[OCR 파일럿] 출력 디렉토리: {output_dir}")
    print(f"[OCR 파일럿] 처리 해상도: {width}x{height} (scale={scale:.4f})")

    ocr = create_ocr_engine()
    predictions = ocr.predict(image)
    if not predictions:
        raise RuntimeError("PaddleOCR가 예측 결과를 반환하지 않았습니다.")

    raw_text_regions, merged_text_regions, median_height = extract_text_regions(
        predictions[0], width, height
    )
    dilation_size = max(5, int(round(median_height * 0.35)))
    raw_text_mask, text_mask = build_text_mask((height, width), merged_text_regions, dilation_size)
    foreground_mask = build_foreground_mask(image)
    document_mask = build_document_mask(image)
    content_roi_mask = build_content_roi_mask(
        (height, width), merged_text_regions, max(40, int(median_height * 4.5))
    )
    shape_candidate_mask = cv2.bitwise_and(foreground_mask, content_roi_mask)
    shape_candidate_mask = cv2.bitwise_and(shape_candidate_mask, document_mask)
    shape_candidate_mask = cv2.bitwise_and(
        shape_candidate_mask, cv2.bitwise_not(text_mask)
    )
    shape_regions = extract_shape_regions(shape_candidate_mask)

    overlay = draw_overlay(image, raw_text_regions, merged_text_regions, shape_regions)
    cv2.imwrite(str(output_dir / "layout_overlay.png"), overlay)
    save_mask(output_dir / "text_mask.png", text_mask)
    save_mask(output_dir / "shape_mask.png", shape_candidate_mask)
    save_regions_json(
        output_dir,
        input_path,
        scale,
        raw_text_regions,
        merged_text_regions,
        shape_regions,
    )

    if args.save_crops:
        save_crops(image, merged_text_regions, "text", output_dir)
        save_crops(image, shape_regions, "shape", output_dir)

    if args.debug:
        save_mask(output_dir / "debug_foreground_mask.png", foreground_mask)
        save_mask(output_dir / "debug_document_mask.png", document_mask)
        save_mask(output_dir / "debug_raw_text_mask.png", raw_text_mask)
        save_mask(output_dir / "debug_text_mask.png", text_mask)
        save_mask(output_dir / "debug_content_roi_mask.png", content_roi_mask)
        save_mask(output_dir / "debug_shape_candidate_mask.png", shape_candidate_mask)

    print(f"[OCR 파일럿] raw text regions: {len(raw_text_regions)}")
    print(f"[OCR 파일럿] merged text regions: {len(merged_text_regions)}")
    print(f"[OCR 파일럿] shape regions: {len(shape_regions)}")


if __name__ == "__main__":
    main()
