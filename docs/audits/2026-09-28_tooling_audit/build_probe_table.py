"""Rebuild a consolidated probe summary + markdown table from the evidence txt files."""
import io, json, os, re

D = r"D:\1-2\MAAOrch\docs\audits\2026-09-28_tooling_audit"
rows = []
for fn in sorted(os.listdir(D)):
    if not fn.endswith(".txt"):
        continue
    txt = io.open(os.path.join(D, fn), encoding="utf-8").read()
    cmd = re.search(r"^CMD: (.*)$", txt, re.M)
    rc = re.search(r"^RC=(\S+) SECS=(\S+)$", txt, re.M)
    rows.append({
        "name": fn[:-4],
        "cmd": cmd.group(1) if cmd else "",
        "rc": rc.group(1) if rc else "?",
        "secs": rc.group(2) if rc else "?",
    })

with io.open(os.path.join(D, "_probe_summary.json"), "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=1)

lines = ["| # | 探针 | 命令（省略 python.exe 前缀） | rc | 秒 |", "| --- | --- | --- | --- | --- |"]
for i, r in enumerate(rows, 1):
    lines.append("| %d | %s | `%s` | %s | %s |" % (i, r["name"], r["cmd"], r["rc"], r["secs"]))
md = "\n".join(lines) + "\n"
with io.open(os.path.join(D, "_probe_table.md"), "w", encoding="utf-8") as f:
    f.write(md)
print(md)
print("rows:", len(rows), "| rc!=0:", [(r["name"], r["rc"]) for r in rows if r["rc"] != "0"])
