"""音域檢測與建議 Key（backend/services/vocal_range.py）"""
import json

import pytest

from backend.services import vocal_range as vr


def bins(spec):
    """`{midi: frames}` 直接當直方圖用（測試裡不必模擬採集過程）。"""
    return dict(spec)


def flat(low, high, frames_per_bin=500):
    """從 low 到 high 每一格一樣多幀的直方圖。"""
    return {m: frames_per_bin for m in range(low, high + 1)}


def humped(center, half_width, peak=2000):
    """中間高兩邊低的直方圖 —— 比較像真的人唱歌。"""
    out = {}
    for offset in range(-half_width, half_width + 1):
        out[center + offset] = max(60, peak - abs(offset) * (peak // (half_width + 1)))
    return out


def notes(spec):
    """`[(midi, 秒數), ...]` → pitch.json 形狀的音符串列。"""
    out = []
    t = 0.0
    for midi, seconds in spec:
        out.append({"midi": midi, "start": t, "end": t + seconds})
        t += seconds
    return out


@pytest.fixture
def store(tmp_path):
    return vr.VocalRange(tmp_path / "vocal_range.json")


# --- 音名：唯一的一份換算 ---

def test_note_name_middle_c():
    assert vr.note_name(60) == "C4"
    assert vr.note_name(69) == "A4"
    assert vr.note_name(48) == "C3"


def test_note_name_rounds_and_survives_garbage():
    assert vr.note_name(60.4) == "C4"
    assert vr.note_name(None) == ""
    assert vr.note_name("高音") == ""


# --- 決定一：兩端是「有支撐的那一格」，不是偵測到的極值 ---

def test_single_octave_error_does_not_widen_the_range():
    """整晚一兩幀的八度誤判不可以把音域拉寬 12 個半音。"""
    data = humped(60, 6)
    data[41] = 3          # 低八度誤判，三幀
    data[83] = 2          # 偶發的高音雜訊
    profile = vr.profile_from_bins(data, songs=5)
    assert profile["ready"]
    assert profile["low"] > 41, "三幀不足以把下緣拉下去"
    assert profile["high"] < 83, "兩幀不足以把上緣拉上去"


def test_a_note_sung_often_enough_does_count_as_the_edge():
    data = humped(60, 6)
    total = sum(data.values())
    data[75] = int(total * 0.05)   # 真的常常唱到的高音
    profile = vr.profile_from_bins(data, songs=5)
    assert profile["high"] == 75


def test_edge_needs_both_ratio_and_absolute_support():
    """資料很少時，比例達標但絕對幀數很少的那一格不算數。"""
    data = {60: 3000, 61: 3000, 75: vr.EDGE_MIN_FRAMES - 1}
    profile = vr.profile_from_bins(data, songs=5)
    assert profile["high"] == 61


# --- 決定二：舒適音域是最窄的那一段 ---

def test_comfort_band_is_narrower_than_the_full_range():
    data = humped(60, 8)
    profile = vr.profile_from_bins(data, songs=5)
    assert profile["comfort_low"] > profile["low"]
    assert profile["comfort_high"] < profile["high"]


def test_comfort_band_follows_the_mass_not_the_midpoint():
    """幾乎所有的幀都在低音區時，舒適區要落在低音區，不是整段的中點。"""
    data = {m: 4000 for m in range(50, 55)}
    # 上面有一長串很淡、但兩道支撐門檻都過得了的痕跡（碰得到的高音）
    data.update({m: 150 for m in range(56, 72)})
    profile = vr.profile_from_bins(data, songs=5)
    assert profile["comfort_high"] <= 56
    assert profile["high"] > 60, "淡痕跡撐得起上緣（碰得到），但那不是舒適區"


def test_comfort_band_covers_the_required_mass():
    data = humped(60, 8)
    total = sum(data.values())
    inside = sum(c for m, c in data.items()
                 if profile_band(data)[0] <= m <= profile_band(data)[1])
    assert inside >= total * vr.COMFORT_MASS


def profile_band(data):
    p = vr.profile_from_bins(data, songs=5)
    return p["comfort_low"], p["comfort_high"]


# --- 決定六：資料不夠就說不知道，而且不給半個數字 ---

def test_not_enough_frames_is_not_ready():
    profile = vr.profile_from_bins({60: 100, 61: 100}, songs=5)
    assert profile["ready"] is False
    assert profile["frames_needed"] > 0
    assert "low" not in profile, "還不知道的時候一個數字都不要給"


def test_not_enough_songs_is_not_ready_even_with_many_frames():
    """一首音域很窄的歌唱兩次，不可以變成這個人的音域。"""
    profile = vr.profile_from_bins(humped(60, 3, peak=40000), songs=2)
    assert profile["ready"] is False
    assert profile["songs_needed"] == vr.MIN_PROFILE_SONGS - 2


def test_ready_when_both_gates_are_met():
    profile = vr.profile_from_bins(humped(60, 6), songs=vr.MIN_PROFILE_SONGS)
    assert profile["ready"] is True
    assert profile["songs_needed"] == 0
    assert profile["frames_needed"] == 0
    assert profile["low_label"] and profile["high_label"]


# --- 決定三：一首歌的高音不是它最高的那個音 ---

def test_one_scream_does_not_define_the_song_high():
    """三分鐘的歌裡兩秒的飆高音，不算這首歌要求的高度。"""
    demand = vr.demand_from_notes(notes([(60, 90), (64, 60), (79, 2)]))
    assert demand["ready"]
    assert demand["high"] == 64
    assert demand["peak"] == 79, "最高音照樣看得到（畫面上要講得出來）"


def test_a_chorus_that_lives_up_there_does_count():
    demand = vr.demand_from_notes(notes([(60, 60), (72, 60)]))
    assert demand["high"] == 72


def test_duration_weighted_not_note_counted():
    """一串短裝飾音壓不過副歌那個長音。"""
    spec = [(72, 40)] + [(55, 0.15)] * 200
    demand = vr.demand_from_notes(notes(spec))
    assert demand["median"] == 72


def test_song_with_too_few_notes_has_no_demand():
    demand = vr.demand_from_notes(notes([(60, 3), (62, 4)]))
    assert demand["ready"] is False


def test_demand_ignores_garbage_notes():
    raw = notes([(60, 60), (64, 60)])
    raw += [{"midi": 200, "start": 0, "end": 5},      # 界外
            {"midi": 60, "start": 5, "end": 1},        # 負長度
            {"midi": "高", "start": 0, "end": 1},      # 不是數字
            "不是 dict"]
    demand = vr.demand_from_notes(raw)
    assert demand["ready"] and demand["high"] == 64


# --- 決定四、五：建議 Key ---

def ready_profile(low, high):
    """做一份剛好跨 low~high 的音域檔案。"""
    return vr.profile_from_bins(flat(low, high, 800), songs=vr.MIN_PROFILE_SONGS)


def test_song_in_range_needs_no_shift():
    profile = ready_profile(52, 72)
    demand = vr.demand_from_notes(notes([(58, 60), (66, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["shift"] == 0
    assert advice["status"] == "fit"


def test_song_too_high_is_advised_down():
    profile = ready_profile(48, 64)
    demand = vr.demand_from_notes(notes([(60, 60), (70, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["status"] == "advice"
    assert advice["shift"] < 0
    assert "偏高" in advice["headline"]


def test_song_too_low_is_advised_up():
    profile = ready_profile(60, 78)
    demand = vr.demand_from_notes(notes([(48, 60), (56, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["status"] == "advice"
    assert advice["shift"] > 0


def test_going_over_the_top_costs_more_than_going_under_the_bottom():
    """高低兩端各差一點的歌，答案是降 Key（唱不上去比唱得悶嚴重）。"""
    profile = ready_profile(52, 68)
    demand = vr.demand_from_notes(notes([(50, 60), (70, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["shift"] < 0


def test_shift_is_clamped_to_six():
    """剛好要降滿 6 個才進得來的歌：報 −6，不多不少。"""
    profile = ready_profile(50, 72)
    demand = vr.demand_from_notes(notes([(58, 60), (78, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["status"] == "advice"
    assert advice["shift"] == -vr.MAX_SHIFT


def test_a_song_no_key_can_reach_is_not_reported_as_a_key():
    """
    沒有任何一個 Key 進得來的歌，**不可以**報成本最低的那個移調量。

    成本函數的 3:1 權重（對一首調得進來的歌是對的）會把這種歌一路壓到 −6，
    而那個答案同時錯了兩次：高音還是上不去，低音又被一起帶到唱不出聲的地方。
    使用者會按下去、發現更難唱，然後不再相信這一行。
    """
    profile = ready_profile(40, 52)
    demand = vr.demand_from_notes(notes([(70, 60), (80, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["status"] == "out_of_range"
    assert advice["shift"] == 0, "給一個沒有用的 −6 比不給還糟"
    assert advice["gap"] == 80 - 52
    assert "假音" in advice["detail"]


def test_out_of_range_says_how_far_six_gets_you():
    """降到底能補上一部分時要說出還差多少 —— 那是他按下去會遇到的事。"""
    profile = ready_profile(48, 60)
    demand = vr.demand_from_notes(notes([(52, 60), (70, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["status"] == "out_of_range"
    assert advice["gap"] == 10
    assert advice["reachable_gap"] == 4
    assert "還差 4 個半音" in advice["detail"]


def test_a_song_that_only_needs_a_reachable_shift_is_still_advice():
    """降得進來的就照常給建議 —— 上面那條守衛不可以把正常的建議一起擋掉。"""
    profile = ready_profile(48, 64)
    demand = vr.demand_from_notes(notes([(58, 60), (68, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["status"] == "advice"
    assert -vr.MAX_SHIFT <= advice["shift"] < 0


def test_marginal_gain_is_not_worth_saying():
    """差一點點就叫人去調 Key 的系統，第三次之後就會被無視。"""
    profile = ready_profile(52, 72)
    demand = vr.demand_from_notes(notes([(59, 60), (67, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["shift"] == 0


def test_out_of_range_says_so_instead_of_recommending_the_floor():
    """降到底還是唱不上去時，講的是「這首歌太高」而不是一個沒有用的 Key。"""
    profile = ready_profile(48, 60)
    # 比音域還寬的歌：降 Key 會把低音一起帶到聽不見的地方，所以移調救不了
    demand = vr.demand_from_notes(notes([(48, 60), (70, 60)]))
    advice = vr.advise(profile, demand)
    assert advice["status"] == "out_of_range"
    assert advice["shift"] == 0
    assert "假音" in advice["detail"]


def test_no_profile_says_how_many_songs_are_left():
    profile = vr.profile_from_bins({60: 100}, songs=1)
    advice = vr.advise(profile, vr.demand_from_notes(notes([(60, 60), (64, 60)])))
    assert advice["status"] == "no_profile"
    assert advice["shift"] == 0
    assert "再唱" in advice["detail"]


def test_no_demand_is_silent_not_wrong():
    profile = ready_profile(52, 72)
    advice = vr.advise(profile, vr.demand_from_notes([]))
    assert advice["status"] == "no_demand"
    assert advice["shift"] == 0


# --- 決定七、八：檔案 ---

def test_submit_merges_and_counts_songs(store):
    store.submit("小明", {60: 2000, 61: 2000})
    result = store.submit("小明", {62: 2000, 63: 2000})
    assert result["recorded"] is True
    assert result["songs"] == 2
    assert result["frames"] == 8000


def test_anonymous_is_refused_with_a_reason(store):
    result = store.submit("   ", {60: 5000})
    assert result["recorded"] is False
    assert result["reason"] == "anonymous"
    assert result["message"]


def test_empty_histogram_is_refused(store):
    assert store.submit("小明", {})["recorded"] is False
    assert store.submit("小明", {"不是數字": "也不是數字"})["recorded"] is False


def test_out_of_range_bins_are_dropped_not_clamped(store):
    """界外的格子夾回邊界的話，邊界那一格會堆出一座假的山。"""
    store.submit("小明", {20: 9999, 60: 3000, 200: 9999})
    profile = store.profile("小明")
    assert profile["frames"] == 3000


def test_nickname_is_normalized(store):
    store.submit(" 小明 ", {60: 3000})
    store.submit("小明", {61: 3000})
    assert store.profile("小明")["songs"] == 2


def test_case_differences_are_the_same_person(store):
    store.submit("Amy", {60: 3000})
    store.submit("amy", {61: 3000})
    assert store.profile("AMY")["songs"] == 2


def test_display_name_follows_the_latest_spelling(store):
    store.submit("amy", {60: 3000})
    store.submit("Amy", {61: 3000})
    assert store.profile("amy")["singer"] == "Amy"


def test_unknown_singer_gets_an_empty_profile_not_an_error(store):
    profile = store.profile("沒唱過的人")
    assert profile["ready"] is False
    assert profile["frames"] == 0


def test_decay_caps_the_file_but_keeps_the_shape(store):
    for _ in range(40):
        store.submit("小明", {60: 20000, 67: 10000})
    profile = store.profile("小明")
    assert profile["frames"] <= vr.MAX_PROFILE_FRAMES
    # 形狀（60 是 67 的兩倍）要留著
    raw = store._singers[vr.VocalRange.normalize("小明")]["bins"]
    assert raw[60] == pytest.approx(raw[67] * 2, rel=0.01)


def test_reset_clears_one_singer_only(store):
    store.submit("小明", {60: 3000})
    store.submit("小美", {70: 3000})
    assert store.reset("小明") is True
    assert store.profile("小明")["frames"] == 0
    assert store.profile("小美")["frames"] == 3000
    assert store.reset("沒有這個人") is False


def test_persists_across_restart(tmp_path):
    path = tmp_path / "vocal_range.json"
    first = vr.VocalRange(path)
    for _ in range(vr.MIN_PROFILE_SONGS):
        first.submit("小明", humped(60, 6))
    reopened = vr.VocalRange(path)
    profile = reopened.profile("小明")
    assert profile["ready"] is True
    assert profile["songs"] == vr.MIN_PROFILE_SONGS


def test_broken_file_does_not_block_startup(tmp_path):
    path = tmp_path / "vocal_range.json"
    path.write_text("{ 這不是 JSON", encoding="utf-8")
    store = vr.VocalRange(path)
    assert store.list_singers() == []
    assert store.submit("小明", {60: 3000})["recorded"] is True


def test_json_keys_survive_the_round_trip(tmp_path):
    """JSON 的鍵一律是字串，讀回來沒轉成 int 的話整份直方圖會靜靜地空掉。"""
    path = tmp_path / "vocal_range.json"
    store = vr.VocalRange(path)
    store.submit("小明", {60: 3000})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "60" in saved["singers"][vr.VocalRange.normalize("小明")]["bins"]
    assert vr.VocalRange(path).profile("小明")["frames"] == 3000


def test_advice_through_the_store(store):
    for _ in range(vr.MIN_PROFILE_SONGS):
        store.submit("小明", flat(48, 64, 800))
    result = store.advice("小明", notes([(60, 60), (70, 60)]))
    assert result["status"] == "advice"
    assert result["shift"] < 0
    assert result["profile"]["ready"] is True
    assert result["demand"]["ready"] is True


def test_list_singers_puts_the_ready_ones_first(store):
    store.submit("剛開始", {60: 200})
    for _ in range(vr.MIN_PROFILE_SONGS):
        store.submit("唱很久", humped(60, 6))
    listing = store.list_singers()
    assert [s["singer"] for s in listing][0] == "唱很久"
    assert listing[0]["ready"] is True


# --- 兩邊的常數要釘在一起 ---

def test_frontend_and_backend_agree_on_the_midi_window():
    """前端送出去的兩端被後端安靜丟掉的話，畫面上的音域比檔案裡的寬。"""
    js = (vr.__file__.rsplit("backend", 1)[0] + "frontend/js/vocal-range.js")
    with open(js, encoding="utf-8") as f:
        source = f.read()
    assert f"const RANGE_MIN_MIDI = {vr.MIN_MIDI};" in source
    assert f"const RANGE_MAX_MIDI = {vr.MAX_MIDI};" in source
