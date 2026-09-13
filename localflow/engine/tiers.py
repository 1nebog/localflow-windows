"""Какую модель распознавания тянет этот компьютер.

Замер берётся даром: при каждом запуске модель прогревается секундой
тишины, а Whisper обсчитывает окно в 30 секунд целиком при любой длине
фразы. Поэтому время прогрева почти равно времени обычной диктовки
(замер 2026-09-13: фраза дольше прогрева на 5–20%).

Зная, сколько считает одна модель, прикидываем остальные по относительной
цене. Цены взяты с запасом: промах вверх стоит лишней скачанной модели, а
промах вниз лишь чуть менее точного текста. После перехода новая модель
замеряется по-настоящему, и если она не тянет, откатываемся на ступень ниже.
"""

# От самой лёгкой к самой точной
LADDER = ("tiny", "base", "small", "large-v3-turbo")

# Цена относительно base. На процессоре замерено (Mac, 2 и 4 потока):
# small ×2.7, turbo ×10.5; на процессоре раннера GitHub turbo дороже, ×16.
# На видеокарте большие модели относительно дешевле.
COST_CPU = {"tiny": 0.5, "base": 1.0, "small": 3.5, "large-v3-turbo": 16.0}
COST_GPU = {"tiny": 0.6, "base": 1.0, "small": 2.5, "large-v3-turbo": 8.0}

# Сколько секунд комфортно ждать текст обычной фразы
TARGET_SEC = 2.5
# Уже выбранная модель остаётся, пока укладывается сюда: без этого запаса
# разброс замеров гонял бы выбор туда-сюда
KEEP_SEC = 3.5
# Дольше этого живую голосовую отмену не проверяем: каждая проверка — полный
# прогон модели, и на медленном компьютере он бы тормозил основную работу.
# «Отмена» в конце фразы всё равно ловится после распознавания.
LIVE_CHECK_MAX_SEC = 0.8


def _costs(on_gpu: bool) -> dict:
    return COST_GPU if on_gpu else COST_CPU


def estimate(model: str, measured_sec: float, on_gpu: bool) -> dict:
    """Прикидка времени фразы для каждой модели по замеру одной."""
    costs = _costs(on_gpu)
    unit = measured_sec / costs[model]
    out = {m: unit * costs[m] for m in LADDER}
    out[model] = measured_sec
    return out


def recommend(model: str, measured_sec: float, on_gpu: bool) -> str:
    """Самая точная модель, которая уложится в комфортное ожидание."""
    times = estimate(model, measured_sec, on_gpu)
    best = LADDER[0]
    for m in LADDER:
        if times[m] <= TARGET_SEC:
            best = m
    # Текущая модель точнее рекомендованной, но всё ещё терпимо быстрая —
    # не трогаем
    if (model in LADDER and LADDER.index(model) > LADDER.index(best)
            and measured_sec <= KEEP_SEC):
        return model
    return best


def prefer_gpu(gpu_sec: float | None, cpu_sec: float | None) -> bool:
    """Считать ли на видеокарте. Встроенная графика бывает медленнее
    процессора — тогда видеокарта только мешает."""
    if gpu_sec is None:
        return False
    if cpu_sec is None:
        return True
    return gpu_sec < cpu_sec * 0.9


def live_checks_ok(last_sec: float | None) -> bool:
    return last_sec is not None and last_sec <= LIVE_CHECK_MAX_SEC
