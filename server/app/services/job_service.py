import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.job import Job, JobEvent

def create_job(db: Session) -> Job:
    job = Job(
        id=f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        status="created",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job

def update_status(db: Session, job_id: str, status: str, message: str = None):
    job = db.query(Job).filter(Job.id == job_id).first()
    job.status = status
    job.updated_at = datetime.now(timezone.utc)
    db.add(JobEvent(job_id=job_id, status=status, message=message))
    db.commit()