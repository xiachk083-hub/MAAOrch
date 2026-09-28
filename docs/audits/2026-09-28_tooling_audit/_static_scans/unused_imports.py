"""Unused-import + unused-global-name check for the 6 target tools (AST)."""
import ast, io, os, sys

ROOT = r"D:\1-2\MAAOrch\tools"
FILES = ["mas_run_profile.py", "mas_ondemand.py", "mas_plan_builder.py",
         "mas_ondemand_mcp.py", "mas_probe_farm.py", "stage_capability.py"]

for fn in FILES:
    path = os.path.join(ROOT, fn)
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    imported = {}   # local name -> (lineno, module)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                local = (a.asname or a.name).split(".")[0]
                imported[local] = (n.lineno, a.name)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == "*":
                    continue
                local = a.asname or a.name
                imported[local] = (n.lineno, "%s.%s" % (n.module, a.name))
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            used.add(n.id)
        elif isinstance(n, ast.Attribute):
            pass
    # names used as attribute bases resolve through Name nodes; also strings in getattr()
    text = src
    unused = []
    for local, (ln, orig) in sorted(imported.items(), key=lambda kv: kv[1][0]):
        if local in used:
            continue
        # count textual mentions outside the import line
        cnt = text.count(local)
        if cnt <= 1:
            unused.append((ln, local, orig))
    print("== %-24s imports=%2d  likely-unused=%s" % (fn, len(imported), unused or "none"))
