import pathlib

p = pathlib.Path(r"D:\1-2\MAAOrch\tools\name_event_cards.py")
s = p.read_text(encoding="utf-8")
old = '''        q = ("这是一张《明日方舟》活动卡片的截图。请只回答 JSON："
             '{"name":"活动中文名","prefix":"关卡代号前缀(如 LE/PV/MT/EP/CV/AD，不确定写 ?)"}。'
             "不确定就写 ?，不要猜。")'''
new = '''        # **注意**：prompt 里不要放引号/花括号 —— Windows 下 subprocess 传参会被拆坏，
        # modlens 收到残缺 prompt 就返回空（实测：同一张图用简单问法能答对"丛林症结"）。
        q = ("这是明日方舟的活动卡片截图。请只回答一行，格式：活动中文名|关卡代号前缀。"
             "前缀形如 LE/PV/MT/EP/CV/AD/EA；不确定就写成 ?。不要输出其它内容。")'''
assert old in s, "锚点未找到"
s = s.replace(old, new)

old2 = '''        ans = v.ask(p, q)
        s = ans[ans.find("{"): ans.rfind("}") + 1] if "{" in ans else ""
        rec = {"raw": ans[:200]}
        try:
            d = json.loads(s)
            rec["name"] = (d.get("name") or "").strip()
            rec["prefix"] = (d.get("prefix") or "?").strip().upper()
        except Exception:
            rec["name"] = ""
            rec["prefix"] = "?"'''
new2 = '''        ans = v.ask(p, q)
        rec = {"raw": ans[:200], "name": "", "prefix": "?"}
        line = next((ln for ln in (ans or "").splitlines() if "|" in ln), "")
        if line:
            name, _, pre = line.partition("|")
            rec["name"] = name.strip().strip("。 .")
            rec["prefix"] = (pre.strip().strip("。 .") or "?").upper()[:6]'''
assert old2 in s, "锚点2未找到"
s = s.replace(old2, new2)
p.write_text(s, encoding="utf-8")
print("已改 prompt（去引号）+ 解析（按 | 切分）")
