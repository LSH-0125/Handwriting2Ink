import os
import shutil
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.job import Job
from app.services.job_service import create_job, update_status
from app.services.stroke_service import run_pipeline
from app.config import settings

router = APIRouter(prefix="/api/jobs")

@router.get("")
def list_jobs(db: Session = Depends(get_db)):
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
async def upload_image(file: UploadFile = File(...), db: Session = Depends(get_db)):
    job = create_job(db)

    output_dir = os.path.join(settings.STORAGE_BASE, job.id)
    os.makedirs(output_dir, exist_ok=True)

    ext = os.path.splitext(file.filename)[1] or ".jpg"
    input_path = os.path.join(output_dir, f"input{ext}")

    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job.input_filename = file.filename
    job.input_path = input_path
    job.output_dir = output_dir
    job.status = "uploaded"
    db.commit()

    # 파이프라인 백그라운드 실행
    asyncio.create_task(run_extraction(job.id, input_path, output_dir, db))

    return {"job_id": job.id, "status": "uploaded"}


async def run_extraction(job_id, input_path, output_dir, db):
    update_status(db, job_id, "extracting_strokes")
    try:
        loop = asyncio.get_event_loop()
        strokes_path = await loop.run_in_executor(
            None, run_pipeline, job_id, input_path, output_dir
        )
        job = db.query(Job).filter(Job.id == job_id).first()
        job.strokes_path = strokes_path
        db.commit()
        update_status(db, job_id, "stroke_ready", "strokes.json 생성 완료")
    except Exception as e:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.error_code = "STROKE_EXTRACTION_FAILED"
        job.error_message = str(e)
        job.status = "failed"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

@router.get("/{job_id}")
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.id,
        "status": job.status,
        "binary_ready": job.status == "bin_ready",
        "error_code": job.error_code,
        "error_message": job.error_message,
    }


@router.get("/{job_id}/binary")
def download_binary(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job or job.status != "bin_ready":
        raise HTTPException(status_code=404, detail="Binary not ready")
    return FileResponse(
        job.binary_path,
        media_type="application/octet-stream",
        filename="goodnotes_clipboard_item0.bin"
    )


@router.post("/{job_id}/delivered")
def mark_delivered(job_id: str, db: Session = Depends(get_db)):
    update_status(db, job_id, "delivered")
    return {"job_id": job_id, "status": "delivered"}