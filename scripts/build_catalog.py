"""Build src/savewatch/data/games.json, the built-in list of games and where
they keep their saves.

    python scripts/build_catalog.py [path/to/manifest.yaml]

Save locations come from the Ludusavi manifest
(https://github.com/mtkennerly/ludusavi-manifest), which is generated from
PCGamingWiki (https://www.pcgamingwiki.com).  Without an argument the
manifest is downloaded.  Needs PyYAML (dev only; the app reads the JSON).

Paths are written with placeholders that the app fills in for the current
user at runtime: {DOCUMENTS}, {SAVEDGAMES}, {APPDATA}, {LOCALAPPDATA},
{LOCALLOW}, {PUBLIC}, {PROGRAMDATA}, {HOME}, {STEAM}, {UBISOFT}, {GAMEDIR}
(the game's Steam install folder) and * (any one folder, e.g. a user id).

Locations for cracked / repacked copies use the folder layout of their Steam
emulator, keyed by the game's Steam app id.  They are only added for games
that save through Steam Cloud (the ones whose Steam saves live in
Steam/userdata/<id>/<appid>/...); every other game writes to the same folder
whatever version it is.  Only emulators whose layout could be confirmed are
listed in EMULATORS.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "src", "savewatch", "data", "games.json")
MANIFEST_URL = ("https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/"
                "master/data/manifest.yaml")

# Display name -> [(manifest key, edition label)]
GAMES: dict[str, list[tuple[str, str]]] = {
    "The Witcher 3: Wild Hunt": [("The Witcher 3: Wild Hunt", "")],
    "Metro Exodus": [("Metro Exodus", ""), ("Metro Exodus Enhanced Edition", "Enhanced Edition")],
    "Metal Gear Solid V: The Phantom Pain": [("Metal Gear Solid V: The Phantom Pain", "")],
    "Assassin's Creed: Black Flag Resynced": [("Assassin's Creed: Black Flag Resynced", "")],
    "Final Fantasy VII Remake": [("Final Fantasy VII Remake Intergrade", "")],
    "Clair Obscur: Expedition 33": [("Clair Obscur: Expedition 33", "")],
    "Grand Theft Auto V": [("Grand Theft Auto V", "Legacy"), ("Grand Theft Auto V Enhanced", "Enhanced")],
    "Mass Effect 3": [("Mass Effect 3", "Original"), ("Mass Effect Legendary Edition", "Legendary Edition")],
    "Cyberpunk 2077": [("Cyberpunk 2077", "")],
    "Max Payne 2: The Fall of Max Payne": [("Max Payne 2: The Fall of Max Payne", "")],
    "Metal Gear Solid Delta: Snake Eater": [("Metal Gear Solid Δ: Snake Eater", "")],
    "Mafia: The Old Country": [("Mafia: The Old Country", "")],
    "Detroit: Become Human": [("Detroit: Become Human", "")],
    "Assassin's Creed Odyssey": [("Assassin's Creed Odyssey", "")],
    "Final Fantasy XVI": [("Final Fantasy XVI", "")],
    "Heavy Rain": [("Heavy Rain", "")],
    "Life is Strange": [("Life Is Strange", "Original"), ("Life Is Strange Remastered", "Remastered")],
    "Max Payne 3": [("Max Payne 3", "")],
    "Quantum Break": [("Quantum Break", "")],
    "BioShock Infinite": [("BioShock Infinite", "")],
    "007 First Light": [("007 First Light", "")],
    "Life is Strange 2": [("Life Is Strange 2", "")],
    "Final Fantasy VII Rebirth": [("Final Fantasy VII Rebirth", "")],
    "Days Gone": [("Days Gone", "")],
    "The Last of Us Part I": [("The Last of Us Part I", "")],
    "The Last of Us Part II": [("The Last of Us Part II Remastered", "Remastered")],
    "The Blood of Dawnwalker": [("The Blood of Dawnwalker", "")],
    "Red Dead Redemption 2": [("Red Dead Redemption 2", "")],
    "Ghost of Tsushima": [("Ghost of Tsushima Director's Cut", "Director's Cut")],
    "Need for Speed: Most Wanted (2005)": [("Need for Speed: Most Wanted", "")],
    "Mafia: Definitive Edition": [("Mafia: Definitive Edition", "")],
    "Uncharted: The Lost Legacy": [("Uncharted: Legacy of Thieves Collection", "Legacy of Thieves Collection")],
    "Uncharted 4: A Thief's End": [("Uncharted: Legacy of Thieves Collection", "Legacy of Thieves Collection")],
    "Tomb Raider (2013)": [("Tomb Raider (2013)", "")],
    "Hitman: Absolution": [("Hitman: Absolution", "")],
    "Deathloop": [("Deathloop", "")],
    "The Quarry": [("The Quarry", "")],
    "Until Dawn": [("Until Dawn", "")],
    "Marvel's Spider-Man 2": [("Marvel's Spider-Man 2", "")],
    "Crisis Core: Final Fantasy VII Reunion": [("Crisis Core: Final Fantasy VII Reunion", "")],
    "Assassin's Creed Unity": [("Assassin's Creed Unity", "")],
    "A Plague Tale: Requiem": [("A Plague Tale: Requiem", "")],
    "The Dark Pictures: House of Ashes": [("House of Ashes", "")],
    "Resident Evil 3": [("Resident Evil 3 (2020)", "")],
    "Dying Light": [("Dying Light", "")],
    "Resident Evil Village": [("Resident Evil Village", "")],
    "Beyond: Two Souls": [("Beyond: Two Souls", "")],
    "Marvel's Spider-Man": [("Marvel's Spider-Man Remastered", "Remastered")],
    "Horizon Zero Dawn": [("Horizon Zero Dawn", "Complete Edition"), ("Horizon Zero Dawn Remastered", "Remastered")],
    "Call of Duty: Ghosts": [("Call of Duty: Ghosts", "")],
    "Marvel's Guardians of the Galaxy": [("Marvel's Guardians of the Galaxy", "")],
    "Mass Effect 2": [("Mass Effect 2", "Original"), ("Mass Effect Legendary Edition", "Legendary Edition")],
    "Final Fantasy XV": [("Final Fantasy XV", "")],
    "Resident Evil 7: Biohazard": [("Resident Evil 7: Biohazard", "")],
    "Call of Duty: Modern Warfare 2 (2009)": [("Call of Duty: Modern Warfare 2", "")],
    "Call of Duty: Infinite Warfare": [("Call of Duty: Infinite Warfare", "")],
    "Call of Duty: Advanced Warfare": [("Call of Duty: Advanced Warfare", "")],
    "Resident Evil 4": [("Resident Evil 4 (2023)", "")],
    "Pragmata": [("Pragmata", "")],
    "The Dark Pictures: Man of Medan": [("Man of Medan", "")],
    "Resident Evil Requiem": [("Resident Evil Requiem", "")],
    "Vampyr": [("Vampyr", "")],
    "The Callisto Protocol": [("The Callisto Protocol", "")],
    "Nobody Wants to Die": [("Nobody Wants to Die", "")],
    "Resident Evil 2": [("Resident Evil 2 (2019)", "")],
    "Grand Theft Auto: San Andreas": [("Grand Theft Auto: San Andreas", "")],
    "Assassin's Creed IV: Black Flag": [("Assassin's Creed IV: Black Flag", "")],
}

# Manifest paths that are not the save folder a player wants watched.
SKIP_PATHS = {
    "<winProgramData>/Socialclub/Player",          # Social Club login data, not GTA V saves
}
# Folder to watch instead of what the manifest lists (keeps related folders together).
REPLACE_PATHS = {
    "<winDocuments>/BioWare/Mass Effect 2/Profile/Player1.prf": "<winDocuments>/BioWare/Mass Effect 2",
    "<winDocuments>/BioWare/Mass Effect 2/Save": "<winDocuments>/BioWare/Mass Effect 2",
}

# Steam emulators used by cracks and repacks: (label, folder template).
# {id} is the Steam app id, {rest} what follows "remote" in the Steam path.
EMULATORS = [
    ("Cracked: CODEX", "{PUBLIC}/Documents/Steam/CODEX/{id}/remote{rest}"),
    ("Cracked: CODEX (alternative)", "{APPDATA}/Steam/CODEX/{id}/remote{rest}"),
    ("Cracked: RUNE", "{PUBLIC}/Documents/Steam/RUNE/{id}/remote{rest}"),
    ("Cracked: EMPRESS", "{APPDATA}/EMPRESS/{id}"),
    ("Repack: Goldberg emulator (FitGirl, DODI...)", "{APPDATA}/Goldberg SteamEmu Saves/{id}/remote{rest}"),
    ("Repack: GSE emulator (FitGirl, DODI...)", "{APPDATA}/GSE Saves/{id}/remote{rest}"),
    ("Cracked: OnlineFix", "{PUBLIC}/Documents/OnlineFix/{id}/Saves{rest}"),
]
# Extra locations confirmed for one game only.
EXTRA = {
    "Grand Theft Auto V": [("Repack: Goldberg SocialClub emulator",
                            "{APPDATA}/Goldberg SocialClub Emu Saves/GTA V")],
}

STORE_LABEL = {"steam": "Steam", "epic": "Epic Games", "gog": "GOG", "uplay": "Ubisoft Connect",
               "origin": "EA app", "ea": "EA app", "any": "All versions"}
TOKENS = [
    ("<home>/Saved Games", "{SAVEDGAMES}"),
    ("<home>/AppData/Local", "{LOCALAPPDATA}"),
    ("<home>/AppData/Roaming", "{APPDATA}"),
    ("<home>/Documents", "{DOCUMENTS}"),
    ("<winDocuments>", "{DOCUMENTS}"),
    ("<winAppData>", "{APPDATA}"),
    ("<winLocalAppDataLow>", "{LOCALLOW}"),
    ("<winLocalAppData>", "{LOCALAPPDATA}"),
    ("<winProgramData>", "{PROGRAMDATA}"),
    ("<winPublic>", "{PUBLIC}"),
    ("<home>", "{HOME}"),
    ("<storeUserId>", "*"),
    ("<base>", "{GAMEDIR}"),
]
# A file name or a file glob ("*.sav", "slot*.save", "Player1.prf"), but not a
# bare "*" standing for a user-id folder.
FILE_LIKE = re.compile(r"(^.+\*.*$|^.*\*.+$|\.[A-Za-z0-9]{1,6}$)")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def to_folder(path: str) -> str:
    """Manifest paths may name files or globs; the app watches folders."""
    parts = path.split("/")
    while parts and FILE_LIKE.search(parts[-1]):
        parts.pop()
    return "/".join(parts)


def convert(path: str, stores: list[str]) -> str | None:
    if path in SKIP_PATHS:
        return None
    path = REPLACE_PATHS.get(path, path)
    if path.startswith("<root>/savegames"):
        path = path.replace("<root>", "{UBISOFT}", 1)          # Ubisoft Connect, also for Steam copies
    elif path.startswith("<root>/userdata"):
        path = path.replace("<root>", "{STEAM}", 1)
    elif "<root>" in path or "<game>" in path or "<storeGameId>" in path:
        return None
    for old, new in TOKENS:
        path = path.replace(old, new)
    return to_folder(path)


def build(manifest: dict) -> dict:
    games = []
    for name, picks in GAMES.items():
        locations: list[dict] = []
        seen: set[str] = set()
        steam_id = None
        install_dirs: list[str] = []
        cloud: list[tuple[str, str]] = []  # (app id, part after "remote")

        def add(label: str, path: str) -> None:
            key = path.lower()
            if key not in seen:
                seen.add(key)
                locations.append({"label": label, "path": path})

        for key, edition in picks:
            g = manifest[key]
            steam_id = steam_id or (g.get("steam") or {}).get("id")
            install_dirs += [d for d in (g.get("installDir") or {}) if d not in install_dirs]
            for raw, info in (g.get("files") or {}).items():
                info = info or {}
                if "save" not in (info.get("tags") or []):
                    continue
                whens = [w for w in (info.get("when") or [{}]) if w.get("os") in (None, "windows")]
                stores = sorted({w.get("store") or "any" for w in whens})
                if not whens or stores == ["microsoft"]:
                    continue  # Game Pass containers are not plain save folders
                stores = [s for s in stores if s != "microsoft"]
                path = convert(raw, stores)
                if not path:
                    continue
                if path.startswith("{GAMEDIR}") and not install_dirs:
                    continue
                if path.startswith("{GAMEDIR}") and stores == ["gog"]:
                    continue  # GOG installs are not found automatically
                m = re.match(r"\{STEAM\}/userdata/\*/(\d+)/remote(.*)$", path)
                if m:
                    cloud.append((m.group(1), m.group(2)))
                store_text = " / ".join(STORE_LABEL.get(s, s.title()) for s in stores)
                if path.startswith("{GAMEDIR}"):
                    store_text = "Game folder (Steam install)"
                add(f"{store_text}{' - ' + edition if edition else ''}", path)

        for app_id, rest in dict.fromkeys(cloud):
            for label, template in EMULATORS:
                add(label, template.format(PUBLIC="{PUBLIC}", APPDATA="{APPDATA}",
                                           id=app_id, rest=rest))
        for label, path in EXTRA.get(name, []):
            add(label, path)

        if not locations:
            raise SystemExit(f"no usable save location for {name}")
        entry = {"id": slug(name), "name": name, "steam": steam_id, "locations": locations}
        if any(loc["path"].startswith("{GAMEDIR}") for loc in locations):
            entry["install_dirs"] = install_dirs
        games.append(entry)

    games.sort(key=lambda g: re.sub(r"^(the|a)\s+", "", g["name"].lower()))
    return {
        "version": 1,
        "source": "Save locations: PCGamingWiki via the Ludusavi manifest; "
                  "Steam emulator layouts: emulator docs and community reports.",
        "games": games,
    }


def main() -> None:
    if len(sys.argv) > 1:
        src = sys.argv[1]
    else:
        src = os.path.join(ROOT, "build", "manifest.yaml")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        print("downloading the Ludusavi manifest...")
        urllib.request.urlretrieve(MANIFEST_URL, src)
    with open(src, encoding="utf-8") as fh:
        manifest = yaml.load(fh, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    catalog = build(manifest)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(catalog, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {len(catalog['games'])} games to {OUT}")


if __name__ == "__main__":
    main()
