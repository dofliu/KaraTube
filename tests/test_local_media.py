"""本機檔案前處理（ffmpeg／ffprobe）單元測試。

這一段的錯誤全部都是「要有真的檔案才看得到」的那種：參數錯一個字，症狀是
匯入完成、歌卻沒有聲音；判讀錯一個欄位，症狀是一張專輯封面被當成 MV 播出來。
所以指令與判讀都抽成純函式，這裡直接釘住它們；會動到磁碟的那幾支則用
`fake_ffmpeg.py` 的假 ffmpeg 跑（CI 上有沒有 ffmpeg 不該是測試要賭的事）。
"""
import json

import pytest

from backend.pipeline.local_media import (
    build_audio_command,
    build_probe_command,
    build_thumbnail_command,
    build_video_copy_command,
    build_video_encode_command,
    extract_audio,
    parse_probe_output,
    prepare_video,
    probe_media,
)
from tests.fake_ffmpeg import GOOD_FFMPEG, POSIX_ONLY, write_fake_ffmpeg

# 失敗的 ffmpeg：串流複製那一次要失敗，才測得到「退回重新編碼」那條路
FAILING_COPY = """
for a in "$@"; do
  if [ "$a" = "copy" ]; then exit 1; fi
done
for a in "$@"; do out="$a"; done
printf 'fake-encoded' > "$out"
exit 0
"""

ALWAYS_FAIL = """
exit 1
"""


def probe_json(**kwargs):
    streams = []
    if kwargs.get("audio", True):
        streams.append({"codec_type": "audio", "duration": "215.0"})
    if kwargs.get("video"):
        streams.append({"codec_type": "video", "width": 1920, "height": 1080})
    if kwargs.get("cover"):
        streams.append({"codec_type": "video", "width": 600, "height": 600,
                        "disposition": {"attached_pic": 1}})
    fmt = {"duration": kwargs.get("duration", "215.0")}
    if kwargs.get("tags"):
        fmt["tags"] = kwargs["tags"]
    return json.dumps({"format": fmt, "streams": streams})


# --- 判讀 -------------------------------------------------------------------

def test_reads_duration_streams_and_embedded_tags():
    info = parse_probe_output(probe_json(video=True, tags={"title": "稻香", "artist": "周杰倫"}))
    assert info["has_audio"] and info["has_video"]
    assert info["duration"] == pytest.approx(215.0)
    assert (info["title"], info["artist"]) == ("稻香", "周杰倫")
    assert (info["width"], info["height"]) == (1920, 1080)


def test_album_art_is_not_a_music_video():
    """mp3 裡的封面圖是以「視訊軌」的樣子出現的。當成 MV 的話，舞台會播一張
    不動的圖整整四分鐘 —— 那正是情境背景要取代的東西。"""
    info = parse_probe_output(probe_json(cover=True))
    assert info["has_audio"] is True
    assert info["has_video"] is False


def test_falls_back_to_the_audio_stream_duration():
    """有些 mkv 的 format 層沒有長度。歌的長度要跟音訊一致，不能回 0
    （0 會讓下游把整首歌當成空的）。"""
    raw = json.dumps({"format": {}, "streams": [{"codec_type": "audio", "duration": "180.5"}]})
    assert parse_probe_output(raw)["duration"] == pytest.approx(180.5)


@pytest.mark.parametrize("junk", ["", "not json", "[]", "{\"format\": 3}"])
def test_garbage_probe_output_is_not_a_crash(junk):
    """ffprobe 吐垃圾時要回「什麼都沒有」，而不是炸掉整條流水線。"""
    info = parse_probe_output(junk)
    assert info["has_audio"] is False and info["duration"] == 0.0


# --- 指令 -------------------------------------------------------------------

def test_audio_command_takes_only_the_first_audio_track(tmp_path):
    cmd = build_audio_command(tmp_path / "in.mkv", tmp_path / "out.mp3", binary="ff")
    assert cmd[0] == "ff"
    assert "-vn" in cmd and "0:a:0" in cmd
    # 輸出寫的是 .part 之類的暫存名，格式一定要明講（見 transcoder.py 的同一個坑）
    assert "-f" in cmd and "mp3" in cmd
    assert "-nostdin" in cmd


def test_video_commands_drop_the_audio_track(tmp_path):
    """背景影片一律靜音：聲音永遠來自 instrumental/vocals，
    多一條音軌只是讓檔案大三成，還多一條讓瀏覽器選錯的路。"""
    for cmd in (build_video_copy_command(tmp_path / "a.mkv", tmp_path / "b.mp4"),
                build_video_encode_command(tmp_path / "a.mkv", tmp_path / "b.mp4")):
        assert "-an" in cmd
        assert "+faststart" in cmd
        # 明講輸出格式（輸出檔名是 .part，副檔名猜不出來）
        assert cmd[-3:-1] == ["-f", "mp4"]


def test_copy_command_really_copies_and_encode_command_really_encodes(tmp_path):
    copy_cmd = build_video_copy_command(tmp_path / "a.mkv", tmp_path / "b.mp4")
    assert "copy" in copy_cmd and "libx264" not in copy_cmd
    encode_cmd = build_video_encode_command(tmp_path / "a.mkv", tmp_path / "b.mp4", height=720)
    assert "libx264" in encode_cmd and "copy" not in encode_cmd
    assert any("720" in part for part in encode_cmd)


def test_thumbnail_seeks_before_input_and_names_its_encoder(tmp_path):
    """`-ss` 放在 `-i` 後面會從頭解碼到那一秒（長片要等很久）；
    輸出是 .part，不講編碼器 image2 就猜不出要編成 JPEG。"""
    cmd = build_thumbnail_command(tmp_path / "a.mp4", tmp_path / "t.jpg", 30.0)
    assert cmd.index("-ss") < cmd.index("-i")
    assert "mjpeg" in cmd


def test_probe_command_asks_for_json(tmp_path):
    cmd = build_probe_command(tmp_path / "a.mp4", binary="ffp")
    assert cmd[0] == "ffp"
    assert "json" in cmd and "-show_streams" in cmd


# --- 真的動磁碟的那幾支 ------------------------------------------------------

@pytest.mark.skipif(not POSIX_ONLY, reason="假 ffmpeg 是 POSIX shell script")
def test_extract_audio_writes_the_final_name_only_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("KARATUBE_FFMPEG", str(write_fake_ffmpeg(tmp_path / "ffmpeg", GOOD_FFMPEG)))
    dst = tmp_path / "original_audio.mp3"
    assert extract_audio(tmp_path / "src.mp4", dst) is True
    assert dst.exists() and dst.stat().st_size > 0
    assert not (tmp_path / "original_audio.mp3.part").exists()


@pytest.mark.skipif(not POSIX_ONLY, reason="假 ffmpeg 是 POSIX shell script")
def test_failed_extraction_leaves_no_half_file(tmp_path, monkeypatch):
    """留下半份檔案的後果很具體：`is_song_complete()` 只看檔案存不存在，
    那首歌會變成「完整但播不出聲音」。"""
    monkeypatch.setenv("KARATUBE_FFMPEG", str(write_fake_ffmpeg(tmp_path / "ffmpeg", ALWAYS_FAIL)))
    dst = tmp_path / "original_audio.mp3"
    assert extract_audio(tmp_path / "src.mp4", dst) is False
    assert not dst.exists()
    assert not dst.with_suffix(".mp3.part").exists()


@pytest.mark.skipif(not POSIX_ONLY, reason="假 ffmpeg 是 POSIX shell script")
def test_video_falls_back_to_re_encoding_when_copy_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("KARATUBE_FFMPEG",
                       str(write_fake_ffmpeg(tmp_path / "ffmpeg", FAILING_COPY)))
    dst = tmp_path / "original_video.mp4"
    assert prepare_video(tmp_path / "src.mkv", dst) is True
    assert dst.read_bytes() == b"fake-encoded"


@pytest.mark.skipif(not POSIX_ONLY, reason="假 ffmpeg 是 POSIX shell script")
def test_video_giving_up_is_not_an_exception(tmp_path, monkeypatch):
    """影像失敗不算整首失敗：那首歌變成「沒有 MV」，情境背景會接手。"""
    monkeypatch.setenv("KARATUBE_FFMPEG", str(write_fake_ffmpeg(tmp_path / "ffmpeg", ALWAYS_FAIL)))
    assert prepare_video(tmp_path / "src.mkv", tmp_path / "original_video.mp4") is False


def test_missing_ffmpeg_is_reported_as_failure_not_a_crash(tmp_path, monkeypatch):
    """ffmpeg 不在（精簡安裝、Windows 手動裝）要回 False，讓呼叫端講出人話。"""
    monkeypatch.setenv("KARATUBE_FFMPEG", str(tmp_path / "definitely-not-here"))
    assert extract_audio(tmp_path / "src.mp4", tmp_path / "out.mp3") is False


def test_missing_ffprobe_says_so_instead_of_blaming_the_file(tmp_path, monkeypatch):
    """
    「這台機器少裝了 ffprobe」與「你這個影片壞了」的下一步完全不同。
    混為一談的話，畫面會對著每一個檔案說「讀不懂」，而使用者會去怪他的影片。
    """
    monkeypatch.setenv("KARATUBE_FFPROBE", str(tmp_path / "definitely-not-here"))
    with pytest.raises(RuntimeError) as excinfo:
        probe_media(tmp_path / "a.mp4")
    assert "ffprobe" in str(excinfo.value)
