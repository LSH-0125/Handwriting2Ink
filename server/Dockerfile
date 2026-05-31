FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    gcc \
    && ln -sf /usr/lib/x86_64-linux-gnu/libgomp.so.1 /usr/lib/libgomp.so.1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p storage/jobs

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]