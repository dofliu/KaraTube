"""整晚打包下載的單元測試。

這支功能會把一整場的錄音串成一個 zip 送出去，測試的重點不是「包得出來」，
而是那幾件出錯了才會被發現、而且是在**別人的電腦上**才會被發現的事：

  * 場次切在哪裡 —— 切錯的話「今晚的」會少掉凌晨那幾首（唱最嗨的那幾首）；
  * zip 裡的檔名 —— 歌名裡的斜線與冒號在 Windows 上是存不下去的；
  * 打包途中檔案不見了 —— 要少一首加一行說明，不是整包下載到一半斷掉；
  * 曲目清單講的是不是實話 —— 它是使用者唯一能對帳的東西。
"""
import io
import zipfile
from datetime import datetime, timedelta

import pytest

from backend.services.night_export import (
    DEFAULT_GAP_HOURS,
    MANIFEST_NAME,
    ExportGate,
    filter_by_singer,
    find_session,
    group_sessions,
    iter_session_zip,
    manifest_text,
    member_name,
    safe_stem,
    session_key,
    truncate_bytes,
    zip_filename,
)


def entry(when: str, title="月亮代表我的心", singer="阿明", file="", **extra):
    e = {
        "id": f"{when[:10].replace('-', '')}-000000-abc123",
        "file": file or f"{when.replace(':', '').replace('-', '')}.webm",
        "created_at": when,
        "title": title,
        "singer": singer,
        "song_id": "vid123",
        "bytes": 1024,
        "duration_ms": 204_000,
        "score": 88_000,
        "grade": "A",
        "mode": "solo",
    }
    e.update(extra)
    return e


# --- 場次切割 ---

def test_a_night_that_crosses_midnight_is_one_session():
    """
    這是整支功能最重要的一條：包廂的一場是「九點唱到凌晨兩點半」。
    照日曆日期切的話這一場會被剖成兩半，而且後半（唱最嗨的那一段）
    會被標成「隔天」—— 使用者按「今晚的」拿到的是半場。
    """
    entries = [entry("2026-09-13T21:05:00"), entry("2026-09-13T23:50:00"),
               entry("2026-09-14T00:20:00"), entry("2026-09-14T02:30:00")]
    sessions = group_sessions(entries, gap_hours=DEFAULT_GAP_HOURS)
    assert len(sessions) == 1
    assert sessions[0]["count"] == 4
    assert sessions[0]["started_at"] == "2026-09-13T21:05:00"
    assert sessions[0]["ended_at"] == "2026-09-14T02:30:00"


def test_two_parties_on_the_same_day_are_two_sessions():
    """反過來也要對：下午一輪、晚上一輪是兩桌不同的客人，日期法分不出來。"""
    entries = [entry("2026-09-13T14:00:00"), entry("2026-09-13T15:10:00"),
               entry("2026-09-13T21:30:00"), entry("2026-09-13T22:40:00")]
    sessions = group_sessions(entries, gap_hours=6)
    assert [s["count"] for s in sessions] == [2, 2]
    # 最近的一場排最前面（畫面上第一顆按鈕就是「今晚的」）
    assert sessions[0]["started_at"] == "2026-09-13T21:30:00"


def test_gap_exactly_at_the_threshold_stays_in_the_same_session():
    """門檻用的是「超過」而不是「大於等於」：剛好 6 小時還算同一場。"""
    entries = [entry("2026-09-13T20:00:00"), entry("2026-09-14T02:00:00")]
    assert len(group_sessions(entries, gap_hours=6)) == 1
    entries.append(entry("2026-09-14T08:00:01"))
    assert len(group_sessions(entries, gap_hours=6)) == 2


def test_broken_timestamp_does_not_split_a_session():
    """
    時間戳壞掉（手改過索引、機器沒對時）的那一筆不該製造斷點：
    切下去只會多出一場只有一首的鬼場次，而使用者在畫面上看到的是
    「今晚」突然變成三場。
    """
    entries = [entry("2026-09-13T21:00:00"), entry("not-a-time"),
               entry("2026-09-13T21:30:00")]
    sessions = group_sessions(entries, gap_hours=6)
    assert len(sessions) == 1 and sessions[0]["count"] == 3


def test_empty_library_has_no_sessions():
    assert group_sessions([], gap_hours=6) == []


def test_session_summary_counts_singers_and_songs():
    entries = [entry("2026-09-13T21:00:00", singer="阿明", song_id="a"),
               entry("2026-09-13T21:20:00", singer="小美", song_id="b"),
               entry("2026-09-13T21:40:00", singer="阿明", song_id="a", pinned=True)]
    s = group_sessions(entries, gap_hours=6)[0]
    assert s["singers"] == ["阿明", "小美"]     # 出場順序，不是字典順序
    assert s["songs"] == 2                      # 同一首唱兩次算一首歌
    assert s["count"] == 3
    assert s["pinned_count"] == 1
    assert s["bytes"] == 3 * 1024


def test_session_key_is_the_start_time_not_an_index():
    """
    key 用開始時間而不是流水號：流水號會因為前面的場次被配額清掉而整批位移，
    使用者剛剛複製的那個網址隔天就指到別場去了。
    """
    assert session_key("2026-09-13T21:05:30") == "20260913-2105"
    # 時間戳壞掉時退回錄音 id（已經被正則釘死的形狀），不會是空字串
    assert session_key("", "20260913-210530-abc123") == "20260913-210530-abc123"
    assert session_key("", "../../etc/passwd") == "etcpasswd"


def test_find_and_filter_by_singer():
    entries = [entry("2026-09-13T21:00:00", singer="阿明"),
               entry("2026-09-13T21:20:00", singer="小美"),
               entry("2026-09-13T21:40:00", singer="阿明")]
    sessions = group_sessions(entries, gap_hours=6)
    found = find_session(sessions, sessions[0]["key"])
    assert found is not None
    assert find_session(sessions, "20991231-2359") is None
    # 「我的那幾首」：一桌八個人，不是每個人都想要另外七個人的版本
    assert len(filter_by_singer(found, "阿明")) == 2
    assert len(filter_by_singer(found, "")) == 3
    assert filter_by_singer(found, "沒有這個人") == []


# --- 檔名 ---

def test_illegal_characters_are_replaced():
    """歌名裡的斜線與冒號很常見（「A/B」「Part 2: …」），Windows 存不下去。"""
    assert safe_stem('A/B: "C" <D>|E') == "A_B_ _C_ _D__E"
    assert safe_stem("換行\n在裡面") == "換行_在裡面"


def test_trailing_dots_and_spaces_are_dropped():
    """Windows 會自己吃掉結尾的點與空白，曲目清單寫的檔名就對不上了。"""
    assert safe_stem("歌名...  ") == "歌名"
    assert safe_stem("   ") == ""


def test_truncation_counts_utf8_bytes_and_never_splits_a_character():
    # 中文一個字 3 bytes：照字數裁會裁出一個超過檔名上限的名字
    assert truncate_bytes("一二三四五", 7) == "一二"
    assert len(truncate_bytes("一二三四五", 7).encode("utf-8")) <= 7
    assert truncate_bytes("abc", 10) == "abc"


def test_member_name_starts_with_an_order_prefix():
    """
    序號與時間在最前面：車機、隨身碟播放器、檔案總管都是照檔名排序的，
    沒有序號的話「今晚唱的順序」在解開之後就找不回來了。
    """
    name = member_name(entry("2026-09-13T21:05:00", title="月亮代表我的心"), 1)
    assert name == "01 21-05 月亮代表我的心 - 阿明.webm"
    # 冒號在 Windows 是非法字元，所以時間寫成 21-05
    assert ":" not in name


def test_member_names_do_not_collide_case_insensitively():
    """
    同一場裡兩首同名的歌（同一首唱兩次很常見）不能互相覆蓋 ——
    覆蓋掉的是一次真的演唱。Windows 與 macOS 的檔案系統預設不分大小寫，
    所以比對也不能分。
    """
    used = set()
    a = member_name(entry("2026-09-13T21:05:00", title="Song"), 1, used)
    b = member_name(entry("2026-09-13T21:05:00", title="SONG"), 1, used)
    assert a != b and b.endswith("(2).webm")


def test_member_name_keeps_the_original_container_suffix():
    """Safari 錄出來的是 .m4a，統一叫 .webm 的話那些檔案在別的地方打不開。"""
    assert member_name(entry("2026-09-13T21:05:00", file="x.m4a"), 3).endswith(".m4a")
    # 索引裡沒有檔名時退回 .webm，不要生出一個沒有副檔名的檔案
    assert member_name(entry("2026-09-13T21:05:00", file=" "), 3).endswith(".webm")


def test_zip_filename_says_which_night_and_how_many():
    session = group_sessions([entry("2026-09-13T21:05:00")], gap_hours=6)[0]
    assert zip_filename(session) == "KaraTube 20260913 1首.zip"
    assert zip_filename(session, "阿明") == "KaraTube 20260913 阿明 1首.zip"


# --- 打包 ---

@pytest.fixture()
def night(tmp_path):
    """磁碟上三個真的檔案 + 一場的摘要。"""
    entries = []
    for i, (when, title, singer) in enumerate([
        ("2026-09-13T21:05:00", "月亮/代表:我的心", "阿明"),
        ("2026-09-13T23:40:00", "海闊天空", "小美"),
        ("2026-09-14T01:10:00", "月亮/代表:我的心", "阿明"),
    ]):
        name = f"take{i}.webm"
        (tmp_path / name).write_bytes(f"audio-{i}".encode() * 1000)
        entries.append(entry(when, title=title, singer=singer, file=name,
                             bytes=(tmp_path / name).stat().st_size, song_id=f"song{i}"))
    session = group_sessions(entries, gap_hours=6)[0]
    return tmp_path, session


def build_zip(base_dir, entries, session, singer=""):
    data = b"".join(iter_session_zip(base_dir, entries, session, singer))
    return data, zipfile.ZipFile(io.BytesIO(data))


def test_zip_contains_every_take_and_a_manifest(night):
    base, session = night
    data, zf = build_zip(base, session["entries"], session)
    assert zf.testzip() is None            # 整包的 CRC 都對得上
    names = zf.namelist()
    assert len(names) == 4 and names[-1] == MANIFEST_NAME
    assert names[0].startswith("01 21-05 ")
    assert zf.read(names[0]) == (base / "take0.webm").read_bytes()


def test_audio_is_stored_not_deflated(night):
    """
    錄音是 Opus/AAC —— 已經壓縮過了。再 deflate 一次省不到 1%，
    換來的是把一整晚重壓一遍的 CPU，而那顆 CPU 正在放歌、算音準。
    清單是純文字，那一份才值得壓。
    """
    base, session = night
    _, zf = build_zip(base, session["entries"], session)
    for info in zf.infolist():
        if info.filename == MANIFEST_NAME:
            assert info.compress_type == zipfile.ZIP_DEFLATED
        else:
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.compress_size == info.file_size


def test_zip_is_streamed_in_chunks_not_built_in_memory(night):
    """
    邊包邊送：generator 要吐出**多塊**，而不是最後一次給一大包。
    一次給完等於整場都住在記憶體裡，而一場可能是好幾百 MB。
    """
    base, session = night
    chunks = list(iter_session_zip(base, session["entries"], session, chunk_bytes=1024))
    assert len(chunks) > 3
    assert zipfile.ZipFile(io.BytesIO(b"".join(chunks))).testzip() is None


def test_missing_file_is_skipped_and_named_in_the_manifest(night):
    """
    打包的當下某一首被配額擠掉（或有人手動刪了檔）：少一首加一行說明，
    而不是整包下載到一半斷掉 —— 後者使用者連哪幾首錄到了都不知道。
    """
    base, session = night
    (base / "take1.webm").unlink()
    _, zf = build_zip(base, session["entries"], session)
    names = zf.namelist()
    assert len(names) == 3                       # 兩首 + 清單
    assert not any("海闊天空" in n for n in names)
    manifest = zf.read(MANIFEST_NAME).decode("utf-8")
    assert "沒有包進來" in manifest and "海闊天空" in manifest


def test_manifest_lists_what_actually_got_in(night):
    """清單是使用者唯一能對帳的東西，寫的檔名要跟 zip 裡的一模一樣。"""
    base, session = night
    _, zf = build_zip(base, session["entries"], session)
    manifest = zf.read(MANIFEST_NAME).decode("utf-8")
    for name in zf.namelist():
        if name != MANIFEST_NAME:
            assert name in manifest
    assert "88,000 分 A" in manifest
    assert "阿明" in manifest and "小美" in manifest


def test_singer_filter_packs_only_that_persons_takes(night):
    base, session = night
    entries = filter_by_singer(session, "阿明")
    _, zf = build_zip(base, entries, {**session, "count": len(entries)}, "阿明")
    assert len([n for n in zf.namelist() if n != MANIFEST_NAME]) == 2
    assert "只有 阿明 唱的" in zf.read(MANIFEST_NAME).decode("utf-8")


def test_filenames_survive_the_round_trip_as_utf8(night):
    """中文檔名要帶著 UTF-8 旗標（bit 11），不然在別台機器上解開會是亂碼。"""
    base, session = night
    _, zf = build_zip(base, session["entries"], session)
    for info in zf.infolist():
        assert info.flag_bits & 0x800, f"{info.filename} 沒有標成 UTF-8"


def test_prehistoric_timestamp_does_not_blow_up_the_zip():
    """
    zip 的時間欄位是 1980 年起算的，傳更早的時間進去 zipfile 會丟例外。
    機器沒對時（開機回到 1970）的那一台不該連打包都做不了。
    """
    from pathlib import Path as _P
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        base = _P(tmp)
        (base / "old.webm").write_bytes(b"x" * 10)
        e = entry("1970-01-01T00:00:00", file="old.webm")
        session = group_sessions([e], gap_hours=6)[0]
        _, zf = build_zip(base, [e], session)
        assert zf.infolist()[0].date_time[0] == 1980


def test_manifest_is_the_last_entry(night):
    """
    清單刻意放最後：它是打包完才生出來的，所以講得出「哪幾首沒包成」。
    先寫的話它會說謊（寫了一首資料夾裡沒有的歌）。
    """
    base, session = night
    _, zf = build_zip(base, session["entries"], session)
    assert zf.namelist()[-1] == MANIFEST_NAME


def test_manifest_text_handles_an_empty_night():
    session = {"started_at": "", "ended_at": "", "count": 0}
    text = manifest_text(session, [], [])
    assert "KaraTube 整晚打包" in text and "0 首" in text


# --- 同時只打一包 ---

def test_only_one_export_at_a_time():
    gate = ExportGate(lease_seconds=60)
    first = gate.acquire()
    assert first and gate.busy
    assert gate.acquire() == 0          # 第二個人要被擋下來
    gate.release(first)
    assert not gate.busy
    assert gate.acquire() != 0


def test_a_stale_lease_can_be_taken_over():
    """
    使用者按了下載又立刻關掉分頁時，串流的 generator 有可能從來沒被跑過 ——
    沒有任何程式碼會執行到 finally，那個位子就永遠空不出來。
    所以租約有期限，過期之後下一個人直接接手。
    """
    gate = ExportGate(lease_seconds=0.0)
    first = gate.acquire()
    second = gate.acquire()
    assert second and second != first
    # 過期的那一張票不能把新的人踢掉
    gate.release(first)
    assert gate._holder == second


def test_release_with_a_wrong_token_does_nothing():
    gate = ExportGate(lease_seconds=60)
    token = gate.acquire()
    gate.release(token + 999)
    assert gate.busy
    gate.release(token)
    assert not gate.busy


def test_sessions_keep_their_order_over_a_long_history():
    """久一點的資料也要切得出來：三天、每天一場。"""
    start = datetime(2026, 9, 10, 21, 0, 0)
    entries = []
    for day in range(3):
        for song in range(2):
            entries.append(entry((start + timedelta(days=day, minutes=30 * song)).isoformat()))
    sessions = group_sessions(entries, gap_hours=6)
    assert len(sessions) == 3
    assert sessions[0]["started_at"].startswith("2026-09-12")
    assert sessions[-1]["started_at"].startswith("2026-09-10")
