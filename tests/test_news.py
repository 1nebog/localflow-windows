"""«Что нового»: список изменений и показ после обновления."""

from localflow import __version__, news


def test_newest_entry_is_this_version():
    assert news.NEWS[0][0] == __version__


def test_every_version_in_every_language():
    for v, texts in news.NEWS:
        assert set(texts) == {"ru", "en", "uk", "de"}, v
        counts = {len(items) for items in texts.values()}
        assert len(counts) == 1 and counts.pop() > 0, v


def test_unknown_language_falls_back_to_english():
    assert news.for_lang("fr")[0]["items"] == news.NEWS[0][1]["en"]


def test_first_install_does_not_show_news():
    cfg = {}
    assert not news.show_after_start(cfg, "0.1.6")
    assert cfg["news_seen"] == "0.1.6"


def test_update_shows_news_once():
    cfg = {"intro_shown": True}                      # стояла 0.1.5 и раньше
    assert news.show_after_start(cfg, "0.1.6")
    assert not news.show_after_start(cfg, "0.1.6")   # следующий запуск — тихо
    assert news.show_after_start(cfg, "0.1.7")       # следующее обновление
