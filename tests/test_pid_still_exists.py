import os
import subprocess
import sys

from orchestrator import leerie


def test_pid_still_exists_true_for_self():
    assert leerie._pid_still_exists(os.getpid()) is True


def test_pid_still_exists_false_after_reaped_child():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = proc.pid
    proc.wait()
    assert leerie._pid_still_exists(pid) is False


def test_pid_still_exists_false_for_pid_far_past_pid_max():
    assert leerie._pid_still_exists(2**30) is False
