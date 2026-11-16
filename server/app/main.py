from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db import Base, engine
from app.api import jobs, worker

Base.metadata.create_all(bind=engine)

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="H2I Server")
app.include_router(jobs.router)
app.include_router(worker.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    """아이패드 사파리로 여는 웹 클라이언트.

    같은 오리진에서 서빙하므로 페이지가 서버 주소를 알 필요가 없다.
    Railway 든 로컬이든 '그 주소를 열면' 그 서버에 붙는다.
    """
    return FileResponse(STATIC / "index.html", media_type="text/html")
