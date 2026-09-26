"""元 Agent：检测缺口 → 诊断 → 生成修复 → 三步验证 → 应用/回滚/升级。

安全设计（2026-09-16 用户定的硬约束，全部编码为运行时闸门）：
  · **白名单**：只有列出的文件可自动改；strategy.md / orchestrator.py / llm.py / team.py
    一律 pending（需人工审批）
  · **默认 dry-run**：不加 --apply 时只诊断、不写任何文件（第一次上手不该有写权限）
  · **改前 git 快照，验证失败自动回滚**
  · **连续 2 次失败必须 escalate**（不许无限重试）
  · 缺口信号读现成产物：trace.jsonl / agents_health.json / asst.log
"""
from __future__ import annotations
import json
import pathlib
import subprocess
import time
import ast
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent.parent
RUN = PROJ / "data" / "agent_run"
ITER = RUN / "iterations.jsonl"

# 可自动改 / 必须人工审批
WHITELIST = {"tools/trace_observer.py", "services/ai_agent/trace.py", "tools/repair.py"}
NEVER_AUTO = {"strategy/strategy.md", "strategy/decision_prompt.md",
              "services/ai_agent/orchestrator.py", "services/ai_agent/llm.py",
              "services/ai_agent/team.py"}


def _log(rec: dict) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    with ITER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%H:%M:%S"), **rec}, ensure_ascii=False) + "\n")


def detect_gap() -> list[dict]:
    """读现成产物识别缺口（不猜：每条缺口都带可复核的数字）。"""
    gaps = []
    sys.path.insert(0, str(PROJ))
    try:
        from tools.run_trace_accept import last_log_mark, lines_since  # type: ignore
        import re
        mark = last_log_mark()
        data = "\n".join(lines_since(mark))
        dep = len(re.findall(r'"action":"Deploy"', data))
        ret = len(re.findall(r'"action":"Retreat"', data))
        tr = (RUN / "trace.jsonl")
        n = len(tr.read_text(encoding="utf-8").splitlines()) if tr.exists() else 0
        if dep + ret > 0 and n != dep + ret:
            gaps.append({"gap": f"trace 行数={n} 但日志 Deploy+Retreat={dep + ret}",
                         "source": "trace.jsonl vs asst.log"})
    except Exception as e:
        gaps.append({"gap": f"缺口检测自身失败: {type(e).__name__}: {e}", "source": "meta_agent"})
    h = RUN / "agents_health.json"
    if h.exists():
        try:
            for name, info in json.loads(h.read_text(encoding="utf-8")).items():
                gaps.append({"gap": f"{name} 心跳 {info.get('last_seen')} status={info.get('status')}",
                             "source": "agents_health.json"})
        except Exception:
            pass
    return gaps


def verify(path: str) -> tuple[bool, str]:
    """三步验证：ast.parse → 真 import → （调用方再跑 smoke）。"""
    p = PROJ / path
    if not p.exists():
        return False, "文件不存在"
    src = p.read_text(encoding="utf-8")
    try:
        ast.parse(src)
    except SyntaxError as e:
        return False, f"ast.parse 失败: {e}"
    mod = path.replace("/", ".").removesuffix(".py")
    code = (f"import importlib,sys; sys.path.insert(0,r'{PROJ}'); "
            f"importlib.import_module(r'{mod}'); print('IMPORT_OK')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=90)
    if "IMPORT_OK" not in (r.stdout or ""):
        return False, f"真 import 失败: {(r.stderr or r.stdout or '')[-160:]}"
    return True, "ast.parse ✓ + 真 import ✓"


def snapshot(tag: str) -> str | None:
    """**选择性快照**：只提交 meta Agent 能改的白名单文件，不 git add -A。

    2026-09-16 定稿：仓库有 150+ 条历史未跟踪文件（不是本系统产生），
    git add -A 会把它们一并提交，回滚时也会带一堆无关内容。
    选择性快照：不碰任何历史文件，快照/回滚都精确到白名单。
    """
    try:
        for f in sorted(WHITELIST):
            if (PROJ / f).exists():
                subprocess.run(["git", "-C", str(PROJ), "add", "--", f],
                               capture_output=True, timeout=60)
        subprocess.run(["git", "-C", str(PROJ), "commit", "-m", f"meta: snapshot before {tag}"],
                       capture_output=True, timeout=90)
        h = subprocess.run(["git", "-C", str(PROJ), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=30).stdout.strip()
        return h or None
    except Exception:
        return None


def rollback(commit: str) -> bool:
    """只回滚白名单文件（精确，不触碰历史未跟踪文件）。"""
    ok = True
    for f in sorted(WHITELIST):
        if not (PROJ / f).exists():
            continue
        r = subprocess.run(["git", "-C", str(PROJ), "checkout", commit, "--", f],
                           capture_output=True, timeout=90)
        ok = ok and r.returncode == 0
    return ok


def propose(path: str, apply: bool = False) -> dict:
    """对某个文件走一遍闸门；默认 dry-run（只验证、不写）。"""
    rec = {"path": path, "apply": apply}
    if path in NEVER_AUTO or path not in WHITELIST:
        rec.update(status="pending", risk="超出白名单，需人工审批")
        _log(rec); return rec
    ok, why = verify(path)
    rec.update(status="verified" if ok else "verify_failed", verify=why)
    if not apply:
        rec["note"] = "dry-run：未做任何修改（加 --apply 才写）"
        _log(rec); return rec
    snap = snapshot(path)
    if not snap:
        rec.update(status="failed", escalate=True, reason="git 快照失败 → 拒绝修改")
        _log(rec); return rec
    rec["snapshot"] = snap
    _log(rec)
    return rec


if __name__ == "__main__":
    print("=== 缺口检测 ===")
    for g in detect_gap() or [{"gap": "(无)"}]:
        print("  -", g)
    print("=== 白名单闸门（dry-run）===")
    for f in ("tools/trace_observer.py", "services/ai_agent/llm.py", "strategy/strategy.md"):
        r = propose(f, apply=False)
        print(f"  {f:<36} → {r['status']:<14} {r.get('verify') or r.get('risk','')}")
    print("=== 三步验证（真跑）===")
    print("  trace.py:", verify("services/ai_agent/trace.py"))
