from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goodnotes_writer import load_strokes
from goodnotes_writer import map_strokes
from goodnotes_writer import replay_with_pyautogui
from goodnotes_writer import sample_strokes
from goodnotes_writer import stroke_bbox
from goodnotes_writer import summarize

GOODNOTES_APP_NAMES = ("Goodnotes", "GoodNotes")

Point = tuple[float, float]
Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class ControllerConfig:
    pen_button_point: Point | None = None
    black_color_point: Point | None = None
    paper_rect: Rect | None = None
    driver: str = "down_move"
    tool_select_mode: str = "hotkey"
    pen_hotkey: str = "cmd+p"
    lasso_hotkey: str = "cmd+l"
    target_rect: Rect | None = None
    fit: str = "contain"
    sample_step: int = 1
    point_delay: float = 0.002
    stroke_delay: float = 0.04
    lasso_padding: float = 24.0
    lasso_drag_duration: float = 0.8
    copy_hotkey: str = "cmd+c"
    countdown: float = 2.0
    line_length: float = 15.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "GoodNotes를 전면/전체화면으로 올리고, 펜을 선택한 뒤, "
            "중앙에 짧은 절대좌표 테스트 선을 입력합니다."
        )
    )
    parser.add_argument("--config", default="mac_worker/config.json", help="워커 설정 JSON 경로")
    parser.add_argument("--pen_point", default=None, help="펜 버튼 절대 좌표 x,y")
    parser.add_argument("--black_color_point", default=None, help="검은색 색상 버튼 절대 좌표 x,y")
    parser.add_argument("--paper_rect", default=None, help="종이 영역 절대 좌표 x,y,width,height")
    parser.add_argument("--strokes", default=None, help="GoodNotes에 그릴 strokes.json 경로")
    parser.add_argument("--target_rect", default=None, help="stroke를 매핑할 GoodNotes 종이 영역 x,y,width,height")
    parser.add_argument(
        "--fit",
        choices=("contain", "stretch"),
        default=None,
        help="stroke bbox를 target_rect에 맞추는 방식",
    )
    parser.add_argument("--sample_step", type=int, default=None, help="N개 점마다 하나씩 사용")
    parser.add_argument("--point_delay", type=float, default=None, help="stroke point 사이 입력 지연")
    parser.add_argument("--stroke_delay", type=float, default=None, help="stroke 사이 입력 지연")
    parser.add_argument(
        "--copy_after_draw",
        action="store_true",
        help="stroke 입력 후 cmd+l로 올가미를 선택하고 stroke bbox 주변을 드래그한 뒤 cmd+c를 실행합니다.",
    )
    parser.add_argument("--lasso_padding", type=float, default=None, help="자동 올가미 bbox padding(px)")
    parser.add_argument("--lasso_drag_duration", type=float, default=None, help="올가미 사각형 드래그 시간")
    parser.add_argument("--copy_hotkey", default=None, help="복사 단축키. 기본값 cmd+c")
    parser.add_argument("--line_center", default=None, help="테스트 선 중심 절대 좌표 x,y")
    parser.add_argument("--line_length", type=float, default=None, help="테스트 선 길이(px). 기본값 15")
    parser.add_argument("--countdown", type=float, default=None, help="실제 선 입력 전 대기 시간")
    parser.add_argument("--driver", choices=("drag", "down_move"), default=None, help="마우스 입력 방식")
    parser.add_argument(
        "--tool_select_mode",
        choices=("hotkey", "point"),
        default=None,
        help="펜 도구 선택 방식. 기본값은 GoodNotes 단축키 hotkey",
    )
    parser.add_argument("--pen_hotkey", default=None, help="펜 선택 단축키. 기본값 cmd+p")
    parser.add_argument("--lasso_hotkey", default=None, help="올가미 선택 단축키. 기본값 cmd+l")
    parser.add_argument(
        "--select_lasso_after_draw",
        action="store_true",
        help="테스트 선 입력 후 올가미 도구까지 선택합니다.",
    )
    parser.add_argument(
        "--skip_fullscreen",
        action="store_true",
        help="GoodNotes 전체화면 전환을 생략합니다.",
    )
    parser.add_argument(
        "--require_pen_point",
        action="store_true",
        help="호환용 옵션입니다. 실제 실행에서는 기본적으로 펜 버튼 좌표가 필요합니다.",
    )
    parser.add_argument(
        "--skip_pen_select",
        action="store_true",
        help="펜 선택을 생략하고 현재 선택된 도구로 테스트 선만 입력합니다.",
    )
    parser.add_argument("--execute", action="store_true", help="실제 GUI 조작과 마우스 입력을 수행합니다.")
    return parser.parse_args()


def resolve_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def parse_point(value: str | None) -> Point | None:
    if not value:
        return None
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 2:
        raise ValueError("좌표는 x,y 형식이어야 합니다.")
    return parts[0], parts[1]


def parse_rect(value: str | None) -> Rect | None:
    if not value:
        return None
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("영역은 x,y,width,height 형식이어야 합니다.")
    x, y, width, height = parts
    if width <= 0 or height <= 0:
        raise ValueError("영역 width/height는 0보다 커야 합니다.")
    return x, y, width, height


def load_json_config(path: str) -> dict[str, Any]:
    config_path = resolve_path(path)
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def controller_config(raw: dict[str, Any], args: argparse.Namespace) -> ControllerConfig:
    section = raw.get("goodnotes_controller", {})
    if not isinstance(section, dict):
        section = {}

    pen_point = parse_point(args.pen_point) or parse_point(section.get("pen_button_point"))
    black_color_point = parse_point(args.black_color_point) or parse_point(section.get("black_color_point"))
    paper_rect = parse_rect(args.paper_rect) or parse_rect(section.get("paper_rect"))
    target_rect = (
        parse_rect(args.target_rect)
        or parse_rect(section.get("target_rect"))
        or parse_rect(raw.get("target_rect"))
    )
    driver = args.driver or section.get("driver") or "down_move"
    tool_select_mode = args.tool_select_mode or section.get("tool_select_mode") or "hotkey"
    pen_hotkey = args.pen_hotkey or section.get("pen_hotkey") or "cmd+p"
    lasso_hotkey = args.lasso_hotkey or section.get("lasso_hotkey") or "cmd+l"
    fit = args.fit or section.get("fit") or raw.get("fit") or "contain"
    sample_step = (
        args.sample_step if args.sample_step is not None else int(section.get("sample_step", raw.get("sample_step", 1)))
    )
    point_delay = (
        args.point_delay
        if args.point_delay is not None
        else float(section.get("point_delay", raw.get("point_delay", 0.002)))
    )
    stroke_delay = (
        args.stroke_delay
        if args.stroke_delay is not None
        else float(section.get("stroke_delay", raw.get("stroke_delay", 0.04)))
    )
    lasso_padding = (
        args.lasso_padding
        if args.lasso_padding is not None
        else float(section.get("lasso_padding", 24.0))
    )
    lasso_drag_duration = (
        args.lasso_drag_duration
        if args.lasso_drag_duration is not None
        else float(section.get("lasso_drag_duration", 0.8))
    )
    copy_hotkey = args.copy_hotkey or section.get("copy_hotkey") or "cmd+c"
    countdown = args.countdown if args.countdown is not None else float(section.get("countdown", 2.0))
    line_length = (
        args.line_length if args.line_length is not None else float(section.get("line_length", 15.0))
    )
    return ControllerConfig(
        pen_button_point=pen_point,
        black_color_point=black_color_point,
        paper_rect=paper_rect,
        driver=driver,
        tool_select_mode=tool_select_mode,
        pen_hotkey=pen_hotkey,
        lasso_hotkey=lasso_hotkey,
        target_rect=target_rect,
        fit=fit,
        sample_step=sample_step,
        point_delay=point_delay,
        stroke_delay=stroke_delay,
        lasso_padding=lasso_padding,
        lasso_drag_duration=lasso_drag_duration,
        copy_hotkey=copy_hotkey,
        countdown=countdown,
        line_length=line_length,
    )


def prepare_mapped_strokes(strokes_path: Path, config: ControllerConfig) -> list[list[Point]]:
    if config.target_rect is None:
        raise RuntimeError("--strokes 사용 시 --target_rect 또는 config target_rect가 필요합니다.")
    strokes = load_strokes(strokes_path)
    strokes = sample_strokes(strokes, config.sample_step)
    source = stroke_bbox(strokes)
    mapped = map_strokes(strokes, source, config.target_rect, config.fit)
    summarize(mapped, source, config.target_rect)
    return mapped


def padded_rect(rect: Rect, padding: float) -> Rect:
    x, y, width, height = rect
    padding = max(0.0, padding)
    return x - padding, y - padding, width + padding * 2.0, height + padding * 2.0


def run_osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "osascript 실행에 실패했습니다.")
    return result.stdout.strip()


def activate_goodnotes() -> str:
    errors = []
    for app_name in GOODNOTES_APP_NAMES:
        result = subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            time.sleep(0.5)
            return app_name
        errors.append((result.stderr or result.stdout).strip())
    raise RuntimeError(f"GoodNotes 활성화에 실패했습니다: {' | '.join(errors)}")


def require_frontmost_goodnotes() -> str:
    frontmost = run_osascript(
        'tell application "System Events" to get name of first application process whose frontmost is true'
    )
    if frontmost not in GOODNOTES_APP_NAMES:
        raise RuntimeError(f"GoodNotes가 전면 앱이 아닙니다: {frontmost}")
    return frontmost


def set_goodnotes_fullscreen(process_name: str) -> None:
    script = f'''
tell application "System Events"
  tell application process "{process_name}"
    set frontmost to true
    delay 0.2
    try
      set value of attribute "AXFullScreen" of window 1 to true
    on error
      keystroke "f" using {{control down, command down}}
    end try
  end tell
end tell
'''
    run_osascript(script)
    time.sleep(1.0)


def screen_size() -> tuple[int, int]:
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError("pyautogui가 설치되어 있지 않습니다.") from exc
    size = pyautogui.size()
    if size.width <= 0 or size.height <= 0:
        raise RuntimeError(f"유효하지 않은 화면 크기입니다: {size.width}x{size.height}")
    return int(size.width), int(size.height)


def center_from_rect(rect: Rect) -> Point:
    x, y, width, height = rect
    return x + width / 2.0, y + height / 2.0


def send_hotkey(hotkey: str) -> None:
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError("pyautogui가 설치되어 있지 않습니다.") from exc

    keys = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
    if not keys:
        raise RuntimeError("단축키가 비어 있습니다.")
    pyautogui.hotkey(*keys)
    time.sleep(0.2)


def select_pen(config: ControllerConfig, skip_pen_select: bool) -> None:
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError("pyautogui가 설치되어 있지 않습니다.") from exc

    pyautogui.PAUSE = 0.1
    if skip_pen_select:
        print("WARN: --skip_pen_select 때문에 펜 선택 클릭을 생략합니다.")
        return

    if config.tool_select_mode == "hotkey":
        send_hotkey(config.pen_hotkey)
        return

    if config.pen_button_point is None:
        raise RuntimeError("펜 버튼 좌표가 없습니다. --pen_point x,y 또는 config 설정이 필요합니다.")

    pyautogui.click(*config.pen_button_point)
    time.sleep(0.2)
    if config.black_color_point is not None:
        pyautogui.click(*config.black_color_point)
        time.sleep(0.2)
    else:
        print("WARN: 검은색 버튼 좌표가 없어 색상 선택 클릭을 생략합니다.")


def select_lasso(config: ControllerConfig) -> None:
    if config.tool_select_mode != "hotkey":
        raise RuntimeError("올가미 선택은 현재 hotkey 모드만 지원합니다.")
    send_hotkey(config.lasso_hotkey)


def drag_lasso_rect(rect: Rect, duration: float) -> None:
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError("pyautogui가 설치되어 있지 않습니다.") from exc

    x, y, width, height = rect
    left, top = x, y
    right, bottom = x + width, y + height
    points = [
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom),
        (left, top),
    ]
    segment_duration = max(duration, 0.0) / max(len(points) - 1, 1)

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True
    pyautogui.moveTo(*points[0], duration=0.1)
    pyautogui.mouseDown(button="left")
    try:
        for point in points[1:]:
            pyautogui.moveTo(*point, duration=segment_duration)
    finally:
        pyautogui.mouseUp(button="left")


def copy_with_lasso(config: ControllerConfig, selection_rect: Rect) -> None:
    select_lasso(config)
    time.sleep(0.2)
    drag_lasso_rect(selection_rect, config.lasso_drag_duration)
    time.sleep(0.2)
    send_hotkey(config.copy_hotkey)


def draw_horizontal_line(center: Point, length: float, driver: str) -> tuple[Point, Point]:
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError("pyautogui가 설치되어 있지 않습니다.") from exc

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True
    half = max(length, 1.0) / 2.0
    start = (center[0] - half, center[1])
    end = (center[0] + half, center[1])

    pyautogui.moveTo(*start, duration=0.1)
    if driver == "drag":
        pyautogui.dragTo(*end, duration=0.35, button="left")
    else:
        pyautogui.mouseDown(button="left")
        try:
            pyautogui.moveTo(*end, duration=0.35)
        finally:
            pyautogui.mouseUp(button="left")
    return start, end


def main() -> None:
    args = parse_args()
    raw_config = load_json_config(args.config)
    config = controller_config(raw_config, args)
    strokes_path = resolve_path(args.strokes) if args.strokes else None
    mapped_strokes = prepare_mapped_strokes(strokes_path, config) if strokes_path else None
    selection_rect = padded_rect(stroke_bbox(mapped_strokes), config.lasso_padding) if mapped_strokes else None
    if args.copy_after_draw and mapped_strokes is None:
        raise RuntimeError("--copy_after_draw는 --strokes 모드에서만 사용할 수 있습니다.")
    if mapped_strokes is None:
        explicit_center = parse_point(args.line_center)
        width, height = screen_size()
        if explicit_center is not None:
            line_center = explicit_center
        elif config.paper_rect is not None:
            line_center = center_from_rect(config.paper_rect)
        else:
            line_center = None
        if line_center is None:
            line_center = (width / 2.0, height / 2.0)
    else:
        width = height = None
        line_center = None

    if width is not None and height is not None:
        print(f"screen: {width}x{height}")
    else:
        print("screen: skipped for strokes mode")
    print(f"pen point: {config.pen_button_point}")
    print(f"black color point: {config.black_color_point}")
    print(f"paper rect: {config.paper_rect}")
    print(f"target rect: {config.target_rect}")
    print(f"tool select mode: {config.tool_select_mode}")
    print(f"pen hotkey: {config.pen_hotkey}")
    print(f"lasso hotkey: {config.lasso_hotkey}")
    if line_center is not None:
        print(f"line center: {tuple(round(v, 2) for v in line_center)}")
    print(f"line length: {config.line_length}")
    print(f"driver: {config.driver}")
    if mapped_strokes is not None:
        print(f"draw mode: strokes")
        print(f"strokes path: {strokes_path}")
        print(f"fit: {config.fit}")
        print(f"sample step: {config.sample_step}")
        print(f"point delay: {config.point_delay}")
        print(f"stroke delay: {config.stroke_delay}")
        if selection_rect is not None:
            print(f"copy after draw: {args.copy_after_draw}")
            print(f"lasso selection rect: {tuple(round(v, 2) for v in selection_rect)}")
            print(f"lasso padding: {config.lasso_padding}")
            print(f"lasso drag duration: {config.lasso_drag_duration}")
            print(f"copy hotkey: {config.copy_hotkey}")
    else:
        print("draw mode: test_line")

    if not args.execute:
        print("dry-run: 실제 GoodNotes 조작은 실행하지 않았습니다. --execute를 붙이면 실행됩니다.")
        return

    app_name = activate_goodnotes()
    if not args.skip_fullscreen:
        set_goodnotes_fullscreen(app_name)
    frontmost = require_frontmost_goodnotes()
    print(f"frontmost: {frontmost}")
    select_pen(config, args.skip_pen_select)
    action = "strokes.json 입력" if mapped_strokes is not None else "중앙 테스트 선 입력"
    print(f"{config.countdown:.1f}초 후 {action}을 시작합니다.")
    time.sleep(max(config.countdown, 0.0))
    if mapped_strokes is not None:
        replay_with_pyautogui(mapped_strokes, config.point_delay, config.stroke_delay, config.driver)
        if args.copy_after_draw and selection_rect is not None:
            copy_with_lasso(config, selection_rect)
            print("copied with lasso")
    else:
        start, end = draw_horizontal_line(line_center, config.line_length, config.driver)
        print(f"line start: {tuple(round(v, 2) for v in start)}")
        print(f"line end: {tuple(round(v, 2) for v in end)}")
    if args.select_lasso_after_draw:
        select_lasso(config)
        print("lasso selected")
    print("done")


if __name__ == "__main__":
    main()
