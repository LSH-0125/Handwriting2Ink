"""Replay extracted stroke coordinates as real mouse input.

GoodNotes usage assumptions:
- GoodNotes is already open and focused.
- The pen tool is already selected.
- The user provides the writable page area as a screen rectangle.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

Point = tuple[float, float]
Rect = tuple[float, float, float, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="stroke JSON 좌표열을 GoodNotes 같은 앱 위에 실제 마우스 입력으로 재생합니다."
    )
    parser.add_argument("--strokes", required=True, help="render_strokes.py가 저장한 stroke JSON 경로")
    parser.add_argument(
        "--target_rect",
        required=True,
        help="화면 필기 영역: x,y,width,height 예) 320,180,980,720",
    )
    parser.add_argument(
        "--fit",
        choices=("contain", "stretch"),
        default="contain",
        help="stroke bbox를 target_rect에 맞추는 방식. 기본값은 비율 유지(contain)",
    )
    parser.add_argument("--sample_step", type=int, default=1, help="N개 점마다 하나씩 사용")
    parser.add_argument("--point_delay", type=float, default=0.002, help="점 사이 대기 시간")
    parser.add_argument("--stroke_delay", type=float, default=0.04, help="stroke 사이 대기 시간")
    parser.add_argument("--countdown", type=float, default=3.0, help="실제 실행 전 대기 시간")
    parser.add_argument(
        "--driver",
        choices=("drag", "down_move"),
        default="drag",
        help="마우스 입력 방식. GoodNotes에는 drag 권장",
    )
    parser.add_argument("--execute", action="store_true", help="실제 마우스를 움직입니다. 없으면 dry-run")
    return parser.parse_args()


def parse_rect(value: str) -> Rect:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("target_rect는 x,y,width,height 형식이어야 합니다.")
    x, y, width, height = parts
    if width <= 0 or height <= 0:
        raise ValueError("target_rect의 width/height는 0보다 커야 합니다.")
    return x, y, width, height


def load_strokes(path: Path) -> list[list[Point]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_strokes = data.get("strokes", data if isinstance(data, list) else [])

    strokes: list[list[Point]] = []
    for raw in raw_strokes:
        points = raw.get("global_points") if isinstance(raw, dict) else raw
        if not points or len(points) < 2:
            continue
        strokes.append([(float(x), float(y)) for x, y in points])
    if not strokes:
        raise ValueError(f"사용 가능한 stroke가 없습니다: {path}")
    return strokes


def stroke_bbox(strokes: list[list[Point]]) -> Rect:
    xs = [x for stroke in strokes for x, _ in stroke]
    ys = [y for stroke in strokes for _, y in stroke]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return min_x, min_y, max(max_x - min_x, 1.0), max(max_y - min_y, 1.0)


def map_strokes(strokes: list[list[Point]], source: Rect, target: Rect, fit: str) -> list[list[Point]]:
    sx, sy, sw, sh = source
    tx, ty, tw, th = target

    if fit == "stretch":
        scale_x, scale_y = tw / sw, th / sh
        offset_x, offset_y = tx, ty
    else:
        scale = min(tw / sw, th / sh)
        scale_x = scale_y = scale
        offset_x = tx + (tw - sw * scale) / 2.0
        offset_y = ty + (th - sh * scale) / 2.0

    mapped: list[list[Point]] = []
    for stroke in strokes:
        mapped.append(
            [
                (offset_x + (x - sx) * scale_x, offset_y + (y - sy) * scale_y)
                for x, y in stroke
            ]
        )
    return mapped


def sample_strokes(strokes: list[list[Point]], step: int) -> list[list[Point]]:
    step = max(1, step)
    sampled = []
    for stroke in strokes:
        points = stroke[::step]
        if points[-1] != stroke[-1]:
            points.append(stroke[-1])
        if len(points) >= 2:
            sampled.append(points)
    return sampled


def summarize(strokes: list[list[Point]], source: Rect, target: Rect) -> None:
    point_count = sum(len(stroke) for stroke in strokes)
    print(f"stroke count: {len(strokes)}")
    print(f"point count: {point_count}")
    print(f"source bbox: {tuple(round(v, 2) for v in source)}")
    print(f"target rect: {tuple(round(v, 2) for v in target)}")


def replay_with_pyautogui(
    strokes: list[list[Point]],
    point_delay: float,
    stroke_delay: float,
    driver: str,
) -> None:
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError(
            "pyautogui가 설치되어 있지 않습니다. "
            "conda run -n DV python -m pip install pyautogui 로 설치하세요."
        ) from exc

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    for stroke in strokes:
        start_x, start_y = stroke[0]
        pyautogui.moveTo(start_x, start_y)
        if driver == "drag":
            for x, y in stroke[1:]:
                pyautogui.dragTo(
                    x,
                    y,
                    duration=max(point_delay, 0.0),
                    button="left",
                )
        else:
            pyautogui.mouseDown(button="left")
            try:
                for x, y in stroke[1:]:
                    pyautogui.moveTo(x, y)
                    if point_delay > 0:
                        time.sleep(point_delay)
            finally:
                pyautogui.mouseUp(button="left")
        if stroke_delay > 0:
            time.sleep(stroke_delay)


def main() -> None:
    args = parse_args()
    strokes = load_strokes(Path(args.strokes))
    strokes = sample_strokes(strokes, args.sample_step)
    source = stroke_bbox(strokes)
    target = parse_rect(args.target_rect)
    mapped = map_strokes(strokes, source, target, args.fit)

    summarize(mapped, source, target)
    if not args.execute:
        print("dry-run: 실제 마우스 입력은 실행하지 않았습니다. --execute를 붙이면 실행됩니다.")
        return

    print(f"{args.countdown:.1f}초 후 마우스 입력을 시작합니다. 중단하려면 마우스를 화면 모서리로 이동하세요.")
    time.sleep(max(0.0, args.countdown))
    replay_with_pyautogui(mapped, args.point_delay, args.stroke_delay, args.driver)
    print("done")


if __name__ == "__main__":
    main()
