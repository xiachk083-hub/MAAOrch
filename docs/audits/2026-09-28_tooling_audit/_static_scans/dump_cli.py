import json, io, sys
r = json.load(io.open(r"C:\Users\xiach\AppData\Local\Temp\tool_audit_ast.json", encoding="utf-8"))
sel = sys.argv[1:] or list(r)
for f in sel:
    i = r[f]
    print("=" * 70)
    print(f)
    for a in i["cli_args"]:
        flags = "/".join(a["flags"])
        print(f"  L{a['line']:<5} {flags:<36} default={a['default']!r} action={a['action']} nargs={a['nargs']} choices={a['choices']}")
