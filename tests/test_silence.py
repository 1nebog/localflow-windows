"""Сторож тишины: когда человек говорит, а когда правда замолчал."""

from localflow.core import SilenceWatch

LOCK_AUTOSTOP = 5.0     # столько тишины — и замок сам останавливает запись


def run(levels, watch=None, start=0.0, step=0.1):
    """Прогоняет замеры громкости по 10 раз в секунду. Возвращает (сторож,
    максимальная тишина, которую он насчитал)."""
    w = watch or SilenceWatch(start)
    now, worst = start, 0.0
    for lvl in levels:
        now += step
        worst = max(worst, w.tick(lvl, now))
    return w, worst


def test_quiet_speech_is_not_mistaken_for_silence():
    """Человек говорит тихо (в 3 раза тише обычного) в тихой комнате.
    Раньше порог был жёстко 0.02 — такая речь в него не попадала, и замок
    обрывал человека на полуслове."""
    loud = [0.05, 0.08, 0.03] * 7           # начал обычным голосом
    quiet = [0.012, 0.008, 0.015, 0.0004] * 150   # тихо, с паузами, минуту
    w, worst = run(loud + quiet)
    assert w.heard
    assert worst < LOCK_AUTOSTOP


def test_real_silence_still_stops_the_recording():
    w, _ = run([0.05] * 20)                 # поговорил
    _, worst = run([0.0005] * 100, watch=w, start=2.0)   # и замолчал на 10 c
    assert worst > LOCK_AUTOSTOP


def test_noisy_room_is_not_speech():
    """Открытое окно и улица: фон высокий, но это не речь."""
    w, worst = run([0.018] * 300)           # ровный шум полминуты
    assert not w.heard
    assert worst > LOCK_AUTOSTOP            # значит, запись не будет висеть


def test_frozen_seconds_do_not_count_as_silence():
    """Whisper разбирал кусок длинной диктовки и забрал процессор: замеров
    громкости не было 7 секунд. Это время не тишина — человек всё это время
    говорил, просто слушать было некому."""
    w, _ = run([0.05] * 20)
    silent = w.tick(0.05, 2.0 + 7.0)        # семь секунд без замеров
    assert silent < LOCK_AUTOSTOP


def test_pause_around_the_freeze_is_still_counted():
    """Но если человек молчал ДО зависания и молчит ПОСЛЕ — тишина копится."""
    w, _ = run([0.05] * 10)                 # секунда речи
    now = 1.0
    for _ in range(30):                     # три секунды тишины
        now += 0.1
        silent = w.tick(0.0005, now)
    silent = w.tick(0.0005, now + 7.0)      # зависание на 7 c
    assert silent < LOCK_AUTOSTOP           # само по себе — ещё не стоп
    for _ in range(25):                     # ещё 2.5 c тишины после
        now += 0.1
        silent = w.tick(0.0005, now + 7.0)
    assert silent > LOCK_AUTOSTOP


def test_quiet_start_is_heard():
    """Человек начинает говорить тихо (или микрофон после обновления системы
    стал тише) в тихой комнате. Раньше планка начала речи была жёстко 0.02 —
    такой голос её не перепрыгивал, и таблетка всю диктовку висела на
    «Говорите…» без волны и таймера, а через 2 c краснела."""
    room = [0.0006] * 5
    quiet = [0.012, 0.008, 0.015, 0.0004] * 20
    w, _ = run(room + quiet)
    assert w.heard
    assert w.heard_at < 1.0


def test_quiet_speech_from_the_first_moment_is_heard():
    """Заговорил тихо сразу, ещё до первого замера тишины: фон сначала
    считается по голосу, но в первой же паузе между словами падает до
    комнаты — и голос становится слышен."""
    w, _ = run([0.012, 0.008, 0.015, 0.0004] * 10)
    assert w.heard
    assert w.heard_at < 1.0


def test_single_click_is_not_speech():
    """Один громкий щелчок (клавиша, звук старта) — ещё не голос."""
    w, _ = run([0.0006] * 5 + [0.05] + [0.0006] * 50)
    assert not w.heard


def test_summary_line():
    w, _ = run([0.0006] * 5 + [0.03] * 10)
    assert "речь услышана" in w.summary()
    w2, _ = run([0.0006] * 20)
    assert "НЕ услышана" in w2.summary()
