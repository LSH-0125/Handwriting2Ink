from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.job import Job
from app.services.job_service import update_status
from app.config import settings
import os, shutil

router = APIRouter(prefix="/api/worker")

def verify_token(authorization: str = Header(...)):
    token = authorization.replace("Bearer ", "")
    if token != settings.WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@router.get("/jobs/next")
def get_next_job(db: Session = Depends(get_db), _=Depends(verify_token)):
    job = db.query(Job).filter(Job.status == "stroke_ready").first()
    if not job:
        return {"job": None}

    update_status(db, job.id, "assigned_to_worker")
    return {
        "job": {
            "job_id": job.id,
            "status": "assigned_to_worker",
            "strokes_url": f"/api/worker/jobs/{job.id}/strokes"
        }
    }

@router.get("/jobs/{job_id}/strokes")
def get_strokes(job_id: str, db: Session = Depends(get_db), _=Depends(verify_token)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job or not job.strokes_path:
        raise HTTPException(status_code=404)
    return FileResponse(job.strokes_path, media_type="application/json")

@router.post("/jobs/{job_id}/status")
def report_status(job_id: str, body: dict,
                  db: Session = Depends(get_db), _=Depends(verify_token)):
    update_status(db, job_id, body["status"], body.get("message"))
    return {"ok": True}

@router.post("/jobs/{job_id}/binary")
async def upload_binary(
    job_id: str,
    file: UploadFile = File(...),
    pasteboard_type: str = "com.goodnotesapp.goodnotes5.notes",
    binary_size: int = 0,
    db: Session = Depends(get_db),
    _=Depends(verify_token)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404)

    bin_path = os.path.join(job.output_dir, "goodnotes_clipboard_item0.bin")
    with open(bin_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job.binary_path = bin_path
    job.pasteboard_type = pasteboard_type
    job.binary_size = binary_size
    db.commit()
    update_status(db, job_id, "bin_ready")
    return {"job_id": job_id, "status": "bin_ready"}

@router.post("/jobs/{job_id}/fail")
def report_failure(job_id: str, body: dict,
                   db: Session = Depends(get_db), _=Depends(verify_token)):
    job = db.query(Job).filter(Job.id == job_id).first()
    job.error_code = body.get("error_code")
    job.error_message = body.get("error_message")
    db.commit()
    update_status(db, job_id, "failed", body.get("error_message"))
    return {"ok": True}