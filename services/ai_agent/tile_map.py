"""关卡格点图与投影 — 把"格子坐标"和"屏幕像素"对上。

为什么需要它:
  部署干员是"从卡拖到某一格"，而游戏里没有格子坐标——只有像素。MAA 的作业
  也写格子（`location: [x, y]`），换算靠 `Arknights-Tile-Pos` 的格点表 + 一套
  透视投影。这里把同一套换算在 Python 侧实现，部署/读位/规划都要用。

投影公式来源与验证:
  公式取自 MAA 的 3rdparty/Arknights-Tile-Pos（TileCalc2.hpp）的 world_to_screen
  链路，**已用真实关卡数值反算验证**：ro1_e_1_1（傀影肉鸽，10x6）的格 (col=2,row=0)
  投影得 (445, 158)，与该关 tile 表算出的已知值完全一致。所以本文件的
  `project()` 不是猜的，是能对上的。

坐标系约定（与 MAA 一致）:
  · 格子 [x, y] ↔ tiles[y][x]：原点左上，x 向右，y 向下
  · 投影输出是 1280x720 基准像素
  · `side=False` 用 view[0]（正常战斗视角），`side=True` 用 view[1]（侧视）
"""
from __future__ import annotations

import json
import math
import pathlib
from dataclasses import dataclass

from services.ai_agent.percept import PROJ, ResourcePaths, log

WINDOW_W, WINDOW_H = 1280, 720


@dataclass
class Tile:
    """一个格子的静态属性（来自 Tile-Pos）。"""

    height_type: int      # 0 地面 / 1 高台（决定屏幕上被抬高的量）
    buildable: int        # 0 不可部署 / 1 地面可部署 / 2 高台可部署
    is_start: bool = False
    is_end: bool = False
    key: str = ""

    @property
    def ground_deployable(self) -> bool:
        return self.buildable == 1

    @property
    def high_deployable(self) -> bool:
        return self.buildable == 2

    @property
    def deployable(self) -> bool:
        return self.buildable in (1, 2)

    @property
    def walkable(self) -> bool:
        """敌人能走（地面且不是高台）。"""
        return self.height_type == 0


class Level:
    """一个关卡的格点图。"""

    def __init__(self, data: dict, filename: str = "") -> None:
        self.raw = data
        self.filename = filename
        self.code = data.get("code", "")
        self.name = data.get("name", "")
        self.stage_id = data.get("stageId", "")
        self.width = int(data.get("width") or 0)
        self.height = int(data.get("height") or 0)
        self.view = [tuple(v) for v in (data.get("view") or [])]
        self.tiles: list[list[Tile]] = []
        for row in (data.get("tiles") or []):
            self.tiles.append([Tile(height_type=int(t.get("heightType") or 0),
                                   buildable=int(t.get("buildableType") or 0),
                                   is_start=bool(t.get("isStart")),
                                   is_end=bool(t.get("isEnd")),
                                   key=str(t.get("tileKey") or ""))
                               for t in row])

    # ── 查询 ──
    def at(self, col: int, row: int) -> Tile | None:
        if 0 <= row < len(self.tiles) and 0 <= col < len(self.tiles[row]):
            return self.tiles[row][col]
        return None

    def all_cells(self):
        for r, row in enumerate(self.tiles):
            for c in range(len(row)):
                yield c, r, self.tiles[r][c]

    @property
    def starts(self) -> list[tuple[int, int]]:
        return [(c, r) for c, r, t in self.all_cells() if t.is_start]

    @property
    def ends(self) -> list[tuple[int, int]]:
        return [(c, r) for c, r, t in self.all_cells() if t.is_end]

    @property
    def ground_spots(self) -> list[tuple[int, int]]:
        return [(c, r) for c, r, t in self.all_cells() if t.ground_deployable]

    @property
    def high_spots(self) -> list[tuple[int, int]]:
        return [(c, r) for c, r, t in self.all_cells() if t.high_deployable]

    # ── 投影（格 → 1280x720 像素）──
    def project(self, col: int, row: int, side: bool = False,
                height_type: int | None = None) -> tuple[int, int] | None:
        """格点中心 → 屏幕像素。公式见模块 docstring（已用真实关卡验证）。"""
        if not self.view:
            return None
        t = self.at(col, row)
        ht = t.height_type if (height_type is None and t is not None) else (height_type or 0)
        X = col - (self.width - 1) / 2.0
        Y = (self.height - 1) / 2.0 - row
        Z = ht * (-0.4)                      # 高台在屏幕上被抬高
        # 1280x720 时相机位置调整系数 t=0，直接用 view 值
        cam = self.view[1 if side else 0] if len(self.view) > (1 if side else 0) else self.view[0]
        x, y, z = X - cam[0], Y - cam[1], Z - cam[2]
        # **逐行对齐官方 TileCalc2.hpp**（2026-09-19 外援指出的 bug 候选）：
        #   官方 euler_angles_yxz: side ? (10°,30°,0°) : (0°,30°,0°)，矩阵序 = proj * matrixX(pitch) * matrixY(yaw) * translate
        #   ⚠️ 我们原来把 side 写成了"绕 X 轴预转 10°"（注释自己写着"近似"）✗ —— 那是 pitch 不是 yaw，
        #      列步因此看起来"两侧一样"，被误判成"side 分支没实现"。现在按官方矩阵严格算 ✓。
        #   正常视角 yaw=0 ⇒ 与旧实现等价（默认路径无回归 ✓）。
        yaw = math.radians(10.0 if side else 0.0)
        pitch = math.radians(30.0)
        tx, ty, tz = x, y, z
        cy_, sy_ = math.cos(yaw), math.sin(yaw)
        x1 = cy_ * tx + sy_ * tz          # matrixY：绕 Y 轴（yaw）
        z1 = -sy_ * tx + cy_ * tz
        y1 = ty
        cx_, sx_ = math.cos(pitch), math.sin(pitch)
        x2 = x1
        y2 = cx_ * y1 - sx_ * z1          # matrixX：绕 X 轴（pitch）
        z2 = -sx_ * y1 - cx_ * z1
        ratio = float(WINDOW_H) / float(WINDOW_W)
        tan_f = math.tan(math.radians(20.0))
        xc = (ratio / tan_f) * x2
        yc = (1.0 / tan_f) * y2
        w = -z2
        if abs(w) < 1e-6:
            return None
        px = (xc / w + 1.0) / 2.0 * WINDOW_W
        py = (1.0 - (yc / w + 1.0) / 2.0) * WINDOW_H
        return int(round(px)), int(round(py))

    # ── 敌人路径（BFS）──
    def enemy_path(self, prefer_end: bool = True) -> list[tuple[int, int]]:
        """从任意红门到任意蓝门的最短路（四邻接，只走地面格）。

        这是"该在哪堵"的依据：路径上的收窄处/靠蓝门处就是阻挡位。
        """
        from collections import deque
        starts = self.starts
        ends = set(self.ends)
        if not starts or not ends:
            return []
        def walk(c, r):
            t = self.at(c, r)
            return t is not None and (t.walkable or t.is_start or t.is_end)
        prev: dict[tuple[int, int], tuple[int, int] | None] = {}
        q = deque()
        for s in starts:
            prev[s] = None
            q.append(s)
        found = None
        while q:
            cur = q.popleft()
            if cur in ends:
                found = cur
                break
            c, r = cur
            for nc, nr in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if (nc, nr) in prev or not walk(nc, nr):
                    continue
                prev[(nc, nr)] = cur
                q.append((nc, nr))
        if found is None:
            return []
        path = []
        cur = found
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        return list(reversed(path))


# ── 加载 ───────────────────────────────────────────────────────────────
_cache: dict[str, Level | None] = {}


def load_level(stage: str, paths: ResourcePaths | None = None) -> Level | None:
    """按关卡代号/关名/stageId 载入格点表。

    三种都能查，因为 MAA 的 `code` 与 `stageId` 在部分主线关是**错位**的
    （实测：stageId=main_15-10 的 code 是 "15-11"），按 code 查会拿到"看起来
    对但其实不是同一文件"的风险；关名最可靠。
    """
    key = f"{stage}"
    if key in _cache:
        return _cache[key]
    paths = paths or ResourcePaths()
    d = paths.tile_pos_dir
    level: Level | None = None
    try:
        ov = json.loads((d / "overview.json").read_text(encoding="utf-8"))
    except Exception as e:
        log(f"overview.json 读取失败: {e}")
        ov = {}
    # 候选收集 + 排序：MAA 里同一关名可能有多个格点表，其中 `#s` 后缀是
    # **特殊/多阶段地图变体**（实测 main_15-10#s：红门十几个、敌人路径退化成
    # 一格，拿去当部署依据会得出"没有可堵的点"这种假结论）。优先无 `#s` 的。
    def rank(v: dict) -> tuple:
        sid = str(v.get("stageId") or "")
        return (1 if "#" in sid else 0,          # 普通图优先
                0 if v.get("name") == stage else 1,
                0 if v.get("code") == stage else 1,
                0 if v.get("stageId") == stage else 1,
                sid)

    cands = [v for v in ov.values()
             if v.get("name") == stage or v.get("code") == stage
             or v.get("stageId") == stage]
    for v in sorted(cands, key=rank):
        level = _load_file(d / v.get("filename", ""))
        if level is not None:
            break
    # 3) 文件名兜底
    if level is None:
        for f in sorted(d.glob("*.json")):
            if stage in f.name:
                level = _load_file(f)
                break
    _cache[key] = level
    return level


def _load_file(p: pathlib.Path) -> Level | None:
    try:
        return Level(json.loads(p.read_text(encoding="utf-8")), p.name)
    except Exception as e:
        log(f"格点表读取失败 {p.name}: {e}")
        return None
