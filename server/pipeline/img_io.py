"""한글 경로에서 안전한 이미지 입출력.

Windows 에서 cv2.imread / cv2.imwrite 는 비ASCII 경로를 처리하지 못한다.
imread 는 None 을 돌려주고 **imwrite 는 예외 없이 False 만 돌려준다** -
즉 파일이 안 생겼는데 아무도 모른다. 레포가 OneDrive\바탕 화면 아래 있으면
전부 조용히 실패한다. (실측으로 확인했다.)

모듈 이름을 img_io 로 둔 이유: imageio 로 두면 skimage 가 쓰는 실제
imageio 패키지를 가린다.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imread_safe(path) -> np.ndarray | None:
    """비ASCII 경로도 읽는다. 실패하면 None."""
    path = str(path)
    image = cv2.imread(path)
    if image is not None:
        return image
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_safe(path, image: np.ndarray) -> bool:
    """비ASCII 경로에도 쓴다. 실패하면 조용히 넘어가지 않고 예외를 던진다."""
    path = Path(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)

    if cv2.imwrite(str(path), image):
        return True

    ok, buffer = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise RuntimeError(f"이미지 인코딩 실패: {path}")
    buffer.tofile(str(path))
    if not path.exists():
        raise RuntimeError(f"이미지 저장 실패: {path}")
    return True
