"""«Что нового»: список изменений по версиям для вкладки панели.

После обновления панель сама открывается на этой вкладке один раз —
чтобы было видно, что поменялось.
"""

NEWS = [
    ("0.1.6", {
        "ru": ["Вкладка «Что нового» — после обновления открывается сама",
               "Кнопка на страницу программы на GitHub: «Настройки» → «Программа»",
               "Подписи в настройках — с заглавной буквы, понятнее подсказка в словаре",
               "Тема панели меняется плавно, без вспышки"],
        "en": ["“What's new” tab — opens by itself after an update",
               "Button to the program's GitHub page: Settings → App",
               "Settings labels start with a capital letter, clearer dictionary hint",
               "The panel theme changes smoothly, without a flash"],
        "uk": ["Вкладка «Що нового» — після оновлення відкривається сама",
               "Кнопка на сторінку програми на GitHub: «Налаштування» → «Програма»",
               "Підписи в налаштуваннях — з великої літери, зрозуміліша підказка в словнику",
               "Тема панелі змінюється плавно, без спалаху"],
        "de": ["Tab „Neu“ — öffnet sich nach einem Update von selbst",
               "Button zur GitHub-Seite des Programms: Einstellungen → Programm",
               "Beschriftungen mit Großbuchstaben, klarerer Hinweis im Wörterbuch",
               "Das Design des Panels wechselt sanft, ohne Aufblitzen"],
    }),
    ("0.1.5", {
        "ru": ["Часы на таблетке: сколько идёт запись и сколько обработка",
               "Длинная диктовка распознаётся кусками прямо во время речи",
               "Умное исправление больше не зависает на длинных диктовках",
               "В статистике — средняя длина диктовки"],
        "en": ["A clock on the pill: how long you've been recording and processing",
               "Long dictations are recognized piece by piece while you speak",
               "Smart correction no longer hangs on long dictations",
               "Average dictation length in Statistics"],
        "uk": ["Годинник на таблетці: скільки йде запис і скільки обробка",
               "Довге диктування розпізнається частинами просто під час мовлення",
               "Розумне виправлення більше не зависає на довгих диктуваннях",
               "У статистиці — середня тривалість диктування"],
        "de": ["Uhr auf der Pille: wie lange Aufnahme und Verarbeitung laufen",
               "Lange Diktate werden schon beim Sprechen stückweise erkannt",
               "Die smarte Korrektur hängt bei langen Diktaten nicht mehr",
               "Durchschnittliche Diktatlänge in der Statistik"],
    }),
    ("0.1.4", {
        "ru": ["Короткие фразы в режиме замка вставляются быстрее"],
        "en": ["Short phrases in lock mode are inserted faster"],
        "uk": ["Короткі фрази в режимі замка вставляються швидше"],
        "de": ["Kurze Sätze im Sperrmodus werden schneller eingefügt"],
    }),
    ("0.1.3", {
        "ru": ["Обновление прямо из программы, без браузера"],
        "en": ["Updates install right from the app, no browser needed"],
        "uk": ["Оновлення просто з програми, без браузера"],
        "de": ["Updates direkt aus dem Programm, ohne Browser"],
    }),
    ("0.1.2", {
        "ru": ["Если микрофон молчит, программа сама пробует другой способ записи"],
        "en": ["If the microphone is silent, the app tries another way to record"],
        "uk": ["Якщо мікрофон мовчить, програма сама пробує інший спосіб запису"],
        "de": ["Bleibt das Mikrofon stumm, versucht das Programm einen anderen Aufnahmeweg"],
    }),
    ("0.1.1", {
        "ru": ["Тихий микрофон усиливается автоматически",
               "Кнопка «Проверить обновления»"],
        "en": ["A quiet microphone is boosted automatically",
               "“Check for updates” button"],
        "uk": ["Тихий мікрофон підсилюється автоматично",
               "Кнопка «Перевірити оновлення»"],
        "de": ["Ein leises Mikrofon wird automatisch verstärkt",
               "Button „Nach Updates suchen“"],
    }),
]


def for_lang(lang: str) -> list[dict]:
    """Версии от новой к старой: [{"v": "0.1.6", "items": [...]}, ...]."""
    return [{"v": v, "items": texts.get(lang) or texts["en"]} for v, texts in NEWS]


def show_after_start(cfg: dict, version: str) -> bool:
    """Открыть ли «Что нового» при этом запуске. Отмечает версию как показанную.

    Только после обновления: при первой установке и так открывается панель
    с настройками, а список изменений новичку ни о чём не говорит.
    """
    if cfg.get("news_seen") == version:
        return False
    updated = bool(cfg.get("intro_shown"))
    cfg["news_seen"] = version
    return updated
