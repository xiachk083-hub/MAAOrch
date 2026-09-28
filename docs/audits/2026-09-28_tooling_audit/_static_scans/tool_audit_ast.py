"""Static audit of MAAOrch tool scripts (read-only analysis, no imports of targets)."""
import ast, io, json, os, sys, collections

FILES = [
    "tools/mas_run_profile.py",
    "tools/mas_ondemand.py",
    "tools/mas_plan_builder.py",
    "tools/mas_ondemand_mcp.py",
    "tools/mas_probe_farm.py",
    "tools/stage_capability.py",
]
ROOT = r"D:\1-2\MAAOrch"

def func_len(node):
    return (node.end_lineno or node.lineno) - node.lineno + 1

report = {}
for rel in FILES:
    path = os.path.join(ROOT, rel.replace("/", os.sep))
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    lines = src.splitlines()
    info = {
        "path": rel,
        "bytes": os.path.getsize(path),
        "lines": len(lines),
        "imports": [],
        "bare_except": [],
        "broad_except": [],
        "except_pass": [],
        "longest_funcs": [],
        "cli_args": [],
        "keywords": collections.Counter(),
        "sys_exit": [],
    }
    # imports
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                info["imports"].append(a.name)
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            for a in n.names:
                info["imports"].append(f"{mod}.{a.name}" if mod else a.name)
    info["imports"] = sorted(set(info["imports"]))
    # except handlers
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler):
            seg = ast.get_source_segment(src, n) or ""
            first = seg.splitlines()[0] if seg else ""
            if n.type is None:
                info["bare_except"].append((n.lineno, first.strip()[:90]))
            else:
                type_src = ast.get_source_segment(src, n.type) or ""
                if type_src in ("Exception", "BaseException"):
                    info["broad_except"].append((n.lineno, first.strip()[:90]))
            body = n.body
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                info["except_pass"].append((n.lineno, first.strip()[:90]))
    # functions
    funcs = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append((func_len(n), n.lineno, n.name))
    funcs.sort(reverse=True)
    info["longest_funcs"] = funcs[:12]
    # CLI args: add_argument calls
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument":
            flags = [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            kwargs = {}
            for kw in n.keywords:
                try:
                    kwargs[kw.arg] = ast.literal_eval(kw.value)
                except Exception:
                    kwargs[kw.arg] = "<expr>"
            info["cli_args"].append({"line": n.lineno, "flags": flags, "default": kwargs.get("default", "<required>" if kwargs.get("required") else "None"), "action": kwargs.get("action"), "nargs": kwargs.get("nargs"), "choices": kwargs.get("choices"), "dest": kwargs.get("dest")})
    # sys.exit codes
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "exit":
            arg = ""
            if n.args:
                arg = ast.get_source_segment(src, n.args[0]) or ""
            info["sys_exit"].append((n.lineno, arg[:60]))
    # keyword scan
    for kw in ["dry_run", "dry-run", "--apply", "ledger", "lock", "msvcrt", "fcntl", "filelock", "requests", "urllib.request", "subprocess", "argparse", "json", "csv", "preflight", "restore", "backup", "extract_again", "signal", "daemon", "threading", "asyncio", "flask", "http.server"]:
        info["keywords"][kw] = src.count(kw)
    report[rel] = info

out = os.path.join(os.environ.get("TMPDIR", "."), "tool_audit_ast.json")
with io.open(out, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
print("WROTE", out)
for rel, info in report.items():
    print("=" * 70)
    print(rel, info["bytes"], "bytes", info["lines"], "lines")
    print("  bare_except:", len(info["bare_except"]), info["bare_except"][:5])
    print("  broad_except:", len(info["broad_except"]), info["broad_except"][:5])
    print("  except_pass:", len(info["except_pass"]), info["except_pass"][:5])
    print("  exit_codes:", len(info["sys_exit"]), sorted({a for _, a in info["sys_exit"]})[:20])
    print("  n_args:", len(info["cli_args"]))
    print("  kw:", {k: v for k, v in info["keywords"].items() if v})
