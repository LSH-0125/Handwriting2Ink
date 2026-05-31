from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    WORKER_TOKEN: str
    STORAGE_BASE: str = "storage/jobs"

    class Config:
        env_file = ".env"

settings = Settings()