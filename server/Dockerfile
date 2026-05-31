FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 파이프라인 의존성 (OpenCV 등)
RUN apt-get update && apt-get install -y libgl1 libglib2.0-0

COPY . .
RUN mkdir -p storage/jobs

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]