import os

import pytest

from savewatch.cli import main


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Never read or write the real user settings file during tests."""
    monkeypatch.setattr("savewatch.config.config_path", lambda: str(tmp_path / "cfg" / "settings.json"))


def test_cli_once_backs_up(tmp_path, capsys):
    src = tmp_path / "src"
    os.makedirs(src / "slot")
    (src / "slot" / "a.sav").write_text("x")
    dst = tmp_path / "dst"
    rc = main(["--source", str(src), "--backup", str(dst), "--once", "--keep", "2"])
    assert rc == 0
    assert os.path.isdir(dst / "slot")
    assert "1 backed up" in capsys.readouterr().out


def test_cli_rejects_backup_inside_source(tmp_path):
    src = tmp_path / "src"
    os.makedirs(src)
    assert main(["--source", str(src), "--backup", str(src / "bk"), "--once"]) == 2


def test_cli_rejects_missing_source(tmp_path):
    assert main(["--source", str(tmp_path / "nope"), "--backup", str(tmp_path / "bk"), "--once"]) == 2
