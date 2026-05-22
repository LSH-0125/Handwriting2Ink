"""Top-level OCR-to-stroke pipeline orchestrator.

This module intentionally keeps the existing stage implementations separate.
It runs the OCR layout stage first, then renders OCR text/shape regions as strokes.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OCR layout 생성부터 crop stroke 합성까지 한 번에 실행합니다."
    )
    parser.add_argument("--input", required=True, help="입력 이미지 경로")
    parser.add_argument(
        "--output_dir",
        default=None,
        help=(
            "명시 출력 디렉토리. 지정하지 않으면 "
            "pilot_outputs/<입력파일명>/runs/rNNN_<mode>를 자동 생성합니다."
        ),
    )
    parser.add_argument(
        "--run_name",
        default=None,
        help="자동 runs 디렉토리 아래에 사용할 짧은 run 이름. 예: r010_raw_ab",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="명시한 출력 디렉토리나 run_name 디렉토리가 이미 있을 때 덮어쓰기를 허용합니다.",
    )
    parser.add_argument(
        "--resize_max",
        type=int,
        default=1600,
        help="OCR layout 단계의 긴 변 기준 최대 리사이즈 길이. 0 이하면 원본 유지",
    )
    parser.add_argument(
        "--skip_layout",
        action="store_true",
        help="이미 생성된 regions.json/crops를 사용하고 OCR layout 단계는 건너뜁니다.",
    )
    parser.add_argument(
        "--skip_render",
        action="store_true",
        help="OCR layout만 실행하고 stroke 렌더링 단계는 건너뜁니다.",
    )
    parser.add_argument(
        "--layout_debug",
        action="store_true",
        help="ocr_layout.py의 debug 산출물을 저장합니다.",
    )
    parser.add_argument(
        "--region_source",
        choices=("ocr_merged", "ocr_raw"),
        default="ocr_merged",
        help="render_strokes.py에서 사용할 text region 소스. shape region은 항상 함께 포함됩니다.",
    )
    parser.add_argument(
        "--crop_scale",
        type=float,
        default=2.0,
        help="render_strokes.py에서 crop별 skeletonize 전에 적용할 확대 배율",
    )
    parser.add_argument(
        "--black_thickness",
        type=int,
        default=1,
        help="흑백 stroke 이미지의 선 두께",
    )
    parser.add_argument(
        "--result_thickness",
        type=int,
        default=None,
        help="3패널 결과 이미지의 컬러 stroke 선 두께",
    )
    parser.add_argument(
        "--save_crop_debug",
        action="store_true",
        help="crop별 scaled crop/binary/skeleton/overlay를 저장합니다.",
    )
    parser.add_argument(
        "--crop_debug_mode",
        choices=("text", "all"),
        default="text",
        help="crop debug 대상: text 또는 all(text+shape)",
    )
    parser.add_argument(
        "--save_merged_debug",
        action="store_true",
        help="crop 병합 canvas와 병합 기준 전처리/stroke 결과를 저장합니다.",
    )
    parser.add_argument(
        "--merged_debug_mode",
        choices=("text", "all"),
        default="text",
        help="merged debug 대상: text 또는 all(text+shape)",
    )
    parser.add_argument(
        "--save_stroke_data",
        action="store_true",
        help="최종 stroke 좌표열 JSON을 저장합니다.",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="실행하지 않고 호출할 하위 명령만 출력합니다.",
    )
    return parser.parse_args()


def region_mode(region_source: str) -> str:
    return "raw" if region_source == "ocr_raw" else "merged"


def next_run_name(runs_dir: Path, mode: str) -> str:
    max_number = 0
    if runs_dir.exists():
        for child in runs_dir.iterdir():
            match = re.match(r"^r(\d{3})(?:_|$)", child.name)
            if match:
                max_number = max(max_number, int(match.group(1)))
    return f"r{max_number + 1:03d}_{mode}"


def validate_run_name(run_name: str) -> None:
    if Path(run_name).name != run_name:
        raise ValueError("--run_name은 경로가 아닌 디렉토리 이름만 사용할 수 있습니다.")
    if not re.match(r"^[A-Za-z0-9._-]+$", run_name):
        raise ValueError("--run_name은 영문, 숫자, '.', '_', '-'만 사용할 수 있습니다.")


def resolve_output_dir(
    input_path: Path,
    output_dir: str | None,
    run_name: str | None,
    region_source: str,
) -> Path:
    if output_dir:
        return Path(output_dir)

    base_dir = PROJECT_ROOT / "pilot_outputs" / input_path.stem
    runs_dir = base_dir / "runs"
    if run_name:
        validate_run_name(run_name)
        return runs_dir / run_name
    return runs_dir / next_run_name(runs_dir, region_mode(region_source))


def ensure_output_dir_is_usable(output_dir: Path, args: argparse.Namespace) -> None:
    if args.dry_run:
        return
    if args.skip_layout:
        if not output_dir.exists():
            raise FileNotFoundError(f"기존 OCR layout 출력 디렉토리가 없습니다: {output_dir}")
        return

    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(
            f"출력 디렉토리가 이미 존재하고 비어 있지 않습니다: {output_dir}\n"
            "--run_name을 다르게 지정하거나 --overwrite를 사용하십시오."
        )
    output_dir.mkdir(parents=True, exist_ok=True)


def get_git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip()


def build_layout_command(args: argparse.Namespace, input_path: Path, output_dir: Path) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "ocr_layout.py"),
        "--input",
        str(input_path),
        "--output_dir",
        str(output_dir),
        "--resize_max",
        str(args.resize_max),
        "--save_crops",
    ]
    if args.layout_debug:
        command.append("--debug")
    return command


def build_render_command(args: argparse.Namespace, output_dir: Path) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "render_strokes.py"),
        "--pilot_dir",
        str(output_dir),
        "--region_source",
        args.region_source,
        "--crop_scale",
        str(args.crop_scale),
        "--black_thickness",
        str(args.black_thickness),
    ]
    if args.result_thickness is not None:
        command.extend(["--result_thickness", str(args.result_thickness)])
    if args.save_crop_debug:
        command.extend(["--save_crop_debug", "--crop_debug_mode", args.crop_debug_mode])
    if args.save_merged_debug:
        command.extend([
            "--save_merged_debug",
            "--merged_debug_mode",
            args.merged_debug_mode,
        ])
    if args.save_stroke_data:
        command.append("--save_stroke_data")
    return command


def run_command(command: list[str], dry_run: bool) -> None:
    printable = " ".join(command)
    print(f"[pipeline] $ {printable}")
    if dry_run:
        return
    subprocess.run(command, check=True, cwd=PROJECT_ROOT)


def write_run_meta(
    output_dir: Path,
    args: argparse.Namespace,
    input_path: Path,
    layout_command: list[str] | None,
    render_command: list[str] | None,
    started_at: str,
    finished_at: str,
) -> None:
    meta = {
        "run_id": output_dir.name,
        "created_at": started_at,
        "finished_at": finished_at,
        "input": str(input_path),
        "output_dir": str(output_dir),
        "git_commit": get_git_commit(),
        "args": vars(args),
        "commands": {
            "layout": layout_command,
            "render": render_command,
        },
    }
    (output_dir / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[pipeline] run_meta: {output_dir / 'run_meta.json'}")


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = resolve_output_dir(
        input_path,
        args.output_dir,
        args.run_name,
        args.region_source,
    )

    if not args.skip_layout and not input_path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {input_path}")
    ensure_output_dir_is_usable(output_dir, args)

    print(f"[pipeline] input: {input_path}")
    print(f"[pipeline] output_dir: {output_dir}")
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    layout_command = None
    render_command = None

    if not args.skip_layout:
        layout_command = build_layout_command(args, input_path, output_dir)
        run_command(layout_command, args.dry_run)
    else:
        print("[pipeline] OCR layout 단계 건너뜀")

    if not args.skip_render:
        render_command = build_render_command(args, output_dir)
        run_command(render_command, args.dry_run)
    else:
        print("[pipeline] stroke rendering 단계 건너뜀")

    if not args.dry_run:
        finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        write_run_meta(
            output_dir,
            args,
            input_path,
            layout_command,
            render_command,
            started_at,
            finished_at,
        )

    print("[pipeline] done")


if __name__ == "__main__":
    main()
