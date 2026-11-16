"""H2I 서버 진입점.

설정이나 DB 연결이 틀렸을 때 프로세스가 죽지 않고, 대신 무엇이 틀렸는지
알려주는 상태로 뜬다.

이렇게 하는 이유: Settings 는 DATABASE_URL 과 WORKER_TOKEN 을 필수로 요구하고
db.py 는 import 시점에 create_all 을 돈다. 하나라도 어긋나면 uvicorn 이 즉시
종료하고, 배포 플랫폼 앞단에서는 그냥 502 로만 보인다. 로그에 접근할 수 없는
상황(시연 중 아이패드)에서는 원인을 전혀 알 수 없다.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

STATIC = Path(__file__).resolve().parent / "static"

CONFIG_ERROR: str | None = None
try:
    from app.db import Base, engine, DATABASE_URL
    from app.api import jobs, worker

    Base.metadata.create_all(bind=engine)
except Exception as exc:  # 설정 누락, DB 접속 실패 등
    CONFIG_ERROR = f"{type(exc).__name__}: {exc}"


if CONFIG_ERROR:
    app = FastAPI(title="H2I Server (설정 오류)")

    def _problem():
        return JSONResponse(
            status_code=503,
            content={
                "status": "config_error",
                "detail": CONFIG_ERROR,
                "hint": "DATABASE_URL 과 WORKER_TOKEN 환경변수를 확인하세요. "
                        "Postgres 라면 접속 가능한지도 함께 확인하세요.",
            },
        )

    @app.get("/health")
    def health():
        return _problem()

    @app.api_route("/{full_path:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def catch_all(full_path: str):
        return _problem()

else:
    app = FastAPI(title="H2I Server")
    app.include_router(jobs.router)
    app.include_router(worker.router)

    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/health")
    def health():
        return {"status": "ok", "db": DATABASE_URL.split("://", 1)[0]}

    @app.get("/")
    def index():
        """아이패드 사파리로 여는 웹 클라이언트.

        같은 오리진에서 서빙하므로 페이지가 서버 주소를 알 필요가 없다.
        """
        return FileResponse(STATIC / "index.html", media_type="text/html")
