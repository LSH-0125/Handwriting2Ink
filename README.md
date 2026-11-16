# Handwriting2Ink

이미지에서 손글씨 형태의 스트로크 경로를 추출하고 렌더링하는 OCR 기반 파이프라인입니다.

## 구성

| 디렉토리 | 역할 |
|---|---|
| `server/pipeline/` | OCR + 스트로크 추출 파이프라인 (단일 정본) |
| `server/app/` | FastAPI 서버 (job 관리) |
| `windows_worker/` | GoodNotes for Windows 주입 워커 (v2) |
| `server/app/static/` | 아이패드용 웹 클라이언트 (사파리로 접속) |

전달 경로는 클립보드 `.bin` 이 아니라 **공유 링크로 연결된 GoodNotes 노트북**이다.
자세한 내용은 `windows_worker/README.md` 참조.

## 구성 요소가 도는 곳

```
아이패드 사파리  ──업로드──>  서버 (Railway 또는 윈도우 노트북)
                                   |
                                   | 워커가 폴링 (아웃바운드만)
                                   v
                          윈도우 노트북: worker.py
                                   |
                                   | 마우스 입력
                                   v
                       GoodNotes for Windows (소페_시연용)
                                   |
                                   | 공유 링크 동기화
                                   v
                              아이패드 GoodNotes
```

웹 클라이언트는 **자신을 서빙한 서버와 같은 오리진**에 붙는다. 그래서 서버를
Railway 에 두든 노트북에 두든 아이패드에서 **그 주소를 열기만** 하면 되고,
페이지를 고칠 필요가 없다. 워커 쪽도 `--server` 한 줄만 바꾸면 된다.

## 서버 실행

```bash
cd server
cp .env.example .env     # DATABASE_URL, WORKER_TOKEN 채우기
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

아이패드에서 `http://<서버주소>:8000/` 을 연다.
공개 배포라면 `.env` 에 `CLIENT_KEY` 를 넣고 `?k=<키>` 를 붙여 연다.

## Railway 배포

`server/Dockerfile` 로 배포한다. 확인할 것:

- **Root Directory 를 `server` 로 설정**해야 한다. Dockerfile 이 레포 루트가
  아니라 `server/` 에 있어서, 기본값(루트)이면 Railway 가 못 찾는다.
- 환경변수 3개
  - `DATABASE_URL` — Postgres 애드온이 주는 값
  - `WORKER_TOKEN` — 워커가 쓸 Bearer 토큰
  - `CLIENT_KEY` — 공개되므로 반드시 설정. 아이패드는 `?k=<값>` 으로 연다
- 포트는 Railway 가 주입하는 `$PORT` 를 따른다 (Dockerfile 의 CMD 가 쉘 형식이어야
  전개된다. exec 형식으로 되돌리지 말 것).
- 파일시스템은 휘발성이다. `strokes.json` 은 워커가 몇 초 안에 가져가므로
  문제되지 않지만, 재배포 중이던 job 은 유실된다.
- 콜드 스타트 + paddle 모델 로딩 때문에 **첫 요청이 느리다. 시연 전에 한 장 미리 돌려 데워둘 것.**

워커는 노트북에서 그대로 돌리고 서버 주소만 바꾼다:

```
python worker.py --server https://<railway-주소> --token <WORKER_TOKEN> --execute
```

워커는 아웃바운드로만 폴링하므로 노트북에 열어줄 포트가 없다.

## 파이프라인 파일

- `ocr_layout.py`: 텍스트 영역을 검출하고 text/shape 레이아웃을 분리합니다.
- `render_strokes.py`: crop 영역을 스트로크 결과로 변환합니다.
- `pipeline.py`: 레이아웃 생성부터 스트로크 렌더링까지 한 번에 실행합니다.
- `simulate_drawing.py`, `skeletonizer.py`, `stroke_extractor.py`: 핵심 스트로크 생성 모듈입니다.


```bash
pip install -r requirements.txt
```

## 빠른 시작

프로젝트 루트에서 실행하세요.

전체 파이프라인 실행:

```bash
python server/pipeline/pipeline.py --input path/to/input_image.png
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
