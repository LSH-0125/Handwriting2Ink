"""필기 사진 전처리: 조명 평탄화, 이진화, 잡음 성분 제거, 종이 영역 검출, 획 단순화.

배경
----
기존 파이프라인은 crop 마다 GaussianBlur + Otsu 로 이진화했다. 조명이 고른
스캔본에서는 잘 되지만, 실제 폰 사진에서는 무너진다.
  - 그림자가 걸린 crop 에서 Otsu 가 '밝은 종이 vs 그늘진 종이' 를 가르는
    임계값을 골라, 그늘진 종이 전체를 잉크로 판정한다. 그 큰 덩어리를
    세선화하면 빈 종이 위에 거대한 계단/대각선 구조물이 생긴다.
    (실제 사용자 사진 609획 중 상당수가 이것이었다.)
  - 종이 밖 책상 나무결, 종이 가장자리가 shape 영역으로 잡혀 획이 된다.

여기 있는 함수들은 그 두 원인을 각각 막는다.
  1) 배경(조명) 추정 -> 정규화된 '어두움' 지도. 그림자는 배경과 함께
     어두워지므로 정규화 후에는 사라지고, 잉크만 남는다.
  2) 종이 마스크. 책상은 채도가 높고 종이는 무채색이라는 점을 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


def _odd(n: int) -> int:
    n = int(max(3, n))
    return n if n % 2 == 1 else n + 1


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def estimate_background(gray: np.ndarray, ksize: int = 41) -> np.ndarray:
    """잉크보다 큰 창으로 closing 해서 '잉크가 없었다면의 종이 밝기' 를 추정한다.

    closing(팽창 후 침식)은 어두운 가는 구조(잉크)를 지우고 밝은 배경을 남긴다.
    이어서 median 으로 경계 계단을 다듬는다. ksize 는 획 굵기의 수 배 이상이어야
    한다. 너무 크면 그림자 경계를 따라가지 못한다.
    """
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(ksize), _odd(ksize)))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, k)
    bg = cv2.medianBlur(bg, _odd(min(ksize, 255)))
    return bg


def darkness(gray: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """배경 대비 상대적 어두움 0..1. 그림자 영향을 제거한 잉크 세기."""
    g = gray.astype(np.float32)
    b = np.maximum(bg.astype(np.float32), 1.0)
    return np.clip(1.0 - g / b, 0.0, 1.0)


def otsu_threshold(values: np.ndarray) -> float:
    v8 = np.clip(values * 255.0, 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(v8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(t) / 255.0


@dataclass
class BinarizeParams:
    method: str = "otsu"          # otsu | norm_otsu | norm_fixed | norm_hyst | adaptive | sauvola | norm_sauvola
    bg_ksize: int = 41            # 배경 추정 창 (현재 해상도 기준 px)
    floor: float = 0.12           # 정규화 어두움 최소 임계 (그 아래는 잉크로 보지 않음)
    fixed: float = 0.22           # norm_fixed 임계
    hyst_low: float = 0.10        # norm_hyst 낮은 임계 (강한 잉크에 이어진 부분만 살림)
    hyst_high: float = 0.25       # norm_hyst 높은 임계
    block: int = 41               # adaptive / sauvola 창
    C: float = 12.0               # adaptive 상수
    k: float = 0.25               # sauvola k
    morph: bool = True            # close/open 2x2 (기존 파이프라인과 같은 후처리)


def binarize(gray: np.ndarray, p: BinarizeParams, bg: np.ndarray | None = None) -> np.ndarray:
    """잉크 = 255 인 이진 영상을 돌려준다.

    bg 를 주면 그 배경으로 정규화한다. crop 단위로 처리할 때는 전체 이미지에서
    미리 구한 배경의 같은 영역을 넘겨야 한다 - 작은 crop 안에서는 배경을
    제대로 추정할 수 없다.
    """
    m = p.method
    if m == "otsu":
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    elif m == "adaptive":
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        binary = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, _odd(p.block), p.C)
    elif m == "sauvola":
        from skimage.filters import threshold_sauvola
        t = threshold_sauvola(gray, window_size=_odd(p.block), k=p.k)
        binary = ((gray < t) * 255).astype(np.uint8)
    else:
        if bg is None:
            bg = estimate_background(gray, p.bg_ksize)
        d = darkness(cv2.GaussianBlur(gray, (3, 3), 0), bg)
        if m == "norm_otsu":
            t = max(otsu_threshold(d), p.floor)
            binary = ((d > t) * 255).astype(np.uint8)
        elif m == "norm_fixed":
            binary = ((d > p.fixed) * 255).astype(np.uint8)
        elif m == "norm_hyst":
            from skimage.filters import apply_hysteresis_threshold
            binary = (apply_hysteresis_threshold(d, p.hyst_low, p.hyst_high) * 255).astype(np.uint8)
        elif m == "norm_sauvola":
            from skimage.filters import threshold_sauvola
            ng = (255 * (1.0 - d)).astype(np.uint8)
            t = threshold_sauvola(ng, window_size=_odd(p.block), k=p.k)
            binary = (((ng < t) & (d > p.floor)) * 255).astype(np.uint8)
        else:
            raise ValueError(f"unknown method {m}")

    if p.morph:
        kernel = np.ones((2, 2), np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    return binary


@dataclass
class FilterParams:
    min_area: int = 0             # 이보다 작은 연결성분 제거 (점 잡음)
    max_thickness: float = 0.0    # 성분의 최대 두께(px)가 이보다 크면 제거. 0 = 끔
    drop_border: bool = False     # 이미지 테두리에 닿는 성분 제거


def filter_components(binary: np.ndarray, p: FilterParams) -> np.ndarray:
    """잉크답지 않은 연결성분을 지운다.

    max_thickness: 펜 획은 가늘다. 그림자 덩어리나 칠해진 면은 두껍다.
    성분 안의 최대 거리변환값 x2 = 그 성분의 최대 두께. 이게 펜 굵기의 몇 배를
    넘으면 잉크가 아니라고 본다.
    """
    if p.min_area <= 0 and p.max_thickness <= 0 and not p.drop_border:
        return binary
    n, labels, stats, _ = cv2.connectedComponentsWithStats((binary > 0).astype(np.uint8), connectivity=8)
    keep = np.ones(n, bool)
    keep[0] = False
    if p.min_area > 0:
        keep &= stats[:, cv2.CC_STAT_AREA] >= p.min_area
    if p.drop_border:
        H, W = binary.shape
        x, y, w, h = (stats[:, cv2.CC_STAT_LEFT], stats[:, cv2.CC_STAT_TOP],
                      stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT])
        touch = (x <= 0) | (y <= 0) | (x + w >= W) | (y + h >= H)
        keep &= ~touch
    if p.max_thickness > 0:
        dt = cv2.distanceTransform((binary > 0).astype(np.uint8), cv2.DIST_L2, 3)
        maxdt = np.zeros(n, np.float32)
        np.maximum.at(maxdt, labels.ravel(), dt.ravel())
        keep &= (2.0 * maxdt) <= p.max_thickness
    keep[0] = False
    return (keep[labels] * 255).astype(np.uint8)


def paper_mask(img_bgr: np.ndarray, erode_px: int = 6) -> np.ndarray | None:
    """사진 속 종이 영역(255). 종이가 화면을 거의 다 채우면 None.

    책상/바닥은 대체로 채도가 있고 종이는 무채색이다. 그림자는 명도만 낮추고
    채도는 잘 바꾸지 않으므로, 채도 기준이 그림자에 강하다.
    """
    small_scale = 600.0 / max(img_bgr.shape[:2])
    small = cv2.resize(img_bgr, None, fx=small_scale, fy=small_scale, interpolation=cv2.INTER_AREA) \
        if small_scale < 1 else img_bgr.copy()
    hsv = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1].astype(np.float32), hsv[..., 2].astype(np.float32)
    v_ref = np.percentile(v, 90)
    cand = ((s < 45) & (v > 0.45 * v_ref)).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    if n <= 1:
        return None
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    frac = stats[biggest, cv2.CC_STAT_AREA] / cand.size
    if frac < 0.20 or frac > 0.97:
        return None          # 종이를 못 찾았거나 종이가 화면 전체 -> 마스크 안 씀
    region = (labels == biggest).astype(np.uint8)
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    mask_small = np.zeros_like(region)
    cv2.fillConvexPoly(mask_small, hull, 1)
    mask = cv2.resize(mask_small, (img_bgr.shape[1], img_bgr.shape[0]), interpolation=cv2.INTER_NEAREST)
    if erode_px > 0:
        mask = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(erode_px * 2), _odd(erode_px * 2))))
    return (mask * 255).astype(np.uint8)


def simplify_stroke(points: np.ndarray, eps: float, max_seg: float = 0.0) -> np.ndarray:
    """Ramer-Douglas-Peucker 로 점을 줄인다. 필기 시간은 점 수에 비례한다.

    max_seg > 0 이면 단순화 후 너무 긴 구간을 다시 잘게 나눈다. 마우스가 한 번에
    너무 멀리 뛰면 앱이 곡선을 직선으로 이어 그릴 수 있어서다.
    """
    if eps <= 0 or len(points) < 3:
        out = points
    else:
        approx = cv2.approxPolyDP(points.reshape(-1, 1, 2).astype(np.float32), float(eps), False)
        out = approx.reshape(-1, 2)
    if max_seg > 0 and len(out) >= 2:
        res = [out[0]]
        for a, b in zip(out[:-1], out[1:]):
            dist = float(np.hypot(*(b - a)))
            n = int(dist // max_seg)
            for i in range(1, n + 1):
                res.append(a + (b - a) * (i / (n + 1)))
            res.append(b)
        out = np.array(res, np.float32)
    return out


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """네 점을 좌상, 우상, 우하, 좌하 순서로."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def paper_quad(img_bgr: np.ndarray) -> np.ndarray | None:
    """사진 속 종이의 네 모서리(원본 좌표). 못 찾거나 믿기 어려우면 None.

    paper_mask 와 같은 채도/명도 기준으로 종이 영역을 잡고, 외곽선을 사각형으로
    근사한다. 볼록하고 화면의 25% 이상을 덮는 사각형일 때만 받아들인다.
    """
    scale = 800.0 / max(img_bgr.shape[:2])
    small = cv2.resize(img_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img_bgr
    scale = min(scale, 1.0)
    hsv = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1].astype(np.float32), hsv[..., 2].astype(np.float32)
    cand = ((s < 45) & (v > 0.45 * np.percentile(v, 90))).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    if n <= 1:
        return None
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    frac = stats[big, cv2.CC_STAT_AREA] / cand.size
    if frac < 0.25 or frac > 0.97:
        return None
    region = (labels == big).astype(np.uint8)
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    peri = cv2.arcLength(hull, True)
    quad = None
    for eps in (0.01, 0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            quad = approx
            break
    if quad is None or not cv2.isContourConvex(quad):
        return None
    return _order_corners(quad) / scale


def rectify(img_bgr: np.ndarray, quad: np.ndarray, trim: int = 6):
    """종이 사각형을 펴서 직사각형 이미지로. (펴진 이미지, 원본->종이 변환행렬) 을 돌려준다.

    trim: 가장자리 몇 픽셀은 종이 끝선/책상이 섞이므로 잘라낸다.
    """
    tl, tr, br, bl = quad
    W = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))))
    H = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))))
    dst = np.float32([[-trim, -trim], [W - 1 + trim, -trim], [W - 1 + trim, H - 1 + trim], [-trim, H - 1 + trim]])
    M = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    warped = cv2.warpPerspective(img_bgr, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return warped, M


def chain_strokes(strokes: list[np.ndarray], max_gap: float) -> list[np.ndarray]:
    """끝점이 아주 가까운 획끼리 이어 획 수를 줄인다.

    세선화/분기점 처리 과정에서 한 획이 1~3px 끊겨 여러 획이 되는 일이 많다.
    필기 시간은 획 수에 크게 좌우되므로(획마다 펜을 들고 이동) 이걸 잇는다.
    잇는 구간은 max_gap 이하라 결과 그림은 사실상 같다.
    """
    if max_gap <= 0 or len(strokes) < 2:
        return strokes
    from scipy.spatial import cKDTree

    cur = [np.asarray(s, np.float32) for s in strokes if len(s)]
    for _ in range(10):                      # 이어진 결과끼리 다시 이어질 수 있어 몇 번 반복
        ends, owner = [], []
        for i, s in enumerate(cur):
            ends.append(s[0]); owner.append((i, 0))
            ends.append(s[-1]); owner.append((i, 1))
        pts = np.asarray(ends, np.float32)
        pairs = cKDTree(pts).query_pairs(max_gap, output_type="ndarray")
        if len(pairs) == 0:
            break
        dist = np.linalg.norm(pts[pairs[:, 0]] - pts[pairs[:, 1]], axis=1)
        used, merged = set(), []
        for a, b in pairs[np.argsort(dist)]:
            (ia, wa), (ib, wb) = owner[a], owner[b]
            if ia == ib or ia in used or ib in used:
                continue
            A = cur[ia] if wa == 1 else cur[ia][::-1]     # A 의 끝 -> B 의 시작
            B = cur[ib] if wb == 0 else cur[ib][::-1]
            merged.append(np.vstack([A, B]))
            used.update((ia, ib))
        if not merged:
            break
        cur = [s for i, s in enumerate(cur) if i not in used] + merged
    return cur


def estimate_stroke_width(binary: np.ndarray) -> float:
    """잉크 마스크의 대표 획 두께(px) = 성분별 (면적 / 세선 길이) 의 길이가중 중앙값.

    임계값(배경 창, 최대 두께, 최소 면적, 단순화 허용오차)이 전부 절대 픽셀이면
    같은 글씨라도 사진 해상도에 따라 결과가 달라진다. 획 두께를 재서 작업
    해상도를 맞추면 모든 파라미터가 '획 두께 단위' 가 되어 크기에 무관해진다.

    폭 w 길이 L 인 획은 면적 ~ w*L, 세선 픽셀 ~ L 이라 비율이 곧 w 다.
    거리변환 중앙값은 값이 1, 2, 2.2 ... 로 계단져서 두께 차이를 못 가렸다.
    덩어리(그림자, 칠)는 면적에 비해 세선이 짧아 값이 크게 튀지만, 길이가중
    중앙값이라 글씨 획이 대부분이면 영향이 작다.
    """
    from skimage.morphology import skeletonize

    b = binary > 0
    if not b.any():
        return 0.0
    sk = skeletonize(b)
    n, labels = cv2.connectedComponents(b.astype(np.uint8), connectivity=8)
    area = np.bincount(labels.ravel(), minlength=n).astype(np.float64)
    sklen = np.bincount(labels[sk], minlength=n).astype(np.float64)
    valid = sklen >= 10
    valid[0] = False
    if not valid.any():
        return 0.0
    w = area[valid] / sklen[valid]
    wt = sklen[valid]
    order = np.argsort(w)
    cw = np.cumsum(wt[order])
    return float(w[order][np.searchsorted(cw, cw[-1] / 2.0)])


def _end_dir(s: np.ndarray, at_start: bool, k: int = 4) -> np.ndarray | None:
    """획 끝의 진행 방향(바깥쪽) 단위벡터."""
    if len(s) < 2:
        return None
    a, b = (s[min(k, len(s) - 1)], s[0]) if at_start else (s[-1 - min(k, len(s) - 1)], s[-1])
    d = b - a
    n = float(np.hypot(*d))
    return d / n if n > 1e-6 else None


def extend_ends(strokes: list[np.ndarray], ink: np.ndarray, max_ext: float) -> list[np.ndarray]:
    """획 양 끝을 진행 방향으로 잉크가 끝나는 곳까지(최대 max_ext) 늘린다.

    세선은 획 두께의 절반쯤 안쪽에서 끝난다. 굵은 매직일수록 글자 끝이 짧아지고,
    교차점에서 갈라진 획 사이가 벌어진다(A 꼭짓점이 열림). 잉크 밖으로는 안
    늘리므로 없는 선이 생기지 않는다.
    """
    H, W = ink.shape
    out = []
    for s in strokes:
        s = np.asarray(s, np.float32)
        if len(s) < 2 or max_ext < 1:
            out.append(s)
            continue
        parts = [s]
        for at_start in (True, False):
            d = _end_dir(s, at_start)
            if d is None:
                continue
            p0 = s[0] if at_start else s[-1]
            last = None
            for t in np.arange(1.0, max_ext + 0.5, 1.0):
                q = p0 + d * t
                x, y = int(round(q[0])), int(round(q[1]))
                if not (0 <= x < W and 0 <= y < H) or ink[y, x] == 0:
                    break
                last = q
            if last is not None:
                if at_start:
                    parts.insert(0, last[None].astype(np.float32))
                else:
                    parts.append(last[None].astype(np.float32))
        out.append(np.concatenate(parts, axis=0))
    return out


def chain_on_ink(strokes: list[np.ndarray], ink: np.ndarray, max_gap: float,
                 max_turn_deg: float = 70.0) -> list[np.ndarray]:
    """끝점이 가깝고, 그 사이가 잉크이고, 방향이 이어지는 두 획을 한 획으로 잇는다.

    펜 들기 횟수(=필기 시간, 끊겨 보이는 이음매)를 줄인다. 거리만 보는
    chain_strokes 와 달리 잉크 위인지와 꺾임 각도를 확인해 엉뚱한 연결을 막는다.
    """
    from scipy.spatial import cKDTree

    S = [np.asarray(s, np.float32) for s in strokes if len(s) >= 1]
    H, W = ink.shape
    cos_lim = np.cos(np.deg2rad(max_turn_deg))

    def on_ink(a, b):
        n = max(2, int(np.hypot(*(b - a))) + 1)
        for t in np.linspace(0, 1, n):
            q = a + (b - a) * t
            x, y = int(round(q[0])), int(round(q[1]))
            if not (0 <= x < W and 0 <= y < H) or ink[y, x] == 0:
                return False
        return True

    for _ in range(6):
        ends, owner = [], []
        for i, s in enumerate(S):
            ends += [s[0], s[-1]]
            owner += [(i, True), (i, False)]
        if len(ends) < 4:
            break
        tree = cKDTree(np.array(ends))
        pairs = sorted(tree.query_pairs(max_gap, output_type="ndarray").tolist(),
                       key=lambda p: float(np.hypot(*(ends[p[0]] - ends[p[1]]))))
        used, merged = set(), {}
        for a, b in pairs:
            (i, ia), (j, ja) = owner[a], owner[b]
            if i == j or i in used or j in used:
                continue
            pa, pb = ends[a], ends[b]
            da, db = _end_dir(S[i], ia), _end_dir(S[j], ja)
            if da is not None and db is not None and float(np.dot(da, -db)) < cos_lim:
                continue                      # 마주 보며 이어지지 않으면 다른 획이다
            if not on_ink(pa, pb):
                continue
            si = S[i][::-1] if ia else S[i]   # i 의 끝이 이음점이 되게
            sj = S[j] if ja else S[j][::-1]   # j 의 시작이 이음점이 되게
            merged[i] = np.concatenate([si, sj], axis=0)
            used |= {i, j}
        if not merged:
            break
        S = [merged[i] for i in merged] + [s for k, s in enumerate(S) if k not in used]
    return S
