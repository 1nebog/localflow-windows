"""Умное исправление: локальная языковая модель причёсывает распознанный текст.

Перенос `TextPolisher` из версии для Mac. Инструкции, примеры, нарезка текста
и все проверки ответа модели — те же (они в core.py и ниже, строка в
строку). Отличается только движок: вместо mlx-lm — llama.cpp отдельным
процессом (engine/llama_server.py).

Выключено по умолчанию. Пока не включишь, модель не скачивается и память не
занимает. Любой сбой модели не ломает диктовку: при проблемах возвращается
исходный текст без изменений.
"""

import logging
import threading
import time
from pathlib import Path

from .core import (
    _CTX_ECHO_RE, _LLM_PREAMBLE_RE, _LLM_REWRITE_SHOTS, _LLM_REWRITE_SYSTEM,
    _LLM_SHOTS, _LLM_SYSTEM, _LLM_TRANSLATE_SYSTEM, ADDED_WORDS_LIMIT,
    LANG_NAMES, LLM_MAX_CHARS, LLM_MIN_CHARS, LLM_MODES, LLM_POLISH_BUDGET_SEC, TASK_POLISH,
    TASK_REWRITE, TASK_TRANSLATE, added_content_ratio, context_hint,
    drop_invented_dashes, invented_numbers, learned_shots, looks_like_lang,
    same_script, split_for_llm, text_similarity, translate_shots,
)
from .engine.catalog import LLM_MODELS
from .engine.download import download, is_ready
from .engine.llama_server import EngineError, LlamaServer

log = logging.getLogger("localflow")


class TextPolisher:
    """Локальная LLM, причёсывающая распознанный текст.

    Грузится лениво и только когда включена; выключил — память возвращается
    системе. Переключение режима и скачивание модели идут в фоне, диктовка
    всё это время работает как обычно.
    """

    def __init__(self, engine_exe: Path, models_dir: Path, log_dir: Path,
                 server_factory=None, downloader=download):
        self.engine_exe = Path(engine_exe)
        self.models_dir = Path(models_dir)
        self.log_dir = Path(log_dir)
        self.mode = "off"
        # Видеокарта разрешена, пока движок на ней не упал
        self.use_gpu = True
        self.on_gpu_disabled = None
        self.progress = None          # (скачано, всего), пока качаем модель
        self.load_error: str | None = None
        self._server_factory = server_factory or LlamaServer
        self._download = downloader
        self._server = None
        self._loaded_mode = None
        self._lock = threading.Lock()         # один запрос к модели за раз
        self._state = threading.Lock()        # кто грузит модель
        self._loading = False
        self._on_done = None

    @property
    def ready(self) -> bool:
        return self._server is not None and self._server.running

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def device(self) -> str:
        return self._server.device if self._server else ""

    def downloaded(self, mode: str) -> bool:
        return mode in LLM_MODELS and is_ready(LLM_MODELS[mode], self.models_dir)

    # --- Режим и загрузка -----------------------------------------------------

    def set_mode(self, mode: str, on_done=None) -> None:
        """Переключить режим. Загрузка/выгрузка — в фоновом потоке.

        on_done(ok) зовётся, когда выбранный режим заработал или не смог.
        Если за время загрузки режим успели сменить ещё раз, зовётся только
        последний.
        """
        if mode not in LLM_MODES:
            return
        self.mode = mode
        with self._state:
            if on_done is not None:
                self._on_done = on_done
            if self._loading:
                return        # работающий загрузчик сам заметит новый режим
            self._loading = True
        threading.Thread(target=self._work, daemon=True, name="llm-mode").start()

    def _work(self) -> None:
        while True:
            want = self.mode
            ok = False
            try:
                if want == "off":
                    self.unload()
                    ok = True
                else:
                    ok = (self._loaded_mode == want and self.ready) or self._load(want)
            except Exception as exc:
                log.error("Переключение умного исправления упало: %s", exc)
            with self._state:
                if self.mode != want:
                    continue          # пока грузили, выбрали другое
                self._loading = False
                done, self._on_done = self._on_done, None
            break
        if done:
            try:
                done(ok)
            except Exception as exc:
                log.warning("Колбэк умного исправления упал: %s", exc)

    def _make_server(self, model: Path):
        return self._server_factory(
            exe=self.engine_exe, model=model,
            log_path=self.log_dir / "llama-server.log", use_gpu=self.use_gpu)

    def _load(self, mode: str) -> bool:
        model = LLM_MODELS[mode]
        t0 = time.monotonic()
        try:
            if not is_ready(model, self.models_dir):
                # Качаем, пока прежняя модель продолжает работать: 2–5 ГБ —
                # это минуты, и диктовка всё это время не должна стоять
                log.info("Модели исправления %s нет на диске — качаю (%d МБ)",
                         model.file, model.size_mb)
                self._download(model, self.models_dir,
                               progress=lambda have, total: setattr(self, "progress", (have, total)),
                               cancelled=lambda: self.mode != mode)
                self.progress = None
            if self.mode != mode:
                return False
            self.unload()
            log.info("Гружу модель исправления %s…", model.file)
            server = self._start(self.models_dir / model.file)
            with self._lock:
                self._server, self._loaded_mode = server, mode
            self.load_error = None
            log.info("Модель исправления загружена за %.1f c (%s)",
                     time.monotonic() - t0, server.device)
            self._warm_up()
            return True
        except Exception as exc:
            if self.mode != mode:
                log.info("Загрузка модели исправления %s прервана: выбран другой режим", model.file)
                return False
            log.error("Модель исправления не загрузилась: %s", exc)
            self.load_error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            return False
        finally:
            self.progress = None

    def _start(self, model_path: Path):
        server = self._make_server(model_path)
        try:
            server.start()
            return server
        except EngineError:
            server.stop()
            if not self.use_gpu:
                raise
        log.warning("Движок исправления не заработал с видеокартой — перехожу на процессор")
        self._disable_gpu()
        server = self._make_server(model_path)
        server.start()
        return server

    def _disable_gpu(self) -> None:
        self.use_gpu = False
        if self.on_gpu_disabled:
            try:
                self.on_gpu_disabled()
            except Exception as exc:
                log.warning("Не сохранился отказ от видеокарты: %s", exc)

    def _warm_up(self) -> None:
        """Прогоняем инструкцию с примерами один раз: движок запомнит её
        расчёт, и первая диктовка не будет ждать лишние секунды."""
        t0 = time.monotonic()
        try:
            with self._lock:
                if self._server is None:
                    return
                self._server.chat(self._prefix_messages(TASK_POLISH)
                                  + [{"role": "user", "content": "Ы"}], max_tokens=1)
                # журнал движка дописывается с задержкой — к этому моменту
                # в нём уже видно, сколько слоёв ушло на видеокарту
                refresh = getattr(self._server, "refresh_device", None)
                device = refresh() if refresh else self.device
            log.info("Промпт исправления прогрет за %.1f c (%s)", time.monotonic() - t0, device)
        except Exception as exc:
            log.warning("Промпт исправления не прогрелся (работаем без этого): %s", exc)

    def unload(self) -> None:
        with self._lock:
            server, self._server = self._server, None
            self._loaded_mode = None
        if server is not None:
            server.stop()
            log.info("Модель исправления выгружена, память освобождена")

    # --- Собственно правка ------------------------------------------------------

    def polish(self, text: str, lang: str = "ru", ctx: dict | None = None) -> str:
        """Причесать текст. При любой проблеме возвращает исходник."""
        if not self.enabled or not self.ready:
            return text
        if not (LLM_MIN_CHARS <= len(text) <= LLM_MAX_CHARS):
            return text
        t0 = time.monotonic()
        hint = context_hint(ctx)
        try:
            # Переносы строк — это голосовые команды («новый абзац»):
            # правим каждый кусок отдельно, чтобы модель их не склеила
            out_parts, left = [], 0
            for p in text.split("\n"):
                if not p.strip():
                    out_parts.append(p)
                    continue
                # Длинную реплику режем по предложениям: модель точнее правит
                # короткий кусок, а промах в одном не тянет за собой остальные
                done = []
                for c in split_for_llm(p):
                    if time.monotonic() - t0 > LLM_POLISH_BUDGET_SEC:
                        done.append(c)      # время вышло — как распознано
                        left += len(c)
                        continue
                    done.append(self._polish_chunk(c, hint))
                out_parts.append("".join(done))
            result = "\n".join(out_parts)
            if left:
                log.info("Исправление не успело за %.0f c: %d знаков из %d "
                         "оставлены как есть", LLM_POLISH_BUDGET_SEC, left, len(text))
        except Exception as exc:
            log.error("Исправление упало: %s", exc)
            return text
        log.info("Исправление за %.2f c: %r -> %r",
                 time.monotonic() - t0, text[:60], result[:60])
        return result

    @staticmethod
    def _prefix_messages(task: str) -> list[dict]:
        """Инструкция + примеры для конкретной задачи (правка / перевод)."""
        if task.startswith(TASK_TRANSLATE):
            lang = task.split(":", 1)[1] if ":" in task else "en"
            system = _LLM_TRANSLATE_SYSTEM.format(lang=LANG_NAMES.get(lang, lang))
            shots = translate_shots(lang)
        elif task == TASK_REWRITE:
            # Сама задача едет в сообщении пользователя, а не в инструкции:
            # так один кэш промпта обслуживает все команды правки
            system, shots = _LLM_REWRITE_SYSTEM, _LLM_REWRITE_SHOTS
        else:
            system = _LLM_SYSTEM
            shots = _LLM_SHOTS + learned_shots()
        msgs = [{"role": "system", "content": system}]
        for s, d in shots:
            msgs.append({"role": "user", "content": s})
            msgs.append({"role": "assistant", "content": d})
        return msgs

    def _run(self, core: str, hint: str, task: str, max_tokens: int) -> str:
        """Один запрос к модели. Звать под self._lock."""
        user = f"{hint}\n{core}" if hint else core
        try:
            return self._server.chat(self._prefix_messages(task)
                                     + [{"role": "user", "content": user}], max_tokens)
        except EngineError:
            if self._server is not None and not self._server.running:
                # Движок упал посреди работы. Следующая диктовка поднимет его
                # заново; если падал на видеокарте — уже на процессоре.
                log.warning("Движок исправления упал — перезапущу при следующей диктовке")
                if self.use_gpu:
                    self._disable_gpu()
                self._server, self._loaded_mode = None, None
            raise

    def _polish_chunk(self, chunk: str, hint: str = "") -> str:
        # На огрызке в пару слов модели не за что зацепиться — она начинает
        # додумывать (и проверка потом всё равно отбросит ответ)
        if len(chunk.strip()) < LLM_MIN_CHARS:
            return chunk
        # Пробелы по краям держим отдельно: они склеивают куски обратно
        core = chunk.strip()
        lead = chunk[:len(chunk) - len(chunk.lstrip())]
        tail = chunk[len(chunk.rstrip()):]
        with self._lock:
            if self._server is None:
                return chunk
            raw = self._run(core, hint, TASK_POLISH,
                            max_tokens=int(len(chunk) / 2) + 120)
        return lead + self._validate(core, raw) + tail

    def _translate_chunk(self, chunk: str, hint: str, task: str,
                         target: str) -> tuple[str, bool]:
        core = chunk.strip()
        lead = chunk[:len(chunk) - len(chunk.lstrip())]
        tail = chunk[len(chunk.rstrip()):]
        if len(core) < LLM_MIN_CHARS:
            return chunk, False
        with self._lock:
            if self._server is None:
                return chunk, False
            # Перевод бывает заметно длиннее оригинала — потолок щедрее
            raw = self._run(core, hint, task,
                            max_tokens=int(len(core) / 1.5) + 160)
        out = self._clean_answer(core, raw)
        if not out:
            log.warning("Перевод отброшен (пустой ответ)")
            return chunk, False
        # Длина: перевод не может отличаться в разы
        ratio = len(out) / max(1, len(core))
        if not (0.35 <= ratio <= 2.2):
            log.warning("Перевод отброшен (длина ×%.2f): %r", ratio, out[:80])
            return chunk, False
        # Главная проверка: язык действительно сменился
        if not looks_like_lang(out, target):
            log.warning("Перевод отброшен (не похоже на %s): %r", target, out[:80])
            return chunk, False
        return lead + out + tail, True

    def translate(self, text: str, target: str,
                  ctx: dict | None = None) -> tuple[str, bool]:
        """Перевести текст на target. Возвращает (текст, получилось ли).

        При любой осечке возвращает ОРИГИНАЛ: вставить русский лучше, чем
        вставить мусор или ответ модели на вопрос из текста.
        """
        if not self.enabled or not self.ready or target not in LANG_NAMES:
            return text, False
        if not (LLM_MIN_CHARS <= len(text) <= LLM_MAX_CHARS):
            return text, False
        task = f"{TASK_TRANSLATE}:{target}"
        t0 = time.monotonic()
        hint = context_hint(ctx)
        try:
            out_parts, ok_any = [], False
            for p in text.split("\n"):
                if not p.strip():
                    out_parts.append(p)
                    continue
                done = []
                for c in split_for_llm(p):
                    piece, ok = self._translate_chunk(c, hint, task, target)
                    done.append(piece)
                    ok_any = ok_any or ok
                out_parts.append("".join(done))
            result = "\n".join(out_parts)
        except Exception as exc:
            log.error("Перевод упал: %s", exc)
            return text, False
        log.info("Перевод на %s за %.2f c: %r -> %r", target,
                 time.monotonic() - t0, text[:50], result[:50])
        return result, ok_any

    def rewrite(self, text: str, instruction: str,
                bounds: tuple = (0.3, 2.5),
                ctx: dict | None = None) -> tuple[str, bool]:
        """Переписать текст по инструкции. Возвращает (текст, получилось ли)."""
        if not self.enabled or not self.ready:
            return text, False
        if not (LLM_MIN_CHARS <= len(text) <= LLM_MAX_CHARS):
            return text, False
        t0 = time.monotonic()
        try:
            body = f"Задача: {instruction}\n\nТекст:\n{text.strip()}"
            with self._lock:
                if self._server is None:
                    return text, False
                raw = self._run(body, context_hint(ctx), TASK_REWRITE,
                                max_tokens=int(len(text) * bounds[1] / 1.5) + 200)
            out = self._clean_answer(text, raw)
            if not out:
                log.warning("Правка отброшена (пустой ответ)")
                return text, False
            ratio = len(out) / max(1, len(text))
            if not (bounds[0] * 0.7 <= ratio <= bounds[1] * 1.4):
                log.warning("Правка отброшена (длина ×%.2f, ждали %.2f–%.2f): %r",
                            ratio, bounds[0], bounds[1], out[:80])
                return text, False
        except Exception as exc:
            log.error("Правка упала: %s", exc)
            return text, False
        log.info("Правка «%s» за %.2f c: %r -> %r", instruction[:24],
                 time.monotonic() - t0, text[:40], out[:40])
        return out, True

    @staticmethod
    def _clean_answer(src: str, raw: str) -> str:
        """Срезать всё, что модель добавила вокруг собственно ответа."""
        out = (raw or "").strip()
        out = _LLM_PREAMBLE_RE.sub("", out)
        # Модель иногда повторяет подсказку «[куда: …]» в начале ответа
        if not src.lstrip().startswith("["):
            out = _CTX_ECHO_RE.sub("", out, count=1)
        out = out.strip().strip("`").strip()
        # Модель иногда заключает ответ в кавычки целиком
        if len(out) > 1 and out[0] in "\"«'" and out[-1] in "\"»'":
            out = out[1:-1].strip()
        return out

    @staticmethod
    def _validate(src: str, raw: str) -> str:
        """Отбрасываем ответ, если модель вместо правки начала фантазировать."""
        out = TextPolisher._clean_answer(src, raw)
        if not out:
            return src
        # Длина: правка не может радикально раздуть или урезать текст
        ratio_len = len(out) / max(1, len(src))
        if not (0.55 <= ratio_len <= 1.6):
            log.warning("Исправление отброшено (длина ×%.2f): %r", ratio_len, out[:80])
            return src
        # Похожесть: модель должна ПРАВИТЬ текст, а не писать свой
        sim = text_similarity(src, out)
        if sim < 0.5:
            log.warning("Исправление отброшено (похожесть %.2f): %r", sim, out[:80])
            return src
        # Придуманные числа = модель ответила на текст, а не отредактировала
        # его («посчитай, сколько будет…» → «…равно 24»)
        new_nums = invented_numbers(src, out)
        if new_nums:
            log.warning("Исправление отброшено (дописаны числа %s): %r", new_nums, out[:80])
            return src
        # То же самое, но словами: «…равно сорока» цифр не содержит
        added, new_words = added_content_ratio(src, out)
        if added > ADDED_WORDS_LIMIT:
            log.warning("Исправление отброшено (%.0f%% слов дописано: %s): %r",
                        added * 100, new_words[:6], out[:80])
            return src
        # Язык не должен меняться сам по себе
        if not same_script(src, out):
            log.warning("Исправление отброшено (сменился язык): %r", out[:80])
            return src
        return drop_invented_dashes(src, out)
