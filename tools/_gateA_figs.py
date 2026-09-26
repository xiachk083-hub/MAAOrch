"""出图：最新一轮（含帧态闸）的 Gate A 逐帧诊断。跑法：python tools/_gateA_figs.py"""
import sys, cv2, numpy as np, pathlib, glob
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import gate_a_check as G, battle_loop as BL
from scipy.optimize import linear_sum_assignment

out = []
for nm, p in G.frames(10):
    im = cv2.imread(str(p))
    if im is None or G.is_selected_state(im):
        continue
    B = G.hp_bars(im)
    if len(B) < 3:
        continue
    try:
        L = BL.Loop(nm, None)
    except Exception:
        continue
    s = L.stage_px
    tiles = [(c, r) for r in range(L.lv.height) for c in range(L.lv.width)
             if (t := L.lv.at(c, r)) and t.buildable and L.lv.project(c, r)]
    pts = [np.array(L.lv.project(c, r)) * s for c, r in tiles]
    q0 = np.array(L.lv.project(0, 0)) * s
    q1 = np.array(L.lv.project(1, 0)) * s if L.lv.project(1, 0) else q0
    pitch = float(np.hypot(*(q1 - q0))) or 130.0
    P = np.array([[q[0], q[1]] for q in pts])
    D = np.linalg.norm(P[:, None, :] - B[None, :, :], axis=2)
    C = np.where(D <= 1.5 * pitch, D, 1e6)
    ri, ci = linear_sum_assignment(C)
    pr = [(int(i), int(j), float(C[i, j])) for i, j in zip(ri, ci) if C[i, j] < 1e6]
    if len(pr) < 3:
        continue
    rng = np.random.default_rng(7)
    idx = rng.permutation(len(pr))
    tr = idx[: max(1, int(len(idx) * 0.7))]
    te = idx[max(1, int(len(idx) * 0.7)):]
    A = np.array([P[i] for i, _j, _d in pr])
    Qm = np.array([B[j] for _i, j, _d in pr])
    M, _ = cv2.estimateAffinePartial2D(A[tr], Qm[tr], method=cv2.LMEDS)
    if M is None or not len(te):
        continue
    e = np.linalg.norm((M[:, :2] @ A[te].T).T + M[:, 2] - Qm[te], axis=1)
    em, ex = float(np.median(e)) / pitch, float(e.max()) / pitch
    out.append((nm, im, L, tiles, P, pitch, B, pr, em, ex))

for k, (nm, im, L, tiles, P, pitch, B, pr, em, ex) in enumerate(out, 1):
    v = im.copy()
    for (c, r) in tiles:
        q = np.array(L.lv.project(c, r)) * L.stage_px
        cv2.circle(v, (int(q[0]), int(q[1])), 4, (255, 180, 0), -1, cv2.LINE_AA)
    worst = max(pr, key=lambda z: z[2])
    for i, j, d in pr:
        isw = (i, j, d) == worst
        cv2.line(v, (int(P[i][0]), int(P[i][1])), (int(B[j][0]), int(B[j][1])),
                 (255, 0, 255) if isw else (0, 255, 255), 4 if isw else 2, cv2.LINE_AA)
        cv2.putText(v, '%.2f' % (d / pitch), (int((P[i][0] + B[j][0]) / 2) - 14, int((P[i][1] + B[j][1]) / 2) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
    for (bx, by) in B:
        cv2.circle(v, (int(bx), int(by)), 18, (0, 0, 255), 3, cv2.LINE_AA)
    v = cv2.resize(v, (1000, 562))
    cv2.rectangle(v, (0, 0), (1000, 34), (0, 0, 0), -1)
    cv2.putText(v, '%s  帧态=正常运行 ✓  HP条%d 配%d ｜ 留出残差 中位%.2f/最大%.2f 格 ⇒ %s'
                % (nm, len(B), len(pr), em, ex, 'PASS ✓' if ex < 0.5 else 'FAIL ✗'),
                (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.66,
                (0, 200, 0) if ex < 0.5 else (0, 120, 255), 2, cv2.LINE_AA)
    cv2.imwrite('data/_run2_%02d.png' % k, v)   # ⚠️ 文件名必须 ASCII：cv2.imread 读不了中文名 ✗
    print('  ✓ %-8s 残差 %.2f/%.2f ⇒ %s' % (nm, em, ex, 'PASS' if ex < 0.5 else 'FAIL'))

fs = sorted(glob.glob('data/_run2_*.png'))
if fs:
    ts = [cv2.copyMakeBorder(cv2.imread(f), 3, 3, 3, 3, cv2.BORDER_CONSTANT, value=(30, 30, 30)) for f in fs]
    h, w = max(t.shape[0] for t in ts), max(t.shape[1] for t in ts)
    ts = [cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, w - t.shape[1], cv2.BORDER_CONSTANT, value=(30, 30, 30)) for t in ts]
    rows = [np.hstack(ts[i:i + 2]) for i in range(0, len(ts), 2)]
    cv2.imwrite('data/_run2_sheet.png', np.vstack(rows))
    print('✓ data/_gateA_run2.png（%d 帧；洋红=最差那一对）' % len(ts))
