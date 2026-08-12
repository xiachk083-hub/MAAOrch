#!/usr/bin/env python3
"""MAA 任务转移图工具 — 解析 tasks.json，展开继承，输出链路图/查询。

用法:
    python tools/task_graph.py                     # 统计摘要
    python tools/task_graph.py --task Fight        # 单任务详情（含继承展开）
    python tools/task_graph.py --chain Fight       # 文本链路（识别→动作→下一候选）
    python tools/task_graph.py --mermaid Fight     # mermaid 流程图（可粘到 docs）
    python tools/task_graph.py --json Fight        # 展开后 JSON
    python tools/task_graph.py --search 全权委托    # 按 Doc/text 关键词搜任务

说明:
    - 继承展开为简化模型：`B@A` 未显式定义时取基任务 A 的定义（baseTask 同样处理）；
      MAA 实际的列表字段 @ 前缀传播不模拟（影响链路起点，不影响理解逻辑）。
    - 解析失败会打印具体任务名与错误，不静默。
"""
from __future__ import annotations
import argparse
import copy
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent

_DEFAULT_SOURCES = [
    PROJ / "services" / "maa" / "source" / "resource" / "tasks" / "tasks.json",
    PROJ / "services" / "maa" / "instances" / "1" / "resource" / "tasks" / "tasks.json",
]

# 需要展开的引用字段
_REF_FIELDS = ("next", "exceededNext", "onErrorNext", "sub")

# 摘要只展示的关键字段
_ALGO_FIELD = "algorithm"
_ACTION_FIELD = "action"


def load_tasks(source: str | None = None) -> dict:
    """加载 tasks.json（自动合并同目录子文件夹的 json：Stages/UiTheme/RA 等，后加载覆盖同名）。"""
    if source:
        paths = [Path(source)]
    else:
        paths = [p for p in _DEFAULT_SOURCES if p.exists()]
    if not paths:
        sys.exit("找不到 tasks.json，请用 --source 指定路径")
    merged: dict = {}
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            sys.exit(f"解析 {p} 失败: {e}")
        merged.update(data)
        print(f"加载 {p} ({len(data)} 任务)", file=sys.stderr)
        # 递归合并同目录树下所有子 json（MAA 多文件扩展模式，含二级如 RA/plugin/）
        for jf in sorted(p.parent.rglob("*.json")):
            if jf == p:
                continue
            try:
                sub_data = json.loads(jf.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"跳过 {jf}: {e}", file=sys.stderr)
                continue
            merged.update(sub_data)
            print(f"合并 {jf} (+{len(sub_data)})", file=sys.stderr)
    return merged


def _merge(base: dict, override: dict) -> dict:
    """合并任务定义：override 覆盖 base（浅层，列表字段整体替换）。"""
    out = copy.deepcopy(base)
    out.update(copy.deepcopy(override))
    return out


def resolve_task(tasks: dict, name: str) -> tuple[dict, str] | None:
    """按名解析任务，展开 baseTask、隐式 @ 继承、`X#next`/`X#back` 双目引用。

    返回 (定义, 实际基任务名)；name 不存在返回 None。
    """
    # 双目引用 X#next / X#back / X#on_error：取 X 的定义
    if "#" in name and not name.startswith("#"):
        name = name.split("#", 1)[0]
    if name in tasks:
        base = name
    elif "@" in name and name.split("@", 1)[1] in tasks:
        base = name.split("@", 1)[1]
    else:
        return None
    # 沿 baseTask 链展开（防环）
    seen: set[str] = set()
    cur = tasks[base]
    while cur.get("baseTask") and cur["baseTask"] in tasks:
        bname = cur["baseTask"]
        if bname in seen:
            break
        seen.add(bname)
        cur = _merge(tasks[bname], cur)
    return cur, base


def _describe(definition: dict) -> str:
    """一行描述：算法 + 识别条件 + 动作。"""
    algo = definition.get("algorithm", "MatchTemplate")
    parts = []
    if algo == "OcrDetect":
        text = definition.get("text")
        if text is None:
            parts.append("OCR: 任意文字")
        elif isinstance(text, list):
            parts.append("OCR: " + "/".join(str(t) for t in text[:3]))
        else:
            parts.append(f"OCR: {text}")
    elif algo == "MatchTemplate":
        tpl = definition.get("template")
        if isinstance(tpl, list):
            parts.append("模板: " + ", ".join(str(t) for t in tpl[:3]))
        elif tpl:
            parts.append(f"模板: {tpl}")
        else:
            parts.append("模板匹配")
    elif algo == "FeatureMatch":
        parts.append(f"特征点: {definition.get('template', '?')}")
    elif algo == "JustReturn":
        parts.append("直接动作")
    action = definition.get("action", "DoNothing")
    if action != "DoNothing":
        parts.append(action)
    return " | ".join(parts)


def fmt_task_line(name: str, definition: dict) -> str:
    """链路里的一行：任务名 [描述]。"""
    return f"{name} [{_describe(definition)}]"


def render_chain(tasks: dict, root: str, depth: int = 30, max_nodes: int = 150) -> list[str]:
    """BFS 展开链路，返回文本行。"""
    start = resolve_task(tasks, root)
    if start is None:
        sys.exit(f"任务 {root} 不存在")
    lines: list[str] = []
    lines.append(fmt_task_line(root, start[0]))
    queue = [(root, 0)]
    visited: set[str] = set()
    expanded = 0
    while queue and expanded < max_nodes:
        name, level = queue.pop(0)
        if name in visited or level >= depth:
            continue
        visited.add(name)
        resolved = resolve_task(tasks, name)
        if resolved is None:
            continue
        definition, _ = resolved
        expanded += 1
        for field in _REF_FIELDS:
            refs = definition.get(field) or []
            if not isinstance(refs, list):
                continue
            for ref in refs[:4]:
                if not isinstance(ref, str) or ref.startswith("#"):
                    continue
                tag = field.replace("Next", "").replace("next", "next")
                rdef = resolve_task(tasks, ref)
                desc = fmt_task_line(ref, rdef[0]) if rdef else f"{ref} [?]"
                lines.append(f"{'  ' * (level + 1)}└─({tag}) {desc}")
                queue.append((ref, level + 1))
    return lines


def render_mermaid(tasks: dict, root: str, depth: int = 15, max_nodes: int = 120) -> str:
    """BFS 展开生成 mermaid flowchart（节点编号，label 原名+描述）。"""
    start = resolve_task(tasks, root)
    if start is None:
        sys.exit(f"任务 {root} 不存在")
    lines = ["flowchart TD"]
    node_id: dict[str, str] = {}
    id_counter = [0]

    def nid(name: str) -> str:
        if name not in node_id:
            node_id[name] = f"n{id_counter[0]}"
            id_counter[0] += 1
        return node_id[name]

    queue = [(root, 0)]
    visited: set[str] = set()
    edges: set[str] = set()
    while queue:
        name, level = queue.pop(0)
        if name in visited or level >= depth or len(node_id) >= max_nodes:
            continue
        visited.add(name)
        resolved = resolve_task(tasks, name)
        if resolved is None:
            continue
        definition, _ = resolved
        label = name.replace('"', "'")
        lines.append(f'    {nid(name)}["{label}<br/>{_describe(definition)}"]')
        for field in _REF_FIELDS:
            refs = definition.get(field) or []
            if not isinstance(refs, list):
                continue
            for ref in refs[:4]:
                if not isinstance(ref, str) or ref.startswith("#"):
                    continue
                arrow = {"sub": "==>", "exceededNext": "-.->", "onErrorNext": "-.->"}.get(field, "-->")
                tag = {"sub": "sub", "exceededNext": "exceed", "onErrorNext": "err"}.get(field, "next")
                e = f"{nid(name)}{arrow}{nid(ref)}"
                if e not in edges:
                    edges.add(e)
                    lines.append(f'    {nid(name)}{arrow}|{tag}|{nid(ref)}')
                queue.append((ref, level + 1))
    return "\n".join(lines)


def stats(tasks: dict) -> str:
    """统计摘要。"""
    algos: Counter = Counter()
    actions: Counter = Counter()
    refs: Counter = Counter()
    at_tasks = 0
    roi = 0
    for name, t in tasks.items():
        if "@" in name:
            at_tasks += 1
        algos[t.get("algorithm", "MatchTemplate(缺省)")] += 1
        actions[t.get("action", "DoNothing(缺省)")] += 1
        if t.get("roi"):
            roi += 1
        for f in _REF_FIELDS:
            if t.get(f):
                refs[f] += 1
    out = [
        f"任务总数: {len(tasks)}",
        "",
        "算法分布:",
        *[f"  {k}: {v}" for k, v in algos.most_common()],
        "",
        "动作分布:",
        *[f"  {k}: {v}" for k, v in actions.most_common()],
        "",
        f"@ 型任务: {at_tasks} | 带 roi: {roi} | 引用字段: "
        + ", ".join(f"{k}={v}" for k, v in refs.items()),
    ]
    return "\n".join(out)


def search_tasks(tasks: dict, keyword: str, limit: int = 20) -> list[str]:
    """按 Doc/text/template 关键词搜任务。"""
    out = []
    kw = keyword.lower()
    for name, t in tasks.items():
        hay = [name, t.get("Doc", ""), t.get("doc", ""), t.get("Docs", "")]
        for f in ("text", "template"):
            v = t.get(f)
            if isinstance(v, list):
                hay.extend(str(x) for x in v)
            elif v:
                hay.append(str(v))
        if any(kw in str(h).lower() for h in hay):
            out.append(f"{name}: {_describe(t)}")
        if len(out) >= limit:
            break
    return out or ["（无匹配）"]


def main() -> None:
    ap = argparse.ArgumentParser(description="MAA 任务转移图工具")
    ap.add_argument("--source", help="tasks.json 路径（默认自动找）")
    ap.add_argument("--task", help="单任务详情（含继承展开）")
    ap.add_argument("--chain", help="文本链路展开（从该任务开始）")
    ap.add_argument("--mermaid", help="mermaid 流程图（从该任务开始）")
    ap.add_argument("--json", help="输出任务定义 JSON（含继承展开）")
    ap.add_argument("--search", help="按 Doc/text/template 关键词搜任务")
    ap.add_argument("--depth", type=int, default=15, help="链路展开深度（默认 15）")
    ap.add_argument("--max-nodes", type=int, default=150, help="节点上限（默认 150）")
    args = ap.parse_args()

    tasks = load_tasks(args.source)

    if args.search:
        print("\n".join(search_tasks(tasks, args.search)))
        return
    if args.task:
        resolved = resolve_task(tasks, args.task)
        if resolved is None:
            sys.exit(f"任务 {args.task} 不存在")
        definition, base = resolved
        print(f"# {args.task} (基任务: {base})")
        print(json.dumps(definition, ensure_ascii=False, indent=2))
        return
    if args.chain:
        print("\n".join(render_chain(tasks, args.chain, args.depth, args.max_nodes)))
        return
    if args.mermaid:
        print(render_mermaid(tasks, args.mermaid, args.depth, args.max_nodes))
        return
    if args.json:
        resolved = resolve_task(tasks, args.json)
        if resolved is None:
            sys.exit(f"任务 {args.json} 不存在")
        print(json.dumps(resolved[0], ensure_ascii=False, indent=2))
        return
    print(stats(tasks))


if __name__ == "__main__":
    main()
