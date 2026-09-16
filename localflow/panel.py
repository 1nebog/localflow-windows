"""Панель LocalFlow: настройки, история, словарь, сниппеты, статистика.

Страница — как у версии для Mac: маленький сервер только на 127.0.0.1, без
интернета. Окно для неё открывает `win/panel_window.py`. Здесь нет ничего от
Windows, поэтому всё проверяется тестами где угодно.

Чужие программы и сайты в браузере до панели не достучатся: страница
отдаётся только по секретному ключу из ссылки, запросы к данным — только с
этим ключом в заголовке, а адрес обязан быть 127.0.0.1 (защита от подмены
имени сайта, DNS rebinding).
"""

import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, core, icons, menu, updates
from .core import MODEL_LABELS, PILL_ANIMATIONS, STYLES, TRANSLATE_TARGETS, lang_label, tr
from .engine.catalog import WHISPER_MODELS
from .engine.download import is_ready
from .keys import DEFAULT_HOTKEY, VK_ESCAPE, validate

log = logging.getLogger("localflow")

PORTS = range(43117, 43127)     # постоянный порт — тема панели запоминается
MAX_BODY = 1_000_000
CAPTURE_TIMEOUT_SEC = 15

PANEL_STRINGS = {
    "ru": {
        "saved": "Сохранено ✓", "theme": "Тема",
        "theme_auto": "как в системе", "theme_light": "светлая", "theme_dark": "тёмная",
        "tab_settings": "Настройки", "tab_history": "История", "tab_dict": "Словарь",
        "tab_snippets": "Сниппеты", "tab_stats": "Статистика",
        "st_today": "Сегодня", "st_total": "Всего слов",
        "st_dicts": "Диктовок", "st_saved": "Сэкономлено времени",
        "st_saved_hint": "против набора ~40 слов/мин",
        "st_words": "слов", "st_chart": "Слова по дням — 2 недели",
        "st_hour": "ч", "st_min": "мин", "st_sec": "с",
        "sec_dictation": "Диктовка", "sec_model": "Распознавание", "sec_app": "Программа",
        "set_hotkey": "Клавиша диктовки", "hk_change": "Изменить", "hk_cancel": "Отмена",
        "hk_press": "Нажми клавишу…", "hk_reset": "Сбросить",
        "set_mic": "Микрофон", "set_language": "Язык диктовки", "set_translate": "Перевод",
        "set_style": "Стиль текста", "set_paste": "Метод вставки",
        "set_model": "Модель", "set_idle": "Выгружать из памяти при простое",
        "set_autostart": "Запускать вместе с Windows", "set_sounds": "Звуки",
        "set_anim": "Подсветка таблетки", "set_uilang": "Язык интерфейса",
        "set_llm": "Умное исправление", "llm_needed": "Нужно умное исправление",
        "set_media": "Пауза музыки при записи",
        "set_profiles": "Программы", "prof_hint": "Свои настройки для отдельных программ.",
        "ph_app": "Программа", "opt_default": "(как везде)",
        "search": "Поиск…", "clear_all": "Очистить всё",
        "empty_history": "Пока пусто — надиктуй что-нибудь 🎙",
        "copy": "Копировать", "delete": "Удалить", "confirm_clear": "Удалить всю историю?",
        "hint_dict": "Как слышится → как надо писать. Меняется на лету.",
        "hint_snip": "Скажи фразу целиком — вставится текст.",
        "ph_trigger": "фраза", "ph_text": "текст", "ph_repl": "замена",
        "set_updates": "Обновления", "upd_check": "Проверить", "upd_checking": "Проверяю…", "upd_version": "Версия {v}", "upd_latest": "Последняя версия ✓", "upd_available": "Есть версия {v}", "upd_get": "Обновить", "upd_manual": "Скачать вручную", "upd_downloading": "Скачиваю… {p}%", "upd_installing": "Ставлю — LocalFlow сейчас перезапустится", "upd_failed": "Не получилось обновить", "upd_error": "Не удалось проверить — нет интернета?",
        "add": "+ Добавить", "offline": "LocalFlow не запущен", "locale": "ru-RU",
    },
    "en": {
        "saved": "Saved ✓", "theme": "Theme",
        "theme_auto": "system", "theme_light": "light", "theme_dark": "dark",
        "tab_settings": "Settings", "tab_history": "History", "tab_dict": "Dictionary",
        "tab_snippets": "Snippets", "tab_stats": "Statistics",
        "st_today": "Today", "st_total": "Total words",
        "st_dicts": "Dictations", "st_saved": "Time saved",
        "st_saved_hint": "vs typing at ~40 wpm",
        "st_words": "words", "st_chart": "Words per day — 2 weeks",
        "st_hour": "h", "st_min": "min", "st_sec": "s",
        "sec_dictation": "Dictation", "sec_model": "Recognition", "sec_app": "App",
        "set_hotkey": "Dictation key", "hk_change": "Change", "hk_cancel": "Cancel",
        "hk_press": "Press a key…", "hk_reset": "Reset",
        "set_mic": "Microphone", "set_language": "Dictation language", "set_translate": "Translation",
        "set_style": "Text style", "set_paste": "Paste method",
        "set_model": "Model", "set_idle": "Free memory when idle",
        "set_autostart": "Start with Windows", "set_sounds": "Sounds",
        "set_anim": "Pill glow", "set_uilang": "Interface language",
        "set_llm": "Smart correction", "llm_needed": "Needs smart correction",
        "set_media": "Pause music while recording",
        "set_profiles": "Apps", "prof_hint": "Separate settings for specific apps.",
        "ph_app": "App", "opt_default": "(same as global)",
        "search": "Search…", "clear_all": "Clear all",
        "empty_history": "Nothing yet — dictate something 🎙",
        "copy": "Copy", "delete": "Delete", "confirm_clear": "Delete all history?",
        "hint_dict": "As heard → as it should be written. Applies instantly.",
        "hint_snip": "Say the whole phrase — the text is inserted.",
        "ph_trigger": "phrase", "ph_text": "text", "ph_repl": "replacement",
        "set_updates": "Updates", "upd_check": "Check", "upd_checking": "Checking…", "upd_version": "Version {v}", "upd_latest": "Up to date ✓", "upd_available": "Version {v} is out", "upd_get": "Update", "upd_manual": "Download manually", "upd_downloading": "Downloading… {p}%", "upd_installing": "Installing — LocalFlow will restart", "upd_failed": "Update failed", "upd_error": "Couldn't check — no internet?",
        "add": "+ Add", "offline": "LocalFlow isn't running", "locale": "en-US",
    },
    "uk": {
        "saved": "Збережено ✓", "theme": "Тема",
        "theme_auto": "як у системі", "theme_light": "світла", "theme_dark": "темна",
        "tab_settings": "Налаштування", "tab_history": "Історія", "tab_dict": "Словник",
        "tab_snippets": "Сніпети", "tab_stats": "Статистика",
        "st_today": "Сьогодні", "st_total": "Усього слів",
        "st_dicts": "Диктувань", "st_saved": "Заощаджено часу",
        "st_saved_hint": "проти набору ~40 слів/хв",
        "st_words": "слів", "st_chart": "Слова по днях — 2 тижні",
        "st_hour": "год", "st_min": "хв", "st_sec": "с",
        "sec_dictation": "Диктування", "sec_model": "Розпізнавання", "sec_app": "Програма",
        "set_hotkey": "Клавіша диктування", "hk_change": "Змінити", "hk_cancel": "Скасувати",
        "hk_press": "Натисни клавішу…", "hk_reset": "Скинути",
        "set_mic": "Мікрофон", "set_language": "Мова диктування", "set_translate": "Переклад",
        "set_style": "Стиль тексту", "set_paste": "Метод вставки",
        "set_model": "Модель", "set_idle": "Вивантажувати з пам'яті під час простою",
        "set_autostart": "Запускати разом з Windows", "set_sounds": "Звуки",
        "set_anim": "Підсвітка таблетки", "set_uilang": "Мова інтерфейсу",
        "set_llm": "Розумне виправлення", "llm_needed": "Потрібне розумне виправлення",
        "set_media": "Пауза музики під час запису",
        "set_profiles": "Програми", "prof_hint": "Свої налаштування для окремих програм.",
        "ph_app": "Програма", "opt_default": "(як усюди)",
        "search": "Пошук…", "clear_all": "Очистити все",
        "empty_history": "Поки порожньо — надиктуй щось 🎙",
        "copy": "Копіювати", "delete": "Видалити", "confirm_clear": "Видалити всю історію?",
        "hint_dict": "Як чується → як треба писати. Застосовується одразу.",
        "hint_snip": "Скажи фразу цілком — вставиться текст.",
        "ph_trigger": "фраза", "ph_text": "текст", "ph_repl": "заміна",
        "set_updates": "Оновлення", "upd_check": "Перевірити", "upd_checking": "Перевіряю…", "upd_version": "Версія {v}", "upd_latest": "Остання версія ✓", "upd_available": "Є версія {v}", "upd_get": "Оновити", "upd_manual": "Завантажити вручну", "upd_downloading": "Завантажую… {p}%", "upd_installing": "Встановлюю — LocalFlow зараз перезапуститься", "upd_failed": "Не вдалося оновити", "upd_error": "Не вдалося перевірити — немає інтернету?",
        "add": "+ Додати", "offline": "LocalFlow не запущено", "locale": "uk-UA",
    },
    "de": {
        "saved": "Gespeichert ✓", "theme": "Design",
        "theme_auto": "wie System", "theme_light": "hell", "theme_dark": "dunkel",
        "tab_settings": "Einstellungen", "tab_history": "Verlauf", "tab_dict": "Wörterbuch",
        "tab_snippets": "Snippets", "tab_stats": "Statistik",
        "st_today": "Heute", "st_total": "Wörter gesamt",
        "st_dicts": "Diktate", "st_saved": "Zeit gespart",
        "st_saved_hint": "vs. Tippen mit ~40 WPM",
        "st_words": "Wörter", "st_chart": "Wörter pro Tag — 2 Wochen",
        "st_hour": "Std", "st_min": "Min", "st_sec": "s",
        "sec_dictation": "Diktat", "sec_model": "Erkennung", "sec_app": "Programm",
        "set_hotkey": "Diktiertaste", "hk_change": "Ändern", "hk_cancel": "Abbrechen",
        "hk_press": "Taste drücken…", "hk_reset": "Zurücksetzen",
        "set_mic": "Mikrofon", "set_language": "Diktiersprache", "set_translate": "Übersetzung",
        "set_style": "Textstil", "set_paste": "Einfügemethode",
        "set_model": "Modell", "set_idle": "Speicher bei Leerlauf freigeben",
        "set_autostart": "Mit Windows starten", "set_sounds": "Töne",
        "set_anim": "Kapsel-Leuchten", "set_uilang": "Sprache der Oberfläche",
        "set_llm": "Intelligente Korrektur", "llm_needed": "Braucht intelligente Korrektur",
        "set_media": "Musik beim Aufnehmen pausieren",
        "set_profiles": "Programme", "prof_hint": "Eigene Einstellungen für einzelne Programme.",
        "ph_app": "Programm", "opt_default": "(wie überall)",
        "search": "Suchen…", "clear_all": "Alles löschen",
        "empty_history": "Noch leer — diktiere etwas 🎙",
        "copy": "Kopieren", "delete": "Löschen", "confirm_clear": "Gesamten Verlauf löschen?",
        "hint_dict": "Wie gehört → wie es geschrieben werden soll. Gilt sofort.",
        "hint_snip": "Sag die ganze Phrase — der Text wird eingefügt.",
        "ph_trigger": "Phrase", "ph_text": "Text", "ph_repl": "Ersetzung",
        "set_updates": "Updates", "upd_check": "Prüfen", "upd_checking": "Prüfe…", "upd_version": "Version {v}", "upd_latest": "Aktuell ✓", "upd_available": "Version {v} ist da", "upd_get": "Aktualisieren", "upd_manual": "Manuell herunterladen", "upd_downloading": "Lade… {p}%", "upd_installing": "Installiere — LocalFlow startet neu", "upd_failed": "Update fehlgeschlagen", "upd_error": "Prüfen fehlgeschlagen — kein Internet?",
        "add": "+ Hinzufügen", "offline": "LocalFlow läuft nicht", "locale": "de-DE",
    },
}


# --- Данные для страницы ------------------------------------------------------

def mic_options(app, mics: list[dict]) -> list[list[str]]:
    out = [["", tr("mic_default")]] + [[m["name"], m.get("label", m["name"])] for m in mics]
    if app.recorder.device and app.recorder.device not in [v for v, _ in out]:
        out.append([app.recorder.device, app.recorder.device])   # отключён, но выбран
    return out


def options(app, mics: list[dict] | None = None) -> dict:
    """Варианты для выпадающих списков: [[значение, подпись], …].
    mics — готовый список микрофонов (его долго получать в потоке окон)."""
    t = app.transcriber
    auto = tr("model_auto")
    if app.autotune.auto and (t.is_ready or app.started) and t.model_name in MODEL_LABELS:
        auto += f" · {MODEL_LABELS[t.model_name]}"
    models = [["auto", auto]]
    for key, model in WHISPER_MODELS.items():
        label = MODEL_LABELS.get(key, key)
        if not is_ready(model, t.models_dir):
            label += f" · {model.size_mb} {tr('mb')}"
        models.append([key, label])
    if mics is None:
        mics = app.list_mics()
    idle = [[str(m), tr("idle_never") if m == 0 else f"{m} {_ps('st_min')}"]
            for m in core.IDLE_UNLOAD_OPTIONS]
    return {
        "model": models,
        "llm_mode": [list(x) for x in menu.llm_options(app)],
        "mic": mic_options(app, mics),
        "language": [[c, tr("lang_auto") if c == "auto" else lang_label(c)] for c in core.LANGUAGES],
        "translate_to": [[c, tr("translate_off") if c == "off" else lang_label(c)]
                         for c in TRANSLATE_TARGETS],
        "paste_method": [["clipboard", tr("paste_fast")], ["type", tr("paste_type")]],
        "style": [[s, tr("style_" + s)] for s in STYLES],
        "pill_animation": [[a, tr("anim_" + a)] for a in PILL_ANIMATIONS],
        "idle_unload_min": idle,
        "ui_lang": [list(x) for x in menu.UI_LANGS],
        "tone": [[s, tr("style_" + s)] for s in STYLES if s != "auto"],
    }


def _ps(key: str) -> str:
    return PANEL_STRINGS.get(core._UI_LANG, PANEL_STRINGS["en"])[key]


def settings(app) -> dict:
    d = app.dictation
    return {
        "hotkey": menu.pretty_key(app.keys.hotkey),
        "hotkey_default": tuple(app.keys.hotkey) == DEFAULT_HOTKEY,
        "mic": app.recorder.device or "",
        "model": "auto" if app.autotune.auto else app.transcriber.model_name,
        "llm_mode": app.polisher.mode,
        "language": app.transcriber.language,
        "translate_to": d.translate_to,
        "paste_method": d.paste_method,
        "style": d.style,
        "pill_animation": app.pill_animation,
        "idle_unload_min": str(d.idle_unload_min),
        "autostart": bool(app.autostart_enabled()),
        "sounds": bool(app.sounds.enabled),
        "pause_media": bool(app.media.enabled),
        "ui_lang": core._UI_LANG,
        "profiles": d.app_profiles,
    }


def snapshot(app, mics: list[dict] | None = None) -> dict:
    history = app.dictation.history
    apps = sorted({it["app"] for it in history if it.get("app")} | set(app.dictation.app_profiles))
    return {
        "settings": settings(app),
        "options": options(app, mics),
        "apps": apps,
        "history": [{"ts": it["ts"], "text": it["text"]} for it in history],
        "dict": core.read_pairs(core.DICTIONARY_PATH),
        "snippets": core.read_pairs(core.SNIPPETS_PATH),
        "stats": core.stats_summary(),
        "version": __version__,
    }


def apply(app, key: str, value) -> bool:
    """Настройка со страницы. Неизвестное и недопустимое — False."""
    if isinstance(value, bool):
        switches = {"autostart": app.set_autostart, "sounds": app.set_sounds,
                    "pause_media": app.set_media_pause}
        if key not in switches:
            return False
        return switches[key](value) is not False
    value = str(value)
    if key == "mic":
        # любое название: микрофон могли отключить, пока открыта панель
        if len(value) > 200:
            return False
        app.set_mic(value or None)
        return True
    allowed = [v for v, _ in options(app, mics=[]).get(key, [])]
    if value not in allowed:
        log.warning("Панель: недопустимая настройка %r=%r", key, value)
        return False
    if key == "model":
        app.choose_model(None if value == "auto" else value)
    elif key == "llm_mode":
        app.set_llm_mode(value)
    elif key == "language":
        app.set_language(value)
    elif key == "translate_to":
        app.set_translate(value)
    elif key == "paste_method":
        app.set_paste(value)
    elif key == "style":
        app.set_style(value)
    elif key == "pill_animation":
        app.set_animation(value)
    elif key == "idle_unload_min":
        app.set_idle_unload(int(value))
    elif key == "ui_lang":
        app.set_ui_lang(value)
    else:
        return False
    return True


def clean_profiles(raw) -> dict:
    """Профили программ со страницы: только известные поля и значения."""
    out = {}
    if not isinstance(raw, dict):
        return out
    tones = [s for s in STYLES if s != "auto"]
    for name, p in raw.items():
        name = str(name).strip()[:100]
        if not name or not isinstance(p, dict):
            continue
        entry = {}
        if p.get("paste_method") in ("clipboard", "type"):
            entry["paste_method"] = p["paste_method"]
        if p.get("tone") in tones:
            entry["tone"] = p["tone"]
        out[name] = entry
    return out


def clean_pairs(raw) -> list[list[str]]:
    if not isinstance(raw, list):
        return []
    return [[str(p[0]), str(p[1])] for p in raw
            if isinstance(p, (list, tuple)) and len(p) == 2][:5000]


# --- Назначение клавиши ---------------------------------------------------------

class HotkeyCapture:
    """«Изменить» → следующее нажатие на клавиатуре становится клавишей
    диктовки. Страница спрашивает состояние, пока ждёт."""

    def __init__(self, app, call=None, clock=time.monotonic):
        self.app = app
        self._call = call or (lambda fn: fn())
        self._clock = clock
        self.timeout = CAPTURE_TIMEOUT_SEC
        self._lock = threading.Lock()
        self._gen = 0
        self._state = "idle"
        self._msg = ""
        self._t0 = 0.0

    def start(self) -> dict:
        if self.app.hook_error:
            with self._lock:
                self._state, self._msg = "error", tr("st_hook_error")
            return self.poll()
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._state, self._msg, self._t0 = "waiting", "", self._clock()
        self.app.keys.capture(lambda chord: self._done(gen, chord))
        # панель могли закрыть посреди ожидания — клавиатура не должна
        # остаться «в режиме назначения»
        timer = threading.Timer(self.timeout, self._expire, args=(gen,))
        timer.daemon = True
        timer.start()
        return self.poll()

    def _expire(self, gen: int) -> None:
        with self._lock:
            stale = gen == self._gen and self._state == "waiting"
        if stale:
            self.cancel()

    def cancel(self) -> dict:
        with self._lock:
            self._gen += 1
            self._state, self._msg = "idle", ""
        self.app.keys.cancel_capture()
        return self._snapshot()

    def _done(self, gen: int, chord) -> None:
        with self._lock:
            if gen != self._gen or self._state != "waiting":
                return
            if tuple(chord) == (VK_ESCAPE,):
                self._state, self._msg = "idle", ""      # Esc — передумал
                return
            error, warnings = validate(chord)
            if error:
                self._state, self._msg = "error", tr(error)
                return
        try:
            self._call(lambda: self.app.set_hotkey(chord))
        except Exception as exc:
            log.error("Клавиша не назначилась: %s", exc)
            with self._lock:
                self._state, self._msg = "idle", ""
            return
        with self._lock:
            if gen == self._gen:
                self._state = "done"
                self._msg = " · ".join(tr(w) for w in warnings)

    def poll(self) -> dict:
        with self._lock:
            waiting_too_long = (self._state == "waiting"
                                and self._clock() - self._t0 > self.timeout)
        if waiting_too_long:
            self.cancel()
        return self._snapshot()

    def _snapshot(self) -> dict:
        with self._lock:
            return {"state": self._state, "msg": self._msg,
                    "label": menu.pretty_key(self.app.keys.hotkey),
                    "default": tuple(self.app.keys.hotkey) == DEFAULT_HOTKEY}


# --- Сервер ---------------------------------------------------------------------

class PanelServer:
    def __init__(self, app, call=None):
        self.app = app
        self.token = secrets.token_urlsafe(24)
        # Настройки меняются в потоке окон — там же, где меню трея
        self.call = call or (lambda fn: fn())
        self.capture = HotkeyCapture(app, self.call)
        self.check_updates = updates.check
        self._update_info = None
        self.port: int | None = None
        self._srv = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?k={self.token}"

    def start(self) -> bool:
        if self._srv is not None:
            return True
        for port in PORTS:
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
            except OSError:
                continue
            srv.daemon_threads = True
            srv.panel = self
            self._srv, self.port = srv, port
            threading.Thread(target=srv.serve_forever, daemon=True, name="panel-server").start()
            log.info("Панель: http://127.0.0.1:%d", port)
            return True
        log.warning("Панель: нет свободного порта")
        return False

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    # --- ответы ---

    def page(self) -> str:
        strings = dict(PANEL_STRINGS.get(core._UI_LANG, PANEL_STRINGS["en"]))
        return (PANEL_HTML.replace("__LANG__", core._UI_LANG)
                .replace("__K__", self.token)
                .replace("__L__", json.dumps(strings, ensure_ascii=False)))

    def get(self, path: str):
        app = self.app
        if path == "/api/all":
            mics = app.list_mics()        # не в потоке окон: это до секунды
            return self.call(lambda: snapshot(app, mics))
        if path == "/api/mics":
            mics = app.list_mics()
            return self.call(lambda: {"options": mic_options(app, mics), "current": app.recorder.device or ""})
        if path == "/api/status":
            def _status():
                h = app.dictation.history
                return {"status": menu.status_text(app), "state": menu.tray_state(app),
                        "history_ts": h[0]["ts"] if h else 0}
            return self.call(_status)
        if path == "/api/hotkey":
            return self.capture.poll()
        if path == "/api/update/status":
            return app.updater.status()
        return None

    def post(self, path: str, body: dict):
        app = self.app
        if path == "/api/settings":
            key = str(body.get("key"))
            ok = self.call(lambda: apply(app, key, body.get("value")))
            return {"ok": ok, "reload": ok and key == "ui_lang"}
        if path == "/api/hotkey/capture":
            return self.capture.start()
        if path == "/api/hotkey/cancel":
            return self.capture.cancel()
        if path == "/api/hotkey/reset":
            self.capture.cancel()
            self.call(lambda: app.set_hotkey(DEFAULT_HOTKEY))
            return self.capture.poll()
        if path == "/api/dict":
            core.write_pairs(core.DICTIONARY_PATH, clean_pairs(body.get("pairs")), core._DICT_HEADER)
            return {"ok": True}
        if path == "/api/snippets":
            core.write_pairs(core.SNIPPETS_PATH, clean_pairs(body.get("pairs")), core._SNIPPETS_HEADER)
            return {"ok": True}
        if path == "/api/profiles":
            profiles = clean_profiles(body.get("profiles"))
            self.call(lambda: app.set_profiles(profiles))
            return {"ok": True}
        if path == "/api/history/delete":
            ts = body.get("ts")
            self.call(lambda: app.delete_history(ts))
            return {"ok": True}
        if path == "/api/update/check":
            self._update_info = self.check_updates()   # сеть — не в потоке окон
            return {k: v for k, v in self._update_info.items() if k in ("state", "latest", "current")}
        if path == "/api/update/install":
            # ставим только то, что сами нашли при проверке, а не адрес со страницы
            return {"ok": bool(app.start_update(self._update_info))}
        if path == "/api/update/open":
            url = updates.safe_url((self._update_info or {}).get("url"))
            self.call(lambda: app.open_url(url))
            return {"ok": True}
        if path == "/api/history/clear":
            self.call(app.clear_history)
            return {"ok": True}
        return None


class _Handler(BaseHTTPRequestHandler):
    server_version = "LocalFlow"

    def log_message(self, *args):     # запросы в журнал не пишем
        pass

    @property
    def panel(self) -> PanelServer:
        return self.server.panel

    def _send(self, body: bytes, ctype="application/json", code=200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                         "img-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200) -> None:
        self._send(json.dumps(data, ensure_ascii=False).encode(), code=code)

    def _host_ok(self) -> bool:
        return self.headers.get("Host") == f"127.0.0.1:{self.panel.port}"

    def _key_ok(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-LocalFlow", ""), self.panel.token)

    def do_GET(self):
        if not self._host_ok():
            return self._send(b"forbidden", "text/plain", 403)
        url = urlparse(self.path)
        if url.path == "/icon.png":
            return self._send(_icon_png(), "image/png")
        if url.path in ("/", "/index.html"):
            key = parse_qs(url.query).get("k", [""])[0]
            if not secrets.compare_digest(key, self.panel.token):
                return self._send(b"forbidden", "text/plain", 403)
            return self._send(self.panel.page().encode(), "text/html")
        if not self._key_ok():
            return self._send(b"forbidden", "text/plain", 403)
        try:
            data = self.panel.get(url.path)
        except Exception as exc:
            log.error("Панель: %s упал: %s", url.path, exc)
            return self._json({"ok": False}, 500)
        if data is None:
            return self._send(b"not found", "text/plain", 404)
        self._json(data)

    def do_POST(self):
        if not self._host_ok() or not self._key_ok():
            return self._send(b"forbidden", "text/plain", 403)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= MAX_BODY:
                return self._json({"ok": False}, 413)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("не объект")
        except Exception:
            return self._json({"ok": False}, 400)
        try:
            data = self.panel.post(urlparse(self.path).path, body)
        except Exception as exc:
            log.error("Панель: %s упал: %s", self.path, exc)
            return self._json({"ok": False}, 500)
        if data is None:
            return self._json({"ok": False}, 404)
        self._json(data)


_ICON = {}


def _icon_png() -> bytes:
    if "png" not in _ICON:
        _ICON["png"] = icons.png_bytes(icons.app_icon(64))
    return _ICON["png"]


PANEL_HTML = r"""<!DOCTYPE html>
<html lang="__LANG__" translate="no"><head><meta charset="utf-8">
<meta name="google" content="notranslate">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LocalFlow</title>
<link rel="icon" href="/icon.png">
<style>
:root{
  --bg:#f3f3f5; --card:#ffffff; --text:#1d1d1f; --muted:#6e6e73;
  --border:#e5e5ea; --accent:#0a84ff; --danger:#e5484d; --ok:#30a14e;
  --shadow:0 1px 2px rgba(0,0,0,.06);
}
[data-theme="dark"]{
  --bg:#1c1c1e; --card:#2a2a2c; --text:#f2f2f7; --muted:#98989d;
  --border:#3a3a3c; --shadow:0 1px 2px rgba(0,0,0,.3); --ok:#30d158;
}
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--bg);scrollbar-color:var(--border) transparent}
body{font:14px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;
  background:var(--bg);color:var(--text);max-width:720px;margin:0 auto;
  padding:22px 20px 60px;transition:background .2s,color .2s}
header{display:flex;align-items:center;gap:10px;margin-bottom:4px}
header img{width:26px;height:26px}
h1{font-size:19px;font-weight:600}
.state{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:13px;
  margin:0 0 16px 36px;min-height:19px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--ok);flex-shrink:0}
.dot.loading{background:#ff9f0a}.dot.error{background:var(--danger)}
.dot.recording{background:#ff453a}.dot.paused{background:var(--muted)}
.tabs{display:flex;gap:4px;background:var(--card);border-radius:10px;
  padding:4px;margin-bottom:18px;box-shadow:var(--shadow)}
.tab{flex:1;text-align:center;padding:7px 0;border-radius:7px;cursor:pointer;
  font-size:13.5px;font-weight:500;color:var(--muted);user-select:none;transition:all .15s}
.tab:hover{color:var(--text)}
.tab.active{background:var(--accent);color:#fff}
.card{background:var(--card);border-radius:10px;padding:12px 14px;
  margin-bottom:8px;box-shadow:var(--shadow);display:flex;gap:12px;align-items:center}
.iconbtn{background:none;border:none;cursor:pointer;font-size:14px;
  color:var(--muted);padding:4px 7px;border-radius:6px}
.iconbtn:hover{background:var(--bg)}
.iconbtn.danger:hover{color:var(--danger)}
input,textarea,select{font:inherit;color:var(--text);background:var(--bg);
  border:1px solid var(--border);border-radius:7px;padding:6px 9px;outline:none}
input,textarea{width:100%}
input:focus,textarea:focus,select:focus{border-color:var(--accent)}
textarea{resize:vertical;min-height:34px}
.row{display:grid;grid-template-columns:1fr 24px 1.4fr 32px;gap:8px;align-items:center;margin-bottom:8px}
.arrow{color:var(--muted);text-align:center}
.btn{background:var(--accent);color:#fff;border:none;border-radius:7px;
  padding:7px 14px;font:inherit;font-weight:500;cursor:pointer}
.btn.ghost{background:var(--bg);color:var(--text);border:1px solid var(--border)}
.btn.ghost:hover{border-color:var(--accent)}
.btn.link{background:none;color:var(--accent);padding:7px 6px}
.toolbar{display:flex;gap:10px;margin-bottom:14px;align-items:center}
.toolbar input{flex:1}
.day{font-size:12px;font-weight:600;color:var(--muted);margin:18px 0 8px}
.time{color:var(--muted);font-size:12px;flex-shrink:0;width:40px}
.htext{flex:1;white-space:pre-wrap;word-break:break-word}
.empty{color:var(--muted);text-align:center;margin-top:60px}
.saved{font-size:13px;color:var(--ok);margin-left:auto;opacity:0;transition:opacity .3s}
.saved.show{opacity:1}
#themeBtn{font-size:16px}
/* статистика */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:14px}
.stat{background:var(--card);border-radius:10px;padding:14px;box-shadow:var(--shadow)}
.stat .num{font-size:24px;font-weight:600}
.stat .lbl{font-size:12px;color:var(--muted);margin-top:2px}
.stat .sub{font-size:11px;color:var(--muted);margin-top:4px;opacity:.85}
.chart-card{background:var(--card);border-radius:10px;padding:14px;box-shadow:var(--shadow)}
.chart-title{font-size:12px;font-weight:600;color:var(--muted);margin-bottom:12px}
.chart{display:flex;align-items:flex-end;gap:5px;height:110px}
.chart .col{flex:1;display:flex;flex-direction:column;align-items:center;gap:5px;height:100%;justify-content:flex-end}
.chart .bar{width:100%;border-radius:4px 4px 2px 2px;background:var(--accent);min-height:2px;opacity:.85;transition:height .4s}
.chart .bar.zero{background:var(--border)}
.chart .dl{font-size:9px;color:var(--muted)}
/* настройки */
.sec-title{font-size:12.5px;font-weight:600;color:var(--muted);margin:18px 0 7px 2px}
.sec-title:first-child{margin-top:0}
.group{background:var(--card);border-radius:10px;box-shadow:var(--shadow);overflow:hidden}
.set-row{display:flex;align-items:center;gap:10px;padding:10px 14px;min-height:50px}
.set-row+.set-row{border-top:1px solid var(--border)}
.set-row .name{flex:1}
.set-row select{min-width:210px;max-width:290px}
.note{font-size:12px;color:var(--muted)}
.note.err{color:var(--danger)}
select:disabled{opacity:.55;cursor:default}
kbd{font:600 13px "Segoe UI",system-ui,sans-serif;background:var(--bg);border:1px solid var(--border);
  border-bottom-width:2px;border-radius:6px;padding:4px 10px;white-space:nowrap}
kbd.wait{color:var(--accent);border-color:var(--accent);animation:blink 1.2s infinite}
@keyframes blink{50%{opacity:.45}}
.switch{position:relative;width:40px;height:22px;flex-shrink:0}
.switch input{opacity:0;width:0;height:0}
.switch span{position:absolute;inset:0;background:var(--border);border-radius:11px;cursor:pointer;transition:background .15s}
.switch span:before{content:"";position:absolute;width:16px;height:16px;left:3px;top:3px;
  background:#fff;border-radius:50%;transition:transform .15s;box-shadow:0 1px 2px rgba(0,0,0,.25)}
.switch input:checked+span{background:var(--accent)}
.switch input:checked+span:before{transform:translateX(18px)}
.switch input:focus-visible+span{outline:2px solid var(--accent);outline-offset:2px}
.hint{font-size:12.5px;color:var(--muted);margin:-2px 0 8px 2px}
.prow{display:grid;grid-template-columns:1.2fr 1fr 1fr 32px;gap:8px;align-items:center;margin-bottom:8px}
.prow select{width:100%}
</style></head><body>
<header>
  <img src="/icon.png" alt="">
  <h1>LocalFlow</h1>
  <span class="saved" id="saved"></span>
  <button class="iconbtn" id="themeBtn"></button>
</header>
<div class="state"><span class="dot" id="dot"></span><span id="state"></span></div>
<div class="tabs">
  <div class="tab" data-tab="settings"></div>
  <div class="tab" data-tab="history"></div>
  <div class="tab" data-tab="dict"></div>
  <div class="tab" data-tab="snippets"></div>
  <div class="tab" data-tab="stats"></div>
</div>
<div id="content"></div>
<datalist id="apps"></datalist>
<script>
const L=__L__, K="__K__";
const $=s=>document.querySelector(s);
let DATA=null, saveTimer=null;
$('#saved').textContent=L.saved;
document.querySelectorAll('.tab').forEach(el=>el.textContent=L['tab_'+el.dataset.tab]);

async function api(path,body){
  const r=await fetch(path,{method:body===undefined?'GET':'POST',
    headers:{'X-LocalFlow':K,'Content-Type':'application/json'},
    body:body===undefined?undefined:JSON.stringify(body)});
  if(!r.ok)throw new Error(r.status);
  return r.json();
}
async function post(path,body){const r=await api(path,body);flashSaved();return r;}
function flashSaved(){const s=$('#saved');s.classList.add('show');
  clearTimeout(flashSaved.t);flashSaved.t=setTimeout(()=>s.classList.remove('show'),1200);}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}

/* тема: авто (как в системе) -> светлая -> тёмная */
const mq=matchMedia('(prefers-color-scheme: dark)');
let theme='auto';
try{theme=localStorage.getItem('lf-theme')||'auto';}catch(e){}
if(!['auto','light','dark'].includes(theme))theme='auto';
function applyTheme(){
  const eff=theme==='auto'?(mq.matches?'dark':'light'):theme;
  document.documentElement.dataset.theme=eff;
  $('#themeBtn').textContent=theme==='auto'?'◐':(theme==='dark'?'🌙':'☀️');
  $('#themeBtn').title=L.theme+': '+L['theme_'+theme];
}
mq.onchange=applyTheme;
$('#themeBtn').onclick=()=>{theme=theme==='auto'?'light':theme==='light'?'dark':'auto';
  try{localStorage.setItem('lf-theme',theme);}catch(e){} applyTheme();};
applyTheme();

/* вкладки */
const TABS=['settings','history','dict','snippets','stats'];
let tab=location.hash.slice(1);
if(!TABS.includes(tab))tab='settings';
function setTab(t){tab=t;history.replaceState(null,'','#'+t);
  document.querySelectorAll('.tab').forEach(el=>el.classList.toggle('active',el.dataset.tab===t));
  render();}
document.querySelectorAll('.tab').forEach(el=>el.onclick=()=>setTab(el.dataset.tab));

async function load(){DATA=await api('/api/all');
  $('#apps').innerHTML=DATA.apps.map(a=>`<option value="${esc(a)}">`).join('');
  setTab(tab);}

/* строка состояния: раз в пару секунд */
let lastTs=null;
async function refreshState(){
  if(document.hidden)return;
  try{
    const s=await api('/api/status');
    $('#state').textContent=s.status;$('#dot').className='dot '+s.state;
    if(lastTs!==null&&s.history_ts!==lastTs&&(tab==='history'||tab==='stats')&&DATA){
      const q=$('#q')?$('#q').value:'';
      DATA=await api('/api/all');tab==='history'?renderHistory(q):renderStats();
    }
    lastTs=s.history_ts;
  }catch(e){$('#state').textContent=L.offline;$('#dot').className='dot error';}
}
setInterval(refreshState,2000);
document.addEventListener('visibilitychange',refreshState);

/* история */
function renderHistory(q=''){
  const c=$('#content');
  const items=DATA.history.filter(it=>it.text.toLowerCase().includes(q.toLowerCase()));
  let h=`<div class="toolbar"><input placeholder="${L.search}" id="q" value="${esc(q)}">
    <button class="btn ghost" id="clearAll">${L.clear_all}</button></div>`;
  if(!items.length)h+=`<div class="empty">${L.empty_history}</div>`;
  let prevDay='';
  for(const it of items){
    const d=new Date(it.ts*1000);
    const day=d.toLocaleDateString(L.locale,{day:'numeric',month:'long'});
    if(day!==prevDay){h+=`<div class="day">${day}</div>`;prevDay=day;}
    const t=d.toLocaleTimeString(L.locale,{hour:'2-digit',minute:'2-digit'});
    h+=`<div class="card"><span class="time">${t}</span>
      <span class="htext">${esc(it.text)}</span>
      <button class="iconbtn" data-copy="${it.ts}" title="${L.copy}">⧉</button>
      <button class="iconbtn danger" data-del="${it.ts}" title="${L.delete}">✕</button></div>`;
  }
  const hadFocus=document.activeElement&&document.activeElement.id==='q';
  c.innerHTML=h;
  if(hadFocus){$('#q').focus();$('#q').setSelectionRange(9999,9999);}
  $('#q').oninput=e=>{renderHistory(e.target.value);$('#q').focus();};
  $('#clearAll').onclick=async()=>{if(confirm(L.confirm_clear)){
    await post('/api/history/clear',{});DATA.history=[];renderHistory();}};
  c.querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{
    const it=DATA.history.find(x=>String(x.ts)===b.dataset.copy);
    await navigator.clipboard.writeText(it.text);
    b.textContent='✓';setTimeout(()=>b.textContent='⧉',900);});
  c.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{
    await post('/api/history/delete',{ts:parseFloat(b.dataset.del)});
    DATA.history=DATA.history.filter(x=>String(x.ts)!==b.dataset.del);
    renderHistory($('#q').value);});
}

/* словарь и сниппеты */
function renderPairs(kind){
  const c=$('#content');
  const hint=kind==='dict'?L.hint_dict:L.hint_snip;
  c.innerHTML=`<div class="hint" style="margin-bottom:12px">${hint}</div>
    <div id="rows"></div><button class="btn" id="add">${L.add}</button>`;
  const rows=$('#rows'), pairs=DATA[kind], multi=kind==='snippets';
  function draw(){
    rows.innerHTML='';
    pairs.forEach((p,i)=>{
      const r=document.createElement('div');r.className='row';
      r.innerHTML=`<input value="${esc(p[0])}" placeholder="${multi?L.ph_trigger:L.ph_trigger}">
        <span class="arrow">→</span>
        ${multi?`<textarea rows="1" placeholder="${L.ph_text}">${esc(p[1].replaceAll('\\n','\n'))}</textarea>`
               :`<input value="${esc(p[1])}" placeholder="${L.ph_repl}">`}
        <button class="iconbtn danger" title="${L.delete}">✕</button>`;
      const [k,,v,del]=r.children;
      k.oninput=()=>{p[0]=k.value;queueSave(kind);};
      v.oninput=()=>{p[1]=multi?v.value.replaceAll('\n','\\n'):v.value;queueSave(kind);};
      del.onclick=()=>{pairs.splice(i,1);draw();queueSave(kind);};
      rows.appendChild(r);
    });
  }
  draw();
  $('#add').onclick=()=>{pairs.push(['','']);draw();rows.lastElementChild.querySelector('input').focus();};
}
function queueSave(kind){clearTimeout(saveTimer);
  saveTimer=setTimeout(()=>post('/api/'+kind,{pairs:DATA[kind]}),500);}

/* статистика */
function human(sec){
  if(sec<60)return Math.round(sec)+' '+L.st_sec;
  const h=Math.floor(sec/3600), m=Math.round((sec%3600)/60);
  return (h?h+' '+L.st_hour+' ':'')+m+' '+L.st_min;
}
function renderStats(){
  const s=DATA.stats, c=$('#content'), fmt=n=>n.toLocaleString(L.locale);
  const saved=Math.max(0,s.total.words/40*60-s.total.sec);
  c.innerHTML=`<div class="stats-grid">
    <div class="stat"><div class="num">${fmt(s.today.words)}</div>
      <div class="lbl">${L.st_today}, ${L.st_words}</div>
      <div class="sub">${s.today.n} ${L.st_dicts.toLowerCase()}</div></div>
    <div class="stat"><div class="num">${fmt(s.total.words)}</div><div class="lbl">${L.st_total}</div></div>
    <div class="stat"><div class="num">${fmt(s.total.n)}</div><div class="lbl">${L.st_dicts}</div></div>
    <div class="stat"><div class="num">${human(saved)}</div><div class="lbl">${L.st_saved}</div>
      <div class="sub">${L.st_saved_hint}</div></div></div>
    <div class="chart-card"><div class="chart-title">${L.st_chart}</div><div class="chart" id="chart"></div></div>`;
  const max=Math.max(1,...s.last14.map(d=>d.words));
  $('#chart').innerHTML=s.last14.map(d=>{
    const h=Math.round(d.words/max*100);
    const day=new Date(d.d+'T12:00').toLocaleDateString(L.locale,{day:'numeric'});
    return `<div class="col" title="${d.d}: ${fmt(d.words)} ${L.st_words}">
      <div class="bar${d.words?'':' zero'}" style="height:${Math.max(h,2)}%"></div><div class="dl">${day}</div></div>`;
  }).join('');
}

/* настройки */
let hkTimer=null, refreshMics=null;
/* подключили микрофон и вернулись в окно — список свежий */
window.addEventListener('focus',()=>{if(tab==='settings'&&refreshMics)refreshMics();});
function renderSettings(){
  const st=DATA.settings, opt=DATA.options, c=$('#content');
  const sel=(key,options,cur,allowEmpty)=>{
    let o=allowEmpty?`<option value="">${L.opt_default}</option>`:'';
    for(const [v,lbl] of options)o+=`<option value="${esc(v)}"${v===cur?' selected':''}>${esc(lbl)}</option>`;
    return `<select data-key="${key}">${o}</select>`;
  };
  const row=(label,control)=>`<div class="set-row"><span class="name">${label}</span>${control}</div>`;
  const pick=key=>sel(key,opt[key],String(st[key]));
  const sw=key=>`<label class="switch"><input type="checkbox" data-switch="${key}"${st[key]?' checked':''}><span></span></label>`;
  c.innerHTML=
    `<div class="sec-title">${L.sec_dictation}</div><div class="group">
      <div class="set-row"><span class="name">${L.set_hotkey}<div class="note" id="hkNote"></div></span>
        <button class="btn link" id="hkReset"${st.hotkey_default?' hidden':''}>${L.hk_reset}</button>
        <kbd id="hkLabel">${esc(st.hotkey)}</kbd>
        <button class="btn ghost" id="hkBtn">${L.hk_change}</button></div>`+
    row(L.set_mic,pick('mic'))+row(L.set_language,pick('language'))+
    `<div class="set-row"><span class="name">${L.set_translate}<div class="note" id="trNote"></div></span>${pick('translate_to')}</div>`+
    row(L.set_style,pick('style'))+
    row(L.set_paste,pick('paste_method'))+
    `</div><div class="sec-title">${L.sec_model}</div><div class="group">`+
    row(L.set_model,pick('model'))+row(L.set_llm,pick('llm_mode'))+row(L.set_idle,pick('idle_unload_min'))+
    `</div><div class="sec-title">${L.sec_app}</div><div class="group">`+
    row(L.set_autostart,sw('autostart'))+row(L.set_sounds,sw('sounds'))+row(L.set_media,sw('pause_media'))+
    row(L.set_anim,pick('pill_animation'))+row(L.set_uilang,pick('ui_lang'))+
    `<div class="set-row"><span class="name">${L.set_updates}<div class="note" id="updNote">${esc(L.upd_version.replace('{v}',DATA.version))}</div></span>
      <button class="btn link" id="updManual" hidden>${L.upd_manual}</button>
      <button class="btn" id="updGet" hidden>${L.upd_get}</button>
      <button class="btn ghost" id="updBtn">${L.upd_check}</button></div>`+
    `</div><div class="sec-title">${L.set_profiles}</div><div class="hint">${L.prof_hint}</div>
     <div id="profiles"></div><button class="btn ghost" id="addProf">${L.add}</button>`;

  const micSel=c.querySelector('select[data-key="mic"]');
  refreshMics=async()=>{
    if(!micSel.isConnected||document.activeElement===micSel)return;
    try{const r=await api('/api/mics');opt.mic=r.options;st.mic=r.current;
      micSel.innerHTML=r.options.map(([v,l])=>`<option value="${esc(v)}"${v===r.current?' selected':''}>${esc(l)}</option>`).join('');}
    catch(e){}
  };
  /* переводит модель исправления: без неё выбор перевода ни на что не влияет */
  const syncTranslate=()=>{const off=st.llm_mode==='off';
    c.querySelector('select[data-key="translate_to"]').disabled=off;
    $('#trNote').textContent=off?L.llm_needed:'';};
  syncTranslate();
  c.querySelectorAll('.set-row select').forEach(s=>s.onchange=async()=>{
    const r=await post('/api/settings',{key:s.dataset.key,value:s.value});
    if(r.ok)st[s.dataset.key]=s.value;
    if(r.reload)location.reload();
    syncTranslate();
  });
  c.querySelectorAll('[data-switch]').forEach(s=>s.onchange=async()=>{
    const key=s.dataset.switch;
    try{const r=await post('/api/settings',{key,value:s.checked});
      if(!r.ok)s.checked=!s.checked; else st[key]=s.checked;}
    catch(e){s.checked=!s.checked;}
  });

  /* обновления: только по кнопке */
  const updNote=(t,err)=>{$('#updNote').textContent=t;$('#updNote').className='note'+(err?' err':'');};
  const updFailed=()=>{updNote(L.upd_failed,true);$('#updManual').hidden=false;
    $('#updGet').hidden=true;$('#updBtn').hidden=false;};
  $('#updBtn').onclick=async()=>{
    const b=$('#updBtn');
    b.disabled=true;b.textContent=L.upd_checking;$('#updGet').hidden=$('#updManual').hidden=true;updNote('');
    try{const r=await api('/api/update/check',{});
      if(r.state==='available'){updNote(L.upd_available.replace('{v}',r.latest));$('#updGet').hidden=false;}
      else if(r.state==='latest')updNote(L.upd_latest);
      else updNote(L.upd_error,true);}
    catch(e){updNote(L.upd_error,true);}
    b.disabled=false;b.textContent=L.upd_check;
  };
  $('#updManual').onclick=()=>api('/api/update/open',{});
  $('#updGet').onclick=async()=>{
    $('#updGet').hidden=$('#updBtn').hidden=true;updNote(L.upd_downloading.replace('{p}',0));
    let r;try{r=await api('/api/update/install',{});}catch(e){r={ok:false};}
    if(!r.ok){updFailed();return;}
    const t=setInterval(async()=>{
      let s;try{s=await api('/api/update/status');}catch(e){return;}   /* программа уже закрылась */
      if(s.state==='downloading')updNote(L.upd_downloading.replace('{p}',s.percent));
      else if(s.state==='installing')updNote(L.upd_installing);
      else if(s.state==='error'){clearInterval(t);updFailed();}
    },500);
  };

  /* клавиша диктовки */
  const note=(t,err)=>{$('#hkNote').textContent=t||'';$('#hkNote').className='note'+(err?' err':'');};
  const showKey=s=>{st.hotkey=s.label;st.hotkey_default=s.default;
    $('#hkLabel').textContent=s.label;$('#hkLabel').classList.remove('wait');
    $('#hkBtn').textContent=L.hk_change;$('#hkReset').hidden=s.default;};
  const stop=()=>{clearInterval(hkTimer);hkTimer=null;};
  $('#hkBtn').onclick=async()=>{
    if(hkTimer){stop();showKey(await api('/api/hotkey/cancel',{}));return;}
    const s=await api('/api/hotkey/capture',{});
    if(s.state==='error'){note(s.msg,true);return;}
    note('');$('#hkLabel').textContent=L.hk_press;$('#hkLabel').classList.add('wait');
    $('#hkBtn').textContent=L.hk_cancel;$('#hkBtn').blur();
    hkTimer=setInterval(async()=>{
      let s;try{s=await api('/api/hotkey');}catch(e){stop();return;}
      if(s.state==='waiting')return;
      stop();showKey(s);
      if(s.state==='done'){note(s.msg,false);flashSaved();}
      else if(s.state==='error')note(s.msg,true);
    },200);
  };
  $('#hkReset').onclick=async()=>{stop();note('');showKey(await post('/api/hotkey/reset',{}));};

  /* профили программ */
  const profs=Object.entries(st.profiles||{}).map(([app,p])=>({app,paste:p.paste_method||'',tone:p.tone||''}));
  const box=$('#profiles');
  function saveProfs(){
    const out={};
    for(const p of profs){if(!p.app.trim())continue;
      const e={};if(p.paste)e.paste_method=p.paste;if(p.tone)e.tone=p.tone;out[p.app.trim()]=e;}
    st.profiles=out;post('/api/profiles',{profiles:out});
  }
  function drawProfs(){
    box.innerHTML='';
    profs.forEach((p,i)=>{
      const r=document.createElement('div');r.className='prow';
      r.innerHTML=`<input value="${esc(p.app)}" placeholder="${L.ph_app}" list="apps">
        ${sel('paste',opt.paste_method,p.paste,true)}${sel('tone',opt.tone,p.tone,true)}
        <button class="iconbtn danger" title="${L.delete}">✕</button>`;
      const [a,ps,tn,del]=r.children;
      a.oninput=()=>{p.app=a.value;clearTimeout(saveTimer);saveTimer=setTimeout(saveProfs,600);};
      ps.onchange=()=>{p.paste=ps.value;saveProfs();};
      tn.onchange=()=>{p.tone=tn.value;saveProfs();};
      del.onclick=()=>{profs.splice(i,1);drawProfs();saveProfs();};
      box.appendChild(r);
    });
  }
  drawProfs();
  $('#addProf').onclick=()=>{profs.push({app:'',paste:'',tone:''});drawProfs();
    box.lastElementChild.querySelector('input').focus();};
}

function render(){
  if(hkTimer&&tab!=='settings'){clearInterval(hkTimer);hkTimer=null;api('/api/hotkey/cancel',{});}
  if(tab==='history')renderHistory();
  else if(tab==='stats')renderStats();
  else if(tab==='settings')renderSettings();
  else renderPairs(tab);
}
refreshState();
load();
</script></body></html>"""
