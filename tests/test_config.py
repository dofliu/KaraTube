"""部署設定（環境變數）的測試。

容器與反向代理的情境全靠這幾個環境變數，而它們最容易在半夜的機房裡被塞進
奇怪的值（`KARATUBE_PORT=8080 ` 帶空白、`""`、`"auto"`）。
機器可以印錯網址，但**不能因為一個環境變數打錯就開不起來**。
"""
from backend.config import PORT, PUBLIC_PORT, _int_env


def test_int_env_reads_and_trims(monkeypatch):
    monkeypatch.setenv("KARATUBE_TEST_PORT", " 9000 ")
    assert _int_env("KARATUBE_TEST_PORT", 8080) == 9000


def test_int_env_falls_back_on_garbage(monkeypatch):
    for junk in ("", "auto", "80.5", "八千"):
        monkeypatch.setenv("KARATUBE_TEST_PORT", junk)
        assert _int_env("KARATUBE_TEST_PORT", 8080) == 8080


def test_int_env_falls_back_when_unset(monkeypatch):
    monkeypatch.delenv("KARATUBE_TEST_PORT", raising=False)
    assert _int_env("KARATUBE_TEST_PORT", 8080) == 8080


def test_public_port_defaults_to_listen_port():
    """沒特別設定時，對外公告的 port 就是監聽的那個。"""
    assert PUBLIC_PORT == PORT
