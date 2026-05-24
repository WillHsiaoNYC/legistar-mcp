import json

from legistar_mcp import __version__

def test_version_is_set():
    assert __version__ == "0.2.0"

def test_fixture_int_0153_loads(bills_dir):
    with open(bills_dir / "int_0153_2022.json") as f:
        b = json.load(f)
    assert b["File"] == "Int 0153-2022"
    assert "office of operations" in b["Text"].lower()
