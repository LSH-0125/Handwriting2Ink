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
    # 세로 정렬. top = 페이지 위에서부터(노트답게), center = 가운데.
    align: str = "top"
    # 본문에서 멀리 떨어진 작은 조각(사진 가장자리 잡음 등)을 bbox 계산에서 뺀다.
    # 이게 하나라도 있으면 bbox 가 늘어나 본문이 쪼그라들고, 획이 필기 영역
    # 가장자리로 밀려 GoodNotes 자동 스크롤을 부른다.
    drop_far_outliers: bool = True
    sample_step: int = 1
    point_delay: float = 0.004       # pacing="legacy" 일 때만 쓴다
    stroke_delay: float = 0.06
    # 입력 재생 페이싱. GoodNotes(WebView2)는 커서 위치를 대략 프레임마다 한 번
    # 읽는다. 획이 꼭짓점만 남은 경우(페이지 모드, 획당 6점 남짓) 4ms 간격으로
    # 보내면 한 획이 20ms 만에 끝나 꼭짓점 대부분이 유실되고 직선 조각만 남는다
    # (실제로 겪었다: 46획 4.2초, 글자가 전부 토막). 그래서 꼭짓점마다 한 프레임
    # 이상 머물고, 꼭짓점 사이 직선은 빠르게 지나간다.
    pacing: str = "vertex"           # vertex | legacy
    input_rdp: float = 0.5           # 화면 px. 촘촘한 점열을 꼭짓점으로 줄인다
    vertex_hold: float = 0.024       # 꼭짓점 체류 시간(초). 1프레임(16.7ms) 이상
    min_vertex_hold: float = 0.017   # 시간 예산에 밀려도 이 밑으로는 안 줄인다
    densify_px: float = 6.0          # 긴 직선 구간의 중간점 간격(px)
    move_delay: float = 0.006        # 중간점 사이 시간
    pre_down: float = 0.012          # 시작점 이동 후 펜 내리기 전
    down_dwell: float = 0.02         # 펜 내린 직후 정지
    countdown: float = 2.0
    maximize: bool = True
    activate_retries: int = 8
    # 도구 좌표. 툴바는 창 중앙 정렬이므로 중앙 기준 x 오프셋으로 둔다.
    # 좌상단 기준 절대값으로 두면 창을 최대화하는 순간 클릭이 빗나간다.
    pen_tool_dx: float = -210.0
    select_tool_dx: float = -279.0
    tool_y: float = 128.0          # 창 상단 기준
    select_pen: bool = True
    # 펜 굵기 자동 선택. 파이프라인이 준 획 두께(종이 폭 대비)로 GoodNotes 의
    # 가늘게/보통/굵게 버튼을 고른다. 굵은 매직 사진(폭의 2.3%)을 가는 펜으로
    # 그리면 같은 글씨라도 빈약해 보인다. 버튼은 펜 선택 시 나오는 보조 바에
    # 있고 창 중앙 기준 x 오프셋이다(실측: -51, -1, +49 / 창 상단 +197).
    pen_width_auto: bool = True
    pen_width_dx: tuple = (-51.0, -1.0, 49.0)
    pen_width_y: float = 197.0
    pen_width_bounds: tuple = (0.006, 0.013)   # 이 미만 가늘게, 사이 보통, 이상 굵게
    detect_paper: bool = True      # 종이 영역을 픽셀로 찾는다
    # 종이 안쪽 여백. 획이 종이 가장자리에 닿으면 GoodNotes 가 자동 스크롤을
    # 해버리고, 그 뒤 획들이 전부 어긋난 자리에 찍힌다(실제로 겪었다).
    # 가장자리를 넉넉히 비워 그 트리거를 피한다.
    paper_margin: float = 0.12
    scroll_guard: bool = True        # 그리는 중 페이지가 움직였는지 감시
    scroll_tolerance: float = 12.0   # 허용 이동량(px)
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


def tool_is_selected(win: winplat.Window, dx: float, cfg: Config) -> bool | None:
    """툴바 아이콘이 선택 상태(흰 둥근 사각 배경)인지 픽셀로 본다.

    이미 선택된 펜을 다시 누르면 GoodNotes 가 펜 옵션 팝업을 연다. 팝업이 열린 채
    필기를 시작하면 첫 획이 팝업 닫기에 먹힌다. 그래서 선택돼 있으면 누르지 않는다.
    실측: 선택 아이콘 밝은 픽셀 60%/파랑 19%, 비선택 10%대/80%대.
    """
    import numpy as np
    import pyautogui

    x, y = tool_point(win, dx, cfg)
    r = 17
    try:
        img = np.array(pyautogui.screenshot(region=(int(x - r), int(y - r), 2 * r, 2 * r)).convert("RGB")).astype(int)
    except Exception:
        return None
    light = float((img.min(axis=2) > 215).mean())
    blue = float(((img[:, :, 2] - img[:, :, 0]) > 60).mean())
    if light > 0.35 and blue < 0.5:
        return True
    if blue > 0.6:
        return False
    return None


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
    # 창 폭/높이 대비 고정 비율(예전 0.3)로 자르면 축소 보기에서 종이가 창의
    # 30% 미만일 때 검출이 통째로 실패하고, 그때 창 비율 추정으로 넘어가 회색
    # 배경(빈 공간)에 그렸다. 가장 '종이다운' 열/행 대비 비율로 자르면 확대·축소와
    # 무관하다. 팝업이 종이 일부를 가리면 그 행은 절반 미만이 되어 자연히 빠진다.
    colsum = warm.sum(axis=0)
    rowsum = warm.sum(axis=1)
    if colsum.max() < 50 or rowsum.max() < 50:
        return None
    cols = np.where(colsum > 0.5 * colsum.max())[0]
    rows = np.where(rowsum > 0.5 * rowsum.max())[0]
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


# 탭 비교용 정규화 크기. 창 크기가 달라도 같은 해상도로 맞춰 비교한다.
TAB_NORM_SIZE = (600, 40)


def grab_tab_strip(win: winplat.Window):
    """탭 줄을 고정 해상도 흑백으로 가져온다.

    창이 최대화됐는지에 따라 크롭 픽셀 크기가 달라지므로, 그대로 비교하면
    보정 때와 창 상태가 다를 때 멀쩡한 노트북을 거부한다(실제로 겪었다).
    고정 크기로 리샘플해서 창 크기와 무관하게 만든다.
    """
    import numpy as np
    from PIL import Image
    import pyautogui

    r = tuple(int(v) for v in winplat.clamp_to_screen(tab_strip_rect(win)))
    img = pyautogui.screenshot(region=r).convert("L").resize(TAB_NORM_SIZE, Image.BILINEAR)
    return np.array(img).astype(int)


def _paper_ref_path(cfg: Config) -> Path:
    return (HERE / cfg.tab_ref).with_suffix(".paper.json")


def calibrate_tab(win: winplat.Window, cfg: Config) -> Path:
    """지금 열려 있는 탭 모습과 종이 위치를 기준으로 저장한다. 대상 노트북을 열어두고 1회 실행."""
    import numpy as np
    from PIL import Image

    strip = grab_tab_strip(win)
    path = HERE / cfg.tab_ref
    Image.fromarray(strip.astype(np.uint8)).save(path)

    # 종이 위치도 저장해 둔다. 실제 필기 때 픽셀 검출이 실패하면(팝업이 종이를
    # 가리는 등) 창 비율 추정으로 떨어져 종이 밖 회색 영역에 그리게 되는데,
    # 그 대신 이 값을 쓴다.
    raw = _detect_paper_once(win, replace(cfg, paper_margin=0.0))
    if raw is not None:
        wx, wy, ww, wh = win.rect
        _paper_ref_path(cfg).write_text(json.dumps({
            "paper_rel": [raw[0] - wx, raw[1] - wy, raw[2], raw[3]],
            "window": [ww, wh],
        }), encoding="utf-8")
    return path


def calibrated_paper_rect(win: winplat.Window, cfg: Config) -> Rect | None:
    """보정 때 저장한 종이 위치. 창 크기가 그때와 같을 때만 믿는다."""
    p = _paper_ref_path(cfg)
    if not p.exists():
        return None
    try:
        ref = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    wx, wy, ww, wh = win.rect
    if abs(ref["window"][0] - ww) > 2 or abs(ref["window"][1] - wh) > 2:
        return None
    dx, dy, w, h = ref["paper_rel"]
    mx, my = w * cfg.paper_margin, h * cfg.paper_margin
    return wx + dx + mx, wy + dy + my, w - 2 * mx, h - 2 * my


def check_tab(win: winplat.Window, cfg: Config) -> tuple[bool, str]:
    """열린 탭이 보정해둔 것과 같은지. (일치여부, 설명) 을 돌려준다."""
    import numpy as np
    from PIL import Image

    path = HERE / cfg.tab_ref
    if not path.exists():
        return True, (f"탭 기준 이미지가 없습니다 ({path.name}). "
                      f"'{cfg.expected_notebook}' 을 열고 --calibrate 를 한 번 실행하면 "
                      "이후 다른 노트북에 잘못 그리는 것을 막을 수 있습니다.")

    ref = np.array(
        Image.open(path).convert("L").resize(TAB_NORM_SIZE, Image.BILINEAR)
    ).astype(int)
    cur = grab_tab_strip(win)
    if ref.shape != cur.shape:
        return False, f"탭 기준 이미지를 정규화하지 못했습니다 (기준 {ref.shape}, 현재 {cur.shape}). 재보정하세요."
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


def rdp(points: list[Point], eps: float) -> list[Point]:
    """Ramer-Douglas-Peucker. 워커에 cv2 의존성을 늘리지 않으려고 numpy 로 쓴다."""
    import numpy as np

    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if n < 3 or eps <= 0:
        return [tuple(map(float, p)) for p in pts]
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        seg = pts[i + 1:j]
        ab = b - a
        length = float(np.hypot(ab[0], ab[1]))
        if length < 1e-9:
            d = np.hypot(seg[:, 0] - a[0], seg[:, 1] - a[1])
        else:
            d = np.abs(ab[0] * (seg[:, 1] - a[1]) - ab[1] * (seg[:, 0] - a[0])) / length
        k = int(np.argmax(d))
        if d[k] > eps:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return [tuple(map(float, p)) for p in pts[keep]]


def pace_stroke(stroke: list[Point], eps: float, hold: float, cfg: Config) -> list[tuple[float, float, float]]:
    """화면 좌표 획 -> (x, y, 체류초) 열. 꼭짓점엔 hold, 직선 중간점엔 move_delay."""
    import math

    v = rdp(stroke, eps) if len(stroke) > 2 else [tuple(map(float, p)) for p in stroke]
    out = [(v[0][0], v[0][1], 0.0)]
    for (x0, y0), (x1, y1) in zip(v, v[1:]):
        n = int(math.hypot(x1 - x0, y1 - y0) // cfg.densify_px) if cfg.densify_px > 0 else 0
        for k in range(1, n + 1):
            f = k / (n + 1)
            out.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, cfg.move_delay))
        out.append((x1, y1, hold))
    return out


STROKE_CALL_OVERHEAD = 0.004   # moveTo + mouseDown/Up 호출 자체 비용(실측 근사)


def estimate_paced(paced: list[list[tuple[float, float, float]]], cfg: Config) -> float:
    per_stroke = cfg.pre_down + cfg.down_dwell + cfg.stroke_delay + STROKE_CALL_OVERHEAD
    return sum(p[2] for s in paced for p in s) + len(paced) * per_stroke


def prepare_input(mapped: list[list[Point]], cfg: Config):
    """매핑된 획을 재생용 (x, y, 체류) 열로 바꾸고 시간 예산에 맞춘다.

    예산을 넘으면 먼저 꼭짓점 허용오차(eps)를 키워 점 수를 줄이고, 그래도
    넘으면 체류 시간을 1프레임 하한까지 줄인다. 점을 k개 중 1개씩 솎는 옛
    방식은 꼭짓점만 남은 획에서는 모양 자체를 부순다.
    """
    eps, hold = cfg.input_rdp, cfg.vertex_hold
    paced = [pace_stroke(s, eps, hold, cfg) for s in mapped]
    est = estimate_paced(paced, cfg)
    first = est
    while cfg.max_draw_seconds > 0 and est > cfg.max_draw_seconds:
        if eps < 2.0:
            eps = round(min(2.0, eps * 1.5), 2)
        elif hold > cfg.min_vertex_hold + 1e-9:
            hold = max(cfg.min_vertex_hold, hold * 0.8)
        else:
            print(f"WARN: 시간 예산({cfg.max_draw_seconds:.0f}초)을 넘지만 더 줄이면 모양이 망가집니다. "
                  f"{est:.0f}초 그대로 진행합니다.")
            break
        paced = [pace_stroke(s, eps, hold, cfg) for s in mapped]
        est = estimate_paced(paced, cfg)
    if est < first:
        print(f"시간 예산 맞춤: {first:.0f}초 -> {est:.0f}초 (꼭짓점 허용오차 {eps}px, 체류 {hold * 1000:.0f}ms)")
    return paced, est


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


def stroke_landed(stroke) -> bool:
    """방금 그린 획이 화면에 잉크로 찍혔는지 본다(획 위 점들의 절반 이상이 어두운가)."""
    import numpy as np
    import pyautogui

    time.sleep(0.15)                      # GoodNotes 렌더 대기
    pts = np.array([(p[0], p[1]) for p in stroke], float)
    x0, y0 = pts.min(axis=0) - 4
    x1, y1 = pts.max(axis=0) + 4
    img = np.array(pyautogui.screenshot(region=(int(x0), int(y0), int(x1 - x0) + 1, int(y1 - y0) + 1))
                   .convert("L")).astype(int)
    hits = 0
    for x, y in pts:
        cx, cy = int(round(x - x0)), int(round(y - y0))
        win = img[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3]
        hits += int(win.size > 0 and win.min() < 150)
    return hits >= 0.5 * len(pts)


def read_stroke_width_rel(strokes_path: Path) -> float | None:
    try:
        page = json.loads(strokes_path.read_text(encoding="utf-8")).get("page") or {}
        v = page.get("stroke_width_rel")
        return float(v) if v else None
    except Exception:
        return None


def select_pen_width(win: winplat.Window, cfg: Config, rel: float | None) -> None:
    """획 두께에 맞는 펜 굵기 버튼을 누른다. 보조 바가 안 보이면 아무것도 안 누른다."""
    import numpy as np
    import pyautogui

    if not cfg.pen_width_auto or rel is None:
        return
    lo, hi = cfg.pen_width_bounds
    idx = 0 if rel < lo else (1 if rel < hi else 2)
    x0, y0, w, _ = win.rect
    cx = x0 + w / 2.0
    y = y0 + cfg.pen_width_y
    # 보조 바 확인: 버튼 사이 지점이 흰색(종이 크림색이 아님)이어야 한다
    probe = (int(cx + (cfg.pen_width_dx[0] + cfg.pen_width_dx[1]) / 2), int(y - 14))
    px = np.array(pyautogui.screenshot(region=(probe[0] - 3, probe[1] - 3, 6, 6)).convert("RGB")).astype(int)
    r, g, b = px.reshape(-1, 3).mean(axis=0)
    if not (min(r, g, b) > 238 and abs(r - b) < 6):
        print(f"펜 굵기 바가 안 보여 굵기는 그대로 둡니다 (rgb {r:.0f},{g:.0f},{b:.0f})")
        return
    name = ["가늘게", "보통", "굵게"][idx]
    print(f"펜 굵기: {name} (획 두께 = 종이 폭의 {rel * 100:.2f}%)")
    winplat.click((cx + cfg.pen_width_dx[idx], y))
    time.sleep(0.3)


def read_page_meta(strokes_path: Path) -> tuple[float, float] | None:
    """파이프라인이 종이 좌표계(page)를 주면 그 크기를 돌려준다.

    page 가 있으면 획 bbox 가 아니라 '종이 전체' 를 GoodNotes 페이지에
    맞춘다. 원본 종이의 여백과 배치가 그대로 유지된다(작은 메모가 페이지
    전체로 확대되지 않는다).
    """
    try:
        data = json.loads(strokes_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    page = data.get("page") if isinstance(data, dict) else None
    if not page:
        return None
    w, h = float(page.get("width", 0)), float(page.get("height", 0))
    return (w, h) if w > 1 and h > 1 else None


def drop_isolated_strokes(strokes: list[list[Point]]) -> list[list[Point]]:
    """본문 덩어리에서 멀리 떨어진 작은 획을 뺀다.

    점 분포의 0.2~99.8 백분위로 '본문 상자' 를 잡고, 그 상자를 15% 넓힌
    범위 밖에만 있는 획을 고립 잡음으로 본다. 본문 가장자리의 실제 획은
    상자 확장 범위 안에 들어오므로 지워지지 않는다.
    """
    import numpy as np

    pts = np.array([p for s in strokes for p in s], np.float32)
    if len(pts) < 50:
        return strokes
    x0, x1 = np.percentile(pts[:, 0], [0.2, 99.8])
    y0, y1 = np.percentile(pts[:, 1], [0.2, 99.8])
    mx, my = (x1 - x0) * 0.15, (y1 - y0) * 0.15
    X0, X1, Y0, Y1 = x0 - mx, x1 + mx, y0 - my, y1 + my
    kept = []
    for s in strokes:
        arr = np.asarray(s)
        inside = (arr[:, 0] >= X0) & (arr[:, 0] <= X1) & (arr[:, 1] >= Y0) & (arr[:, 1] <= Y1)
        if inside.any():
            kept.append(s)
    removed = len(strokes) - len(kept)
    if removed:
        print(f"고립 획 {removed}개 제외 (본문 상자 밖)")
    return kept or strokes


def map_aligned(strokes: list[list[Point]], source: Rect, target: Rect, align: str) -> list[list[Point]]:
    """비율을 유지해 target 안에 넣는다. 가로는 가운데, 세로는 align 에 따른다."""
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    scale = min(tw / max(sw, 1e-6), th / max(sh, 1e-6))
    ox = tx + (tw - sw * scale) / 2.0
    oy = ty if align == "top" else ty + (th - sh * scale) / 2.0
    return [[(ox + (x - sx) * scale, oy + (y - sy) * scale) for x, y in s] for s in strokes]


def clamp_to(strokes: list[list[Point]], rect: Rect) -> list[list[Point]]:
    """어떤 점도 필기 영역 밖으로 나가지 않게 한다. 가장자리 접근은 자동 스크롤을 부른다."""
    x, y, w, h = rect
    return [[(min(max(px, x), x + w), min(max(py, y), y + h)) for px, py in s] for s in strokes]


def plan(strokes_path: Path, win: winplat.Window, cfg: Config,
         strict_paper: bool = False) -> tuple[list[list[Point]], Rect]:
    target = cfg.target_rect
    if target is None and cfg.detect_paper:
        target = detect_paper_rect(win, cfg)
        if target is not None:
            print(f"종이 검출: {tuple(round(v, 1) for v in target)}")
    if target is None and cfg.detect_paper:
        target = calibrated_paper_rect(win, cfg)
        if target is not None:
            print(f"종이 검출 실패 - 보정값 사용: {tuple(round(v, 1) for v in target)}")
        elif strict_paper:
            # 창 비율 추정은 종이 밖 회색 영역까지 포함한다. 실제로 그리면
            # 빈 공간에 획이 찍히므로, 차라리 멈추고 알린다.
            raise RuntimeError(
                "GoodNotes 종이 영역을 찾지 못했습니다. 종이를 가리는 팝업이 있으면 닫고, "
                "그래도 안 되면 노트북을 연 상태에서 'python worker.py --calibrate' 를 다시 실행하세요."
            )
    if target is None:
        visible = winplat.clamp_to_screen(win.rect)
        target = derive_target_rect(visible, cfg.target_inset)
        print(f"종이 검출 실패 - 창 비율로 추정(dry-run 전용): {tuple(round(v, 1) for v in target)}")
    winplat.assert_on_screen(target, "target_rect")

    strokes = load_strokes(strokes_path)
    strokes = sample_strokes(strokes, cfg.sample_step)
    if cfg.pacing == "legacy":
        strokes, _ = fit_time_budget(strokes, cfg)

    page = read_page_meta(strokes_path)
    if page is not None:
        source = (0.0, 0.0, page[0], page[1])
        print(f"종이 좌표계 사용: {page[0]:.0f} x {page[1]:.0f}")
    else:
        if cfg.drop_far_outliers:
            strokes = drop_isolated_strokes(strokes)
        source = stroke_bbox(strokes)

    if cfg.fit == "stretch":
        mapped = map_strokes(strokes, source, target, "stretch")
    else:
        mapped = map_aligned(strokes, source, target, cfg.align)
    mapped = clamp_to(mapped, target)
    winplat.assert_on_screen(stroke_bbox(mapped), "매핑된 stroke 영역")
    return mapped, target


def inject(strokes_path: Path, cfg: Config, execute: bool = False,
           progress_cb=None) -> dict[str, Any]:
    """strokes.json 을 GoodNotes 현재 페이지에 필기한다. 요약 dict 를 돌려준다."""
    winplat.ensure_dpi_aware()
    win = acquire_window(cfg) if execute else winplat.restore_if_minimized(winplat.find_goodnotes())
    mapped, target = plan(strokes_path, win, cfg, strict_paper=execute)

    if cfg.pacing == "legacy":
        playback, est = mapped, estimate_seconds(mapped, cfg)
    else:
        playback, est = prepare_input(mapped, cfg)

    summary = {
        "window": {"exe": win.exe, "cls": win.cls, "rect": win.rect},
        "stroke_count": len(playback),
        "point_count": sum(len(s) for s in playback),
        "target_rect": tuple(round(v, 1) for v in target),
        "executed": False,
        "pacing": cfg.pacing,
    }
    summary["estimated_sec"] = round(est, 1)
    print(f"window : {win.exe} {win.rect}")
    print(f"strokes: {summary['stroke_count']}  points: {summary['point_count']}  "
          f"예상 {summary['estimated_sec']}초")
    print(f"target : {summary['target_rect']}")

    if not execute:
        print("dry-run: 실제 입력 없음 (--execute 로 실행)")
        return summary

    if cfg.select_pen:
        pen = tool_point(win, cfg.pen_tool_dx, cfg)
        if tool_is_selected(win, cfg.pen_tool_dx, cfg):
            print("펜 도구 이미 선택됨 - 클릭 생략 (다시 누르면 옵션 팝업이 열린다)")
        else:
            print(f"펜 도구 클릭: {tuple(round(v, 1) for v in pen)}")
            winplat.click(pen)
            time.sleep(0.4)
            ensure_front(win, cfg, "펜 도구 클릭")

    if cfg.select_pen:
        select_pen_width(win, cfg, read_stroke_width_rel(strokes_path))
        ensure_front(win, cfg, "펜 굵기")

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

    baseline = detect_paper_rect(win, cfg, retries=2) if cfg.scroll_guard else None

    def _check(done_count: int, total: int) -> None:
        if progress_cb is not None:
            try:
                progress_cb(done_count, total)
            except Exception:
                pass                     # 진행 보고 실패가 필기를 멈추면 안 된다
        if baseline is None:
            return
        now = _detect_paper_once(win, cfg)
        if now is None:
            return
        shift = max(abs(now[0] - baseline[0]), abs(now[1] - baseline[1]))
        if shift > cfg.scroll_tolerance:
            raise RuntimeError(
                f"필기 중 페이지가 움직였습니다 ({shift:.0f}px, {done_count}/{total}획 지점). "
                "이후 획이 전부 어긋나므로 중단합니다. "
                "GoodNotes 에서 페이지를 고정하거나 paper_margin 을 더 키우세요."
            )

    started = time.time()
    if cfg.pacing == "legacy":
        winplat.replay_strokes(mapped, cfg.point_delay, cfg.stroke_delay, guard=win,
                               periodic_check=_check if (cfg.scroll_guard or progress_cb) else None)
    else:
        winplat.replay_strokes(playback, 0.0, cfg.stroke_delay, guard=win,
                               periodic_check=_check if (cfg.scroll_guard or progress_cb) else None,
                               pre_down=cfg.pre_down, down_dwell=cfg.down_dwell,
                               landed_check=stroke_landed)
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
