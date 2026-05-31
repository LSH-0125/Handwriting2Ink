from fastapi import FastAPI
from app.db import Base, engine
from app.api import jobs, worker

Base.metadata.create_all(bind=engine)

app = FastAPI(title="H2I Server")
app.include_router(jobs.router)
app.include_router(worker.router)

@app.get("/health")
def health():
    return {"status": "ok"}