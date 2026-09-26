"""AI 导航：读图 → 判断页面与下一步按钮 → 点一次 → 双重验证。只验证**导航**，不打关。

2026-09-16 定稿纪律：坐标由模型给（不猜）；一步一验证（画面必须变化）；
连续两步同一按钮即判「打转」停止；模型输出非 JSON 则重试一次。
"""
import sys, time, json, re, cv2, numpy as np, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, "tools")
from services.ai_agent.percept import AdbScreen
from services.ai_agent import llm as LLM
import screen_state as SS

Q = ("这是明日方舟截图。目标：走到 GA-EX-2 关卡详情页（该页有「开始行动」按钮和关卡名 GA-EX-2）。\n"
     "回答：当前是什么页面？下一步应该点哪个按钮？给出该按钮中心相对坐标 x,y（0~1）。\n"
     "只输出 JSON: {\"page\":\"...\",\"done\":true/false,\"button\":\"...\",\"x\":0.xx,\"y\":0.yy,\"reason\":\"...\"}\n"
     "若**已经**在 GA-EX-2 详情页（能看到「开始行动」），done 填 true。\n"
     "常用路径：主界面→点右上/侧边的「终端」或先点「任务」进入子页再用左上小房子切到终端；"
     "终端里找到 GA-EX-2 节点或「前往上一次作战」卡片。")


def ask(frame, L, retry=True):
    r = L.chat("你是方舟UI导航助手，只描述看到的、只给一个动作。", Q, image=frame, vision=True, max_tokens=16000)
    txt = (r.get("text") or "") if isinstance(r, dict) and r.get("ok") else ""
    m = re.search(r"\{[\s\S]*\}", txt)
    if not m and retry:
        return ask(frame, L, retry=False)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


KNOWN_EXITS = {                     # ② 层：已知页的预定义出口（相对坐标）
    "main":   (0.62, 0.80),         # 主界面 → 任务（今晚实测路径的第一步）
    "detail": None,                 # 目标页
    "form":   None,                 # 目标页（编队 = 详情页的下一步，MAA 可直接起）
}


def navigate_step(sc, frame, L, W):
    """三层匹配：①指纹(0延迟) → ②state_of已知页 → ③读图+验证存盘。

    每层命中就不进下一层；纪要越全，读图越少（2026-09-16 用户定）。
    """
    import time as _t
    from tools.page_memory import lookup_page, verify_and_save
    import screen_state as _SS

    # ① 指纹
    page = lookup_page(frame)
    if page:
        if page.get("exit") is None:
            return "arrived", f"指纹命中「{page.get('name')}」(目标页)"
        e = page["exit"]
        x, y = int(e["x"] * W), int(e["y"] * frame.shape[0])
        sc.run_shell(["input", "tap", str(x), str(y)]); _t.sleep(1.0)
        return "advanced", f"指纹命中「{page.get('name')}」→ 点 {e.get('name')} ({x},{y})"

    # ② 已知页
    st, conf = _SS.state_of(frame)
    if st in KNOWN_EXITS:
        tgt = KNOWN_EXITS[st]
        if tgt is None:
            return "arrived", f"已知页 {st}（目标页）"
        x, y = int(tgt[0] * W), int(tgt[1] * frame.shape[0])
        sc.run_shell(["input", "tap", str(x), str(y)]); _t.sleep(1.0)
        return "advanced", f"已知页 {st} → 点 ({x},{y})"

    # ③ 读图（新页面）+ 验证存盘
    d = ask(frame, L)
    ex = d.get("exit") or ({"x": d.get("x"), "y": d.get("y"), "name": d.get("button")}
                           if d.get("x") is not None else None)
    if not ex or ex.get("x") is None:
        return "fail", f"AI 找不到出口: {d.get('page')}"
    ok = verify_and_save(frame, ex, lambda x, y: sc.run_shell(["input", "tap", str(x), str(y)]),
                         sc.capture)
    if not ok:
        return "fail", f"验证失败，不存纪要（页: {d.get('page')}）"
    return "advanced", f"新页面「{d.get('page')}」→ 已存纪要并退出"


def main(max_steps=6):
    sc = AdbScreen("2"); sc.ensure_connected(); L = LLM.LLM()
    W = None; done = False; last_btn = None; same = 0
    for step in range(max_steps):
        f = sc.capture()
        if W is None:
            W = f.shape[1]
        st = SS.state_of(f)[0]
        verdict, why = navigate_step(sc, f, L, W)
        print(f"步{step}: state={st} → {verdict} | {why}")
        if verdict == "arrived":
            done = True; break
        if verdict == "fail":
            break
        f2 = sc.capture()
        diff = float(np.abs(f2.astype(int) - f.astype(int)).mean())
        print(f"       验证: 像素差={diff:.1f} {'✓' if diff > 3 else '✗没变→停'}")
        if diff <= 3:
            break
        continue
        btn = str(d.get("button"))
        same = same + 1 if btn == last_btn else 0
        last_btn = btn
        if same >= 1:
            print("连续两步同一按钮 → 判打转，停止"); break
        try:
            x, y = int(float(d["x"]) * W), int(float(d["y"]) * f.shape[0])
        except Exception:
            print("坐标不合法 → 停"); break
        sc.run_shell(["input", "tap", str(x), str(y)]); time.sleep(1.0)
        f2 = sc.capture()
        diff = float(np.abs(f2.astype(int) - f.astype(int)).mean())
        print(f"       验证: 像素差={diff:.1f} {'✓画面已变' if diff > 3 else '✗没变→停'}")
        if diff <= 3:
            break
    print("结果:", "导航成功 ✓" if done else "未到达 ✗")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 6)
