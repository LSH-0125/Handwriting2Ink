import os
import subprocess

def run_pipeline(job_id: str, input_path: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)

    abs_input = os.path.abspath(input_path)
    abs_output = os.path.abspath(output_dir)

    result = subprocess.run([
        "python", "pipeline/pipeline.py",
        "--input", abs_input,
        "--output_dir", abs_output,
        "--save_stroke_data",
        "--overwrite"
    ], capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"stderr: {result.stderr[-2000:]}\nstdout: {result.stdout[-1000:]}"
        )

    strokes_path = os.path.join(abs_output, "crop_stroke_composite_strokes.json")
    if not os.path.exists(strokes_path):
        raise RuntimeError(f"strokes.json not found. stdout: {result.stdout[-1000:]}")

    return strokes_path