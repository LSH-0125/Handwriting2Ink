"""각 테스트 이미지에 대해 OCR 레이아웃(regions.json)을 한 번만 만들어 둔다.

OCR 은 이미지당 수 초가 걸리고 변형 실험마다 반복할 필요가 없다. 전처리
변형은 같은 영역 위에서 비교해야 공정하기도 하다.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPE = HERE.parents[1] / "server" / "pipeline" / "ocr_layout.py"


def cases():
    out = []
    for d in sorted((HERE / "_data" / "synth").iterdir()):
        if (d / "input.jpg").exists():
            out.append((d.name, d / "input.jpg"))
    real = HERE / "_data" / "real"
    if real.exists():
        for d in sorted(real.iterdir()):
            if (d / "input.jpg").exists():
                out.append((d.name, d / "input.jpg"))
    return out


if __name__ == "__main__":
    py = sys.executable
    for name, img in cases():
        out_dir = HERE / "_data" / "ocr" / name
        if (out_dir / "regions.json").exists():
            print(f"skip {name}")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        # 낮은 우선순위: 시연(서버 파이프라인, GoodNotes 필기)과 같은 노트북에서 돈다
        flags = 0x4000 if sys.platform == "win32" else 0   # BELOW_NORMAL_PRIORITY_CLASS
        r = subprocess.run([py, str(PIPE), "--input", str(img), "--output_dir", str(out_dir),
                            "--resize_max", "1600", "--save_crops"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=flags)
        status = "ok" if r.returncode == 0 and (out_dir / "regions.json").exists() else "FAIL"
        print(f"{status} {name}", flush=True)
        if status == "FAIL":
            print(r.stderr[-800:], flush=True)
