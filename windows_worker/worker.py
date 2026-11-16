"""Windows 워커 루프.

  서버 폴링 -> strokes.json 다운로드 -> GoodNotes 에 필기 -> 상태 보고

전달 경로: 클립보드 .bin 이 아니라 GoodNotes 계정 동기화(Windows -> iOS).
그래서 bin_ready 단계가 없고 상태는 stroke_ready -> drawing -> drawn -> delivered 로 간다.

실행 중에는 마우스를 점유한다. 중단하려면 마우스를 화면 좌상단 모서리로 옮기거나
Ctrl+C 를 누른다.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import controller
import winplat
from client import ServerClient, ServerError

HERE = Path(__file__).resolve().parent


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def preflight(cfg: controller.Config) -> None:
    """돌리기 전에 환경을 전수 확인한다. 잡을 잡고 나서 실패하면 잡이 버려진다."""
    log(f"DPI: {winplat.ensure_dpi_aware()}")
    win = winplat.find_goodnotes()          # 앱 아닌 브라우저 탭이면 여기서 거부된다
    win = winplat.restore_if_minimized(win)
    winplat.assert_on_screen(win.rect, "GoodNotes 창")
    log(f"GoodNotes: {win.exe} {win.cls} rect={win.rect}")
    visible = winplat.clamp_to_screen(win.rect)
    target = cfg.target_rect or controller.derive_target_rect(visible, cfg.target_inset)
    winplat.assert_on_screen(target, "target_rect")
    log(f"필기 영역: {tuple(round(v, 1) for v in target)}")
    log(f"대상 노트북: {cfg.expected_notebook}")

    ok, detail = controller.check_tab(win, cfg)
    if ok:
        log(f"탭 확인: {detail}")
    else:
        raise RuntimeError(
            f"열려 있는 노트북이 '{cfg.expected_notebook}' 이 아닌 것 같습니다. {detail}"
        )


def handle_job(client: ServerClient, job: dict, cfg: controller.Config,
               workdir: Path, execute: bool) -> None:
    job_id = job["job_id"]
    log(f"잡 수신: {job_id}")
    dest = workdir / job_id / "strokes.json"

    try:
        client.download_strokes(job_id, dest)
        log(f"strokes 다운로드: {dest}")
        client.report_status(job_id, "drawing", "GoodNotes 주입 시작")

        summary = controller.inject(dest, cfg, execute=execute)
        if not summary["executed"]:
            log("dry-run 이라 상태 보고를 생략합니다.")
            return

        msg = (f"{summary['stroke_count']}획 {summary['point_count']}점, "
               f"{summary['elapsed_sec']}초")
        # 워커는 '그렸다'(drawn)까지만 책임진다. delivered 는 클라이언트가
        # 결과를 확인했을 때 앱이 찍는다. 워커가 둘 다 찍으면 'delivered' 가
        # 아무 의미 없는 값이 된다.
        client.report_status(job_id, "drawn", msg,
                             stroke_count=summary["stroke_count"])
        log(f"완료: {job_id} ({msg})")

    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        log(f"실패: {job_id} - {detail}")
        traceback.print_exc()
        try:
            client.report_failure(job_id, "WIN_INJECTION_FAILED", detail)
        except ServerError as report_err:
            log(f"실패 보고도 실패: {report_err}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Handwriting2Ink Windows 워커")
    ap.add_argument("--server", default=os.environ.get("H2I_SERVER", "http://127.0.0.1:8000"))
    ap.add_argument("--token", default=os.environ.get("H2I_WORKER_TOKEN"))
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--workdir", default=None, help="strokes.json 저장 위치 (기본: ./work)")
    ap.add_argument("--poll_interval", type=float, default=3.0)
    ap.add_argument("--once", action="store_true", help="잡 하나만 처리하고 종료")
    ap.add_argument("--execute", action="store_true", help="실제 마우스 입력. 없으면 dry-run")
    ap.add_argument("--calibrate", action="store_true",
                    help="지금 열려 있는 탭을 대상 노트북 기준으로 저장하고 종료합니다.")
    args = ap.parse_args()

    if args.calibrate:
        cfg = controller.load_config(args.config)
        win = controller.acquire_window(cfg)
        path = controller.calibrate_tab(win, cfg)
        log(f"탭 기준 저장: {path}")
        log(f"이 탭을 '{cfg.expected_notebook}' 으로 간주합니다. 다르면 config 의 expected_notebook 을 고치세요.")
        return

    if not args.token:
        raise SystemExit("WORKER_TOKEN 이 필요합니다. --token 또는 H2I_WORKER_TOKEN 환경변수.")


    cfg = controller.load_config(args.config)
    workdir = Path(args.workdir) if args.workdir else HERE / "work"
    workdir.mkdir(parents=True, exist_ok=True)

    log(f"서버: {args.server}")
    log(f"작업 디렉토리: {workdir}")
    log(f"모드: {'EXECUTE (마우스 점유)' if args.execute else 'dry-run'}")

    # 실제로 그릴 때는 환경이 안 맞으면 바로 멈춰야 한다. 잡을 잡고 나서
    # 실패하면 그 잡이 버려진다. dry-run 은 GoodNotes 없이도 서버 연동을
    # 점검할 수 있어야 하므로 경고만 하고 넘어간다.
    try:
        preflight(cfg)
    except Exception as e:
        if args.execute:
            raise
        log(f"WARN: preflight 실패 (dry-run 이라 계속합니다) - {e}")

    client = ServerClient(args.server, args.token)
    if not client.health():
        log("WARN: /health 응답이 없습니다. 서버 주소를 확인하세요.")

    idle = 0
    try:
        while True:
            try:
                job = client.next_job()
            except ServerError as e:
                log(f"폴링 실패: {e}")
                time.sleep(args.poll_interval * 2)
                continue

            if job is None:
                idle += 1
                if idle % 20 == 1:
                    log("대기 중...")
                if args.once:
                    log("--once: 처리할 잡이 없어 종료합니다.")
                    return
                time.sleep(args.poll_interval)
                continue

            idle = 0
            handle_job(client, job, cfg, workdir, args.execute)
            if args.once:
                return
    except KeyboardInterrupt:
        log("중단됨")


if __name__ == "__main__":
    main()
