#!/usr/bin/env python3
"""AUTO-MAS 账号信息一键导出表格。

拉取全部账号配置（昵称/Id/服务器/关卡/剿灭/基建/队列/状态）导出 CSV，
Excel 双击直接打开（utf-8-sig）。

用法:
    python tools/auto_mas_export.py                      # 导出到 data/ 下
    python tools/auto_mas_export.py --host <addr>        # 指定 AUTO-MAS API
    python tools/auto_mas_export.py --out <file.csv>     # 指定输出文件
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
import time
import urllib.request

DEFAULT_HOST = "http://100.79.173.69:36163"
DEFAULT_OUT = pathlib.Path(__file__).resolve().parent.parent / "data"


def api(host: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        host + path,
        data=json.dumps(body if body is not None else {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def collect(host: str) -> list[dict]:
    """拉取全部账号信息，返回行列表。"""
    sc = api(host, "/api/scripts/get", {"scriptId": None})
    rows = []
    for it in sc.get("index", []):
        if it.get("type") != "MaaConfig":
            continue
        sid = it["uid"]
        data = sc["data"][sid]
        script_name = data["Info"].get("Name", "?").replace("明日方舟-", "")
        maa = str(data["Info"].get("Path", "")).replace("E:\\MAA-Multi\\", "")
        emu_idx = str(data.get("Emulator", {}).get("Index", "?"))

        u = api(host, "/api/scripts/user/get", {"scriptId": sid, "userId": None})
        for u_uid, uu in u.get("data", {}).items():
            ui = uu.get("Info", {})
            rows.append({
                "账号": script_name,
                "MAA副本": maa,
                "模拟器": emu_idx,
                "服务器": ui.get("Server", "?"),
                "用户名": ui.get("Name", ""),
                "Id": ui.get("Id", ""),
                "备注": ui.get("Notes", ""),
                "关卡模式": ui.get("StageMode", "?"),
                "主关卡": ui.get("Stage", "-"),
                "备选1": ui.get("Stage_1", "-"),
                "备选2": ui.get("Stage_2", "-"),
                "备选3": ui.get("Stage_3", "-"),
                "扫尾": ui.get("Stage_Remain", "-"),
                "剿灭": ui.get("Annihilation", "?"),
                "基建模式": ui.get("InfrastMode", "?"),
                "启用": "是" if ui.get("Status") is not False else "停用",
            })
        time.sleep(0.05)
    return rows


def write_csv(rows: list[dict], out: pathlib.Path) -> None:
    fields = ["账号", "MAA副本", "模拟器", "服务器", "用户名", "Id", "备注",
              "关卡模式", "主关卡", "备选1", "备选2", "备选3", "扫尾",
              "剿灭", "基建模式", "启用"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> None:
    ap = argparse.ArgumentParser(description="AUTO-MAS 账号信息一键导出表格")
    ap.add_argument("--host", default=DEFAULT_HOST, help="AUTO-MAS API 地址")
    ap.add_argument("--out", default=None, help="输出 CSV 路径")
    args = ap.parse_args()
    host = args.host.rstrip("/")

    print("== 拉取 AUTO-MAS 账号信息 ==")
    rows = collect(host)
    if not rows:
        sys.exit("[错误] 未拉取到账号信息（AUTO-MAS 是否在运行？）")
    rows.sort(key=lambda r: r["账号"])

    out = pathlib.Path(args.out) if args.out else (
        DEFAULT_OUT / f"auto_mas_accounts.csv")
    write_csv(rows, out)
    print(f"导出 {len(rows)} 个账号 -> {out}")

    # 控制台也打一份
    print(f"\n{'账号':<12}{'服':<4}{'用户名':<14}{'Id':<14}{'主关卡':<8}{'剿灭':<10}{'基建':<8}{'MAA'}")
    for r in rows:
        print(f"{r['账号']:<12}{r['服务器']:<4}{str(r['用户名']):<14}{str(r['Id']):<14}"
              f"{r['主关卡']:<8}{r['剿灭']:<10}{r['基建模式']:<8}{r['MAA副本']}")
    print(f"\n共 {len(rows)} 个账号，表格已导出。双击打开 CSV 即见。")


if __name__ == "__main__":
    main()
