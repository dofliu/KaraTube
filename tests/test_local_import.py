"""本機曲庫匯入單元測試。

這一支釘的是匯入對使用者的三個承諾：

1. **同一個檔案永遠是同一首歌** —— 改名、搬資料夾都不會變成第二首
   （變成第二首的代價是再跑一次十幾分鐘的流水線，而歌號、最愛、個人最佳全部分家）。
2. **檔名不會被亂切** —— `A-Lin` 不能變成一位叫「A」的歌星。
3. **送進來的路徑出不了匯入資料夾** —— 那條路徑是從瀏覽器來的。
"""
import json

import pytest

from backend.services.local_import import (
    LOCAL_ID_PREFIX,
    LocalImportLibrary,
    fingerprint_file,
    is_local_id,
    kind_of,
    local_url,
    parse_source_name,
    sidecar_lrc,
    strip_noise,
)
from backend.services.storage import SongStorage


def make_media(root, name, size=200 * 1024, seed=b"A"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(seed * size)
    return path


def make_library(tmp_path, storage=None):
    return LocalImportLibrary(tmp_path / "import", tmp_path / "local_imports.json",
                              storage=storage)


def make_cached_song(songs_root, song_id, title="稻香", complete=True):
    song_dir = songs_root / song_id
    song_dir.mkdir(parents=True, exist_ok=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": title, "artist": "周杰倫"}, ensure_ascii=False),
        encoding="utf-8")
    (song_dir / "lyrics.json").write_text("[]", encoding="utf-8")
    if complete:
        (song_dir / "instrumental.mp3").write_bytes(b"x")
        (song_dir / "vocals.mp3").write_bytes(b"x")
    return song_dir


# --- 檔名解析 ---------------------------------------------------------------

@pytest.mark.parametrize("stem,title,artist", [
    ("周杰倫 - 稻香", "稻香", "周杰倫"),
    ("周杰倫－稻香", "稻香", "周杰倫"),          # 全形減號可以貼著字
    ("周杰倫 – 稻香", "稻香", "周杰倫"),
    ("05. 五月天 - 倔強", "倔強", "五月天"),     # 開頭的曲序要拿掉
    ("稻香", "稻香", ""),                        # 沒有歌手不是錯誤
])
def test_parses_artist_and_title_from_filenames(stem, title, artist):
    assert parse_source_name(stem) == (title, artist)


def test_hyphen_inside_a_name_is_not_a_separator():
    """`A-Lin` 切開的話，那首歌會永遠掛在一位叫「A」的歌星底下。

    半形減號兩邊要有空白才算分隔 —— 這是整個解析裡唯一會**默默把資料弄錯**
    的地方（其餘解錯都看得出來）。
    """
    assert parse_source_name("A-Lin") == ("A-Lin", "")
    assert parse_source_name("A-Lin - 有一種悲傷") == ("有一種悲傷", "A-Lin")


def test_strips_technical_noise_but_keeps_version_info():
    """規格括號丟掉（歌名字數查歌是照字數算的），版本括號一定要留。"""
    assert strip_noise("稻香 [1080p]") == "稻香"
    assert strip_noise("稻香(官方MV)") == "稻香"
    assert strip_noise("稻香 【4K】 HD") == "稻香"
    # (Live) 是另一個錄音：丟掉的話它會跟原版在曲庫裡撞成一首
    assert strip_noise("稻香 (Live)") == "稻香 (Live)"
    assert strip_noise("海闊天空 (粵語版)") == "海闊天空 (粵語版)"


def test_parse_never_returns_an_empty_title():
    """整串都被當成雜訊砍掉時要退回原文 —— 沒有名字的歌在曲庫裡找不回來。"""
    title, artist = parse_source_name("HD")
    assert title
    assert artist == ""


# --- 指紋 -------------------------------------------------------------------

def test_same_content_keeps_the_same_song_id_after_rename(tmp_path):
    a = make_media(tmp_path, "周杰倫 - 稻香.mp4")
    fp_before = fingerprint_file(a)
    b = a.rename(tmp_path / "稻香.mp4")
    assert fingerprint_file(b) == fp_before
    assert is_local_id(fp_before)
    assert local_url(fp_before) == f"local:{fp_before}"


def test_different_content_gets_a_different_id(tmp_path):
    a = make_media(tmp_path, "a.mp4", seed=b"A")
    b = make_media(tmp_path, "b.mp4", seed=b"B")
    assert fingerprint_file(a) != fingerprint_file(b)


def test_fingerprint_is_cached_between_scans(tmp_path):
    """第二次掃描不該再讀那 2MB —— 一顆隨身碟掃一次要好幾分鐘的話沒有人會用。"""
    lib = make_library(tmp_path)
    media = make_media(lib.root, "歌手 - 歌.mp4")
    first = lib.scan()["files"][0]["song_id"]

    calls = {"n": 0}
    real = fingerprint_file

    def counting(path, *args, **kwargs):
        calls["n"] += 1
        return real(path, *args, **kwargs)

    import backend.services.local_import as mod
    mod.fingerprint_file = counting
    try:
        second = lib.scan()["files"][0]["song_id"]
    finally:
        mod.fingerprint_file = real
    assert second == first
    assert calls["n"] == 0, "指紋應該從 registry 的快取拿，不該重讀檔案"

    # 檔案內容換了（同名覆蓋）就要重算，不然會沿用別首歌的身分
    media.write_bytes(b"Z" * (300 * 1024))
    assert lib.scan()["files"][0]["song_id"] != first


# --- 掃描 -------------------------------------------------------------------

def test_scan_lists_media_and_explains_what_it_skipped(tmp_path):
    lib = make_library(tmp_path)
    make_media(lib.root, "周杰倫 - 稻香.mp4")
    make_media(lib.root, "專輯/02. 五月天 - 倔強.mp3")
    (lib.root / "封面.png").write_bytes(b"x" * (200 * 1024))
    make_media(lib.root, "半個檔案.mp4", size=1)      # 太小，不是一首歌

    scan = lib.scan()
    paths = {f["path"] for f in scan["files"]}
    assert any(p.endswith("稻香.mp4") for p in paths)
    assert any(p.endswith("倔強.mp3") for p in paths)        # 子資料夾也要掃
    assert scan["counts"]["new"] == 2
    reasons = " ".join(s["reason"] for s in scan["skipped"])
    assert "不支援" in reasons and "太小" in reasons


def test_scan_hides_our_own_readme_and_lrc_files(tmp_path):
    """.lrc 是歌詞不是歌，說明檔是我們自己放的 —— 兩者都不該出現在「略過」清單上，
    不然畫面會一直說「有兩個檔案機器不認得」。"""
    lib = make_library(tmp_path)
    make_media(lib.root, "稻香.mp4")
    (lib.root / "稻香.lrc").write_text("[00:01.00]稻香", encoding="utf-8")
    scan = lib.scan()
    assert len(scan["files"]) == 1
    assert scan["files"][0]["has_lrc"] is True
    assert scan["skipped"] == []


def test_already_imported_songs_are_marked_not_offered_again(tmp_path):
    """已經在曲庫裡的檔案要標成「已在曲庫」—— 勾得下去的話，使用者會等十分鐘
    換來一首他本來就有的歌。判準是**曲庫裡的檔案齊不齊**，不是 registry 說了算。"""
    songs_root = tmp_path / "songs"
    songs_root.mkdir()
    storage = SongStorage(songs_root)
    lib = make_library(tmp_path, storage=storage)
    media = make_media(lib.root, "稻香.mp4")
    song_id = fingerprint_file(media)
    make_cached_song(songs_root, song_id)

    entry = lib.scan()["files"][0]
    assert entry["state"] == "imported"
    assert "曲庫" in entry["note"]


def test_a_cleared_cache_makes_the_file_importable_again(tmp_path):
    """registry 說跑完了、但曲庫裡的檔案被快取上限清掉了：那是「可以再匯入一次」，
    不是「壞了」。說成壞掉的話使用者會去刪原始檔案。"""
    songs_root = tmp_path / "songs"
    songs_root.mkdir()
    lib = make_library(tmp_path, storage=SongStorage(songs_root))
    media = make_media(lib.root, "稻香.mp4")
    song_id = fingerprint_file(media)
    lib.remember(song_id, "稻香.mp4", "稻香", "")
    lib.mark(song_id, "done")

    entry = lib.scan()["files"][0]
    assert entry["state"] == "new"
    assert "已經沒有" in entry["note"]


def test_failed_imports_keep_their_reason(tmp_path):
    lib = make_library(tmp_path)
    media = make_media(lib.root, "壞檔.mp4")
    song_id = fingerprint_file(media)
    lib.remember(song_id, "壞檔.mp4", "壞檔", "")
    lib.mark(song_id, "error", "這個檔案裡沒有音訊軌")

    entry = lib.scan()["files"][0]
    assert entry["state"] == "failed"
    assert "沒有音訊軌" in entry["note"]


def test_warns_when_the_library_already_has_the_same_title(tmp_path):
    songs_root = tmp_path / "songs"
    songs_root.mkdir()
    lib = make_library(tmp_path, storage=SongStorage(songs_root))
    make_cached_song(songs_root, "abc12345678", title="稻香")
    make_media(lib.root, "周杰倫 - 稻香.mp4")

    entry = lib.scan()["files"][0]
    assert entry["state"] == "new", "同名只是提醒，不能擋"
    assert entry["duplicate_of"] == "abc12345678"


def test_user_edited_titles_survive_the_next_scan(tmp_path):
    """使用者改過的歌名要活過重新掃描：檔名解析永遠只會得到同一個答案，
    每次掃描都覆蓋回去的話，他改的那一次等於沒發生。"""
    lib = make_library(tmp_path)
    make_media(lib.root, "track07.mp4")
    lib.prepare([{"path": "track07.mp4", "title": "紅豆", "artist": "王菲"}])
    entry = lib.scan()["files"][0]
    assert (entry["title"], entry["artist"]) == ("紅豆", "王菲")


def test_scan_creates_the_folder_with_a_readme(tmp_path):
    """資料夾必須自己講得出用法 —— 部署文件在另一台電腦上。"""
    lib = make_library(tmp_path)
    assert lib.root.is_dir()
    readme = next(lib.root.glob("讀我*.txt"))
    text = readme.read_text(encoding="utf-8")
    assert ".lrc" in text and "歌手 - 歌名" in text
    assert lib.scan()["files"] == [], "說明檔不該被當成一首歌"


# --- 路徑安全 ---------------------------------------------------------------

@pytest.mark.parametrize("evil", [
    "../../etc/passwd", "/etc/passwd", "a/../../outside.mp4", "..\\..\\windows\\win.ini",
])
def test_paths_cannot_escape_the_import_folder(tmp_path, evil):
    """
    路徑是從瀏覽器送進來的字串。不變量是「解出來的一定在匯入資料夾裡面」——
    拒絕或當成相對路徑都可以（絕對路徑的開頭斜線就是被當成相對處理的），
    唯一不能發生的是解到資料夾外面去。
    """
    lib = make_library(tmp_path)
    base = lib.root.resolve()
    try:
        resolved = lib.resolve(evil)
    except ValueError:
        return
    assert base in resolved.parents, f"{evil} 解到了 {resolved}"


def test_symlinks_pointing_outside_are_refused(tmp_path):
    lib = make_library(tmp_path)
    outside = make_media(tmp_path, "outside.mp4")
    link = lib.root / "link.mp4"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("這個平台不支援符號連結")
    with pytest.raises(ValueError):
        lib.resolve("link.mp4")


# --- 準備匯入 ---------------------------------------------------------------

def test_prepare_builds_sources_the_scheduler_understands(tmp_path):
    lib = make_library(tmp_path)
    make_media(lib.root, "周杰倫 - 稻香.mp4")
    result = lib.prepare([{"path": "周杰倫 - 稻香.mp4"}])

    assert result["failed"] == []
    source = result["sources"][0]
    assert source["title"] == "稻香" and source["artist"] == "周杰倫"
    assert source["url"] == local_url(source["song_id"])
    assert source["song_id"].startswith(LOCAL_ID_PREFIX)
    # 帳本記得住這首歌的來源，處理時才找得到檔案
    assert lib.entry(source["song_id"])["path"] == "周杰倫 - 稻香.mp4"


def test_prepare_skips_bad_entries_without_dropping_the_whole_batch(tmp_path):
    """一顆隨身碟裡有一兩個壞檔是常態，不能因此整批都收不下來。"""
    lib = make_library(tmp_path)
    make_media(lib.root, "好的.mp4")
    result = lib.prepare([
        {"path": "好的.mp4"},
        {"path": "不存在.mp4"},
        {"path": "../逃出去.mp4"},
    ])
    assert len(result["sources"]) == 1
    assert len(result["failed"]) == 2


def test_prepare_deduplicates_the_same_file_listed_twice(tmp_path):
    lib = make_library(tmp_path)
    make_media(lib.root, "稻香.mp4")
    result = lib.prepare([{"path": "稻香.mp4"}, {"path": "稻香.mp4"}])
    assert len(result["sources"]) == 1


def test_source_path_says_where_to_put_the_file_back(tmp_path):
    """來源不見了是這條路上最常見的失敗（隨身碟被拔掉），
    而錯誤訊息要講得出「放回哪裡」—— 講「找不到檔案」等於沒講。"""
    lib = make_library(tmp_path)
    media = make_media(lib.root, "稻香.mp4")
    song_id = lib.prepare([{"path": "稻香.mp4"}])["sources"][0]["song_id"]
    media.unlink()
    with pytest.raises(FileNotFoundError) as excinfo:
        lib.source_path(song_id)
    assert "import" in str(excinfo.value)


def test_sidecar_lrc_is_found_next_to_the_media(tmp_path):
    lib = make_library(tmp_path)
    make_media(lib.root, "稻香.mp4")
    (lib.root / "稻香.lrc").write_text("[00:01.00]午後的陽光", encoding="utf-8")
    song_id = lib.prepare([{"path": "稻香.mp4"}])["sources"][0]["song_id"]
    assert sidecar_lrc(lib.root / "稻香.mp4") is not None
    assert "午後的陽光" in lib.sidecar_lrc_text(song_id)


def test_registry_survives_a_restart_and_a_broken_file(tmp_path):
    lib = make_library(tmp_path)
    make_media(lib.root, "稻香.mp4")
    song_id = lib.prepare([{"path": "稻香.mp4"}])["sources"][0]["song_id"]

    again = make_library(tmp_path)
    assert again.entry(song_id)["path"] == "稻香.mp4"

    (tmp_path / "local_imports.json").write_text("{壞掉的 JSON", encoding="utf-8")
    broken = make_library(tmp_path)
    assert broken.entry(song_id) is None      # 從空的開始，但不能讓機器開不起來
    assert broken.scan()["counts"]["new"] == 1


def test_kind_of_knows_audio_from_video():
    assert kind_of(".mp4") == "video"
    assert kind_of(".FLAC") == "audio"
    assert kind_of(".txt") == ""
