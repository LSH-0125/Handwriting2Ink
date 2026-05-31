"""
Stroke Extractor - 스켈레톤에서 좌표열(Stroke Sequence) 추출
============================================================
skeletonizer.py의 전처리/스켈레톤화 기능을 모듈로 사용하여,
스켈레톤에서 필기 순서를 담은 좌표열을 추출하고 시각화합니다.

사용법:
    python stroke_extractor.py --input image.png
    python stroke_extractor.py --input image.png --save
    python stroke_extractor.py --input image.png --detailed --save
"""

import argparse
import os
import sys
import numpy as np
import cv2
import matplotlib
if matplotlib.get_backend() == 'agg':  # 이미 다른 백엔드(Agg 등)가 설정된 경우 유지
    matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from collections import defaultdict, deque

# skeletonizer.py의 기능을 모듈로 사용
from skeletonizer import load_and_preprocess, skeletonize_zhang


# ============================================================
# 1. Graph 구축 및 분석
# ============================================================

# 8-방향 연결 오프셋
OFFSETS_8 = [(-1, -1), (-1, 0), (-1, 1),
             (0, -1),           (0, 1),
             (1, -1),  (1, 0),  (1, 1)]


def build_skeleton_graph(skeleton):
    """스켈레톤 이미지에서 그래프(인접 리스트)를 구축합니다.
    
    Args:
        skeleton: 스켈레톤 이미지 (uint8, 0 or 255)
    
    Returns:
        graph: {(y,x): [(ny,nx), ...]} 형태의 인접 리스트
        pixel_set: 스켈레톤 픽셀 좌표 집합
    """
    skel = (skeleton > 0).astype(np.uint8)
    ys, xs = np.where(skel > 0)
    pixel_set = set(zip(ys.tolist(), xs.tolist()))
    
    graph = defaultdict(list)
    for y, x in pixel_set:
        for dy, dx in OFFSETS_8:
            ny, nx = y + dy, x + dx
            if (ny, nx) in pixel_set:
                graph[(y, x)].append((ny, nx))
                
    
    # 고립 픽셀도 그래프에 포함
    for p in pixel_set:
        if p not in graph:
            graph[p] = []
    
    return graph, pixel_set


def find_special_points(graph):
    """끝점(degree 1)과 분기점(degree >= 3)을 찾습니다.
    
    Returns:
        end_points: 끝점 집합
        branch_points: 분기점 집합
    """
    end_points = set()
    branch_points = set()
    
    for node, neighbors in graph.items():
        degree = len(neighbors)
        if degree == 1:
            end_points.add(node)
        elif degree >= 3:
            branch_points.add(node)
    
    return end_points, branch_points


def split_into_connected_components(graph, pixel_set):
    """스켈레톤 그래프를 8-연결 컴포넌트 단위로 분리합니다."""
    visited = set()
    components = []

    for start in sorted(pixel_set):
        if start in visited:
            continue

        queue = deque([start])
        visited.add(start)
        component_pixels = []

        while queue:
            node = queue.popleft()
            component_pixels.append(node)
            for neighbor in graph[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        component_set = set(component_pixels)
        component_graph = defaultdict(list)
        for node in component_pixels:
            component_graph[node] = [n for n in graph[node] if n in component_set]

        components.append((component_graph, component_set))

    return components


# ============================================================
# 2. Stroke 추출 알고리즘
# ============================================================

def extract_strokes(skeleton, min_stroke_length=3, merge_angle=60, image_gray=None):
    """스켈레톤에서 좌표열(stroke sequences)을 추출합니다.
    
    알고리즘:
    1. 스켈레톤 픽셀로 그래프 구축 (8-연결)
    2. 끝점(degree 1)과 분기점(degree >= 3) 탐색
    3. 분기점 제거하여 그래프를 세그먼트로 분할
    4. 각 세그먼트에서 DFS로 점 순서 결정
    5. 연속성(곡률+명암) 기반 세그먼트 매칭 및 병합
    6. 획을 위→아래, 왼→오른 순서로 정렬
    
    Args:
        skeleton: 스켈레톤 이미지
        min_stroke_length: 최소 획 길이 (이보다 짧은 세그먼트 무시)
        merge_angle: 병합 각도 임계값 (도).
        image_gray: 서브픽셀 강도(Intensity) 조회를 위한 원본 Gray 이미지 (선택)
    
    Returns:
        strokes: list of np.ndarray, 각각 shape (N, 2), (x, y) 좌표
    """
    skel = (skeleton > 0).astype(np.uint8)
    
    # 1. 그래프 구축
    graph, pixel_set = build_skeleton_graph(skel)
    
    if not pixel_set:
        return []
    
    components = split_into_connected_components(graph, pixel_set)
    total_end_points = 0
    total_branch_points = 0
    total_raw_segments = 0
    ordered_segments = []

    for component_graph, component_pixels in components:
        end_points, branch_points = find_special_points(component_graph)
        total_end_points += len(end_points)
        total_branch_points += len(branch_points)

        # 글자/성분 단위로 분리한 뒤 그 안에서만 세그먼트화합니다.
        remaining = component_pixels - branch_points
        visited = set()
        raw_segments = []

        for start in sorted(remaining):
            if start in visited:
                continue

            component = []
            queue = deque([start])
            visited.add(start)

            while queue:
                node = queue.popleft()
                component.append(node)
                for neighbor in component_graph[node]:
                    if neighbor in remaining and neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            raw_segments.append(component)

        total_raw_segments += len(raw_segments)

        component_segments = []
        for segment in raw_segments:
            if len(segment) < 2:
                continue
            ordered = _trace_segment(segment, component_graph)
            if ordered is not None and len(ordered) >= 2:
                component_segments.append(ordered)

        if merge_angle > 0 and branch_points:
            added_bp = set(branch_points)
            filtered_segments = []
            for seg in component_segments:
                if len(seg) < 15:
                    start_is_bp = any(_is_8_neighbor(seg[0], bp) for bp in branch_points)
                    end_is_bp = any(_is_8_neighbor(seg[-1], bp) for bp in branch_points)
                    if start_is_bp and end_is_bp:
                        added_bp.update(seg)
                        continue
                filtered_segments.append(seg)

            component_segments = filtered_segments
            bp_clusters = _cluster_branch_points(added_bp, component_graph)
            before = len(component_segments)
            component_segments = _merge_continuous_segments(
                component_segments, bp_clusters, image_gray=image_gray, angle_threshold_deg=merge_angle
            )
            after = len(component_segments)
            if before != after:
                print(f"   컴포넌트 세그먼트 병합: {before} → {after} ({before - after}개 병합됨)")

        ordered_segments.extend(component_segments)

    print(
        f"   총 픽셀: {len(pixel_set)}, 컴포넌트: {len(components)}, "
        f"끝점: {total_end_points}, 분기점: {total_branch_points}, 세그먼트: {total_raw_segments}"
    )

    # 6. 최소 길이 필터 + (y, x) → (x, y) 변환
    strokes = []
    for segment in ordered_segments:
        if len(segment) >= min_stroke_length:
            stroke = np.array([(x, y) for y, x in segment])
            strokes.append(stroke)

    # 7. 획 순서 정렬 (위→아래, 왼→오른)
    strokes = order_strokes(strokes)
    
    return strokes


def _trace_segment(segment, full_graph):
    """세그먼트 내의 점들을 끝에서 끝으로 순서대로 추적합니다.
    
    세그먼트 내에서는 대부분의 노드가 degree 2 이하이므로
    한쪽 끝에서 시작하여 반대쪽 끝까지 순서대로 이어갑니다.
    """
    segment_set = set(segment)
    
    # 세그먼트 내부 인접 리스트 구축
    local_adj = defaultdict(list)
    for node in segment:
        for neighbor in full_graph[node]:
            if neighbor in segment_set:
                local_adj[node].append(neighbor)
    
    # 로컬 끝점 (세그먼트 내에서 degree <= 1인 노드)
    local_endpoints = [n for n in segment if len(local_adj[n]) <= 1]
    
    if local_endpoints:
        # 끝점이 있으면 그 중 하나에서 시작
        start = local_endpoints[0]
    else:
        # 닫힌 루프 → 아무 점에서 시작
        start = segment[0]
    
    # DFS로 순서 추적
    ordered = [start]
    trace_visited = {start}
    current = start
    
    while True:
        found = False
        for neighbor in local_adj[current]:
            if neighbor not in trace_visited:
                ordered.append(neighbor)
                trace_visited.add(neighbor)
                current = neighbor
                found = True
                break
        if not found:
            break
    
    return ordered


def order_strokes(strokes, row_tolerance=20):
    """획들을 시작점 기준으로 위→아래, 왼→오른 순서로 정렬합니다.
    작은 높이 차이는 뭉뚱그려(row_tolerance) 가로(좌→우) 정렬이 우선되도록 합니다.
    """
    if not strokes:
        return strokes
    
    indexed = []
    for i, s in enumerate(strokes):
        start_x, start_y = s[0]
        # 높이 차이가 row_tolerance 이내면 같은 줄로 취급
        row_id = start_y // row_tolerance
        indexed.append((i, row_id, start_x, start_y))
    
    # 줄 번호(row_id) 우선 정렬 후, X 좌표, 마지막으로 구체적인 Y 좌표 기준 정렬
    indexed.sort(key=lambda t: (t[1], t[2], t[3]))
    
    return [strokes[i] for i, _, _, _ in indexed]


# ============================================================
# 2.5 세그먼트 병합 (동일선상 판별)
# ============================================================

def _is_8_neighbor(p1, p2):
    """두 점이 8-연결 이웃인지 확인합니다."""
    return 0 < max(abs(p1[0] - p2[0]), abs(p1[1] - p2[1])) <= 1


def _compute_endpoint_direction(segment, endpoint_type, n_points=15):
    """세그먼트 끝점에서의 방향 벡터를 계산합니다.
    
    세그먼트 내부에서 끝점(분기점 쪽) 방향으로 향하는 단위 벡터를 반환합니다.
    n_points개의 점을 사용하여 안정적인 방향을 추정합니다.
    (분기점 근처 왜곡을 피하기 위해 n_points를 15 정도로 크게 사용)
    
    Args:
        segment: (y, x) 좌표 리스트 (순서 있음)
        endpoint_type: 'start' 또는 'end'
        n_points: 방향 계산에 사용할 점 수
    
    Returns:
        unit direction vector (dx, dy)
    """
    n = min(n_points, len(segment))
    if n < 2:
        return np.array([0.0, 0.0])
    
    if endpoint_type == 'start':
        # 내부 → 시작점 방향
        interior_pt = segment[n - 1]
        tip_pt = segment[0]
    else:
        # 내부 → 끝점 방향
        interior_pt = segment[-n]
        tip_pt = segment[-1]
    
    vec = np.array([tip_pt[1] - interior_pt[1],
                    tip_pt[0] - interior_pt[0]], dtype=float)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def _join_two_segments(seg_a, end_a, seg_b, end_b, bp_cluster):
    """두 세그먼트를 분기점 클러스터를 통해 연결합니다.
    
    seg_a의 end_a 쪽 끝과 seg_b의 end_b 쪽 끝을 연결합니다.
    단순 직선 연결 대신 클러스터 내부 경로를 따라 이어 shape를 보존합니다.
    """
    if end_a == 'start':
        part_a = list(reversed(seg_a))
    else:
        part_a = list(seg_a)
    
    if end_b == 'end':
        part_b = list(reversed(seg_b))
    else:
        part_b = list(seg_b)
    
    bridge = _build_cluster_bridge(part_a[-1], part_b[0], bp_cluster)
    merged = list(part_a)
    for point in bridge:
        if point != merged[-1]:
            merged.append(point)
    for point in part_b:
        if point != merged[-1]:
            merged.append(point)
    return merged


def _build_cluster_bridge(point_a, point_b, bp_cluster):
    """분기점 클러스터 내부의 최단 픽셀 경로를 찾아 세그먼트를 이어줍니다."""
    cluster_set = set(bp_cluster)
    start_neighbors = [bp for bp in cluster_set if _is_8_neighbor(point_a, bp)]
    end_neighbors = [bp for bp in cluster_set if _is_8_neighbor(point_b, bp)]

    if not start_neighbors or not end_neighbors:
        return []

    best_path = None
    best_length = None

    for start_bp in start_neighbors:
        queue = deque([(start_bp, [start_bp])])
        visited = {start_bp}

        while queue:
            current, path = queue.popleft()
            if current in end_neighbors:
                if best_length is None or len(path) < best_length:
                    best_path = path
                    best_length = len(path)
                break

            for neighbor in cluster_set:
                if neighbor in visited or not _is_8_neighbor(current, neighbor):
                    continue
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    return best_path or []


def _cluster_branch_points(branch_points, graph):
    """인접한 분기점들을 하나의 클러스터로 묶습니다."""
    visited = set()
    clusters = []
    bp_set = set(branch_points)
    
    for bp in sorted(branch_points):
        if bp in visited:
            continue
        
        comp = []
        queue = deque([bp])
        visited.add(bp)
        
        while queue:
            node = queue.popleft()
            comp.append(node)
            for neighbor in graph[node]:
                if neighbor in bp_set and neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        clusters.append(tuple(comp))
    
    return clusters


def _compute_endpoint_direction_and_curvature(segment, endpoint_type, n_points=8):
    """세그먼트 끝점에서의 국소 방향 벡터를 반환합니다.
    점 개수를 줄여 곡선(특히 필기체 꼬임)에서도 끝단 진입각을 더 예민하게 잡습니다.
    """
    n = min(n_points, len(segment))
    if n < 2:
        return np.array([0.0, 0.0])
    
    if endpoint_type == 'start':
        interior_pt = segment[n - 1]
        tip_pt = segment[0]
    else:
        interior_pt = segment[-n]
        tip_pt = segment[-1]
    
    vec = np.array([tip_pt[1] - interior_pt[1],
                    tip_pt[0] - interior_pt[0]], dtype=float)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def _merge_continuous_segments(segments, bp_clusters, image_gray=None, angle_threshold_deg=110):
    """분기점에서 곡률 연속성과 명암비(Intensity)를 고려하여 세그먼트 병합.

    교차점(Junction)마다 진입하는 획들을 쌍(Pair)으로 매칭합니다.

    [개선] Priority Queue(min-heap) 방식으로 O(n²·k) → O(n² log n) 으로 최적화.
    - 초기에 모든 후보 쌍을 heap에 한 번만 삽입
    - 병합 후 구식(stale) 항목은 pop 시 ID 유효성 검사로 O(1) 스킵
    - 새 병합 세그먼트의 후보 쌍만 heap에 추가 (증분 업데이트)
    """
    import heapq

    if not segments or not bp_clusters:
        return segments

    cos_threshold = np.cos(np.radians(180 - angle_threshold_deg))

    # ── 강도(intensity) 점수 ──
    def get_intensity_score(pt1, pt2):
        if image_gray is None:
            return 0.0
        i1 = 255 - int(image_gray[int(pt1[0]), int(pt1[1])])
        i2 = 255 - int(image_gray[int(pt2[0]), int(pt2[1])])
        return (i1 + i2) / 510.0

    # ── 안정적인 ID 기반 세그먼트 저장소 ──
    next_id = [0]
    def new_id():
        i = next_id[0]
        next_id[0] += 1
        return i

    segs = {}          # {seg_id: segment_list}
    for s in segments:
        sid = new_id()
        segs[sid] = list(s)

    # ── 클러스터 인접 정보: {seg_id: [(c_idx, end_type, direction, pt), ...]} ──
    # bp_clusters 각 클러스터의 픽셀을 set으로 미리 변환 (이웃 탐색 가속)
    bp_cluster_sets = [set(c) for c in bp_clusters]

    def build_seg_cluster_adj(sid, seg):
        """세그먼트 하나의 클러스터 접속 정보를 반환."""
        result = []
        start_matched = False
        end_matched = False
        for c_idx, cset in enumerate(bp_cluster_sets):
            if not start_matched:
                for bp in cset:
                    if _is_8_neighbor(seg[0], bp):
                        d = _compute_endpoint_direction_and_curvature(seg, 'start', 7)
                        result.append((c_idx, 'start', d, seg[0]))
                        start_matched = True
                        break
            if not end_matched:
                for bp in cset:
                    if _is_8_neighbor(seg[-1], bp):
                        d = _compute_endpoint_direction_and_curvature(seg, 'end', 7)
                        result.append((c_idx, 'end', d, seg[-1]))
                        end_matched = True
                        break
            if start_matched and end_matched:
                break
        return result

    # seg_id → [(c_idx, end_type, direction, pt), ...]
    seg_adj = {sid: build_seg_cluster_adj(sid, seg) for sid, seg in segs.items()}

    # cluster → [(seg_id, end_type, direction, pt), ...]
    cluster_members = defaultdict(list)
    for sid, adjs in seg_adj.items():
        for c_idx, end_type, d, pt in adjs:
            cluster_members[c_idx].append((sid, end_type, d, pt))

    # ── 비용 계산 ──
    def compute_cost(di, dj, pt_i, pt_j):
        dot = float(np.dot(di, dj))
        if dot >= cos_threshold:
            return None  # 각도 조건 불만족 → 후보 아님
        intensity_bonus = get_intensity_score(pt_i, pt_j) * 0.5
        return dot - intensity_bonus

    # ── 초기 heap 구축: O(n² log n) ──
    heap = []  # (cost, id_a, end_a, id_b, end_b, c_idx)
    for c_idx, members in cluster_members.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                id_i, ei, di, pt_i = members[i]
                id_j, ej, dj, pt_j = members[j]
                if id_i == id_j:
                    continue
                cost = compute_cost(di, dj, pt_i, pt_j)
                if cost is not None:
                    heapq.heappush(heap, (cost, id_i, ei, id_j, ej, c_idx))

    # ── 그리디 병합 루프: O(k log n) per merge ──
    while heap:
        cost, id_i, ei, id_j, ej, c_idx = heapq.heappop(heap)

        # stale 검사: 두 세그먼트가 아직 유효한지 확인
        if id_i not in segs or id_j not in segs:
            continue

        # 실제 방향이 여전히 유효한지 재확인 (세그먼트가 이전에 다른 쪽과 병합됐을 수 있음)
        seg_i = segs[id_i]
        seg_j = segs[id_j]
        di_now = _compute_endpoint_direction_and_curvature(seg_i, ei, 7)
        dj_now = _compute_endpoint_direction_and_curvature(seg_j, ej, 7)
        pt_i_now = seg_i[0] if ei == 'start' else seg_i[-1]
        pt_j_now = seg_j[0] if ej == 'start' else seg_j[-1]
        cost_now = compute_cost(di_now, dj_now, pt_i_now, pt_j_now)
        if cost_now is None:
            continue

        # ── 병합 수행 ──
        cluster = bp_clusters[c_idx]
        merged = _join_two_segments(seg_i, ei, seg_j, ej, cluster)

        del segs[id_i]
        del segs[id_j]

        # cluster_members에서 두 id 제거
        for c in list(cluster_members.keys()):
            cluster_members[c] = [(s, e, d, p) for s, e, d, p in cluster_members[c]
                                   if s != id_i and s != id_j]

        # 새 세그먼트 등록
        new_sid = new_id()
        segs[new_sid] = merged

        # 새 세그먼트의 클러스터 접속 정보 계산 후 heap에 추가
        new_adjs = build_seg_cluster_adj(new_sid, merged)
        seg_adj[new_sid] = new_adjs
        for c_idx2, end_type2, d2, pt2 in new_adjs:
            cluster_members[c_idx2].append((new_sid, end_type2, d2, pt2))
            # 같은 클러스터의 기존 세그먼트들과 새 쌍 추가
            for other_sid, other_end, other_d, other_pt in cluster_members[c_idx2]:
                if other_sid == new_sid:
                    continue
                cost_new = compute_cost(d2, other_d, pt2, other_pt)
                if cost_new is not None:
                    heapq.heappush(heap, (cost_new, new_sid, end_type2,
                                          other_sid, other_end, c_idx2))

    return list(segs.values())




# ============================================================
# 3. 시각화
# ============================================================

# 획별 구분 색상 (밝고 선명한 색상들)
STROKE_COLORS = [
    '#FF0000',  # Red
    '#0066FF',  # Blue
    '#00CC00',  # Green
    '#FF8800',  # Orange
    '#9900CC',  # Purple
    '#00CCCC',  # Cyan
    '#FF00FF',  # Magenta
    '#AAAA00',  # Olive
    '#0099FF',  # Sky Blue
    '#FF6600',  # Dark Orange
    '#6600CC',  # Indigo
    '#CC0066',  # Rose
    '#00CC66',  # Emerald
    '#FF3366',  # Coral
    '#3366FF',  # Royal Blue
]

# OpenCV용 BGR 색상
STROKE_COLORS_BGR = [
    (0, 0, 255),      # Red
    (255, 102, 0),     # Blue
    (0, 204, 0),       # Green
    (0, 136, 255),     # Orange
    (204, 0, 153),     # Purple
    (204, 204, 0),     # Cyan
    (255, 0, 255),     # Magenta
    (0, 170, 170),     # Olive
    (255, 153, 0),     # Sky Blue
    (0, 102, 255),     # Dark Orange
    (204, 0, 102),     # Indigo
    (102, 0, 204),     # Rose
    (102, 204, 0),     # Emerald
    (102, 51, 255),    # Coral
    (255, 102, 51),    # Royal Blue
]


def visualize_strokes(image_bgr, skeleton, strokes, save_path=None):
    """추출된 좌표열을 3패널로 시각화합니다.
    
    [원본 이미지] | [스켈레톤] | [좌표열 오버레이 (색상 + 화살표 + 번호)]
    """
    fig, axes = plt.subplots(1, 3, figsize=(20, 7))
    fig.suptitle(f'Stroke Extraction Result ({len(strokes)} strokes)',
                 fontsize=14, fontweight='bold')
    
    # ── Panel 1: 원본 이미지 ──
    axes[0].imshow(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
    axes[0].set_title('Original Image')
    axes[0].axis('off')
    
    # ── Panel 2: 스켈레톤 ──
    axes[1].imshow(skeleton, cmap='gray')
    axes[1].set_title('Skeleton')
    axes[1].axis('off')
    
    # ── Panel 3: 좌표열 시각화 ──
    axes[2].imshow(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB), alpha=0.3)
    axes[2].set_title(f'Extracted Strokes ({len(strokes)})')
    axes[2].axis('off')
    
    for i, stroke in enumerate(strokes):
        color = STROKE_COLORS[i % len(STROKE_COLORS)]
        xs = stroke[:, 0]
        ys = stroke[:, 1]
        
        # 획을 선으로 그리기
        axes[2].plot(xs, ys, color=color, linewidth=2.5, solid_capstyle='round')
        
        # 시작점 (큰 원)
        axes[2].plot(xs[0], ys[0], 'o', color=color, markersize=10,
                     markeredgecolor='white', markeredgewidth=2, zorder=5)
        
        # 끝점 (사각형)
        axes[2].plot(xs[-1], ys[-1], 's', color=color, markersize=7,
                     markeredgecolor='white', markeredgewidth=1.5, zorder=5)
        
        # 방향 화살표 (일정 간격으로)
        n = len(stroke)
        arrow_step = max(1, n // 5)
        for j in range(arrow_step, n - 1, arrow_step):
            dx = float(xs[j] - xs[j - 1])
            dy = float(ys[j] - ys[j - 1])
            if dx != 0 or dy != 0:
                axes[2].annotate(
                    '', xy=(xs[j], ys[j]),
                    xytext=(xs[j - 1], ys[j - 1]),
                    arrowprops=dict(arrowstyle='->', color=color, lw=1.5)
                )
        
        # 획 번호 라벨
        mid = n // 2
        axes[2].text(
            xs[mid], ys[mid], str(i + 1),
            fontsize=11, fontweight='bold', color='white',
            ha='center', va='center',
            bbox=dict(boxstyle='round,pad=0.3', facecolor=color, alpha=0.85,
                      edgecolor='white', linewidth=1)
        )
    
    # 범례 (획이 적을 때만)
    if 0 < len(strokes) <= 12:
        legend_elements = []
        for i in range(len(strokes)):
            color = STROKE_COLORS[i % len(STROKE_COLORS)]
            from matplotlib.lines import Line2D
            legend_elements.append(
                Line2D([0], [0], color=color, linewidth=2.5,
                       label=f'Stroke {i + 1} ({len(strokes[i])} pts)')
            )
        axes[2].legend(handles=legend_elements, loc='upper right',
                       fontsize=7, framealpha=0.8)
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"결과 저장: {save_path}")
    
    plt.show()


def visualize_strokes_detailed(image_bgr, strokes, save_path=None):
    """각 획을 개별적으로 상세하게 시각화합니다."""
    n_strokes = len(strokes)
    if n_strokes == 0:
        print("추출된 획이 없습니다.")
        return
    
    cols = min(4, n_strokes)
    rows = (n_strokes + cols - 1) // cols
    
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 5 * rows))
    fig.suptitle(f'Individual Strokes ({n_strokes} total)',
                 fontsize=14, fontweight='bold')
    
    # axes를 항상 2D 배열로 통일
    if n_strokes == 1:
        axes = np.array([[axes]])
    elif rows == 1:
        axes = axes.reshape(1, -1)
    elif cols == 1:
        axes = axes.reshape(-1, 1)
    
    for i in range(n_strokes):
        row, col = divmod(i, cols)
        ax = axes[row, col]
        stroke = strokes[i]
        color = STROKE_COLORS[i % len(STROKE_COLORS)]
        
        xs = stroke[:, 0]
        ys = stroke[:, 1]
        
        # 배경에 원본 이미지 흐리게
        ax.imshow(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB), alpha=0.2)
        
        # 획 그리기
        ax.plot(xs, ys, '-', color=color, linewidth=3, solid_capstyle='round')
        
        # 시작점 (녹색 원)
        ax.plot(xs[0], ys[0], 'o', color='#00CC00', markersize=12,
                markeredgecolor='white', markeredgewidth=2, label='Start', zorder=5)
        
        # 끝점 (빨간 사각형)
        ax.plot(xs[-1], ys[-1], 's', color='#FF0000', markersize=9,
                markeredgecolor='white', markeredgewidth=2, label='End', zorder=5)
        
        # 방향 화살표
        n = len(stroke)
        arrow_step = max(1, n // 4)
        for j in range(arrow_step, n - 1, arrow_step):
            dx = float(xs[j] - xs[j - 1])
            dy = float(ys[j] - ys[j - 1])
            if dx != 0 or dy != 0:
                ax.annotate(
                    '', xy=(xs[j], ys[j]),
                    xytext=(xs[j - 1], ys[j - 1]),
                    arrowprops=dict(arrowstyle='->', color=color, lw=2)
                )
        
        ax.set_title(f'Stroke {i + 1} ({len(stroke)} points)', fontsize=10)
        ax.legend(fontsize=7, loc='upper right')
        ax.axis('off')
    
    # 사용하지 않는 서브플롯 숨기기
    for i in range(n_strokes, rows * cols):
        row, col = divmod(i, cols)
        axes[row, col].axis('off')
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"상세 결과 저장: {save_path}")
    
    plt.show()


def create_stroke_overlay(image_bgr, strokes, thickness=3):
    """원본 이미지 위에 획별 색상으로 오버레이 이미지를 생성합니다."""
    overlay = image_bgr.copy()
    
    for i, stroke in enumerate(strokes):
        color = STROKE_COLORS_BGR[i % len(STROKE_COLORS_BGR)]
        
        # 획을 polyline으로 그리기
        pts = stroke.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(overlay, [pts], isClosed=False, color=color,
                      thickness=thickness, lineType=cv2.LINE_AA)
        
        # 시작점 (큰 원)
        start = tuple(stroke[0].astype(int))
        cv2.circle(overlay, start, thickness + 3, color, -1, lineType=cv2.LINE_AA)
        cv2.circle(overlay, start, thickness + 3, (255, 255, 255), 1, lineType=cv2.LINE_AA)
        
        # 끝점 (사각형)
        end = tuple(stroke[-1].astype(int))
        half = thickness + 2
        cv2.rectangle(overlay,
                      (end[0] - half, end[1] - half),
                      (end[0] + half, end[1] + half),
                      color, -1, lineType=cv2.LINE_AA)
        
        # 획 번호
        mid = len(stroke) // 2
        mid_pt = tuple(stroke[mid].astype(int))
        cv2.putText(overlay, str(i + 1), (mid_pt[0] - 5, mid_pt[1] + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
                    lineType=cv2.LINE_AA)
        cv2.putText(overlay, str(i + 1), (mid_pt[0] - 5, mid_pt[1] + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1,
                    lineType=cv2.LINE_AA)
    
    return overlay


def print_stroke_summary(strokes):
    """좌표열 요약 정보를 콘솔에 출력합니다."""
    print(f"\n{'=' * 65}")
    print(f"  추출된 획 수: {len(strokes)}")
    print(f"{'=' * 65}")
    print(f"  {'#':>3}  {'Points':>8}  {'Start (x, y)':>16}  "
          f"{'End (x, y)':>16}  {'Length':>8}")
    print(f"  {'-' * 59}")
    
    total_points = 0
    total_length = 0.0
    
    for i, stroke in enumerate(strokes):
        start = stroke[0]
        end = stroke[-1]
        
        # 경로 길이 계산
        diffs = np.diff(stroke, axis=0)
        path_length = np.sum(np.sqrt(diffs[:, 0] ** 2 + diffs[:, 1] ** 2))
        
        total_points += len(stroke)
        total_length += path_length
        
        print(f"  {i + 1:>3}  {len(stroke):>8}  "
              f"({start[0]:>6.0f},{start[1]:>6.0f})  "
              f"({end[0]:>6.0f},{end[1]:>6.0f})  "
              f"{path_length:>8.1f}")
    
    print(f"  {'-' * 59}")
    print(f"  Total: {total_points} points, path length: {total_length:.1f} px")
    print(f"{'=' * 65}")


# ============================================================
# 4. 메인 실행
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Stroke Extractor - 스켈레톤에서 좌표열(Stroke Sequence) 추출',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python stroke_extractor.py --input image.png             # 기본 실행
  python stroke_extractor.py --input image.png --save      # 결과 저장
  python stroke_extractor.py --input image.png --detailed  # 획별 상세 시각화
  python stroke_extractor.py --input image.png --min_length 5  # 최소 획 길이 설정
        """
    )
    parser.add_argument('--input', '-i', type=str, required=True,
                        help='입력 이미지 경로')
    parser.add_argument('--save', '-s', action='store_true',
                        help='결과 이미지 저장')
    parser.add_argument('--output_dir', '-o', type=str, default='./results',
                        help='결과 저장 경로 (기본: ./results)')
    parser.add_argument('--min_length', type=int, default=3,
                        help='최소 획 길이 — 이보다 짧은 세그먼트는 무시 (기본: 3)')
    parser.add_argument('--merge_angle', type=int, default=110,
                        help='세그먼트 병합 각도 임계값 (기본: 110도). 0이면 병합 비활성화')
    parser.add_argument('--detailed', '-d', action='store_true',
                        help='각 획을 개별적으로 상세 시각화')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input):
        print(f"파일을 찾을 수 없습니다: {args.input}")
        sys.exit(1)
    
    print(f"입력 이미지: {args.input}")
    
    # ── Step 1: 전처리 + 스켈레톤화 (skeletonizer 모듈 사용) ──
    print("\n1. 전처리 및 스켈레톤화...")
    img, gray, binary = load_and_preprocess(args.input)
    skeleton, elapsed, method_name = skeletonize_zhang(binary)
    print(f"   스켈레톤화 완료 ({method_name}, {elapsed * 1000:.1f}ms)")
    
    # ── Step 2: 좌표열 추출 ──
    print("\n2. 좌표열 추출 중...")
    strokes = extract_strokes(skeleton, min_stroke_length=args.min_length,
                              merge_angle=args.merge_angle, image_gray=gray)
    print(f"   → {len(strokes)}개의 획 추출 완료")
    
    # ── Step 3: 요약 출력 ──
    print_stroke_summary(strokes)
    
    # ── Step 4: 시각화 ──
    print("\n3. 시각화...")
    
    save_path = None
    detail_path = None
    overlay_path = None
    basename = os.path.splitext(os.path.basename(args.input))[0]
    
    if args.save:
        os.makedirs(args.output_dir, exist_ok=True)
        save_path = os.path.join(args.output_dir, f"{basename}_strokes.png")
        overlay_path = os.path.join(args.output_dir, f"{basename}_strokes_overlay.png")
    
    # 기본 3패널 시각화
    visualize_strokes(img, skeleton, strokes, save_path=save_path)
    
    # 상세 시각화 (옵션)
    if args.detailed:
        if args.save:
            detail_path = os.path.join(args.output_dir, f"{basename}_strokes_detail.png")
        visualize_strokes_detailed(img, strokes, save_path=detail_path)
    
    # 오버레이 이미지 저장 (옵션)
    if args.save:
        overlay = create_stroke_overlay(img, strokes, thickness=3)
        cv2.imwrite(overlay_path, overlay)
        print(f"오버레이 이미지 저장: {overlay_path}")
    
    print("\n완료!")


if __name__ == '__main__':
    main()
