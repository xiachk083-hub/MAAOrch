"""Tests for maint_ops core logic: version comparison, config migration."""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import utils as umod
import config as cmod


class TestVersionComparison:
    """Test version comparison used in batch updates."""

    def test_version_tuple_parsing(self):
        assert umod._version_tuple("v5.12.0") == (5, 12, 0)
        assert umod._version_tuple("v6.0.0-beta") == (0,)
        assert umod._version_tuple("") == (0,)

    def test_version_comparison_chain(self):
        old = umod._version_tuple("v5.12.0")
        new = umod._version_tuple("v6.0.0")
        assert old < new

    def test_version_equal(self):
        assert umod._version_tuple("v5.12.0") >= umod._version_tuple("v5.12.0")

    def test_parse_maa_version_from_path(self, tmp_path):
        d = tmp_path / "MAA-v5.13.1-win-x64" / "MAA.exe"
        d.parent.mkdir(parents=True)
        d.touch()
        assert umod.parse_maa_version(d) == "v5.13.1"

    def test_parse_maa_version_no_match(self, tmp_path):
        d = tmp_path / "some_folder" / "app.exe"
        d.parent.mkdir(parents=True)
        assert umod.parse_maa_version(d) is None


class TestConfigMigrationMaint:
    """Test config migration chains used during maintenance operations."""

    def test_v4_to_v5_account_fields(self):
        data = {"version": 4, "accounts": [{}], "groups": [], "warehouse": []}
        result = cmod.migrate_v4_to_v5(data)
        a = result["accounts"][0]
        assert a["emu_launch"] is False
        assert a["emu_wait"] == 30
        assert a["sync_tasks"] is False
        assert a["start_minimized"] is False
        assert a["adb_retry"] == 0
        assert a["stats"] == {}

    def test_v4_to_v5_warehouse_fields(self):
        data = {"version": 4, "accounts": [], "groups": [],
                "warehouse": [{"path": "MAA.exe", "args": []}]}
        result = cmod.migrate_v4_to_v5(data)
        w = result["warehouse"][0]
        assert w["guard_enabled"] is False
        assert w["guard_max_restart"] == 3
        assert w["update_channel"] == "Stable"
        assert w["guard_capture_log"] is False

    def test_full_load_chain(self, tmp_path):
        cf = tmp_path / "config.json"
        orig = {"version": 4, "accounts": [{}], "groups": [],
                "warehouse": [{"path": "notepad.exe", "args": [], "cwd": "", "env": {}}]}
        cf.write_text(json.dumps(orig), encoding="utf-8")
        old_cfg = cmod.CONFIG_FILE
        cmod.CONFIG_FILE = cf
        try:
            data = cmod.load_config()
            assert data["version"] == 5
            assert data["accounts"][0]["adb_retry"] == 0
        finally:
            cmod.CONFIG_FILE = old_cfg


class TestAdbSanitizeViaConfig:
    """Test adb_address sanitize through load_config() (calls real code)."""

    def test_broken_addr_fixed_via_load(self, tmp_path):
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps({"version": 5, "accounts": [{"adb_address": "27.0.0.1:16416"}],
                                   "groups": [], "warehouse": []}), encoding="utf-8")
        old_cfg = cmod.CONFIG_FILE
        cmod.CONFIG_FILE = cf
        try:
            data = cmod.load_config()
            assert data["accounts"][0]["adb_address"] == "127.0.0.1:16416"
        finally:
            cmod.CONFIG_FILE = old_cfg

    def test_good_addr_unchanged_via_load(self, tmp_path):
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps({"version": 5, "accounts": [{"adb_address": "127.0.0.1:7555"}],
                                   "groups": [], "warehouse": []}), encoding="utf-8")
        old_cfg = cmod.CONFIG_FILE
        cmod.CONFIG_FILE = cf
        try:
            data = cmod.load_config()
            assert data["accounts"][0]["adb_address"] == "127.0.0.1:7555"
        finally:
            cmod.CONFIG_FILE = old_cfg
