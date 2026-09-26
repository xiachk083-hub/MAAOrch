#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审批单「执行前预检」——执行端（MAAOrch rig）侧，**只读，不驱动游戏**。

契约：docs/HANDOFF_ARKOPS_APPROVAL.md（本脚本是 §4 的可执行实现）

用法（用 rig 的解释器跑）：
  python tools/approval_preflight.py --host local-16416
  python tools/approval_preflight.py --host local-16416 --id <uuid>
  python tools/approval_preflight.py --sign <path/to/item.json>      # 补算本条 sha256 并回写
  python tools/approval_preflight.py --host local-16416 --no-live    # 只做静态校验，不碰 rig
  python tools/approval_preflight.py --host local-16416 --no-ocr      # 跳过证据图 OCR（只有 C14 降级）

退出码：0 = 至少一条单子全部通过（可执行）；1 = 无单 / 存在 blocker。

断言编号（与 ark-audit/contract_check.py 同语义，两侧独立实现才算复核）：
  C1 必填字段   C2 host/端口   C3 anchor 必填   C4 只官服   C5 uid_source=lobby_ocr
  C6 anchor 禁用键（撞号键）      C7 cost 键白名单            C8 task_type 白名单
  C9 人工放行   C10 sha256 复算  C11 未清场                 C12 大厅 UID 与 anchor 不符
  C13 链断      C14 evidence 胸牌自洽（存在 + 离线 OCR == anchor.uid）
  C15 区服证据（前台包名，非装机列表）                      C16 代价与关卡价表自洽
  C17 两源真分歧未裁定 ⇒ 拒（裁定值必须进 evidence.sanity_adjudicated）

设计约束（踩过的坑）：
  * 绝不把「名字/index/emu_index/旧表 adb」当锚点 —— 它们是跨机器撞号键（见契约 §2）。
  * 大厅 UID 只在 **page == 大厅** 时读；战斗/切场时读不到就报 blocker，**绝不猜**。
  * 大厅胸牌只有「名字 + ID」，**证不了区服** ⇒ 区服靠前台包名（`mCurrentFocus`），
    不能靠 `pm list packages`（16416 上官方包与 .bilibili 同时装着，装机列表证明不了任何事）。
  * 解析 mCurrentFocus 必须要求包名带点：真实串是
    `Window{35dd6ad u0 com.hypergryph.arknights/com.u8.sdk.U8UnityContext}` —— 包名前蹲着无点的 `u0`，
    用 `\\s+([\\w.]+)/` 会抓到 `u0`。
  * 价格表查询：`0` 与负值都**不是**「免费」，是不消耗理智/特殊（游戏表用 -1 表示，导出会塌成 0）
    ⇒ 一律降级 manual，绝不按 0 放行。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent          # D:\1-2\MAAOrch
QUEUE_ROOT_DEFAULT = ROOT / "queue"
RIG = "http://127.0.0.1:8765"

HOST_PORT = {"local-16416": 16416}                              # host → 期望 adb_port
HOST_SERIAL = {"local-16416": "127.0.0.1:16416"}                # host → adb serial（禁 emulator-*）
TASK_TYPES = {"维护/登录", "刷关", "剿灭", "肉鸽"}
REQUIRED = ("id", "ts", "host", "action", "task_type", "params", "cost",
            "anchor", "evidence", "approved_by", "sha256")
ANCHOR_REQUIRED = ("adb_port", "uid", "server", "uid_source")
FORBIDDEN_ANCHOR_KEYS = ("index", "emu_index", "name", "instance_id", "emulator_label", "device")
COST_KEYS = {"sanity", "sanity_source", "sanity_reconcile", "zone"}
UID_RE = re.compile(r"ID\s*[:：]?\s*(\d{6,12})", re.I)
PKG_RE = re.compile(r"[A-Za-z][\w]*\.[\w.]+")                    # 必须带点（避开 `u0`）
OFFICIAL_PKG = "com.hypergryph.arknights"                       # 权威：_maa_src/resource/config.json
BILIBILI_PKG = "com.hypergryph.arknights.bilibili"
TBL_V4 = ROOT / "data" / "stage_apcost_v4.json"                 # 消费面：unit_cost["code|mode"]，免判难度
TBL_V3 = ROOT / "data" / "stage_apcost_v3.json"                 # 表身：主键 stageId，code 只作索引
TBL_V2 = ROOT / "data" / "stage_apcost_v2.json"                 # 旧件（保留复算用，不参与判定）
TBL_MAA = ROOT / "data" / "stage_apcost.json"                   # MAA 源导出（523 条）
OCR_CACHE = ROOT / "queue" / "_cache" / "uid_ocr.json"


def canon(item: dict) -> str:
    core = {k: v for k, v in item.items() if k != "sha256"}
    return json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(item: dict) -> str:
    return hashlib.sha256(canon(item).encode("utf-8")).hexdigest()


def get(path: str, timeout: int = 20) -> dict:
    try:
        with urllib.request.urlopen(RIG + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:   # noqa: BLE001
        return {"ok": False, "err": f"rig 不可达/返回异常: {e}"}


_TBL: dict = {}
WARNINGS: list[str] = []                       # 非阻断观察（如 unit_cost 缺键），最后一起打印


def tables() -> dict:
    """v3 价表（主键 stageId，code 只作索引；ap ≤ 0 已剔除落 no_cost）。
    返回 {stages, code_index, no_cost, genuine, zone_resolved}；读盘一次后缓存。"""
    if _TBL:
        return _TBL
    v3 = {}
    try:
        v3 = json.loads(TBL_V3.read_text(encoding="utf-8-sig"))
    except Exception:                                             # noqa: BLE001
        pass
    v4 = {}
    try:
        v4 = json.loads(TBL_V4.read_text(encoding="utf-8-sig"))
    except Exception:                                             # noqa: BLE001
        pass
    _TBL.update({"unit_cost": v4.get("unit_cost") or {},
                 "stages": v3.get("stages") or {}, "code_index": v3.get("code_index") or {},
                 "no_cost": v3.get("no_cost") or {},
                 "genuine": v3.get("_divergences_genuine") or {},
                 "zone_resolved": v3.get("_divergences_zone_resolved") or {}})
    return _TBL


def price_candidates(code: str, mode: str) -> dict:
    """{zone: ap} —— mode=突袭 的选法有**两套约定**，都要认（这是实测踩出来的洞）：

      * 活动 EX 突袭：stageId 带 `#f#`、difficulty=FOUR_STAR（`act18side_ex03#f#`，774 条）
      * 主线突袭：stageId 前缀 `tough_`，但 difficulty 仍是 **NORMAL**（`tough_10-09`，85 条）
        ⇒ 只按 `difficulty == FOUR_STAR` 选，会把 `10-10|突袭`、`10-17|突袭` 误判成"无突袭价"。

    mode=普通 则排除 `tough_`（突袭变体）与 `easy_`（ap=0）。code 多候选（原版/复刻 zone 不同价）
    全部列出，**不替调用方挑值**。"""
    t = tables()
    out: dict = {}
    for sid in (t["code_index"].get(code) or []):
        s = t["stages"].get(sid) or {}
        ap = s.get("ap")
        if not (isinstance(ap, int) and ap > 0):
            continue
        four = s.get("difficulty") == "FOUR_STAR" or sid.endswith("#f#")
        tough = sid.startswith("tough_")
        if mode == "突袭":
            if four or tough:
                out[str(s.get("zone") or sid)] = ap
        elif not four and not tough and not sid.startswith("easy_"):
            out[str(s.get("zone") or sid)] = ap
    return out


def unit_price(code: str, mode: str) -> dict:
    """v4 `unit_cost` 直查（key = `code|mode`，消费方不必再判难度/前缀），并**用 stageId 口径独立复算当交叉校验**。

    返回 {"kind": single|zone|none|conflict, "value": int|None, "variants": {...}, "cands": {...}, "vals": [...]}
      * int > 0            ⇒ single
      * dict（含有 variants）⇒ zone：必须声明 zone，禁止静默挑值（SV-1/2/3/5/6/7|普通）
      * null / 缺键        ⇒ none：走 manual（游戏表 -1/0 的关、无突袭玩法的关）
      * 与 stageId 复算不符 ⇒ conflict：表侧回归，拒（不静默信一边）
    """
    t = tables()
    raw = (t["unit_cost"] or {}).get(f"{code}|{mode}", "__MISSING__")
    cands = price_candidates(code, mode)
    vals = sorted(set(cands.values()))
    out: dict = {"kind": "none", "value": None, "variants": {}, "cands": cands, "vals": vals}
    if isinstance(raw, dict):
        out["kind"], out["variants"] = "zone", (raw.get("variants") or {})
    elif isinstance(raw, int) and raw > 0:
        out["value"] = raw
        out["kind"] = "conflict" if (vals and raw not in vals) else "single"
    elif raw == "__MISSING__" and vals:
        WARNINGS.append(f"unit_cost 缺键 `{code}|{mode}`，但 stageId 复算有价 {vals}（不拦，只记）")
    return out


def ocr_ids(png: pathlib.Path) -> list[str]:
    """离线 OCR 一张证据图，取 `ID:<数字>` 命中（结果按文件指纹缓存在 queue/_cache/）。"""
    key = f"{png}|{png.stat().st_size if png.exists() else 0}|{int(png.stat().st_mtime) if png.exists() else 0}"
    try:
        cache = json.loads(OCR_CACHE.read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001
        cache = {}
    if key in cache:
        return cache[key]
    import cv2                                                    # 只在真要读图时才付加载成本
    from rapidocr_onnxruntime import RapidOCR
    res, _ = RapidOCR()(cv2.imread(str(png)))
    blob = " ".join(str(r[1] or "") for r in (res or []))
    ids = UID_RE.findall(blob)
    cache[key] = ids
    try:
        OCR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        OCR_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:                                             # noqa: BLE001
        pass
    return ids


def fg_read(serial: str) -> tuple[str | None, str]:
    """只读当前前台包名（区服证据）。返回 (包名, 原始串)"""
    try:
        out = subprocess.run(["adb", "-s", serial, "shell",
                              "dumpsys window | grep -i mCurrentFocus"],
                             capture_output=True, text=True, timeout=25).stdout
    except Exception as e:                                        # noqa: BLE001
        return None, f"adb 失败: {e}"
    raw = out.strip()
    m = PKG_RE.search(raw)
    return (m.group(0) if m else None), raw


def static_checks(item: dict, host: str, expect_port: "int | None",
                  no_ocr: bool = False) -> tuple[list[str], str | None]:
    """返回 (blockers, 本单 sha256 复算值)"""
    b: list[str] = []
    for k in REQUIRED:
        if k not in item or item[k] in (None, "", {}, []):
            b.append(f"C1 缺必填字段 `{k}`")
    if "prev" not in item:            # 链首单 prev 允许为空串（genesis），但键必须在
        b.append("C1 缺必填字段 `prev`（链首写 \"\"）")
    if item.get("host") != host:
        b.append(f"C2 host 不符：单={item.get('host')!r} 目录={host!r}")
    anchor = item.get("anchor") or {}
    if isinstance(anchor, dict):
        for k in ANCHOR_REQUIRED:
            if not anchor.get(k):
                b.append(f"C3 anchor 缺 `{k}`（执行前无法比对 UID）")
        if anchor.get("adb_port") not in (expect_port, str(expect_port)):
            b.append(f"C2 anchor.adb_port={anchor.get('adb_port')!r} ≠ host 端口 {expect_port}")
        if anchor.get("server") not in ("官服", "official"):
            b.append(f"C4 非官服：{anchor.get('server')!r}")
        if anchor.get("uid_source") != "lobby_ocr":
            b.append(f"C5 uid_source={anchor.get('uid_source')!r}，只认 lobby_ocr")
        hit = [k for k in FORBIDDEN_ANCHOR_KEYS if k in anchor]
        if hit:
            b.append(f"C6 anchor 里出现禁用键 {hit} —— 跨机器撞号键，名字对上也不算自证")
    else:
        b.append("C3 anchor 不是对象")
    if item.get("task_type") not in TASK_TYPES:
        b.append(f"C8 task_type={item.get('task_type')!r} 不在 {sorted(TASK_TYPES)}")
    if item.get("approved_by") in (None, "", "pending", "auto"):
        b.append("C9 没有人工放行（approved_by 为空/pending/auto）")

    # ── C7/C16/C17 代价：键白名单 + 关卡价表自洽 + 真分歧必须裁定 ────────
    cost = item.get("cost") or {}
    params = item.get("params") or {}
    ev0 = item.get("evidence") or {}
    code = str(params.get("stage") or "") if isinstance(params, dict) else ""
    mode = str(params.get("mode") or "") if isinstance(params, dict) else ""
    if not isinstance(cost, dict) or set(cost) - COST_KEYS:
        b.append(f"C7 cost 只允许 {sorted(COST_KEYS)}，实际 {cost!r}")
    elif item.get("task_type") == "刷关":
        t = tables()
        manual_ok = (cost.get("sanity_source") == "manual"
                     and cost.get("sanity_reconcile") == "required")
        gv = t["genuine"].get(code)
        up = unit_price(code, mode)
        cands, vals = up["cands"], up["vals"]
        if mode == "突袭" and up["kind"] == "none" and gv is None:
            b.append(f"C16 `{code}` 查不到 FOUR_STAR(突袭) 条目 ⇒ 该关没有突袭玩法，"
                     f"或价表缺项，必须 manual")
        elif gv is not None:
            adj = str(ev0.get("sanity_adjudicated") or "") if isinstance(ev0, dict) else ""
            if not adj:
                b.append(f"C17 `{code}` 属两源真分歧（游戏 {gv.get('game')} vs MAA {gv.get('maa')}）"
                         f"⇒ 未裁定，不许静默取一个")
            elif str(cost.get("sanity")) not in re.findall(r"\d+", adj):
                b.append(f"C17 裁定值 `{adj}` 与 cost.sanity={cost.get('sanity')!r} 不一致")
            elif cost.get("sanity_source") != "manual":
                b.append(f"C17 裁定关必须 sanity_source=\"manual\"（裁定：{adj}）")
        elif up["kind"] == "conflict":
            b.append(f"C16 表内不一致：unit_cost[{code}|{mode}]={up['value']} 与 stageId 复算 {cands} 不符 "
                     f"⇒ 价表回归/口径漂移，拒")
        elif up["kind"] == "zone":
            zone = str(cost.get("zone") or "")
            variants = up["variants"]
            if zone not in variants:
                b.append(f"C16 `{code}` 是 zone 变体 {variants} ⇒ 必须用 cost.zone 显式声明打哪个区，"
                         f"禁止静默挑值")
            elif cost.get("sanity") != variants[zone]:
                b.append(f"C16 与 zone={zone} 表价不符：单={cost.get('sanity')!r} 表={variants[zone]}")
        elif not vals:
            tail = "（需 sanity_source=\"manual\" + sanity_reconcile=\"required\"）" if not manual_ok else ""
            b.append(f"C16 `{code}` 无正值表价（no_cost/未收录；游戏表 -1 表示不消耗理智/特殊，"
                     f"导出会塌成 0，0 不是免费）⇒ 不许按 0 放行{tail}")
        elif len(vals) > 1:
            zone = str(cost.get("zone") or "")
            if zone not in cands:
                b.append(f"C16 `{code}` 是 zone 多候选 {cands} ⇒ 必须用 cost.zone 显式声明打哪个区，"
                         f"禁止静默挑值")
            elif cost.get("sanity") != cands[zone]:
                b.append(f"C16 与 zone={zone} 表价不符：单={cost.get('sanity')!r} 表={cands[zone]}")
        elif cost.get("sanity") != vals[0]:
            b.append(f"C16 代价与关卡表不符：单={cost.get('sanity')!r} 表({code})={vals[0]} "
                     f"⇒ 拼接/笔误代价，拒")
    if item.get("sanity_source") is None and isinstance(cost, dict) and "sanity_source" not in cost:
        b.append("C16 cost 缺 `sanity_source`（table|manual）—— 代价必须有出处")

    # ── C14 evidence 胸牌与 anchor 自洽（离线读图，不吃 rig）─────────────
    ev = item.get("evidence") or {}
    png_raw = str(ev.get("ocr_uid_png") or "") if isinstance(ev, dict) else ""
    want_uid = str(anchor.get("uid") or "") if isinstance(anchor, dict) else ""
    if not png_raw:
        b.append("C14 evidence 缺 `ocr_uid_png`（唯一能证「打的是这个号」的凭证）")
    else:
        png = pathlib.Path(png_raw)
        if not png.is_absolute():
            png = ROOT / png_raw
        if not png.exists():
            b.append(f"C14 证据图不存在：{png}（相对路径按仓库根 {ROOT} 解析）")
        elif no_ocr:
            b.append(f"C14 [降级] --no-ocr：只验了 {png.name} 存在，未读胸牌")
        else:
            ids = ocr_ids(png)
            if not ids:
                b.append(f"C14 证据图读不到 `ID:<数字>`：{png.name}")
            elif want_uid not in ids:
                b.append(f"C14 证据图与 anchor 不自洽：{png.name} OCR 出 {ids}，anchor.uid={want_uid}")

    # ── C15 区服证据只能来自前台包名 ────────────────────────────────────
    fgp = str(ev.get("fg_package") or "") if isinstance(ev, dict) else ""
    if not fgp:
        b.append("C15 evidence 缺 `fg_package`（大厅胸牌没有区服字段，装机列表也不算证据）")
    elif fgp == BILIBILI_PKG:
        b.append(f"C15 前台包名是 B 服：{fgp}")
    elif fgp != OFFICIAL_PKG:
        b.append(f"C15 前台包名 {fgp!r} 不是官服（应为 {OFFICIAL_PKG}）")

    mine = digest(item) if "sha256" in item else None
    if mine and item.get("sha256") != mine:
        b.append(f"C10 sha256 复算不符：单={str(item.get('sha256'))[:12]} 实={mine[:12]}")
    return b, mine


def live_checks(item: dict, host: str, expect_port: "int | None") -> list[str]:
    """清场 + 大厅 UID 比对 + 前台包名复核（契约 §4.0/§4.2/§4.3）"""
    b: list[str] = []
    # 区服：前台包名（与清场无关，战斗中也能读）
    serial = HOST_SERIAL.get(host)
    if serial:
        pkg, raw = fg_read(serial)
        if pkg is None:
            b.append(f"C15 前台包名读不出：{raw[:120]}")
        elif pkg != OFFICIAL_PKG:
            b.append(f"C15 现场前台包名={pkg} ≠ {OFFICIAL_PKG} ⇒ 拒（raw: {raw[:120]}）")

    pg = get("/page")
    if not pg.get("ok"):
        return b + [f"C11 {pg.get('err')}"]
    page, score = pg.get("page"), pg.get("page_score")
    if page != "大厅":
        return b + [f"C11 未清场：page={page!r} (score={score}) —— 设备不在大厅，UID 读不出来，"
                    f"先 quiesce（打完当前关 → 退回大厅）再放行"]
    look = get("/look")
    if not look.get("ok"):
        return b + ["C12 /look 取帧/OCR 失败"]
    blob = " ".join(look.get("texts") or [])
    got = UID_RE.findall(blob)
    want = str((item.get("anchor") or {}).get("uid") or "")
    if not got:
        b.append("C12 大厅 OCR 里没读到 `ID:<数字>`（不许猜）")
    elif want not in got:
        b.append(f"C12 锚点不符：anchor.uid={want} 现场读到 {got} ⇒ 打错账号风险，拒执行")
    return b


def load_items(root: pathlib.Path, host: str, only: str | None) -> list[pathlib.Path]:
    d = root / host / "pending"
    if not d.is_dir():
        return []
    files = sorted(p for p in d.glob("*.json") if not p.name.startswith("."))
    if only:
        files = [p for p in files if p.stem == only]
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(QUEUE_ROOT_DEFAULT))
    ap.add_argument("--host", default="local-16416")
    ap.add_argument("--id", default=None)
    ap.add_argument("--no-live", action="store_true")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--sign", default=None, help="补算并回写该文件的 sha256")
    a = ap.parse_args()

    if a.sign:
        p = pathlib.Path(a.sign)
        item = json.loads(p.read_text(encoding="utf-8"))
        item["sha256"] = digest(item)
        p.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "signed": str(p), "sha256": item["sha256"]}, ensure_ascii=False))
        return 0

    root = pathlib.Path(a.root)
    files = load_items(root, a.host, a.id)
    if not files:
        print(json.dumps({"ok": False, "host": a.host, "pending": 0,
                          "err": f"{(root / a.host / 'pending')} 下没有待检单子"}, ensure_ascii=False))
        return 1

    expect_port = HOST_PORT.get(a.host)
    WARNINGS.clear()
    verds, prev = [], ""
    for p in files:
        try:
            item = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:                                    # noqa: BLE001
            verds.append({"file": p.name, "verdict": "refuse", "blockers": [f"C0 JSON 解析失败: {e}"]})
            continue
        b, mine = static_checks(item, a.host, expect_port, no_ocr=a.no_ocr)
        if item.get("prev") != prev:
            b.append(f"C13 链断：prev={str(item.get('prev'))[:12]} 应为上一条 sha256={prev[:12] or '(genesis)'}")
        if not b and not a.no_live:
            b += live_checks(item, a.host, expect_port)
        verds.append({"file": p.name, "id": item.get("id"), "verdict": "refuse" if b else "pass",
                      "blockers": b, "sha256_recomputed": mine})
        prev = item.get("sha256") or prev

    ok = any(v["verdict"] == "pass" for v in verds)
    print(json.dumps({"ok": ok, "host": a.host, "pending": len(files),
                      "live": not a.no_live, "warnings": WARNINGS, "verdicts": verds},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
