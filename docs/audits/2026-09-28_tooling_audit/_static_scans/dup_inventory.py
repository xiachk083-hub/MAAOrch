"""Cross-tool duplicate-helper inventory (AST, read-only)."""
import ast, io, os, collections, json

TOOLS = r"D:\1-2\MAAOrch\tools"
TARGETS = ["mas_run_profile.py", "mas_ondemand.py", "mas_plan_builder.py",
           "mas_ondemand_mcp.py", "mas_probe_farm.py", "stage_capability.py"]

def top_funcs(path):
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    out = {}
    for n in tree.body:  # top-level only
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[n.name] = (n.end_lineno or n.lineno) - n.lineno + 1
    return out

files = {}
alltools = {}
for name in sorted(os.listdir(TOOLS)):
    if not name.endswith(".py"):
        continue
    p = os.path.join(TOOLS, name)
    try:
        fns = top_funcs(p)
    except SyntaxError as e:
        print("SKIP", name, e)
        continue
    alltools[name] = fns
    if name in TARGETS:
        files[name] = fns

# duplicates among the 6 targets
owner = collections.defaultdict(list)
for f, fns in files.items():
    for fn, L in fns.items():
        owner[fn].append((f, L))
print("### duplicate top-level defs among the 6 target tools (name: file(len))")
for fn, lst in sorted(owner.items(), key=lambda kv: -len(kv[1])):
    if len(lst) > 1:
        print("  %-26s %s" % (fn, ", ".join("%s(%d)" % (f.replace('.py', ''), L) for f, L in lst)))

# how widely each name from the targets appears in the whole tools/ dir
print()
print("### target helper names also defined in OTHER tools/*.py (>=1 other file)")
for f, fns in files.items():
    for fn in fns:
        others = [g for g, gs in alltools.items() if g != f and fn in gs]
        if others:
            print("  %-24s %-22s -> %s" % (fn, f.replace('.py', ''), ", ".join(o.replace('.py', '') for o in others[:8]) + (" ..." if len(others) > 8 else "")))
