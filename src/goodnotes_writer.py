from __future__ import annotations

import runpy
from pathlib import Path


ROOT_WRITER = Path(__file__).resolve().parents[1] / "goodnotes_writer.py"


if __name__ == "__main__":
    runpy.run_path(str(ROOT_WRITER), run_name="__main__")
