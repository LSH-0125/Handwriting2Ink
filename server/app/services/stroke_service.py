import os
import subprocess

def run_pipeline(job_id: str, input_path: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)

    result = subprocess.run([
        "python", "pipeline/pipeline.py",
        "--input", input_path,
        "--output_dir", output_dir,
        "--save_stroke_data",
        "--overwrite"
    ], capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"stderr: {result.stderr[-2000:]}\nstdout: {result.stdout[-1000:]}"
        )

    strokes_path = os.path.join(output_dir, "crop_stroke_composite_strokes.json")
    if not os.path.exists(strokes_path):
        raise RuntimeError(f"strokes.json not found. stdout: {result.stdout[-1000:]}")

    return strokes_path