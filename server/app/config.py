from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    WORKER_TOKEN: str
    STORAGE_BASE: str = "storage/jobs"

    # 공개 배포(Railway)용 간단 접근키.
    # 비워두면 인증 없음 - 로컬 LAN 시연에서는 그 편이 편하다.
    # 값을 넣으면 클라이언트 API 가 X-H2I-Key 헤더나 ?k= 쿼리를 요구한다.
    CLIENT_KEY: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
