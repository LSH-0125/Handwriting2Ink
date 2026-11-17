from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.job import Job
from app.services.job_service import update_status
from app.services import worker_presence
from app.config import settings

router = APIRouter(prefix="/api/worker")

# Windows 워커 전용 계약.
# 맥 워커의 클립보드 .bin 업로드 경로는 제거했다. GoodNotes for Windows 는
# 복사 시 시스템 클립보드에 타입 이름 문자열만 올리고 잉크를 주지 않으므로
# .bin 을 만들 수 없다. 전달은 GoodNotes 계정 동기화가 담당한다.


def verify_token(authorization: str | None = Header(default=None)):
    # Header(...) 로 두면 헤더가 없을 때 401 이 아니라 422 가 나간다.
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization 헤더가 없습니다")
    token = authorization.replace("Bearer ", "").strip()
    if token != settings.WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    # 인증된 워커 요청은 전부 생존 신호다(폴링, 진행 보고, 다운로드)
    worker_presence.touch()


@router.get("/jobs/next")
def get_next_job(db: Session = Depends(get_db), _=Depends(verify_token)):
    query = db.query(Job).filter(Job.status == "stroke_ready").order_by(Job.created_at)
    try:
        # 워커를 2대 이상 띄워도 같은 잡을 중복 할당하지 않는다.
        # SQLite 에서는 무시되므로 그 경우엔 워커 1대 전제가 유지된다.
        job = query.with_for_update(skip_locked=True).first()
    except Exception:
        db.rollback()
        job = query.first()

    if not job:
        return {"job": None}

    update_status(db, job.id, "assigned_to_worker", "워커에 할당됨")
    return {
        "job": {
            "job_id": job.id,
            "status": "assigned_to_worker",
            "strokes_url": f"/api/worker/jobs/{job.id}/strokes",
        }
    }


@router.get("/jobs/{job_id}/strokes")
def get_strokes(job_id: str, db: Session = Depends(get_db), _=Depends(verify_token)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job or not job.strokes_path:
        raise HTTPException(status_code=404, detail="strokes 를 찾을 수 없습니다")
    return FileResponse(job.strokes_path, media_type="application/json")


@router.post("/jobs/{job_id}/status")
def report_status(job_id: str, body: dict,
                  db: Session = Depends(get_db), _=Depends(verify_token)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    status = body.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="status 가 필요합니다")

    if status == "drawn":
        job.stroke_count = body.get("stroke_count")
        db.commit()

    update_status(db, job_id, status, body.get("message"))
    return {"ok": True}


@router.post("/jobs/{job_id}/fail")
def report_failure(job_id: str, body: dict,
                   db: Session = Depends(get_db), _=Depends(verify_token)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.error_code = body.get("error_code")
    job.error_message = (body.get("error_message") or "")[:4000]
    db.commit()
    update_status(db, job_id, "failed", job.error_message)
    return {"ok": True}
