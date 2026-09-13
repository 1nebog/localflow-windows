"""Набор входов для сверки текстовой логики с маковой версией.

Все тексты придуманы для проверки, личных диктовок здесь нет.
Каждый случай: (имя функции, позиционные аргументы).
"""

RU_RAW = [
    "ну вот смотри я думаю что нам надо переделать эту кнопку",
    "Ну, вот, смотри, я думаю, что нам надо, короче, переделать эту кнопку.",
    "Короче, значит, давай так, в общем-то, сделаем.",
    "Э-э, м-м, я, эм, хотел сказать, что всё работает.",
    "Да, да, я тебя понял.",
    "Да, да.",
    "Да, да, да, да.",
    "Ну да, ну да.",
    "Слушай, а ты можешь, типа, проверить это?",
    "Вот эта кнопка, вот, должна быть синей.",
    "Это самое, так сказать, по сути, готово.",
    "Я так же самое думаю.",
    "Проверь пункт три. Проверь пункт три.",
    "Сделай это, сделай это, сделай это.",
    "Первое предложение с новой строки второе предложение",
    "Заголовок новый абзац текст абзаца",
    "Кнопку красной, вернее, синей сделай.",
    "Отправь Маше, я имею в виду Даше, файл.",
    "Говори точнее, пожалуйста.",
    "Во-первых, купить хлеб. Во-вторых, молоко. В-третьих, сыр.",
    "Во-первых, это важно.",
    "План на завтра: сделать тесты, собрать установщик.",
    "Привет. Как дела. Я тут подумал, что можно сделать иначе.",
    "Субтитры сделал DimaTorzok. Привет, как дела?",
    "Продолжение следует...",
    "Спасибо за просмотр!",
    "Отмена",
    "Всё, отмена.",
    "Я отменил встречу вчера.",
    "Это аккуратная диктовка: со знаками препинания, с заглавными буквами. Английские слова пишем as is: deploy, feature, pull request, LocalFlow. Привет, это мой текст.",
    "Англисякие слова пишем as is a result of the process. Нормальный текст дальше.",
    "Термины: LocalFlow, GitHub. А вот здесь уже речь.",
    "Нужно сделать deploy на staging и открыть pull request в GitHub.",
    "Переведи на английский: нужно выключить кнопку, пока форма невалидная.",
    "По-английски это называется deploy.",
    "по-английски, привет всем",
    "Покороче",
    "Сделай формальнее",
    "Сделай списком",
    "покороче бы это всё",
    "Посчитай, сколько будет два плюс два.",
    "Здравствуйте, Иван Петрович! Хотел уточнить по договору. С уважением, Анна.",
    "Добрый день! Отправляю отчёт за неделю. Там три раздела. Первый про продажи. Второй про расходы. Третий про планы. Спасибо.",
    "realize realize realize realize realize realize realize realize",
    "Андрей Андреевич Андреевич Андреевич Андреевич Андреевич Андреевич Андреевич Андреевич Андре",
    "пробел", "запятая", "точка", "тире", "новый абзац", "абзац", "энтер",
    "прабел", "тачка", "делаем абзац", "с новой строки",
    "Готово. Я закончил тест. С телефона. Всё работает.",
    "",
    "   ",
    "Ёлка, ёж, всё ещё, берёза.",
]

EN_RAW = [
    "um so you know I think we should like deploy this",
    "Um, you know, I think, like, we should deploy this.",
    "Right, okay, let's do it.",
    "First, buy milk. Second, bread. Third, cheese.",
    "Hi John, just checking in about the contract. Best regards, Anna",
    "Dear Mr. Smith, thank you for the update. Kind regards, Anna",
    "new line next sentence",
    "Thanks for watching!",
    "cancel",
    "space", "comma", "period", "paragraph",
    "Make the button red, I mean blue.",
]

DE_RAW = [
    "Ähm, also, quasi, das funktioniert.",
    "Sehr geehrter Herr Müller, vielen Dank. Mit freundlichen Grüßen, Anna",
    "Liebe Anna, danke im Voraus. Viele Grüße, Max",
    "neue Zeile nächster Satz",
    "Abbruch",
    "Untertitel im Auftrag des ZDF",
]

UK_RAW = [
    "Ну, коротше, типу, це працює.",
    "Шановний Олександре, дякую. З повагою, Анна",
    "Дякую за перегляд!",
    "відміна",
]

LLM_PAIRS = [
    ("я хочу чтобы ты проверил эту кнопку", "Я хочу, чтобы ты проверил эту кнопку."),
    ("я хочу чтобы ты проверил эту кнопку", "Я проверю эту кнопку."),
    ("посчитай сколько будет два плюс два", "Посчитай, сколько будет два плюс два?"),
    ("посчитай сколько будет два плюс два", "Два плюс два равно четырём, это сорок."),
    ("сделай отчёт за 2025 год", "Сделай отчёт за 2026 год."),
    ("давай сделаем функцию экспорта", "Давай сделаем функцию экспорта — чтобы сохранять."),
    ("давай сделаем — функцию экспорта", "Давай сделаем — функцию экспорта."),
    ("кнопка должна быть синей", "The button should be blue."),
    ("the button should be blue", "Кнопка должна быть синей."),
    ("короткий текст", "Короткий текст, который модель зачем-то сильно раздула лишними словами и деталями."),
    ("", ""),
]

LONG = (
    "Это первое предложение длинной диктовки, в нём есть запятые и паузы. "
    "Второе предложение продолжает мысль и тоже довольно длинное, чтобы "
    "проверить разбиение на куски. Третье предложение без точки в конце и "
    "с перечислением: первое, второе, третье, четвёртое, пятое "
) * 6

TITLES = [
    "app.tsx — LocalFlow — Visual Studio Code",
    "Pull request #42 · 1nebog/localflow-windows - Google Chrome",
    "Telegram",
    "",
    "README.md - Notepad",
]

APPS = ["Code", "Visual Studio Code", "Telegram", "WhatsApp", "Google Chrome",
        "Microsoft Edge", "Notepad", "Slack", "Discord", "Microsoft Word",
        "Terminal", "Windows Terminal", "Cursor", "Gemini", ""]

CTXS = [
    None,
    {},
    {"app": "Code", "title": "app.tsx — LocalFlow"},
    {"app": "Telegram", "title": "Чат"},
    {"app": "Google Chrome", "title": "Gemini"},
]

HISTORY_ITEMS = [
    {"text": "Нужно сделать deploy в Kubernetes и проверить GitHub Actions.", "raw": "", "ts": 1},
    {"text": "Проверь GitHub Actions ещё раз, Kubernetes упал.", "raw": "", "ts": 2},
    {"text": "Иван сказал, что Иван приедет. Встреча с Иваном завтра.", "raw": "", "ts": 3},
    {"text": "Обнови README и LocalFlow.", "raw": "", "ts": 4},
    {"text": "README готов, LocalFlow тоже.", "raw": "", "ts": 5},
]


def cases():
    out = []
    add = out.append
    for lang, texts in (("ru", RU_RAW), ("en", EN_RAW), ("de", DE_RAW), ("uk", UK_RAW)):
        for t in texts:
            add(("clean_text", [t, lang]))
            add(("email_tone", [t, lang]))
            add(("looks_like_lang", [t, lang]))
    for t in RU_RAW + EN_RAW + DE_RAW + UK_RAW:
        for fn in ("apply_editor_command", "apply_self_corrections",
                   "structure_by_voice", "casual_tone", "notes_tone",
                   "scrub_hallucinations", "is_hallucination",
                   "strip_loop_tail", "is_spam_repeat", "is_voice_cancel",
                   "strip_prompt_echo", "extract_translate_cmd",
                   "extract_rewrite_cmd", "cyrillic_share", "_merge_stub_sentences"):
            add((fn, [t]))
    for a, b in LLM_PAIRS:
        for fn in ("added_content_ratio", "invented_numbers",
                   "drop_invented_dashes", "text_similarity", "same_script",
                   "word_level_swap"):
            add((fn, [a, b]))
    for limit in (120, 420):
        add(("split_for_llm", [LONG, limit]))
    add(("split_for_llm", [LONG]))
    add(("split_for_llm", ["Короткий текст."]))
    for t in TITLES:
        add(("title_terms", [t]))
        add(("title_terms", [t, 2]))
    for a in APPS:
        add(("app_kind", [a]))
        add(("is_messenger_app", [a]))
    for c in CTXS:
        add(("context_hint", [c]))
    for lang in ("auto", "ru", "en", "uk", "de", "xx"):
        add(("build_initial_prompt", [lang]))
        add(("build_initial_prompt", [lang, ["LocalFlow", "GitHub", "Kubernetes"]]))
        add(("build_initial_prompt", [lang, ["очень-длинный-термин-" + str(i) for i in range(60)]]))
        add(("translate_shots", [lang]))
        add(("lang_label", [lang]))
    add(("extract_terms", [HISTORY_ITEMS]))
    add(("extract_terms", [HISTORY_ITEMS, 1, 3]))
    for key in ("speak", "transcribing", "silence", "cancelled", "no_such_key"):
        add(("tr", [key]))
    return out


def normalize(value):
    """JSON-совместимый вид: кортежи -> списки, числа округлены."""
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, set):
        return sorted(normalize(v) for v in value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return repr(value)
