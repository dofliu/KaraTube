"""
備份與還原的測試。

這個功能的失敗模式跟其他每一個都不一樣：**它會把原本還在的東西弄不見**。
所以這裡的測試分成三組，而第二、三組才是主體：

1. 打包：清單上的東西有沒有進去、不在清單上的東西有沒有**確實沒進去**。
2. 拒絕：什麼樣的檔案在還原之前就要被擋下來。倒到一半才發現的失敗比
   一開始就拒絕糟糕得多。
3. 套用：覆寫、清除、現況備份，以及「現況備份做不出來就整個不做」。
"""
import json
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest

from backend.services import backup


def write(cache: Path, name: str, payload):
    (cache / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def cache(tmp_path):
    folder = tmp_path / "cache"
    folder.mkdir()
    write(folder, "song_numbers.json",
          {"version": 1, "songs": {"aaa": {"number": 100001}, "bbb": {"number": 100002}}})
    write(folder, "favorites.json", {"songs": {"aaa": {"song_id": "aaa"}}})
    write(folder, "settings.json", {"whisper_model": "small"})
    return folder


def names_in(data: bytes):
    return set(zipfile.ZipFile(BytesIO(data)).namelist())


# --- 一、打包 -----------------------------------------------------------


def test_archive_contains_listed_files(cache):
    data = backup.create_archive(cache, app_version="9.9.9")
    got = names_in(data)
    assert backup.MANIFEST_NAME in got
    assert "data/song_numbers.json" in got
    assert "data/favorites.json" in got
    assert "data/settings.json" in got


def test_missing_files_are_simply_absent(cache):
    """
    沒有這個檔案是正常的（還沒有人收藏過任何一首歌）。

    塞一個空的 `{}` 進去會讓還原時「原本就沒有」與「有，但是空的」分不開。
    """
    got = names_in(backup.create_archive(cache))
    assert "data/score_history.json" not in got


def test_excluded_files_never_enter_the_archive(cache):
    """
    這一條是整組測試裡最重要的一條。

    櫃檯密碼跟著備份檔跑出這台機器，是這個功能唯一一個**安全**等級的錯，
    而它不會有任何症狀 —— 沒有人會發現。所以釘死。
    """
    for name in ("staff_lock.json", "recording_shares.json", "batch_jobs.json",
                 "contest.json", "room_timer.json", "service_calls.json"):
        write(cache, name, {"secret": name})
    got = names_in(backup.create_archive(cache))
    for name in ("staff_lock.json", "recording_shares.json", "batch_jobs.json",
                 "contest.json", "room_timer.json", "service_calls.json"):
        assert f"data/{name}" not in got, f"{name} 不該出現在備份裡"
    blob = backup.create_archive(cache)
    assert b"staff_lock" not in zipfile.ZipFile(BytesIO(blob)).read("data/settings.json")


def test_manifest_records_checksums_and_counts(cache):
    manifest = json.loads(
        zipfile.ZipFile(BytesIO(backup.create_archive(cache, app_version="1.2.3")))
        .read(backup.MANIFEST_NAME))
    assert manifest["kind"] == backup.BACKUP_KIND
    assert manifest["app_version"] == "1.2.3"
    row = next(r for r in manifest["files"] if r["name"] == "song_numbers.json")
    assert len(row["sha256"]) == 64
    assert row["entries"] == 2  # 兩首有號碼


def test_library_snapshot_travels_but_songs_do_not(cache):
    rows = [{"song_id": "aaa", "title": "稻香", "number": 100001,
             "source": "https://youtu.be/aaa", "thumbnail": "x.jpg"}]
    data = backup.create_archive(cache, library=rows)
    got = json.loads(zipfile.ZipFile(BytesIO(data)).read(backup.LIBRARY_NAME))
    assert got["songs"][0]["song_id"] == "aaa"
    assert got["songs"][0]["source"] == "https://youtu.be/aaa"
    # 縮圖之類重跑流水線就會回來的東西不留
    assert "thumbnail" not in got["songs"][0]


def test_library_snapshot_skips_rows_without_id():
    assert backup.library_snapshot([{"title": "沒有 id"}, None, {"song_id": "ok"}]) == \
        [{"song_id": "ok"}]


def test_readme_says_what_is_not_inside(cache):
    text = zipfile.ZipFile(BytesIO(backup.create_archive(cache))).read(
        backup.READ_ME_NAME).decode("utf-8")
    assert "沒有" in text
    assert "recordings/" in text


def test_backup_filename_carries_version_and_time():
    name = backup.backup_filename("1.30.0", datetime(2026, 9, 30, 21, 5, 0))
    assert name == "karatube-backup-v1.30.0-20260930-210500.zip"


def test_describe_plan_lists_groups_and_reasons(cache):
    plan = backup.describe_plan(cache)
    groups = {g["group"]: g for g in plan["groups"]}
    assert set(groups) == {"promise", "people", "machine"}
    book = next(f for f in groups["promise"]["files"] if f["name"] == "song_numbers.json")
    assert book["present"] is True
    assert book["entries"] == 2
    absent = next(f for f in groups["people"]["files"] if f["name"] == "play_stats.json")
    assert absent["present"] is False
    assert absent["entries"] is None
    assert plan["excluded"]


def test_count_entries_unknown_is_none_not_zero():
    """「0 筆」與「不知道」在確認畫面上是兩件完全不同的事。"""
    assert backup.count_entries("favorites.json", b"not json") is None
    assert backup.count_entries("favorites.json", b'{"songs": {}}') == 0
    assert backup.count_entries("unknown.json", b'{"songs": {}}') is None


# --- 二、拒絕 -----------------------------------------------------------


def test_reject_empty_and_non_zip(cache):
    with pytest.raises(backup.BackupError):
        backup.read_manifest(b"")
    with pytest.raises(backup.BackupError, match="zip"):
        backup.read_manifest(b"hello world, not a zip")


def test_reject_zip_without_manifest(cache):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(backup.BackupError, match="備份清單"):
        backup.read_manifest(buf.getvalue())


def test_reject_other_products_backup():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(backup.MANIFEST_NAME, json.dumps({"kind": "something-else"}))
    with pytest.raises(backup.BackupError, match="不是 KaraTube"):
        backup.read_manifest(buf.getvalue())


def test_reject_newer_format(cache):
    """
    比這台機器新的備份一律拒絕。

    欄位只增不減，所以舊備份永遠讀得進來；反過來硬倒的話會壞得很難查，
    而「壞得很難查」正是還原最不能出的事。
    """
    data = backup.create_archive(cache)
    bumped = _rewrite_manifest(data, lambda m: {**m, "format": backup.BACKUP_FORMAT + 1})
    with pytest.raises(backup.BackupError, match="較新的格式"):
        backup.read_manifest(bumped)


def test_older_format_is_accepted(cache):
    data = backup.create_archive(cache)
    older = _rewrite_manifest(data, lambda m: {**m, "format": 1})
    assert backup.read_manifest(older)["format"] == 1


def _rewrite_manifest(data: bytes, mutate):
    src = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for info in src.infolist():
            payload = src.read(info.filename)
            if info.filename == backup.MANIFEST_NAME:
                payload = json.dumps(
                    mutate(json.loads(payload)), ensure_ascii=False).encode("utf-8")
            zf.writestr(info.filename, payload)
    return out.getvalue()


def _replace_member(data: bytes, name: str, payload: bytes) -> bytes:
    src = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for info in src.infolist():
            zf.writestr(info.filename,
                        payload if info.filename == name else src.read(info.filename))
    return out.getvalue()


def test_checksum_mismatch_is_reported(cache):
    """傳輸中壞掉的一份備份倒進去，比沒有備份更糟：原本的資料也沒了。"""
    tampered = _replace_member(backup.create_archive(cache), "data/favorites.json",
                               b'{"songs": {"zzz": {}}}')
    report = backup.inspect_archive(tampered, cache, [])
    assert report["ok"] is False
    assert any("對不起來" in p for p in report["problems"])


def test_broken_json_inside_archive_is_reported(cache):
    broken = _replace_member(backup.create_archive(cache), "data/favorites.json",
                             b"{not json")
    report = backup.inspect_archive(broken, cache, [])
    assert report["ok"] is False


def test_empty_backup_is_not_a_success(tmp_path):
    """一份什麼都沒有的備份倒下去會把機器清空，而使用者以為自己在還原。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    report = backup.inspect_archive(backup.create_archive(empty), empty, [])
    assert report["ok"] is False
    assert any("清空" in p for p in report["problems"])


def test_unknown_data_members_are_listed_not_restored(cache):
    data = backup.create_archive(cache)
    src = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for info in src.infolist():
            zf.writestr(info.filename, src.read(info.filename))
        zf.writestr("data/../../etc/passwd", b"root:x:0:0")
        zf.writestr("data/mystery.json", b"{}")
    report = backup.inspect_archive(out.getvalue(), cache, [])
    assert "mystery.json" in report["unknown"]
    assert report["ok"] is True  # 多餘的東西只是被略過，不讓整份備份作廢


def test_path_traversal_member_is_never_written(cache, tmp_path):
    data = backup.create_archive(cache)
    src = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for info in src.infolist():
            zf.writestr(info.filename, src.read(info.filename))
        zf.writestr("data/../escaped.json", b"{}")
    backup.stage_restore(out.getvalue(), cache, [])
    assert not (cache.parent / "escaped.json").exists()
    assert not (cache / "escaped.json").exists()


def test_oversized_archive_is_rejected(cache):
    with pytest.raises(backup.BackupError, match="太大"):
        backup.read_manifest(b"x" * (backup.MAX_ARCHIVE_BYTES + 1))


# --- 三、檢查報告 -------------------------------------------------------


def test_inspect_shows_before_and_after_counts(cache):
    data = backup.create_archive(cache)
    write(cache, "favorites.json", {"songs": {"a": {}, "b": {}, "c": {}}})
    report = backup.inspect_archive(data, cache, [])
    fav = next(r for r in report["files"] if r["name"] == "favorites.json")
    assert fav["entries"] == 1          # 備份裡的
    assert fav["current_entries"] == 3  # 這台機器現在的


def test_inspect_lists_files_that_will_be_cleared(cache):
    """
    還原是整份取代，所以備份裡沒有的那幾份會被清掉 —— 而那件事要先講。
    """
    data = backup.create_archive(cache)
    write(cache, "score_history.json", {"records": [{"a": 1}, {"b": 2}]})
    report = backup.inspect_archive(data, cache, [])
    cleared = {r["name"]: r for r in report["missing"]}
    assert cleared["score_history.json"]["current_entries"] == 2


def test_song_coverage_explains_the_tombstones(cache):
    """
    「號碼簿上有 3 首，這台機器上只有 1 首」—— 不講的話，還原完那幾百個歌號
    會無聲地變成墓碑，而使用者會以為還原失敗了。
    """
    rows = [{"song_id": s, "title": s, "number": 100000 + i}
            for i, s in enumerate(("aaa", "bbb", "ccc"), start=1)]
    data = backup.create_archive(cache, library=rows)
    report = backup.inspect_archive(data, cache, ["aaa"])
    songs = report["songs"]
    assert songs["known"] is True
    assert songs["in_backup"] == 3
    assert songs["matched"] == 1
    assert songs["missing"] == 2
    assert {e["song_id"] for e in songs["examples"]} == {"bbb", "ccc"}


def test_song_coverage_examples_are_capped(cache):
    rows = [{"song_id": f"s{i:03d}"} for i in range(50)]
    data = backup.create_archive(cache, library=rows)
    report = backup.inspect_archive(data, cache, [])
    assert report["songs"]["missing"] == 50
    assert len(report["songs"]["examples"]) == 8


def test_library_rows_reads_back_the_snapshot(cache):
    data = backup.create_archive(cache, library=[{"song_id": "aaa", "title": "稻香"}])
    assert backup.library_rows(data)[0]["title"] == "稻香"
    assert backup.library_rows(b"garbage") == []


# --- 四、待套用與取消 ---------------------------------------------------


def test_staging_touches_nothing_in_cache(cache):
    before = json.loads((cache / "favorites.json").read_text(encoding="utf-8"))
    data = backup.create_archive(cache)
    write(cache, "favorites.json", {"songs": {"changed": {}}})
    backup.stage_restore(data, cache, [])
    # 現況一個位元組都不該動 —— 取消得掉正是「延後到開機」換來的東西
    now = json.loads((cache / "favorites.json").read_text(encoding="utf-8"))
    assert now == {"songs": {"changed": {}}}
    assert now != before


def test_staging_rejects_a_bad_archive(cache):
    broken = _replace_member(backup.create_archive(cache), "data/favorites.json",
                             b"{not json")
    with pytest.raises(backup.BackupError):
        backup.stage_restore(broken, cache, [])
    assert backup.pending_restore(cache) is None


def test_pending_then_cancel(cache):
    backup.stage_restore(backup.create_archive(cache), cache, [])
    assert backup.pending_restore(cache) is not None
    assert backup.cancel_restore(cache) is True
    assert backup.pending_restore(cache) is None
    assert backup.cancel_restore(cache) is False


def test_staging_twice_replaces_the_first(cache):
    backup.stage_restore(backup.create_archive(cache, app_version="1.0.0"), cache, [])
    backup.stage_restore(backup.create_archive(cache, app_version="2.0.0"), cache, [])
    assert backup.pending_restore(cache)["app_version"] == "2.0.0"


# --- 五、開機時套用 -----------------------------------------------------


def test_apply_without_pending_is_a_noop(cache):
    assert backup.apply_pending_restore(cache) is None


def test_apply_restores_contents(cache):
    data = backup.create_archive(cache)
    write(cache, "favorites.json", {"songs": {"changed": {}}})
    backup.stage_restore(data, cache, [])

    report = backup.apply_pending_restore(cache, app_version="1.30.0")
    assert report["status"] == "done"
    restored = json.loads((cache / "favorites.json").read_text(encoding="utf-8"))
    assert restored == {"songs": {"aaa": {"song_id": "aaa"}}}
    assert {r["name"] for r in report["restored"]} >= {"favorites.json",
                                                       "song_numbers.json"}


def test_apply_clears_files_missing_from_the_backup(cache):
    """只還原、不合併：備份裡沒有的那一份要清掉，不然兩台機器的資料會混在一起。"""
    data = backup.create_archive(cache)
    write(cache, "score_history.json", {"records": [{"x": 1}]})
    backup.stage_restore(data, cache, [])
    report = backup.apply_pending_restore(cache)
    assert not (cache / "score_history.json").exists()
    assert [r["name"] for r in report["cleared"]] == ["score_history.json"]


def test_apply_writes_a_safety_copy_first(cache):
    data = backup.create_archive(cache)
    write(cache, "favorites.json", {"songs": {"only-here": {}}})
    backup.stage_restore(data, cache, [])
    report = backup.apply_pending_restore(cache)

    copies = backup.safety_copies(cache)
    assert len(copies) == 1
    assert copies[0]["name"] == report["safety_copy"]
    # 現況備份裡要有「被蓋掉之前」的那一份
    saved = zipfile.ZipFile(cache / backup.SAFETY_DIRNAME / copies[0]["name"])
    assert json.loads(saved.read("data/favorites.json")) == {"songs": {"only-here": {}}}


def test_apply_aborts_when_the_safety_copy_cannot_be_written(cache, monkeypatch):
    """
    現況備份做不出來就整個不做。

    一個沒有退路的還原不值得做 —— 「今天先不還原」使用者按一下就能重來，
    「原本的資料也沒了」不行。而且 pending 要留著，磁碟清乾淨之後重開機
    自動接著做，不必再上傳一次那個檔案。
    """
    backup.stage_restore(backup.create_archive(cache), cache, [])
    write(cache, "favorites.json", {"songs": {"kept": {}}})

    def boom(*_a, **_k):
        raise OSError("no space left on device")

    monkeypatch.setattr(backup, "create_archive", boom)
    report = backup.apply_pending_restore(cache)

    assert report["status"] == "aborted"
    assert json.loads((cache / "favorites.json").read_text(encoding="utf-8")) == \
        {"songs": {"kept": {}}}
    assert backup.pending_restore(cache) is not None


def test_pending_is_consumed_after_a_successful_apply(cache):
    backup.stage_restore(backup.create_archive(cache), cache, [])
    backup.apply_pending_restore(cache)
    assert backup.pending_restore(cache) is None
    # 重開第二次不該再還原一次
    assert backup.apply_pending_restore(cache) is None


def test_report_survives_for_the_person_who_comes_back(cache):
    """還原發生在沒有人看著的開機那一刻，所以結果要留下來。"""
    backup.stage_restore(backup.create_archive(cache, app_version="1.29.0"), cache, [])
    backup.apply_pending_restore(cache, app_version="1.30.0")
    saved = backup.last_report(cache)
    assert saved["status"] == "done"
    assert saved["source_version"] == "1.29.0"


def test_safety_copies_are_pruned(cache, monkeypatch):
    folder = cache / backup.SAFETY_DIRNAME
    folder.mkdir()
    for i in range(backup.SAFETY_KEEP + 3):
        (folder / f"pre-restore-2026093{i:01d}-000000.zip").write_bytes(b"x")
    backup._prune_safety(folder)
    assert len(backup.safety_copies(cache)) == backup.SAFETY_KEEP
    # 留下來的是最新的那幾份
    assert backup.safety_copies(cache)[0]["name"].startswith("pre-restore-20260937")


def test_safety_path_refuses_anything_that_is_not_a_safety_copy(cache):
    folder = cache / backup.SAFETY_DIRNAME
    folder.mkdir()
    (folder / "pre-restore-20260930-000000.zip").write_bytes(b"x")
    assert backup.safety_path(cache, "pre-restore-20260930-000000.zip") is not None
    for evil in ("../settings.json", "..%2Fsettings.json", "settings.json",
                 "pre-restore-x.txt", "", "pre-restore-../x.zip"):
        assert backup.safety_path(cache, evil) is None


def test_last_report_handles_a_broken_file(cache):
    (cache / backup.REPORT_NAME).write_text("{not json", encoding="utf-8")
    assert backup.last_report(cache) is None


def test_pending_handles_a_broken_manifest(cache):
    folder = backup.pending_dir(cache)
    folder.mkdir(parents=True)
    (folder / backup.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    assert backup.pending_restore(cache) is None
    assert backup.apply_pending_restore(cache) is None


def test_apply_reports_a_file_that_vanished_from_the_staging_area(cache):
    backup.stage_restore(backup.create_archive(cache), cache, [])
    (backup.pending_dir(cache) / "favorites.json").unlink()
    report = backup.apply_pending_restore(cache)
    assert report["status"] == "partial"
    assert [r["name"] for r in report["failed"]] == ["favorites.json"]


def test_round_trip_on_a_fresh_machine(cache, tmp_path):
    """
    整條路：舊機器打包 → 新機器（什麼都沒有）還原 → 內容一模一樣。
    """
    data = backup.create_archive(cache, app_version="1.30.0",
                                 library=[{"song_id": "aaa", "title": "稻香"}])
    fresh = tmp_path / "new-machine"
    fresh.mkdir()

    report = backup.inspect_archive(data, fresh, [])
    assert report["ok"] is True
    assert report["songs"]["missing"] == 1  # 新機器上一首歌都沒有

    backup.stage_restore(data, fresh, [])
    backup.apply_pending_restore(fresh, app_version="1.30.0")

    for name in ("song_numbers.json", "favorites.json", "settings.json"):
        assert (fresh / name).read_bytes() == (cache / name).read_bytes()
    # 櫃檯密碼沒有跟過來（新機器仍然是「還沒設定」）
    assert not (fresh / "staff_lock.json").exists()
