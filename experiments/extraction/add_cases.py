"""테스트셋 확장: 실제 업로드 사진 + 저해상도 합성 케이스.

실사용에서 드러난 '이미지 크기 의존' 문제를 재현하려고 만든다.
  - real_marker_small : 아이패드에서 올린 527x249 마커 사진 (job_..._084149)
  - real_sample_900   : 900x520 샘플 (job_..._024505)
  - small_<합성>      : 합성 사진을 0.3배(600x450)로 줄인 것. 정답도 같이 줄인다.
"""
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from skimage.morphology import skeletonize

HERE = Path(__file__).resolve().parent
DATA = HERE / "_data"
JOBS = HERE.parents[1] / "server" / "storage" / "jobs"

REAL = {
    "real_marker_small": "job_20261117_084149_20bf23/input.jpg",
    "real_sample_900": "job_20261117_024505_bedb29/input.png",
}
SMALL = ["photo_flat_s11", "photo_shadow_s11", "photo_gradient_s23", "photo_pencil_s11",
         "photo_thickpen_s23", "scan_clean_s23"]
SMALL_F = 0.3


def imread(p):
    return cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)


def imwrite(p, im, q=92):
    p.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tofile(str(p))


for name, rel in REAL.items():
    src = JOBS / rel
    if not src.exists():
        print("없음", src)
        continue
    imwrite(DATA / "real" / name / "input.jpg", imread(src))
    print("real", name, imread(src).shape)

for base in SMALL:
    d = DATA / "synth" / base
    out = DATA / "synth" / f"small_{base}"
    img = imread(d / "input.jpg")
    small = cv2.resize(img, None, fx=SMALL_F, fy=SMALL_F, interpolation=cv2.INTER_AREA)
    mask = np.load(d / "gt_mask.npy").astype(np.float32)
    m_small = (cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) >= 0.35).astype(np.uint8)
    skel = skeletonize(m_small > 0).astype(np.uint8)
    out.mkdir(parents=True, exist_ok=True)
    imwrite(out / "input.jpg", small, q=90)
    np.save(out / "gt_mask.npy", m_small)
    np.save(out / "gt_skel.npy", skel)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta.update(name=out.name, scale=SMALL_F, shape=list(small.shape[:2]),
                paper_quad=(np.array(meta["paper_quad"]) * SMALL_F).tolist(),
                gt_ink_px=int(m_small.sum()), gt_skel_px=int(skel.sum()))
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print("small", out.name, small.shape, "skel", int(skel.sum()))
