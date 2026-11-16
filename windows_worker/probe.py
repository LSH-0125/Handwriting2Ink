"""GoodNotes for Windows 프로브 - version2 착수 전 결정 실험.

세 커맨드:
  python probe.py info          창 핸들 / DPI / 현재 클립보드 포맷 (입력 없음, 안전)
  python probe.py draw          GoodNotes 전면화 후 짧은 선 하나 그림 (펜은 미리 선택)
  python probe.py clip [--dump] 클립보드 전체 포맷 열거 + blob 덤프

핫키(ctrl+p / ctrl+l)는 웹앱 셸에서 인쇄창/주소창을 열 수 있어 일부러 쓰지 않습니다.
펜 선택 / 올가미 / ctrl+c 는 손으로 하세요.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import sys
import time
from ctypes import wintypes
from pathlib import Path

TITLE_HINTS = ("goodnotes", "good notes")
OUT_DIR = Path(__file__).resolve().parent / "probe_out"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_STANDARD = {
    1: "CF_TEXT", 2: "CF_BITMAP", 3: "CF_METAFILEPICT", 4: "CF_SYLK", 5: "CF_DIF",
    6: "CF_TIFF", 7: "CF_OEMTEXT", 8: "CF_DIB", 9: "CF_PALETTE", 10: "CF_PENDATA",
    11: "CF_RIFF", 12: "CF_WAVE", 13: "CF_UNICODETEXT", 14: "CF_ENHMETAFILE",
    15: "CF_HDROP", 16: "CF_LOCALE", 17: "CF_DIBV5",
}
# HGLOBAL이 아니라서 GlobalLock을 쓸 수 없는 핸들형 포맷
NON_HGLOBAL = {2, 3, 9, 14}

# 64비트 핸들이 int로 잘리지 않게 프로토타입을 고정한다 (이게 없으면 GlobalSize가 0)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [wintypes.HANDLE]


# --------------------------------------------------------------- DPI / 창
def set_dpi_aware() -> str:
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        return "per-monitor-v1"
    except Exception:
        try:
            user32.SetProcessDPIAware()
            return "system"
        except Exception:
            return "none"


def enum_goodnotes_windows() -> list[dict]:
    found: list[dict] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if not any(h in title.lower() for h in TITLE_HINTS):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        client = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(client))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = "<?>"
        hp = kernel32.OpenProcess(0x1000, False, pid.value)
        if hp:
            try:
                sz = wintypes.DWORD(260)
                nb = ctypes.create_unicode_buffer(260)
                if kernel32.QueryFullProcessImageNameW(hp, 0, nb, ctypes.byref(sz)):
                    exe = Path(nb.value).name
            finally:
                kernel32.CloseHandle(hp)
        found.append({
            "hwnd": int(hwnd), "title": title, "class": cls.value, "pid": int(pid.value),
            "exe": exe, "iconic": bool(user32.IsIconic(hwnd)),
            "rect": (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
            "client": (client.right, client.bottom),
        })
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def pick_window() -> dict:
    wins = enum_goodnotes_windows()
    if not wins:
        raise RuntimeError(
            "GoodNotes 창을 찾지 못했습니다. GoodNotes for Windows를 실행하고 노트를 하나 열어두세요."
        )
    # 가장 큰 창 = 실제 문서 창
    return max(wins, key=lambda w: w["rect"][2] * w["rect"][3])


def activate(hwnd: int) -> bool:
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # 포그라운드 락 우회: 현재 전면 스레드에 붙었다 떼기
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None)
    tid_me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(tid_me, tid_fg, True)
    ok = bool(user32.SetForegroundWindow(hwnd))
    user32.AttachThreadInput(tid_me, tid_fg, False)
    time.sleep(0.6)
    return ok and user32.GetForegroundWindow() == hwnd


# --------------------------------------------------------------- 클립보드
def clipboard_formats() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    if not user32.OpenClipboard(None):
        raise RuntimeError(f"OpenClipboard 실패 (err={ctypes.get_last_error()})")
    try:
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if fmt == 0:
                break
            name = CF_STANDARD.get(fmt)
            if name is None:
                buf = ctypes.create_unicode_buffer(512)
                n = user32.GetClipboardFormatNameW(fmt, buf, 512)
                name = buf.value if n else f"<unnamed {fmt}>"
            out.append((fmt, name))
    finally:
        user32.CloseClipboard()
    return out


def read_format(fmt: int) -> bytes | None:
    if fmt in NON_HGLOBAL:
        return None
    if not user32.OpenClipboard(None):
        return None
    try:
        h = user32.GetClipboardData(fmt)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            size = kernel32.GlobalSize(h)
            return ctypes.string_at(ptr, size)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def safe_name(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in name)[:80]


def cmd_clip(dump: bool) -> None:
    formats = clipboard_formats()
    if not formats:
        print("클립보드가 비어 있습니다.")
        return
    print(f"클립보드 포맷 {len(formats)}개\n")
    if dump:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fmt, name in formats:
        data = read_format(fmt)
        if data is None:
            print(f"  [{fmt:>6}] {name:<42} (핸들형/읽기불가)")
            continue
        head = data[:16].hex(" ")
        print(f"  [{fmt:>6}] {name:<42} {len(data):>10,} B  md5={hashlib.md5(data).hexdigest()[:12]}  {head}")
        if dump:
            p = OUT_DIR / f"{fmt}_{safe_name(name)}.bin"
            p.write_bytes(data)
    if dump:
        print(f"\n덤프: {OUT_DIR}")
    # 잉크 후보 판정
    inkish = [n for _, n in formats
              if "goodnote" in n.lower() or "ink" in n.lower() or "Web Custom Format" in n]
    print("\n잉크 후보 포맷:", inkish if inkish else "없음")


# --------------------------------------------------------------- watch
def foreground_info() -> str:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "<없음>"
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = "<?>"
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if h:
        try:
            size = wintypes.DWORD(260)
            nb = ctypes.create_unicode_buffer(260)
            if kernel32.QueryFullProcessImageNameW(h, 0, nb, ctypes.byref(size)):
                exe = Path(nb.value).name
        finally:
            kernel32.CloseHandle(h)
    return f"{exe} pid={pid.value} class={cls.value!r} title={buf.value!r}"


def cmd_watch(timeout: float, dump: bool) -> None:
    """클립보드가 바뀌는 순간을 잡아 '누가 복사했는지'까지 같이 기록한다."""
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    start = user32.GetClipboardSequenceNumber()
    print(f"클립보드 감시 시작 (seq={start}, 최대 {timeout:.0f}초)")
    print("지금 GoodNotes 앱에서 펜으로 쓴 획을 올가미로 감싸고 Ctrl+C 하세요.")
    print()
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = user32.GetClipboardSequenceNumber()
        if cur != start:
            time.sleep(0.3)  # 쓰기 완료 대기
            print(f"변경 감지 (seq {start} -> {cur})")
            print(f"복사 주체: {foreground_info()}")
            print()
            cmd_clip(dump)
            return
        time.sleep(0.15)
    print("시간 초과. 클립보드 변경이 없었습니다.")


# --------------------------------------------------------------- draw
def cmd_draw(execute: bool) -> None:
    mode = set_dpi_aware()
    win = pick_window()
    x, y, w, h = win["rect"]
    print(f"DPI awareness: {mode}")
    print(f"창: hwnd={win['hwnd']} class={win['class']!r} rect={win['rect']} client={win['client']}")

    import pyautogui
    sw, sh = pyautogui.size()
    print(f"pyautogui.size()={sw}x{sh}  (창 rect와 스케일이 맞는지 확인)")

    # 창 중앙 근처에 가로 120px 선
    cx, cy = x + w * 0.45, y + h * 0.5
    pts = [(cx + i * 4, cy + (8 if (i // 6) % 2 else -8)) for i in range(30)]
    print(f"그릴 선: {len(pts)}점, {pts[0]} → {pts[-1]}")

    if not execute:
        print("\ndry-run. 실제로 그리려면 --execute. GoodNotes에서 펜을 미리 선택해두세요.")
        return

    if not activate(win["hwnd"]):
        print("WARN: SetForegroundWindow가 실패했거나 전면 전환이 확인되지 않았습니다.")
    print("2초 후 입력. 중단은 마우스를 화면 좌상단 모서리로.")
    time.sleep(2.0)

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True
    pyautogui.moveTo(*pts[0], duration=0.1)
    pyautogui.mouseDown(button="left")
    try:
        for px, py in pts[1:]:
            pyautogui.moveTo(px, py)
            time.sleep(0.004)
    finally:
        pyautogui.mouseUp(button="left")
    print("done. GoodNotes에 선이 그려졌는지 눈으로 확인하세요.")


def cmd_info() -> None:
    mode = set_dpi_aware()
    print(f"DPI awareness: {mode}")
    print(f"python: {sys.version.split()[0]}  {sys.executable}\n")
    wins = enum_goodnotes_windows()
    if not wins:
        print("GoodNotes 창: 없음 (앱을 실행하고 노트를 여세요)")
    else:
        print(f"GoodNotes 창 {len(wins)}개:")
        for w in wins:
            flag = " [최소화]" if w["iconic"] else ""
            print(f"  exe={w['exe']:<22} class={w['class']:<30} rect={w['rect']}{flag}")
            print(f"      hwnd={w['hwnd']} pid={w['pid']} title={w['title']!r}")
    print()
    try:
        cmd_clip(dump=False)
    except Exception as e:
        print(f"클립보드 읽기 실패: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description="GoodNotes for Windows 프로브")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    d = sub.add_parser("draw")
    d.add_argument("--execute", action="store_true")
    c = sub.add_parser("clip")
    c.add_argument("--dump", action="store_true")
    w = sub.add_parser("watch")
    w.add_argument("--dump", action="store_true")
    w.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    if args.cmd == "info":
        cmd_info()
    elif args.cmd == "draw":
        cmd_draw(args.execute)
    elif args.cmd == "watch":
        cmd_watch(args.timeout, args.dump)
    else:
        cmd_clip(args.dump)


if __name__ == "__main__":
    main()
