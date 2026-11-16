"""GoodNotes for Windows 주입기.

strokes.json 을 받아 GoodNotes 노트북 현재 페이지에 실제 마우스 입력으로 필기한다.

mac v1 과 결정적으로 다른 점: 클립보드 .bin 을 만들지 않는다.
GoodNotes for Windows 의 '복사하기' 는 시스템 클립보드에 잉크가 아니라
타입 이름 문자열('com.goodnotesapp.goodnotes5.notes') 34바이트만 올린다.
실제 획 데이터는 앱 내부에 남는다 - 웹앱이라 Chromium 클립보드 API 로는
네이티브 커스텀 포맷을 쓸 수 없기 때문이다. 그래서 전달은 클립보드가 아니라
GoodNotes 계정 동기화(Windows -> iOS 방향)에 맡긴다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "server" / "pipeline"))

from goodnotes_writer import load_strokes, map_strokes, sample_strokes, stroke_bbox  # noqa: E402

import winplat  # noqa: E402

Point = tuple[float, float]
Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Config:
    # 필기 영역: 미지정이면 창에서 추정한다. 정확히 주는 쪽이 항상 낫다.
    target_rect: Rect | None = None
    target_inset: float = 0.12
    fit: str = "contain"
    sample_step: int = 1
    point_delay: float = 0.004
    stroke_delay: float = 0.06
    countdown: float = 2.0
    maximize: bool = True
    activate_retries: int = 8
    # 도구 좌표. 툴바는 창 중앙 정렬이므로 중앙 기준 x 오프셋으로 둔다.
    # 좌상단 기준 절대값으로 두면 창을 최대화하는 순간 클릭이 빗나간다.
    pen_tool_dx: float = -210.0
    select_tool_dx: float = -279.0
    tool_y: float = 128.0          # 창 상단 기준
    select_pen: bool = True
    detect_paper: bool = True      # 종이 영역을 픽셀로 찾는다
    paper_margin: float = 0.06     # 종이 안쪽 여백 비율
    verify_drawn: bool = True      # 그린 뒤 실제로 픽셀이 바뀌었는지 확인

    # 대상 노트북. 워커는 "그 시점 열려 있는 페이지" 에 그리므로, 엉뚱한
    # 노트북이 열려 있으면 거기에 그려지고 공유도 안 된다.
    # GoodNotes 는 WebView2 라 UI Automation 으로 탭 이름을 읽을 수 없어서
    # (접근성 트리를 노출하지 않는다) 탭 영역 픽셀을 대조한다.
    expected_notebook: str = "소페_시연용"
    tab_ref: str = "notebook_ref.png"   # --calibrate 로 만든다
    tab_match_tolerance: float = 8.0    # 평균 절대차 허용치 (0-255)

    # 그리기 시간 예산(초). 실제 손글씨 한 페이지는 400획 4만점 정도가 나오고
    # 그대로 그리면 3분 반이 넘는다. 예산을 넘으면 sample_step 을 올려
    # 점을 솎아내고, 몇 배로 솎았는지 로그에 남긴다. 0 이면 무제한.
    max_draw_seconds: float = 240.0

    # 이미 필기가 있는 페이지에 덧그리면 결과가 겹쳐 못 쓰게 된다.
    # 워커는 페이지를 넘기지 않으므로(자동 페이지 넘김은 GUI 리스크가 크다)
    # 최소한 조용히 겹치지는 않도록 경고하거나 막는다.
    # 'warn' = 경고만, 'abort' = 거부, 'off' = 검사 안 함
    on_dirty_page: str = "warn"
    dirty_page_threshold: int = 500   # 필기 영역 내 어두운 픽셀 수


def parse_rect(value: str | None) -> Rect | None:
    if not value:
        return None
    parts = [float(p.strip()) for p in str(value).split(",")]
    if len(parts) != 4:
        raise ValueError("영역은 x,y,width,height 형식이어야 합니다.")
    if parts[2] <= 0 or parts[3] <= 0:
        raise ValueError("width/height는 0보다 커야 합니다.")
    return parts[0], parts[1], parts[2], parts[3]


def load_config(path: str | Path | None) -> Config:
    cfg = Config()
    if not path:
        return cfg
    p = Path(path)
    if not p.is_absolute():
        p = HERE / p
    if not p.exists():
        return cfg
    raw: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    sec = raw.get("controller", raw)
    known = {f for f in Config.__dataclass_fields__}
    patch: dict[str, Any] = {}
    for k, v in sec.items():
        if k not in known:
            continue
        if k == "target_rect":
            v = parse_rect(v)
        elif k in ("pen_tool_offset", "select_tool_offset"):
            v = (float(v[0]), float(v[1]))
        patch[k] = v
    return replace(cfg, **patch)


def derive_target_rect(win_rect: Rect, inset: float) -> Rect:
    """창에서 필기 가능한 안쪽 영역을 깎아낸다. 위쪽은 툴바가 있어 더 깎는다."""
    x, y, w, h = win_rect
    mx = w * inset
    top = h * (inset * 1.6)
    bottom = h * inset
    return x + mx, y + top, w - mx * 2, h - top - bottom


def tool_point(win: winplat.Window, dx: float, cfg: Config) -> Point:
    """툴바 아이콘 좌표. 툴바가 창 중앙 정렬이라 중앙에서 재야 한다."""
    x, y, w, _ = win.rect
    return x + w / 2.0 + dx, y + cfg.tool_y


def _longest_run(idx) -> tuple[int, int]:
    import numpy as np

    splits = np.split(idx, np.where(np.diff(idx) != 1)[0] + 1)
    best = max(splits, key=len)
    return int(best[0]), int(best[-1])


def paper_mask(img):
    """종이(크림색)만 True. 배경 회색/타이틀바/파란 툴바를 전부 배제한다.

    종이 (248,247,233) 는 R-B=15 로 따뜻하고, 배경 (230,229,228) 은 R-B=2,
    타이틀바 (243,243,247) 은 R-B=-4, 툴바 파랑은 R-B=-110 이다.
    """
    return (img[:, :, 0] - img[:, :, 2] >= 8) & (img[:, :, 0] >= 200)


def detect_paper_rect(win: winplat.Window, cfg: Config, retries: int = 6) -> Rect | None:
    """최대화 직후에는 WebView2 가 아직 리레이아웃 중이라 종이가 안 잡힌다.

    검출에 실패하면 창 비율로 추정하는데, 그 값은 종이보다 훨씬 넓어서 획이
    종이 밖으로 나간다. 즉 조용히 품질이 망가지므로 충분히 재시도한다.
    """
    for attempt in range(retries):
        rect = _detect_paper_once(win, cfg)
        if rect is not None:
            if attempt:
                print(f"종이 검출: {attempt + 1}번째 시도에서 성공")
            return rect
        time.sleep(0.8)
    return None


def _detect_paper_once(win: winplat.Window, cfg: Config) -> Rect | None:
    """화면에서 실제 종이 영역을 찾는다.

    창 크기 비율로 추정하면 종이가 가운데 좁은 칸일 때 획이 종이 밖으로
    나간다. 픽셀로 직접 찾는 쪽이 줌/창크기와 무관하게 맞는다.
    """
    import numpy as np
    import pyautogui

    vis = winplat.clamp_to_screen(win.rect)
    region = tuple(int(v) for v in vis)
    img = np.array(pyautogui.screenshot(region=region).convert("RGB")).astype(int)
    warm = paper_mask(img)
    cols = np.where(warm.mean(axis=0) > 0.3)[0]
    rows = np.where(warm.mean(axis=1) > 0.3)[0]
    if len(cols) == 0 or len(rows) == 0:
        return None
    x0, x1 = _longest_run(cols)
    y0, y1 = _longest_run(rows)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    if w < 50 or h < 50:
        return None
    mx, my = w * cfg.paper_margin, h * cfg.paper_margin
    return vis[0] + x0 + mx, vis[1] + y0 + my, w - mx * 2, h - my * 2


def tab_strip_rect(win: winplat.Window) -> Rect:
    """탭 줄 영역. 창 크기에 비례해 잡아 최대화 여부와 무관하게 같은 곳을 본다."""
    x, y, w, h = win.rect
    return x + w * 0.03, y + h * 0.035, w * 0.53, h * 0.05


def grab_tab_strip(win: winplat.Window):
    import numpy as np
    import pyautogui

    r = tuple(int(v) for v in winplat.clamp_to_screen(tab_strip_rect(win)))
    return np.array(pyautogui.screenshot(region=r).convert("L")).astype(int)


def calibrate_tab(win: winplat.Window, cfg: Config) -> Path:
    """지금 열려 있는 탭 모습을 기준으로 저장한다. 대상 노트북을 열어두고 1회 실행."""
    import numpy as np
    from PIL import Image

    strip = grab_tab_strip(win)
    path = HERE / cfg.tab_ref
    Image.fromarray(strip.astype(np.uint8)).save(path)
    return path


def check_tab(win: winplat.Window, cfg: Config) -> tuple[bool, str]:
    """열린 탭이 보정해둔 것과 같은지. (일치여부, 설명) 을 돌려준다."""
    import numpy as np
    from PIL import Image

    path = HERE / cfg.tab_ref
    if not path.exists():
        return True, (f"탭 기준 이미지가 없습니다 ({path.name}). "
                      f"'{cfg.expected_notebook}' 을 열고 --calibrate 를 한 번 실행하면 "
                      "이후 다른 노트북에 잘못 그리는 것을 막을 수 있습니다.")

    ref = np.array(Image.open(path).convert("L")).astype(int)
    cur = grab_tab_strip(win)
    if ref.shape != cur.shape:
        return False, f"탭 영역 크기가 다릅니다 (기준 {ref.shape}, 현재 {cur.shape}). 창 크기를 바꿨다면 재보정하세요."
    diff = float(np.abs(ref - cur).mean())
    ok = diff <= cfg.tab_match_tolerance
    return ok, f"탭 평균차 {diff:.1f} (허용 {cfg.tab_match_tolerance})"


def paper_fingerprint(win: winplat.Window, rect: Rect):
    """필기 영역의 어두운 픽셀 수. 그리기 전후로 비교해 실제 입력을 확인한다."""
    import numpy as np
    import pyautogui

    r = tuple(int(v) for v in winplat.clamp_to_screen(rect))
    img = np.array(pyautogui.screenshot(region=r).convert("L")).astype(int)
    return int((img < 150).sum())


def acquire_window(cfg: Config) -> winplat.Window:
    """GoodNotes 앱 창을 찾아 복원하고 전면으로 올린다.

    다른 앱이 포커스를 되가져가는 경우가 잦아 여러 번 시도한다.
    """
    import pyautogui

    win = winplat.restore_if_minimized(winplat.find_goodnotes())
    for _ in range(max(1, cfg.activate_retries)):
        # 더미 ALT 로 포그라운드 락을 푼다
        pyautogui.keyDown("alt")
        time.sleep(0.05)
        pyautogui.keyUp("alt")
        winplat.activate(win)
        time.sleep(0.7)
        if winplat.is_frontmost(win):
            break
    else:
        raise RuntimeError(
            f"GoodNotes 를 전면으로 올리지 못했습니다 ({cfg.activate_retries}회 시도). "
            "다른 앱이 포커스를 잡고 있습니다."
        )
    if cfg.maximize:
        win = winplat.maximize(win)
        time.sleep(1.2)   # WebView2 리레이아웃 대기 - 짧으면 종이 검출이 빈 프레임을 본다
    winplat.assert_on_screen(win.rect, "GoodNotes 창")
    return win


def ensure_front(win: winplat.Window, cfg: Config, what: str) -> None:
    """전면을 잃었으면 다시 가져온다.

    다른 앱이 잠깐 포커스를 채가는 일이 잦다. 거기서 바로 실패시키면 잡이
    통째로 버려지므로, 되찾을 수 있으면 되찾고 정말 안 될 때만 던진다.
    """
    if winplat.is_frontmost(win):
        return
    import pyautogui

    for _ in range(max(1, cfg.activate_retries)):
        pyautogui.keyDown("alt")
        time.sleep(0.05)
        pyautogui.keyUp("alt")
        winplat.activate(win)
        time.sleep(0.6)
        if winplat.is_frontmost(win):
            print(f"  (포커스 재획득: {what})")
            return
    raise RuntimeError(f"{what} 후 GoodNotes 전면 복귀 실패")


def estimate_seconds(strokes: list[list[Point]], cfg: Config) -> float:
    """그리기에 걸릴 시간 추정.

    실측 기준점: 33획 2,617점 -> 15.2초 (point_delay 0.004, stroke_delay 0.06).
    획당 상수 0.15 는 moveTo(duration=0.05) + mouseDown/Up 왕복이다.
    약간 보수적으로 나와서 예산 판단에 쓰기 좋다.
    """
    points = sum(len(s) for s in strokes)
    return points * cfg.point_delay + len(strokes) * (cfg.stroke_delay + 0.15)


def fit_time_budget(strokes: list[list[Point]], cfg: Config) -> tuple[list[list[Point]], int]:
    """예산을 넘으면 점을 더 솎아낸다. (솎은 결과, 최종 sample_step) 을 돌려준다."""
    step = max(1, cfg.sample_step)
    if cfg.max_draw_seconds <= 0:
        return strokes, step

    est = estimate_seconds(strokes, cfg)
    if est <= cfg.max_draw_seconds:
        return strokes, step

    base = strokes
    for extra in range(2, 13):
        candidate = sample_strokes(base, extra)
        if estimate_seconds(candidate, cfg) <= cfg.max_draw_seconds:
            print(f"시간 예산 초과({est:.0f}초 > {cfg.max_draw_seconds:.0f}초) "
                  f"-> 점을 1/{extra} 로 솎음 ({estimate_seconds(candidate, cfg):.0f}초)")
            return candidate, step * extra
    worst = sample_strokes(base, 12)
    print(f"WARN: 1/12 까지 솎아도 {estimate_seconds(worst, cfg):.0f}초 걸립니다. 그대로 진행합니다.")
    return worst, step * 12


def plan(strokes_path: Path, win: winplat.Window, cfg: Config) -> tuple[list[list[Point]], Rect]:
    target = cfg.target_rect
    if target is None and cfg.detect_paper:
        target = detect_paper_rect(win, cfg)
        if target is not None:
            print(f"종이 검출: {tuple(round(v, 1) for v in target)}")
    if target is None:
        visible = winplat.clamp_to_screen(win.rect)
        target = derive_target_rect(visible, cfg.target_inset)
        print(f"종이 검출 실패 - 창 비율로 추정: {tuple(round(v, 1) for v in target)}")
    winplat.assert_on_screen(target, "target_rect")

    strokes = load_strokes(strokes_path)
    strokes = sample_strokes(strokes, cfg.sample_step)
    strokes, _ = fit_time_budget(strokes, cfg)
    source = stroke_bbox(strokes)
    mapped = map_strokes(strokes, source, target, cfg.fit)
    winplat.assert_on_screen(stroke_bbox(mapped), "매핑된 stroke 영역")
    return mapped, target


def inject(strokes_path: Path, cfg: Config, execute: bool = False) -> dict[str, Any]:
    """strokes.json 을 GoodNotes 현재 페이지에 필기한다. 요약 dict 를 돌려준다."""
    winplat.ensure_dpi_aware()
    win = acquire_window(cfg) if execute else winplat.restore_if_minimized(winplat.find_goodnotes())
    mapped, target = plan(strokes_path, win, cfg)

    summary = {
        "window": {"exe": win.exe, "cls": win.cls, "rect": win.rect},
        "stroke_count": len(mapped),
        "point_count": sum(len(s) for s in mapped),
        "target_rect": tuple(round(v, 1) for v in target),
        "executed": False,
    }
    summary["estimated_sec"] = round(estimate_seconds(mapped, cfg), 1)
    print(f"window : {win.exe} {win.rect}")
    print(f"strokes: {summary['stroke_count']}  points: {summary['point_count']}  "
          f"예상 {summary['estimated_sec']}초")
    print(f"target : {summary['target_rect']}")

    if not execute:
        print("dry-run: 실제 입력 없음 (--execute 로 실행)")
        return summary

    if cfg.select_pen:
        pen = tool_point(win, cfg.pen_tool_dx, cfg)
        print(f"펜 도구 클릭: {tuple(round(v, 1) for v in pen)}")
        winplat.click(pen)
        time.sleep(0.4)
        ensure_front(win, cfg, "펜 도구 클릭")

    before = paper_fingerprint(win, target) if cfg.verify_drawn else None

    if cfg.on_dirty_page != "off" and before is not None:
        if before > cfg.dirty_page_threshold:
            msg = (f"필기 영역에 이미 잉크가 있습니다 (어두운 픽셀 {before:,}개). "
                   f"그대로 그리면 겹칩니다. GoodNotes 에서 빈 페이지로 넘기세요.")
            if cfg.on_dirty_page == "abort":
                raise RuntimeError(msg)
            print(f"WARN: {msg}")
        summary["ink_before"] = before

    if cfg.countdown > 0:
        print(f"{cfg.countdown:.1f}초 후 필기 시작. 중단은 마우스를 화면 좌상단 모서리로.")
        time.sleep(cfg.countdown)

    ensure_front(win, cfg, "필기 직전")
    started = time.time()
    winplat.replay_strokes(mapped, cfg.point_delay, cfg.stroke_delay, guard=win)
    summary["elapsed_sec"] = round(time.time() - started, 1)

    if cfg.verify_drawn:
        time.sleep(0.8)
        after = paper_fingerprint(win, target)
        delta = after - before
        summary["ink_pixels_added"] = delta
        print(f"잉크 픽셀 변화: {before:,} -> {after:,} ({delta:+,})")
        if delta < summary["point_count"] * 0.1:
            raise RuntimeError(
                f"필기가 반영되지 않았습니다 (잉크 픽셀 {delta:+,}). "
                "펜 도구가 선택되지 않았거나 필기 영역이 종이 밖입니다."
            )

    summary["executed"] = True
    print(f"필기 완료: {summary['elapsed_sec']}초")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="strokes.json 을 GoodNotes for Windows 에 필기")
    ap.add_argument("--strokes", required=True)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--target_rect", default=None)
    ap.add_argument("--sample_step", type=int, default=None)
    ap.add_argument("--point_delay", type=float, default=None)
    ap.add_argument("--stroke_delay", type=float, default=None)
    ap.add_argument("--countdown", type=float, default=None)
    ap.add_argument("--no_pen_select", action="store_true")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    overrides: dict[str, Any] = {}
    if args.target_rect:
        overrides["target_rect"] = parse_rect(args.target_rect)
    for name in ("sample_step", "point_delay", "stroke_delay", "countdown"):
        v = getattr(args, name)
        if v is not None:
            overrides[name] = v
    if args.no_pen_select:
        overrides["select_pen"] = False
    cfg = replace(cfg, **overrides)

    path = Path(args.strokes)
    if not path.exists():
        raise FileNotFoundError(f"strokes.json 이 없습니다: {path}")
    inject(path, cfg, execute=args.execute)


if __name__ == "__main__":
    main()
