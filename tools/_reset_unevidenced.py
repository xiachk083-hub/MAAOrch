import json
import pathlib

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
rv = json.loads((ROOT / "runtime" / "plans" / "reverify_list.json").read_text(encoding="utf-8"))
units = list(rv["no_evidence"]) + [w["unit"] for w in rv["weak_only"]]

p = ROOT / "runtime" / "_prime_state3.json"
st = json.loads(p.read_text(encoding="utf-8"))
n = 0
for u in units:
    if st.get(u) == "done":
        st[u] = "todo"
        n += 1
p.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
print("把 %d 个『标了 done 但无/弱证据』的单位改回 todo 重新核验" % n)
print("现在可打:", sum(1 for v in st.values() if v in ("todo", "partial")))
