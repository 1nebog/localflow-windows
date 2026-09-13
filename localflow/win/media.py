"""Пауза музыки и видео на время диктовки.

Через тот же «пульт», которым Windows управляет медиа с клавиатуры и из
всплывающего окна громкости (System Media Transport Controls). Он знает
настоящее состояние каждого плеера — Spotify, YouTube в браузере, плеер
Windows, — поэтому ставим на паузу только то, что правда играет, и после
записи включаем обратно только это. Клавишу «плей/пауза» не жмём: она
переключает вслепую и могла бы запустить то, что стояло на паузе.

На Mac то же самое сделано через MediaRemote. Нет пульта (старая Windows,
урезанная сборка) — пауза просто не работает, диктовка не страдает.
"""

import asyncio
import logging
import queue
import threading

log = logging.getLogger("localflow")

PLAYING = 4     # GlobalSystemMediaTransportControlsSessionPlaybackStatus
PAUSED = 5


class MediaPause:
    """pause() и resume() не ждут: вся работа идёт по очереди в своём потоке,
    поэтому быстрое «нажал-отпустил» не перепутает порядок."""

    def __init__(self, manager_factory=None):
        self.enabled = True
        self._factory = manager_factory or _request_manager
        self._jobs: queue.Queue = queue.Queue()
        self._paused: list[str] = []      # кого поставили на паузу мы
        self._manager = None
        self._broken = False
        self._thread = None

    def warm_up(self) -> None:
        """Подключиться к пульту заранее: первый запрос — до полусекунды."""
        self._submit(self._sessions)

    def pause(self) -> None:
        if self.enabled and not self._broken:
            self._submit(self._pause)

    def resume(self) -> None:
        if not self._broken:
            self._submit(self._resume)

    def wait(self, timeout: float = 5.0) -> None:
        """Дождаться очереди — для тестов."""
        done = threading.Event()
        self._submit(lambda: _set(done))
        done.wait(timeout)

    # --- поток пульта ---

    def _submit(self, job) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="media")
            self._thread.start()
        self._jobs.put(job)

    def _loop(self) -> None:
        loop = asyncio.new_event_loop()
        while True:
            job = self._jobs.get()
            try:
                result = job()
                if asyncio.iscoroutine(result):
                    loop.run_until_complete(result)
            except Exception as exc:
                log.warning("Пауза музыки: %s", exc)

    async def _sessions(self):
        if self._manager is None:
            try:
                self._manager = await self._factory()
                log.info("Пауза музыки: пульт медиа подключён")
            except Exception as exc:
                self._broken = True
                log.info("Пауза музыки недоступна: %s", exc)
                return []
        return list(self._manager.get_sessions())

    async def _pause(self) -> None:
        if self._paused:
            return
        for s in await self._sessions():
            try:
                if s.get_playback_info().playback_status != PLAYING:
                    continue
                if await s.try_pause_async():
                    self._paused.append(s.source_app_user_model_id)
                    log.info("Пауза музыки: %s", s.source_app_user_model_id)
            except Exception as exc:
                log.warning("Пауза музыки не прошла: %s", exc)

    async def _resume(self) -> None:
        ids, self._paused = self._paused, []
        if not ids:
            return
        for s in await self._sessions():
            try:
                app_id = s.source_app_user_model_id
                # Человек мог сам что-то включить или закрыть плеер — трогаем
                # только то, что мы остановили и что до сих пор на паузе
                if app_id in ids and s.get_playback_info().playback_status == PAUSED:
                    await s.try_play_async()
                    ids.remove(app_id)
                    log.info("Музыка дальше: %s", app_id)
            except Exception as exc:
                log.warning("Музыка не включилась обратно: %s", exc)


def _set(event: threading.Event) -> None:
    event.set()


async def _request_manager():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )
    return await Manager.request_async()
