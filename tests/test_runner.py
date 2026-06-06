"""Tests for AccountRunner — launch, stop, process tracking."""
from datetime import datetime
from callbacks import ServiceContext


def _make_ctx():
    """Create a minimal ServiceContext for runner testing."""
    return ServiceContext(
        accounts=[
            {"id": "a1", "name": "test", "adb_address": "127.0.0.1:5555", "emu_instance_index": "0"},
        ],
        warehouse=[
            {"id": "w1", "path": "test.exe", "account_ref": "a1", "maa_type": "general", "launch_mode": "gui"},
        ],
        config={},
        groups=[],
    )


class TestAccountRunner:
    def test_launch_no_prog(self):
        from runner import AccountRunner
        ctx = _make_ctx()
        ctx.warehouse = []  # no programs
        r = AccountRunner(ctx)
        ok = r.launch(0)
        assert not ok
        assert not r.is_running("a1")

    def test_launch_duplicate(self):
        from runner import AccountRunner
        ctx = _make_ctx()
        r = AccountRunner(ctx)
        r._active["a1"] = ctx.accounts[0]
        ok = r.launch(0)
        assert not ok  # already running

    def test_stop_unknown(self):
        from runner import AccountRunner
        ctx = _make_ctx()
        r = AccountRunner(ctx)
        r.stop("nonexistent")
        assert r.active_count == 0

    def test_active_count(self):
        from runner import AccountRunner
        ctx = _make_ctx()
        r = AccountRunner(ctx)
        assert r.active_count == 0
        r._active["a1"] = ctx.accounts[0]
        assert r.active_count == 1
        assert r.is_running("a1")
        assert not r.is_running("a2")

    def test_active_ids(self):
        from runner import AccountRunner
        ctx = _make_ctx()
        r = AccountRunner(ctx)
        r._active["a1"] = ctx.accounts[0]
        r._active["a2"] = {"id": "a2"}
        ids = r.active_ids()
        assert "a1" in ids
        assert "a2" in ids
        assert len(ids) == 2

    def test_check_empty(self):
        """check_processes on empty state should not crash."""
        from runner import AccountRunner
        ctx = _make_ctx()
        r = AccountRunner(ctx)
        r.check_processes()  # no running processes

    def test_track_stats(self):
        from runner import AccountRunner
        ctx = _make_ctx()
        r = AccountRunner(ctx)
        today = datetime.now().strftime("%Y-%m-%d")
        r._track_stats(ctx.accounts[0])
        sd = ctx.accounts[0].get("stats", {}).get(today, {})
        assert sd.get("launches", 0) >= 1

    def test_preflight_check(self):
        from runner import AccountRunner
        ctx = _make_ctx()
        r = AccountRunner(ctx)
        ac = ctx.accounts[0]

        # OK case — use existing file
        import sys
        progs_ok = [{"id": "w1", "path": sys.executable, "account_ref": "a1"}]
        issues = r.preflight_check(ac, progs_ok)
        assert not any(i.startswith("❌") for i in issues)

        # Missing ADB
        ac2 = dict(ac)
        ac2["adb_address"] = ""
        issues = r.preflight_check(ac2, progs_ok)
        assert any("ADB" in i for i in issues)

        # Missing program
        progs_bad = [{"id": "w1", "path": "C:\\nonexistent\\MAA.exe", "account_ref": "a1"}]
        issues = r.preflight_check(ac, progs_bad)
        assert any("不存在" in i for i in issues)

    def test_log_signals(self):
        """Verify signals are properly connected."""
        try:
            from PySide6.QtCore import QObject
        except ImportError:
            return  # skip if no PySide6
        from runner import AccountRunner
        ctx = _make_ctx()
        r = AccountRunner(ctx)
        msgs = []
        r.log_msg.connect(lambda m: msgs.append(m))
        r.log_msg.emit("test message")
        assert "test message" in msgs
