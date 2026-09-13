# LocalFlow для Windows

Диктовка голосом в любое окно. Зажал клавишу, сказал, отпустил — текст
появился там, где стоит курсор. Работает без интернета: речь не уходит
никуда с компьютера.

> **Первая версия готовится.** Когда установщик появится на странице
> [Releases](https://github.com/1nebog/localflow-windows/releases), инструкция ниже заработает.

## Установка

1. Скачай `LocalFlow-Setup-….exe` со страницы
   [Releases](https://github.com/1nebog/localflow-windows/releases/latest).
2. Запусти. Если Windows покажет «Система Windows защитила ваш компьютер» —
   нажми **Подробнее** → **Выполнить в любом случае**. Так бывает с любой
   программой без платной цифровой подписи.
3. Права администратора не нужны. После установки откроются настройки,
   а LocalFlow один раз скачает модель распознавания (~60 МБ).

Нужна Windows 10 или 11 (64-бит). Видеокарта не обязательна.

## Как пользоваться

- **Зажми правый Ctrl** и говори. Отпустил — текст вставился.
- **Коротко нажми** — запись идёт сама. Закончить — нажми ещё раз или **Enter**.
- **Esc** — отменить.
- Значок у часов: меню, история, пауза. **Настройки…** — клавиша,
  микрофон, язык, модель, автозапуск.

## Удаление

Параметры Windows → Приложения → LocalFlow → Удалить. Установщик спросит,
удалить ли заодно настройки, историю и скачанные модели.

---

# LocalFlow for Windows

Voice dictation into any window. Hold a key, speak, release — the text
appears where your cursor is. Works offline: your voice never leaves your
computer.

> **First version coming soon.** Once the installer is on the
> [Releases](https://github.com/1nebog/localflow-windows/releases) page, the steps below apply.

## Install

1. Download `LocalFlow-Setup-….exe` from
   [Releases](https://github.com/1nebog/localflow-windows/releases/latest).
2. Run it. If Windows says "Windows protected your PC", click
   **More info** → **Run anyway** (this happens to any app without a paid
   code-signing certificate).
3. No admin rights needed. Settings open after install, and LocalFlow
   downloads the speech model once (~60 MB).

Requires Windows 10 or 11 (64-bit). A graphics card is optional.

## Use

- **Hold Right Ctrl** and speak. Release — the text is pasted.
- **Tap** to keep recording hands-free. Tap again or press **Enter** to finish.
- **Esc** cancels.
- Tray icon: menu, history, pause. **Settings…** — key, microphone,
  language, model, start with Windows.

## Uninstall

Windows Settings → Apps → LocalFlow → Uninstall. You'll be asked whether to
also delete settings, history and downloaded models.

## License

MIT. Speech recognition: [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (MIT).
