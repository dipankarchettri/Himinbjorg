"""Puts the repo root on sys.path, and fails a test case that runs
longer than 2 s instead of letting the whole run hang."""
import signal

import pytest

CASE_SECONDS = 2


@pytest.fixture(autouse=True)
def _case_time_limit(request):
    def stop(signum, frame):
        raise TimeoutError(f"{request.node.name} ran longer than {CASE_SECONDS} s "
                           "(probably an infinite loop)")
    previous = signal.signal(signal.SIGALRM, stop)
    signal.alarm(CASE_SECONDS)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
