"""Распознавание речи: Whisper на whisper.cpp + вся чистка текста как на Mac.

Порядок обработки текста повторяет `Transcriber.transcribe()` версии для Mac
строка в строку. Отличие одно — как запись режется на куски.

На Mac стоит `condition_on_previous_text=False`: подсказка действует только
на первое 30-секундное окно, дальше каждое окно распознаётся с чистого
листа. Так Whisper не срывается в петлю на длинных диктовках («realize
realize…» ×200). В whisper.cpp внутри одного запроса такого выключателя
нет: он либо тянет текст из окна в окно, либо отключает подсказку совсем.
Поэтому окна режем сами — каждое окно отдельным запросом, подсказка только
в первом, а движок между запросами ничего не помнит.
"""

import logging
import threading
import time
from pathlib import Path

import numpy as np

from .audio_util import to_wav_bytes
from .core import (
    DEFAULT_LANGUAGE, DEFAULT_MODEL, INITIAL_PROMPTS, SAMPLE_RATE,
    SILENCE_PEAK_LEVEL, apply_dictionary, apply_editor_command,
    apply_self_corrections, apply_snippet, build_initial_prompt, clean_text,
    is_spam_repeat, scrub_hallucinations, strip_loop_tail, strip_prompt_echo,
)
from .engine.catalog import WHISPER_MODELS
from .engine.languages import to_code
from .engine.whisper_server import EngineError, WhisperServer

log = logging.getLogger("localflow")


class Transcriber:
    """Обёртка над движком whisper.cpp. Модель грузится один раз."""

    WINDOW_SEC = 30.0          # окно Whisper
    NO_SPEECH_THOLD = 0.85     # как на Mac, см. комментарий в _decode
    # Окно всегда полные 30 секунд, даже для фразы в две: движок умеет
    # слушать только длину фразы (audio_ctx), и это в разы быстрее, но
    # обычные модели Whisper на укороченном окне начинают перевирать и
    # зацикливаться (замер 2026-09-13: ошибка base выросла с 14% до 25–66%).

    def __init__(self, engine_exe: Path, models_dir: Path, log_dir: Path,
                 model_name: str = DEFAULT_MODEL, server_factory=None):
        self.engine_exe = Path(engine_exe)
        self.models_dir = Path(models_dir)
        self.log_dir = Path(log_dir)
        self.model_name = model_name
        self.language = DEFAULT_LANGUAGE
        self.last_language = "ru"  # язык последней фразы (для стиля «Письмо»)
        # Последний результат — дословная вставка (сниппет/команда редактора):
        # такой текст умное исправление не трогает
        self.last_verbatim = False
        # Подсказка декодеру: термины из заголовка окна и автословаря.
        # Пишется приложением перед каждой транскрипцией.
        self.hint_terms: list[str] = []
        # Видеокарта разрешена, пока не доказано обратное: если движок падает
        # на ней (кривой драйвер), переходим на процессор и запоминаем это
        self.use_gpu = True
        self.on_gpu_disabled = None   # колбэк: сохранить в настройки
        self._server_factory = server_factory or WhisperServer
        self._server = None
        self._ready = False
        self._loading = False
        self._lock = threading.Lock()
        # Сколько длился прогрев секундой тишины. Фраза до 30 секунд стоит
        # почти столько же (окно всегда полное), так что это честный замер
        # скорости компьютера — по нему подбирается модель
        self.warmup_sec: float | None = None
        self.last_infer_sec: float | None = None   # последний запрос к движку

    # --- Загрузка ------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def device(self) -> str:
        return self._server.device if self._server else ""

    def model_path(self, name: str | None = None) -> Path:
        return self.models_dir / WHISPER_MODELS[name or self.model_name].file

    def _make_server(self):
        return self._server_factory(
            exe=self.engine_exe, model=self.model_path(),
            log_path=self.log_dir / "whisper-server.log",
            use_gpu=self.use_gpu, no_speech_thold=self.NO_SPEECH_THOLD)

    def _warm_up(self) -> float:
        wav = to_wav_bytes(np.zeros(SAMPLE_RATE, dtype=np.float32))
        t0 = time.monotonic()
        self._server.inference(wav, self._fields("ru"))
        took = time.monotonic() - t0
        # На видеокарте первый прогон ещё и собирает вычислительные программы
        # под неё — замеряем второй. На процессоре первый и так честный.
        if self.use_gpu and took < 5.0:
            t0 = time.monotonic()
            self._server.inference(wav, self._fields("ru"))
            took = min(took, time.monotonic() - t0)
        return took

    def _start_server(self) -> None:
        self._server = self._make_server()
        try:
            self._server.start()
            # Прогрев: пусть всё медленное первого запуска случится сейчас,
            # а не на первой диктовке
            self.warmup_sec = self._warm_up()
        except EngineError:
            self._server.stop()
            if not self.use_gpu:
                raise
            log.warning("Движок не заработал с видеокартой — перехожу на процессор")
            self._disable_gpu()
            self._server = self._make_server()
            self._server.start()
            self.warmup_sec = self._warm_up()
        log.info("Прогрев модели: %.2f c на фразу (%s)", self.warmup_sec,
                 self._server.device)

    def _disable_gpu(self) -> None:
        self.use_gpu = False
        if self.on_gpu_disabled:
            try:
                self.on_gpu_disabled()
            except Exception as exc:
                log.warning("Не сохранился отказ от видеокарты: %s", exc)

    def load(self, model_name: str | None = None) -> bool:
        """Запустить движок с моделью. Модель уже должна быть скачана."""
        if model_name and model_name != self.model_name:
            self.model_name = model_name
            self.unload()
        with self._lock:
            if self._ready and self._server and self._server.running:
                return True
            self._loading = True
            t0 = time.monotonic()
            log.info("Загружаю модель Whisper '%s'…", self.model_name)
            try:
                self._start_server()
            except Exception as exc:
                log.error("Не удалось загрузить модель: %s", exc)
                return False
            finally:
                self._loading = False
            self._ready = True
            log.info("Модель '%s' загружена за %.1f c", self.model_name,
                     time.monotonic() - t0)
            return True

    def load_async(self, model_name: str | None = None, on_done=None) -> None:
        """Загрузка модели в фоновом потоке — не блокирует интерфейс."""
        def _run():
            ok = self.load(model_name)
            if on_done:
                on_done(ok)

        threading.Thread(target=_run, daemon=True, name="model-loader").start()

    def unload(self) -> None:
        """Выгрузить модель: процесс движка закрывается, память возвращается."""
        with self._lock:
            self._ready = False
            if self._server:
                self._server.stop()
            self._server = None

    # --- Один прогон ---------------------------------------------------------

    def _fields(self, lang, prompt=None) -> dict:
        fields = {
            "response_format": "verbose_json",
            "language": lang or "auto",
            "temperature": "0.0",
            "temperature_inc": "0.2",
            # Порог «здесь тишина, окно можно пропустить» поднят:
            # на дефолтных 0.6 Whisper иногда выбрасывал целое окно
            # длинной диктовки, и хвост фразы терялся. Тишину у нас
            # и так ловят проверка пика и обрезка краёв записи.
            "no_speech_thold": str(self.NO_SPEECH_THOLD),
            # Вероятности всех языков нам не нужны, а считаются отдельно
            "no_language_probabilities": True,
            # Время каждого отдельного слова не нужно: хватает границ фраз.
            # Без этого движок ещё и режет фразы на куски по 60 символов
            # посреди слова, а по границам фраз мы двигаем окна.
            "token_timestamps": False,
        }
        if prompt:
            fields["prompt"] = prompt
        return fields

    def _infer(self, audio: np.ndarray, lang, prompt) -> dict:
        wav = to_wav_bytes(audio)
        fields = self._fields(lang, prompt)
        timeout = 120 + 10 * len(audio) / SAMPLE_RATE
        t0 = time.monotonic()
        try:
            res = self._server.inference(wav, fields, timeout=timeout)
            self.last_infer_sec = time.monotonic() - t0
            return res
        except EngineError as exc:
            if self._server and self._server.running:
                raise
            # Движок упал посреди работы. Поднимаем заново; если он падал на
            # видеокарте — дальше считаем на процессоре.
            log.warning("Движок упал: %s — перезапускаю", exc)
            if self.use_gpu:
                self._disable_gpu()
            self._server = self._make_server()
            self._server.start()
            return self._server.inference(wav, fields, timeout=timeout)

    def _decode(self, audio: np.ndarray, lang, prompt) -> dict:
        """Прогон Whisper по окнам. Ответ в том же виде, что у mlx-whisper на
        Mac: {"text", "segments": [{"start", "end", "text"}], "language"}."""
        if self._server is None or not self._ready:
            raise RuntimeError("Модель ещё не загружена")
        total = len(audio) / SAMPLE_RATE
        segments: list[dict] = []
        language = None
        off, first = 0.0, True
        for _ in range(1000):  # предохранитель от бесконечного цикла
            end = min(total, off + self.WINDOW_SEC)
            chunk = audio[int(off * SAMPLE_RATE):int(end * SAMPLE_RATE)]
            res = self._infer(chunk, lang, prompt if first else None)
            first = False
            language = language or to_code(res.get("language"))
            # Язык определяем один раз, по первому окну (как mlx-whisper на
            # Mac): определение — это лишний проход модели на каждое окно
            lang = lang or language
            win = []
            for seg in res.get("segments") or []:
                text = seg.get("text") or ""
                if not text.strip():
                    continue
                win.append({"start": off + float(seg.get("start") or 0.0),
                            "end": off + float(seg.get("end") or 0.0),
                            "text": text})
            segments.extend(win)
            if end >= total - 0.01:
                break
            # Куда двигать следующее окно — так же, как решает сам Whisper:
            if (len(win) >= 2 and end - win[-1]["end"] < 0.5
                    and win[-1]["start"] - off >= 5.0):
                # последняя фраза разрезана границей окна: выбрасываем её и
                # распознаём заново целиком в следующем окне
                off = segments.pop()["start"]
            elif win and end - win[-1]["end"] > 1.0 and win[-1]["end"] - off >= 5.0:
                # декодер бросил окно раньше конца: всё, что после последней
                # фразы, отдаём следующему окну, а не теряем
                off = win[-1]["end"]
            else:
                off = end
        return {"text": "".join(s["text"] for s in segments),
                "segments": segments, "language": language}

    # --- Дальше — перенос с Mac ---------------------------------------------

    # Хвост короче этого не спасаем: обрывок в полсекунды почти всегда
    # дыхание или щелчок, а не потерянная фраза
    TAIL_LOST_MIN = 1.5      # секунд «недосказанного», с которых начинаем
    TAIL_VOICE_MIN = 0.4     # секунд реального голоса в этом куске
    TAIL_OVERLAP = 0.4       # назад по времени, чтобы не срезать слово
    TAIL_MAX_ROUNDS = 3      # сколько раз подряд готовы доспасать хвост

    @staticmethod
    def _last_segment_end(result) -> float:
        segs = result.get("segments") or []
        if not segs:
            return 0.0
        try:
            return float(segs[-1].get("end", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _recover_tail(self, audio: np.ndarray, result, text: str, lang) -> str:
        """Досказать то, что Whisper бросил недодекодированным."""
        try:
            piece = audio          # кусок, который последним отдавали модели
            for _ in range(self.TAIL_MAX_ROUNDS):
                piece_sec = len(piece) / SAMPLE_RATE
                last_end = self._last_segment_end(result)
                lost = piece_sec - last_end
                if lost < self.TAIL_LOST_MIN:
                    return text
                # В брошенном куске должен быть ГОЛОС, а не тишина в конце
                lost_from = max(0, int(last_end * SAMPLE_RATE))
                voiced = int(np.count_nonzero(
                    np.abs(piece[lost_from:]) > SILENCE_PEAK_LEVEL))
                if voiced < self.TAIL_VOICE_MIN * SAMPLE_RATE:
                    return text
                log.warning(
                    "Хвост не распознан: кусок %.1f c, текст кончается на "
                    "%.1f c (потеряно %.1f c, голоса %.1f c) — распознаю "
                    "хвост отдельно",
                    piece_sec, last_end, lost, voiced / SAMPLE_RATE)
                # Чуть заходим назад, чтобы не срезать слово пополам
                start = max(0, int((last_end - self.TAIL_OVERLAP) * SAMPLE_RATE))
                piece = piece[start:]
                # Подсказку хвосту не даём: на коротком куске она как раз и
                # вызывает эхо инструкции вместо расшифровки
                result = self._decode(piece, lang, None)
                extra = strip_prompt_echo(result["text"].strip())
                if not extra:
                    return text
                text = (text + " " + extra).strip() if text else extra
                # Модель не сказала, докуда дошла — второй заход вслепую
                # может дописать то же самое ещё раз. Останавливаемся.
                if self._last_segment_end(result) <= 0:
                    return text
            return text
        except Exception as exc:
            log.warning("Спасение хвоста не удалось: %s", exc)
            return text

    def transcribe(self, audio: np.ndarray) -> str:
        """Распознать буфер аудио. Возвращает текст без висящих пробелов."""
        if not self._ready:
            raise RuntimeError("Модель ещё не загружена")

        lang = None if self.language == "auto" else self.language
        # hint_terms — имена из заголовка окна и автословарь из истории:
        # подсказываем Whisper написание слов, которые он иначе перевирает
        prompt = build_initial_prompt(self.language, self.hint_terms)
        t0 = time.monotonic()
        result = self._decode(audio, lang, prompt)
        raw_text = result["text"].strip()
        # Возврат потерянного хвоста: Whisper иногда обрывает декодирование
        # посреди окна — ставит «конец текста» и уходит дальше, а всё, что
        # человек сказал после, пропадает молча.
        raw_text = self._recover_tail(audio, result, raw_text, lang)
        # Чистим по языку, который реально определился в этой фразе
        detected = result.get("language") or lang or "ru"
        self.last_language = detected
        # Эхо подсказки в начале («Английские слова пишем as is…») — не речь
        text = strip_prompt_echo(raw_text)
        # Остатки петли (страховка, если проскочит) — вырезаем хвост целиком
        text = strip_loop_tail(text)
        # Галлюцинации Whisper режем всегда, в любом месте текста
        text = scrub_hallucinations(text)
        # «Да, да, да, да…» на тишине/дакании — мусор, не вставляем ничего
        if is_spam_repeat(text):
            log.info("Спам-повтор (галлюцинация): %r — пропускаю", text)
            return ""
        # Команда редактора, сказанная отдельно («пробел», «запятая»…)?
        cmd = apply_editor_command(text)
        if cmd is not None:
            log.info("Команда редактора: %r -> %r", text, cmd)
            self.last_verbatim = True
            return cmd
        # Самоисправления: «кнопку красной, вернее, синей» -> «кнопку синей»
        text = apply_self_corrections(text)
        text = apply_dictionary(clean_text(text, detected))
        # Голосовые шорткаты: вся фраза — триггер => вставляем сниппет
        before_snippet = text
        text = apply_snippet(text)
        # Сниппет и команда редактора — это ТОЧНЫЙ текст пользователя
        # (адрес почты, шаблон): умное исправление их трогать не должно
        self.last_verbatim = text != before_snippet
        elapsed = time.monotonic() - t0
        log.info(
            "Распознано за %.2f c (язык: %s): %r -> %r",
            elapsed, detected, raw_text, text,
        )
        return text

    def transcribe_snippet(self, audio_i16: np.ndarray, prompt: str | None = None) -> str:
        """Быстрая дословная расшифровка пары секунд звука — для живой
        голосовой отмены («отмена» на паузе речи)."""
        if not self._ready:
            return ""
        x = audio_i16.astype(np.float32) / 32768.0
        try:
            return self._decode(x, None, prompt).get("text", "")
        except Exception as exc:
            log.warning("Короткая расшифровка не удалась: %s", exc)
            return ""

    PARAGRAPH_PAUSE = 1.2  # секунд паузы в речи, после которых — новый абзац

    def transcribe_long(self, audio: np.ndarray) -> str:
        """Длинное аудио (файлы): текст с абзацами по паузам между фразами."""
        if not self._ready:
            raise RuntimeError("Модель ещё не загружена")
        lang = None if self.language == "auto" else self.language
        prompt = INITIAL_PROMPTS.get(self.language, INITIAL_PROMPTS["auto"])
        result = self._decode(audio, lang, prompt)
        detected = result.get("language") or lang or "ru"
        self.last_language = detected
        paras: list[list[str]] = [[]]
        prev_end = None
        for seg in result.get("segments", []):
            if (prev_end is not None
                    and seg["start"] - prev_end > self.PARAGRAPH_PAUSE
                    and paras[-1]):
                paras.append([])
            chunk = seg.get("text", "").strip()
            if chunk:
                paras[-1].append(chunk)
            prev_end = seg["end"]
        out = []
        for p in paras:
            txt = clean_text(scrub_hallucinations(" ".join(p)), detected)
            if txt:
                out.append(txt)
        # Эхо подсказки бывает и здесь, и всегда в самом начале
        if out:
            first = strip_prompt_echo(out[0])
            if first:
                out[0] = first
            else:
                out.pop(0)
        return "\n\n".join(out)
