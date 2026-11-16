# Windows 워커 (version2)

mac 워커를 Windows로 옮긴 것. `strokes.json` 을 받아 GoodNotes for Windows 에
실제 마우스 입력으로 필기한다.

## v1(mac) 과 결정적으로 다른 점: 전달 경로

mac 워커는 GoodNotes에 그린 뒤 올가미로 복사해서 **NSPasteboard 의
`com.goodnotesapp.goodnotes5.notes` 바이너리**를 떠 `.bin` 으로 올렸다.
Windows 에서는 이게 **불가능하다.**

실측 결과, GoodNotes for Windows 의 "복사하기" 가 시스템 클립보드에 남기는 것은
잉크가 아니라 타입 이름 문자열뿐이다:

```
[13] CF_UNICODETEXT  68 B  'com.goodnotesapp.goodnotes5.notes\0'
[ 1] CF_TEXT         34 B  'com.goodnotesapp.goodnotes5.notes\0'
[49298] Chromium internal source URL  'https://web.goodnotes.com/s/...'
```

GoodNotes for Windows 는 `web.goodnotes.com` 을 WebView2 로 감싼 웹앱이고,
Chromium 클립보드 API 로는 네이티브 커스텀 포맷에 임의 바이너리를 쓸 수 없다.
실제 획 데이터는 앱 내부에 남고 클립보드엔 마커만 올라간다. 버그가 아니라 설계다.

그래서 v2 는 **공유 링크로 연결된 노트북**을 통해 전달한다.

Goodnotes Cloud(계정 전체 라이브러리 동기화)는 Goodnotes Pro 전용인데다
Windows 쪽이 제대로 동작하지 않는다. 대신 노트북 단위 공유 링크를 쓰면
요금제 제약 없이 아이패드 - Windows 간 실시간 양방향 동기화가 된다.

연결 절차 (한 번만):

1. 아이패드에서 대상 노트를 연다
2. 상단 공유(내보내기) 아이콘 -> **공유 가능한 링크** 토글 ON -> 링크 복사
3. 링크를 Windows PC 로 보내 연다 -> Windows 굿노트 앱 **공유됨** 탭에 등록된다

연결된 노트북은 web.goodnotes.com/s/<id> 형태의 주소를 갖는다.
워커는 "그 노트북 페이지에 그렸다" 까지만 책임지고, 기기 간 전달은 공유 링크가 한다.
`.bin` / `bin_ready` 단계가 없다.

상태 흐름: `stroke_ready -> assigned_to_worker -> drawing -> drawn -> delivered`
(`delivered` 는 워커가 아니라 앱이 결과를 확인했을 때 찍는다)

> **운영상 전제**: 워커는 그 시점에 열려 있는 페이지에 그린다.
> 실행 전에 Windows 굿노트에서 **공유 링크로 연결된 대상 노트북의 빈 페이지**를
> 열어둬야 한다. 다른 노트북이 열려 있으면 탭 대조에서 걸러진다.
>
> **여러 장을 연속으로 시연할 때는 사이사이 페이지를 넘겨야 한다.** 워커는
> 페이지를 넘기지 않는다(자동 페이지 넘김은 GUI 조작 실패 위험이 커서 넣지
> 않았다). 이미 잉크가 있는 페이지면 `on_dirty_page` 설정에 따라 경고하거나
> (`warn`, 기본) 아예 거부한다(`abort`).

## 구성

| 파일 | 역할 |
|---|---|
| `winplat.py` | 창 제어 · DPI · 잉크 입력 · 클립보드. mac 의 osascript/NSPasteboard 대체 |
| `controller.py` | `strokes.json` → 좌표 매핑 → 종이 검출 → 필기 → 반영 검증 |
| `client.py` | 서버 `/api/worker/*` 클라이언트 (stdlib urllib 만 사용) |
| `worker.py` | 폴링 루프 |
| `probe.py` | 진단용: `info` / `draw` / `clip` / `watch` |

좌표 변환은 `server/pipeline/goodnotes_writer.py` 를 그대로 import 한다. mac 경로는 건드리지 않았다.

## 실행

대상 노트북(`소페_시연용`)을 GoodNotes 에서 연 뒤, **한 번만** 보정한다:

```
python worker.py --calibrate
```

탭 줄을 캡처해 `notebook_ref.png` 로 저장한다. 이후 워커는 매 실행마다 탭을
대조해서 **다른 노트북이 열려 있으면 그리지 않고 멈춘다.** (GoodNotes 는
WebView2 라 UI Automation 으로 탭 이름을 읽을 수 없어 픽셀로 대조한다.)

```
set H2I_WORKER_TOKEN=...
python worker.py --server http://<서버>:8000 --execute
```

- `--execute` 없으면 dry-run (마우스를 잡지 않는다)
- `--once` 잡 하나만 처리
- 중단: 마우스를 화면 좌상단 모서리로 (pyautogui FAILSAFE) 또는 Ctrl+C

실행 전 GoodNotes for Windows 에서 **대상 노트북의 페이지를 열어둬야 한다.**
워커는 현재 열린 페이지에 그린다.

## Windows 에서 실제로 물렸던 것들

고치면서 전부 실측으로 잡은 것이라 다시 건드릴 때 참고:

1. **최소화 창 좌표** — `GetWindowRect` 가 `(-32000,-32000)` 을 준다. 그대로 쓰면
   화면 밖에 그려서 에러 없이 아무 일도 안 일어난다. → `IsIconic` 감지 후 복원.
2. **DPI 단위 혼용** — `GetWindowRect` 는 물리 픽셀, `GetSystemMetrics` 는 DPI 선언
   전이면 논리 픽셀. 125% 스케일에서 정상 창을 "화면 밖" 으로 오판한다.
   → `virtual_screen()` 이 `ensure_dpi_aware()` 를 먼저 호출.
3. **브라우저 탭 오인** — 창 제목으로 찾으면 `web.goodnotes.com` 을 띄운 Edge 탭이
   걸린다. → 프로세스 이름(`GoodnotesWindows.exe`) 으로 판정.
4. **툴바 좌표** — 툴바는 창 **중앙 정렬**이다. 좌상단 기준 절대 오프셋으로 박으면
   최대화하는 순간 펜 클릭이 빗나가고, 선택 도구인 채로 드래그해 잉크 대신 선택이 된다.
   → 중앙 기준 `pen_tool_dx` 로 저장.
5. **드래그 방식** — `pyautogui` 의 `duration` 보간은 WebView2 캔버스가 포인터
   이벤트로 받지 못한다. 작은 `sleep` 을 끼운 개별 `moveTo` 여야 한다.
6. **선택 드래그 궤적** — 사각형 둘레를 돌아 시작점으로 복귀하면 선택이 풀린다.
   좌상단에서 눌러 **우하단에서 떼야** 한다.
7. **종이 영역** — 창 비율로 추정하면 획이 종이 밖으로 나간다. 종이(248,247,233)는
   배경(230,229,228)과 R−B 차이로 분리되므로 픽셀로 직접 찾는다. 최대화 직후에는
   WebView2 가 리레이아웃 중이라 못 찾으니 대기 후 재시도.
8. **Ctrl+C 미바인딩** — 복사는 우클릭 컨텍스트 메뉴로만 된다. (지금은 안 쓴다)
9. **포커스 상실** — 다른 앱이 수시로 포커스를 채간다. 바로 실패시키면 잡이 버려지므로
   `ensure_front()` 로 재획득하고, 필기 중 상실은 `replay_strokes` 가드가 중단시킨다.

## 아직 안 된 것

- **GoodNotes 동기화 Windows → iOS 를 실제로 확인하지 않았다.** 계정/아이패드가 필요하다.
- 서버는 여전히 `.bin` 계약을 들고 있다 (`binary_path`, `pasteboard_type`, `/binary`).
  `GET /api/jobs/{id}` 가 `binary_ready` 를 돌려주는데 이 경로에서는 영원히 false 다.
  클라이언트가 완료를 못 본다 → 서버 수정 필요.
- 워커가 새 페이지를 만들지 않는다. 열린 페이지에 덧그린다.
- `/api/worker/jobs/next` 에 row lock 이 없어 워커 1대 전제.
