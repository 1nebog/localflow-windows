"""Меню и состояние значка в трее — без Windows, чтобы проверять тестами.

Пункты по образцу меню версии для Mac (модель, умное исправление, язык,
стиль, перевод, метод вставки, язык интерфейса, история), плюс пауза: на
Windows клавиша диктовки может мешать в играх.
"""

from dataclasses import dataclass, field
from typing import Callable

from . import core
from .core import LLM_MODES, MODEL_LABELS, TRANSLATE_TARGETS, lang_label, tr
from .engine.catalog import LLM_MODELS, WHISPER_MODELS
from .engine.download import is_ready
from .keys import key_name, normalize

UI_LANGS = [("ru", "Русский"), ("en", "English"), ("uk", "Українська"), ("de", "Deutsch")]
HISTORY_ITEMS = 10
HISTORY_CHARS = 60


@dataclass
class Item:
    label: str
    action: Callable[[], None] | None = None
    checked: bool = False
    enabled: bool = True
    children: list | None = field(default=None)
    # после выбора вернуть фокус в прежнее окно; не нужно, если пункт сам
    # открывает окно (иначе оно появится позади)
    focus_back: bool = True


def pretty_key(chord) -> str:
    """«правый Ctrl» вместо «RCtrl»."""
    parts = []
    for vk in normalize(chord):
        name = key_name(vk)
        if name[:1] in "LR" and name[1:] in ("Ctrl", "Alt", "Shift", "Win"):
            side = "key_left" if name[0] == "L" else "key_right"
            name = tr(side).format(key=name[1:])
        parts.append(name)
    return "+".join(parts)


def tray_state(app) -> str:
    """idle / loading / recording / busy / error / paused — для значка."""
    t = app.transcriber
    if app.paused:
        return "paused"
    if app.hook_error:
        return "error"
    if app.recorder.is_recording:
        return "recording"
    if app.dictation._busy:
        return "busy"
    if t.is_ready or (app.started and not t.is_loading and not t.load_error):
        return "idle"     # модель могла уснуть от простоя — проснётся по клавише
    if t.load_error and not t.is_loading:
        return "error"
    return "loading"


def gb(size: int) -> str:
    """«2,5 ГБ», «5 ГБ»."""
    num = f"{size / 1e9:.1f}".removesuffix(".0")
    if core._UI_LANG != "en":
        num = num.replace(".", ",")
    return f"{num} {tr('gb')}"


def polisher_status(app) -> str:
    """Что делает умное исправление, если есть что сказать. Пусто — ничего."""
    p = getattr(app, "polisher", None)
    if p is None or not p.enabled:
        return ""
    if p.progress is not None:
        have, total = p.progress
        return tr("st_llm_download").format(pct=f"{have * 100 // total}%" if total else "").strip()
    if p.is_loading:
        return tr("llm_loading")
    return ""


def status_text(app) -> str:
    state = tray_state(app)
    if state == "paused":
        return tr("st_paused")
    if state == "recording":
        return tr("st_recording")
    if state == "busy":
        return tr("transcribing")
    if app.hook_error:
        return tr("st_hook_error")
    progress = app.download or app.autotune.progress
    status = app.autotune.status
    if progress is not None:
        name, have, total = progress
        pct = f"{have * 100 // total}%" if total else ""
        return tr("st_download").format(model=MODEL_LABELS.get(name, name), pct=pct).strip()
    if status.startswith("switch:"):
        name = status.split(":", 1)[1]
        return tr("st_switch").format(model=MODEL_LABELS.get(name, name))
    if state == "error":
        return tr("st_error")
    if state == "loading":
        return tr("st_loading")
    return polisher_status(app) or tr("st_ready").format(key=pretty_key(app.keys.hotkey))


def tooltip(app) -> str:
    return f"LocalFlow — {status_text(app)}"[:127]


def _radio(options, current, pick) -> list[Item]:
    return [Item(label, (lambda v=value: pick(v)), checked=value == current)
            for value, label in options]


def _model_items(app) -> list[Item]:
    auto = app.autotune.auto
    current = app.transcriber.model_name
    auto_label = tr("model_auto")
    if auto and (app.transcriber.is_ready or app.started) and current in MODEL_LABELS:
        auto_label += f" · {MODEL_LABELS[current]}"
    items = [Item(auto_label, lambda: app.choose_model(None), checked=auto), None]
    for key, model in WHISPER_MODELS.items():
        label = MODEL_LABELS.get(key, key)
        if not is_ready(model, app.transcriber.models_dir):
            label += f" · {model.size_mb} {tr('mb')}"
        items.append(Item(label, (lambda k=key: app.choose_model(k)),
                          checked=not auto and key == current))
    return items


def llm_options(app) -> list[tuple[str, str]]:
    """Режимы умного исправления; у нескачанной модели — её размер."""
    out = []
    for mode in LLM_MODES:
        label = tr("llm_" + mode)
        model = LLM_MODELS.get(mode)
        if model is not None and not app.polisher.downloaded(mode):
            label += f" · {gb(model.size)}"
        out.append((mode, label))
    return out


def _history_items(app) -> list[Item]:
    history = app.dictation.history[:HISTORY_ITEMS]
    if not history:
        return [Item(tr("empty"), enabled=False)]
    items = []
    for it in history:
        text = " ".join(it["text"].split())
        label = text if len(text) <= HISTORY_CHARS else text[:HISTORY_CHARS - 1] + "…"
        items.append(Item(label, (lambda t=it["text"]: app.paste_from_history(t))))
    return items


def build(app) -> list:
    """Пункты меню; None — разделитель."""
    d = app.dictation
    langs = [(code, tr("lang_auto") if code == "auto" else lang_label(code))
             for code in core.LANGUAGES]
    targets = [(code, tr("translate_off") if code == "off" else lang_label(code))
               for code in TRANSLATE_TARGETS]
    styles = [(key, tr("style_" + key)) for key in core.STYLES]
    pastes = [("clipboard", tr("paste_fast")), ("type", tr("paste_type"))]
    return [
        Item(status_text(app), enabled=False),
        Item(tr("menu_settings"), app.open_panel, focus_back=False),
        None,
        Item(tr("model"), children=_model_items(app)),
        Item(tr("llm"), children=_radio(llm_options(app), app.polisher.mode, app.set_llm_mode)),
        Item(tr("language"), children=_radio(langs, app.transcriber.language, app.set_language)),
        Item(tr("style"), children=_radio(styles, d.style, app.set_style)),
        # переводит модель исправления — без неё пункт не работает
        Item(tr("translate_to"), children=_radio(targets, d.translate_to, app.set_translate),
             enabled=app.polisher.enabled),
        Item(tr("paste"), children=_radio(pastes, d.paste_method, app.set_paste)),
        Item(tr("ui_lang"), children=_radio(UI_LANGS, core._UI_LANG, app.set_ui_lang)),
        Item(tr("history"), children=_history_items(app)),
        None,
        Item(tr("menu_pause"), app.toggle_pause, checked=app.paused),
        Item(tr("menu_quit"), app.quit),
    ]
