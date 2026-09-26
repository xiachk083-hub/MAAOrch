import json, shutil
p = r"E:\AUTO-MAS\config\ScriptConfig.json"
bak = p + ".bak_20260823b"
shutil.copy2(p, bak)
d = json.load(open(p, encoding="utf-8"))
u = d["0e12fe1e-a8da-42da-8e35-68a90cca73e8"]["SubConfigsInfo"]["UserData"]["3aad7c6b-7cbf-45fd-b6a5-05e4918b697f"]["Info"]
u["Annihilation"] = "LungmenOutskirts@Annihilation"
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
print("written, backup:", bak)
print("file value:", d["0e12fe1e-a8da-42da-8e35-68a90cca73e8"]["SubConfigsInfo"]["UserData"]["3aad7c6b-7cbf-45fd-b6a5-05e4918b697f"]["Info"]["Annihilation"])
