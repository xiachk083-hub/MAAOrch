"""box 验收清单（box 到位后**第一件事**，不过就停，不往下跑 candidates）。

为什么要先跑：47 份 box 来自目标机，schema 可能与本地历史 box 不一致
（2026-09-16 已踩过 operbox_emu* 的 0/0）。box 解析失败会让 compute_missing
把账号判成「全部缺员」并输出一堆 no —— 看起来像「没有可跑作业」，实际是数据问题。

三个数（用户定义）：
  ① 全部解析成功   ② 每份 owned_roles 非空（char_table join 成功）   ③ 无人 0 人（已知 schema 不符类明确标出）
"""
import json, glob, pathlib, sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
from tools.match_candidates import build_box_index
from tools.batch_index import load_box, BoxParseError

BATCH = PROJ / "data" / "operbox_batch"


def accept(files: list[str]) -> int:
    ok, empty_role, failed = [], [], []
    for f in sorted(files):
        name = pathlib.Path(f).stem
        try:
            raw = load_box(f)                       # 0 人 / own 全假 → BoxParseError（不静默当 0）
            idx = build_box_index(raw)
            if not idx["owned_roles"]:
                empty_role.append(name)
            else:
                ok.append((name, len(idx["owned_names"]), len(idx["owned_roles"])))
        except BoxParseError as e:
            failed.append((name, str(e)))
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))
    print(f"① 解析成功: {len(ok)}/{len(files)}")
    for n, nops, nroles in ok[:8]:
        print(f"   ✓ {n:<28} {nops} 人 / {nroles} 职业")
    if len(ok) > 8:
        print(f"   …（其余 {len(ok)-8} 份同样通过）")
    print(f"② owned_roles 为空的份: {len(empty_role)}" + (f" → {empty_role}" if empty_role else " ✓"))
    print(f"③ 解析失败(0人/schema不符)的份: {len(failed)}")
    for n, why in failed:
        print(f"   ✗ {n:<28} {why}")
    passed = bool(files) and not empty_role and not failed
    print("\n[判定]", "通过 ✓ 可以跑 match_candidates.py" if passed
          else "不通过 ✗ —— 先查 schema，不要往下跑 candidates")
    return 0 if passed else 1


if __name__ == "__main__":
    files = glob.glob(str(BATCH / "*.json"))
    if not files:
        print(f"[SKIP] {BATCH.name}/ 为空（目标机 box 未导出）")
        print("       用本地历史 box 做一次自检：")
        files = sorted(glob.glob(str(PROJ / "data" / "operbox_pasted_0915*.json")))
        if not files:
            sys.exit(0)
    sys.exit(accept(files))
