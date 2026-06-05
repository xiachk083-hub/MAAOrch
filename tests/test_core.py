"""Tests for MAAOrch core functions"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from batch_launcher import load_config, save_config, make_id, parse_maa_version, _version_tuple, get_platform_key


class TestConfig:
    def test_make_id(self):
        assert len(make_id()) == 8
        assert make_id() != make_id()  # unique each call

    def test_parse_maa_version(self, tmp_path):
        # Function looks at parent dir name
        d = tmp_path / "MAA-v5.12.0" / "sub"
        d.mkdir(parents=True)
        assert parse_maa_version(d) == "v5.12.0"
        d2 = tmp_path / "v6.11.1" / "sub"
        d2.mkdir(parents=True)
        assert parse_maa_version(d2) == "v6.11.1"
        assert parse_maa_version(tmp_path) is None

    def test_version_tuple(self):
        assert _version_tuple("v5.12.0") == (5, 12, 0)
        assert _version_tuple("6.11.1") == (6, 11, 1)
        assert _version_tuple("") == (0,)
        assert _version_tuple("invalid") == (0,)
        assert _version_tuple("1.2.3-beta") == (0,)  # pre-release not supported

    def test_get_platform_key(self):
        k = get_platform_key()
        assert isinstance(k, str)
        assert k in ("win-x64", "win-arm64")

    def test_load_save_config(self, tmp_path):
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps({"version": 5, "accounts": [], "groups": [], "warehouse": []}))
        # Patch CONFIG_FILE
        import batch_launcher as bl
        orig = bl.CONFIG_FILE
        bl.CONFIG_FILE = cf
        try:
            data = load_config()
            assert data is not None
            assert data["version"] == 5
            data["test"] = 1
            save_config(data)
            loaded = load_config()
            assert loaded["test"] == 1
        finally:
            bl.CONFIG_FILE = orig
