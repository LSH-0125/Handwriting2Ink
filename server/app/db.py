from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings


def normalize_db_url(raw: str) -> str:
    """배포처가 주는 DATABASE_URL 을 SQLAlchemy 가 확실히 받아들이는 형태로 맞춘다.

    두 가지 함정을 막는다.

    1) 레거시 스킴 `postgres://`
       Railway/Heroku 가 이 형태로 주는 경우가 있는데 SQLAlchemy 는
       NoSuchModuleError 로 거절한다.

    2) 드라이버 미지정 `postgresql://`
       SQLAlchemy 2.1 부터 이 경우의 기본 DBAPI 가 psycopg2 에서 psycopg(v3)
       로 바뀌었다. requirements 에 psycopg2-binary 만 있으면
       `ModuleNotFoundError: No module named 'psycopg'` 로 죽는다.
       그래서 드라이버를 명시해 버전에 상관없이 같은 동작을 하게 한다.

    이미 드라이버가 지정돼 있으면(`postgresql+psycopg2://` 등) 건드리지 않는다.
    """
    url = raw.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = normalize_db_url(settings.DATABASE_URL)

# SQLite 는 기본적으로 커넥션을 만든 스레드에서만 쓸 수 있는데, 추출 작업이
# run_in_executor 로 다른 스레드에서 돈다. 로컬 시연용으로만 완화한다.
connect_args = {}
if make_url(DATABASE_URL).get_backend_name() == "sqlite":
    connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
