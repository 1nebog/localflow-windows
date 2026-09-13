"""Строки интерфейса, которых нет в версии для Mac.

Добавляются к общим `core.UI_STRINGS` вызовом install(). Коротко: люди это видят
на бегу, объяснения ограничений только отвлекают.
"""

from . import core

WIN_STRINGS = {
    "ru": {
        "mic_title": "Микрофон недоступен",
        "mic_msg": "Проверь, что микрофон подключён и не занят другой программой.",
        "mic_blocked": "Микрофон выключен",
        "mic_blocked_msg": "Разреши доступ: Параметры → Конфиденциальность → Микрофон.",
        "paste_blocked_title": "Не вставилось",
        "paste_blocked_msg": "Текст в буфере обмена — нажми Ctrl+V.",
        "error_title": "Ошибка распознавания",
        "hk_err_empty": "Нажми клавишу или сочетание",
        "hk_err_too_many": "Не больше трёх клавиш",
        "hk_err_reserved": "Esc и Enter уже заняты",
        "hk_err_typing": "Эта клавиша нужна для набора текста — добавь Ctrl или Alt",
        "hk_warn_alt": "Alt в программах открывает меню",
        "hk_warn_win": "Win открывает меню «Пуск»",
        "hk_warn_shift": "Shift нужен для заглавных букв",
    },
    "en": {
        "mic_title": "Microphone unavailable",
        "mic_msg": "Check that the microphone is connected and not used by another app.",
        "mic_blocked": "Microphone is off",
        "mic_blocked_msg": "Allow access: Settings → Privacy → Microphone.",
        "paste_blocked_title": "Couldn't paste",
        "paste_blocked_msg": "The text is on the clipboard — press Ctrl+V.",
        "error_title": "Transcription error",
        "hk_err_empty": "Press a key or a combination",
        "hk_err_too_many": "Three keys at most",
        "hk_err_reserved": "Esc and Enter are already taken",
        "hk_err_typing": "This key is needed for typing — add Ctrl or Alt",
        "hk_warn_alt": "Alt opens menus in apps",
        "hk_warn_win": "Win opens the Start menu",
        "hk_warn_shift": "Shift is needed for capital letters",
    },
    "uk": {
        "mic_title": "Мікрофон недоступний",
        "mic_msg": "Перевір, що мікрофон підключений і не зайнятий іншою програмою.",
        "mic_blocked": "Мікрофон вимкнено",
        "mic_blocked_msg": "Дозволь доступ: Параметри → Конфіденційність → Мікрофон.",
        "paste_blocked_title": "Не вставилося",
        "paste_blocked_msg": "Текст у буфері обміну — натисни Ctrl+V.",
        "error_title": "Помилка розпізнавання",
        "hk_err_empty": "Натисни клавішу або поєднання",
        "hk_err_too_many": "Не більше трьох клавіш",
        "hk_err_reserved": "Esc і Enter уже зайняті",
        "hk_err_typing": "Ця клавіша потрібна для набору тексту — додай Ctrl або Alt",
        "hk_warn_alt": "Alt у програмах відкриває меню",
        "hk_warn_win": "Win відкриває меню «Пуск»",
        "hk_warn_shift": "Shift потрібен для великих літер",
    },
    "de": {
        "mic_title": "Mikrofon nicht verfügbar",
        "mic_msg": "Prüfe, ob das Mikrofon angeschlossen und frei ist.",
        "mic_blocked": "Mikrofon ist aus",
        "mic_blocked_msg": "Zugriff erlauben: Einstellungen → Datenschutz → Mikrofon.",
        "paste_blocked_title": "Nicht eingefügt",
        "paste_blocked_msg": "Der Text ist in der Zwischenablage — drücke Strg+V.",
        "error_title": "Erkennungsfehler",
        "hk_err_empty": "Drücke eine Taste oder Kombination",
        "hk_err_too_many": "Höchstens drei Tasten",
        "hk_err_reserved": "Esc und Enter sind schon belegt",
        "hk_err_typing": "Diese Taste braucht man zum Tippen — nimm Strg oder Alt dazu",
        "hk_warn_alt": "Alt öffnet in Programmen das Menü",
        "hk_warn_win": "Win öffnet das Startmenü",
        "hk_warn_shift": "Shift braucht man für Großbuchstaben",
    },
}


def install() -> None:
    for lang, items in WIN_STRINGS.items():
        core.UI_STRINGS.setdefault(lang, {}).update(items)
