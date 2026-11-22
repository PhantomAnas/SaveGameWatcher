import os

import pytest

from savewatch import core
from savewatch.cli import main
from savewatch.config import Settings


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


def test_cli_uses_named_profile_and_restores(tmp_path, capsys):
    src, dst = tmp_path / "src", tmp_path / "dst"
    os.makedirs(src / "slot")
    (src / "slot" / "a.sav").write_text("old")
    s = Settings()
    s.add_profile("Other game", source=str(tmp_path / "x"), backup=str(tmp_path / "y"))
    s.add_profile("Hades", source=str(src), backup=str(dst))
    s.save()

    assert main(["--list-profiles"]) == 0
    assert "Hades" in capsys.readouterr().out
    assert main(["--profile", "nope", "--once"]) == 2
    assert main(["--profile", "hades", "--once"]) == 0
    version = core.list_versions(str(dst / "slot"))[0]

    (src / "slot" / "a.sav").write_text("new")
    assert main(["--profile", "Hades", "--list"]) == 0
    assert version in capsys.readouterr().out
    assert main(["--profile", "Hades", "--restore", "slot", "--version", version]) == 0
    assert (src / "slot" / "a.sav").read_text() == "old"
    assert main(["--profile", "Hades", "--undo", "slot"]) == 0
    assert (src / "slot" / "a.sav").read_text() == "new"
    assert main(["--profile", "Hades", "--restore", "missing"]) == 2
