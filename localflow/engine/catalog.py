"""Какие модели есть и откуда их качать.

Ключи моделей те же, что у версии для Mac (`core.MODELS`), чтобы настройки
и тексты меню совпадали. Файлы — сжатые модели whisper.cpp: они в 3–5 раз
меньше исходных, а качество на диктовке почти не страдает.
"""

from dataclasses import dataclass

HF_WHISPER = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"


@dataclass(frozen=True)
class ModelFile:
    file: str
    size: int      # байт, для проверки и полоски прогресса
    sha256: str

    @property
    def url(self) -> str:
        return HF_WHISPER + self.file

    @property
    def size_mb(self) -> int:
        return round(self.size / 1_000_000)


WHISPER_MODELS = {
    "tiny": ModelFile(
        "ggml-tiny-q5_1.bin", 32_152_673,
        "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7"),
    "base": ModelFile(
        "ggml-base-q5_1.bin", 59_707_625,
        "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898"),
    "small": ModelFile(
        "ggml-small-q5_1.bin", 190_085_487,
        "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb"),
    "medium": ModelFile(
        "ggml-medium-q5_0.bin", 539_212_467,
        "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f"),
    "large-v3-turbo": ModelFile(
        "ggml-large-v3-turbo-q5_0.bin", 574_041_195,
        "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"),
}

# Сборка движка распознавания, которую ставит установщик и проверяют тесты
WHISPER_ENGINE_REF = "b5130"
WHISPER_SERVER_EXE = "whisper-server.exe"
