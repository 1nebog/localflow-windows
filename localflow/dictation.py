"""Диктовка: клавиша → запись → распознавание → вставка.

Перенос записи и вставки из `LocalFlowApp` версии для Mac: удержание и
«замок» по короткому нажатию, Esc — отмена, Esc сразу после вставки —
откат, Enter — «готово», голосовая отмена, автостоп замка по тишине,
порог речи над шумом улицы, резервная запись на случай сбоя, стиль текста.

Всё, что зависит от Windows (перехват клавиш, вставка, звуки, таблетка),
приходит снаружи — так эту логику можно проверить без Windows.

Отличия от Mac:
- при зажатой клавише диктовки нажали другую клавишу в первую секунду —
  это сочетание (правый Ctrl+C), а не диктовка: запись тихо отменяется;
- живая голосовая отмена включается, только если компьютер распознаёт
  быстро: на медленном каждая проверка тормозила бы основную работу;
- если микрофон запрещён в настройках приватности Windows, запись идёт,
  но звука в ней нет вовсе — об этом говорим прямо.
"""

import logging
import re
import threading
import time
from collections import deque

import numpy as np

from . import core, strings
from .core import (
    LANG_NAMES, MIN_DURATION_SEC, SAMPLE_RATE, SILENCE_PEAK_LEVEL, add_stats,
    casual_tone, clear_recovery, email_tone, extract_translate_cmd,
    is_hallucination, is_messenger_app, is_voice_cancel, load_recovery,
    notes_tone, prune_history, save_history, save_recovery, structure_by_voice,
    title_terms, tr,
)
from .engine.tiers import live_checks_ok
from .keys import ENTER, ESCAPE, HOTKEY_CHORD, HOTKEY_DOWN, HOTKEY_UP

log = logging.getLogger("localflow")
strings.install()

N_BARS = 20   # столбиков волны в таблетке


class NullIndicator:
    """Таблетка-заглушка: пока настоящей нет, пишет состояние в журнал."""

    def show_text(self, text, locked=False, glow="blue"):
        log.debug("[таблетка] %s (%s%s)", text, glow, ", замок" if locked else "")

    def show_wave(self, levels, locked=False, glow="blue"):
        pass

    def hide(self, after=0.0):
        pass


class Dictation:
    TAP_THRESHOLD = 0.3  # секунд: короче — «тап» (замок), дольше — удержание
    UNDO_WINDOW = 10.0   # секунд после вставки, когда Esc делает откат
    # Другая клавиша при зажатой клавише диктовки в первые N секунд —
    # это сочетание клавиш, а не диктовка
    CHORD_CANCEL_SEC = 1.0

    SPEECH_LEVEL = 0.02   # RMS, ниже которого считаем «тишина»
    SILENCE_PEAK = SILENCE_PEAK_LEVEL  # пик записи, ниже — не анализируем
    # Порог речи плавает над фоном: иначе на шумной улице «тишина» после
    # речи не наступала бы никогда
    SPEECH_MARGIN = 2.5      # во сколько раз речь должна быть громче фона
    SPEECH_LEVEL_MAX = 0.06  # выше не поднимаем — иначе съест тихую речь
    NOISE_RISE = 0.003       # как быстро фон ползёт вверх (~30 c до нового)

    SILENCE_GLOW_AFTER = 2.0  # секунд полной тишины, после которых синий → красный
    LOCK_AUTOSTOP = 5.0       # секунд тишины в «замке» до автостопа

    VOICE_CANCEL_AFTER = 0.5   # секунд тишины до первой проверки
    VOICE_CANCEL_TAIL = 1.5    # сколько секунд хвоста скармливаем Whisper
    VOICE_CANCEL_TRIES = 3     # проверок на одну паузу
    VOICE_CANCEL_EVERY = 1.1   # секунд между проверками

    def __init__(self, transcriber, recorder, keys, *, paste, sounds,
                 indicator=None, frontmost_app=lambda: "",
                 window_title=lambda: "", notify=None, autodict=None,
                 history=None):
        self.transcriber = transcriber
        self.recorder = recorder
        self.keys = keys                # KeyboardLogic: Enter взводим отсюда
        self.paste = paste              # .paste_text(text, method) -> bool, .undo()
        self.sounds = sounds            # .play("start" | "stop" | "cancel")
        self.indicator = indicator or NullIndicator()
        self._frontmost_app = frontmost_app
        self._window_title = window_title
        self._notify = notify or (lambda title, msg: log.info("%s: %s", title, msg))
        self.autodict = autodict
        self.polisher = None            # умное исправление — следующий этап

        self.paste_method = "clipboard"
        self.style = core.DEFAULT_STYLE
        self.translate_to = core.DEFAULT_TRANSLATE_TO
        self.voice_structure = True
        self.app_profiles: dict = {}
        self.idle_unload_min = core.DEFAULT_IDLE_UNLOAD_MIN
        self.history: list[dict] = history if history is not None else []
        self.on_history_changed = None

        self._busy = False         # идёт распознавание
        self._hotkey_down = False  # клавиша диктовки зажата прямо сейчас
        self._locked = False       # запись «на замке» (короткое нажатие)
        self._press_t = 0.0
        self._last_paste_t = 0.0
        self._undo_armed = False
        self._last_paste_text = ""
        self._last_use_t = time.monotonic()
        self._active_app = ""
        self._active_title = ""
        self._heard_speech = False
        self._noise_floor = 0.0
        self._stop_lock = threading.Lock()

    # --- События клавиатуры -------------------------------------------------

    def on_key_event(self, ev: str) -> None:
        if ev == HOTKEY_DOWN:
            self._on_press()
        elif ev == HOTKEY_UP:
            self._on_release()
        elif ev == HOTKEY_CHORD:
            self._on_chord()
        elif ev == ENTER:
            self._on_enter()
        elif ev == ESCAPE:
            self._on_escape()

    def _on_press(self) -> None:
        self._hotkey_down = True
        if self._busy:
            return   # запись стартует сама, когда закончится распознавание
        if self.recorder.is_recording:
            if self._locked:
                # нажатие при записи «на замке» — останавливаем
                self._locked = False
                self._finish_recording()
            return
        if (not self.transcriber.is_ready and not self.transcriber.is_loading
                and self.transcriber.load_error):
            # модель не запускается — диктовать бессмысленно, пробуем ещё раз
            self._notify(tr("error_title"), self.transcriber.load_error)
            self._wake_models()
            return
        # Модель может ещё грузиться (запуск или сон после простоя) — пишем
        # всё равно: пока человек говорит, она успевает подняться
        self._begin_recording()

    def _on_release(self) -> None:
        self._hotkey_down = False
        if not self.recorder.is_recording:
            return
        if time.monotonic() - self._press_t < self.TAP_THRESHOLD:
            self._locked = True
            log.info("Запись на замке: остановится по следующему нажатию")
            return
        self._finish_recording()

    def _on_chord(self) -> None:
        if (self.recorder.is_recording and not self._locked
                and time.monotonic() - self._press_t < self.CHORD_CANCEL_SEC):
            self._cancel_recording("сочетание клавиш", quiet=True)

    def _on_enter(self) -> None:
        if not self.recorder.is_recording:
            self.keys.enter_armed = False
            return
        log.info("Enter: заканчиваю запись, не дожидаясь тишины")
        self._locked = False
        self._finish_recording()

    def _on_escape(self) -> None:
        if self.recorder.is_recording:
            self._cancel_recording("Esc")
            return
        if self._undo_armed and time.monotonic() - self._last_paste_t < self.UNDO_WINDOW:
            self._undo_armed = False
            log.info("Откат вставки по Esc (Ctrl+Z)")
            self.paste.undo()

    # --- Запись --------------------------------------------------------------

    def _wake_models(self) -> None:
        self._last_use_t = time.monotonic()
        if not (self.transcriber.is_ready or self.transcriber.is_loading):
            log.info("Просыпаюсь: гружу Whisper обратно")
            self.transcriber.load_async()

    def _begin_recording(self) -> None:
        # Таблетка — первым делом: остальное занимает десятки миллисекунд
        self.indicator.show_text(tr("speak"), glow="blue")
        self._active_app = self._frontmost_app()
        self._active_title = self._window_title()
        self._wake_models()
        try:
            self.recorder.start()
        except Exception as exc:
            log.error("Не удалось начать запись: %s", exc)
            self.indicator.hide()
            self._notify(tr("mic_title"), tr("mic_msg"))
            return
        self._press_t = time.monotonic()
        self._heard_speech = False
        self._noise_floor = 0.0
        self.keys.enter_armed = True   # с этой секунды Enter = «распознавай»
        self.sounds.play("start")
        threading.Thread(target=self._level_loop, daemon=True, name="level-meter").start()

    def _cancel_recording(self, reason: str, quiet: bool = False) -> None:
        """Полная отмена записи: буфер выбрасывается, ничего не распознаём."""
        with self._stop_lock:
            if not self.recorder.is_recording:
                return
            self._locked = False
            self.keys.enter_armed = False
            self.recorder.stop()
        log.info("Запись отменена (%s)", reason)
        if quiet:
            self.indicator.hide()
            return
        self.sounds.play("cancel")
        self.indicator.show_text(tr("cancelled"), glow="red")
        self.indicator.hide(after=0.6)

    def _level_loop(self) -> None:
        """Волна в таблетке, цвет по тишине, голосовая отмена, автостоп замка."""
        wave = deque([0.0] * N_BARS, maxlen=N_BARS)
        last_voice = time.monotonic()
        cancel_checks = 0
        last_cancel_check = 0.0
        last_hint = None
        floor = None
        while self.recorder.is_recording:
            lvl = self.recorder.level
            if floor is None or lvl < floor:
                floor = lvl
            else:
                floor += (lvl - floor) * self.NOISE_RISE
            self._noise_floor = floor
            thr = min(self.SPEECH_LEVEL_MAX, max(self.SPEECH_LEVEL, floor * self.SPEECH_MARGIN))
            if lvl > thr:
                self._heard_speech = True
                last_voice = time.monotonic()
                cancel_checks = 0
            silent_for = time.monotonic() - last_voice
            if (self._heard_speech
                    and cancel_checks < self.VOICE_CANCEL_TRIES
                    and silent_for > self.VOICE_CANCEL_AFTER
                    and time.monotonic() - last_cancel_check > self.VOICE_CANCEL_EVERY
                    and not self._busy and self.transcriber.is_ready
                    and live_checks_ok(self.transcriber.warmup_sec)):
                cancel_checks += 1
                last_cancel_check = time.monotonic()
                threading.Thread(target=self._check_voice_cancel, daemon=True,
                                 name="voice-cancel").start()
            if self._locked and silent_for > self.LOCK_AUTOSTOP:
                if self._heard_speech:
                    log.info("Замок: %.0f c тишины после речи (фон %.4f, порог %.4f) — распознаю",
                             self.LOCK_AUTOSTOP, floor or 0.0, thr)
                    self._locked = False
                    self._finish_recording()
                else:
                    self._cancel_recording("автостоп замка: речи не было")
                break
            glow = "red" if silent_for > self.SILENCE_GLOW_AFTER else "blue"
            if not self._heard_speech:
                state = (glow, self._locked)
                if state != last_hint:
                    last_hint = state
                    self.indicator.show_text(tr("speak"), locked=self._locked, glow=glow)
            else:
                last_hint = None
                wave.append(min(1.0, lvl * 14))
                self.indicator.show_wave(list(wave), locked=self._locked, glow=glow)
            time.sleep(0.1)

    def _check_voice_cancel(self) -> None:
        try:
            tail = self.recorder.tail(self.VOICE_CANCEL_TAIL)
            if len(tail) < SAMPLE_RATE * 0.6:
                return
            # В хвосте должна быть речь: на тишине Whisper выдумывает слова
            if (np.abs(tail) > self.SILENCE_PEAK).sum() < SAMPLE_RATE * 0.3:
                return
            text = self.transcriber.transcribe_snippet(
                np.clip(tail * 32767, -32767, 32767).astype(np.int16))
            if text and is_voice_cancel(text) and self.recorder.is_recording:
                self._cancel_recording(f"голосом: {text.strip()!r}")
        except Exception as exc:
            log.warning("Живая проверка отмены не удалась: %s", exc)

    def _finish_recording(self) -> None:
        with self._stop_lock:
            if not self.recorder.is_recording:
                return
            self.keys.enter_armed = False
            audio = self.recorder.stop()
        self.sounds.play("stop")

        duration = len(audio) / SAMPLE_RATE
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if duration < MIN_DURATION_SEC:
            log.info("Запись слишком короткая (%.2f c) — игнорирую", duration)
            self.indicator.hide()
            return
        if peak == 0.0:
            # Не тишина, а ровный ноль: так Windows отдаёт звук, когда доступ
            # к микрофону выключен в параметрах конфиденциальности
            log.warning("В записи ни одного звука — микрофон, похоже, запрещён")
            self.indicator.show_text(tr("mic_blocked"), glow="red")
            self.indicator.hide(after=2.5)
            self._notify(tr("mic_title"), tr("mic_blocked_msg"))
            return
        voiced = np.where(np.abs(audio) > self.SILENCE_PEAK)[0]
        if voiced.size == 0:
            log.info("Тишина (пик %.4f) — не анализирую", peak)
            self.indicator.show_text(tr("silence"), glow="red")
            self.indicator.hide(after=1.2)
            return
        start = max(0, voiced[0] - int(0.15 * SAMPLE_RATE))
        end = min(len(audio), voiced[-1] + int(0.35 * SAMPLE_RATE))
        audio = audio[start:end]

        # Резервная запись до распознавания: упадём — текст не потеряется
        save_recovery(audio)

        self.indicator.show_text(tr("transcribing"), glow="green")
        self._busy = True
        threading.Thread(target=self._transcribe_and_paste, args=(audio, peak),
                         daemon=True, name="transcriber").start()

    # --- Распознавание и вставка -------------------------------------------

    def _wait_for_model(self, timeout: float = 120.0) -> bool:
        if self.transcriber.is_ready:
            return True
        self.indicator.show_text(tr("loading_model"), glow="green")
        deadline = time.monotonic() + timeout
        while not self.transcriber.is_ready and time.monotonic() < deadline:
            time.sleep(0.15)
        return self.transcriber.is_ready

    def screen_context(self) -> dict:
        return {"app": self._active_app or "", "title": self._active_title or "",
                "kind": core.app_kind(self._active_app)}

    def _resolve_translate(self, text: str) -> tuple[str | None, str]:
        cmd, rest = extract_translate_cmd(text)
        if cmd == "off":
            log.info("Команда «без перевода» — вставляю как есть")
            return None, rest
        if cmd:
            log.info("Команда перевода на %s", cmd)
            return cmd, rest
        target = self.translate_to
        return (target if target in LANG_NAMES else None), text

    def _apply_style(self, text: str) -> str:
        profile = self.app_profiles.get(self._active_app, {})
        style = self.style
        if style == "auto":
            style = profile.get("tone") or (
                "chat" if is_messenger_app(self._active_app) else "normal")
        if style == "casual":
            style = "chat"
        if style == "chat" and re.search(r"[^\W\d_]", text):
            log.info("Стиль «чат» для %r", self._active_app)
            return casual_tone(text)
        if style == "email":
            log.info("Стиль «письмо» (%s) для %r", self.transcriber.last_language, self._active_app)
            return email_tone(text, self.transcriber.last_language)
        if style == "notes":
            log.info("Стиль «заметки» для %r", self._active_app)
            return notes_tone(text)
        return text

    def _add_history(self, text: str, raw: str | None = None) -> None:
        item = {"ts": time.time(), "text": text}
        if raw is not None and raw != text:
            item["raw"] = raw
        if self._active_app:
            item["app"] = self._active_app      # подсказка имени в профилях программ
        self.history.insert(0, item)
        self.history[:] = prune_history(self.history)
        save_history(self.history)
        if self.autodict is not None:
            self.autodict.invalidate()
        if self.on_history_changed:
            self.on_history_changed()

    def _transcribe_and_paste(self, audio: np.ndarray, peak: float) -> None:
        voice_cancelled = False
        try:
            if not self._wait_for_model():
                raise RuntimeError("модель не загрузилась")
            ctx = self.screen_context()
            self.transcriber.hint_terms = title_terms(ctx["title"]) + (
                self.autodict.terms() if self.autodict is not None else [])
            text = self.transcriber.transcribe(audio)
            if text and is_hallucination(text) and peak < 0.08:
                log.info("Похоже на галлюцинацию (пик %.4f): %r — пропускаю", peak, text)
                text = ""
            if text and is_voice_cancel(text):
                log.info("Голосовая отмена (%r) — ничего не вставляю", text)
                voice_cancelled = True
                self.sounds.play("cancel")
                self.indicator.show_text(tr("cancelled"), glow="red")
                text = ""
            _target, text = self._resolve_translate(text)
            raw_text = text
            if text and self.voice_structure and not self.transcriber.last_verbatim:
                text = structure_by_voice(text)
            if text:
                profile = self.app_profiles.get(self._active_app, {})
                method = profile.get("paste_method", self.paste_method)
                text = self._apply_style(text)
                add_stats(len(text.split()), len(audio) / SAMPLE_RATE)
                log.info("Вставка в %r методом %s", self._active_app or "?", method)
                if self.paste.paste_text(text, method):
                    self._last_paste_t = time.monotonic()
                    self._undo_armed = True
                    self._last_paste_text = text
                else:
                    log.warning("Windows не пропустила вставку — текст оставлен в буфере")
                    self._notify(tr("paste_blocked_title"), tr("paste_blocked_msg"))
                self._add_history(text, raw_text)
            else:
                log.info("Пустой результат распознавания — ничего не вставляю")
            clear_recovery()
        except Exception as exc:
            log.error("Ошибка распознавания: %s", exc)
            self._notify(tr("error_title"), str(exc))
        finally:
            self._busy = False
            self._last_use_t = time.monotonic()
            if self.recorder.is_recording:
                pass
            elif self._hotkey_down and self.transcriber.is_ready:
                # Клавишу зажали, пока заканчивалось распознавание: человек
                # уже начал следующую диктовку — стартуем запись
                log.info("Клавиша зажата во время распознавания — стартую запись")
                self._begin_recording()
                # отпускание после отложенного старта — удержание, а не «замок»
                self._press_t = time.monotonic() - self.TAP_THRESHOLD
                if not self._hotkey_down and self.recorder.is_recording:
                    self._finish_recording()
            else:
                self.indicator.hide(after=0.9 if voice_cancelled else 0.0)

    # --- Фоновое -------------------------------------------------------------

    def recover_lost_dictation(self) -> None:
        """Осталась запись с прошлого сбоя — распознаём и кладём в буфер."""
        try:
            if not core.RECOVERY_PATH.exists():
                return
            audio = load_recovery()
            if len(audio) < SAMPLE_RATE * MIN_DURATION_SEC:
                return
            log.info("Нашёл несохранённую запись (%.1f c) — восстанавливаю",
                     len(audio) / SAMPLE_RATE)
            text = self.transcriber.transcribe(audio)
            if text:
                self.paste.set_clipboard(text)
                self._add_history(text)
                self._notify(tr("recovered_title"), tr("recovered_msg"))
                log.info("Восстановлено: %r", text)
        except Exception as exc:
            log.error("Восстановление записи не удалось: %s", exc)
        finally:
            clear_recovery()

    def maybe_unload_idle(self) -> bool:
        """Долго не диктовали — выгружаем модель, память возвращается системе."""
        if not self.idle_unload_min or self.recorder.is_recording or self._busy:
            return False
        if time.monotonic() - self._last_use_t < self.idle_unload_min * 60:
            return False
        if not self.transcriber.is_ready:
            return False
        log.info("Простой %d мин — выгружаю модель", self.idle_unload_min)
        self.transcriber.unload()
        return True
