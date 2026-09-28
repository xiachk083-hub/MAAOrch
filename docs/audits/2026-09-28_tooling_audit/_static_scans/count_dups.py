"""Count how many tools/*.py define each headline helper name."""
import ast, io, os, collections

TOOLS = r"D:\1-2\MAAOrch\tools"
names = ["post", "reachable", "ssh_ps", "audit", "load_json", "save_json", "scrub",
         "pick_python", "selftest", "_now", "_env_int", "norm_dir", "_rel",
         "load_state", "save_state", "sh", "host_probe", "read_host_file",
         "ledger_append", "_append_jsonl", "_lock", "hold_lock", "read_host_tail",
         "host_snapshot", "_ssh_base", "_child_env", "_ssh_exe", "_mask"]
counts = collections.Counter()
where = collections.defaultdict(list)
files = 0
for fn in sorted(os.listdir(TOOLS)):
    if not fn.endswith(".py"):
        continue
    files += 1
    try:
        tree = ast.parse(io.open(os.path.join(TOOLS, fn), encoding="utf-8").read())
    except SyntaxError:
        continue
    defs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for nm in names:
        if nm in defs:
            counts[nm] += 1
            where[nm].append(fn)
print("tools/*.py files scanned:", files)
for nm in names:
    if counts[nm] > 1:
        print("%-16s x%-3d %s" % (nm, counts[nm], ", ".join(w.replace(".py", "") for w in where[nm])))
