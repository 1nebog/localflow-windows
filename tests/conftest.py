"""Все проверки пишут настройки, историю и журналы во временную папку,
а не в настоящие папки программы на этом компьютере."""

import os
import tempfile

os.environ.setdefault("LOCALFLOW_HOME", tempfile.mkdtemp(prefix="lf-test-"))
