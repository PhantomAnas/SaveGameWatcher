"""Built-in game list, placeholder filling, detection and search by name."""

from __future__ import annotations

import os
import re

import pytest

from savewatch import catalog
from savewatch.catalog import Game, Location

ALLOWED = {"DOCUMENTS", "SAVEDGAMES", "APPDATA", "LOCALAPPDATA", "LOCALLOW", "PUBLIC",
           "PROGRAMDATA", "HOME", "STEAM", "UBISOFT", "GAMEDIR"}


def _touch(path, text="x") -> None:
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def pc(tmp_path):
    """A fake user profile: placeholder -> folder under tmp_path."""
    folders = {name: str(tmp_path / name.lower()) for name in ALLOWED - {"GAMEDIR"}}
    for path in folders.values():
        os.makedirs(path, exist_ok=True)
    return folders


# -------------------------------------------------------------- the list

def test_bundled_list_is_complete_and_portable():
    games = catalog.load_catalog()
    assert len(games) >= 60
    ids = [g.id for g in games]
    assert len(ids) == len(set(ids))
    for g in games:
        assert g.name and g.locations, g.id
        for loc in g.locations:
            assert set(catalog.TOKEN_RE.findall(loc.path)) <= ALLOWED, loc.path
            # Nothing tied to one PC: no drive letters, user names or manifest tokens.
            assert not re.search(r"^[A-Za-z]:|<|>|\\", loc.path), loc.path
            assert loc.path.startswith("{"), loc.path
        if any("{GAMEDIR}" in loc.path for loc in g.locations):
            assert g.install_dirs, g.id


def test_witcher_3_is_listed_with_its_real_folder():
    g = catalog.find_game("the-witcher-3-wild-hunt")
    assert g is not None and g.steam == 292030
    assert any(loc.path == "{DOCUMENTS}/The Witcher 3/gamesaves" for loc in g.locations)


def test_steam_cloud_games_list_cracked_locations_and_others_do_not():
    re4 = catalog.find_game("resident-evil-4")
    labels = [loc.label for loc in re4.locations]
    assert any(lbl.startswith("Cracked: CODEX") for lbl in labels)
    assert any("{APPDATA}/GSE Saves/2050650/remote/win64_save" == loc.path for loc in re4.locations)
    witcher = catalog.find_game("the-witcher-3-wild-hunt")
    assert not any(loc.label.startswith(("Cracked", "Repack")) for loc in witcher.locations)


def test_search_matches_words_in_any_order():
    names = [g.name for g in catalog.search("evil res")]
    assert "Resident Evil 4" in names and "Resident Evil Village" in names
    assert [g.name for g in catalog.search("witcher")] == ["The Witcher 3: Wild Hunt"]
    assert catalog.search("assassins black flag")
    assert catalog.search("zzz not a game") == []


# ------------------------------------------------------------- expanding

def test_fill_and_missing_placeholder(pc):
    assert catalog.fill("{DOCUMENTS}/My Games/X", pc) == os.path.normpath(pc["DOCUMENTS"] + "/My Games/X")
    assert catalog.fill("{GAMEDIR}/players2", pc) is None
    assert catalog.fill("{NOPE}/x", pc) is None


def test_expand_wildcards_files_and_brackets(pc):
    docs = pc["DOCUMENTS"]
    _touch(os.path.join(docs, "Rockstar Games", "GTA V", "Profiles", "AB12", "SGTA50000"))
    _touch(os.path.join(docs, "Rockstar Games", "GTA V", "Profiles", "CD34", "SGTA50001"))
    _touch(os.path.join(docs, "Odd [Game]", "save.dat"))
    game = Game("g", "G", None, ())
    profiles = catalog.expand(game, Location("x", "{DOCUMENTS}/Rockstar Games/GTA V/Profiles/*"), pc)
    assert [os.path.basename(p) for p in profiles] == ["AB12", "CD34"]
    # Brackets in a folder name are literal, not a glob character class.
    assert catalog.expand(game, Location("x", "{DOCUMENTS}/Odd [Game]/*"), pc) == []
    assert catalog.expand(game, Location("x", "{DOCUMENTS}/Odd [Game]"), pc) == [
        os.path.join(docs, "Odd [Game]")]
    # A location that names a file means its folder.
    assert catalog.expand(game, Location("x", "{DOCUMENTS}/Odd [Game]/save.dat"), pc) == [
        os.path.join(docs, "Odd [Game]")]


def test_gamedir_uses_every_steam_library(pc, tmp_path):
    steam = pc["STEAM"]
    other = tmp_path / "D_SteamLibrary"
    os.makedirs(os.path.join(steam, "steamapps"))
    with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), "w") as fh:
        fh.write('"libraryfolders"\n{\n "1"\n {\n  "path"\t\t"%s"\n }\n}\n'
                 % str(other).replace("\\", "\\\\"))
    _touch(other / "steamapps" / "common" / "Call of Duty Ghosts" / "players2" / "save.svg")
    game = Game("cod", "CoD", 1, (Location("Game folder", "{GAMEDIR}/players2"),),
                ("Call of Duty Ghosts",))
    assert catalog.steam_libraries(steam) == [steam, os.path.normpath(str(other))]
    found = [c for c in catalog.detect(game, pc) if c.exists]
    assert len(found) == 1 and found[0].path.endswith(os.path.join("Call of Duty Ghosts", "players2"))


def test_detect_puts_found_locations_first_newest_on_top(pc):
    old = os.path.join(pc["DOCUMENTS"], "Old", "a.sav")
    new = os.path.join(pc["APPDATA"], "New", "b.sav")
    _touch(old)
    _touch(new)
    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.makedirs(os.path.join(pc["LOCALAPPDATA"], "Empty"))
    game = Game("g", "G", None, (
        Location("Missing", "{SAVEDGAMES}/Nope"),
        Location("Empty", "{LOCALAPPDATA}/Empty"),
        Location("Old", "{DOCUMENTS}/Old"),
        Location("New", "{APPDATA}/New"),
        Location("Wild", "{DOCUMENTS}/Nothing/*"),
    ))
    result = catalog.detect(game, pc)
    assert [c.label for c in result] == ["New", "Old", "Missing", "Empty", "Wild"]
    assert result[0].exists and result[0].files == 1 and result[0].newest is not None
    assert not result[3].exists  # a folder with no files is not a find


# -------------------------------------------------------- search by name

def test_name_pattern():
    p = catalog.name_pattern("The Witcher 3: Wild Hunt")
    for folder in ("The Witcher 3", "TheWitcher3", "witcher_3", "The.Witcher.3"):
        assert p.search(folder), folder
    assert not p.search("Witcher 2")
    assert catalog.name_pattern("Mafia: Definitive Edition").search("Mafia")
    assert catalog.name_pattern("") is None
    assert catalog.name_pattern("Q") is None


def test_search_by_name_finds_save_folders(pc):
    _touch(os.path.join(pc["DOCUMENTS"], "My Games", "Hollow Knight Silksong", "Saves", "user1.dat"))
    _touch(os.path.join(pc["LOCALLOW"], "Team Cherry", "HollowKnight", "user2.dat"))
    os.makedirs(os.path.join(pc["APPDATA"], "Hollow Knight empty"))
    _touch(os.path.join(pc["APPDATA"], "Some Other Game", "x.sav"))
    hits = catalog.search_by_name("Hollow Knight", pc)
    paths = sorted(os.path.relpath(c.path, os.path.dirname(pc["HOME"])) for c in hits)
    assert paths == [
        os.path.join("documents", "My Games", "Hollow Knight Silksong", "Saves"),  # save subfolder preferred
        os.path.join("locallow", "Team Cherry", "HollowKnight"),
    ]
    assert all(c.label == "Found by name" and c.exists for c in hits)


def test_cached_cover_without_download(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "cover_cache_dir", lambda: str(tmp_path))
    assert catalog.cached_cover(None) is None
    assert catalog.cached_cover(292030) is None
    (tmp_path / "292030.jpg").write_bytes(b"\xff\xd8\xff data")
    assert catalog.cached_cover(292030) == str(tmp_path / "292030.jpg")
    assert catalog.fetch_cover(None) is None
