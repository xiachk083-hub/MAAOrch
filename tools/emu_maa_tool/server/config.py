"""工具配置（server.json）+ 数据目录管理。"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = TOOL_ROOT / "data"
CONFIG_FILE = DATA_DIR / "server.json"
INSTANCES_DIR = TOOL_ROOT / "maa_instances"
WEB_DIR = TOOL_ROOT / "web"

DEFAULT_CONFIG = {
    "port": 18889,
    "token": "",                 # 空 = 不鉴权（本机模式）；远程部署必须设置
    "maa_source_path": "",       # MAA 模板目录（目标机 E:\MAAOrch\services\maa\source）
    "project_root": "",          # MAAOrch 项目根（复用 task_constants 等）
    "pool_size": 4,              # 副本池并发上限
    "config_sync_interval": 30,  # MAA 运行期配置同步间隔（秒）
}


def ensure_dirs() -> None:
    for d in (DATA_DIR, DATA_DIR / "configs", DATA_DIR / "logs",
              INSTANCES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    ensure_dirs()
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def _guess_defaults(cfg: dict) -> dict:
    """未配置 maa_source_path / project_root 时自动探测本机路径。"""
    if not cfg.get("maa_source_path"):
        # 探测: 本机 MAAOrch 开发副本
        cand = TOOL_ROOT.parent.parent / "services" / "maa" / "source"
        if (cand / "MAA.exe").exists():
            cfg["maa_source_path"] = str(cand)
            cfg["project_root"] = str(TOOL_ROOT.parent.parent)
    if not cfg.get("project_root"):
        src = cfg.get("maa_source_path", "")
        if src:
            cfg["project_root"] = str(Path(src).parent.parent.parent)
    return cfg


def get_config() -> dict:
    cfg = load_config()
    _guess_defaults(cfg)
    return cfg


def setup_sys_path() -> None:
    """把 MAAOrch 项目根加入 sys.path（复用 task_constants / instance_pool /
    emu_service 的纯函数）。"""
    cfg = get_config()
    root = cfg.get("project_root") or ""
    if root and root not in sys.path:
        sys.path.insert(0, root)


def data_file(*parts: str) -> Path:
    return DATA_DIR.joinpath(*parts)
