#!/usr/bin/env python3
"""Generate SunnivaFollower.esp — a Skyrim SE plugin that adds Sunniva,
a custom Nord follower, without needing the Creation Kit.

The plugin is a standard TES4/SSE binary (form version 44), ESL-flagged so it
does not consume a load-order slot. It contains three records:

  NPC_  SUNV_Sunniva          the follower herself
  OTFT  SUNV_SunnivaOutfit    her outfit (Tavern Clothes + Fine Boots)
  RELA  SUNV_SunnivaPlayerAlly player-ally relationship (needed to recruit)

Her appearance/stats are templated from Jordis the Sword-Maiden (Skyrim.esm
0x000A2C8F) via the Traits/Stats/SpellList template flags. Templating traits
means the game reuses Jordis's shipped FaceGen data, which avoids the
"grey face" bug that hits ESP-only NPCs with hand-built faces — and it means
any NPC appearance overhaul in your load order applies to Sunniva too.

Vanilla FormIDs referenced (all Skyrim.esm):
  0x000A2C8F  Jordis the Sword-Maiden (NPC_, appearance/stats template)
  0x00013746  NordRace
  0x00013176  CombatWarrior1H (class)
  0x0005C84D  PotentialFollowerFaction
  0x0005C84E  CurrentFollowerFaction (rank -1)
  0x000D191F  Tavern Clothes
  0x00086993  Fine Boots
  0x00000007  Player

Run:  python3 generate_esp.py   ->  writes SunnivaFollower.esp next to it.
"""

import struct
from pathlib import Path

FORM_VERSION = 44          # Skyrim SE
ESL_FLAG = 0x00000200      # TES4 header flag: light plugin

# FormIDs of the new records. One master (Skyrim.esm) -> mod index 0x01 in
# this file. Object IDs stay in 0x800..0xFFF so the ESL flag is legal.
FID_NPC = 0x01000800
FID_OUTFIT = 0x01000801
FID_RELA = 0x01000802

# Vanilla Skyrim.esm FormIDs
JORDIS = 0x000A2C8F
NORD_RACE = 0x00013746
CLASS_WARRIOR_1H = 0x00013176
FACTION_POTENTIAL_FOLLOWER = 0x0005C84D
FACTION_CURRENT_FOLLOWER = 0x0005C84E
TAVERN_CLOTHES = 0x000D191F
FINE_BOOTS = 0x00086993
PLAYER = 0x00000007


def sub(sig: bytes, data: bytes) -> bytes:
    """A subrecord: 4-byte signature + uint16 size + payload."""
    return sig + struct.pack("<H", len(data)) + data


def zstr(s: str) -> bytes:
    return s.encode("cp1252") + b"\x00"


def record(sig: bytes, formid: int, data: bytes, flags: int = 0) -> bytes:
    header = struct.pack("<4sIIIHHHH", sig, len(data), flags, formid,
                         0, 0, FORM_VERSION, 0)
    return header + data


def group(label: bytes, records: bytes) -> bytes:
    header = struct.pack("<4sI4siHHHH", b"GRUP", 24 + len(records), label,
                         0, 0, 0, FORM_VERSION, 0)
    return header + records


def tes4(num_records: int) -> bytes:
    data = b"".join([
        sub(b"HEDR", struct.pack("<fiI", 1.7, num_records, 0x00000803)),
        sub(b"CNAM", zstr("Dylan")),
        sub(b"SNAM", zstr("Adds Sunniva, a custom Nord follower.")),
        sub(b"MAST", zstr("Skyrim.esm")),
        sub(b"DATA", struct.pack("<Q", 0)),
    ])
    return record(b"TES4", 0, data, flags=ESL_FLAG)


def npc_sunniva() -> bytes:
    acbs = struct.pack(
        "<IhhHHHhhHhh",
        0x23,    # flags: Female | Essential | Unique
        0, 0,    # magicka / stamina offsets
        10,      # level
        0, 0,    # calc min/max
        100,     # speed mult
        35,      # disposition base
        0x0B,    # template flags: UseTraits | UseStats | UseSpellList
        0, 0,    # health offset, bleedout override
    )
    aidt = struct.pack(
        "<8BIII",
        0,   # aggression: unaggressive
        3,   # confidence: brave
        50,  # energy
        0,   # morality: any crime (follows player commands)
        0,   # mood: neutral
        2,   # assistance: helps friends and allies
        0, 0,      # aggro radius behavior off, unused
        0, 0, 0,   # warn / warn-attack / attack radii (unused)
    )
    # Skills are placeholders — UseStats templates real stats from Jordis.
    skills = bytes([35, 20, 25, 30, 15, 25, 30, 15, 15, 20,
                    15, 20, 15, 15, 15, 15, 20, 15])
    dnam = (skills + bytes(18)                       # skill offsets
            + struct.pack("<HHHH", 150, 50, 120, 0)  # health/magicka/stamina
            + struct.pack("<f", 0.0)                 # far-away model distance
            + struct.pack("<B3x", 1))                # geared-up weapons

    data = b"".join([
        sub(b"EDID", zstr("SUNV_Sunniva")),
        sub(b"OBND", bytes(12)),
        sub(b"ACBS", acbs),
        sub(b"SNAM", struct.pack("<IB3s", FACTION_POTENTIAL_FOLLOWER, 0, b"\x00\x00\x00")),
        sub(b"SNAM", struct.pack("<IB3s", FACTION_CURRENT_FOLLOWER, 0xFF, b"\x00\x00\x00")),
        sub(b"VTCK", struct.pack("<I", 0)),      # voice comes from template
        sub(b"TPLT", struct.pack("<I", JORDIS)),
        sub(b"RNAM", struct.pack("<I", NORD_RACE)),
        sub(b"AIDT", aidt),
        sub(b"CNAM", struct.pack("<I", CLASS_WARRIOR_1H)),
        sub(b"FULL", zstr("Sunniva")),
        sub(b"DATA", b""),
        sub(b"DNAM", dnam),
        sub(b"NAM5", struct.pack("<H", 255)),
        sub(b"NAM6", struct.pack("<f", 1.0)),    # height
        sub(b"NAM7", struct.pack("<f", 40.0)),   # weight (slim)
        sub(b"NAM8", struct.pack("<I", 1)),      # sound level: normal
        sub(b"DOFT", struct.pack("<I", FID_OUTFIT)),
    ])
    return record(b"NPC_", FID_NPC, data)


def outfit() -> bytes:
    data = b"".join([
        sub(b"EDID", zstr("SUNV_SunnivaOutfit")),
        sub(b"INAM", struct.pack("<II", TAVERN_CLOTHES, FINE_BOOTS)),
    ])
    return record(b"OTFT", FID_OUTFIT, data)


def player_ally() -> bytes:
    data = b"".join([
        sub(b"EDID", zstr("SUNV_SunnivaPlayerAlly")),
        sub(b"DATA", struct.pack("<IIHBBI", FID_NPC, PLAYER,
                                 1,      # rank: Ally
                                 0, 0,   # unknown, flags
                                 0)),    # association type: none
    ])
    return record(b"RELA", FID_RELA, data)


def build() -> bytes:
    groups = b"".join([
        group(b"NPC_", npc_sunniva()),
        group(b"OTFT", outfit()),
        group(b"RELA", player_ally()),
    ])
    return tes4(num_records=6) + groups  # 3 records + 3 groups


if __name__ == "__main__":
    out = Path(__file__).with_name("SunnivaFollower.esp")
    out.write_bytes(build())
    print(f"wrote {out} ({out.stat().st_size} bytes)")
