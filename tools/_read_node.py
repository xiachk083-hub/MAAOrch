import sys, time, cv2, numpy as np, pathlib, hashlib
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen, TemplateMatcher
import screen_state as SS
sc=AdbScreen("2"); sc.ensure_connected(); tm=None
def cap(tag):
    f=sc.capture(); cv2.imwrite(f"data/agent_run/_rn_{tag}.png", f); return f
def find(name, thr=0.9):
    global tm
    if tm is None:
        from services.ai_agent.percept import TemplateMatcher as T; tm=T()
    f=sc.capture(); h=tm.find(f, name, threshold=thr)
    return (h.center if h else None), f
def tap(p,w=2.0): sc.run_shell(["input","tap",str(p[0]),str(p[1])]); time.sleep(w)

f=cap("0_lobby"); print("起点 state:", SS.state_of(f)[0])
# 1) 大厅 → 任务
tap((820,645),2.2)
# 2) 小房子（模板定位，不写死）
c,_=find("QuickSwitch/Menu.png")
print("小房子:", c)
if c: tap(c,1.8)
elif _: print("  !! 没找到小房子")
c2,_=find("QuickSwitch/Terminal.png")
print("终端入口:", c2)
if c2: tap(c2,3.0)
f=cap("1_terminal"); print("终端页 state:", SS.state_of(f)[0])
# 3) 前往上一次作战 → 详情页
tap((1100,578),3.5)
f=cap("2_detail"); print("详情页 state:", SS.state_of(f)[0])
# 4) 返回一次 → 活动地图
sc.run_shell(["input","keyevent","4"]); time.sleep(2.5)
f=cap("3_map"); s,c=SS.state_of(f); print("地图 state:", s, c)
if s!="exmap":
    print("  不是地图 → 再返回一次")
    sc.run_shell(["input","keyevent","4"]); time.sleep(2.5)
    f=cap("4_map2"); s,c=SS.state_of(f); print("  再试 state:", s, c)
if s=="exmap":
    def col(box):
        x0,y0,x1,y1=box; r=f[y0:y1,x0:x1]; hsv=cv2.cvtColor(r,cv2.COLOR_BGR2HSV)
        return r.reshape(-1,3).mean(0).round(0), round(float(hsv[:,:,1].mean()),1), round(float(hsv[:,:,0].mean()),1)
    res={}
    for tag,box in {"GA-EX-1 对照":(316,148,346,180),"GA-EX-2 目标":(316,260,346,292),
                    "GA-EX-1 标签区":(330,140,420,190),"GA-EX-2 标签区":(330,250,420,300)}.items():
        bgr,sat,hue=col(box); res[tag]=(bgr,sat,hue)
        print(f"    {tag}: BGR={bgr} 饱和={sat} 色相={hue}")
    s1=res["GA-EX-1 对照"][1]; s2=res["GA-EX-2 目标"][1]
    print(f"\n判定: EX-1 饱和={s1} / EX-2 饱和={s2} → " +
          ("GA-EX-2 已亮（与已三星的 EX-1 同色）" if abs(s1-s2)<30 and s2>60 else "GA-EX-2 未亮（与 EX-1 明显不同或低饱和）"))
