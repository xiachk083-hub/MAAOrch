import json, shutil
p = r"E:\AUTO-MAS\config\ScriptConfig.json"
bak = p + ".bak_20260823"
shutil.copy2(p, bak)
d = json.load(open(p, encoding="utf-8"))
u = d["ec5e46a7-f6c2-498d-9c33-fb19b2d2a4d5"]["SubConfigsInfo"]["UserData"]["2f9360e1-5b18-4ad7-aad6-afcd9cb25098"]["Info"]
u["Annihilation"] = "LungmenOutskirts@Annihilation"
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
print("written, backup:", bak)
print("file value:", d["ec5e46a7-f6c2-498d-9c33-fb19b2d2a4d5"]["SubConfigsInfo"]["UserData"]["2f9360e1-5b18-4ad7-aad6-afcd9cb25098"]["Info"]["Annihilation"])
