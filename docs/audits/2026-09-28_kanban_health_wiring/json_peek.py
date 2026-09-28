# -*- coding: utf-8 -*-
"""解析 kanban_health --json 的落盘文件并打印摘要（证据用，只读）。用法：python json_peek.py <file.json>"""
import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
boards = d.get("boards") or []
print("schema=%s status=%s" % (d.get("schema"), d.get("status")))
print("line=%s" % d.get("line"))
print("totals=%s" % json.dumps(d.get("totals"), ensure_ascii=False))
print("boards=%d findings=%d" % (len(boards), sum(len(b.get("findings") or []) for b in boards)))
