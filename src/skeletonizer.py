"""
Zhang-Suen 기반 스켈레톤화 모듈
================================
실제 파이프라인에서 사용하는 전처리와 스켈레톤화 기능만 제공합니다.
"""

import time

import cv2
import numpy as np
from skimage import img_as_ubyte
from skimage.morphology import skeletonize


def load_and_preprocess(image_input, target_size=None):
    """이미지를 로드하고 이진화 전처리를 수행합니다.
    (파일 경로 또는 OpenCV BGR numpy 배열 입력 지원)
    """
    if isinstance(image_input, str):
        img = cv2.imread(image_input)
        if img is None:
            raise FileNotFoundError(f"이미지를 로드할 수 없습니다: {image_input}")
    else:
        img = image_input.copy()

    if target_size is not None:
        h, w = img.shape[:2]
        scale = target_size / max(h, w)
        if scale < 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)

    # 글자가 전경(흰색)으로 나오도록 반전 이진화를 사용합니다.
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kernel = np.ones((2, 2), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    return img, gray, binary


def skeletonize_zhang(binary):
    """Zhang-Suen thinning으로 1px skeleton을 생성합니다."""
    binary_bool = binary > 0
    start = time.time()
    skeleton = skeletonize(binary_bool)
    elapsed = time.time() - start
    return img_as_ubyte(skeleton), elapsed, "Skeletonize (Zhang-Suen)"
