"""GoodNotes for Windows 입력 재생 속도 실험.

같은 획을 '재생 방식' 만 바꿔 여러 칸에 그리고, 화면 캡처를 이상적 경로와
비교한다. 어느 속도부터 GoodNotes 가 중간 점을 놓치는지 잰다.

  python pace_test.py --strokes <strokes.json> [--line_max_y 100] [--scale 0.55]

배경: 페이지 모드 추출은 RDP 로 꼭짓점만 남겨 획당 점이 6개 남짓이다.
워커가 이를 4ms 간격으로 보내면 한 획이 20ms 만에 끝나는데, 앱은 대략
한 프레임(16ms+)에 한 번 커서 위치를 읽으므로 꼭짓점 대부분이 사라진다.
(Windows 는 WM_MOUSEMOVE 를 합치므로 앱이 바쁘면 중간 위치가 유실된다.)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "windows_worker"))
sys.path.insert(0, str(REPO / "server" / "pipeline"))

import controller  # noqa: E402
import winplat  # noqa: E402
from goodnotes_writer import load_strokes  # noqa: E402

OUT = Path(__file__).resolve().parent / "_data"

VARIANTS = [
    dict(name="A_vertex_4ms", densify=0, delay=0.004, pre=0.0, down=0.0, up=0.0),     # 현재 워커
    dict(name="B_d3px_4ms", densify=3, delay=0.004, pre=0.01, down=0.02, up=0.02),
    dict(name="C_d3px_10ms", densify=3, delay=0.010, pre=0.01, down=0.02, up=0.02),
    dict(name="D_vertex_20ms", densify=0, delay=0.020, pre=0.01, down=0.02, up=0.02),
    dict(name="E_vertex_35ms", densify=0, delay=0.035, pre=0.01, down=0.03, up=0.03),
    dict(name="F_d2px_16ms", densify=2, delay=0.016, pre=0.01, down=0.02, up=0.02),
]


def densify(stroke, step):
    if step <= 0 or len(stroke) < 2:
        return list(stroke)
    out = [stroke[0]]
    for (x0, y0), (x1, y1) in zip(stroke, stroke[1:]):
        n = max(1, int(np.ceil(np.hypot(x1 - x0, y1 - y0) / step)))
        out += [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(1, n + 1)]
    return out


def replay(strokes, v, guard):
    import pyautogui
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True
    t0 = time.perf_counter()
    for s in strokes:
        if not winplat.is_frontmost(guard):
            raise RuntimeError("GoodNotes 전면 상실 - 중단")
        pts = densify(s, v["densify"])
        pyautogui.moveTo(*pts[0])
        if v["pre"]:
            time.sleep(v["pre"])
        pyautogui.mouseDown(button="left")
        try:
            if v["down"]:
                time.sleep(v["down"])
            for x, y in pts[1:]:
                pyautogui.moveTo(x, y)
                if v["delay"]:
                    time.sleep(v["delay"])
            if v["up"]:
                time.sleep(v["up"])
        finally:
            pyautogui.mouseUp(button="left")
        time.sleep(0.06)
    return time.perf_counter() - t0


def grab_screen():
    from PIL import ImageGrab
    return np.array(ImageGrab.grab(all_screens=False).convert("RGB")).astype(np.int16)


def paper_bounds(img):
    warm = controller.paper_mask(img)
    cols = np.where(warm.mean(axis=0) > 0.3)[0]
    rows = np.where(warm.mean(axis=1) > 0.3)[0]
    x0, x1 = controller._longest_run(cols)
    y0, y1 = controller._longest_run(rows)
    return x0, y0, x1, y1


def ink_mask(img):
    g = img.mean(axis=2)
    return g < 200


def score(img, strokes, box, tol=2.5):
    """이상 경로(꼭짓점을 직선으로 이은 것) 대비 캡처 잉크의 재현율/정밀도."""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    crop = img[y0:y1, x0:x1]
    ink = ink_mask(crop).astype(np.uint8)
    ideal = np.zeros(ink.shape, np.uint8)
    for s in strokes:
        p = np.round(np.array(s) - [x0, y0]).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(ideal, [p], False, 1, 1)
    d_to_ink = cv2.distanceTransform(1 - ink, cv2.DIST_L2, 3)
    d_to_ideal = cv2.distanceTransform(1 - ideal, cv2.DIST_L2, 3)
    rec = float((d_to_ink[ideal > 0] <= tol).mean()) if ideal.any() else 0.0
    prec = float((d_to_ideal[ink > 0] <= tol).mean()) if ink.any() else 0.0
    f1 = 2 * rec * prec / max(rec + prec, 1e-9)
    return dict(recall=round(rec, 3), precision=round(prec, 3), f1=round(f1, 3),
                ink_px=int(ink.sum()), ideal_px=int(ideal.sum()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strokes", required=True)
    ap.add_argument("--line_max_y", type=float, default=100.0)
    ap.add_argument("--scale", type=float, default=0.55)
    ap.add_argument("--control_target", default="713.0,308.8,494.0,587.5",
                    help="이미 그려진 대조군(현재 워커)의 target_rect")
    ap.add_argument("--page", default="527,249")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    cfg = controller.load_config(REPO / "windows_worker" / "config.json")
    winplat.ensure_dpi_aware()
    win = controller.acquire_window(cfg) if not args.dry else winplat.find_goodnotes()
    time.sleep(0.5)

    all_strokes = load_strokes(Path(args.strokes))
    pw, ph = [float(v) for v in args.page.split(",")]

    # 대조군: 워커가 실제로 보낸 좌표를 재구성
    ctrl_target = tuple(float(v) for v in args.control_target.split(","))
    ctrl = controller.clamp_to(controller.map_aligned(all_strokes, (0, 0, pw, ph), ctrl_target, "top"), ctrl_target)

    img0 = grab_screen()
    X0, Y0, X1, Y1 = paper_bounds(img0)
    paper_ink = ink_mask(img0[Y0:Y1, X0:X1])
    rows_with_ink = np.where(paper_ink.sum(axis=1) > 0)[0]
    ink_bottom = Y0 + (rows_with_ink.max() if len(rows_with_ink) else 0)
    W, H = X1 - X0, Y1 - Y0
    rx0, rx1 = X0 + 0.10 * W, X1 - 0.10 * W
    ry0, ry1 = ink_bottom + 45, Y1 - 0.13 * H
    print(f"paper=({X0},{Y0})-({X1},{Y1})  ink_bottom={ink_bottom}  region=({rx0:.0f},{ry0:.0f})-({rx1:.0f},{ry1:.0f})")
    if ry1 - ry0 < 240:
        raise SystemExit("빈 공간이 부족합니다")

    line = [s for s in all_strokes if max(p[1] for p in s) < args.line_max_y]
    lx0 = min(p[0] for s in line for p in s); ly0 = min(p[1] for s in line for p in s)
    cols, rows = 2, 3
    bw, bh = (rx1 - rx0) / cols, (ry1 - ry0) / rows
    boxes, mapped = [], []
    for i, v in enumerate(VARIANTS):
        c, r = i % cols, i // cols
        bx, by = rx0 + c * bw, ry0 + r * bh
        m = [[(bx + 10 + (x - lx0) * args.scale, by + 8 + (y - ly0) * args.scale) for x, y in s] for s in line]
        boxes.append((bx, by, bx + bw, by + bh)); mapped.append(m)
        if paper_ink[int(by) - Y0:int(by + bh) - Y0, int(bx) - X0:int(bx + bw) - X0].any():
            raise SystemExit(f"{v['name']} 칸에 이미 잉크가 있습니다")
    npts = sum(len(s) for s in line)
    print(f"line strokes={len(line)} vertices={npts} scale={args.scale}")
    if args.dry:
        return

    controller.ensure_front(win, cfg, "실험 시작")
    timings = {}
    for v, m in zip(VARIANTS, mapped):
        before = paper_bounds(grab_screen())
        t = replay(m, v, win)
        after = paper_bounds(grab_screen())
        shift = max(abs(a - b) for a, b in zip(before, after))
        timings[v["name"]] = round(t, 2)
        print(f"  {v['name']:<16} {t:5.2f}s  paper shift {shift}px", flush=True)
        if shift > 8:
            raise SystemExit("페이지가 움직였습니다 - 중단")
        time.sleep(0.4)

    time.sleep(1.0)
    img = grab_screen()
    stamp = time.strftime("%H%M%S")
    cv2.imwrite(str(OUT / f"pace_{stamp}.png"), cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2BGR))
    res = {}
    cb = controller.stroke_bbox(ctrl)
    res["CONTROL_worker_now(scale~0.63)"] = score(img, ctrl, (cb[0] - 6, cb[1] - 6, cb[0] + cb[2] + 6, cb[1] + cb[3] + 6))
    for v, m, b in zip(VARIANTS, mapped, boxes):
        res[v["name"]] = score(img, m, b) | {"sec": timings[v["name"]]}
    for k, r in res.items():
        print(f"{k:<30} F1={r['f1']:.3f}  rec={r['recall']:.3f}  prec={r['precision']:.3f}  "
              f"ink={r['ink_px']:>5}  {r.get('sec', '')}")
    (OUT / f"pace_{stamp}.json").write_text(json.dumps(dict(variants=VARIANTS, results=res, boxes=boxes,
                                                            scale=args.scale), ensure_ascii=False, indent=1),
                                           encoding="utf-8")


if __name__ == "__main__":
    main()
