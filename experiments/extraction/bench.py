"""획 추출 변형 비교 벤치.

  python bench.py <stage> [--workers 4] [--viz]

지표 (합성 데이터, 정답 있음)
  prec   : 그린 선 픽셀 중 실제 잉크 근처(tol 이내)에 있는 비율. 낮을수록 '빈 공간에 그림'
  rec    : 실제 필기 중심선 중 그린 선 근처에 있는 비율. 낮을수록 '글씨 누락'
  f1     : 둘의 조화평균
  junk   : 잉크와 무관한 곳에 그린 선 길이(px)
  frag   : 정답 중심선 1000px 당 획 수 (낮을수록 덜 쪼개짐)
  est_s  : 워커 필기 시간 추정 = 점*0.004 + 획*0.21
지표 (실사진, 정답 없음 - 대리 지표)
  sup    : 그린 선 중 '배경 대비 어두운 곳' 위에 있는 비율 (prec 대리)
  cov    : 확실한 잉크 중심선 중 재현된 비율 (rec 대리)
  out    : 종이 밖에 그린 비율
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
HERE = Path(__file__).resolve().parent
PIPE = HERE.parents[1] / "server" / "pipeline"
sys.path.insert(0, str(PIPE))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

DATA = HERE / "_data"
TOL = 4.0
PT_COST, STROKE_COST = 0.004, 0.21


# ----------------------------------------------------------------- IO
def read_img(p: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)


def write_img(p: Path, im: np.ndarray, q: int = 85):
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = p.suffix.lower()
    params = [cv2.IMWRITE_JPEG_QUALITY, q] if ext == ".jpg" else []
    cv2.imencode(ext, im, params)[1].tofile(str(p))


def list_cases():
    cases = []
    for d in sorted((DATA / "synth").iterdir()):
        if (d / "input.jpg").exists():
            cases.append({"name": d.name, "dir": str(d), "kind": "synth"})
    for d in sorted((DATA / "real").iterdir()):
        if (d / "input.jpg").exists():
            cases.append({"name": d.name, "dir": str(d), "kind": "real"})
    return cases


# ----------------------------------------------------------------- 추출
def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def run_extract(img: np.ndarray, regions: dict | None, v: dict) -> list[np.ndarray]:
    from preprocess import (BinarizeParams, FilterParams, binarize, filter_components,
                            paper_mask, estimate_background, to_gray, simplify_stroke)
    from skeletonizer import skeletonize_zhang
    from stroke_extractor import extract_strokes

    bp = BinarizeParams(**v.get("bin", {}))
    fp = FilterParams(**v.get("filt", {}))
    smooth = v.get("smooth", 5)
    min_len, merge = v.get("min_len", 3), v.get("merge_angle", 60)
    out: list[np.ndarray] = []

    if v["mode"] == "crops":
        s = float(regions.get("scale", 1.0))
        ref = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 0.999 else img
        gray_ref = to_gray(ref)
        bg_ref = estimate_background(gray_ref, bp.bg_ksize) if bp.method.startswith("norm") else None
        pm = paper_mask(ref, erode_px=v.get("paper_erode", 8)) if v.get("paper") else None
        cs = v.get("crop_scale", 2.0)
        specs = [r for r in regions["regions"]
                 if r["type"] == "text" and r["source"] == v.get("region_source", "ocr_merged")]
        if v.get("shapes", True):
            specs += [r for r in regions["regions"] if r["type"] == "shape"]
        H, W = gray_ref.shape
        for r in specs:
            x, y, w, h = [int(t) for t in r["bbox"]]
            x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            g = gray_ref[y0:y1, x0:x1]
            bgc = bg_ref[y0:y1, x0:x1] if bg_ref is not None else None
            if cs > 1:
                g = cv2.resize(g, None, fx=cs, fy=cs, interpolation=cv2.INTER_CUBIC)
                if bgc is not None:
                    bgc = cv2.resize(bgc, (g.shape[1], g.shape[0]), interpolation=cv2.INTER_LINEAR)
            b = binarize(g, bp, bgc)
            b = filter_components(b, fp)
            if pm is not None:
                pmc = pm[y0:y1, x0:x1]
                if cs > 1:
                    pmc = cv2.resize(pmc, (b.shape[1], b.shape[0]), interpolation=cv2.INTER_NEAREST)
                b = cv2.bitwise_and(b, pmc)
            skel, _, _ = skeletonize_zhang(b)
            st = _quiet(extract_strokes, skel, min_stroke_length=min_len, merge_angle=merge,
                        image_gray=g, smooth_window=smooth)
            for p in st:
                if len(p):
                    out.append((p.astype(np.float32) / cs + np.array([x0, y0], np.float32)) / s)

    elif v["mode"] == "page":
        # rectify: 종이 네 모서리를 찾아 펴서 그 위에서 추출한다. 평가를 위해
        # 결과 좌표는 원본 사진 좌표로 되돌린다(Minv).
        Minv = None
        if v.get("rectify"):
            from preprocess import paper_quad, rectify
            q = paper_quad(img)
            if q is not None:
                img, M = rectify(img, q, trim=v.get("trim", 6))
                Minv = np.linalg.inv(M)
        f = min(1.0, v.get("page_max", 2000) / max(img.shape[:2]))
        work = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img
        ps = v.get("page_scale", 1.0)
        if v.get("page_min") and max(work.shape[:2]) * ps < v["page_min"]:
            ps = v["page_min"] / max(work.shape[:2])      # 작은 사진은 키워서 처리
        if ps > 1:
            work = cv2.resize(work, None, fx=ps, fy=ps, interpolation=cv2.INTER_CUBIC)
        g = to_gray(work)
        bgw = estimate_background(g, bp.bg_ksize) if bp.method.startswith("norm") else None
        if v.get("sw_target"):
            # 획 두께 정규화: 먼저 재고, 목표 두께가 되도록 작업 해상도를 바꾼다
            from preprocess import estimate_stroke_width
            b0 = filter_components(binarize(g, bp, bgw), FilterParams(min_area=fp.min_area))
            sw = estimate_stroke_width(b0)
            if sw > 0:
                f2 = float(np.clip(v["sw_target"] / sw, v.get("sw_min_f", 0.35), v.get("sw_max_f", 3.0)))
                f2 = min(f2, v.get("sw_page_cap", 3000) / max(work.shape[:2]))
                if abs(f2 - 1.0) > 0.12:
                    interp = cv2.INTER_AREA if f2 < 1 else cv2.INTER_CUBIC
                    work = cv2.resize(work, None, fx=f2, fy=f2, interpolation=interp)
                    ps *= f2
                    g = to_gray(work)
                    bgw = estimate_background(g, bp.bg_ksize) if bp.method.startswith("norm") else None
        b = binarize(g, bp, bgw)
        if v.get("paper"):
            pm = paper_mask(work, erode_px=v.get("paper_erode", 8))
            if pm is not None:
                b = cv2.bitwise_and(b, pm)
        b = filter_components(b, fp)
        skel, _, _ = skeletonize_zhang(b)
        st = _quiet(extract_strokes, skel, min_stroke_length=min_len, merge_angle=merge,
                    image_gray=g, smooth_window=smooth)
        k = f * ps
        out = [p.astype(np.float32) / k for p in st if len(p)]
        if Minv is not None:
            out = [cv2.perspectiveTransform(p.reshape(-1, 1, 2).astype(np.float64), Minv).reshape(-1, 2).astype(np.float32)
                   for p in out]
    else:
        raise ValueError(v["mode"])

    if v.get("chain", 0) > 0:
        from preprocess import chain_strokes
        out = chain_strokes(out, v["chain"])
    if v.get("min_pts"):
        out = [p for p in out if len(p) >= v["min_pts"]]
    if v.get("min_extent"):
        out = [p for p in out if float(np.ptp(p[:, 0]) + np.ptp(p[:, 1])) >= v["min_extent"]]
    if v.get("rdp", 0) > 0 or v.get("max_seg", 0) > 0:
        from preprocess import simplify_stroke
        out = [simplify_stroke(p, v.get("rdp", 0.0), v.get("max_seg", 0.0)) for p in out]
    return out


# ----------------------------------------------------------------- 지표
def rasterize(strokes, shape) -> np.ndarray:
    canvas = np.zeros(shape, np.uint8)
    for p in strokes:
        pts = np.round(p).astype(np.int32).reshape(-1, 1, 2)
        if len(pts) == 1:
            cv2.circle(canvas, (int(pts[0, 0, 0]), int(pts[0, 0, 1])), 0, 1, -1)
        else:
            cv2.polylines(canvas, [pts], False, 1, 1)
    return canvas


def _dt(mask_zero_where_target: np.ndarray) -> np.ndarray:
    return cv2.distanceTransform(mask_zero_where_target.astype(np.uint8), cv2.DIST_L2, 3)


def gt_metrics(strokes, gt_mask, gt_skel, tol=TOL) -> dict:
    P = rasterize(strokes, gt_mask.shape)
    d_ink = _dt(gt_mask == 0)
    d_pred = _dt(P == 0)
    n_pred, n_gt = int(P.sum()), int(gt_skel.sum())
    on = d_ink[P > 0] <= tol
    prec = float(on.mean()) if n_pred else 0.0
    rec = float((d_pred[gt_skel > 0] <= tol).mean()) if n_gt else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec > 0 else 0.0
    return {"prec": prec, "rec": rec, "f1": f1, "junk": int((~on).sum()), "frag": len(strokes) / max(n_gt, 1) * 1000}


_FREE_CACHE: dict = {}


def free_metrics(strokes, img, key, tol=TOL) -> dict:
    from preprocess import estimate_background, darkness, to_gray, paper_mask, filter_components, FilterParams
    from skimage.morphology import skeletonize
    if key not in _FREE_CACHE:
        g = to_gray(img)
        d = darkness(cv2.GaussianBlur(g, (3, 3), 0), estimate_background(g, 81))
        pm = paper_mask(img, erode_px=0)
        inside = (pm > 0) if pm is not None else np.ones(g.shape, bool)
        ev = (d > 0.10) & inside
        conf = ((d > 0.25) & inside).astype(np.uint8) * 255
        conf = filter_components(conf, FilterParams(min_area=15, max_thickness=30)) > 0
        ref_skel = skeletonize(conf)
        _FREE_CACHE[key] = (_dt(~ev), ref_skel, inside)
    d_ev, ref_skel, inside = _FREE_CACHE[key]
    P = rasterize(strokes, img.shape[:2])
    n = int(P.sum())
    d_pred = _dt(P == 0)
    return {"sup": float((d_ev[P > 0] <= tol).mean()) if n else 0.0,
            "cov": float((d_pred[ref_skel] <= tol).mean()) if ref_skel.any() else 0.0,
            "out": float((~inside[P > 0]).mean()) if n else 0.0}


# ----------------------------------------------------------------- 실행
def _run_task(task):
    v, case = task
    d = Path(case["dir"])
    img = read_img(d / "input.jpg")
    reg_p = DATA / "ocr" / case["name"] / "regions.json"
    regions = json.loads(reg_p.read_text(encoding="utf-8")) if reg_p.exists() else None
    if v["mode"] == "crops" and regions is None:
        return {"variant": v["name"], "case": case["name"], "error": "no regions"}
    t0 = time.time()
    try:
        st = run_extract(img, regions, v)
    except Exception as e:  # 실험 하나가 죽어도 전체는 계속
        return {"variant": v["name"], "case": case["name"], "error": f"{type(e).__name__}: {e}"}
    dt = time.time() - t0
    n_pts = int(sum(len(p) for p in st))
    # 워커 vertex 페이싱 기준 시간. 화면 배율은 사진 긴 변 ~1900px -> 종이 ~650px(0.35)
    # 화면 RDP 0.5px = 사진 1.4px. 직선 중간점은 화면 6px 마다 6ms.
    verts, length = 0, 0.0
    for p in st:
        if len(p) >= 2:
            a = cv2.approxPolyDP(p.reshape(-1, 1, 2).astype(np.float32), 1.4, False)
            verts += len(a)
            length += float(np.sum(np.hypot(*np.diff(p, axis=0).T)))
        else:
            verts += 1
    draw_s = verts * 0.024 + len(st) * 0.086 + length * 0.35 / 6.0 * 0.006
    row = {"variant": v["name"], "case": case["name"], "kind": case["kind"], "t": round(dt, 2),
           "strokes": len(st), "points": n_pts, "est_s": round(n_pts * PT_COST + len(st) * STROKE_COST, 1),
           "verts": verts, "draw_s": round(draw_s, 1)}
    if case["kind"] == "synth":
        row |= gt_metrics(st, np.load(d / "gt_mask.npy"), np.load(d / "gt_skel.npy"))
    else:
        row |= free_metrics(st, img, case["name"])
    if v.get("_viz") and case["kind"] == "real":
        vis = (img * 0.35 + 255 * 0.65).astype(np.uint8)
        for p in st:
            cv2.polylines(vis, [np.round(p).astype(np.int32).reshape(-1, 1, 2)], False, (0, 0, 220), 2)
        write_img(DATA / "viz" / f"{case['name']}__{v['name']}.jpg", cv2.resize(vis, None, fx=0.5, fy=0.5))
    return row


def summarize(rows, variants):
    """표 하나로 비교한다.

    F1/prec/rec/junk/frag : 원본 크기 합성 20장 평균
    작F1                  : 0.3배 축소 합성 6장 평균 (이미지 크기 의존성)
    획/필기s              : 합성 평균 획 수, 새 워커 페이싱 기준 필기 시간
    실sup/실cov/실획/실필기s : 책상 그림자 실사진 (정답 없음, 대리 지표)
    마sup/마cov           : 아이패드로 올린 527x249 마커 사진 (대리 지표)
    """
    by = {}
    for r in rows:
        by.setdefault(r["variant"], []).append(r)
    print()
    print(f"{'variant':34s} {'F1':>5s} {'prec':>5s} {'rec':>5s} {'junk':>6s} {'frag':>5s} {'작F1':>5s} | "
          f"{'획':>4s} {'필기s':>5s} | {'실sup':>5s} {'실cov':>5s} {'실획':>4s} {'실필기s':>6s} | "
          f"{'마sup':>5s} {'마cov':>5s} | {'추출s':>5s}")
    for v in variants:
        rs = [r for r in by.get(v["name"], []) if "error" not in r]
        errs = [r for r in by.get(v["name"], []) if "error" in r]
        sy = [r for r in rs if r.get("kind") == "synth" and not r["case"].startswith("small_")]
        sm = [r for r in rs if r.get("kind") == "synth" and r["case"].startswith("small_")]
        if not sy:
            print(f"{v['name']:34s}  (결과 없음, 오류 {len(errs)}: {errs[0]['error'] if errs else ''})")
            continue
        mean = lambda k, L: float(np.mean([r[k] for r in L if k in r])) if L else float("nan")
        line = (f"{v['name']:34s} {mean('f1', sy):5.3f} {mean('prec', sy):5.3f} {mean('rec', sy):5.3f} "
                f"{mean('junk', sy):6.0f} {mean('frag', sy):5.1f} {mean('f1', sm):5.3f} | "
                f"{mean('strokes', sy):4.0f} {mean('draw_s', sy):5.0f} | ")
        rd = [r for r in rs if r["case"] == "real_desk_shadow"]
        if rd:
            r = rd[0]
            line += f"{r['sup']:5.3f} {r['cov']:5.3f} {r['strokes']:4d} {r.get('draw_s', float('nan')):6.0f} | "
        else:
            line += f"{'-':>5s} {'-':>5s} {'-':>4s} {'-':>6s} | "
        rm = [r for r in rs if r["case"] == "real_marker_small"]
        if rm:
            line += f"{rm[0]['sup']:5.3f} {rm[0]['cov']:5.3f} | "
        else:
            line += f"{'-':>5s} {'-':>5s} | "
        line += f"{mean('t', rs):5.1f}"
        if errs:
            line += f"  (오류 {len(errs)})"
        print(line)


def per_condition(rows, names):
    conds = sorted({r["case"].rsplit("_s", 1)[0] for r in rows if r.get("kind") == "synth"})
    print(f"\n조건별 F1 {'':20s}" + "".join(f"{c[:11]:>12s}" for c in conds))
    for n in names:
        line = f"{n:31s}"
        for c in conds:
            vals = [r["f1"] for r in rows if r["variant"] == n and r.get("kind") == "synth"
                    and r["case"].rsplit("_s", 1)[0] == c and "error" not in r]
            line += f"{(np.mean(vals) if vals else float('nan')):12.3f}"
        print(line)


def _lower_priority():
    """실험이 시연(서버 파이프라인, GoodNotes 필기)을 방해하지 않게 우선순위를 낮춘다."""
    if os.name == "nt":
        import ctypes
        BELOW_NORMAL = 0x4000
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL)
    else:
        with contextlib.suppress(Exception):
            os.nice(10)


def _worker_loop(wid, tq, rq):
    _lower_priority()
    while True:
        item = tq.get()
        if item is None:
            break
        idx, task = item
        rq.put(("start", wid, idx, time.time()))
        try:
            row = _run_task(task)
        except Exception as e:  # noqa: BLE001
            v, case = task
            row = {"variant": v["name"], "case": case["name"], "error": f"{type(e).__name__}: {e}"}
        rq.put(("done", wid, idx, row))


TASK_TIMEOUT = float(os.environ.get("BENCH_TASK_TIMEOUT", "150"))


def run_pool(tasks, workers, timeout=TASK_TIMEOUT, log=print):
    """감시견 달린 프로세스 풀.

    ProcessPoolExecutor 는 과제 하나가 끝나지 않으면 전체가 영원히 기다린다
    (실제로 한 과제가 5시간 넘게 CPU 를 태우며 stageA 전체가 결과 0건으로
    멈췄다). 과제별로 시간을 재서 넘으면 그 워커만 죽이고 새로 띄우고,
    그 과제는 timeout 오류로 기록한다. 느린 변형은 실사용에서도 못 쓴다.
    """
    import multiprocessing as mp
    import queue as _queue

    ctx = mp.get_context("spawn")
    tq, rq = ctx.Queue(), ctx.Queue()
    for i, t in enumerate(tasks):
        tq.put((i, t))
    procs: dict[int, object] = {}

    def spawn(wid):
        p = ctx.Process(target=_worker_loop, args=(wid, tq, rq), daemon=True)
        p.start()
        procs[wid] = p

    for w in range(workers):
        spawn(w)
    next_wid = workers
    running: dict[int, tuple[int, float]] = {}
    rows: dict[int, dict] = {}
    t0 = time.time()
    while len(rows) < len(tasks):
        try:
            kind, wid, idx, payload = rq.get(timeout=2)
            if kind == "start":
                running[wid] = (idx, payload)
            else:
                running.pop(wid, None)
                rows[idx] = payload
                if len(rows) % 20 == 0 or len(rows) == len(tasks):
                    log(f"  진행 {len(rows)}/{len(tasks)}  ({time.time() - t0:.0f}s)")
        except _queue.Empty:
            pass
        now = time.time()
        for wid, (idx, ts) in list(running.items()):
            dead = not procs[wid].is_alive()
            if now - ts > timeout or dead:
                with contextlib.suppress(Exception):
                    procs[wid].kill()
                    procs[wid].join(5)
                v, case = tasks[idx]
                why = "crashed" if dead else f"timeout>{timeout:.0f}s"
                rows[idx] = {"variant": v["name"], "case": case["name"], "error": why}
                log(f"  ! {why}: {v['name']} / {case['name']}")
                running.pop(wid)
                del procs[wid]
                spawn(next_wid)
                next_wid += 1
    for _ in procs:
        tq.put(None)
    for p in procs.values():
        p.join(10)
    return [rows[i] for i in range(len(tasks))]


def run_stage(variants, workers=4, viz=False, tag="stage"):
    cases = list_cases()
    tasks = [(v | ({"_viz": True} if viz else {}), c) for v in variants for c in cases]
    rows = run_pool(tasks, workers, log=lambda m: print(m, flush=True))
    out = DATA / "results" / f"{tag}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")
    summarize(rows, variants)
    per_condition(rows, [v["name"] for v in variants])
    return rows


if __name__ == "__main__":
    import argparse
    import stages
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--viz", action="store_true")
    ap.add_argument("--summarize", action="store_true", help="다시 돌리지 않고 저장된 결과만 표로")
    a = ap.parse_args()
    vs = getattr(stages, a.stage)()
    if a.summarize:
        saved = [json.loads(x) for x in (DATA / "results" / f"{a.stage}.jsonl").read_text(encoding="utf-8").splitlines() if x]
        summarize(saved, vs)
        per_condition(saved, [v["name"] for v in vs])
        raise SystemExit(0)
    print(f"[{a.stage}] 변형 {len(vs)}개 x 케이스 {len(list_cases())}개")
    run_stage(vs, a.workers, a.viz, tag=a.stage)
