"""錄音轉 MP3 的單元測試。

這支功能會開子行程、會寫檔、會被好幾個請求同時打進來，所以測試的重點不是
「轉得出來」（那是 ffmpeg 的事），而是**轉不出來的時候會發生什麼**：

  * ffmpeg 不在／沒有 libmp3lame —— 要講得出是哪一種，而不是一句失敗；
  * 轉到一半失敗或逾時 —— 資料夾裡不可以留下一個播到一半會斷的 .part；
  * 同一筆被按兩次、整台機器被十個人同時按 —— 不可以有兩個 ffmpeg 同時
    寫同一個目標檔，也不可以十個一起跑把 CPU 從舞台那邊搶走。

機器上有沒有真的 ffmpeg 不影響這些測試：`KARATUBE_FFMPEG` 指到一支假的
ffmpeg（一個小 shell script），成功、失敗、逾時、空輸出都演得出來。
"""
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from backend.services import transcoder
from backend.services.transcoder import (DEFAULT_BITRATE_KBPS, MAX_BITRATE_KBPS,
                                         MIN_BITRATE_KBPS, TranscodeGate, build_command,
                                         clamp_bitrate, probe_ffmpeg, reset_probe_cache,
                                         transcode_to_mp3)
from tests.fake_ffmpeg import GOOD_FFMPEG, POSIX_ONLY, write_fake_ffmpeg

# 假的 ffmpeg 是 shell script，Windows 上跑不起來。CI 與部署目標都是 Linux。
pytestmark = pytest.mark.skipif(not POSIX_ONLY, reason="假 ffmpeg 用的是 POSIX shell script")


@pytest.fixture(autouse=True)
def clean_probe_cache():
    """探測結果是模組層級的快取，測試之間不清會互相污染。"""
    reset_probe_cache()
    yield
    reset_probe_cache()
    os.environ.pop("KARATUBE_FFMPEG", None)


@pytest.fixture()
def good_ffmpeg(tmp_path):
    return str(write_fake_ffmpeg(tmp_path / "ffmpeg-good", GOOD_FFMPEG))


@pytest.fixture()
def source(tmp_path):
    src = tmp_path / "take.webm"
    src.write_bytes(b"\x1aE\xdf\xa3fake-webm")
    return src


# --- 位元率 ---

def test_bitrate_clamped_into_range():
    assert clamp_bitrate(192) == 192
    assert clamp_bitrate(10) == MIN_BITRATE_KBPS
    assert clamp_bitrate(9999) == MAX_BITRATE_KBPS


def test_bitrate_garbage_falls_back_to_default():
    # 設定檔被人手動改壞時，要的是「照預設值轉」而不是整個功能不見
    for bad in (None, "", "很大聲", float("nan")):
        assert clamp_bitrate(bad) == DEFAULT_BITRATE_KBPS


# --- 指令組裝 ---

def test_command_has_the_parts_that_make_it_playable_everywhere():
    cmd = build_command(Path("/a/in.webm"), Path("/a/out.mp3"), 192, binary="ffmpeg")
    assert cmd[0] == "ffmpeg"
    assert "libmp3lame" in cmd
    assert "192k" in cmd
    # 44100 是給老車機的：48kHz 的 MP3 合法，但不是每台都吃
    assert cmd[cmd.index("-ar") + 1] == "44100"
    # 輸出一定在最後（ffmpeg 的規矩），而且來源要在 -i 後面
    assert cmd[-1] == "/a/out.mp3"
    assert cmd[cmd.index("-i") + 1] == "/a/in.webm"
    # 格式要明講：實際寫出去的是 `out.mp3.part`（原子換檔用），
    # 而 ffmpeg 是看副檔名猜格式的 —— 不講就直接 "Invalid argument"。
    # 這是真的跑一次 ffmpeg 才會發現的事，所以用測試釘住。
    assert cmd[-3:-1] == ["-f", "mp3"]


def test_command_carries_id3_tags_for_car_stereo_screens():
    cmd = build_command(Path("in.webm"), Path("out.mp3"),
                        tags={"title": "海闊天空", "artist": "阿明", "album": "KaraTube"})
    assert "title=海闊天空" in cmd
    assert "artist=阿明" in cmd


def test_tags_cannot_smuggle_extra_metadata_keys():
    # 歌名是 YouTube 抓回來的字串。裡面的 `=` 會讓 ffmpeg 把它讀成另一個欄位，
    # 換行更是直接把一個參數拆成兩個。
    cmd = build_command(Path("in.webm"), Path("out.mp3"),
                        tags={"title": "a=b\nartist=別人"})
    title = next(v for v in cmd if v.startswith("title="))
    assert "\n" not in title
    assert title.count("=") == 1


def test_empty_tags_are_skipped_entirely():
    cmd = build_command(Path("in.webm"), Path("out.mp3"), tags={"title": "", "artist": None})
    assert "-metadata" not in cmd


# --- 探測 ---

def test_probe_reports_available_when_lame_is_there(good_ffmpeg):
    cap = probe_ffmpeg(binary=good_ffmpeg)
    assert cap["available"] is True


def test_probe_says_not_installed_when_binary_is_missing(tmp_path):
    cap = probe_ffmpeg(binary=str(tmp_path / "no-such-ffmpeg"))
    assert cap["available"] is False
    assert cap["reason"] == "not_installed"
    assert "ffmpeg" in cap["message"]


def test_probe_distinguishes_ffmpeg_without_mp3_encoder(tmp_path):
    # 精簡版 ffmpeg 是真的存在的（有些發行版為了授權把 lame 拆出去）。
    # 它的失敗方式是「轉了兩秒然後吐一個看不懂的錯」，所以要先問清楚。
    binary = write_fake_ffmpeg(tmp_path / "ffmpeg-nolame",
                               'echo " A..... aac    AAC (Advanced Audio Coding)"\nexit 0')
    cap = probe_ffmpeg(binary=str(binary))
    assert cap["available"] is False
    assert cap["reason"] == "no_lame"


def test_probe_result_is_cached_between_calls(tmp_path, monkeypatch):
    calls = []
    real_run = subprocess.run

    def counting_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return real_run(cmd, *args, **kwargs)

    binary = str(write_fake_ffmpeg(tmp_path / "ffmpeg-count", GOOD_FFMPEG))
    monkeypatch.setattr(transcoder.subprocess, "run", counting_run)
    probe_ffmpeg(binary=binary)
    probe_ffmpeg(binary=binary)
    assert len(calls) == 1          # 每一次下載都去問一次 ffmpeg 是白花的
    probe_ffmpeg(binary=binary, force=True)
    assert len(calls) == 2          # 但「重新偵測」要真的重問


# --- 轉檔 ---

def test_transcode_writes_the_file(good_ffmpeg, source, tmp_path):
    dst = tmp_path / "mp3" / "out.mp3"
    result = transcode_to_mp3(source, dst, binary=good_ffmpeg)
    assert result["status"] == "ok"
    assert dst.is_file() and dst.stat().st_size > 0
    assert result["bytes"] == dst.stat().st_size


def test_failed_transcode_leaves_nothing_behind(source, tmp_path):
    # 轉一半失敗留下的 .part 如果被當成 mp3 換上去，使用者拿到的是一個
    # 播到一半會斷的檔案，他的結論會是「這次的錄音壞了」。
    binary = write_fake_ffmpeg(tmp_path / "ffmpeg-fail",
                               'for a in "$@"; do out="$a"; done\n'
                               'printf half > "$out"\n'
                               'echo "Conversion failed" 1>&2\nexit 1')
    dst = tmp_path / "mp3" / "out.mp3"
    result = transcode_to_mp3(source, dst, binary=str(binary))
    assert result["status"] == "failed"
    assert result["reason"] == "ffmpeg_error"
    assert not dst.exists()
    assert list((tmp_path / "mp3").glob("*.part")) == []


def test_transcode_rejects_empty_output_even_when_ffmpeg_says_ok(source, tmp_path):
    # rc=0 但沒寫出東西是真的會發生（來源沒有音軌時某些版本這樣收場）。
    binary = write_fake_ffmpeg(tmp_path / "ffmpeg-empty", "exit 0")
    result = transcode_to_mp3(source, tmp_path / "out.mp3", binary=str(binary))
    assert result["status"] == "failed"
    assert result["reason"] == "empty_output"
    assert not (tmp_path / "out.mp3").exists()


def test_transcode_times_out_instead_of_holding_the_thread_forever(source, tmp_path):
    binary = write_fake_ffmpeg(tmp_path / "ffmpeg-hang", "sleep 30")
    started = time.time()
    result = transcode_to_mp3(source, tmp_path / "out.mp3", binary=str(binary), timeout=1)
    assert result["status"] == "failed"
    assert result["reason"] == "timeout"
    assert time.time() - started < 10       # 真的有砍掉，不是等它自己結束
    assert list(tmp_path.glob("*.part")) == []


def test_transcode_without_ffmpeg_says_so(source, tmp_path):
    result = transcode_to_mp3(source, tmp_path / "out.mp3", binary=str(tmp_path / "nope"))
    assert result["status"] == "failed"
    assert result["reason"] == "not_installed"


def test_transcode_missing_source_is_not_an_exception(tmp_path, good_ffmpeg):
    result = transcode_to_mp3(tmp_path / "gone.webm", tmp_path / "out.mp3", binary=good_ffmpeg)
    assert result["status"] == "failed"
    assert result["reason"] == "missing_source"


# --- 兩道關卡 ---

def test_gate_runs_one_at_a_time_across_recordings():
    gate = TranscodeGate(max_concurrent=1)
    peak = {"now": 0, "max": 0}
    lock = threading.Lock()

    def worker():
        with lock:
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
        time.sleep(0.05)
        with lock:
            peak["now"] -= 1
        return {"status": "ready"}

    threads = [threading.Thread(target=lambda i=i: gate.run(f"rec{i}", worker))
               for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 同時跑四個 ffmpeg 會把 CPU 從「正在放歌、算音準」那邊搶走
    assert peak["max"] == 1


def test_gate_lets_the_second_caller_see_the_first_ones_result():
    """同一筆被按兩次時，第二個 worker 進來時第一個已經轉好了。"""
    gate = TranscodeGate(max_concurrent=2)
    done = []

    def worker():
        if done:
            return {"status": "ready", "cached": True}
        time.sleep(0.05)
        done.append(1)
        return {"status": "ready", "cached": False}

    results = []
    threads = [threading.Thread(target=lambda: results.append(gate.run("same", worker)))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(done) == 1                                    # 只轉了一次
    assert sorted(r.get("cached", False) for r in results) == [False, True]


def test_gate_gives_up_instead_of_hanging_forever():
    gate = TranscodeGate(max_concurrent=1, wait_timeout=0.1)
    release = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        release.wait(timeout=5)
        return {"status": "ready"}

    holder = threading.Thread(target=lambda: gate.run("busy", slow))
    holder.start()
    started.wait(timeout=5)
    # 排不進去時要有結論（一句「現在忙」），而不是讓瀏覽器一直轉圈圈
    result = gate.run("other", lambda: {"status": "ready"})
    assert result["status"] == "busy"
    release.set()
    holder.join()
