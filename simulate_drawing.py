import turtle
import time
import argparse
import sys
import os
import cv2
import numpy as np
import matplotlib
matplotlib.use('Agg')  # 화면 없이 파일로 저장
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import font_manager

# Windows 한글 폰트 설정
def _set_korean_font():
    candidates = ['Malgun Gothic', 'NanumGothic', 'AppleGothic', 'NanumBarunGothic']
    for name in candidates:
        if any(name.lower() in f.name.lower() for f in font_manager.fontManager.ttflist):
            plt.rcParams['font.family'] = name
            return
    # 폴백: 맑은 고딕 경로 직접 등록
    mgothic = r'C:\Windows\Fonts\malgun.ttf'
    if os.path.exists(mgothic):
        font_manager.fontManager.addfont(mgothic)
        plt.rcParams['font.family'] = font_manager.FontProperties(fname=mgothic).get_name()

_set_korean_font()

# 기존 모듈 임포트
from skeletonizer import load_and_preprocess, skeletonize_zhang
from stroke_extractor import extract_strokes, STROKE_COLORS, STROKE_COLORS_BGR

def save_result_image(strokes, img, save_path, thickness=None):
    """3패널 결과 이미지를 저장합니다.

    [패널 1] 원본 이미지
    [패널 2] 원본 + 획 오버레이  (획별 색상, 시작점 작은 도트만 표시)
    [패널 3] 복원 이미지         (흰 배경에 획별 색상으로만 표시)
    """
    h, w = img.shape[:2]
    orig_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if len(img.shape) == 3 \
               else cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)

    thickness_ov = thickness if thickness is not None else max(2, int(max(w, h) * 0.006))
    thickness_re = thickness if thickness is not None else max(2, int(max(w, h) * 0.007))

    # ── 패널 2: 원본 위에 획 오버레이 ──
    base_overlay = cv2.addWeighted(orig_rgb, 0.45,
                                   np.ones_like(orig_rgb) * 255, 0.55, 0)
    for i, stroke in enumerate(strokes):
        if len(stroke) == 0:
            continue
        bgr = STROKE_COLORS_BGR[i % len(STROKE_COLORS_BGR)]
        color_rgb = (bgr[2], bgr[1], bgr[0])
        pts = stroke.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(base_overlay, [pts], isClosed=False, color=color_rgb,
                      thickness=thickness_ov, lineType=cv2.LINE_AA)
        # 시작점만 작은 도트로 표시
        sx, sy = int(stroke[0][0]), int(stroke[0][1])
        r = thickness_ov + 2
        cv2.circle(base_overlay, (sx, sy), r, color_rgb, -1, lineType=cv2.LINE_AA)
        cv2.circle(base_overlay, (sx, sy), r + 1, (255, 255, 255), 1, lineType=cv2.LINE_AA)

    # ── 패널 3: 복원 이미지 (흰 배경 + 색상 획) ──
    restored = np.ones((h, w, 3), dtype=np.uint8) * 255
    for i, stroke in enumerate(strokes):
        if len(stroke) == 0:
            continue
        bgr = STROKE_COLORS_BGR[i % len(STROKE_COLORS_BGR)]
        color_rgb = (bgr[2], bgr[1], bgr[0])
        pts = stroke.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(restored, [pts], isClosed=False, color=color_rgb,
                      thickness=thickness_re, lineType=cv2.LINE_AA)
        # 시작점 작은 도트
        sx, sy = int(stroke[0][0]), int(stroke[0][1])
        r = thickness_re + 1
        cv2.circle(restored, (sx, sy), r, color_rgb, -1, lineType=cv2.LINE_AA)

    # ── matplotlib 3패널 배치 ──
    fig_w = max(14, w / 40)
    fig_h = max(5,  h / 40)
    fig, axes = plt.subplots(1, 3, figsize=(fig_w, fig_h), dpi=120)

    axes[0].imshow(orig_rgb)
    axes[0].set_title("원본 이미지", fontsize=11)
    axes[0].axis('off')

    axes[1].imshow(base_overlay)
    axes[1].set_title(f"원본 + 획 오버레이  ({len(strokes)}획)", fontsize=11)
    axes[1].axis('off')

    axes[2].imshow(restored)
    axes[2].set_title("복원 이미지 (획 구분)", fontsize=11)
    axes[2].axis('off')

    plt.tight_layout(pad=1.5)
    plt.savefig(save_path, bbox_inches='tight', dpi=120)
    plt.close(fig)
    print(f"결과 이미지 저장 완료: {save_path}")


def save_black_strokes_image(strokes, img, save_path, thickness=None):
    """모든 획을 검은색으로 그린 이미지를 저장합니다 (색 구분 없음)."""
    h, w = img.shape[:2]
    canvas = np.ones((h, w, 3), dtype=np.uint8) * 255
    t = thickness if thickness is not None else max(2, int(max(w, h) * 0.007))
    for stroke in strokes:
        if len(stroke) == 0:
            continue
        pts = stroke.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [pts], isClosed=False, color=(0, 0, 0),
                      thickness=t, lineType=cv2.LINE_AA)
    # BGR → RGB for matplotlib
    fig, ax = plt.subplots(1, 1, figsize=(max(6, w / 100), max(6, h / 100)), dpi=120)
    ax.imshow(canvas)
    ax.axis('off')
    plt.tight_layout(pad=0.5)
    plt.savefig(save_path, bbox_inches='tight', dpi=120)
    plt.close(fig)
    print(f"흑백 획 이미지 저장 완료: {save_path}")


def draw_strokes_with_turtle(strokes, img_width, img_height, speed=0, thickness=None):
    """
    Turtle 모듈을 사용하여 추출된 획(Stroke)들을 애니메이션으로 랜더링합니다.

    창 크기는 모니터 해상도에 맞게 자동 축소되며, 좌표도 같은 비율로 스케일됩니다.
    """
    import tkinter as _tk
    # ── 모니터 해상도 조회 후 창 크기 결정 ──
    _root = _tk.Tk()
    _root.withdraw()
    screen_w = _root.winfo_screenwidth()
    screen_h = _root.winfo_screenheight()
    _root.destroy()

    MAX_W = int(screen_w * 0.85)
    MAX_H = int(screen_h * 0.85)

    scale = min(MAX_W / img_width, MAX_H / img_height, 1.0)
    win_w = int(img_width  * scale)
    win_h = int(img_height * scale)

    screen = turtle.Screen()
    screen.setup(width=win_w + 20, height=win_h + 20)
    screen.title("스마트 아카이브 - 자동 필기 시뮬레이션")
    screen.bgcolor("white")

    # Turtle 초기화
    pen = turtle.Turtle()
    pen.speed(0)       # turtle 자체 speed는 항상 최대 — 부드러움은 tracer로 제어
    pen.pensize(thickness if thickness is not None else max(1, int(2 * scale)))
    pen.hideturtle()

    # 좌표계 변환: 이미지(좌상단 원점, y↓) → Turtle(중앙 원점, y↑), 스케일 적용
    def to_turtle_coords(x, y):
        tx = (x - img_width  / 2) * scale
        ty = (img_height / 2 - y) * scale
        return tx, ty

    if speed == 0:
        screen.tracer(0)   # 자동 업데이트 OFF
    else:
        screen.tracer(1)   # 매 틱마다 갱신 (애니메이션 가시)

    for i, stroke in enumerate(strokes):
        if len(stroke) == 0:
            continue

        color_hex = STROKE_COLORS[i % len(STROKE_COLORS)]
        pen.color(color_hex)
        pen.penup()
        pen.goto(to_turtle_coords(*stroke[0]))
        pen.pendown()

        # 점 샘플링: speed>0 이면 보기 좋게 촘촘히, speed=0 이면 더 성기게
        step = max(1, len(stroke) // 80) if speed > 0 else max(1, len(stroke) // 20)
        for p_idx in range(1, len(stroke), step):
            pen.goto(to_turtle_coords(*stroke[p_idx]))
        pen.goto(to_turtle_coords(*stroke[-1]))  # 마지막 점 보장

        pen.penup()

        if speed == 0:
            screen.update()   # 획 하나 완성 후 화면에 반영

    screen.update()
    print("그리기 완료! 화면을 클릭하면 종료됩니다.")
    try:
        screen.exitonclick()
    except Exception:
        pass

def draw_strokes_in_paint(strokes, img_width, img_height,
                          canvas_x=None, canvas_y=None,
                          canvas_w=None, canvas_h=None,
                          countdown=5, move_interval=0.0,
                          stroke_delay=0.005, thickness=None):
    """
    ctypes Windows API를 직접 사용해 실제 그림판에 획을 마우스로 자동으로 그립니다.
    pyautogui의 오버헤드 없이 모든 스켈레톤 점을 그대로 전송 → Turtle과 동일한 품질.

    Parameters
    ----------
    strokes       : 획 좌표 리스트 (extract_strokes 반환값)
    img_width/h   : 원본 이미지 크기 (좌표 스케일링 기준)
    canvas_x/y    : 그림판 캔버스 좌상단 화면 좌표. None이면 카운트다운 후 마우스 위치 사용.
    canvas_w/h    : 그림판 캔버스 크기(픽셀). None이면 화면 비율로 자동 계산.
    countdown     : 그리기 시작 전 대기 시간(초).
    move_interval : 점 간 추가 sleep(초). 0이면 최대 속도. 그림판이 못 따라오면 0.001~0.003.
    stroke_delay  : 획 사이 대기 시간(초).
    thickness     : 사용 안 함(인터페이스 통일용).
    """
    import ctypes
    import ctypes.wintypes
    import time as _time

    user32 = ctypes.windll.user32

    # ── Windows 마우스 제어 ──
    MOUSEEVENTF_MOVE     = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP   = 0x0004

    def _down(sx, sy):
        """첫 점에서 왼쪽 버튼 누름"""
        user32.SetCursorPos(int(sx), int(sy))
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)

    def _move_drag(tx, ty):
        """드래그 중 이동. 실제 커서 위치 기준 상대 delta를 계산해 누적 오차를 방지."""
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        dx = int(tx - pt.x)
        dy = int(ty - pt.y)
        if dx != 0 or dy != 0:
            user32.mouse_event(MOUSEEVENTF_MOVE, dx, dy, 0, 0)

    def _up():
        """왼쪽 버튼 뗌"""
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def _failsafe_check():
        """마우스가 화면 좌상단 (0,0) 근처면 중단."""
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        if pt.x <= 5 and pt.y <= 5:
            raise KeyboardInterrupt("Failsafe: 마우스가 좌상단 모서리로 이동됨 → 중단")

    # 화면 크기 조회
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    # ── 캔버스 좌표 결정 ──
    if canvas_x is None or canvas_y is None:
        print("\n[그림판 자동 그리기]")
        print("  1) 그림판(또는 그리고 싶은 앱)을 미리 열어두세요.")
        print("  2) 브러시 도구 & 원하는 색상/굵기를 미리 선택하세요.")
        print(f"  3) {countdown}초 카운트다운 후 현재 마우스 위치를 캔버스 좌상단으로 사용합니다.")
        print("     → 지금 마우스를 그림판 캔버스의 좌상단 모서리에 올려두세요!\n")
        for i in range(countdown, 0, -1):
            print(f"  {i}초 후 시작...", end='\r', flush=True)
            _time.sleep(1)
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        canvas_x, canvas_y = pt.x, pt.y
        print(f"\n  캔버스 기준점 설정: ({canvas_x}, {canvas_y})")

    if canvas_w is None:
        canvas_w = min(int(screen_w * 0.8), screen_w - canvas_x - 10)
    if canvas_h is None:
        canvas_h = int(canvas_w * img_height / img_width)
        canvas_h = min(canvas_h, screen_h - canvas_y - 10)

    scale_x = canvas_w / img_width
    scale_y = canvas_h / img_height

    def to_screen(x, y):
        """이미지 좌표 → 화면 좌표 변환. Turtle과 동일한 스케일·중심 기준."""
        tx = (x - img_width  / 2) * scale_x
        ty = (img_height / 2 - y) * scale_y
        sx = int(canvas_x + canvas_w / 2 + tx)
        sy = int(canvas_y + canvas_h / 2 - ty)
        sx = max(canvas_x, min(sx, canvas_x + canvas_w - 1))
        sy = max(canvas_y, min(sy, canvas_y + canvas_h - 1))
        return sx, sy

    def densify_screen_points(points, max_gap=1):
        """연속 점 사이 간격을 max_gap 픽셀 이하로 보간해 손실을 줄입니다."""
        if len(points) <= 1:
            return points

        dense = [points[0]]
        for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
            dx = x1 - x0
            dy = y1 - y0
            steps = max(abs(dx), abs(dy))

            if steps <= max_gap:
                if dense[-1] != (x1, y1):
                    dense.append((x1, y1))
                continue

            for k in range(1, steps + 1):
                nx = int(round(x0 + dx * (k / steps)))
                ny = int(round(y0 + dy * (k / steps)))
                if dense[-1] != (nx, ny):
                    dense.append((nx, ny))

        return dense

    print(f"\n  캔버스 영역: ({canvas_x}, {canvas_y}) ~ "
          f"({canvas_x + canvas_w}, {canvas_y + canvas_h})  ({canvas_w}×{canvas_h}px)")
    print(f"  총 {len(strokes)}획 그리기 시작! (중단: 마우스를 화면 좌상단 모서리로 이동)\n")

    try:
        for i, stroke in enumerate(strokes):
            if len(stroke) == 0:
                continue

            _failsafe_check()

            # Turtle(speed=0)과 동일한 샘플링 후 1px 보간
            step = max(1, len(stroke) // 20)
            sampled_stroke = stroke[::step]
            if not np.array_equal(sampled_stroke[-1], stroke[-1]):
                sampled_stroke = np.vstack([sampled_stroke, stroke[-1]])

            screen_pts = [to_screen(*pt) for pt in sampled_stroke]
            screen_pts = densify_screen_points(screen_pts, max_gap=1)

            if len(screen_pts) == 0:
                continue

            sx, sy = screen_pts[0]
            _down(sx, sy)
            for tx, ty in screen_pts[1:]:
                _move_drag(tx, ty)
                if move_interval > 0:
                    _time.sleep(move_interval)
            _up()

            if (i + 1) % 5 == 0 or i == len(strokes) - 1:
                print(f"  진행: {i + 1}/{len(strokes)} 획 완료")

            if stroke_delay > 0:
                _time.sleep(stroke_delay)

    except KeyboardInterrupt as e:
        _up()
        print(f"\n중단됨: {e}")
        return

    print("\n그리기 완료!")


def main():
    parser = argparse.ArgumentParser(description='Turtle을 이용한 필기 애니메이션 시연')
    parser.add_argument('--input', '-i', type=str, required=True, help='입력 이미지 경로')
    parser.add_argument('--speed', '-s', type=int, default=0, help='그리기 속도 (1~10, 0은 최고속도)')
    parser.add_argument('--save', action='store_true', help='결과 이미지 저장')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='3패널 컬러 결과 이미지 저장 경로 (기본: 입력파일명_result.png)')
    parser.add_argument('--output_black', type=str, default=None,
                        help='흑백 획 이미지 저장 경로 (기본: 입력파일명_strokes_black.png)')
    parser.add_argument('--no_turtle', action='store_true', help='Turtle 애니메이션 없이 저장만 수행')
    parser.add_argument('--min_area', type=int, default=50,
                        help='잡음 제거: 이 픽셀 수 미만의 고립 영역 제거 (기본 50, 메모지 질감 심하면 100~300)')
    parser.add_argument('--thickness', '-t', type=int, default=None,
                        help='폴 굵기 (px). 미지정 시 이미지 크기에 비례해 자동 계산')
    # ── 그림판 자동 그리기 옵션 ──
    parser.add_argument('--paint', action='store_true',
                        help='pyautogui로 실제 그림판에 자동 그리기 (Turtle 대신)')
    parser.add_argument('--paint_x', type=int, default=None,
                        help='그림판 캔버스 좌상단 X 좌표 (미지정시 카운트다운 후 마우스 위치 사용)')
    parser.add_argument('--paint_y', type=int, default=None,
                        help='그림판 캔버스 좌상단 Y 좌표 (미지정시 카운트다운 후 마우스 위치 사용)')
    parser.add_argument('--paint_w', type=int, default=None,
                        help='그림판 캔버스 너비 (px). 미지정시 화면 80%% 사용')
    parser.add_argument('--paint_h', type=int, default=None,
                        help='그림판 캔버스 높이 (px). 미지정시 이미지 비율로 자동 계산')
    parser.add_argument('--countdown', type=int, default=5,
                        help='그리기 시작 전 대기 시간(초). 기본 5초.')
    parser.add_argument('--move_interval', type=float, default=0.0,
                        help='마우스 이동 속도(초/점). 기본 0.005. 빠르니 줄이기 (0.001), 느리니 늘리기 (0.01).')
    parser.add_argument('--stroke_delay', type=float, default=0.005,
                        help='획 사이 대기 시간(초). 기본 0.05.')
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print("파일이 존재하지 않습니다.")
        sys.exit(1)

    print(f"[{args.input}] 획 추출 중...")
    img, gray, binary = load_and_preprocess(args.input, min_area=args.min_area)
    h, w = img.shape[:2]

    skeleton, _, _ = method_skeletonize_zhang(binary)
    strokes = extract_strokes(skeleton, image_gray=gray)
    print(f"총 {len(strokes)}가닥의 획을 그립니다!")

    # 결과 이미지 저장
    if args.save or args.output or args.output_black or args.no_turtle:
        base = os.path.splitext(os.path.basename(args.input))[0]
        out_dir = os.path.dirname(args.input) or '.'

        save_path = args.output or os.path.join(out_dir, f"{base}_result.png")
        black_path = args.output_black or os.path.join(out_dir, f"{base}_strokes_black.png")

        save_result_image(strokes, img, save_path, thickness=args.thickness)
        save_black_strokes_image(strokes, img, black_path, thickness=args.thickness)

    # Turtle 애니메이션
    if not args.no_turtle and not args.paint:
        draw_strokes_with_turtle(strokes, w, h, speed=args.speed, thickness=args.thickness)

    # 그림판 자동 그리기
    if args.paint:
        draw_strokes_in_paint(
            strokes, w, h,
            canvas_x=args.paint_x, canvas_y=args.paint_y,
            canvas_w=args.paint_w, canvas_h=args.paint_h,
            countdown=args.countdown,
            move_interval=args.move_interval,
            stroke_delay=args.stroke_delay,
        )

if __name__ == '__main__':
    main()
