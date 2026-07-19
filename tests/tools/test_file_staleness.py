#!/usr/bin/env python3
"""
Tests for file staleness detection in write_file and patch.

When a file is modified externally between the agent's read and write,
the write should include a warning so the agent can re-read and verify.

Run with:  python -m pytest tests/tools/test_file_staleness.py -v
"""

import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from tools import file_state
from tools.file_tools import (
    read_file_tool,
    write_file_tool,
    patch_tool,
    _check_file_staleness,
    _file_edit_require_read_enabled,
    _read_before_edit_error,
    _read_tracker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeReadResult:
    def __init__(self, content="line1\nline2\n", total_lines=2, file_size=100):
        self.content = content
        self._total_lines = total_lines
        self._file_size = file_size

    def to_dict(self):
        return {
            "content": self.content,
            "total_lines": self._total_lines,
            "file_size": self._file_size,
        }


class _FakeWriteResult:
    def __init__(self):
        self.bytes_written = 10

    def to_dict(self):
        return {"bytes_written": self.bytes_written}


class _FakePatchResult:
    def __init__(self):
        self.success = True

    def to_dict(self):
        return {"success": True, "diff": "--- a\n+++ b\n@@ ...\n"}


def _make_fake_ops(read_content="hello\n", file_size=6):
    fake = MagicMock()
    fake.read_file = lambda path, offset=1, limit=500: _FakeReadResult(
        content=read_content, total_lines=1, file_size=file_size,
    )
    fake.write_file = lambda path, content: _FakeWriteResult()
    fake.patch_replace = lambda path, old, new, replace_all=False: _FakePatchResult()
    return fake


# ---------------------------------------------------------------------------
# Core staleness check
# ---------------------------------------------------------------------------

class TestStalenessCheck(unittest.TestCase):

    def setUp(self):
        _read_tracker.clear()
        file_state.get_registry().clear()
        self._tmpdir = tempfile.mkdtemp()
        self._tmpfile = os.path.join(self._tmpdir, "stale_test.txt")
        with open(self._tmpfile, "w") as f:
            f.write("original content\n")

    def tearDown(self):
        _read_tracker.clear()
        file_state.get_registry().clear()
        try:
            os.unlink(self._tmpfile)
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    @patch("tools.file_tools._get_file_ops")
    def test_no_warning_when_file_unchanged(self, mock_ops):
        """Read then write with no external modification — no warning."""
        mock_ops.return_value = _make_fake_ops("original content\n", 18)
        read_file_tool(self._tmpfile, task_id="t1")

        result = json.loads(write_file_tool(self._tmpfile, "new content", task_id="t1"))
        self.assertNotIn("_warning", result)

    @patch("tools.file_tools._get_file_ops")
    def test_warning_when_file_modified_externally(self, mock_ops):
        """Read, then external modify, then write — should warn."""
        mock_ops.return_value = _make_fake_ops("original content\n", 18)
        read_file_tool(self._tmpfile, task_id="t1")

        # Simulate external modification
        time.sleep(0.05)
        with open(self._tmpfile, "w") as f:
            f.write("someone else changed this\n")

        result = json.loads(write_file_tool(self._tmpfile, "new content", task_id="t1"))
        self.assertIn("_warning", result)
        self.assertIn("modified since you last read", result["_warning"])

    @patch("tools.file_tools._get_file_ops")
    def test_no_warning_when_file_never_read(self, mock_ops):
        """Writing a file that was never read — no warning."""
        mock_ops.return_value = _make_fake_ops()
        result = json.loads(write_file_tool(self._tmpfile, "new content", task_id="t2"))
        self.assertNotIn("_warning", result)

    @patch("tools.file_tools._get_file_ops")
    def test_no_warning_for_new_file(self, mock_ops):
        """Creating a new file — no warning."""
        mock_ops.return_value = _make_fake_ops()
        new_path = os.path.join(self._tmpdir, "brand_new.txt")
        result = json.loads(write_file_tool(new_path, "content", task_id="t3"))
        self.assertNotIn("_warning", result)
        try:
            os.unlink(new_path)
        except OSError:
            pass

    @patch("tools.file_tools._get_file_ops")
    def test_different_task_isolated(self, mock_ops):
        """Task A reads, file changes, Task B writes — no warning for B."""
        mock_ops.return_value = _make_fake_ops("original content\n", 18)
        read_file_tool(self._tmpfile, task_id="task_a")

        time.sleep(0.05)
        with open(self._tmpfile, "w") as f:
            f.write("changed\n")

        result = json.loads(write_file_tool(self._tmpfile, "new", task_id="task_b"))
        self.assertNotIn("_warning", result)

    @patch("tools.file_tools._get_file_ops")
    def test_relative_path_uses_live_cwd_for_staleness_tracking(self, mock_ops):
        """Relative-path stale tracking must follow the live terminal cwd."""
        start_dir = os.path.join(self._tmpdir, "start")
        live_dir = os.path.join(self._tmpdir, "worktree")
        os.makedirs(start_dir, exist_ok=True)
        os.makedirs(live_dir, exist_ok=True)

        start_file = os.path.join(start_dir, "shared.txt")
        live_file = os.path.join(live_dir, "shared.txt")
        with open(start_file, "w") as f:
            f.write("start copy\n")
        with open(live_file, "w") as f:
            f.write("live copy\n")

        fake_ops = _make_fake_ops("live copy\n", 10)
        fake_ops.env = SimpleNamespace(cwd=live_dir)
        fake_ops.cwd = start_dir
        mock_ops.return_value = fake_ops

        from tools import file_tools

        with file_tools._file_ops_lock:
            previous = file_tools._file_ops_cache.get("live_task")
            file_tools._file_ops_cache["live_task"] = fake_ops

        try:
            with patch.dict(os.environ, {"TERMINAL_CWD": start_dir}, clear=False):
                read_file_tool("shared.txt", task_id="live_task")

                time.sleep(0.05)
                with open(live_file, "w") as f:
                    f.write("live copy modified elsewhere\n")

                result = json.loads(
                    write_file_tool("shared.txt", "replacement", task_id="live_task")
                )
        finally:
            with file_tools._file_ops_lock:
                if previous is None:
                    file_tools._file_ops_cache.pop("live_task", None)
                else:
                    file_tools._file_ops_cache["live_task"] = previous

        self.assertIn("_warning", result)
        self.assertIn("modified since you last read", result["_warning"])


# ---------------------------------------------------------------------------
# Staleness in patch
# ---------------------------------------------------------------------------

class TestPatchStaleness(unittest.TestCase):

    def setUp(self):
        _read_tracker.clear()
        file_state.get_registry().clear()
        self._tmpdir = tempfile.mkdtemp()
        self._tmpfile = os.path.join(self._tmpdir, "patch_test.txt")
        with open(self._tmpfile, "w") as f:
            f.write("original line\n")

    def tearDown(self):
        _read_tracker.clear()
        file_state.get_registry().clear()
        try:
            os.unlink(self._tmpfile)
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    @patch("tools.file_tools._get_file_ops")
    def test_patch_warns_on_stale_file(self, mock_ops):
        """Patch should warn if the target file changed since last read."""
        mock_ops.return_value = _make_fake_ops("original line\n", 15)
        read_file_tool(self._tmpfile, task_id="p1")

        time.sleep(0.05)
        with open(self._tmpfile, "w") as f:
            f.write("externally modified\n")

        result = json.loads(patch_tool(
            mode="replace", path=self._tmpfile,
            old_string="original", new_string="patched",
            task_id="p1",
        ))
        self.assertIn("_warning", result)
        self.assertIn("modified since you last read", result["_warning"])

    @patch("tools.file_tools._get_file_ops")
    def test_patch_no_warning_when_fresh(self, mock_ops):
        """Patch with no external changes — no warning."""
        mock_ops.return_value = _make_fake_ops("original line\n", 15)
        read_file_tool(self._tmpfile, task_id="p2")

        result = json.loads(patch_tool(
            mode="replace", path=self._tmpfile,
            old_string="original", new_string="patched",
            task_id="p2",
        ))
        self.assertNotIn("_warning", result)


# ---------------------------------------------------------------------------
# Unit test for the helper
# ---------------------------------------------------------------------------

class TestCheckFileStalenessHelper(unittest.TestCase):

    def setUp(self):
        _read_tracker.clear()
        file_state.get_registry().clear()

    def tearDown(self):
        _read_tracker.clear()
        file_state.get_registry().clear()

    def test_returns_none_for_unknown_task(self):
        self.assertIsNone(_check_file_staleness("/tmp/x.py", "nonexistent"))

    def test_returns_none_for_unread_file(self):
        # Populate tracker with a different file
        from tools.file_tools import _read_tracker, _read_tracker_lock
        with _read_tracker_lock:
            _read_tracker["t1"] = {
                "last_key": None, "consecutive": 0,
                "read_history": set(), "dedup": {},
                "read_timestamps": {"/tmp/other.py": 12345.0},
            }
        self.assertIsNone(_check_file_staleness("/tmp/x.py", "t1"))

    def test_returns_none_when_stat_fails(self):
        from tools.file_tools import _read_tracker, _read_tracker_lock
        with _read_tracker_lock:
            _read_tracker["t1"] = {
                "last_key": None, "consecutive": 0,
                "read_history": set(), "dedup": {},
                "read_timestamps": {"/nonexistent/path": 99999.0},
            }
        # File doesn't exist → stat fails → returns None (let write handle it)
        self.assertIsNone(_check_file_staleness("/nonexistent/path", "t1"))


# ---------------------------------------------------------------------------
# Opt-in hard read-before-edit gate (CODING-HARNESS-REVIEW-2026-07-16 §3.2)
# ---------------------------------------------------------------------------

_GATE_ENV = "HERMES_FILE_EDIT_REQUIRE_READ"
_STRICT_ENV = "HERMES_FILE_EDIT_STRICT_EXACT"


class _EditPrecisionEnvMixin:
    """Shared env hygiene: neither flag env var may leak between tests."""

    def _clean_env(self):
        """Enter a patch.dict(os.environ) scope with both flags removed.

        patch.dict snapshots the whole environ and restores it on exit, so
        deletions and additions made inside the scope are both undone.
        """
        ctx = patch.dict(os.environ)
        ctx.start()
        self.addCleanup(ctx.stop)
        os.environ.pop(_GATE_ENV, None)
        os.environ.pop(_STRICT_ENV, None)

    def _bind_current_modules(self):
        """Bind self.ft / self.fs to the modules CURRENTLY in sys.modules.

        tests/agent/test_verification_stop_caching.py purges every
        agent.*/tools.*/hermes_* entry from sys.modules and re-imports
        run_agent. When this file runs after it in the same pytest process,
        the symbols imported at collection time up top point at ORPHANED
        module objects, while ``patch("tools.file_tools._get_file_ops")``
        (resolved at test RUN time) patches the fresh replacement module —
        so the mock never binds and the test fails order-dependently
        (observed in the CODING-HARNESS-REVIEW-2026-07-16 verification
        sweep; the pre-existing classes above carry the same latent hazard).
        Calling through the current module keeps the patched module and the
        exercised functions identical regardless of suite ordering.
        """
        import importlib
        self.ft = importlib.import_module("tools.file_tools")
        self.fs = importlib.import_module("tools.file_state")


class TestReadBeforeEditGate(unittest.TestCase, _EditPrecisionEnvMixin):

    def setUp(self):
        self._bind_current_modules()
        self.ft._read_tracker.clear()
        self.fs.get_registry().clear()
        self._clean_env()
        self._tmpdir = tempfile.mkdtemp()
        self._tmpfile = os.path.join(self._tmpdir, "gate_test.txt")
        with open(self._tmpfile, "w") as f:
            f.write("existing content\n")

    def tearDown(self):
        self.ft._read_tracker.clear()
        self.fs.get_registry().clear()
        for name in os.listdir(self._tmpdir):
            try:
                os.unlink(os.path.join(self._tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    # -- default: gate OFF, behavior identical to before ------------------

    @patch("tools.file_tools._get_file_ops")
    def test_default_off_unread_write_succeeds(self, mock_ops):
        """Default config: writing an unread existing file is NOT blocked."""
        mock_ops.return_value = _make_fake_ops("existing content\n", 17)
        result = json.loads(self.ft.write_file_tool(self._tmpfile, "new", task_id="g0"))
        self.assertNotIn("error", result)

    @patch("tools.file_tools._get_file_ops")
    def test_default_off_unread_patch_succeeds(self, mock_ops):
        """Default config: patching an unread existing file is NOT blocked."""
        mock_ops.return_value = _make_fake_ops("existing content\n", 17)
        result = json.loads(self.ft.patch_tool(
            mode="replace", path=self._tmpfile,
            old_string="existing", new_string="patched", task_id="g0b",
        ))
        self.assertNotIn("error", result)

    # -- gate ON ----------------------------------------------------------

    @patch("tools.file_tools._get_file_ops")
    def test_gate_blocks_write_on_unread_existing_file(self, mock_ops):
        fake = _make_fake_ops("existing content\n", 17)
        fake.write_file = MagicMock(return_value=_FakeWriteResult())
        mock_ops.return_value = fake
        os.environ[_GATE_ENV] = "1"
        result = json.loads(self.ft.write_file_tool(self._tmpfile, "new", task_id="g1"))
        self.assertIn("error", result)
        self.assertIn("Read-before-edit gate", result["error"])
        fake.write_file.assert_not_called()

    @patch("tools.file_tools._get_file_ops")
    def test_gate_blocks_patch_on_unread_existing_file(self, mock_ops):
        fake = _make_fake_ops("existing content\n", 17)
        fake.patch_replace = MagicMock(return_value=_FakePatchResult())
        mock_ops.return_value = fake
        os.environ[_GATE_ENV] = "1"
        result = json.loads(self.ft.patch_tool(
            mode="replace", path=self._tmpfile,
            old_string="existing", new_string="patched", task_id="g2",
        ))
        self.assertIn("error", result)
        self.assertIn("Read-before-edit gate", result["error"])
        fake.patch_replace.assert_not_called()

    @patch("tools.file_tools._get_file_ops")
    def test_gate_allows_write_after_read(self, mock_ops):
        mock_ops.return_value = _make_fake_ops("existing content\n", 17)
        os.environ[_GATE_ENV] = "1"
        self.ft.read_file_tool(self._tmpfile, task_id="g3")
        result = json.loads(self.ft.write_file_tool(self._tmpfile, "new", task_id="g3"))
        self.assertNotIn("error", result)

    @patch("tools.file_tools._get_file_ops")
    def test_gate_allows_patch_after_read(self, mock_ops):
        mock_ops.return_value = _make_fake_ops("existing content\n", 17)
        os.environ[_GATE_ENV] = "1"
        self.ft.read_file_tool(self._tmpfile, task_id="g4")
        result = json.loads(self.ft.patch_tool(
            mode="replace", path=self._tmpfile,
            old_string="existing", new_string="patched", task_id="g4",
        ))
        self.assertNotIn("error", result)

    @patch("tools.file_tools._get_file_ops")
    def test_gate_exempts_new_file_creation(self, mock_ops):
        """Creating a file that doesn't exist yet needs no prior read."""
        mock_ops.return_value = _make_fake_ops()
        os.environ[_GATE_ENV] = "1"
        new_path = os.path.join(self._tmpdir, "brand_new_gated.txt")
        result = json.loads(self.ft.write_file_tool(new_path, "content", task_id="g5"))
        self.assertNotIn("error", result)

    @patch("tools.file_tools._get_file_ops")
    def test_gate_treats_own_write_as_implicit_read(self, mock_ops):
        """Create a NEW file, then patch it without reading — allowed,
        because a successful write records an implicit read (the agent
        knows the content it just wrote)."""
        fake = _make_fake_ops()

        def _real_write(p, content):
            # Fake ops that actually lands bytes on disk, so the post-write
            # timestamp/registry bookkeeping (which stats the file) records
            # the implicit read like the real backend would.
            with open(p, "w") as f:
                f.write(content)
            return _FakeWriteResult()

        fake.write_file = _real_write
        fake.patch_replace = MagicMock(return_value=_FakePatchResult())
        mock_ops.return_value = fake
        os.environ[_GATE_ENV] = "1"
        new_path = os.path.join(self._tmpdir, "created_then_patched.txt")
        create = json.loads(self.ft.write_file_tool(new_path, "seed content\n", task_id="g6"))
        self.assertNotIn("error", create)
        result = json.loads(self.ft.patch_tool(
            mode="replace", path=new_path,
            old_string="seed", new_string="grown", task_id="g6",
        ))
        self.assertNotIn("error", result)
        fake.patch_replace.assert_called_once()

    @patch("tools.file_tools._get_file_ops")
    def test_gate_blocks_v4a_update_of_unread_file(self, mock_ops):
        fake = _make_fake_ops("existing content\n", 17)
        fake.patch_v4a = MagicMock(return_value=_FakePatchResult())
        mock_ops.return_value = fake
        os.environ[_GATE_ENV] = "1"
        v4a = (
            "*** Begin Patch\n"
            f"*** Update File: {self._tmpfile}\n"
            "-existing content\n"
            "+updated content\n"
            "*** End Patch"
        )
        result = json.loads(self.ft.patch_tool(mode="patch", patch=v4a, task_id="g7"))
        self.assertIn("error", result)
        self.assertIn("Read-before-edit gate", result["error"])
        fake.patch_v4a.assert_not_called()

    # -- flag resolution (env vs config) ----------------------------------

    def test_flag_env_wins_over_config_in_both_directions(self):
        with patch("hermes_cli.config.load_config_readonly",
                   return_value={"file_edit_require_read": True}):
            os.environ[_GATE_ENV] = "0"
            self.assertFalse(self.ft._file_edit_require_read_enabled())
            os.environ.pop(_GATE_ENV, None)
            self.assertTrue(self.ft._file_edit_require_read_enabled())
        with patch("hermes_cli.config.load_config_readonly",
                   return_value={"file_edit_require_read": False}):
            os.environ[_GATE_ENV] = "1"
            self.assertTrue(self.ft._file_edit_require_read_enabled())
            os.environ.pop(_GATE_ENV, None)
            self.assertFalse(self.ft._file_edit_require_read_enabled())

    def test_helper_returns_none_when_disabled_even_if_unread(self):
        """The gate helper itself is inert while the flag is off."""
        self.assertIsNone(
            self.ft._read_before_edit_error(self._tmpfile, self._tmpfile, "never-read-task")
        )


# ---------------------------------------------------------------------------
# Strict-exact wiring through patch_tool (CODING-HARNESS-REVIEW-2026-07-16 §3.3)
# ---------------------------------------------------------------------------

class TestStrictExactWiring(unittest.TestCase, _EditPrecisionEnvMixin):

    def setUp(self):
        self._bind_current_modules()
        self.ft._read_tracker.clear()
        self.fs.get_registry().clear()
        self._clean_env()
        self._tmpdir = tempfile.mkdtemp()
        self._tmpfile = os.path.join(self._tmpdir, "strict_test.txt")
        with open(self._tmpfile, "w") as f:
            f.write("alpha beta\n")

    def tearDown(self):
        self.ft._read_tracker.clear()
        self.fs.get_registry().clear()
        try:
            os.unlink(self._tmpfile)
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    @patch("tools.file_tools._get_file_ops")
    def test_default_call_omits_strict_kwarg(self, mock_ops):
        """Flag off: patch_replace is called with the historical positional
        signature and NO strict_exact kwarg — so existing fakes/backends
        without the parameter keep working unchanged."""
        fake = _make_fake_ops("alpha beta\n", 11)
        fake.patch_replace = MagicMock(return_value=_FakePatchResult())
        mock_ops.return_value = fake
        json.loads(self.ft.patch_tool(
            mode="replace", path=self._tmpfile,
            old_string="alpha", new_string="gamma", task_id="s1",
        ))
        args, kwargs = fake.patch_replace.call_args
        self.assertNotIn("strict_exact", kwargs)

    @patch("tools.file_tools._get_file_ops")
    def test_env_flag_passes_strict_kwarg(self, mock_ops):
        fake = _make_fake_ops("alpha beta\n", 11)
        fake.patch_replace = MagicMock(return_value=_FakePatchResult())
        mock_ops.return_value = fake
        os.environ[_STRICT_ENV] = "1"
        json.loads(self.ft.patch_tool(
            mode="replace", path=self._tmpfile,
            old_string="alpha", new_string="gamma", task_id="s2",
        ))
        args, kwargs = fake.patch_replace.call_args
        self.assertTrue(kwargs.get("strict_exact"))

    @patch("tools.file_tools._get_file_ops")
    def test_env_flag_passes_strict_kwarg_to_v4a(self, mock_ops):
        fake = _make_fake_ops("alpha beta\n", 11)
        fake.patch_v4a = MagicMock(return_value=_FakePatchResult())
        mock_ops.return_value = fake
        os.environ[_STRICT_ENV] = "1"
        v4a = (
            "*** Begin Patch\n"
            f"*** Update File: {self._tmpfile}\n"
            "-alpha beta\n"
            "+gamma beta\n"
            "*** End Patch"
        )
        json.loads(self.ft.patch_tool(mode="patch", patch=v4a, task_id="s3"))
        args, kwargs = fake.patch_v4a.call_args
        self.assertTrue(kwargs.get("strict_exact"))


if __name__ == "__main__":
    unittest.main()
