from sqlalchemy import Column, String, Integer, DateTime, Text
from datetime import datetime, timezone
from app.db import Base

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    status = Column(String, nullable=False)
    input_filename = Column(String)
    input_path = Column(String)
    output_dir = Column(String)
    strokes_path = Column(String)
    binary_path = Column(String)
    pasteboard_type = Column(String)
    binary_size = Column(Integer)
    stroke_count = Column(Integer)
    assigned_worker_id = Column(String)
    error_code = Column(String)
    error_message = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

class JobEvent(Base):
    __tablename__ = "job_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, nullable=False)
    status = Column(String, nullable=False)
    message = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))