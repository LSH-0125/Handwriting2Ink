import os
import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[2] / "pipeline" / "pipeline.py"

# 추출이 끝나지 않으면 잡이 영원히 extracting_strokes 에 머문다. 실험에서 특정
# 이진화 조합이 세선화 결과를 폭증시켜 한 이미지에 5시간 넘게 돈 적이 있다.
# 페이지 모드는 보통 1~3초, OCR 모드는 30~60초다.
PIPELINE_TIMEOUT = float(os.environ.get("PIPELINE_TIMEOUT", "180"))


def run_pipeline(job_id: str, input_path: str, output_dir: str) -> str:
    """OCR + 스트로크 추출 파이프라인을 별도 프로세스로 돌린다."""
    os.makedirs(output_dir, exist_ok=True)

    abs_input = os.path.abspath(input_path)
    abs_output = os.path.abspath(output_dir)

    env = dict(os.environ)
    # 파이프라인이 한글을 찍는다. Windows 기본 cp949 로 디코딩하면 깨지거나 터진다.
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        result = subprocess.run(
            [
                sys.executable,          # "python" 은 Windows 에서 Store 스텁일 수 있다
                str(PIPELINE),
                "--input", abs_input,
                "--output_dir", abs_output,
                "--save_stroke_data",
                "--overwrite",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=PIPELINE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"획 추출이 {PIPELINE_TIMEOUT:.0f}초 안에 끝나지 않았습니다. "
            "사진이 너무 크거나 배경이 복잡한 경우입니다. 종이만 보이게 다시 찍어 주세요."
        ) from e

    if result.returncode != 0:
        raise RuntimeError(
            f"pipeline exit {result.returncode}\n"
            f"stderr: {(result.stderr or '')[-2000:]}\n"
            f"stdout: {(result.stdout or '')[-1000:]}"
        )

    strokes_path = os.path.join(abs_output, "crop_stroke_composite_strokes.json")
    if not os.path.exists(strokes_path):
        raise RuntimeError(
            f"strokes.json 이 생성되지 않았습니다. stdout: {(result.stdout or '')[-1000:]}"
        )

    return strokes_path
