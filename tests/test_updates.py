"""Проверка обновлений без интернета."""

from localflow import __version__, updates


def release(tag, assets=()):
    return lambda timeout: {"tag_name": tag, "assets": [
        {"browser_download_url": u} for u in assets]}


def test_versions_compare_as_numbers():
    assert updates.is_newer("v0.10.0", "0.9.9")
    assert updates.is_newer("0.1.1", "0.1")
    assert not updates.is_newer("v0.1.0", "0.1.0")
    assert not updates.is_newer("v0.0.9", "0.1.0")
    assert not updates.is_newer("nightly", "0.1.0")


def test_latest_already_installed():
    r = updates.check(release("v" + __version__))
    assert r["state"] == "latest" and r["latest"] == __version__


def test_new_version_links_straight_to_installer():
    exe = "https://github.com/1nebog/localflow-windows/releases/download/v9.0.0/LocalFlow-Setup-9.0.0.exe"
    r = updates.check(release("v9.0.0", [exe + ".sha256", exe]))
    assert r == {"state": "available", "current": __version__, "latest": "9.0.0", "url": exe}


def test_foreign_download_link_is_ignored():
    r = updates.check(release("v9.0.0", ["https://evil.example/LocalFlow-Setup.exe"]))
    assert r["state"] == "available" and r["url"] == updates.RELEASES_URL
    assert updates.safe_url("https://evil.example/x.exe") == updates.RELEASES_URL


def test_no_internet_is_not_a_crash():
    def offline(timeout):
        raise OSError("нет сети")
    assert updates.check(offline)["state"] == "error"
