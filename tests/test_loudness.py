"""
響度量測 (EBU R128 / ITU-R BS.1770-4) 單元測試。

全部用合成訊號，不需要音檔、不需要 librosa / torch，所以 CI 上跑得動。
規格書給了明確的驗收點（48kHz 參考係數、-20 dBFS 1kHz 正弦 = -20 LUFS），
這些是真的能證明實作正確的斷言，不是「跑起來沒爆」而已。
"""
import math

import numpy as np
import pytest

from backend.pipeline import loudness as L


def sine(freq: float, sample_rate: int, duration: float, amplitude: float,
         channels: int = 2) -> np.ndarray:
    t = np.arange(int(sample_rate * duration)) / sample_rate
    mono = amplitude * np.sin(2 * np.pi * freq * t)
    return mono.reshape(-1, 1).repeat(channels, axis=1)


# --- K 加權濾波器 ---

def test_shelf_coefficients_match_bs1770_reference_at_48k():
    """BS.1770-4 表 1 直接列出 48kHz 的係數，推導對的話要對得上。"""
    b, a = L.shelf_coefficients(48000)
    assert b == pytest.approx([1.53512485958697, -2.69169618940638, 1.19839281085285], abs=1e-9)
    assert a == pytest.approx([1.0, -1.69065929318241, 0.73248077421585], abs=1e-9)


def test_highpass_coefficients_match_bs1770_reference_at_48k():
    b, a = L.highpass_coefficients(48000)
    assert b == pytest.approx([1.0, -2.0, 1.0], abs=1e-12)
    assert a == pytest.approx([1.0, -1.99004745483398, 0.99007225036621], abs=1e-9)


def test_k_weighting_shape():
    """低頻被 RLB 高通壓掉、高頻被棚增益抬起來，這就是 K 加權的形狀。"""
    sr = 48000

    def gain_db(freq):
        x = sine(freq, sr, 0.3, 0.5)
        y = L.k_weight(x, sr)
        skip = sr // 10  # 濾波器暫態不算
        return 20 * math.log10(
            float(np.sqrt(np.mean(y[skip:] ** 2)) / np.sqrt(np.mean(x[skip:] ** 2))))

    assert gain_db(60) < -2.0
    assert gain_db(1000) == pytest.approx(0.7, abs=0.5)
    assert gain_db(10000) == pytest.approx(4.0, abs=0.5)


# --- 整合響度 ---

def test_stereo_1khz_at_minus_20_dbfs_is_minus_20_lufs():
    """規格書的校準點：-20 dBFS 的 1kHz 正弦（雙聲道同相）= -20 LUFS。"""
    sr = 48000
    lufs = L.integrated_loudness(sine(1000, sr, 1.0, 10 ** (-20 / 20)), sr)
    assert lufs == pytest.approx(-20.0, abs=0.2)


def test_mono_is_3db_quieter_than_stereo():
    """單聲道只有一半能量，響度差 3.01 LU。"""
    sr = 16000
    stereo = sine(1000, sr, 1.0, 0.1)
    assert (L.integrated_loudness(stereo, sr)
            - L.integrated_loudness(stereo[:, 0], sr)) == pytest.approx(3.01, abs=0.1)


def test_doubling_amplitude_adds_six_lu():
    sr = 16000
    x = sine(440, sr, 1.0, 0.1)
    assert (L.integrated_loudness(x * 2, sr)
            - L.integrated_loudness(x, sr)) == pytest.approx(6.02, abs=0.05)


def test_silence_is_negative_infinity():
    assert L.integrated_loudness(np.zeros((16000, 2)), 16000) == float("-inf")


def test_signal_shorter_than_one_block_is_negative_infinity():
    """不足 400ms 連一個量測區塊都湊不出來，只能誠實回報量不到。"""
    sr = 16000
    assert L.integrated_loudness(sine(1000, sr, 0.2, 0.1), sr) == float("-inf")


def test_gating_ignores_leading_silence():
    """
    這是閘門存在的理由：歌曲前後的無聲不能把整首的響度拉低。
    沒有閘門的話「2 秒靜音 + 2 秒歌聲」會比「2 秒歌聲」低 3 dB。
    """
    sr = 16000
    loud = sine(1000, sr, 2.0, 0.1)
    with_silence = np.concatenate([np.zeros((sr * 2, 2)), loud])
    assert L.integrated_loudness(with_silence, sr) == pytest.approx(
        L.integrated_loudness(loud, sr), abs=0.5)


def test_mono_and_2d_input_are_equivalent():
    sr = 16000
    mono = sine(1000, sr, 1.0, 0.1, channels=1)
    assert L.integrated_loudness(mono[:, 0], sr) == pytest.approx(
        L.integrated_loudness(mono, sr), abs=1e-9)


def test_rejects_3d_input():
    with pytest.raises(ValueError):
        L.as_2d(np.zeros((4, 2, 2)))


# --- 峰值與增益計算 ---

def test_peak_dbfs():
    assert L.peak_dbfs(np.array([0.5, -1.0, 0.25])) == pytest.approx(0.0)
    assert L.peak_dbfs(np.array([0.1, -0.1])) == pytest.approx(-20.0, abs=0.01)
    assert L.peak_dbfs(np.zeros(10)) == float("-inf")
    assert L.peak_dbfs(np.array([])) == float("-inf")


def test_gain_lifts_quiet_song_to_target():
    assert L.gain_db_for_target(-20.0, -14.0) == pytest.approx(6.0)


def test_gain_attenuates_loud_song():
    assert L.gain_db_for_target(-5.0, -14.0) == pytest.approx(-9.0)


def test_gain_is_clamped_both_ways():
    """差太多通常代表檔案有問題，硬拉只會把底噪一起放大。"""
    assert L.gain_db_for_target(-60.0, -14.0) == pytest.approx(12.0)
    assert L.gain_db_for_target(20.0, -14.0) == pytest.approx(-12.0)


def test_gain_respects_peak_headroom():
    """峰值只剩 2 dB 空間時就不能再拉 6 dB，否則播放端會削波破音。"""
    assert L.gain_db_for_target(-20.0, -14.0, peak=-2.0) == pytest.approx(1.0)


def test_peak_headroom_never_forces_extra_attenuation():
    """已經削到 0 dBFS 的母帶不該因為峰值保護被硬壓到 -12 dB 以下。"""
    gain = L.gain_db_for_target(-8.0, -14.0, peak=0.0)
    assert gain == pytest.approx(-6.0)


def test_unmeasurable_loudness_means_no_gain():
    """量不到就不要動它 —— 亂調比不調更糟。"""
    assert L.gain_db_for_target(float("-inf"), -14.0) == 0.0
    assert L.gain_db_for_target(float("nan"), -14.0) == 0.0
    assert L.gain_db_for_target(None, -14.0) == 0.0


# --- 整包分析 ---

def test_analyze_samples_shape():
    sr = 16000
    result = L.analyze_samples(sine(1000, sr, 1.0, 10 ** (-20 / 20)), sr, target_lufs=-14.0)
    assert result["lufs"] == pytest.approx(-20.0, abs=0.3)
    assert result["peak_dbfs"] == pytest.approx(-20.0, abs=0.1)
    assert result["target_lufs"] == -14.0
    assert result["gain_db"] == pytest.approx(6.0, abs=0.3)
    assert result["sample_rate"] == sr


def test_analyze_samples_on_silence_reports_no_measurement():
    result = L.analyze_samples(np.zeros((16000, 2)), 16000)
    assert result["lufs"] is None
    assert result["peak_dbfs"] is None
    assert result["gain_db"] == 0.0


def test_analyze_audio_file_missing_file_returns_none(tmp_path):
    assert L.analyze_audio_file(tmp_path / "nope.mp3") is None


def test_numpy_lfilter_matches_scipy_when_available():
    """
    備援濾波器（沒有 scipy 的環境）必須跟 scipy 算出一樣的結果，
    否則 CI 綠燈不代表正式環境的量測正確。
    """
    scipy_signal = pytest.importorskip("scipy.signal")
    b, a = L.shelf_coefficients(48000)
    x = np.random.RandomState(0).randn(2000, 2)
    expected = scipy_signal.lfilter(b, a, x, axis=0)
    assert L._lfilter_numpy(b, a, x) == pytest.approx(expected, abs=1e-9)


def test_numpy_lfilter_impulse_response():
    """沒有 scipy 也要能證明備援濾波器是對的：脈衝響應必須等於差分方程的展開。"""
    b = np.array([0.5, 0.25, 0.125])
    a = np.array([1.0, -0.5, 0.25])
    impulse = np.zeros((6, 1))
    impulse[0, 0] = 1.0
    y = L._lfilter_numpy(b, a, impulse)[:, 0]

    expected = []
    for n in range(6):
        acc = sum(b[k] * (1.0 if n - k == 0 else 0.0) for k in range(3) if n - k >= 0)
        acc -= sum(a[k] * expected[n - k] for k in range(1, 3) if n - k >= 0)
        expected.append(acc)
    assert y == pytest.approx(expected, abs=1e-12)
