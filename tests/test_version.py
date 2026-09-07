"""版本號與發布資訊的測試。

發布流程最容易出的錯不是「程式壞掉」，而是**版本號對不上**：
標籤打了 v1.0.1、`version.py` 還是 1.0.0、CHANGELOG 忘了寫。
發出去之後這種錯最難追（使用者回報的版本號是假的），所以在這裡先擋。

release workflow 也會再檢查一次標籤與 `version.py` 是否一致 —— 這裡守的是
「進 main 之前，version.py 與 CHANGELOG 就得一致」。
"""
import re
from pathlib import Path

from backend.version import VERSION_CODENAME, __version__, build_id, version_info

REPO_ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_version_is_semver():
    assert SEMVER.match(__version__), f"版本號不是 MAJOR.MINOR.PATCH：{__version__}"


def test_version_info_shape():
    info = version_info()
    assert info["name"] == "KaraTube"
    assert info["version"] == __version__
    assert info["codename"] == VERSION_CODENAME
    # 沒設 KARATUBE_BUILD 時不要放一個空字串的 build 欄位進去
    if not build_id():
        assert "build" not in info


def test_build_id_comes_from_env(monkeypatch):
    monkeypatch.setenv("KARATUBE_BUILD", "  abc1234  ")
    assert build_id() == "abc1234"
    assert version_info()["build"] == "abc1234"


def test_changelog_top_entry_matches_version():
    """CHANGELOG 最上面那一筆必須就是目前的版本號。"""
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    entries = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, flags=re.MULTILINE)
    assert entries, "CHANGELOG.md 裡找不到任何 '## [x.y.z]' 版本段落"
    assert entries[0] == __version__, (
        f"CHANGELOG 最新一筆是 {entries[0]}，但 backend/version.py 是 {__version__}")


def test_changelog_entries_are_in_descending_order():
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = [tuple(int(p) for p in v.split("."))
                for v in re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, flags=re.MULTILINE)]
    assert versions == sorted(versions, reverse=True), "CHANGELOG 的版本順序不是由新到舊"
