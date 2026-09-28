import json, os
from collections import Counter
ROOT=r"D:\1-2\MAAOrch"
rep=json.load(open(os.path.join(ROOT,"runtime","reports","stage_capability.json"),encoding="utf-8"))
state=json.load(open(os.path.join(ROOT,"runtime","state","stage_capability_state.json"),encoding="utf-8"))["attempts"]
rows={(r["account"],r["stage"]):r for r in rep["rows"]}

print("== SR-7 success 与 failed 的账号是否重叠 ==")
s={r["account"] for r in rep["rows"] if r["stage"]=="SR-7" and r["status"]=="success"}
f={r["account"] for r in rep["rows"] if r["stage"]=="SR-7" and r["status"]=="failed"}
print("  success 账号 %d, failed 账号 %d, 交集 %s" % (len(s),len(f),sorted(s&f)))
print("  failed 名单:", sorted(f))

print()
print("== 抽样 8 个 failed 对的行摘要 ==")
for pair in [("拍前看简介","AT-6"),("皮埃尔商店","CA-5"),("檀丝江喜","LS-6"),("tbNick_h56zy","SR-7"),
             ("白桑_","SR-7"),("爱尔奎特我老婆","SR-7"),("Gitano","SR-5"),("LEUNG","LS-5")]:
    r=rows[pair]
    fs=r.get("failures") or []
    days=sorted({x["day"] for x in fs})
    print("  %-14s %-4s fail_n=%-3s not_open_n=%-3s first=%s last=%s types=%s days=%s" % (
        pair[0],pair[1],r.get("fail_n"),r.get("not_open_n"),days[0] if days else "-",r.get("last_failure"),
        dict(r.get("failure_types") or {}), "%s~%s(%d)"%(days[0],days[-1],len(days)) if days else "-"))

print()
print("== 6 个 unknown 行的行摘要 ==")
for r in rep["rows"]:
    if r["status"]=="unknown":
        print("  %-16s %-6s attempt_n=%-3s selected_n=%-3s not_open_n=%-2s last_attempt=%s net_fail_n=%s impact=%s" % (
            r["account"],r["stage"],r.get("attempt_n"),r.get("selected_n"),r.get("not_open_n"),
            r.get("last_attempt"),r.get("net_fail_n"),r.get("impact")))

print()
print("== 寻找勇者的大魔王 / 摸摸摸摸鱼 09-07 尝试（按行内 LS-6 关键字）==")
for k,v in sorted(state.items(), key=lambda kv:(kv[1]["acct"],kv[1]["day"],kv[1]["file"])):
    if v["acct"] in ("寻找勇者的大魔王","摸摸摸摸鱼") and v["day"]=="2026-09-07":
        hit=[ln for ln in (v.get("lines") or []) if "LS-6" in ln]
        print("  %s %s | %s | drops=%s | LS6行=%d | result=%s" % (v["acct"],v["day"],v["file"],v.get("drops"),len(hit),v.get("result")))
        for ln in (v.get("lines") or [])[:8]:
            print("     |", ln)
