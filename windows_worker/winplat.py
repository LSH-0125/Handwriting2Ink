"""GoodNotes for Windows 플랫폼 레이어.

mac의 osascript / cmd+키 / NSPasteboard 를 대체하는 세 가지만 담는다.
  - 창 제어:   EnumWindows + SetForegroundWindow (WinUI3: Microsoft.UI.Windowing.Window)
  - 잉크 입력: DPI-aware 절대좌표 mouseDown → move → mouseUp
  - 클립보드:  RegisterClipboardFormat 기반 커스텀 포맷 read/write

서비스 구성은 Windows 노트북 + 아이패드뿐이다. 맥은 없다.
mac 관련 언급은 '왜 이렇게 만들었는지' 를 남겨둔 설계 근거일 뿐 실행 경로가 아니다.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
kernel32.GlobalAlloc.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [wintypes.HANDLE]

user32.IsIconic.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

GMEM_MOVEABLE = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# 제목에 "Goodnotes" 가 들어간 브라우저 탭을 앱으로 오인하면 안 된다
BROWSER_EXES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
                "opera.exe", "whale.exe", "vivaldi.exe", "iexplore.exe"}
SW_RESTORE, SW_MAXIMIZE = 9, 3
SM_VIRTUAL = (76, 77, 78, 79)  # X, Y, CX, CY of virtual screen
TITLE_HINTS = ("goodnotes", "good notes")

Point = tuple[float, float]
Rect = tuple[float, float, float, float]


# ----------------------------------------------------------------- DPI
_dpi_set = False


def ensure_dpi_aware() -> str:
    """pyautogui 좌표가 모니터 스케일링으로 틀어지지 않게 선언한다. 반드시 입력 전에."""
    global _dpi_set
    if _dpi_set:
        return "already"
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        _dpi_set = True
        return "per-monitor"
    except Exception:
        try:
            user32.SetProcessDPIAware()
            _dpi_set = True
            return "system"
        except Exception:
            return "none"


# ----------------------------------------------------------------- 창
@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    cls: str
    rect: Rect
    exe: str = "<?>"


def process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return "<?>"
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(260)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return "<?>"
    finally:
        kernel32.CloseHandle(h)


def virtual_screen() -> Rect:
    """가상 화면 크기. 물리 픽셀로 받으려면 DPI 선언이 먼저여야 한다.

    선언 전에는 논리 좌표(125% 스케일이면 1536x864)가 나와서 GetWindowRect 의
    물리 좌표(1920x1080)와 단위가 어긋난다. 그 상태로 비교하면 정상적으로
    최대화된 창을 '화면 밖'으로 오판한다.
    """
    ensure_dpi_aware()
    x, y, w, h = (user32.GetSystemMetrics(m) for m in SM_VIRTUAL)
    return float(x), float(y), float(w), float(h)


def _measure(hwnd: int, title: str, cls: str, exe: str = "<?>") -> Window:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return Window(hwnd, title, cls,
                  (r.left, r.top, r.right - r.left, r.bottom - r.top), exe)


def restore_if_minimized(win: Window) -> Window:
    """최소화된 창의 GetWindowRect 는 (-32000, -32000) 을 돌려준다.

    그 좌표로 좌표 매핑을 하면 화면 밖에 필기해 조용히 아무 일도 일어나지 않는다.
    반드시 복원하고 다시 측정한다.
    """
    if not user32.IsIconic(win.hwnd):
        return win
    user32.ShowWindow(win.hwnd, SW_RESTORE)
    time.sleep(0.5)
    return _measure(win.hwnd, win.title, win.cls, win.exe)


def clamp_to_screen(rect: Rect) -> Rect:
    """창 rect 를 실제로 보이는 영역으로 깎는다.

    최대화된 Windows 창은 보이지 않는 리사이즈 프레임 때문에 화면보다 사방 ~9px
    크게 보고된다. 그 좌표를 그대로 쓰면 필기 영역이 화면 밖으로 삐져나간다.
    """
    vx, vy, vw, vh = virtual_screen()
    x, y, w, h = rect
    left, top = max(x, vx), max(y, vy)
    right, bottom = min(x + w, vx + vw), min(y + h, vy + vh)
    return left, top, max(right - left, 0.0), max(bottom - top, 0.0)


def assert_on_screen(rect: Rect, label: str = "rect", min_overlap: float = 0.5) -> None:
    """최소화 창의 (-32000, -32000) 같은 좌표를 걸러낸다.

    완전 포함은 요구하지 않는다 (최대화 창이 정상적으로 화면을 넘기므로).
    중심이 화면 안에 있고 면적의 min_overlap 이상이 겹치는지만 본다.
    """
    x, y, w, h = rect
    if w <= 1 or h <= 1:
        raise RuntimeError(f"{label} 크기가 비정상입니다: {rect}")
    vx, vy, vw, vh = virtual_screen()
    cx, cy = x + w / 2, y + h / 2
    inside = vx <= cx <= vx + vw and vy <= cy <= vy + vh
    _, _, ow, oh = clamp_to_screen(rect)
    ratio = (ow * oh) / (w * h)
    if not inside or ratio < min_overlap:
        raise RuntimeError(
            f"{label} 이 화면 밖입니다: {tuple(round(v, 1) for v in rect)} "
            f"(가상 화면 {tuple(round(v, 1) for v in (vx, vy, vw, vh))}, 겹침 {ratio:.0%}). "
            "창이 최소화됐거나 target_rect 가 잘못됐습니다."
        )


def list_goodnotes_windows() -> list[Window]:
    """제목에 GoodNotes 가 들어간 보이는 창 전부. 진단용 - 브라우저 탭도 포함된다."""
    ensure_dpi_aware()  # 선언 전 GetWindowRect 는 논리좌표를 준다 (125%면 1536x864)
    found: list[Window] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if not any(h in buf.value.lower() for h in TITLE_HINTS):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        found.append(Window(int(hwnd), buf.value, cls.value,
                            (r.left, r.top, r.right - r.left, r.bottom - r.top),
                            process_name(int(hwnd))))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def find_goodnotes() -> Window:
    """실제 GoodNotes 앱 창 하나를 고른다."""
    found = list_goodnotes_windows()
    if not found:
        raise RuntimeError("GoodNotes 창을 찾지 못했습니다. 앱을 실행하고 노트를 여세요.")

    # 제목이 아니라 프로세스로 판정한다. web.goodnotes.com 을 띄운 브라우저 탭도
    # 제목에 "Goodnotes" 가 들어가므로 제목만 보면 엉뚱한 창에 필기한다.
    app = [w for w in found if "goodnote" in w.exe.lower()]
    if not app:
        others = [f"{w.exe}({w.cls})" for w in found]
        raise RuntimeError(
            "GoodNotes 앱 창을 찾지 못했습니다. 제목이 맞는 창은 있지만 "
            f"전부 다른 프로세스입니다: {others}. "
            "브라우저 탭이 아니라 GoodNotes for Windows 앱을 실행하세요."
        )
    found = app
    live = [w for w in found if not user32.IsIconic(w.hwnd)]
    if live:
        return max(live, key=lambda w: w.rect[2] * w.rect[3])
    # 전부 최소화 상태라면 가장 큰 것을 복원해서 쓴다
    return restore_if_minimized(max(found, key=lambda w: w.rect[2] * w.rect[3]))


def activate(win: Window, timeout: float = 3.0) -> bool:
    """mac의 `osascript tell application "Goodnotes" to activate` 대체."""
    user32.ShowWindow(win.hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None)
    tid_me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(tid_me, tid_fg, True)
    try:
        user32.SetForegroundWindow(win.hwnd)
    finally:
        user32.AttachThreadInput(tid_me, tid_fg, False)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if user32.GetForegroundWindow() == win.hwnd:
            time.sleep(0.3)
            return True
        time.sleep(0.1)
    return False


def is_frontmost(win: Window) -> bool:
    """mac의 System Events frontmost 검증 대체. 입력 직전에 반드시 확인."""
    return user32.GetForegroundWindow() == win.hwnd


def maximize(win: Window) -> Window:
    user32.ShowWindow(win.hwnd, SW_MAXIMIZE)
    time.sleep(0.5)
    return _measure(win.hwnd, win.title, win.cls, win.exe)


# ----------------------------------------------------------------- 입력
def replay_strokes(
    strokes: list[list[Point]],
    point_delay: float = 0.004,
    stroke_delay: float = 0.06,
    guard: Window | None = None,
) -> None:
    """획 좌표열을 실제 마우스 입력으로 재생한다.

    mac의 drag 방식 대신 down_move 를 고정으로 쓴다. WinUI3+WebView2 셸에서
    dragTo 반복은 pointer 이벤트가 유실되어 획이 끊긴다.
    """
    ensure_dpi_aware()
    import pyautogui

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    for i, stroke in enumerate(strokes):
        if guard is not None and not is_frontmost(guard):
            raise RuntimeError(f"GoodNotes가 전면이 아닙니다 (stroke {i}에서 중단)")
        pyautogui.moveTo(*stroke[0], duration=0.05)
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


def _interpolate(a: Point, b: Point, steps: int) -> list[Point]:
    steps = max(2, steps)
    return [(a[0] + (b[0] - a[0]) * i / (steps - 1),
             a[1] + (b[1] - a[1]) * i / (steps - 1)) for i in range(steps)]


def drag_select(rect: Rect, steps: int = 40, step_delay: float = 0.02) -> None:
    """선택 도구로 영역을 잡는다. 좌상단에서 눌러 우하단에서 뗀다.

    둘레를 따라 돌아 시작점으로 복귀하면 선택이 그대로 풀린다. 끝점이
    우하단이어야 한다.

    이동은 duration 인자가 아니라 작은 sleep 을 끼운 개별 moveTo 로 한다.
    WebView2 캔버스는 pyautogui 의 duration 보간을 포인터 이벤트로 받지 못한다.
    """
    ensure_dpi_aware()
    import pyautogui

    x, y, w, h = rect
    pts = _interpolate((x, y), (x + w, y + h), steps)
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True
    pyautogui.moveTo(*pts[0], duration=0.2)
    time.sleep(0.2)
    pyautogui.mouseDown(button="left")
    time.sleep(0.15)
    try:
        for px, py in pts[1:]:
            pyautogui.moveTo(px, py)
            time.sleep(step_delay)
    finally:
        time.sleep(0.15)
        pyautogui.mouseUp(button="left")


def right_click(point: Point) -> None:
    """선택 영역 안에서 컨텍스트 메뉴를 연다. Windows 에서 복사는 이 메뉴로만 된다."""
    ensure_dpi_aware()
    import pyautogui

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True
    pyautogui.moveTo(*point, duration=0.2)
    time.sleep(0.2)
    pyautogui.rightClick()
    time.sleep(0.8)


def click(point: Point) -> None:
    ensure_dpi_aware()
    import pyautogui

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True
    pyautogui.moveTo(*point, duration=0.2)
    time.sleep(0.15)
    pyautogui.click()
    time.sleep(0.4)


def hotkey(combo: str) -> None:
    """'ctrl+c' 형식. mac의 cmd+ 를 ctrl+ 로 바꾼 것."""
    ensure_dpi_aware()
    import pyautogui

    keys = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not keys:
        raise RuntimeError("단축키가 비어 있습니다.")
    pyautogui.hotkey(*keys)
    time.sleep(0.25)


# ----------------------------------------------------------------- 클립보드
def _open(retries: int = 10):
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise RuntimeError(f"OpenClipboard 실패 (err={ctypes.get_last_error()})")


def register_format(name: str) -> int:
    fmt = user32.RegisterClipboardFormatW(name)
    if fmt == 0:
        raise RuntimeError(f"RegisterClipboardFormat 실패: {name}")
    return fmt


def list_formats() -> list[tuple[int, str]]:
    out = []
    _open()
    try:
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if fmt == 0:
                break
            buf = ctypes.create_unicode_buffer(512)
            n = user32.GetClipboardFormatNameW(fmt, buf, 512)
            out.append((fmt, buf.value if n else f"<standard {fmt}>"))
    finally:
        user32.CloseClipboard()
    return out


def read_ink(format_name: str) -> bytes | None:
    """mac의 NSPasteboard dataForType_ 대체."""
    fmt = register_format(format_name)
    _open()
    try:
        h = user32.GetClipboardData(fmt)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            return ctypes.string_at(ptr, kernel32.GlobalSize(h))
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def write_ink(format_name: str, payload: bytes, keep_others: bool = False) -> None:
    """mac에서 뽑은 .bin 을 Windows 클립보드에 올린다. 역방향 호환성 검증용."""
    fmt = register_format(format_name)
    _open()
    try:
        if not keep_others:
            user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not h:
            raise RuntimeError("GlobalAlloc 실패")
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            raise RuntimeError("GlobalLock 실패")
        try:
            ctypes.memmove(ptr, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(fmt, h):
            raise RuntimeError(f"SetClipboardData 실패 (err={ctypes.get_last_error()})")
    finally:
        user32.CloseClipboard()
