import pathlib

p = pathlib.Path(r"D:\1-2\MAAOrch\tools\prts_job_finder.py")
s = p.read_text(encoding="utf-8")
old = 'norm(a.get("name",""))'
new = 'norm(a.get("name") or "")'
if old in s:
    p.write_text(s.replace(old, new), encoding="utf-8")
    print("已修 prts_job_finder.py:208 的 NoneType 崩溃（动作 name=null 时）")
else:
    print("未找到目标片段，可能已被改过；当前含 'a.get(\"name\")' 次数:", s.count('a.get("name")'))
