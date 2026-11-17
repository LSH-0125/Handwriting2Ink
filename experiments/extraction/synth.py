"""손글씨 사진을 흉내 낸 합성 테스트셋 + 정답(GT) 생성기.

실제 사용자 사진에서 추출이 무너진 조건을 그대로 재현한다:
  - 종이 일부를 덮는 그림자 / 한쪽으로 어두워지는 조명
  - 종이 밖 책상(나무결) 배경 + 원근 왜곡
  - 노트 줄(정답이 아님 - 그리면 안 되는 대상)
  - 연필(낮은 대비), 초점 흐림, 센서 노이즈, JPEG

정답:
  gt_mask : 잉크 픽셀(사진 좌표계)
  gt_skel : 잉크 중심선(사진 좌표계)
노트 줄, 그림자, 책상은 정답에 들어가지 않는다. 거기에 그린 획은 전부
'빈 공간에 그린 것' 으로 집계된다.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.morphology import skeletonize

FONT_DIR = Path("C:/Windows/Fonts")
LATIN_FONTS = ["Inkfree.ttf", "segoepr.ttf", "segoesc.ttf", "BRADHITC.TTF", "LHANDW.TTF", "comic.ttf"]
KOREAN_FONTS = ["malgun.ttf"]

WORDS = ["nullable", "FIRST", "FOLLOW", "S->xAN", "A->A.w", "N->y", "B->Z",
         "parse", "item", "goto", "state", "shift", "reduce", "LR(1)", "$",
         "x, w", "y, $", "Z, w", "closure", "token", "lookahead", "accept"]
KOREAN = ["문법", "상태", "전이", "필기", "노트", "정리", "규칙", "예시", "결과", "확인"]


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def _jitter_polyline(pts, rng, amp, step=6):
    """직선 구간을 손으로 그린 듯 흔든다."""
    out = []
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step))
        phase = rng.uniform(0, 2 * math.pi)
        freq = rng.uniform(0.5, 1.5)
        for i in range(n):
            t = i / n
            nx, ny = -(y1 - y0), (x1 - x0)
            norm = math.hypot(nx, ny) or 1
            d = amp * math.sin(phase + freq * t * math.pi * 2) + rng.gauss(0, amp * 0.25)
            out.append((x0 + (x1 - x0) * t + nx / norm * d, y0 + (y1 - y0) * t + ny / norm * d))
    out.append(pts[-1])
    return out


@dataclass
class Spec:
    name: str
    seed: int
    illum: str = "flat"          # flat | gradient | shadow | vignette
    desk: bool = True            # 종이 밖 책상 배경 + 원근
    ruled: bool = False          # 노트 줄
    pencil: bool = False         # 연필(낮은 대비)
    blur: float = 0.0            # 가우시안 블러 sigma
    noise: float = 4.0           # 센서 노이즈 sigma
    jpeg: int = 88
    density: str = "normal"      # normal | dense (박스/화살표 많은 다이어그램)
    pen: int = 3                 # 펜 굵기(px, 종이 좌표)


def draw_content(spec: Spec, W: int, H: int, rng: random.Random):
    """흰 바탕에 검은 잉크로 내용만 그린다. (ink 0..255 alpha) 를 돌려준다."""
    img = Image.new("L", (W, H), 0)          # 0 = 잉크 없음, 255 = 잉크
    d = ImageDraw.Draw(img)
    pen = spec.pen
    n_text = 10 if spec.density == "normal" else 14
    n_box = 3 if spec.density == "normal" else 9
    n_arrow = 3 if spec.density == "normal" else 10
    n_circle = 2 if spec.density == "normal" else 4

    def text_at(x, y):
        if rng.random() < 0.25:
            s = rng.choice(KOREAN) + " " + rng.choice(KOREAN)
            f = _font(rng.choice(KOREAN_FONTS), rng.randint(30, 48))
        else:
            s = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3)))
            f = _font(rng.choice(LATIN_FONTS), rng.randint(30, 54))
        d.text((x, y), s, fill=255, font=f, stroke_width=max(0, pen // 3))
        return d.textbbox((x, y), s, font=f)

    margin = 60
    for _ in range(n_text):
        text_at(rng.randint(margin, W - 420), rng.randint(margin, H - 90))

    for _ in range(n_box):
        x0, y0 = rng.randint(margin, W - 360), rng.randint(margin, H - 220)
        w, h = rng.randint(160, 330), rng.randint(70, 190)
        pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h), (x0, y0 + 2)]
        d.line(_jitter_polyline(pts, rng, 1.6), fill=255, width=pen, joint="curve")
        f = _font(rng.choice(LATIN_FONTS), rng.randint(26, 38))
        for k in range(rng.randint(1, 3)):
            d.text((x0 + 12, y0 + 10 + k * 42), rng.choice(WORDS), fill=255, font=f)

    for _ in range(n_arrow):
        x0, y0 = rng.randint(margin, W - margin), rng.randint(margin, H - margin)
        ang = rng.uniform(0, 2 * math.pi)
        L = rng.randint(120, 380)
        x1 = min(W - margin, max(margin, x0 + L * math.cos(ang)))
        y1 = min(H - margin, max(margin, y0 + L * math.sin(ang)))
        mid = ((x0 + x1) / 2 + rng.uniform(-40, 40), (y0 + y1) / 2 + rng.uniform(-40, 40))
        d.line(_jitter_polyline([(x0, y0), mid, (x1, y1)], rng, 1.2), fill=255, width=pen)
        a = math.atan2(y1 - mid[1], x1 - mid[0])
        for s in (+1, -1):
            hx = x1 - 22 * math.cos(a + s * 0.45)
            hy = y1 - 22 * math.sin(a + s * 0.45)
            d.line([(x1, y1), (hx, hy)], fill=255, width=pen)

    for _ in range(n_circle):
        cx, cy, r = rng.randint(120, W - 120), rng.randint(120, H - 120), rng.randint(18, 60)
        pts = [(cx + r * math.cos(t / 30 * 2 * math.pi) * rng.uniform(0.95, 1.05),
                cy + r * math.sin(t / 30 * 2 * math.pi) * rng.uniform(0.95, 1.05)) for t in range(31)]
        d.line(pts, fill=255, width=pen, joint="curve")

    return np.array(img, dtype=np.uint8)


def illumination(spec: Spec, W: int, H: int, rng: random.Random) -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    field = np.ones((H, W), np.float32)
    if spec.illum in ("gradient", "shadow"):
        a = rng.uniform(0, 2 * math.pi)
        proj = (xx * math.cos(a) + yy * math.sin(a))
        proj = (proj - proj.min()) / (proj.max() - proj.min())
        field *= 1.0 - rng.uniform(0.30, 0.45) * proj
    if spec.illum == "shadow":
        # 손/폰 그림자처럼 부드러운 경계의 어두운 영역
        mask = np.zeros((H, W), np.uint8)
        cx, cy = rng.randint(W // 4, W), rng.randint(H // 3, H)
        poly = np.array([[cx - rng.randint(300, 700), H], [cx - rng.randint(0, 200), cy],
                         [cx + rng.randint(100, 400), cy - rng.randint(0, 150)], [W, H]], np.int32)
        cv2.fillPoly(mask, [poly], 255)
        soft = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), 45)
        field *= 1.0 - rng.uniform(0.25, 0.40) * soft
    if spec.illum == "vignette":
        r = np.sqrt(((xx - W / 2) / W) ** 2 + ((yy - H / 2) / H) ** 2)
        field *= 1.0 - 0.6 * r ** 2
    return field


def wood(W: int, H: int, rng: random.Random) -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    base = np.array([60, 105, 150], np.float32)               # BGR 갈색
    grain = np.sin(xx / rng.uniform(9, 16) + 3 * np.sin(yy / rng.uniform(60, 110))) * 18
    noise = cv2.GaussianBlur(np.random.default_rng(rng.randint(0, 9999)).normal(0, 14, (H, W)).astype(np.float32), (0, 0), 2)
    img = base[None, None, :] + (grain + noise)[..., None] * np.array([0.6, 0.8, 1.0], np.float32)
    return np.clip(img, 0, 255)


def make_case(spec: Spec, out_dir: Path) -> dict:
    rng = random.Random(spec.seed)
    W, H = 1600, 1150                                       # 종이 좌표계
    ink = draw_content(spec, W, H, rng)                    # 0..255 잉크 알파

    paper_rgb = np.array([rng.uniform(236, 248)] * 3, np.float32) + np.array([0, 2, 5], np.float32)
    paper = np.ones((H, W, 3), np.float32) * paper_rgb
    if spec.ruled:
        for y in range(90, H - 40, 42):
            cv2.line(paper, (0, y), (W, y), (215, 190, 170), 1, cv2.LINE_AA)   # 연한 파랑
        cv2.line(paper, (110, 0), (110, H), (190, 190, 235), 1, cv2.LINE_AA)  # 왼쪽 여백선

    ink_color = np.array([95, 92, 90] if spec.pencil else [70, 35, 30], np.float32)   # 연필 / 남색 볼펜
    a = (ink.astype(np.float32) / 255.0)[..., None]
    if spec.pencil:
        a = a * 0.75
    page = paper * (1 - a) + ink_color * a
    page *= illumination(spec, W, H, rng)[..., None]

    gt_mask_p = (ink >= 128).astype(np.uint8)
    gt_skel_p = skeletonize(gt_mask_p > 0).astype(np.uint8)

    if spec.desk:
        FW, FH = 2000, 1500
        bg = wood(FW, FH, rng) * illumination(Spec("bg", 0, illum="gradient"), FW, FH, rng)[..., None]
        cover = rng.uniform(0.74, 0.88)
        pw, ph = FW * cover, FH * cover
        ox, oy = (FW - pw) / 2 + rng.uniform(-60, 60), (FH - ph) / 2 + rng.uniform(-50, 50)
        j = lambda: rng.uniform(-45, 45)
        dst = np.float32([[ox + j(), oy + j()], [ox + pw + j(), oy + j()],
                          [ox + pw + j(), oy + ph + j()], [ox + j(), oy + ph + j()]])
        src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
        M = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(page, M, (FW, FH), flags=cv2.INTER_LINEAR)
        cover_mask = cv2.warpPerspective(np.ones((H, W), np.float32), M, (FW, FH))
        photo = bg * (1 - cover_mask[..., None]) + warped * cover_mask[..., None]
        gt_mask = cv2.warpPerspective(gt_mask_p, M, (FW, FH), flags=cv2.INTER_NEAREST)
        gt_skel = cv2.warpPerspective(gt_skel_p, M, (FW, FH), flags=cv2.INTER_NEAREST)
        paper_quad = dst.tolist()
    else:
        photo = page
        gt_mask, gt_skel = gt_mask_p, gt_skel_p
        paper_quad = [[0, 0], [W, 0], [W, H], [0, H]]

    if spec.blur > 0:
        photo = cv2.GaussianBlur(photo, (0, 0), spec.blur)
    photo = photo + np.random.default_rng(spec.seed).normal(0, spec.noise, photo.shape)
    photo = np.clip(photo, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, spec.jpeg])
    photo = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    case_dir = out_dir / spec.name
    case_dir.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tofile(str(case_dir / "input.jpg"))
    np.save(case_dir / "gt_mask.npy", gt_mask.astype(np.uint8))
    np.save(case_dir / "gt_skel.npy", gt_skel.astype(np.uint8))
    meta = asdict(spec) | {"paper_quad": paper_quad, "shape": list(photo.shape[:2]),
                          "gt_ink_px": int(gt_mask.sum()), "gt_skel_px": int(gt_skel.sum())}
    (case_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


SUITE = [
    # 이름, 조건
    ("scan_clean",      dict(illum="flat", desk=False, noise=1.5, jpeg=95)),
    ("photo_flat",      dict(illum="flat")),
    ("photo_gradient",  dict(illum="gradient")),
    ("photo_shadow",    dict(illum="shadow")),
    ("photo_ruled",     dict(illum="gradient", ruled=True)),
    ("photo_pencil",    dict(illum="gradient", pencil=True)),
    ("photo_dense",     dict(illum="shadow", density="dense")),
    ("photo_blur",      dict(illum="flat", blur=1.4)),
    ("photo_vignette",  dict(illum="vignette")),
    ("photo_thickpen",  dict(illum="shadow", pen=5)),
]


def build_suite(out_dir: Path, seeds=(11, 23)) -> list[dict]:
    metas = []
    for name, kw in SUITE:
        for s in seeds:
            metas.append(make_case(Spec(name=f"{name}_s{s}", seed=s, **kw), out_dir))
    return metas


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "_data" / "synth"
    ms = build_suite(out)
    for m in ms:
        print(f"{m['name']:24s} {m['shape']}  GT잉크 {m['gt_ink_px']:>7,}px  중심선 {m['gt_skel_px']:>6,}px")
