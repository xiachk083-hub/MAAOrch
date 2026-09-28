import json, io, sys
r = json.load(io.open(r"C:\Users\xiach\AppData\Local\Temp\tool_audit_ast.json", encoding="utf-8"))
for f, i in r.items():
    print("=" * 70)
    print(f)
    print("  imports:", ", ".join(i["imports"]))
    print("  longest funcs (len, line, name):")
    for L, ln, name in i["longest_funcs"]:
        print(f"     {L:5d} lines  L{ln:<5} {name}")
    if i["except_pass"]:
        print("  except->pass sites:", i["except_pass"])
