import asyncio
import os
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session

from app.db import get_db, SessionLocal
from app.models.job import Job, JobEvent
from app.services.job_service import create_job, update_status
from app.services.stroke_service import run_pipeline
from app.config import settings

router = APIRouter(prefix="/api/jobs")

# 워커가 GoodNotes 에 필기를 마치면 drawn, 클라이언트가 확인하면 delivered.
# Windows 워커는 클립보드 .bin 을 만들지 않는다. 전달은 GoodNotes 계정 동기화가 한다.
TERMINAL_OK = ("drawn", "delivered")
TERMINAL = TERMINAL_OK + ("failed",)


def verify_client(
    x_h2i_key: str | None = Header(default=None),
    k: str | None = Query(default=None),
):
    """공개 배포용 접근키. settings.CLIENT_KEY 가 비어 있으면 아무것도 요구하지 않는다.

    시연용 최소 방어다. 토큰이 URL 쿼리로도 오는 이유는 아이패드에서
    주소 하나만 열면 되게 하기 위해서다.
    """
    if not settings.CLIENT_KEY:
        return
    if (x_h2i_key or k) != settings.CLIENT_KEY:
        raise HTTPException(status_code=401, detail="접근키가 필요합니다")


def latest_message(db: Session, job_id: str) -> str | None:
    event = (
        db.query(JobEvent)
        .filter(JobEvent.job_id == job_id)
        .order_by(JobEvent.id.desc())
        .first()
    )
    return event.message if event else None


@router.get("")
def list_jobs(db: Session = Depends(get_db), _=Depends(verify_client)):
    jobs = db.query(Job).order_by(Job.created_at.desc()).limit(20).all()
    return [
        {
            "job_id": job.id,
            "status": job.status,
            "created_at": str(job.created_at),
            "error_code": job.error_code,
            "error_message": job.error_message,
        }
        for job in jobs
    ]


@router.post("")
async def upload_image(file: UploadFile = File(...), db: Session = Depends(get_db),
                       _=Depends(verify_client)):
    job = create_job(db)

    output_dir = os.path.join(settings.STORAGE_BASE, job.id)
    os.makedirs(output_dir, exist_ok=True)

    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    input_path = os.path.join(output_dir, f"input{ext}")

    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job.input_filename = file.filename
    job.input_path = input_path
    job.output_dir = output_dir
    job.status = "uploaded"
    db.commit()

    # 요청 세션(db)을 백그라운드로 넘기면 안 된다. Depends(get_db) 가 응답 직후
    # 닫아버리므로 추출이 끝날 때쯤엔 이미 죽은 세션이다. 작업용 세션을 따로 연다.
    asyncio.create_task(run_extraction(job.id, input_path, output_dir))

    return {"job_id": job.id, "status": "uploaded"}


async def run_extraction(job_id: str, input_path: str, output_dir: str):
    db = SessionLocal()
    try:
        update_status(db, job_id, "extracting_strokes", "스트로크 추출 시작")
        loop = asyncio.get_running_loop()
        strokes_path = await loop.run_in_executor(
            None, run_pipeline, job_id, input_path, output_dir
        )
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            return
        job.strokes_path = strokes_path
        db.commit()
        update_status(db, job_id, "stroke_ready", "스트로크 추출 완료. 워커 대기 중")
    except Exception as e:
        db.rollback()
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is not None:
            job.error_code = "STROKE_EXTRACTION_FAILED"
            job.error_message = str(e)[:4000]
            job.status = "failed"
            job.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.add(JobEvent(job_id=job_id, status="failed", message=str(e)[:4000]))
            db.commit()
    finally:
        db.close()


@router.get("/{job_id}")
def get_job_status(job_id: str, db: Session = Depends(get_db), _=Depends(verify_client)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job.id,
        "status": job.status,
        # 클라이언트는 이 두 개만 보면 된다. binary_ready 는 더 이상 없다.
        "done": job.status in TERMINAL_OK,
        "failed": job.status == "failed",
        "message": latest_message(db, job.id),
        "stroke_count": job.stroke_count,
        "error_code": job.error_code,
        "error_message": job.error_message,
    }


@router.post("/{job_id}/delivered")
def mark_delivered(job_id: str, db: Session = Depends(get_db), _=Depends(verify_client)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    update_status(db, job_id, "delivered", "클라이언트 확인 완료")
    return {"job_id": job_id, "status": "delivered"}
