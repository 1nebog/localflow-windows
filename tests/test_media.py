"""Пауза музыки без Windows: подменный «пульт» с плеерами."""

from types import SimpleNamespace

from localflow.win.media import PAUSED, PLAYING, MediaPause


class Player:
    def __init__(self, app_id, status):
        self.source_app_user_model_id = app_id
        self.status = status
        self.commands = []

    def get_playback_info(self):
        return SimpleNamespace(playback_status=self.status)

    async def try_pause_async(self):
        self.commands.append("pause")
        self.status = PAUSED
        return True

    async def try_play_async(self):
        self.commands.append("play")
        self.status = PLAYING
        return True


def make(*players):
    manager = SimpleNamespace(get_sessions=lambda: list(players))

    async def factory():
        return manager
    return MediaPause(manager_factory=factory)


def test_pauses_only_playing_and_resumes_only_them():
    spotify, paused_video = Player("Spotify", PLAYING), Player("Chrome", PAUSED)
    m = make(spotify, paused_video)
    m.pause()
    m.wait()
    assert spotify.status == PAUSED and paused_video.commands == []
    m.resume()
    m.wait()
    assert spotify.commands == ["pause", "play"]
    assert paused_video.commands == []         # стояло на паузе до нас — не трогаем


def test_nothing_playing_nothing_resumed():
    player = Player("Spotify", PAUSED)
    m = make(player)
    m.pause()
    m.resume()
    m.wait()
    assert player.commands == []


def test_user_started_music_himself_during_recording():
    player = Player("Spotify", PLAYING)
    m = make(player)
    m.pause()
    m.wait()
    player.status = PLAYING                   # включил сам, пока диктовал
    m.resume()
    m.wait()
    assert player.commands == ["pause"]


def test_fast_tap_keeps_order():
    player = Player("Spotify", PLAYING)
    m = make(player)
    for _ in range(5):
        m.pause()
        m.resume()
    m.wait()
    assert player.status == PLAYING and player.commands[-1] == "play"


def test_disabled_does_not_pause():
    player = Player("Spotify", PLAYING)
    m = make(player)
    m.enabled = False
    m.pause()
    m.resume()
    m.wait()
    assert player.commands == []


def test_no_media_controls_on_this_windows():
    async def broken():
        raise OSError("класс не зарегистрирован")
    m = MediaPause(manager_factory=broken)
    m.pause()
    m.wait()
    assert m._broken
    m.pause()                                  # дальше даже не пытается
    m.resume()
