import sys, cv2, time, pathlib
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen, ResourcePaths
res=ResourcePaths("2","Official").resource_dir; td=res/"template"
sc=AdbScreen("2"); sc.ensure_connected()

def shot(tag):
    f=sc.capture()
    p=pathlib.Path(f"data/agent_run/_probe_{tag}.png"); cv2.imwrite(str(p),f)
    return f

def best_terminal(f):
    g=cv2.cvtColor(f,cv2.COLOR_BGR2GRAY); best=(0,"",(0,0))
    for p in td.rglob("Terminal.png"):
        t=cv2.imread(str(p),cv2.IMREAD_GRAYSCALE)
        if t is None or t.shape[0]>g.shape[0] or t.shape[1]>g.shape[1]: continue
        r=cv2.matchTemplate(g,t,cv2.TM_CCOEFF_NORMED); _,mx,_,loc=cv2.minMaxLoc(r)
        if mx>best[0]: best=(mx,str(p).replace(str(td),''),(loc[0]+t.shape[1]//2,loc[1]+t.shape[0]//2))
    return best

def hits(f,names):
    g=cv2.cvtColor(f,cv2.COLOR_BGR2GRAY); out=[]
    for nm in names:
        for p in td.rglob(nm):
            t=cv2.imread(str(p),cv2.IMREAD_GRAYSCALE)
            if t is None or t.shape[0]>g.shape[0] or t.shape[1]>g.shape[1]: continue
            r=cv2.matchTemplate(g,t,cv2.TM_CCOEFF_NORMED); _,mx,_,loc=cv2.minMaxLoc(r)
            out.append((round(float(mx),3), nm, (int(loc[0]+t.shape[1]//2),int(loc[1]+t.shape[0]//2))))
    return sorted(out,reverse=True)[:4]

f0=shot("before")
print("before: terminal best =", best_terminal(f0))
print("before: Home.tmpl =", hits(f0,["Home.png"]))
# MAA CloseHomeSanity 点击点
sc.run_shell(["input","tap","820","645"]); time.sleep(2.5)
f1=shot("after_close")
print("after close(820,645): terminal best =", best_terminal(f1))
print("after close: Home.tmpl =", hits(f1,["Home.png"]))
