import sys, cv2, time, pathlib, json
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
sc=AdbScreen("2"); sc.ensure_connected()

def cap(tag, roi=None):
    f=sc.capture()
    if roi: x,y,w,h=roi; f=f[y:y+h, x:x+w] if False else f
    cv2.imwrite(f"data/agent_run/_qs_{tag}.png", f)
    return f

# 1) 点小房子
sc.run_shell(["input","tap","213","40"]); time.sleep(2.0)
f=cap("menu")
# 2) 在 MAA 的 QuickSwitch 模板里找命中（全屏，看下拉里有哪些入口）
import glob
g=cv2.cvtColor(f,cv2.COLOR_BGR2GRAY)
rows=[]
for p in sorted(glob.glob("services/maa/instances/2/resource/template/QuickSwitch/*.png")):
    t=cv2.imread(p, cv2.IMREAD_GRAYSCALE)
    if t is None or t.shape[0]>g.shape[0] or t.shape[1]>g.shape[1]: continue
    r=cv2.matchTemplate(g,t,cv2.TM_CCOEFF_NORMED); _,mx,_,loc=cv2.minMaxLoc(r)
    rows.append((round(float(mx),3), pathlib.Path(p).name, (int(loc[0]+t.shape[1]//2), int(loc[1]+t.shape[0]//2))))
rows.sort(reverse=True)
print("QuickSwitch 下拉命中：")
for r in rows: print("   ", r)
