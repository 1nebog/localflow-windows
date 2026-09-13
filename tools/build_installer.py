"""Сборка установщика: LocalFlow.exe (PyInstaller) + движок + Inno Setup.

    python tools/build_installer.py --engine engine-bin

Результат: build/LocalFlow-Setup-<версия>.exe. Запускается в GitHub Actions
на Windows; нужны pyinstaller и Inno Setup 6.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from localflow import __version__, icons  # noqa: E402

BUILD = ROOT / "build"

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}, 0), prodvers=({v}, 0), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'LocalFlow'),
      StringStruct('FileDescription', 'LocalFlow — offline voice dictation'),
      StringStruct('FileVersion', '{s}'),
      StringStruct('InternalName', 'LocalFlow'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'LocalFlow.exe'),
      StringStruct('ProductName', 'LocalFlow'),
      StringStruct('ProductVersion', '{s}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def iscc() -> str:
    found = shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            for sub in ("Inno Setup 6", r"Programs\Inno Setup 6"):
                path = Path(base) / sub / "ISCC.exe"
                if path.exists():
                    return str(path)
    sys.exit("Inno Setup 6 не найден")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, help="папка со сборкой whisper.cpp")
    args = ap.parse_args()
    engine = Path(args.engine).resolve()
    if not (engine / "whisper-server.exe").exists():
        sys.exit(f"В {engine} нет whisper-server.exe")

    BUILD.mkdir(exist_ok=True)
    ico = BUILD / "LocalFlow.ico"
    ico.write_bytes(icons.ico_bytes())
    nums = ", ".join((__version__.split(".") + ["0", "0", "0"])[:3])
    (BUILD / "version_info.txt").write_text(VERSION_INFO.format(v=nums, s=__version__), encoding="utf-8")

    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "LocalFlow", "--windowed", "--onedir",
        "--icon", str(ico), "--version-file", str(BUILD / "version_info.txt"),
        "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "pyi"),
        "--specpath", str(BUILD),
        "--paths", str(ROOT),
        # в программе не нужны — только место занимают
        "--exclude-module", "tkinter", "--exclude-module", "pytest",
        "--exclude-module", "unittest", "--exclude-module", "pydoc",
        str(ROOT / "packaging" / "localflow_main.py"),
    ], check=True)

    shutil.copy(ROOT / "LICENSE", BUILD / "dist" / "LocalFlow" / "LICENSE.txt")
    subprocess.run([
        iscc(), f"/DAppVersion={__version__}", f"/DDistDir={BUILD / 'dist' / 'LocalFlow'}",
        f"/DEngineDir={engine}", f"/DIconFile={ico}", f"/O{BUILD}",
        str(ROOT / "packaging" / "LocalFlow.iss"),
    ], check=True)
    print(f"Готово: {BUILD / f'LocalFlow-Setup-{__version__}.exe'}")


if __name__ == "__main__":
    main()
