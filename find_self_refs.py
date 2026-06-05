for fn in ["emu_ops.py", "config_ops.py", "log_ops.py", "maint_ops.py"]:
    lines = open(fn, encoding="utf-8").readlines()
    for i, l in enumerate(lines):
        s = l.strip()
        if "hasattr(self," in s and "hasattr(self.mw" not in s:
            print(f"{fn}:{i+1}: hasattr(self,...) -> {s[:80]}")
        if "getattr(self," in s and "getattr(self.mw" not in s:
            print(f"{fn}:{i+1}: getattr(self,...) -> {s[:80]}")
