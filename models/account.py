"""Account data model — typed wrapper with backward-compatible dict access."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Account:
    id: str = ""
    name: str = "未命名"
    game_client: str = "Official"
    adb_path: str = ""
    adb_address: str = ""
    connection_preset: str = ""
    touch_mode: str = "MiniTouch"
    account_switch: str = ""
    emu_instance_index: str = ""
    emu_launch: bool = True
    emu_wait: int = 30
    adb_retry: int = 0
    start_minimized: bool = True
    start_directly: bool = True
    post_action: str = "ExitEmulator,ExitSelf"
    fight_stage: str = ""
    task_pipeline: str = ""
    task_settings: dict = field(default_factory=dict)
    sync_tasks: bool = False
    stats: dict = field(default_factory=dict)
    loop_enabled: bool = False
    loop_interval: int = 5
    loop_max_rounds: int = 10
    min_sanity: int = 0
    stuck_timeout_min: int = 10
    tags: str = ""
    round_robin_deficit: int = 0
    stamina_threshold_pct: int = 80

    # Smart scheduling fields
    smart_stage: str = ""
    smart_annihilation: str = ""
    smart_annihilation_enabled: bool = True
    smart_mon: str = ""
    smart_tue: str = ""
    smart_wed: str = ""
    smart_thu: str = ""
    smart_fri: str = ""
    smart_sat: str = ""
    smart_sun: str = ""
    smart_materials_enabled: bool = True
    smart_pending: bool = False
    smart_last_error: float = 0.0
    smart_plan: str = ""

    # ---- Backward-compatible dict access ----
    def __getitem__(self, key: str):
        return getattr(self, key)

    def __setitem__(self, key: str, value) -> None:
        if hasattr(self, key):
            setattr(self, key, value)

    def get(self, key: str, default=None):
        return getattr(self, key, default) if hasattr(self, key) else default

    def setdefault(self, key: str, default=None):
        if not hasattr(self, key):
            setattr(self, key, default)
        return getattr(self, key, default)

    def update(self, d: dict) -> None:
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, v)

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    _TRANSIENT = {"smart_pending", "smart_last_error", "smart_plan"}

    def to_dict(self) -> dict:
        result = {k: getattr(self, k) for k in self.__dataclass_fields__ if k not in self._TRANSIENT}
        # Preserve any extra keys set dynamically (not in dataclass fields)
        for k in self.__dict__:
            if k not in self.__dataclass_fields__ and k not in self._TRANSIENT and not k.startswith("_"):
                result[k] = self.__dict__[k]
        return result
