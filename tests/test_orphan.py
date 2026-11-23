"""POSIX half: a painter must die with the app there too.

Windows has the kill-on-close job object. Linux and macOS have nothing equivalent that
survives SIGKILL, so the child watches its parent instead - and the parent spawns it in
its own session so a stray group can be reaped deliberately.

fluxgen.py is standalone (the app starts it with -I, no package imports), so the
watchdog lives there; these tests import it as a module, which is safe because torch is
only imported inside functions.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from murmur import fluxgen, proc

POSIX = os.name != "nt"
ROOT = pathlib.Path(__file__).resolve().parent.parent


class WatchParentUnitTest(unittest.TestCase):
    def test_no_parent_pid_means_no_watch(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MURMUR_PARENT_PID", None)
            self.assertFalse(fluxgen.watch_parent())

    def test_a_bad_parent_pid_is_ignored(self):
        with mock.patch.dict(os.environ, {"MURMUR_PARENT_PID": "not-a-pid"}):
            self.assertFalse(fluxgen.watch_parent())

    def test_a_changed_parent_counts_as_gone(self):
        # getppid() returning something other than the recorded pid is the whole test,
        # and it must not be confused with "the same parent, later".
        with mock.patch.object(fluxgen.os, "getppid", return_value=4242):
            self.assertFalse(fluxgen.parent_is_gone(4242))
            self.assertTrue(fluxgen.parent_is_gone(1))

    def test_windows_leaves_it_to_the_job_object(self):
        if not POSIX:
            self.skipTest("POSIX only")
        with mock.patch.object(fluxgen.sys, "platform", "win32"), \
                mock.patch.dict(os.environ, {"MURMUR_PARENT_PID": str(os.getpid())}):
            self.assertFalse(fluxgen.watch_parent())


@unittest.skipUnless(POSIX, "POSIX process semantics")
class WatchedChildTest(unittest.TestCase):
    """End to end: kill the app, the painter follows - in the middle of its work."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="rh-orphan-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, name: str, source: str) -> pathlib.Path:
        path = self.tmp / name
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return path

    def test_a_spawned_child_gets_its_own_session(self):
        child = proc.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    if os.getsid(child.pid) == child.pid:
                        return
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            self.fail("the child was not put in its own session, so its group cannot be reaped")
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_a_watched_painter_dies_when_the_app_is_killed(self):
        child_script = self._write("painter.py", f"""
            import sys, time
            sys.path.insert(0, {str(ROOT)!r})
            from murmur import fluxgen
            assert fluxgen.watch_parent(), "the watchdog declined to start"
            time.sleep(120)
        """)
        parent_script = self._write("app.py", f"""
            import sys, time
            sys.path.insert(0, {str(ROOT)!r})
            from murmur import proc
            child = proc.spawn([sys.executable, {str(child_script)!r}])
            print(child.pid, flush=True)
            time.sleep(120)
        """)

        parent = subprocess.Popen([sys.executable, str(parent_script)],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(parent.stdout.close)
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        grandchild = int(parent.stdout.readline().strip())
        self.addCleanup(lambda: _kill(grandchild))
        time.sleep(1.5)  # let the watchdog take its first look

        parent.kill()  # the app is gone, with no chance to clean up
        parent.wait(timeout=10)

        deadline = time.time() + 10
        while time.time() < deadline:
            if not _alive(grandchild):
                return
            time.sleep(0.2)
        self.fail(f"painter {grandchild} outlived its app by more than 10 s")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill(pid: int) -> None:
    try:
        os.kill(pid, 9)
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
