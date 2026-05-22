# Handwriting2Ink

이미지에서 손글씨 형태의 스트로크 경로를 추출하고 렌더링하는 OCR 기반 파이프라인입니다.

## 구성 파일

- `ocr_layout.py`: 텍스트 영역을 검출하고 text/shape 레이아웃을 분리합니다.
- `render_strokes.py`: crop 영역을 스트로크 결과로 변환합니다.
- `pipeline.py`: 레이아웃 생성부터 스트로크 렌더링까지 한 번에 실행합니다.
- `simulate_drawing.py`, `skeletonizer.py`, `stroke_extractor.py`: 핵심 스트로크 생성 모듈입니다.

## 실행 환경

- Python 3.10 이상
- 의존성 설치:

```bash
pip install -r requirements.txt
```

## 빠른 시작

전체 파이프라인 실행:

```bash
python pipeline.py --input path/to/input_image.png
```

자주 사용하는 옵션:

- `--region_source ocr_merged` 또는 `--region_source ocr_raw`
- `--crop_scale 2.0`
- `--save_crop_debug`
- `--save_merged_debug`
- `--save_stroke_data`

기본 출력 경로는 `pilot_outputs/` 아래입니다.

## 참고

- 이 저장소는 소스 코드와 설정 파일 중심으로 관리합니다.
- 대용량 생성 결과물과 로컬 캐시 디렉토리는 `.gitignore`로 제외됩니다.
