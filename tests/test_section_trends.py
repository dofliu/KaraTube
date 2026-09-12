"""跨場次段落趨勢：一次不算趨勢、要跟自己比、曲式變了不混算。"""
from backend.services.section_trends import (
    MIN_PERFORMANCES,
    MIN_TREND_MARGIN,
    compute_trend,
    signature_of,
)


def perf(**sections):
    """一場演唱：`perf(主歌1=0.7, 副歌1=0.5)`（dict 保序，就是演唱順序）。"""
    return {"sections": [{"label": k, "accuracy": v} for k, v in sections.items()]}


def labels(rows):
    return [r["label"] for r in rows]


# --- 資料不足時不下結論 ---

def test_no_section_data_is_status_none():
    t = compute_trend([{"score": 100}, {"score": 200, "sections": "壞資料"}])
    assert t["status"] == "none"
    assert t["performances"] == 0
    assert t["home"] is None and t["weak"] is None


def test_two_performances_is_insufficient_with_countdown():
    shots = [perf(主歌1=0.8, 副歌1=0.4)] * 2
    t = compute_trend(shots)
    assert t["status"] == "insufficient"
    assert t["performances"] == 2
    # 「還差幾場」要講得出來，前端才寫得出「再唱一次就能看出你的弱點」
    assert t["needed"] == MIN_PERFORMANCES - 2


def test_single_section_performances_never_count():
    # 只有一段可比的場次不帶任何形狀資訊（減掉自己的平均恆等於 0）
    t = compute_trend([perf(副歌1=0.9)] * 5)
    assert t["status"] == "none"


def test_three_performances_is_enough():
    t = compute_trend([perf(主歌1=0.8, 副歌1=0.4)] * 3)
    assert t["status"] == "ok"
    assert t["performances"] == 3


# --- 核心：比的是形狀，不是高度 ---

def test_bad_voice_day_does_not_make_every_section_a_weakness():
    # 第三場整體掉 30 個百分點（感冒了），但形狀完全一樣：副歌一向比主歌低。
    shots = [
        perf(主歌1=0.80, 副歌1=0.50),
        perf(主歌1=0.78, 副歌1=0.48),
        perf(主歌1=0.50, 副歌1=0.20),
    ]
    t = compute_trend(shots)
    assert t["home"]["label"] == "主歌1"
    assert t["weak"]["label"] == "副歌1"
    # 偏差是「與那一場自己的平均」的差，整體平移不影響
    assert t["weak"]["mean_delta"] == -0.15


def test_uniform_singer_gets_no_finger_pointing():
    # 每一段都差不多，就不該點名任何段落（跟單場段落評分同一個原則）
    t = compute_trend([perf(主歌1=0.61, 副歌1=0.60, 主歌2=0.59)] * 4)
    assert t["status"] == "ok"
    assert t["home"] is None and t["weak"] is None


def test_margin_gate_is_the_documented_threshold():
    # 剛好差到門檻（±0.05）才點名：主歌 +0.05 / 副歌 -0.05
    t = compute_trend([perf(主歌1=0.65, 副歌1=0.55)] * 3)
    assert abs(t["home"]["mean_delta"]) == MIN_TREND_MARGIN
    assert t["home"]["label"] == "主歌1"

    # 差一點點就不點名
    t2 = compute_trend([perf(主歌1=0.62, 副歌1=0.56)] * 3)
    assert t2["home"] is None and t2["weak"] is None


def test_one_disaster_does_not_become_a_trend():
    # 兩場正常、一場副歌崩掉：平均偏差夠大，但方向只有一場同意 → 不點名。
    # （平均值分不出「三次都掉一點」與「一次掉很多」，但該講的話完全不同）
    shots = [
        perf(主歌1=0.60, 副歌1=0.62),
        perf(主歌1=0.60, 副歌1=0.61),
        perf(主歌1=0.60, 副歌1=0.05),
    ]
    t = compute_trend(shots)
    assert t["status"] == "ok"
    assert t["weak"] is None
    weak_row = next(r for r in t["sections"] if r["label"] == "副歌1")
    assert weak_row["named"] is False
    assert weak_row["consistency"] < 0.67


def test_consistent_two_of_three_is_enough():
    shots = [
        perf(主歌1=0.70, 副歌1=0.50),
        perf(主歌1=0.70, 副歌1=0.52),
        perf(主歌1=0.60, 副歌1=0.62),  # 這一場反過來
    ]
    t = compute_trend(shots)
    assert t["weak"]["label"] == "副歌1"
    assert t["weak"]["consistency"] >= 2 / 3


def test_single_nameable_section_is_not_both_home_and_weak():
    # 只有一段夠格時，它只能站一邊 —— 「主場就是待加強」是自相矛盾的一句話
    shots = [perf(主歌1=0.75, 副歌1=0.55, 主歌2=0.55)] * 3
    t = compute_trend(shots)
    assert t["home"]["label"] == "主歌1"
    assert t["weak"] is None or t["weak"]["label"] != "主歌1"


# --- 曲式變了就重新累積 ---

def test_layout_change_excludes_old_performances():
    old = [perf(主歌1=0.8, 副歌1=0.4)] * 4          # 舊曲式：兩段
    new = [perf(主歌1=0.4, 副歌1=0.8, 主歌2=0.6)] * 2  # 重新處理後：三段
    t = compute_trend(old + new)
    # 最新一場的曲式是三段，只有兩場可比 → 還不能下結論
    assert t["status"] == "insufficient"
    assert t["skipped_layout_changed"] == 4


def test_layout_change_accumulates_from_the_new_layout():
    old = [perf(主歌1=0.8, 副歌1=0.4)] * 5
    new = [perf(主歌1=0.4, 副歌1=0.8, 主歌2=0.6)] * 3
    t = compute_trend(old + new)
    assert t["status"] == "ok"
    assert t["performances"] == 3
    # 新曲式說副歌才是主場，舊曲式的四場不准把它拉回去
    assert t["home"]["label"] == "副歌1"
    assert labels(t["sections"]) == ["主歌1", "副歌1", "主歌2"]


def test_signature_is_order_sensitive():
    assert signature_of([{"label": "A"}, {"label": "B"}]) != \
        signature_of([{"label": "B"}, {"label": "A"}])


# --- 呈現細節 ---

def test_sections_are_in_song_order_not_ranked():
    # 長條圖要照歌的順序讀，排名是另一件事
    t = compute_trend([perf(主歌1=0.5, 副歌1=0.9, 主歌2=0.3)] * 3)
    assert labels(t["sections"]) == ["主歌1", "副歌1", "主歌2"]


def test_only_recent_performances_count():
    from backend.services.section_trends import MAX_TRACKED_PERFORMANCES
    ancient = [perf(主歌1=0.9, 副歌1=0.1)] * 30
    lately = [perf(主歌1=0.1, 副歌1=0.9)] * MAX_TRACKED_PERFORMANCES
    t = compute_trend(ancient + lately)
    assert t["performances"] == MAX_TRACKED_PERFORMANCES
    # 半年前的唱法不能拿來說「一向」
    assert t["home"]["label"] == "副歌1"


def test_malformed_rows_are_skipped_not_zero_filled():
    shots = [{"sections": [
        {"label": "主歌1", "accuracy": 0.8},
        {"label": "副歌1", "accuracy": "壞掉"},   # 不是數字
        {"label": "", "accuracy": 0.5},           # 沒有標籤
        {"label": "尾聲", "accuracy": 1.7},       # 超出範圍
        {"label": "主歌2", "accuracy": 0.4},
    ]}] * 3
    t = compute_trend(shots)
    # 補 0 的話「副歌1」會變成永遠的弱點，那是憑空捏造的結論
    assert labels(t["sections"]) == ["主歌1", "主歌2"]


# --- 進步／退步 ---

def test_direction_needs_four_performances():
    t = compute_trend([perf(主歌1=0.3, 副歌1=0.3),
                       perf(主歌1=0.5, 副歌1=0.5),
                       perf(主歌1=0.7, 副歌1=0.7)])
    assert t["direction"] is None


def test_direction_improving_and_slipping():
    rising = [perf(主歌1=a, 副歌1=a) for a in (0.30, 0.40, 0.50, 0.60)]
    t = compute_trend(rising)
    assert t["direction"] == "improving"
    assert t["delta_accuracy"] == 0.3

    falling = [perf(主歌1=a, 副歌1=a) for a in (0.60, 0.50, 0.40, 0.30)]
    assert compute_trend(falling)["direction"] == "slipping"


def test_direction_steady_when_flat_or_noisy():
    flat = [perf(主歌1=a, 副歌1=a) for a in (0.50, 0.52, 0.49, 0.51)]
    t = compute_trend(flat)
    assert t["direction"] == "steady"
    assert abs(t["delta_accuracy"]) < 0.05
