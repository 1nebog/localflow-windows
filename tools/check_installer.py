"""Проверка установщика на чистой Windows (GitHub Actions).

    python tools/check_installer.py build/LocalFlow-Setup-0.1.0.exe --models models

Тихо ставит программу, запускает LocalFlow.exe, ждёт готовой модели,
проверяет второй запуск и закрытие, затем тихо удаляет. Модель base берётся
из --models, чтобы не качать её заново.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import winreg
from pathlib import Path

LOCAL = Path(os.environ["LOCALAPPDATA"])
APP_DIR = LOCAL / "Programs" / "LocalFlow"
EXE = APP_DIR / "LocalFlow.exe"
DATA = LOCAL / "LocalFlow"
LOG = DATA / "logs" / "localflow.log"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def step(text: str) -> None:
    print(f"\n== {text}", flush=True)


def fail(text: str) -> None:
    print(f"ОШИБКА: {text}")
    if LOG.exists():
        print("---- журнал программы ----")
        print(LOG.read_text(encoding="utf-8", errors="replace")[-6000:])
    sys.exit(1)


def log_text() -> str:
    return LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""


def wait_log(marker: str, timeout: float) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if marker in log_text():
            return time.monotonic() - t0
        time.sleep(0.5)
    fail(f"в журнале нет «{marker}» за {timeout:.0f} c")


def wait_log_count(marker: str, count: int, timeout: float) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if log_text().count(marker) >= count:
            return
        time.sleep(0.5)
    fail(f"в журнале нет {count}-го «{marker}» за {timeout:.0f} c")


def run_value() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, "LocalFlow")[0]
    except OSError:
        return None


def running(name: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                         capture_output=True, text=True).stdout
    return name.lower() in out.lower()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("setup")
    ap.add_argument("--models", help="папка с уже скачанной моделью base")
    args = ap.parse_args()
    setup = Path(args.setup).resolve()
    print(f"Установщик: {setup.name}, {setup.stat().st_size / 1e6:.1f} МБ")

    step("Тихая установка")
    t0 = time.monotonic()
    subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    "/TASKS=autostart", f"/LOG={setup.parent / 'install.log'}"], check=True)
    print(f"за {time.monotonic() - t0:.1f} c")
    for path in (EXE, APP_DIR / "engine" / "whisper-server.exe", APP_DIR / "unins000.exe"):
        if not path.exists():
            fail(f"нет файла {path}")
    size = sum(f.stat().st_size for f in APP_DIR.rglob("*") if f.is_file())
    print(f"Папка программы: {size / 1e6:.0f} МБ")
    if run_value() != f'"{EXE}"':
        fail(f"автозапуск: {run_value()!r}")

    if args.models:
        (DATA / "models").mkdir(parents=True, exist_ok=True)
        for f in Path(args.models).glob("*.bin"):
            shutil.copy(f, DATA / "models" / f.name)

    step("Первый запуск")
    t0 = time.monotonic()
    proc = subprocess.Popen([str(EXE)])
    print(f"клавиша: {wait_log('Перехват клавиатуры включён', 60):.1f} c")
    print(f"модель готова: {wait_log('Готово: зажми', 240):.1f} c от запуска")
    wait_log("Панель: открываю окно", 30)            # первый запуск показывает панель
    if not running("whisper-server.exe"):
        fail("движок не запущен")
    if proc.poll() is not None:
        fail(f"программа закрылась сама, код {proc.returncode}")

    step("Второй запуск открывает настройки, а не вторую копию")
    second = subprocess.run([str(EXE)], timeout=60)
    print(f"второй запуск вышел с кодом {second.returncode}")
    wait_log("Команда от второго запуска: 1", 15)
    if proc.poll() is not None:
        fail("первая копия закрылась")

    step("Закрытие командой установщика")
    subprocess.run([str(EXE), "--quit"], timeout=60)
    try:
        proc.wait(20)
    except subprocess.TimeoutExpired:
        fail("программа не закрылась по --quit")
    time.sleep(2)
    if running("whisper-server.exe"):
        fail("движок остался висеть после выхода")
    if "Traceback" in log_text():
        fail("в журнале есть ошибки Python")

    step("Обновление поверх запущенной программы")
    proc = subprocess.Popen([str(EXE)])
    wait_log_count("Перехват клавиатуры включён", 2, 60)
    subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    "/TASKS=autostart", f"/LOG={setup.parent / 'update.log'}"], check=True, timeout=180)
    try:
        proc.wait(20)                      # установщик сам закрыл старую копию
    except subprocess.TimeoutExpired:
        fail("установщик не закрыл запущенную программу")
    if not EXE.exists():
        fail("после обновления нет LocalFlow.exe")
    print("обновилось, старая копия закрыта")

    step("Тихое удаление")
    subprocess.run([str(APP_DIR / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                   check=True)
    for _ in range(120):                  # удалятор доделывает работу в фоне
        if not EXE.exists() and run_value() is None and not APP_DIR.exists():
            break
        time.sleep(0.5)
    if EXE.exists():
        fail("программа не удалилась")
    if run_value() is not None:
        fail("автозапуск остался после удаления")
    if not (DATA / "models").exists():
        fail("тихое удаление стёрло модели — без вопроса их трогать нельзя")
    print("\nВсё в порядке")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
