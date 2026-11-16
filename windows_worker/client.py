"""H2I 서버 API 클라이언트 (워커용).

의존성 없이 stdlib urllib 만 쓴다. 워커는 GUI 자동화 때문에 환경이 이미
까다로우므로 패키지를 더 얹지 않는다.

서버 엔드포인트 (server/app/api/worker.py):
  GET  /api/worker/jobs/next
  GET  /api/worker/jobs/{id}/strokes
  POST /api/worker/jobs/{id}/status   {"status": ..., "message": ...}
  POST /api/worker/jobs/{id}/fail     {"error_code": ..., "error_message": ...}
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class ServerError(RuntimeError):
    pass


class ServerClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------- 내부
    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        data = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise ServerError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise ServerError(f"{method} {path} -> 연결 실패: {e.reason}") from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    # ------------------------------------------------------------- 공개
    def health(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base}/health")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read()).get("status") == "ok"
        except Exception:
            return False

    def next_job(self) -> dict | None:
        """할당 가능한 잡 하나. 없으면 None.

        주의: 서버의 /jobs/next 는 row lock 이 없어 워커를 2대 이상 띄우면
        같은 잡을 중복 할당한다. 지금은 워커 1대 전제다.
        """
        payload = self._request("GET", "/api/worker/jobs/next")
        return (payload or {}).get("job")

    def download_strokes(self, job_id: str, dest: Path) -> Path:
        url = f"{self.base}/api/worker/jobs/{job_id}/strokes"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise ServerError(f"strokes 다운로드 실패 (HTTP {e.code})") from e
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        # 내용 검증: 빈 파일이나 깨진 JSON 이 주입 단계까지 내려가지 않게 한다
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ServerError(f"strokes.json 파싱 실패: {e}") from e
        if not data.get("strokes"):
            raise ServerError(f"strokes 가 비어 있습니다 (job {job_id})")
        return dest

    def report_status(self, job_id: str, status: str, message: str | None = None,
                      stroke_count: int | None = None) -> None:
        body: dict = {"status": status, "message": message}
        if stroke_count is not None:
            body["stroke_count"] = stroke_count
        self._request("POST", f"/api/worker/jobs/{job_id}/status", body)

    def report_failure(self, job_id: str, code: str, message: str) -> None:
        self._request("POST", f"/api/worker/jobs/{job_id}/fail",
                      {"error_code": code, "error_message": message[:2000]})
