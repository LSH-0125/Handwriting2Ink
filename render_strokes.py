import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".mplconfig"))
os.environ.setdefault("XDG_CACHE_HOME", str(PROJECT_ROOT / ".xdg_cache"))

from simulate_drawing import save_black_strokes_image, save_result_image
from skeletonizer import load_and_preprocess, skeletonize_zhang
from stroke_extractor import extract_strokes


def parse_args():
    parser = argparse.ArgumentParser(
        description="OCR text와 shape region을 stroke로 변환한 뒤 원래 위치에 맞춰 전체 캔버스에 렌더링합니다."
    )
    parser.add_argument(
        "--pilot_dir",
        required=True,
        help="OCR layout 출력 디렉토리 경로",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="3패널 결과 이미지 경로 (기본: <pilot_dir>/crop_stroke_composite_result.png)",
    )
    parser.add_argument(
        "--output_black",
        default=None,
        help="흑백 stroke 이미지 경로 (기본: <pilot_dir>/crop_stroke_composite_black.png)",
    )
    parser.add_argument(
        "--region_source",
        choices=("ocr_merged", "ocr_raw"),
        default="ocr_merged",
        help="stroke로 변환할 text region 소스. shape region은 항상 함께 포함됩니다. (기본: ocr_merged)",
    )
    parser.add_argument(
        "--crop_scale",
        type=float,
        default=2.0,
        help="skeletonize 전에 각 crop을 확대할 배율 (기본: 2.0, 1.0이면 비활성)",
    )
    parser.add_argument(
        "--black_thickness",
        type=int,
        default=1,
        help="흑백 stroke 이미지의 선 두께 (기본: 1)",
    )
    parser.add_argument(
        "--result_thickness",
        type=int,
        default=None,
        help="3패널 결과 이미지의 컬러 stroke 선 두께 (기본: 캔버스 크기 기반 자동값)",
    )
    parser.add_argument(
        "--save_merged_debug",
        action="store_true",
        help="crop들을 원래 위치에 병합한 이미지와 병합 이미지 기준 전처리/stroke 결과를 저장",
    )
    parser.add_argument(
        "--merged_debug_mode",
        choices=("text", "all"),
        default="text",
        help="병합 디버그에 사용할 crop 범위: text 또는 all(text+shape) (기본: text)",
    )
    parser.add_argument(
        "--save_crop_debug",
        action="store_true",
        help="각 crop별 binary, skeleton, binary+skeleton overlay 이미지를 저장",
    )
    parser.add_argument(
        "--crop_debug_mode",
        choices=("text", "all"),
        default="text",
        help="crop 디버그에 사용할 crop 범위: text 또는 all(text+shape) (기본: text)",
    )
    parser.add_argument(
        "--save_stroke_data",
        action="store_true",
        help="복원된 stroke를 좌표열 JSON으로 저장",
    )
    parser.add_argument(
        "--stroke_data_output",
        default=None,
        help="stroke 좌표열 JSON 경로 (기본: <pilot_dir>/crop_stroke_composite_strokes.json)",
    )
    return parser.parse_args()


def load_regions(pilot_dir: Path, region_source: str):
    data = json.loads((pilot_dir / "regions.json").read_text(encoding="utf-8"))
    regions = [
        region
        for region in data["regions"]
        if region["type"] == "text" and region["source"] == region_source
    ]
    return data, regions


def make_text_crop_spec(region: dict, index: int, pilot_dir: Path, region_source: str):
    crop_path = None
    if region_source == "ocr_merged":
        crop_path = pilot_dir / "crops" / f"text_{index:03d}.png"

    return {
        "type": "text",
        "source": region_source,
        "index": index,
        "bbox": region["bbox"],
        "crop_path": crop_path,
        "region": region,
    }


def make_shape_crop_spec(region: dict, index: int, pilot_dir: Path):
    return {
        "type": "shape",
        "source": "cv_component",
        "index": index,
        "bbox": region["bbox"],
        "crop_path": pilot_dir / "crops" / f"shape_{index:03d}.png",
        "region": region,
    }


def collect_target_crop_specs(data: dict, pilot_dir: Path, region_source: str):
    specs = []
    text_regions = [
        region
        for region in data["regions"]
        if region["type"] == "text" and region["source"] == region_source
    ]
    for index, region in enumerate(text_regions, start=1):
        specs.append(make_text_crop_spec(region, index, pilot_dir, region_source))

    shape_regions = [region for region in data["regions"] if region["type"] == "shape"]
    for index, region in enumerate(shape_regions, start=1):
        specs.append(make_shape_crop_spec(region, index, pilot_dir))

    return specs


def collect_crop_specs(data: dict, pilot_dir: Path, mode: str, region_source: str):
    specs = []
    text_regions = [
        region
        for region in data["regions"]
        if region["type"] == "text" and region["source"] == region_source
    ]
    for index, region in enumerate(text_regions, start=1):
        specs.append(make_text_crop_spec(region, index, pilot_dir, region_source))

    if mode == "all":
        shape_regions = [region for region in data["regions"] if region["type"] == "shape"]
        for index, region in enumerate(shape_regions, start=1):
            specs.append(make_shape_crop_spec(region, index, pilot_dir))

    return specs


def resolve_input_image(input_path_value: str) -> Path:
    raw_path = Path(input_path_value)
    candidates = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(PROJECT_ROOT / raw_path)
        candidates.append(PROJECT_ROOT / "images" / "inputs" / raw_path.name)

    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate

    raise FileNotFoundError(f"원본 입력 이미지를 찾을 수 없습니다: {input_path_value}")


def read_image_unicode_safe(path: Path) -> np.ndarray | None:
    """Read an image reliably on Windows paths that may include non-ASCII chars."""
    image = cv2.imread(str(path))
    if image is not None:
        return image

    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def load_reference_image(data: dict) -> np.ndarray:
    input_path = resolve_input_image(data["input_path"])
    image = read_image_unicode_safe(input_path)
    if image is None:
        raise FileNotFoundError(f"원본 입력 이미지를 읽을 수 없습니다: {input_path}")

    scale = float(data.get("scale", 1.0))
    if scale < 0.999:
        image = cv2.resize(
            image,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA,
        )
    return image


def crop_image_by_bbox(image: np.ndarray, bbox):
    x, y, w, h = [int(value) for value in bbox]
    if w <= 0 or h <= 0:
        raise ValueError(f"잘못된 bbox입니다: {bbox}")

    image_h, image_w = image.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(image_w, x + w), min(image_h, y + h)
    if x0 >= x1 or y0 >= y1:
        raise ValueError(f"이미지 범위를 벗어난 bbox입니다: {bbox}")
    return image[y0:y1, x0:x1]


def read_crop_image(spec: dict, reference_image: np.ndarray):
    crop_path = spec.get("crop_path")
    if crop_path and Path(crop_path).exists():
        crop_image = read_image_unicode_safe(Path(crop_path))
        if crop_image is None:
            raise FileNotFoundError(f"crop 이미지를 읽을 수 없습니다: {crop_path}")
        return crop_image

    return crop_image_by_bbox(reference_image, spec["bbox"])


def crop_path_to_string(spec: dict):
    crop_path = spec.get("crop_path")
    return str(crop_path) if crop_path else None


def upscale_crop(crop_image: np.ndarray, scale: float) -> np.ndarray:
    if scale <= 1.0:
        return crop_image
    return cv2.resize(
        crop_image,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )


def downscale_strokes(strokes, scale: float):
    if scale <= 1.0:
        return strokes

    downscaled = []
    for stroke in strokes:
        if len(stroke) == 0:
            continue
        local_points = np.rint(stroke.astype(np.float32) / scale).astype(np.int32)
        if len(local_points) > 0:
            downscaled.append(local_points)
    return downscaled


def preprocess_crop_image(crop_image: np.ndarray, crop_scale: float):
    scaled_crop = upscale_crop(crop_image, crop_scale)
    _, gray, binary = load_and_preprocess(scaled_crop)
    skeleton, _, _ = skeletonize_zhang(binary)
    return scaled_crop, gray, binary, skeleton


def preprocess_crop_spec(spec: dict, reference_image: np.ndarray, crop_scale: float):
    crop_image = read_crop_image(spec, reference_image)
    scaled_crop, gray, binary, skeleton = preprocess_crop_image(crop_image, crop_scale)
    return crop_image, scaled_crop, gray, binary, skeleton


def extract_crop_strokes(spec: dict, reference_image: np.ndarray, crop_scale: float):
    _, _, gray, binary, skeleton = preprocess_crop_spec(spec, reference_image, crop_scale)
    strokes = extract_strokes(skeleton, image_gray=gray)
    return downscale_strokes(strokes, crop_scale)


def create_skeleton_overlay(binary: np.ndarray, skeleton: np.ndarray):
    binary_preview = 255 - binary
    overlay = cv2.cvtColor(binary_preview, cv2.COLOR_GRAY2BGR)
    skeleton_visible = cv2.dilate(
        skeleton,
        np.ones((3, 3), dtype=np.uint8),
        iterations=1,
    )
    overlay[skeleton_visible > 0] = (0, 0, 255)
    return overlay


def crop_debug_prefix(spec: dict):
    if spec["type"] == "shape":
        return f"shape_{spec['index']:03d}"
    source_label = "raw" if spec.get("source") == "ocr_raw" else "merged"
    return f"text_{source_label}_{spec['index']:03d}"


def save_crop_debug_outputs(
    pilot_dir: Path,
    crop_specs,
    reference_image: np.ndarray,
    crop_scale: float,
    region_source: str,
):
    debug_dir = pilot_dir / f"crop_debug_{region_source}_scale{crop_scale:g}"
    debug_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for spec in crop_specs:
        prefix = crop_debug_prefix(spec)
        try:
            _, scaled_crop, gray, binary, skeleton = preprocess_crop_spec(
                spec,
                reference_image,
                crop_scale,
            )
        except FileNotFoundError as exc:
            print(f"[warn] {exc}")
            continue

        strokes = extract_strokes(skeleton, image_gray=gray)
        binary_preview = 255 - binary
        skeleton_preview = 255 - skeleton
        overlay = create_skeleton_overlay(binary, skeleton)

        scaled_crop_path = debug_dir / f"{prefix}_scaled_crop.png"
        binary_path = debug_dir / f"{prefix}_binary.png"
        skeleton_path = debug_dir / f"{prefix}_skeleton.png"
        overlay_path = debug_dir / f"{prefix}_skeleton_overlay.png"

        cv2.imwrite(str(scaled_crop_path), scaled_crop)
        cv2.imwrite(str(binary_path), binary_preview)
        cv2.imwrite(str(skeleton_path), skeleton_preview)
        cv2.imwrite(str(overlay_path), overlay)

        summary.append(
            {
                "type": spec["type"],
                "index": spec["index"],
                "bbox": spec["bbox"],
                "crop_path": crop_path_to_string(spec),
                "source": spec.get("source"),
                "crop_scale": crop_scale,
                "scaled_size": [int(scaled_crop.shape[1]), int(scaled_crop.shape[0])],
                "stroke_count": len(strokes),
                "scaled_crop": str(scaled_crop_path),
                "binary": str(binary_path),
                "skeleton": str(skeleton_path),
                "skeleton_overlay": str(overlay_path),
            }
        )
        print(
            f"[crop debug] {prefix} bbox={spec['bbox']} strokes={len(strokes)} saved={overlay_path}"
        )

    summary_path = debug_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "region_source": region_source,
                "crop_scale": crop_scale,
                "crop_count": len(summary),
                "crops": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"crop debug summary saved: {summary_path}")
    return debug_dir


def paste_crop(canvas: np.ndarray, crop: np.ndarray, bbox):
    x, y, w, h = [int(value) for value in bbox]
    if w <= 0 or h <= 0:
        return

    if crop.ndim == 2:
        crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    if crop.shape[1] != w or crop.shape[0] != h:
        crop = cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA)

    canvas_h, canvas_w = canvas.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(canvas_w, x + w), min(canvas_h, y + h)
    if x0 >= x1 or y0 >= y1:
        return

    crop_x0, crop_y0 = x0 - x, y0 - y
    crop_x1, crop_y1 = crop_x0 + (x1 - x0), crop_y0 + (y1 - y0)
    canvas[y0:y1, x0:x1] = crop[crop_y0:crop_y1, crop_x0:crop_x1]


def build_merged_crop_canvas(reference_image: np.ndarray, crop_specs):
    canvas = np.ones_like(reference_image) * 255
    pasted = []
    for spec in crop_specs:
        try:
            crop = read_crop_image(spec, reference_image)
        except (FileNotFoundError, ValueError) as exc:
            print(f"[warn] {exc}")
            continue
        paste_crop(canvas, crop, spec["bbox"])
        pasted.append(
            {
                "type": spec["type"],
                "index": spec["index"],
                "bbox": spec["bbox"],
                "crop_path": crop_path_to_string(spec),
                "source": spec.get("source"),
            }
        )
    return canvas, pasted


def save_merged_debug_outputs(
    pilot_dir: Path,
    reference_image: np.ndarray,
    crop_specs,
    mode: str,
    region_source: str,
    black_thickness: int,
    result_thickness,
):
    merged_canvas, pasted = build_merged_crop_canvas(reference_image, crop_specs)
    prefix_label = mode if region_source == "ocr_merged" else f"{region_source}_{mode}"
    output_prefix = pilot_dir / f"merged_{prefix_label}_crops"

    merged_path = output_prefix.with_name(f"{output_prefix.name}_canvas.png")
    binary_path = output_prefix.with_name(f"{output_prefix.name}_binary.png")
    skeleton_path = output_prefix.with_name(f"{output_prefix.name}_skeleton.png")
    stroke_result_path = output_prefix.with_name(f"{output_prefix.name}_stroke_result.png")
    stroke_black_path = output_prefix.with_name(f"{output_prefix.name}_stroke_black.png")
    summary_path = output_prefix.with_name(f"{output_prefix.name}_summary.json")

    cv2.imwrite(str(merged_path), merged_canvas)
    _, gray, binary = load_and_preprocess(merged_canvas)
    skeleton, _, _ = skeletonize_zhang(binary)
    strokes = extract_strokes(skeleton, image_gray=gray)

    cv2.imwrite(str(binary_path), 255 - binary)
    cv2.imwrite(str(skeleton_path), 255 - skeleton)
    save_result_image(
        strokes,
        merged_canvas,
        str(stroke_result_path),
        thickness=result_thickness,
    )
    save_black_strokes_image(
        strokes,
        merged_canvas,
        str(stroke_black_path),
        thickness=black_thickness,
    )

    summary_path.write_text(
        json.dumps(
            {
                "mode": mode,
                "region_source": region_source,
                "merged_canvas": str(merged_path),
                "binary_preview": str(binary_path),
                "skeleton_preview": str(skeleton_path),
                "stroke_result": str(stroke_result_path),
                "stroke_black": str(stroke_black_path),
                "crop_count": len(pasted),
                "stroke_count": len(strokes),
                "crops": pasted,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"merged crop canvas saved: {merged_path}")
    print(f"merged binary preview saved: {binary_path}")
    print(f"merged skeleton preview saved: {skeleton_path}")
    print(f"merged stroke summary saved: {summary_path}")


def offset_strokes(strokes, bbox):
    x, y, _, _ = bbox
    offset = np.array([x, y], dtype=np.int32)
    shifted = []
    for stroke in strokes:
        if len(stroke) == 0:
            continue
        shifted.append(stroke.astype(np.int32) + offset)
    return shifted


def stroke_to_points(stroke):
    return [[int(x), int(y)] for x, y in stroke.astype(np.int32).tolist()]


def save_stroke_data(
    output_path: Path,
    data: dict,
    crop_scale: float,
    region_source: str,
    target_specs,
    region_stroke_records,
):
    flat_strokes = []
    for region_record in region_stroke_records:
        flat_strokes.extend(region_record["strokes"])

    text_region_count = sum(1 for spec in target_specs if spec["type"] == "text")
    shape_region_count = sum(1 for spec in target_specs if spec["type"] == "shape")

    output_path.write_text(
        json.dumps(
            {
                "input_path": data["input_path"],
                "scale": data.get("scale", 1.0),
                "crop_scale": crop_scale,
                "region_source": region_source,
                "shape_rendering": "always",
                "coordinate_system": {
                    "local_points": "crop-local coordinates after optional upscaling/downscaling",
                    "global_points": "reference image coordinates after adding the selected region bbox offset",
                    "point_order": "[x, y]",
                },
                "region_count": len(target_specs),
                "text_region_count": text_region_count,
                "shape_region_count": shape_region_count,
                "total_stroke_count": len(flat_strokes),
                "regions": region_stroke_records,
                "strokes": flat_strokes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"stroke coordinate data saved: {output_path}")


def main():
    args = parse_args()
    pilot_dir = Path(args.pilot_dir)
    if not pilot_dir.exists():
        raise FileNotFoundError(f"pilot 디렉토리가 없습니다: {pilot_dir}")

    data, _ = load_regions(pilot_dir, args.region_source)
    reference_image = load_reference_image(data)
    target_specs = collect_target_crop_specs(
        data,
        pilot_dir,
        args.region_source,
    )

    if args.save_merged_debug:
        crop_specs = collect_crop_specs(
            data,
            pilot_dir,
            args.merged_debug_mode,
            args.region_source,
        )
        save_merged_debug_outputs(
            pilot_dir,
            reference_image,
            crop_specs,
            args.merged_debug_mode,
            args.region_source,
            args.black_thickness,
            args.result_thickness,
        )

    if args.save_crop_debug:
        crop_specs = collect_crop_specs(
            data,
            pilot_dir,
            args.crop_debug_mode,
            args.region_source,
        )
        save_crop_debug_outputs(
            pilot_dir,
            crop_specs,
            reference_image,
            args.crop_scale,
            args.region_source,
        )

    all_shifted_strokes = []
    summary = []
    region_stroke_records = []
    global_stroke_id = 1
    for region_number, spec in enumerate(target_specs, start=1):
        bbox = spec["bbox"]
        strokes = extract_crop_strokes(spec, reference_image, args.crop_scale)
        shifted_strokes = offset_strokes(strokes, bbox)
        all_shifted_strokes.extend(shifted_strokes)

        stroke_records = []
        for local_stroke, shifted_stroke in zip(strokes, shifted_strokes):
            stroke_records.append(
                {
                    "id": global_stroke_id,
                    "region_number": region_number,
                    "region_type": spec["type"],
                    "region_index": spec["index"],
                    "bbox": bbox,
                    "point_count": int(len(shifted_stroke)),
                    "local_points": stroke_to_points(local_stroke),
                    "global_points": stroke_to_points(shifted_stroke),
                }
            )
            global_stroke_id += 1

        region_stroke_records.append(
            {
                "region_number": region_number,
                "type": spec["type"],
                "index": spec["index"],
                "bbox": bbox,
                "crop_path": crop_path_to_string(spec),
                "source": spec.get("source"),
                "stroke_count": len(stroke_records),
                "strokes": stroke_records,
            }
        )
        summary.append(
            {
                "region_number": region_number,
                "type": spec["type"],
                "index": spec["index"],
                "bbox": bbox,
                "crop_path": crop_path_to_string(spec),
                "source": spec.get("source"),
                "crop_scale": args.crop_scale,
                "stroke_count": len(shifted_strokes),
            }
        )
        print(
            f"[region {region_number:02d}] type={spec['type']} index={spec['index']} bbox={bbox} local_strokes={len(strokes)} shifted_strokes={len(shifted_strokes)}"
        )

    output_suffix = "" if args.region_source == "ocr_merged" else f"_{args.region_source}"
    output_path = (
        Path(args.output)
        if args.output
        else pilot_dir / f"crop_stroke_composite{output_suffix}_result.png"
    )
    output_black_path = (
        Path(args.output_black)
        if args.output_black
        else pilot_dir / f"crop_stroke_composite{output_suffix}_black.png"
    )

    save_result_image(
        all_shifted_strokes,
        reference_image,
        str(output_path),
        thickness=args.result_thickness,
    )
    save_black_strokes_image(
        all_shifted_strokes,
        reference_image,
        str(output_black_path),
        thickness=args.black_thickness,
    )

    summary_path = pilot_dir / f"crop_stroke_composite{output_suffix}_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "pilot_dir": str(pilot_dir),
                "input_path": data["input_path"],
                "scale": data.get("scale", 1.0),
                "crop_scale": args.crop_scale,
                "region_source": args.region_source,
                "shape_rendering": "always",
                "result_thickness": args.result_thickness,
                "black_thickness": args.black_thickness,
                "region_count": len(target_specs),
                "text_region_count": sum(1 for spec in target_specs if spec["type"] == "text"),
                "shape_region_count": sum(1 for spec in target_specs if spec["type"] == "shape"),
                "total_stroke_count": len(all_shifted_strokes),
                "regions": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if args.save_stroke_data:
        stroke_data_path = (
            Path(args.stroke_data_output)
            if args.stroke_data_output
            else pilot_dir / f"crop_stroke_composite{output_suffix}_strokes.json"
        )
        save_stroke_data(
            stroke_data_path,
            data,
            args.crop_scale,
            args.region_source,
            target_specs,
            region_stroke_records,
        )

    print(f"region source: {args.region_source}")
    print(f"region count: {len(target_specs)}")
    print(f"text region count: {sum(1 for spec in target_specs if spec['type'] == 'text')}")
    print(f"shape region count: {sum(1 for spec in target_specs if spec['type'] == 'shape')}")
    print(f"total shifted stroke count: {len(all_shifted_strokes)}")
    print(f"summary saved: {summary_path}")


if __name__ == "__main__":
    main()
