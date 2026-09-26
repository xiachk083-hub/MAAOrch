"""盯 v5：出结果 → 失败就立刻齿轮+放弃行动退出（不等）→ 存终局图；成功则验三星。"""
import sys, cv2, time, pathlib
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
sc=AdbScreen("2"); sc.ensure_connected()
lg=open("data/agent_run/_watch_v5.log","w",encoding="utf-8")
def L(m): lg.write(m+"\n"); lg.flush()
def st():
    f=sc.capture(); return (SS.state_of(f) if f is not None else ("other",0.0)), f
res=None
for i in range(60):                      # 最多 10 分钟
    (s,c),f=st()
    if s in ("win","fail"):
        res=s; cv2.imwrite(f"data/agent_run/_v5_result_{s}.png", f); L(f"[{i*10}s] 结果={s}"); break
    if i%3==0:
        cv2.imwrite("data/agent_run/_v5_latest.png", f); L(f"[{i*10}s] {s}")
    time.sleep(10)
if res=="fail":
    L("失败 → 立刻退出（齿轮→放弃行动）")
    sc.run_shell(["input","tap","80","45"]); time.sleep(1.8)
    sc.run_shell(["input","tap","960","574"]); time.sleep(2.5)
    for j in range(8):
        (s,c),f=st(); L(f"  清场{j}) {s}")
        if s in ("detail","form"): cv2.imwrite("data/agent_run/_v5_back_detail.png", f); break
        if s=="exmap":
            sc.run_shell(["input","tap","370","275"]); time.sleep(2.5); continue
        sc.run_shell(["input","tap","640","660"]); time.sleep(1.6)
    L("已退出到关卡页，等下一版编排")
elif res=="win":
    cv2.imwrite("data/agent_run/_v5_win.png", sc.capture()); L("通关！去核对三星/代理指挥")
else:
    L("10 分钟未见结果（可能仍在打）")
L("守望结束")
