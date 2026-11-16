FROM python:3.11

WORKDIR /app
COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && ldconfig \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p storage/jobs

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    MPLBACKEND=Agg

EXPOSE 8000

# Railway 는 PORT 를 주입하고 그 포트로 라우팅한다.
# exec 형식(JSON 배열)은 쉘을 안 거쳐서 ${PORT} 가 전개되지 않으므로 쉘 형식으로 둔다.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
