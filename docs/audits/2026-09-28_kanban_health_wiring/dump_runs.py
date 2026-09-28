# -*- coding: utf-8 -*-
"""t_4cd871de 复核脚本 3（只读 sqlite）：查两卡 run/事件，核对崩溃与重跑。"""
import json
import os
import sqlite3
import sys

DB = os.environ.get("HERMES_KANBAN_DB") or os.path.expanduser(
    "~/AppData/Local/hermes/kanban/boards/maa-parallel-benchmark-20260926/kanban.db")
DB = DB.replace("\\", "/")


def main():
    uri = "file:%s?mode=ro" % DB
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r["name"] for r in cur.fetchall()]
    print("DB=%s" % DB)
    print("tables: %s" % ", ".join(tables))
    for tid in ("t_403cd83b", "t_4cd871de", "t_537fbff2", "t_67915887"):
        cur.execute("SELECT id,title,status,assignee,current_run_id,last_failure_error,workspace_kind,workspace_path,created_at,started_at,completed_at FROM tasks WHERE id=?", (tid,))
        row = cur.fetchone()
        if not row:
            print("\n== %s : NOT FOUND ==" % tid)
            continue
        print("\n== %s ==" % tid)
        print("  title=%s" % row["title"])
        print("  status=%s assignee=%s run=%s ws=%s@%s" % (row["status"], row["assignee"], row["current_run_id"], row["workspace_kind"], row["workspace_path"]))
        print("  started=%s completed=%s" % (row["started_at"], row["completed_at"]))
        print("  last_failure_error=%s" % (str(row["last_failure_error"])[:200],))
        if "runs" in tables:
            cur.execute("SELECT id,status,outcome,started_at,ended_at,substr(coalesce(error,''),1,160) e FROM runs WHERE task_id=? ORDER BY id", (tid,))
            for r in cur.fetchall():
                print("  run #%s %s/%s %s..%s err=%s" % (r["id"], r["status"], r["outcome"], r["started_at"], r["ended_at"], r["e"]))
    con.close()


if __name__ == "__main__":
    main()
