"""Tests for emu_ops: emulator detection, ADB address parsing, port formulas."""
import sys, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from task_constants import EMU_PRESETS, MUMU_INSTANCE_DIRS, find_mumu_cli


class TestAdbDeviceParsing:
    """Test ADB 'devices' output parsing logic used in EmuService.scan()"""

    DEVICE_PATTERN = re.compile(rb':(\d+)\s+(\S+)')

    def test_parse_single_device(self):
        raw = b"127.0.0.1:16384\tdevice\n"
        results = []
        for m in self.DEVICE_PATTERN.finditer(raw):
            results.append(("127.0.0.1:" + m.group(1).decode("ascii"), m.group(2) == b"device"))
        assert results == [("127.0.0.1:16384", True)]

    def test_parse_multiple_devices(self):
        raw = b"127.0.0.1:16384\tdevice\n127.0.0.1:16416\tdevice\n127.0.0.1:7555\toffline\n"
        results = []
        for m in self.DEVICE_PATTERN.finditer(raw):
            results.append(("127.0.0.1:" + m.group(1).decode("ascii"), m.group(2) == b"device"))
        assert ("127.0.0.1:16384", True) in results
        assert ("127.0.0.1:16416", True) in results
        assert ("127.0.0.1:7555", False) in results

    def test_parse_garbled_output(self):
        raw = b"\x00\x00127.0.0.1:5555\tdevice\r\n"
        for m in self.DEVICE_PATTERN.finditer(raw):
            addr = "127.0.0.1:" + m.group(1).decode("ascii")
            assert addr == "127.0.0.1:5555"

    def test_parse_empty_output(self):
        raw = b"List of devices attached\n\n"
        results = list(self.DEVICE_PATTERN.finditer(raw))
        assert len(results) == 0

    def test_parse_unauthorized_device(self):
        raw = b"127.0.0.1:16384\tunauthorized\n"
        results = []
        for m in self.DEVICE_PATTERN.finditer(raw):
            results.append((m.group(2).decode(), m.group(2) in (b"device", b"unauthorized", b"offline")))
        assert results == [("unauthorized", True)]


class TestEmuPresets:
    """Test emulator preset definitions."""

    def test_all_presets_have_names(self):
        for ep in EMU_PRESETS:
            assert ep["name"], f"Missing name in preset: {ep}"
            assert ep["type"], f"Missing type in preset: {ep}"
            assert ep["ports"], f"Missing ports in preset: {ep}"

    def test_mumu12_port_formula(self):
        """MuMu 12 uses port = 16384 + index * 32"""
        for i in range(10):
            port = 16384 + i * 32
            assert str(port) in EMU_PRESETS[0]["ports"]

    def test_mumu6_default_port(self):
        assert "7555" in EMU_PRESETS[1]["ports"]

    def test_ldplayer_default_port(self):
        assert "5555" in EMU_PRESETS[3]["ports"]


class TestFindMumuCli:
    """Test mumu-cli detection (no mocking — checks path existence)."""

    def test_returns_string_or_none(self):
        result = find_mumu_cli()
        assert result is None or isinstance(result, str)

    def test_known_candidates_exist_or_are_valid(self):
        """Ensure candidate paths are valid absolute paths."""
        from task_constants import MUMU_CLI_CANDIDATES
        for c in MUMU_CLI_CANDIDATES:
            assert Path(c).is_absolute() or c.startswith("C:") or c.startswith("D:")


class TestMuMuInstanceDirs:
    """Test MuMu instance directory candidates."""

    def test_dirs_are_absolute(self):
        for d in MUMU_INSTANCE_DIRS:
            assert d.is_absolute()
