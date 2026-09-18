"""Диктовка целиком, без Windows: клавиша → запись → распознавание → вставка."""

import os
import tempfile
import threading
import time

import numpy as np
import pytest

os.environ.setdefault("LOCALFLOW_HOME", tempfile.mkdtemp(prefix="lf-test-"))

from localflow import core  # noqa: E402
from localflow.dictation import Dictation  # noqa: E402
from localflow.keys import (  # noqa: E402
    ENTER, ESCAPE, HOTKEY_CHORD, HOTKEY_DOWN, HOTKEY_UP, KeyboardLogic,
)

SR = core.SAMPLE_RATE


def voice(sec=2.0, level=0.3):
    rng = np.random.default_rng(1)
    return rng.uniform(-level, level, int(sec * SR)).astype(np.float32)


class FakeRecorder:
    def __init__(self):
        self.audio = voice()
        self.level = 0.0
        self._rec = False
        self.fail = False
        self.starts = 0
        self.other_apis = 0          # сколько ещё подсистем можно попробовать
        self.heard = None            # сколько записи «уже есть» к моменту снимка

    @property
    def raw_peak(self):
        return float(np.max(np.abs(self.audio))) if len(self.audio) else 0.0

    def switch_api(self):
        if not self.other_apis:
            return False
        self.other_apis -= 1
        return True

    @property
    def is_recording(self):
        return self._rec

    def start(self):
        if self.fail:
            raise OSError("нет микрофона")
        self.starts += 1
        self._rec = True

    def stop(self):
        self._rec = False
        return self.audio

    def tail(self, sec):
        return self.audio[-int(sec * SR):]

    def snapshot(self):
        return self.audio[:self.heard] if self.heard is not None else self.audio


class FakeTranscriber:
    def __init__(self):
        self.text = "Привет, мир."
        self.is_ready = True
        self.is_loading = False
        self.load_error = None
        self.warmup_sec = 0.3
        self.last_language = "ru"
        self.last_verbatim = False
        self.hint_terms = []
        self.delay = 0.0
        self.calls = 0
        self.snippets = 0

    def transcribe(self, audio):
        self.calls += 1
        time.sleep(self.delay)
        return self.text

    def transcribe_snippet(self, audio_i16, prompt=None):
        self.snippets += 1
        return "отмена"

    def load_async(self, *a, **kw):
        self.is_loading = True

        def _finish():
            time.sleep(0.2)
            self.is_loading, self.is_ready = False, True
        threading.Thread(target=_finish, daemon=True).start()

    def unload(self):
        self.is_ready = False


class FakePaste:
    def __init__(self):
        self.pasted = []
        self.undos = 0
        self.ok = True
        self.clipboard = None

    def paste_text(self, text, method="clipboard"):
        self.pasted.append((text, method))
        return self.ok

    def undo(self):
        self.undos += 1

    def set_clipboard(self, text):
        self.clipboard = text


class FakeSounds:
    def __init__(self):
        self.played = []

    def play(self, name):
        self.played.append(name)


@pytest.fixture
def d(monkeypatch):
    monkeypatch.setattr(core, "_UI_LANG", "ru")
    notes = []
    dic = Dictation(FakeTranscriber(), FakeRecorder(), KeyboardLogic(),
                    paste=FakePaste(), sounds=FakeSounds(),
                    frontmost_app=lambda: "Notepad",
                    notify=lambda t, m: notes.append((t, m)))
    dic.style = "normal"
    dic.notes = notes
    yield dic
    dic.recorder._rec = False


def wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def hold(d, sec=0.4):
    d.on_key_event(HOTKEY_DOWN)
    time.sleep(sec)
    d.on_key_event(HOTKEY_UP)


def test_hold_speak_release_pastes(d):
    hold(d)
    assert wait(lambda: d.paste.pasted)
    assert d.paste.pasted == [("Привет, мир.", "clipboard")]
    assert d.sounds.played[:2] == ["start", "stop"]
    assert d.history[0]["text"] == "Привет, мир."
    assert not d.keys.enter_armed
    assert wait(lambda: not d._busy)


def test_tap_locks_and_second_press_stops(d):
    hold(d, 0.05)
    assert d.recorder.is_recording and d._locked
    d.on_key_event(HOTKEY_DOWN)
    d.on_key_event(HOTKEY_UP)
    assert wait(lambda: d.paste.pasted)


def test_escape_cancels_recording(d):
    d.on_key_event(HOTKEY_DOWN)
    d.on_key_event(ESCAPE)
    d.on_key_event(HOTKEY_UP)
    time.sleep(0.1)
    assert not d.recorder.is_recording
    assert d.paste.pasted == [] and "cancel" in d.sounds.played
    assert d.transcriber.calls == 0


def test_escape_right_after_paste_undoes(d):
    hold(d)
    assert wait(lambda: d.paste.pasted) and wait(lambda: not d._busy)
    d.on_key_event(ESCAPE)
    assert d.paste.undos == 1
    d.on_key_event(ESCAPE)            # второй раз — уже нечего откатывать
    assert d.paste.undos == 1


def test_enter_finishes_locked_recording(d):
    hold(d, 0.05)
    assert d.keys.enter_armed
    d.on_key_event(ENTER)
    assert wait(lambda: d.paste.pasted)


def test_shortcut_with_hotkey_quietly_cancels(d):
    d.on_key_event(HOTKEY_DOWN)
    d.on_key_event(HOTKEY_CHORD)      # правый Ctrl+C
    d.on_key_event(HOTKEY_UP)
    assert not d.recorder.is_recording
    assert "cancel" not in d.sounds.played and d.transcriber.calls == 0


def test_chord_after_first_second_is_ignored(d, monkeypatch):
    monkeypatch.setattr(Dictation, "CHORD_CANCEL_SEC", 0.05)
    d.on_key_event(HOTKEY_DOWN)
    time.sleep(0.1)
    d.on_key_event(HOTKEY_CHORD)
    assert d.recorder.is_recording
    time.sleep(0.25)                  # удержание, а не короткое нажатие
    d.on_key_event(HOTKEY_UP)
    assert wait(lambda: d.paste.pasted)


def test_silence_is_not_transcribed(d):
    d.recorder.audio = np.full(SR, 0.001, np.float32)
    hold(d)
    time.sleep(0.2)
    assert d.transcriber.calls == 0 and d.paste.pasted == []


def test_blocked_microphone_gives_exact_zeros(d):
    d.recorder.audio = np.zeros(SR * 2, np.float32)
    hold(d)
    assert d.notes and d.notes[-1][0] == "Микрофон недоступен"
    assert "Конфиденциальность" in d.notes[-1][1]


def test_near_silent_microphone_tries_another_subsystem_first(d):
    # Не ровный ноль, а крошечный шум — так было на ноутбуке с Realtek
    d.recorder.audio = np.random.default_rng(0).uniform(-3e-5, 3e-5, SR * 2).astype(np.float32)
    d.recorder.other_apis = 1
    hold(d)
    assert d.notes == [] and d.recorder.other_apis == 0 and d.transcriber.calls == 0
    hold(d)                                     # и там пусто — тогда подсказка
    assert d.notes[-1][0] == "Микрофон недоступен"


def test_too_short_recording_ignored(d):
    d.recorder.audio = voice(0.1)
    hold(d)
    time.sleep(0.1)
    assert d.transcriber.calls == 0


def test_microphone_error_notifies(d):
    d.recorder.fail = True
    d.on_key_event(HOTKEY_DOWN)
    assert d.notes[-1][0] == "Микрофон недоступен"
    assert not d.keys.enter_armed


def test_press_during_transcription_starts_next_recording(d):
    d.transcriber.delay = 0.4
    hold(d)
    assert wait(lambda: d._busy)
    d.on_key_event(HOTKEY_DOWN)       # зажал, пока распознаётся первая
    assert wait(lambda: d.recorder.starts == 2)
    time.sleep(0.35)
    d.on_key_event(HOTKEY_UP)
    assert wait(lambda: len(d.paste.pasted) == 2, timeout=4)


def test_recording_while_model_wakes_up(d):
    d.transcriber.is_ready = False    # выгружена после простоя
    hold(d)
    assert d.recorder.starts == 1
    assert wait(lambda: d.paste.pasted)


def test_broken_model_does_not_record(d):
    d.transcriber.is_ready = False
    d.transcriber.load_error = "Нет модели"
    d.on_key_event(HOTKEY_DOWN)
    assert d.recorder.starts == 0 and d.notes[-1][1] == "Нет модели"


def test_paste_blocked_by_admin_window(d):
    d.paste.ok = False
    hold(d)
    assert wait(lambda: d.notes)
    assert d.notes[-1][0] == "Не вставилось"
    assert wait(lambda: not d._busy)
    d.on_key_event(ESCAPE)            # откатывать нечего — вставки не было
    assert d.paste.undos == 0


def test_voice_cancel_in_final_text(d):
    d.transcriber.text = "Напиши письмо маме. Отмена."
    hold(d)
    assert wait(lambda: not d._busy and d.transcriber.calls)
    assert d.paste.pasted == []


def test_messenger_gets_chat_style(d):
    d.style = "auto"
    d._frontmost_app = lambda: "Telegram"
    d.transcriber.text = "Привет. Как дела."
    hold(d)
    assert wait(lambda: d.paste.pasted)
    assert d.paste.pasted[0][0] == core.casual_tone("Привет. Как дела.")


def test_lock_autostops_after_silence(d, monkeypatch):
    monkeypatch.setattr(Dictation, "LOCK_AUTOSTOP", 0.3)
    monkeypatch.setattr(Dictation, "VOICE_CANCEL_TRIES", 0)
    hold(d, 0.05)
    d.recorder.level = 0.3            # говорит
    time.sleep(0.25)
    d.recorder.level = 0.0            # замолчал
    assert wait(lambda: d.paste.pasted, timeout=3)


def test_lock_recognizes_during_pause_before_autostop(d, monkeypatch):
    monkeypatch.setattr(Dictation, "LOCK_AUTOSTOP", 1.0)
    monkeypatch.setattr(Dictation, "SPEC_AFTER", 0.1)
    monkeypatch.setattr(Dictation, "VOICE_CANCEL_TRIES", 0)
    d.transcriber.delay = 0.5         # распознавание небыстрое
    d.recorder.audio = np.concatenate([voice(1.0), np.zeros(SR, np.float32)])
    hold(d, 0.05)
    d.recorder.level = 0.3
    time.sleep(0.2)
    d.recorder.level = 0.0            # замолчал: распознаём, не дожидаясь автостопа
    assert wait(lambda: d.transcriber.calls == 1, timeout=1)
    assert d.recorder.is_recording    # запись ещё идёт, а распознавание уже
    assert wait(lambda: d.paste.pasted, timeout=3)
    assert d.transcriber.calls == 1   # после автостопа второй раз не считали


def test_speech_after_early_recognition_is_not_lost(d, monkeypatch):
    monkeypatch.setattr(Dictation, "SPEC_AFTER", 0.1)
    monkeypatch.setattr(Dictation, "VOICE_CANCEL_TRIES", 0)
    d.recorder.audio = voice(2.0)
    d.recorder.heard = SR             # заготовка — только по первой секунде
    hold(d, 0.05)
    d.recorder.level = 0.3
    time.sleep(0.2)
    d.recorder.level = 0.0
    assert wait(lambda: d.transcriber.calls == 1, timeout=1)
    d.on_key_event(HOTKEY_DOWN)       # остановил сам; после паузы ещё говорил
    d.on_key_event(HOTKEY_UP)
    assert wait(lambda: d.paste.pasted, timeout=3)
    assert d.transcriber.calls == 2   # распознали всю запись заново


def test_live_voice_cancel_only_on_fast_computer(d, monkeypatch):
    monkeypatch.setattr(Dictation, "VOICE_CANCEL_AFTER", 0.05)
    monkeypatch.setattr(Dictation, "VOICE_CANCEL_EVERY", 0.05)
    d.transcriber.warmup_sec = 2.5    # медленный компьютер
    hold(d, 0.05)
    d.recorder.level = 0.3
    time.sleep(0.25)
    d.recorder.level = 0.0
    time.sleep(0.4)
    assert d.transcriber.snippets == 0 and d.recorder.is_recording
    d.on_key_event(ESCAPE)

    d.transcriber.warmup_sec = 0.2    # быстрый
    hold(d, 0.05)
    d.recorder.level = 0.3
    time.sleep(0.25)
    d.recorder.level = 0.0
    assert wait(lambda: not d.recorder.is_recording, timeout=3)
    assert d.transcriber.snippets >= 1 and d.paste.pasted == []


def test_recovery_after_crash_goes_to_clipboard(d):
    core.save_recovery(voice(1.0))
    d.recover_lost_dictation()
    assert d.paste.clipboard == "Привет, мир."
    assert not core.RECOVERY_PATH.exists()


def test_idle_unload(d, monkeypatch):
    d.idle_unload_min = 1
    assert not d.maybe_unload_idle()
    d._last_use_t -= 61
    assert d.maybe_unload_idle() and not d.transcriber.is_ready


# --- Умное исправление, перевод, правка голосом, пауза музыки ------------------

class FakePolisher:
    def __init__(self):
        self.mode = "fast"
        self.ready = True
        self.is_loading = False
        self.calls = []
        self.ok = True

    @property
    def enabled(self):
        return self.mode != "off"

    def set_mode(self, mode, on_done=None):
        self.calls.append(("set_mode", mode))

    def polish(self, text, lang="ru", ctx=None):
        self.calls.append(("polish", text))
        return text.replace("короче ", "").capitalize()

    def translate(self, text, target, ctx=None):
        self.calls.append(("translate", target, text))
        return ("Hello, world." if self.ok else text), self.ok

    def rewrite(self, text, instruction, bounds=(0.3, 2.5), ctx=None):
        self.calls.append(("rewrite", text))
        return ("Коротко." if self.ok else text), self.ok

    def unload(self):
        self.ready = False


class FakeMedia:
    def __init__(self):
        self.events = []

    def pause(self):
        self.events.append("pause")

    def resume(self):
        self.events.append("resume")


@pytest.fixture
def dp(d):
    d.polisher = FakePolisher()
    d.media = FakeMedia()
    return d


def test_polish_before_style_and_raw_kept_in_history(dp):
    dp.transcriber.text = "короче привет мир"
    hold(dp)
    assert wait(lambda: dp.paste.pasted)
    assert dp.paste.pasted[0][0] == "Привет мир"
    assert dp.history[0] == {**dp.history[0], "text": "Привет мир", "raw": "короче привет мир"}


def test_snippets_are_not_polished(dp):
    dp.transcriber.last_verbatim = True
    dp.transcriber.text = "моя почта"
    hold(dp)
    assert wait(lambda: dp.paste.pasted)
    assert dp.paste.pasted[0][0] == "моя почта" and dp.polisher.calls == []


def test_polisher_off_does_nothing(dp):
    dp.polisher.mode = "off"
    dp.translate_to = "en"
    hold(dp)
    assert wait(lambda: dp.paste.pasted)
    assert dp.paste.pasted[0][0] == "Привет, мир." and dp.polisher.calls == []


def test_translate_mode_and_failed_translation_is_polished(dp):
    dp.translate_to = "en"
    hold(dp)
    assert wait(lambda: dp.paste.pasted)
    assert dp.paste.pasted[0][0] == "Hello, world."
    dp.polisher.ok = False
    hold(dp)
    assert wait(lambda: len(dp.paste.pasted) == 2)
    assert [c[0] for c in dp.polisher.calls[-2:]] == ["translate", "polish"]


def test_voice_command_translates_one_phrase(dp):
    dp.transcriber.text = "По-английски, привет мир"
    hold(dp)
    assert wait(lambda: dp.paste.pasted)
    assert dp.polisher.calls[0] == ("translate", "en", "привет мир")


def test_voice_edit_rewrites_last_paste(dp):
    hold(dp)
    assert wait(lambda: dp.paste.pasted) and wait(lambda: not dp._busy)
    dp.transcriber.text = "Покороче"
    hold(dp)
    assert wait(lambda: len(dp.paste.pasted) == 2) and wait(lambda: not dp._busy)
    assert dp.paste.undos == 1 and dp.paste.pasted[1][0] == "Коротко."
    assert dp.history[0]["text"] == "Коротко." and dp.history[0]["raw"] == "Привет, мир."


def test_voice_edit_failure_leaves_text(dp):
    hold(dp)
    assert wait(lambda: dp.paste.pasted) and wait(lambda: not dp._busy)
    dp.polisher.ok = False
    dp.transcriber.text = "Покороче"
    hold(dp)
    assert wait(lambda: not dp._busy and dp.transcriber.calls == 2)
    assert dp.paste.undos == 0 and len(dp.paste.pasted) == 1


def test_voice_edit_only_right_after_paste(dp, monkeypatch):
    monkeypatch.setattr(Dictation, "VOICE_EDIT_WINDOW", 0.0)
    hold(dp)
    assert wait(lambda: dp.paste.pasted) and wait(lambda: not dp._busy)
    dp.transcriber.text = "Покороче"
    hold(dp)
    assert wait(lambda: len(dp.paste.pasted) == 2)
    assert dp.paste.undos == 0                # обычная диктовка слова «покороче»


def test_waits_for_waking_polisher(dp):
    dp.polisher.ready, dp.polisher.is_loading = False, True

    def wake():
        time.sleep(0.3)
        dp.polisher.ready, dp.polisher.is_loading = True, False
    threading.Thread(target=wake, daemon=True).start()
    dp.transcriber.text = "короче привет"
    hold(dp)
    assert wait(lambda: dp.paste.pasted)
    assert dp.paste.pasted[0][0] == "Привет"


def test_recording_wakes_sleeping_polisher(dp):
    dp.polisher.ready = False
    hold(dp)
    assert ("set_mode", "fast") in dp.polisher.calls


def test_idle_unload_frees_both_models(dp):
    dp.idle_unload_min = 1
    dp._last_use_t -= 61
    assert dp.maybe_unload_idle()
    assert not dp.polisher.ready and not dp.transcriber.is_ready


def test_music_paused_for_recording(dp):
    hold(dp)
    assert wait(lambda: dp.paste.pasted)
    assert dp.media.events == ["pause", "resume"]
    dp.on_key_event(HOTKEY_DOWN)
    dp.on_key_event(ESCAPE)
    assert dp.media.events[-2:] == ["pause", "resume"]
    dp.recorder.fail = True
    dp.on_key_event(HOTKEY_DOWN)
    assert dp.media.events[-2:] == ["pause", "resume"]
