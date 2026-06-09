"""Tests for LaunchQueue — priority queue and scheduling logic."""
from datetime import datetime, timedelta
from app.service_context import ServiceContext
from models.account import Account


def _make_ctx():
    """Create a minimal ServiceContext for queue testing."""
    return ServiceContext(
        accounts=[
            Account(id="a1", name="大号", emu_instance_index="0"),
            Account(id="a2", name="小号", emu_instance_index="0"),
            Account(id="a3", name="材料号", emu_instance_index="1"),
        ],
        warehouse=[],
        config={},
        groups=[],
    )


class TestLaunchQueue:
    def test_enqueue_dequeue(self):
        from services.launch_queue import LaunchQueue
        ctx = _make_ctx()
        q = LaunchQueue(ctx)
        assert q.pending_count == 0

        q.enqueue("a1", "manual", priority=0)
        assert q.pending_count == 1
        assert q.is_queued("a1")

        q.dequeue("a1")
        assert q.pending_count == 0
        assert not q.is_queued("a1")

    def test_enqueue_dedup(self):
        from services.launch_queue import LaunchQueue
        ctx = _make_ctx()
        q = LaunchQueue(ctx)
        q.enqueue("a1", "manual", priority=0)
        q.enqueue("a1", "sanity", priority=2)  # should replace
        assert q.pending_count == 1
        import heapq
        e = heapq.heappop(q._pending)
        assert e.source == "sanity"
        assert e.sort_key[0] == 2

    def test_priority_ordering(self):
        from services.launch_queue import LaunchQueue
        from models.queue_entry import QueueEntry
        ctx = _make_ctx()
        q = LaunchQueue(ctx)
        q.enqueue("a3", "sanity", priority=2)
        q.enqueue("a1", "manual", priority=0)
        q.enqueue("a2", "schedule", priority=1)
        import heapq
        entries = [heapq.heappop(q._pending) for _ in range(3)]
        sources = [e.source for e in entries]
        # manual(0) < schedule(1) < sanity(2)
        assert sources == ["manual", "schedule", "sanity"]

    def test_not_before_blocks_launch(self):
        from services.launch_queue import LaunchQueue
        ctx = _make_ctx()
        q = LaunchQueue(ctx)
        future = datetime.now() + timedelta(hours=1)
        q.enqueue("a1", "sanity", priority=2, not_before=future)
        # Tick should not launch (future time)
        q._tick()
        # Should still be in queue
        assert q.pending_count == 1
        assert q.active_count == 0
        assert q.is_queued("a1")

    def test_same_emu_serial(self):
        """Accounts on the same emulator should not launch in parallel."""
        from services.launch_queue import LaunchQueue
        ctx = _make_ctx()
        q = LaunchQueue(ctx)
        q.resume()  # unpause for test
        q.enqueue("a1", "manual", priority=0)
        q.enqueue("a2", "manual", priority=0)

        # Simulate a1 launched (a2 on same emu should be skipped)
        q._active_emus["0"] = "a1"
        q._tick()
        # a2 should still be queued (same emu as a1)
        assert q.is_queued("a2")
        assert q.active_count == 1

        # Release a1
        q._active_emus.pop("0")
        q._tick()
        # a2 should now be launched
        assert not q.is_queued("a2")
        assert q.active_count == 1
        assert "a2" in q._active_emus.values()

    def test_pending_summary(self):
        from services.launch_queue import LaunchQueue
        ctx = _make_ctx()
        q = LaunchQueue(ctx)
        q.enqueue("a1", "manual", priority=0)
        q.enqueue("a2", "sanity", priority=2)
        s = q.pending_summary()
        assert "大号" in s
        assert "小号" in s

    def test_get_next_for(self):
        from services.launch_queue import LaunchQueue
        ctx = _make_ctx()
        q = LaunchQueue(ctx)
        future = datetime.now() + timedelta(hours=1)
        q.enqueue("a1", "sanity", priority=2, not_before=future)
        nxt = q.get_next_for("a1")
        assert nxt != "即将启动"  # far in future
        assert nxt != ""
        nxt2 = q.get_next_for("a2")
        assert nxt2 == ""  # not queued
