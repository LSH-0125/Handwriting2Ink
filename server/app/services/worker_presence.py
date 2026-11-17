"""워커 생존 신호.

워커는 몇 초마다 /api/worker/jobs/next 를 폴링한다. 그 마지막 시각을 기억해 두면
'잡이 stroke_ready 에서 안 움직임' 이 워커가 꺼진 탓인지 바로 알 수 있다.
(실제로 워커가 꺼진 채 웹 화면이 아무 설명 없이 무한 대기한 적이 있다.)

프로세스 메모리에만 둔다. 서버를 재시작하면 워커가 다음 폴링(3초)에서 다시 채운다.
"""
import time

_last_seen: float | None = None


def touch() -> None:
    global _last_seen
    _last_seen = time.time()


def seconds_since() -> float | None:
    return None if _last_seen is None else round(time.time() - _last_seen, 1)
