# -*- coding: utf-8 -*-
"""agent-time 量化器的离线测试（不连 Hermes、不连 MAS）。

两条硬约束：
  1. 数值口径：agent_time / LLM / 工具 / token / 投影 必须能从 fixture 精确复算；
  2. 只读：量化器以 mode=ro 打开 Hermes 的 sqlite，且源码里不允许出现写 SQL。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import pathlib
import re
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import mas_agent_time as m  # noqa: E402

JOB = "52b88b0ffca0"


def _iso(epoch):
    return dt.datetime.fromtimestamp(epoch).astimezone().isoformat()


def build_home(tmp_path, *, fires):
    """fires: [(started_epoch, finished_epoch, status, source, error, session{...}|None)]"""
    profile = tmp_path / "profiles" / "mas"
    (profile / "cron").mkdir(parents=True)

    ex = sqlite3.connect(profile / "cron" / "executions.db")
    ex.execute("CREATE TABLE executions (id TEXT PRIMARY KEY, job_id TEXT, source TEXT, process_id TEXT,"
               " pid INTEGER, process_started_at INTEGER, status TEXT, handoff_pending INTEGER,"
               " handoff_started_at TEXT, claimed_at TEXT, started_at TEXT, finished_at TEXT, error TEXT,"
               " delivery_outcome TEXT, scheduled_instant TEXT)")
    st = sqlite3.connect(profile / "state.db")
    st.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, started_at REAL,"
               " ended_at REAL, end_reason TEXT, message_count INTEGER, tool_call_count INTEGER,"
               " api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER,"
               " cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,"
               " estimated_cost_usd REAL, archived INTEGER)")

    for i, (start, end, status, source, error, sess) in enumerate(fires):
        ex.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("exec%d" % i, JOB, source, "p%d" % i, 100 + i, int(start * 1000), status, 0, None,
                    _iso(start), _iso(start), _iso(end), error, "suppressed", None))
        if sess:
            st.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("cron_%s_%d" % (JOB, int(start)), "cron", sess.get("model", "test-model"),
                        start, end, sess.get("end_reason", "cron_complete"),
                        sess.get("message_count", 10), sess.get("tool_calls", 5),
                        sess.get("llm_calls", 3), sess.get("input_tokens", 100),
                        sess.get("output_tokens", 20), sess.get("cache_read_tokens", 7),
                        sess.get("cache_write_tokens", 0), sess.get("reasoning_tokens", 3),
                        sess.get("estimated_cost_usd", 0.0), 0))
    ex.commit()
    st.commit()
    ex.close()
    st.close()
    return profile


NOW = dt.datetime(2026, 9, 26, 18, 0, 0).timestamp()


def test_active_time_and_projection():
    start, end = NOW - 3600, NOW - 3600 + 600  # 10 分钟
    runs = [{"status": "completed", "source": "builtin", "agent_time_s": 600.0,
             "llm_calls": 6, "tool_calls": 6, "tokens_total": 300000, "error_kind": ""}]
    s = m.summarize(runs, cadence_minutes=30, health_window=3)
    assert s["agent_time_median_s"] == 600.0
    assert s["fires_per_day"] == 48.0
    assert s["agent_time_per_day_h"] == pytest.approx(8.0)          # 600s * 48 / 3600
    assert s["utilization_pct"] == pytest.approx(33.333, rel=1e-3)
    assert s["tokens_per_day"] == pytest.approx(300000 * 48)
    assert s["auto_path_healthy"] is True
    assert start < end


def test_failed_fire_does_not_dilute_projection():
    runs = [{"status": "failed", "source": "builtin", "agent_time_s": 0.2,
             "llm_calls": None, "tool_calls": None, "tokens_total": None,
             "error_kind": "依赖缺失/环境错配"},
            {"status": "completed", "source": "direct", "agent_time_s": 900.0,
             "llm_calls": 20, "tool_calls": 85, "tokens_total": 2_400_000, "error_kind": ""}]
    s = m.summarize(runs, cadence_minutes=30, health_window=3)
    assert s["agent_time_median_s"] == 900.0          # 失败样本不参与
    assert s["runs_failed"] == 1 and s["runs_completed"] == 1
    assert s["success_rate"] == pytest.approx(0.5)
    assert s["failure_kinds"] == {"依赖缺失/环境错配": 1}


def test_auto_path_unhealthy_when_recent_builtin_fails():
    runs = [{"status": "failed", "source": "builtin", "agent_time_s": 0.2, "llm_calls": None,
             "tool_calls": None, "tokens_total": None, "error_kind": "x"},
            {"status": "completed", "source": "direct", "agent_time_s": 900.0,
             "llm_calls": 1, "tool_calls": 1, "tokens_total": 1, "error_kind": ""}]
    s = m.summarize(runs, cadence_minutes=30, health_window=3)
    assert s["auto_path_healthy"] is False
    assert s["unattended_runs"] == 1 and s["unattended_completed"] == 0


def test_running_fire_is_not_a_failure():
    """正在飞的巡检不该被探针判死。"""
    runs = [{"status": "running", "source": "builtin", "agent_time_s": None, "llm_calls": None,
             "tool_calls": None, "tokens_total": None, "error_kind": "", "started_at": None}]
    s = m.summarize(runs, cadence_minutes=30, health_window=3)
    assert s["auto_path_healthy"] is True
    assert s["runs_running"] == 1 and s["runs_failed"] == 0


def test_no_builtin_sample_is_unknown_not_healthy():
    runs = [{"status": "completed", "source": "direct", "agent_time_s": 900.0, "llm_calls": 1,
             "tool_calls": 1, "tokens_total": 1, "error_kind": ""}]
    assert m.summarize(runs, health_window=3)["auto_path_healthy"] is None


def test_recovered_when_latest_builtin_succeeds_after_older_failure():
    """修复后：最近一次自动 fire 成功、失败只在更早位置 = 已恢复（不是 FAILING）。"""
    runs = [{"status": "completed", "source": "builtin", "agent_time_s": 199.8,
             "llm_calls": 3, "tool_calls": 4, "tokens_total": 96760, "error_kind": "", "started_at": NOW},
            {"status": "failed", "source": "builtin", "agent_time_s": 0.45,
             "llm_calls": None, "tool_calls": None, "tokens_total": None, "error_kind": "env", "started_at": NOW - 1800},
            {"status": "completed", "source": "direct", "agent_time_s": 303.0,
             "llm_calls": 6, "tool_calls": 5, "tokens_total": 229604, "error_kind": "", "started_at": NOW - 3600}]
    s = m.summarize(runs, cadence_minutes=30, health_window=3)
    assert s["auto_path_healthy"] is False       # 严格窗口口径：窗口内仍有失败
    assert s["auto_recovered"] is True           # 但最近一次已经成功 → 已恢复
    assert s["auto_stale_failures"] == 1
    assert "已恢复" in m.render_markdown(s, runs, profile="mas", job_id=JOB)


def test_classify_error_signature():
    err = ("Traceback (most recent call last):\n"
           "RuntimeError: Failed to initialize OpenAI client: No module named 'pydantic_core._pydantic_core'")
    assert "pydantic_core" in m.classify_error(err)
    assert m.classify_error("") == ""


def test_end_to_end_join_from_fixture(tmp_path):
    fires = [
        (NOW - 7200, NOW - 7200 + 1154, "completed", "direct", "",
         {"llm_calls": 20, "tool_calls": 85, "message_count": 106, "input_tokens": 231262,
          "output_tokens": 128152, "cache_read_tokens": 2112512, "reasoning_tokens": 119709}),
        (NOW - 3600, NOW - 3600 + 0.4, "failed", "builtin",
         "RuntimeError: Failed to initialize OpenAI client: No module named 'pydantic_core._pydantic_core'", None),
        (NOW - 600, NOW - 600 + 0.3, "failed", "builtin",
         "RuntimeError: Failed to initialize OpenAI client: No module named 'pydantic_core._pydantic_core'", None),
    ]
    profile = build_home(tmp_path, fires=fires)
    executions = m.load_executions(profile / "cron" / "executions.db", job_id=JOB, limit=10)
    sessions = m.load_cron_sessions(profile / "state.db", limit=10)
    runs = m.join_runs(executions, sessions, [])
    assert len(runs) == 3
    ok = [r for r in runs if r["status"] == "completed"][0]
    assert ok["agent_time_s"] == pytest.approx(1154.0, abs=1.0)
    assert ok["llm_calls"] == 20 and ok["tool_calls"] == 85
    assert ok["tokens_total"] == 231262 + 128152 + 2112512 + 119709
    s = m.summarize(runs, cadence_minutes=30, health_window=3)
    assert s["auto_path_healthy"] is False           # 最近窗口内有 builtin 失败
    assert s["failure_kinds"] == {"运行时环境错配：gateway 的 sys.path 挂着不匹配的 pydantic_core（3.11 venv vs 3.14 解释器）": 2}
    md = m.render_markdown(s, runs, profile="mas", job_id=JOB)
    assert "无人值守路径故障" in md
    assert "1154.0 s" in md


def test_failed_fire_does_not_inherit_next_session(tmp_path):
    """回归：失败 fire 是秒级空转，不能去认领下一次成功运行的 session 账本。"""
    fires = [
        (NOW - 1500, NOW - 1200, "completed", "direct", "",
         {"llm_calls": 6, "tool_calls": 5, "input_tokens": 200000, "output_tokens": 20000,
          "cache_read_tokens": 9000, "cache_write_tokens": 0, "reasoning_tokens": 100}),
        (NOW - 1000, NOW - 999.6, "failed", "builtin",
         "RuntimeError: Failed to initialize OpenAI client: No module named 'pydantic_core._pydantic_core'", None),
    ]
    profile = build_home(tmp_path, fires=fires)
    runs = m.join_runs(m.load_executions(profile / "cron" / "executions.db", job_id=JOB),
                       m.load_cron_sessions(profile / "state.db"), [])
    failed = [r for r in runs if r["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["session_id"] is None, "失败 fire 不该匹配到任何 session"
    assert failed[0]["tokens_total"] is None
    assert failed[0]["llm_calls"] is None
    ok = [r for r in runs if r["status"] == "completed"][0]
    assert ok["llm_calls"] == 6 and ok["tool_calls"] == 5


def test_check_mode_exit_codes(tmp_path, capsys):
    healthy = [(NOW - 300, NOW - 200, "completed", "builtin", "",
                {"llm_calls": 6, "tool_calls": 6, "input_tokens": 10, "output_tokens": 1,
                 "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0})]
    bad = healthy + [(NOW - 100, NOW - 99.6, "failed", "builtin",
                      "RuntimeError: Failed to initialize OpenAI client:"
                      " No module named 'pydantic_core._pydantic_core'", None)]
    only_direct = [(NOW - 300, NOW - 200, "completed", "direct", "",
                    {"llm_calls": 1, "tool_calls": 1, "input_tokens": 10, "output_tokens": 1,
                     "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0})]

    for name, fires, expected, marker in (
            ("healthy", healthy, 0, "AUTO_PATH=HEALTHY"),
            ("failing", bad, 3, "AUTO_PATH=FAILING"),
            ("unknown", only_direct, 4, "AUTO_PATH=UNKNOWN"),
            # 修复后场景：失败在前、最新一次成功 ⇒ 探针报已恢复并退 0
            ("recovered", bad[::-1] + [(NOW - 50, NOW - 50 + 199.8, "completed", "builtin", "",
                                       {"llm_calls": 3, "tool_calls": 4, "input_tokens": 10, "output_tokens": 1,
                                        "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0})],
             0, "AUTO_PATH=RECOVERED")):
        home = tmp_path / name
        build_home(home, fires=fires)
        args = ["--hermes-home", str(home), "--profile", "mas", "--job", JOB, "--check"]
        assert m.main(args) == expected, name
        assert marker in capsys.readouterr().out, name


def test_read_only_connection_rejects_writes(tmp_path):
    profile = build_home(tmp_path, fires=[(NOW, NOW + 1, "completed", "direct", "", None)])
    conn = m._connect_ro(profile / "cron" / "executions.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM executions")
    finally:
        conn.close()


def test_loading_does_not_modify_hermes_artifacts(tmp_path):
    profile = build_home(tmp_path, fires=[(NOW, NOW + 1, "completed", "direct", "", None)])
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (profile / "cron" / "executions.db", profile / "state.db")}
    m.load_executions(profile / "cron" / "executions.db", job_id=JOB)
    m.load_cron_sessions(profile / "state.db")
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (profile / "cron" / "executions.db", profile / "state.db")}
    assert before == after


def test_source_contains_no_write_sql():
    src = (ROOT / "tools" / "mas_agent_time.py").read_text(encoding="utf-8")
    bad = re.findall(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE|REPLACE\s+INTO)\b",
                     src, flags=re.I)
    assert bad == [], "量化器里出现写 SQL: %s" % bad
    assert "mode=ro" in src, "必须用只读 URI 打开 Hermes 的 sqlite"


def test_cli_no_write_prints_report(tmp_path, capsys):
    fires = [(NOW - 600, NOW - 600 + 300, "completed", "builtin", "",
              {"llm_calls": 6, "tool_calls": 6, "input_tokens": 1000, "output_tokens": 100,
               "cache_read_tokens": 10, "cache_write_tokens": 0, "reasoning_tokens": 5})]
    build_home(tmp_path, fires=fires)
    rc = m.main(["--hermes-home", str(tmp_path), "--profile", "mas", "--job", JOB, "--no-write"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "agent-time 量化" in out
    assert "无人值守路径健康" in out
