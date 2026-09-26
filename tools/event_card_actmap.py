#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入口卡认名 → **直出 `act_map.json`**（slug → act/中文名/关卡前缀）。

> 与 `tools/name_event_cards.py` 的分工：那个工具负责**问视觉模型**（产出 `labels_vision.json`），
> 本工具负责**把名字对到 act**：读 `index.json` + `labels_vision.json`（+ 可选直接问视觉），
> 与**官方真值表**比对，产出 `data/templates/events/act_map.json`（Lead 指定路径）＋副本。
> 为什么拆开：`name_event_cards.py` 在我准备改它时**正在被别人编辑**（写完报"file changed"）
> ⇒ 不去覆盖，改成独立文件；两边组合即闭环。

真值表来源（离线、可复现）：`runtime/plans/_event_truth.json`
  = 公开 `activity_table.json` 的**官方活动名** ×（本地关卡表推出的）**关卡代号前缀**
  例：act18side 尘影余音→LE、act38side 揭幕者们→PV、act42side 众生行记→MT、act39side 出苍白海→EP、
      act28side 不义之财→CV、act43side 红丝绒→AD、act41side 挽歌燃烧殆尽→EA

**认不出就 unknown，不许猜**（宁可空着，等人给完成进度页截图人工确认）。

用法:
    python tools/event_card_actmap.py --dry                  # 离线：index.json + labels_vision + 真值表
    python tools/event_card_actmap.py --vision               # 对未认出的卡直接问视觉模型
    python tools/event_card_actmap.py --shot runtime/_grid_now.png --check   # 读完成进度页 + 自洽检查
    python tools/event_card_actmap.py --apply-entry-slug     # 输出可粘进 _prime_queue3.ENTRY_SLUG 的行
"""
from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
TPL = ROOT / "data" / "templates" / "events"
PLANS = ROOT / "runtime" / "plans"
TRUTH = PLANS / "_event_truth.json"
OUT_MAP = TPL / "act_map.json"
OUT_COPY = PLANS / "act_map.json"
LABELS = TPL / "labels_vision.json"


def clean_name(s: str) -> str:
    """把 index.json 里的名字洗成主名：去括号注释、去尾部进度（`红丝绒 28/56`、`灯火序曲 6/6`）。"""
    s = re.split(r"[（(]", str(s or "").strip())[0]
    s = re.sub(r"\s*\d+\s*/\s*\d+\s*\??\s*$", "", s)          # 52/56 / 6/6?
    s = re.sub(r"\s*(未动过|已清|打完|已领)\s*$", "", s)
    return s.strip()


def match_truth(name: str, truth: dict) -> tuple[dict | None, float, str]:
    name = clean_name(name)
    if not name:
        return None, 0.0, ""
    by_name = truth.get("by_name") or {}
    if name in by_name:
        return truth["by_act"][by_name[name][0]], 1.0, "exact"
    best, bs = None, 0.0
    for nm, acts in by_name.items():
        r = difflib.SequenceMatcher(None, name, nm).ratio()
        if r > bs:
            best, bs = truth["by_act"][acts[0]], r
    if best and bs >= 0.75:
        return best, round(bs, 2), "fuzzy"
    return None, round(bs, 2), ""


def ask_card(png: pathlib.Path) -> dict:
    """问视觉模型这张卡是哪个活动（用 `_modlens_json` 拿结构化结果；prompt 里不放双引号）。"""
    txt = ""
    try:
        from discover_stages import _modlens_json                        # noqa: PLC0415
        q = ("这是一张《明日方舟》活动卡片的截图。请回答：①活动中文名 ②英文名（若有）"
             "③这个活动的关卡代号前缀（形如 EA / EP / PV / MT / LE / CV / AD）。"
             "只输出 JSON：{'name':'…','en':'…','prefix':'…'}；不确定就写 ?，不要猜。")
        res = (_modlens_json(png, q).get("result") or {})
        txt = "\n".join(str(t) for t in ((res.get("ocr") or {}).get("full_text"), res.get("summary"),
                                         json.dumps(res.get("semantics") or {}, ensure_ascii=False)) if t)
    except Exception as e:                                               # noqa: BLE001
        txt = f"(modlens 失败: {str(e)[:80]})"
    rec = {"raw": txt[:400], "name": "", "prefix": "?"}
    m = re.search(r"\{[^{}]*name[^{}]*\}", txt)
    if m:
        try:
            j = json.loads(m.group(0).replace("'", '"'))
            rec["name"] = str(j.get("name") or "").strip()
            rec["prefix"] = (str(j.get("prefix") or "?").strip().upper() or "?")
        except Exception:                                                # noqa: BLE001
            pass
    if not rec["name"] and TRUTH.exists():
        for nm in (json.loads(TRUTH.read_text(encoding="utf-8")).get("by_name") or {}):
            if nm and nm in txt:
                rec["name"] = nm
                break
    if rec["prefix"] in ("", "?"):
        m2 = re.search(r"\b(EA|EP|PV|MT|LE|CV|AD)\b", txt.upper())
        rec["prefix"] = m2.group(1) if m2 else "?"
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只用 index.json + labels_vision（不调视觉）")
    ap.add_argument("--vision", action="store_true", help="对未认出的卡直接问视觉模型")
    ap.add_argument("--vision-all", action="store_true",
                    help="**对全部 14 张卡**都问一遍（用来查'模板库到底有没有 LE/PV/MT/EP 那四张'）")
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--shot", default="", help="读一张『完成进度』页截图，列出与真值表比中的活动")
    ap.add_argument("--check", action="store_true", help="顺带跑 tools/event_templates.py --match")
    ap.add_argument("--apply-entry-slug", action="store_true",
                    help="打印可直接粘进 runtime/_prime_queue3.py 的 ENTRY_SLUG 行")
    ap.add_argument("--check-drift", action="store_true",
                    help="只检查两份 act_map.json 是否漂移（不一致 ⇒ exit 2），不重生成")
    ap.add_argument("--force-drift", action="store_true",
                    help="两份不一致时仍然重生成（**会静默丢掉副本上的改动**，先想清楚）")
    a = ap.parse_args()

    # 两份 act_map.json：**权威 = OUT_MAP（data/templates/events/，消费者读这份）**，OUT_COPY 是生成的副本
    #（2026-09-24 达芬奇审计：手改落在副本上时，读取方看不到、而重生成会把它抹掉 ⇒ 第三个"会漂移的副本"）。
    def _drift() -> list[str]:
        if not (OUT_MAP.exists() and OUT_COPY.exists()):
            return []
        _a = json.loads(OUT_MAP.read_text(encoding="utf-8"))
        _c = json.loads(OUT_COPY.read_text(encoding="utf-8"))
        return sorted(k for k in set(_a) | set(_c) if _a.get(k) != _c.get(k))

    dkeys = _drift()
    if a.check_drift:
        if dkeys:
            print(f"✗ 两份 act_map.json 不一致（顶层差异键：{dkeys}）")
            print(f"   权威 = {OUT_MAP}｜副本 = {OUT_COPY}")
            return 2
        print("✓ 两份 act_map.json 字节相同（无漂移）")
        return 0
    if dkeys and not a.force_drift:
        print(f"✗ 两份 act_map.json 不一致（顶层差异键：{dkeys}）⇒ 拒绝重生成")
        print(f"   权威 = {OUT_MAP}（消费者读这份）｜副本 = {OUT_COPY}")
        print("   重写会把**副本上的改动静默丢掉**：请把改动并进权威份，或加 --force-drift 明确丢弃。")
        return 2
    if dkeys:
        print(f"⚠ --force-drift：副本上的差异键 {dkeys} 将被覆盖")

    truth = json.loads(TRUTH.read_text(encoding="utf-8")) if TRUTH.exists() else {"by_act": {}, "by_name": {}}
    if not truth.get("by_act"):
        print("⚠ 缺真值表：先跑 python runtime/plans/_event_truth_build.py")
    index = json.loads((TPL / "index.json").read_text(encoding="utf-8")) if (TPL / "index.json").exists() else {}
    labels = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.exists() else {}
    old = json.loads(OUT_MAP.read_text(encoding="utf-8")).get("cards", {}) if OUT_MAP.exists() else {}
    slugs = sorted(p.stem for p in TPL.glob("*.png"))
    if a.only:
        slugs = [s for s in slugs if s in set(a.only)]

    cards: dict = {}
    for slug in slugs:
        e = {"act": "", "name": "", "prefix": "", "confidence": "unknown", "source": "", "evidence": ""}
        # ① index.json
        idx = index.get(slug, "")
        hit, score, how = match_truth(idx, truth)
        if hit:
            e.update(act=hit["act"], name=hit["name"] or clean_name(idx), prefix=hit["prefix"],
                     confidence="high" if how == "exact" else "medium",
                     source=f"index.json({how} {score})", evidence=clean_name(idx))
        # ② labels_vision.json（另一个工具问出来的）
        if not e["act"]:
            lab = labels.get(slug) or {}
            if lab.get("name") or (lab.get("prefix") and lab["prefix"] != "?"):
                hit2, score2, how2 = match_truth(lab.get("name", ""), truth)
                if hit2:
                    e.update(act=hit2["act"], name=hit2["name"] or lab.get("name"), prefix=hit2["prefix"],
                             confidence="high" if how2 == "exact" else "medium",
                             source=f"labels_vision({how2} {score2})", evidence=str(lab.get("raw"))[:160])
                elif lab.get("prefix") and lab["prefix"] != "?":
                    cands = [t for t in truth["by_act"].values() if t.get("prefix") == lab["prefix"]]
                    if len(cands) == 1:
                        e.update(act=cands[0]["act"], name=cands[0]["name"], prefix=lab["prefix"],
                                 confidence="medium", source="labels_vision(prefix)",
                                 evidence=str(lab.get("raw"))[:160])
        # ③ 直接问视觉
        if (a.vision or a.vision_all) and (a.vision_all or not e["act"]):
            v = ask_card(TPL / f"{slug}.png")
            labels[slug] = v
            print(f"   [vision] {slug}: name={v.get('name')!r} prefix={v.get('prefix')!r}", flush=True)
            hit3, score3, how3 = match_truth(v.get("name", ""), truth)
            if hit3:
                e.update(act=hit3["act"], name=hit3["name"] or v["name"], prefix=hit3["prefix"],
                         confidence="high" if how3 == "exact" else "medium",
                         source=f"vision({how3} {score3})", evidence=v["raw"][:180])
            elif v.get("prefix") and v["prefix"] != "?":
                cands = [t for t in truth["by_act"].values() if t.get("prefix") == v["prefix"]]
                if len(cands) == 1:
                    e.update(act=cands[0]["act"], name=cands[0]["name"], prefix=v["prefix"],
                             confidence="medium", source="vision(prefix)", evidence=v["raw"][:180])
                else:
                    e["evidence"] = f"视觉前缀 {v['prefix']} 对应多个 act ⇒ 不猜"
            else:
                e["evidence"] = f"视觉没给出可用名字；raw={v['raw'][:120]}"
        # ④ 保留上一轮已认出的
        if not e["act"] and old.get(slug, {}).get("act"):
            e = old[slug]
        cards[slug] = e

    if a.vision and labels:
        LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 一致性过滤（**宁可 unknown，不许猜**）────────────────────────────────────
    #   2026-09-24 实测：vision 会把两张卡认成同一个活动（conglin/heroes 都报"丛林症结"）、
    #   也会和 index.json 打架（qijiu：index 说"七丘的新芽"、vision 说"揭幕者们"）。
    #   这类冲突**绝不能直接进生产**（认错活动 ⇒ 扫错图/把矿记到别的活动头上）。
    needs_human: list[dict] = []
    owner: dict[str, list[str]] = {}
    for slug, v in cards.items():
        if v.get("act"):
            owner.setdefault(v["name"] or v["act"], []).append(slug)
    for name, slugs in owner.items():
        if len(slugs) > 1:
            corroborated = [s for s in slugs if clean_name(index.get(s, "")) == name]
            for s in slugs:
                if s not in corroborated:
                    needs_human.append({"slug": s, "reason": f"与 {'、'.join(x for x in slugs if x != s)} "
                                                             f"被认成同一个活动（{name}）⇒ 必有一个错"})
                    cards[s].update(act="", name=cards[s].get("name") or "", prefix="",
                                    confidence="conflict", source=cards[s].get("source", ""))
    for slug, v in cards.items():
        if not v.get("act"):
            continue
        idx_clean = clean_name(index.get(slug, ""))
        if idx_clean and clean_name(v.get("name", "")) and idx_clean != clean_name(v["name"]):
            ratio = difflib.SequenceMatcher(None, idx_clean, clean_name(v["name"])).ratio()
            if ratio < 0.5:
                _ov = old.get(slug) or {}
                if _ov.get("_human_resolved") or _ov.get("key_kind") or _ov.get("verified_by"):
                    print(f"  [保留人工裁决] {slug}：旧条目是人工裁决（key_kind="
                          f"{_ov.get('key_kind')!r}）⇒ **跳过** index.json 冲突降级"
                          f"（『{idx_clean}』vs『{v['name']}』是层级/别名差异，不是冲突）")
                    continue
                needs_human.append({"slug": slug, "reason": f"index.json 说『{idx_clean}』，"
                                                            f"{v.get('source')} 说『{v['name']}』⇒ 冲突",
                                    "kept_act": v["act"], "kept_name": v["name"], "confidence": "low"})
                v["confidence"] = "low（与 index.json 冲突，**用前必须人工确认**）"

    # ①c **人工裁决必须比自动比对更硬**（2026-09-24 达芬奇审计）：本脚本每次都**重建** cards，
    #     会把人工写进条目的 `key_kind/section/siblings/verified_by/confidence` 连同顶层 `_resolved`
    #     一起丢掉（哪怕两份副本已经对齐）。⇒ 带 `_human_resolved`（或 key_kind/verified_by）的条目
    #     原样保留，且不再进 needs_human。
    _KEEP = ("_human_resolved", "key_kind", "section", "section_code", "card", "siblings",
             "frame_format", "verified_by", "confidence")
    _kept = []
    for _s in list(cards):
        _ov = old.get(_s) or {}
        if not (_ov.get("_human_resolved") or _ov.get("key_kind") or _ov.get("verified_by")):
            continue
        cards[_s] = {**cards[_s], **{k: _ov[k] for k in _KEEP if k in _ov}}
        needs_human = [x for x in needs_human if x.get("slug") != _s]
        _kept.append(_s)
    if _kept:
        print(f"  [保留人工裁决] {len(_kept)} 条：{_kept}（重生成不覆盖 key_kind/confidence）")

    _old_top = json.loads(OUT_MAP.read_text(encoding="utf-8")) if OUT_MAP.exists() else {}
    _resolved = {k: v for k, v in (_old_top.get("_resolved") or {}).items() if k in cards}

    doc = {"_note": "入口卡认名：slug → act/中文名/关卡前缀。**unknown = 没认出来（宁可空着不许猜）**；"
                    "confidence=low/conflict 的**用前必须人工确认**。"
                    "`_human_resolved: true` 的条目是**人工裁决**，重生成时原样保留（见 `_resolved`）。",
           "_truth": "runtime/plans/_event_truth.json（公开 activity_table 官方名 × 本地关卡表前缀）",
           "needs_human": needs_human, "cards": cards}
    if _resolved:
        doc["_resolved"] = _resolved
    for p in (OUT_MAP, OUT_COPY):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = [s for s, v in cards.items() if v["act"]]
    unk = [s for s, v in cards.items() if not v["act"]]
    print(f"认出 {len(ok)}/{len(cards)} 张卡 → {OUT_MAP.relative_to(ROOT)}")
    for s in sorted(cards):
        v = cards[s]
        print(f"  {'✅' if v['act'] else '❔'} {s:12s} {v['act'] or 'unknown':12s} "
              f"{(v['name'] or ''):12s} {(v['prefix'] or ''):4s} [{v['confidence']}] {v['source']}")
    if unk:
        print(f"未认出：{unk} ⇒ 加 --vision，或在窗口里给我『完成进度』页截图")
    if a.apply_entry_slug:
        print("\n--- 可粘进 runtime/_prime_queue3.py 的 ENTRY_SLUG（只列已认出的）---")
        for s in sorted(cards):
            v = cards[s]
            if v["act"]:
                print(f'    "{v["act"]}": "{s}",   # {v["name"]}（{v["prefix"]}）')
    if a.shot:
        shot = pathlib.Path(a.shot)
        if not shot.is_absolute():
            shot = ROOT / shot
        try:
            from discover_stages import _modlens_json                    # noqa: PLC0415
            q = ("这是明日方舟『完成进度』页截图。请列出页面上每张活动卡片上的：活动中文名 + 进度（形如 52/56）。"
                 "只输出 JSON：{'cards':[{'name':'…','progress':'52/56'}]}")
            res = (_modlens_json(shot, q).get("result") or {})
            txt = "\n".join(str(t) for t in ((res.get("ocr") or {}).get("full_text"), res.get("summary"),
                                             json.dumps(res.get("semantics") or {}, ensure_ascii=False)) if t)
            print(f"\n--- 完成进度页（{shot.name}）与真值表比中的活动 ---")
            for nm in (truth.get("by_name") or {}):
                if nm and nm in txt:
                    t = truth["by_name"][nm][0]
                    print(f"  {nm} → {t} 前缀 {truth['by_act'][t]['prefix']}")
            print("（摘要）" + txt[:240].replace("\n", " | "))
        except Exception as e:                                           # noqa: BLE001
            print("读完成进度页失败：", str(e)[:120])
    if a.check:
        cmd = ["python", "tools/event_templates.py", "--match", a.shot] if a.shot else \
              ["python", "tools/event_templates.py"]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
        print("\n--- event_templates 自洽检查 ---")
        print(((r.stdout or "") + (r.stderr or ""))[-700:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
