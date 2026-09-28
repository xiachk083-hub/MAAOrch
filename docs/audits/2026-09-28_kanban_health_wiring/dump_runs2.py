# -*- coding: utf-8 -*-
"""t_4cd871de 复核脚本 4（只读 sqlite）：task_runs/task_events 崩溃与重跑取证。"""
import os
import sqlite3

DB = os.environ.get("HERMES_KANBAN_DB") or os.path.expanduser(
    "~/AppData/Local/hermes/kanban/boards/maa-parallel-benchmark-20260926/kanban.db")
DB = DB.replace("\\", "/")
TIDS = ("t_403cd83b", "t_4cd871de", "t_537fbff2")


def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("PRAGMA table_info(task_runs)")
    print("task_runs cols: %s" % [r[1] for r in cur.fetchall()])
    cur.execute("PRAGMA table_info(task_events)")
    print("task_events cols: %s" % [r[1] for r in cur.fetchall()])
    print()
    for tid in TIDS:
        print("== %s ==" % tid)
        cur.execute("SELECT * FROM task_runs WHERE task_id=? ORDER BY id", (tid,))
        for r in cur.fetchall():
            d = dict(r)
            print("  run#%s status=%s outcome=%s started=%s ended=%s hb=%s" % (
                d.get("id"), d.get("status"), d.get("outcome"), d.get("started_at"), d.get("ended_at"),
                d.get("last_heartbeat_at")))
            err = str(d.get("error") or "")
            if err:
                print("    error: %s" % err[:220].replace("\n", " "))
        cur.execute("SELECT kind, count(*) n FROM task_events WHERE task_id=? GROUP BY kind ORDER BY n DESC", (tid,))
        print("  events: %s" % ", ".join("%s=%d" % (r["kind"], r["n"]) for r in cur.fetchall()))
        cur.execute("SELECT kind, payload, created_at FROM task_events WHERE task_id=? AND kind IN ('crashed','failed','reclaimed','blocked','retry') ORDER BY created_at", (tid,))
        for r in cur.fetchall():
            print("  [%s] %s" % (r["kind"], str(r["payload"])[:260].replace("\n", " ")))
    con.close()


if __name__ == "__main__":
    main()
