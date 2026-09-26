# -*- coding: utf-8 -*-
"""AUTO-MAS 脚本恢复（2026-08-25 07:50 事故：48 脚本注册表被清空）。

数据来源:
  - 脚本全量: tools/_scriptconfig_dump.json   (08-24 01:40, 48 个脚本含全部用户配置)
  - 队列绑定: tools/_queueconfig.json         (08-24 09:31, 104 个队列项原始 ScriptId)
  - 模拟器清单: tools/_emustatus.json          (l-3/l-5 模仿器名/索引)

流程: scripts/add(type=MAA) -> scripts/update(Info/Emulator/Run) ->
      scripts/user/add -> scripts/user/update(全量用户配置) ->
      queue/item/update(重绑 ScriptId) -> l-3/l-5 最小补建。

新脚本 ID 表落盘 tools/_restore_map.json（旧ID -> 新ID），失败项记录尾部。
"""
import json
import time
import urllib.request

API = "http://100.79.173.69:36163"
DUMP = r"D:\1-2\MAAOrch\tools\_scriptconfig_dump.json"
QUEUE = r"D:\1-2\MAAOrch\tools\_queueconfig.json"
EMUSTATUS = r"D:\1-2\MAAOrch\tools\_emustatus.json"
MAP_FILE = r"D:\1-2\MAAOrch\tools\_restore_map.json"
EMU_ID = "6e09a6bb-3126-412e-8412-9c90fb0c4499"


def api(path, body, t=15, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(
                API + path, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            # 直连，绕过本地代理(127.0.0.1:7890 黑掉 tailscale 地址导致超时)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            r = json.loads(opener.open(req, timeout=t).read())
            return r
        except Exception as e:
            last = e
            time.sleep(1)
    raise last


def log(*a):
    print(*a, flush=True)


def main():
    dump = json.load(open(DUMP, encoding="utf-8"))
    sids = [k for k in dump if k != "instances"]
    # 续跑支持：已有 map 则跳过已建脚本
    try:
        sid_map = json.load(open(MAP_FILE, encoding="utf-8"))
    except Exception:
        sid_map = {}
    if isinstance(sid_map, dict) and "map" in sid_map:
        sid_map = sid_map["map"]
    done = set(sid_map.keys()) & set(sids)
    log(f"dump 脚本 {len(sids)} 个, 已建 {len(done)} 个, 待建 {len(sids) - len(done)} 个")

    new_map = dict(sid_map)
    fails = []
    for i, old in enumerate(sids):
        if old in new_map and new_map[old]:
            continue
        v = dump[old]
        name = (v.get("Info") or {}).get("Name", "?")
        # 1) 建脚本
        try:
            r = api("/api/scripts/add", {"type": "MAA"})
        except Exception as e:
            fails.append(("add", old, name, str(e)[:100]))
            log(f"[{i+1}/{len(sids)}] FAIL add {name}: {str(e)[:100]}")
            continue
        if r.get("code") != 200 or not r.get("scriptId"):
            fails.append(("add", old, name, str(r.get("message") or r.get("error") or r)))
            log(f"[{i+1}/{len(sids)}] FAIL add {name}: {r.get('message')}")
            continue
        new = r["scriptId"]
        new_map[old] = new
        json.dump({"map": new_map, "fails": fails[:50]},
                  open(MAP_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        try:
            # 2) 脚本级配置
            r = api("/api/scripts/update", {"scriptId": new, "data": {
                "Info": v.get("Info", {}),
                "Emulator": v.get("Emulator", {}),
                "Run": v.get("Run", {}),
            }})
            if r.get("code") != 200:
                fails.append(("update", old, name, str(r.get("message") or r)))
                log(f"[{i+1}/{len(sids)}] FAIL update {name}: {r.get('message')}")
            # 3) 用户 + 用户配置
            ud = (v.get("SubConfigsInfo") or {}).get("UserData") or {}
            for uid, uentry in ud.items():
                if uid == "instances":
                    continue
                ru = api("/api/scripts/user/add", {"scriptId": new})
                if ru.get("code") != 200 or not ru.get("userId"):
                    fails.append(("user_add", old, name, str(ru.get("message") or ru)))
                    log(f"[{i+1}/{len(sids)}] FAIL user_add {name}: {ru.get('message')}")
                    continue
                nuid = ru["userId"]
                payload = {k: v2 for k, v2 in uentry.items() if k != "instances"}
                r2 = api("/api/scripts/user/update", {"scriptId": new, "userId": nuid, "data": payload})
                if r2.get("code") != 200:
                    fails.append(("user_update", old, name, str(r2.get("message") or r2)))
                    log(f"[{i+1}/{len(sids)}] FAIL user_update {name}: {r2.get('message')}")
        except Exception as e:
            fails.append(("detail", old, name, str(e)[:100]))
            log(f"[{i+1}/{len(sids)}] FAIL detail {name}: {str(e)[:100]}")
        log(f"[{i+1}/{len(sids)}] OK {name} -> {new}")
        if i % 5 == 0:
            time.sleep(0.3)

    # 4) 补跑：add 失败的脚本再来一轮（含 update/user 后续补齐）
    retry_needed = [s for s in sids if not new_map.get(s)]
    if retry_needed:
        log(f"补跑 {len(retry_needed)} 个 add 失败脚本...")
        for old in retry_needed:
            v = dump[old]
            name = (v.get("Info") or {}).get("Name", "?")
            try:
                r = api("/api/scripts/add", {"type": "MAA"})
                if r.get("code") != 200 or not r.get("scriptId"):
                    log(f"  补跑 FAIL add {name}: {r.get('message')}")
                    continue
                new = r["scriptId"]
                new_map[old] = new
                json.dump({"map": new_map, "fails": fails[:50]},
                          open(MAP_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                api("/api/scripts/update", {"scriptId": new, "data": {
                    "Info": v.get("Info", {}),
                    "Emulator": v.get("Emulator", {}),
                    "Run": v.get("Run", {}),
                }})
                ud = (v.get("SubConfigsInfo") or {}).get("UserData") or {}
                for uid, uentry in ud.items():
                    if uid == "instances":
                        continue
                    ru = api("/api/scripts/user/add", {"scriptId": new})
                    if ru.get("code") == 200 and ru.get("userId"):
                        payload = {k: v2 for k, v2 in uentry.items() if k != "instances"}
                        api("/api/scripts/user/update", {"scriptId": new, "userId": ru["userId"],
                                                         "data": payload})
                log(f"  补跑 OK {name} -> {new}")
            except Exception as e:
                log(f"  补跑 FAIL {name}: {str(e)[:100]}")

    # 5) 队列重绑
    q = json.load(open(QUEUE, encoding="utf-8"))
    bind_ok = bind_fail = 0
    for qid, qv in q.items():
        if qid == "instances":
            continue
        sic = (qv.get("SubConfigsInfo") or {}).get("QueueItem") or {}
        qname = (qv.get("Info") or {}).get("Name", "?")
        for inst in sic.get("instances", []):
            iid = inst["uid"]
            old = (sic.get(iid) or {}).get("Info", {}).get("ScriptId", "")
            if not old or old == "-":
                continue
            new = new_map.get(old)
            if not new:
                log(f"  队列 {qname}: 旧脚本 {old[:8]} 无映射(可能是l-3/l-5), 跳过")
                bind_fail += 1
                continue
            r = None
            try:
                r = api("/api/queue/item/update", {"queueId": qid, "queueItemId": iid,
                                                   "data": {"Info": {"ScriptId": new}}})
            except Exception as e:
                bind_fail += 1
                log(f"  队列 {qname}: FAIL 重绑 {old[:8]} -> {new[:8]}: {str(e)[:80]}")
                continue
            if r.get("code") == 200:
                bind_ok += 1
            else:
                bind_fail += 1
                log(f"  队列 {qname}: FAIL 重绑 {old[:8]} -> {new[:8]}: {r.get('message')}")
    log(f"队列重绑 OK={bind_ok} FAIL={bind_fail}")

    # 5) l-3/l-5 最小补建（MAA Path 未知, 留空待用户填写）
    emu = json.load(open(EMUSTATUS, encoding="utf-8"))
    emus = {}
    data = emu.get("data", {})
    if isinstance(data, dict):
        for node in data.values():
            for idx, e in (node.items() if isinstance(node, dict) else []):
                title = (e or {}).get("title", "")
                if title.lower().startswith("l-"):
                    emus[str(idx)] = title
    for idx, qname in (("44", "临时-肉鸽-l-3"), ("34", "临时-肉鸽-l-5")):
        title = emus.get(idx, "")
        if not title:
            log(f"补建 {qname}: 模拟器#{idx} 标题未找到, 跳过")
            continue
        r = api("/api/scripts/add", {"type": "MAA"})
        if r.get("code") != 200:
            log(f"补建 {title}: add FAIL {r.get('message')}")
            continue
        nsid = r["scriptId"]
        r = api("/api/scripts/update", {"scriptId": nsid, "data": {
            "Info": {"Name": title, "Path": ""},
            "Emulator": {"Id": EMU_ID, "Index": idx},
            "Run": {},
        }})
        for qid, qv in q.items():
            if qid == "instances":
                continue
            if (qv.get("Info") or {}).get("Name") != qname:
                continue
            sic = (qv.get("SubConfigsInfo") or {}).get("QueueItem") or {}
            for inst in sic.get("instances", []):
                api("/api/queue/item/update", {"queueId": qid, "queueItemId": inst["uid"],
                                               "data": {"Info": {"ScriptId": nsid}}})
                break
        log(f"补建 {title} (模拟器#{idx}) -> {nsid}  [MAA Path 待用户填写]")

    # 6) 汇总
    log("=" * 40)
    log(f"脚本映射 {len(new_map)} 个 -> tools/_restore_map.json")
    if fails:
        log(f"失败 {len(fails)} 条:")
        for f in fails[:30]:
            log("  ", f)
    else:
        log("全部恢复成功")


def qname_new_for(title):
    if "古茶" in title or "l-5" in title:
        return "临时-肉鸽-l-5"
    return "临时-肉鸽-l-3"


if __name__ == "__main__":
    main()
