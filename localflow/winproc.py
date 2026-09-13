"""Дочерние процессы, которые не переживают программу.

Движки работают отдельными процессами и держат в памяти модели — это
гигабайт и больше. Если LocalFlow упадёт или его закроют из диспетчера
задач, движок не должен остаться висеть в памяти. На Windows для этого
процесс кладётся в «задание» (Job Object) с флагом «убить всех при
закрытии»: система сама прибьёт движок, когда исчезнет LocalFlow.
"""

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000

_job = None


def _kill_on_close_job():
    global _job
    if _job is not None or sys.platform != "win32":
        return _job
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info))  # ExtendedLimitInformation
    if not ok:
        return None
    _job = (k32, job)
    return _job


def spawn(args, **kwargs) -> subprocess.Popen:
    """Как subprocess.Popen, но без чёрного окна консоли и с автоуборкой."""
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
    proc = subprocess.Popen(args, **kwargs)
    job = _kill_on_close_job()
    if job is not None:
        k32, handle = job
        k32.AssignProcessToJobObject(handle, int(proc._handle))
    return proc
