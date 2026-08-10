import sys, pathlib, json, io, time, re, subprocess, gc, os
sys.path.insert(0, "E:/MAAOrch/services/maa/instances/1/Python")
path = pathlib.Path("E:/MAAOrch/services/maa/instances/1")
from asst.asst import Asst

EMU = sys.argv[1]
STAGES = ["Chernobog@Annihilation", "LungmenOutskirts@Annihilation", "LungmenDowntown@Annihilation"]
MM = "E:/MuMu Player 12/nx_main/MuMuManager.exe"
ADB = "E:/MuMu Player 12/nx_main/adb.exe"
log_f = "E:/MAAOrch/services/maa/instances/1/debug/asst.log"
SUCC_TPL = "E:/MAAOrch/services/maa/instances/1/resource/template/Battle/UsePrts/UsePrts-AnnihilationSuccess.png"
LOCK_TPL = "E:/MAAOrch/services/maa/instances/1/resource/template/Battle/UsePrts/UnableToAgent2.png"

def emu_ready(idx, timeout=180):
    dl = time.time() + timeout
    while time.time() < dl:
        r = subprocess.run([MM, "info", "-v", str(idx)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        try:
            d = json.loads(r.stdout)
            if d.get("is_android_started") and d.get("adb_port"):
                return "127.0.0.1:" + str(d["adb_port"])
        except Exception:
            pass
        time.sleep(5)
    return None

def clear_log():
    for i in range(5):
        try:
            io.open(log_f, "w", encoding="utf-8").write("")
            return True
        except PermissionError:
            time.sleep(3)
    return False

def tpl_score(g, tpl_path):
    import cv2, numpy as np
    t = cv2.imread(tpl_path, 0).astype(np.float32)
    th, tw = t.shape
    h, w = g.shape
    best = 0.0
    for y in range(0, h - th, 3):
        for x in range(0, w - tw, 3):
            s = g[y:y+th, x:x+tw]
            n = np.sum((s - s.mean()) * (t - t.mean()))
            d = np.sqrt(np.sum((s-s.mean())**2) * np.sum((t-t.mean())**2))
            c = n / max(1e-6, d)
            if c > best:
                best = c
    return best

def main():
    r = subprocess.run([MM, "info", "-v", EMU], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    try:
        d = json.loads(r.stdout)
        if not (d.get("is_android_started") or d.get("is_process_started")):
            print("start emu " + EMU)
            subprocess.run([MM, "control", "-v", EMU, "launch"], capture_output=True, timeout=15)
    except Exception:
        pass

    print("wait ready...")
    addr = emu_ready(EMU)
    if not addr:
        print("timeout")
        sys.exit(2)
    print("ADB: " + addr)
    subprocess.run([ADB, "connect", addr], capture_output=True, text=True, timeout=10)
    time.sleep(3)

    print("StartUp...")
    clear_log()
    Asst.load(path=path)
    asst = Asst()
    ok = asst.connect(ADB, addr)
    if not ok:
        print("connect fail")
        sys.exit(1)
    asst.append_task("StartUp", {"client_type": "Official", "start_game_enabled": True})
    asst.start()
    dl = time.time() + 150
    while time.time() < dl and asst.running():
        time.sleep(3)
    asst.stop()
    del asst
    gc.collect()
    time.sleep(1)

    results = []
    for stage in STAGES:
        sname = stage.split("@")[0]
        print("--- " + sname + " ---")
        clear_log()
        Asst.load(path=path)
        asst = Asst()
        ok = asst.connect(ADB, addr)
        if not ok:
            results.append(sname + ": connect fail")
            continue
        asst.append_task("Fight", {"stage": stage, "times": 0, "medicine": 0, "stone": 0})
        asst.start()
        dl = time.time() + 120
        while time.time() < dl and asst.running():
            time.sleep(2)
        asst.stop()
        del asst
        gc.collect()
        time.sleep(1)
        log = io.open(log_f, encoding="utf-8", errors="replace").read()

        # 点击全权委托开关 + 前后对比判定
        import cv2, numpy as np
        verdict = "UNKNOWN"
        try:
            # 探测后截图（点前）
            rr = subprocess.run([ADB, "-s", addr, "exec-out", "screencap", "-p"], capture_output=True, timeout=15)
            img_before = cv2.imdecode(np.frombuffer(rr.stdout, np.uint8), cv2.IMREAD_COLOR)
            # 找蓝色开关（全权委托）— 只在"开始行动"按钮上方区域 (x 850-1200, y 520-640)
            b = img_before[:,:,2].astype(int)
            r2 = img_before[:,:,0].astype(int)
            g2 = img_before[:,:,1].astype(int)
            blue_mask = (b > 150) & (b > r2 + 40) & (b > g2 + 40)
            region_mask = np.zeros_like(blue_mask)
            region_mask[520:640, 850:1200] = True
            blue_mask = blue_mask & region_mask
            ys, xs = np.where(blue_mask)
            if len(ys) > 10:
                cx, cy = int(xs.mean()), int(ys.mean())
                # 点击开关中心
                subprocess.run([ADB, "-s", addr, "shell", "input", "tap", str(cx), str(cy)],
                               capture_output=True, timeout=10)
                time.sleep(0.8)
                # 点后截图
                rr2 = subprocess.run([ADB, "-s", addr, "exec-out", "screencap", "-p"], capture_output=True, timeout=15)
                img_after = cv2.imdecode(np.frombuffer(rr2.stdout, np.uint8), cv2.IMREAD_COLOR)
                # 右上角提示区域差异 (939-1279, 62-162)
                d1 = np.abs(img_before[62:162, 939:1279].astype(int) - img_after[62:162, 939:1279].astype(int)).mean()
                # 开关区域差异 (开关周围 100x100)
                sx0, sy0 = max(0, cx-50), max(0, cy-50)
                d2 = np.abs(img_before[sy0:sy0+100, sx0:sx0+100].astype(int) - img_after[sy0:sy0+100, sx0:sx0+100].astype(int)).mean()
                print("  点击(" + str(cx) + "," + str(cy) + ") 右上角差=" + format(d1, ".1f") + " 开关差=" + format(d2, ".1f"))
                if d1 > 10:
                    verdict = "CANNOT(弹提示)"
                elif d2 > 5:
                    verdict = "OK(开关变化)"
                else:
                    verdict = "CHECK(无变化)"
            else:
                # 没找到蓝色开关 → 可能是灰色开关（未解锁样式）→ 刷不了
                print("  未找到蓝色开关")
                # 找灰色开关（开始行动上方方框）
                verdict = "CANNOT(无蓝开关)"
            # 存档截图
            try:
                shot_dir = "E:/MAAOrch/probe_shots"
                os.makedirs(shot_dir, exist_ok=True)
                cv2.imwrite(shot_dir + "/emu" + EMU + "_" + sname + ".png", img_before)
            except Exception:
                pass
        except Exception as e:
            print("  截图失败:", e)
            verdict = "SHOTFAIL"
        results.append(sname + ": " + verdict)
        print("  " + results[-1])

    print("=== RESULT ===")
    for r2 in results:
        print(r2)
    out = "E:/MAAOrch/batch_result_" + EMU + ".txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(chr(10).join(results))
    print("saved: " + out)

main()
