"""실험 단계별 변형 정의. bench.py <stage> 로 실행한다."""


def _v(name, **kw):
    return {"name": name, **kw}


def stageA():
    """이진화 방법 x 처리 단위. 필터/종이마스크 없이 방법 자체만 비교한다.

    crop 모드는 2배 확대 crop 위에서 이진화하므로 창 크기(block)를 2배로 둔다.
    배경 추정(bg_ksize)은 OCR 축소 좌표(원본의 0.8배) 기준이다.
    """
    C = dict(mode="crops", crop_scale=2.0, smooth=5)
    P = dict(mode="page", page_max=2000, page_scale=1.0, smooth=5)
    return [
        _v("A0_crops_otsu_LSH(현재)",      **C, bin=dict(method="otsu")),
        _v("A0j_crops_otsu_Jihun(무스무딩)", **(C | {"smooth": 0}), bin=dict(method="otsu")),
        _v("A1_crops_norm_otsu",           **C, bin=dict(method="norm_otsu", bg_ksize=41)),
        _v("A2_crops_norm_fixed",          **C, bin=dict(method="norm_fixed", bg_ksize=41, fixed=0.22)),
        _v("A3_crops_norm_hyst",           **C, bin=dict(method="norm_hyst", bg_ksize=41)),
        _v("A4_crops_adaptive",            **C, bin=dict(method="adaptive", block=81, C=12)),
        _v("A5_crops_sauvola",             **C, bin=dict(method="sauvola", block=81, k=0.25)),
        _v("A6_crops_norm_sauvola",        **C, bin=dict(method="norm_sauvola", bg_ksize=41, block=81)),
        _v("P0_page_otsu",                 **P, bin=dict(method="otsu")),
        _v("P1_page_norm_otsu",            **P, bin=dict(method="norm_otsu", bg_ksize=41)),
        _v("P2_page_norm_fixed",           **P, bin=dict(method="norm_fixed", bg_ksize=41, fixed=0.22)),
        _v("P3_page_norm_hyst",            **P, bin=dict(method="norm_hyst", bg_ksize=41)),
        _v("P4_page_adaptive",             **P, bin=dict(method="adaptive", block=41, C=12)),
        _v("P5_page_sauvola",              **P, bin=dict(method="sauvola", block=41, k=0.25)),
    ]


# ---------------------------------------------------------------- B: 잡음 제거
PAGE = dict(mode="page", page_max=2000, page_scale=1.0, smooth=5)
NORM_OTSU = dict(method="norm_otsu", bg_ksize=41)


def stageB():
    """페이지 모드 정규화 이진화 위에 종이 처리/성분 필터를 쌓는다."""
    T = dict(max_thickness=18)          # 펜 3~5px 의 3~4배. 그림자 덩어리 제거용
    TA = dict(max_thickness=18, min_area=12)
    TAB = dict(max_thickness=18, min_area=12, drop_border=True)
    return [
        _v("B0_P1_base",                **PAGE, bin=NORM_OTSU),
        _v("B1_P1_paper",               **PAGE, bin=NORM_OTSU, paper=True),
        _v("B2_P1_rect",                **PAGE, bin=NORM_OTSU, rectify=True),
        _v("B3_P1_rect_thick",          **PAGE, bin=NORM_OTSU, rectify=True, filt=T),
        _v("B4_P1_rect_thick_area",     **PAGE, bin=NORM_OTSU, rectify=True, filt=TA),
        _v("B5_P1_rect_thick_area_bord",**PAGE, bin=NORM_OTSU, rectify=True, filt=TAB),
        _v("B6_P3hyst_rect_TA",         **PAGE, bin=dict(method="norm_hyst", bg_ksize=41), rectify=True, filt=TA),
        _v("B7_P2fixed_rect_TA",        **PAGE, bin=dict(method="norm_fixed", bg_ksize=41), rectify=True, filt=TA),
        _v("B8_P1_paper_TA",            **PAGE, bin=NORM_OTSU, paper=True, filt=TA),
        _v("B9_A1crops_paper_TA",       mode="crops", crop_scale=2.0, smooth=5, bin=NORM_OTSU, paper=True,
                                        filt=dict(max_thickness=36, min_area=40)),
    ]


def sweep(base: dict, key_path: str, values, prefix: str):
    """base 의 한 파라미터만 바꿔가며 변형을 만든다. key_path 예: 'bin.bg_ksize', 'smooth'."""
    import copy
    out = []
    for val in values:
        v = copy.deepcopy(base)
        if "." in key_path:
            a, b = key_path.split(".")
            v.setdefault(a, {})[b] = val
        else:
            v[key_path] = val
        v["name"] = f"{prefix}_{key_path.split('.')[-1]}={val}"
        out.append(v)
    return out


# stageB 승자: 페이지 + 조명평탄화 Otsu + 종이 펴기, 두께 필터 없음 (F1 0.973, 작F1 0.939, junk 0)
BEST_B = dict(name="best", mode="page", page_max=2000, page_scale=1.0, smooth=5,
              bin=dict(method="norm_otsu", bg_ksize=41), rectify=True, filt=dict())


def _best_b():
    if BEST_B is not None:
        return BEST_B
    return dict(name="best", **PAGE, bin=dict(NORM_OTSU), rectify=True, filt=dict(max_thickness=18, min_area=12))


def stageC():
    """이진화 세부 파라미터 스윕."""
    b = _best_b()
    return (sweep(b, "bin.bg_ksize", [21, 31, 41, 61, 81], "C1")
            + sweep(b, "bin.floor", [0.06, 0.09, 0.12, 0.15, 0.18], "C2")
            + sweep(b, "filt.max_thickness", [12, 15, 18, 24, 32], "C3")
            + sweep(b, "filt.min_area", [0, 6, 12, 24, 40], "C4"))


def stageD():
    """획 추출 파라미터 스윕: 스무딩(LSH vs Jihun), 확대, 병합 각도, 최소 길이."""
    b = _best_b()
    return (sweep(b, "smooth", [0, 3, 5, 7, 9], "D1")
            + sweep(b, "page_scale", [1.0, 1.5], "D2")
            + sweep(b, "page_max", [1600, 2000, 2600], "D3")
            + sweep(b, "merge_angle", [30, 45, 60, 90], "D4")
            + sweep(b, "min_len", [2, 3, 5, 8], "D5")
            + sweep(b, "min_extent", [0, 3, 6, 10], "D6"))


def stageE():
    """획 단순화(RDP): 점 수 = 필기 시간. 모양을 지키면서 얼마나 줄일 수 있나."""
    b = _best_b()
    out = []
    for eps in [0.0, 0.5, 0.8, 1.2, 1.6, 2.2]:
        for mseg in [0.0, 12.0]:
            v = dict(b, rdp=eps, max_seg=mseg)
            v["name"] = f"E_rdp={eps}_seg={mseg}"
            out.append(v)
    return out


def stageF():
    """획 잇기(chain): 끝점 간격 g 이하인 획을 잇는다. 획 수 = 펜 들기 횟수 = 시간."""
    b = _best_b()
    return sweep(b, "chain", [0, 1.5, 2.5, 4.0, 6.0, 9.0], "F")


def stageG():
    """크기 정규화. 임계값이 절대 픽셀이라 사진 해상도에 따라 결과가 달라지는 문제.

    sw_target : 획 두께를 재서 그 두께가 되도록 작업 해상도를 바꾼다
    page_min  : 긴 변이 이보다 작으면 키워서 처리한다 (아이패드 527px 사진 같은 경우)
    작F1(0.3배 축소 합성)과 마sup/마cov(실제 527x249 업로드)를 같이 본다.
    """
    b = _best_b()
    out = [dict(b, name="G0_none")]
    for sw in [3.0, 4.0, 5.0, 6.5]:
        out.append(dict(b, name=f"G1_sw={sw}", sw_target=sw))
    for pmin in [1000, 1400, 2000]:
        out.append(dict(b, name=f"G2_pmin={pmin}", page_min=pmin))
    return out
