import os
import subprocess

def run_pipeline(job_id: str, input_path: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)

    subprocess.run([
        "python", "pipeline/pipeline.py",
        "--input", input_path,
        "--output_dir", output_dir,
        "--save_stroke_data",
        "--overwrite"
    ], check=True)

    strokes_path = os.path.join(output_dir, "crop_stroke_composite_strokes.json")
    if not os.path.exists(strokes_path):
        raise RuntimeError("strokes.json was not generated")

    return strokes_path