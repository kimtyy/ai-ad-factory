"""
core/render/commerce_layout.py - 쇼핑 숏폼 전용 화면 배치 ('상단 자막 + 중앙~하단 상품 카드')

상품 사진은 대부분 상품이 가운데에 있어서, 숙소용 정중앙(50%) 자막이 상품 정면을 가림.
→ 화면을 세로로 나눔 (높이 비율):
    0.00~0.07  쇼츠 상단 UI (비움)
    ~0.085     쿠팡 파트너스 제휴 표시 (compositor.DISCLOSURE_Y)
    ~0.215     자막·후킹 (COMMERCE_CAPTION_CENTER_Y)
    0.31~0.71  상품 카드: 원본 사진을 잘리지 않게 담아 선명하게 + 카드 안에서 Ken Burns
    0.71~      쇼츠 하단 UI(채널명·제목·구독·사운드명) 영역 (비움)
  배경: 같은 사진을 크게 흐리고 어둡게 깔아 빈 곳을 채움 (자막 가독성 + 상품 강조)
"""

# 상품 카드가 들어갈 수 있는 최대 영역 (x0, y0, x1, y1, 화면 비율). 사진 비율대로 이 안에 '잘림 없이' 맞춤
COMMERCE_CARD_BOX = (0.12, 0.31, 0.88, 0.71)   # 좌우 12%: 가로·정사각 사진도 우측 좋아요·댓글 버튼 열(약 86%~)과 거의 안 겹침
COMMERCE_CAPTION_CENTER_Y = 0.215
COMMERCE_CARD_RADIUS = 0.035           # 카드 모서리 둥글기 (화면 폭 비율)
COMMERCE_CARD_ZOOM = 1.10              # 카드 안 Ken Burns 줌 폭 (1.2면 상품 가장자리가 잘림)
COMMERCE_BG_ZOOM = 1.05                # 흐린 배경도 천천히 확대 (정지 화면 느낌 방지)
COMMERCE_BG_BLUR = 38                  # 배경 블러 반경 (px, 1080 폭 기준)
COMMERCE_BG_DIM = 0.62                 # 배경 밝기 배율 (자막·카드 대비)

# 시골책방: 책 표지 카드는 2:3 고정 비율 (사진을 가운데 기준으로 2:3에 맞춰 채움). 1080x1920 기준 540x806
BOOK_CARD_BOX = (0.263, 0.34, 0.737, 0.74)   # 512x768 (2:3), 위쪽에 명조체 자막 3줄 자리
BOOK_QUOTE_BOX = (0.32, 0.245, 0.68, 0.549)   # 인용 씬: 카드를 위로 줄여 아래에 종이 인용 카드 자리 (389x583)
BOOK_CARD_ZOOM = 1.05                          # 차분한 호흡: 카드 안 줌 폭을 작게

# ---------------------------------------------------------------- 대표 사진 1장 → 다각도 연출 (Smart Crop)
# 같은 사진이 여러 씬에 쓰이면 등장 순서대로 앵글을 바꿔 잘라냄: 전체 → 중앙 줌인 → 상단 디테일 → 하단 디테일
SHOT_SEQUENCE = ("full", "center", "top", "bottom")
SHOT_LABELS = {"full": "전체 풀샷", "center": "중앙 줌인", "top": "상단 디테일", "bottom": "하단 디테일"}
SHOT_MIN_SIDE = 320          # 잘린 앵글의 최소 변 길이 (이보다 작으면 중심 기준으로 넓혀 카드에서 뭉개지지 않게)


def product_bbox(img):
    """
    상품 영역 (x0, y0, x1, y1): 사진 가장자리 색(배경)과 확실히 다른 픽셀의 범위.
    쇼핑몰 대표 사진은 대부분 단색 배경이라 잘 잡힘. 배경이 복잡해 판별이 안 되면 사진 전체
    """
    import numpy as np
    small = img.convert("RGB").copy()
    small.thumbnail((256, 256))
    a = np.asarray(small).astype(np.int16)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(border, axis=0)
    mask = np.abs(a - bg).sum(axis=2) > 40
    if mask.mean() < 0.02 or mask.mean() > 0.92:
        return 0, 0, img.width, img.height
    ys, xs = np.where(mask)
    sx, sy = img.width / a.shape[1], img.height / a.shape[0]
    y0, y1 = np.percentile(ys, [1, 99])
    x0, x1 = np.percentile(xs, [1, 99])
    return int(x0 * sx), int(y0 * sy), int((x1 + 1) * sx), int((y1 + 1) * sy)


SHOT_MAX_ASPECT = 1.3        # 상단·하단 디테일이 가로로 긴 띠가 되지 않게 (카드가 작아짐)


def shot_crop(img, shot: str = "full", box=None):
    """
    앵글별로 상품 영역을 잘라낸 이미지.
    box: 상품 본체 위치 (x0, y0, x1, y1, 0~1 비율, Gemini 판정). 없으면 배경색과 다른 영역(product_bbox)으로 추정
    (광고 문구가 박힌 대표 사진은 배경 판별로는 사진 전체가 잡혀 글씨를 자르게 되므로 box가 있으면 우선)
    """
    if box:
        x0, y0, x1, y1 = int(box[0] * img.width), int(box[1] * img.height), int(box[2] * img.width), int(box[3] * img.height)
    else:
        x0, y0, x1, y1 = product_bbox(img)
    bw, bh = x1 - x0, y1 - y0
    if shot == "center":
        cw, ch = bw * 0.62, bh * 0.62
        box = (x0 + (bw - cw) / 2, y0 + (bh - ch) / 2, x0 + (bw + cw) / 2, y0 + (bh + ch) / 2)
    elif shot in ("top", "bottom"):
        h = bh * 0.55
        w = min(bw, h * SHOT_MAX_ASPECT)
        cx = (x0 + x1) / 2
        box = (cx - w / 2, y0, cx + w / 2, y0 + h) if shot == "top" else (cx - w / 2, y1 - h, cx + w / 2, y1)
    else:                                     # full: 상품 둘레에 여백 8%
        mx, my = bw * 0.08, bh * 0.08
        box = (x0 - mx, y0 - my, x1 + mx, y1 + my)
    # 너무 가늘거나 작은 쪽은 중심을 유지한 채 SHOT_MIN_SIDE까지 넓힘 (좁은 상품도 줌인 효과 유지, 뭉개짐 방지)
    l, t, r, btm = box
    for lo, hi, limit in ((0, 2, img.width), (1, 3, img.height)):
        size = (r - l) if lo == 0 else (btm - t)
        need = min(SHOT_MIN_SIDE, limit)
        if size < need:
            c = ((l + r) if lo == 0 else (t + btm)) / 2
            a0 = min(max(0, c - need / 2), limit - need)
            if lo == 0:
                l, r = a0, a0 + need
            else:
                t, btm = a0, a0 + need
    box = (max(0, int(l)), max(0, int(t)), min(img.width, int(r)), min(img.height, int(btm)))
    return img.crop(box)


def assign_shots(paths):
    """씬별 미디어 경로 목록 → 같은 사진의 n번째 등장에 SHOT_SEQUENCE[n] (사진이 여러 장이면 각자 전체 샷부터)"""
    seen, shots = {}, []
    for p in paths:
        n = seen.get(p, 0)
        shots.append(SHOT_SEQUENCE[n % len(SHOT_SEQUENCE)])
        seen[p] = n + 1
    return shots
