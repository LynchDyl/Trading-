#!/usr/bin/env python3
"""Structural validator for SunnivaFollower.esp.

Walks the TES4 plugin byte-for-byte: every record header, group size, and
subrecord size must account for the whole file with nothing left over.
Prints the parsed tree so the contents can be eyeballed. Exits non-zero on
any structural inconsistency.
"""

import struct
import sys
from pathlib import Path


def parse_subrecords(data: bytes, indent: str) -> None:
    pos = 0
    while pos < len(data):
        if pos + 6 > len(data):
            sys.exit(f"FAIL: truncated subrecord header at {pos}")
        sig = data[pos:pos + 4]
        (size,) = struct.unpack_from("<H", data, pos + 4)
        payload = data[pos + 6:pos + 6 + size]
        if len(payload) != size:
            sys.exit(f"FAIL: subrecord {sig} payload truncated")
        shown = payload.hex(" ") if size <= 24 else payload[:24].hex(" ") + " ..."
        text = ""
        if sig in (b"EDID", b"FULL", b"CNAM", b"SNAM", b"MAST") and payload.endswith(b"\x00"):
            try:
                text = f"  {payload[:-1].decode('cp1252')!r}"
            except UnicodeDecodeError:
                pass
        print(f"{indent}{sig.decode()} [{size:3}] {shown}{text}")
        pos += 6 + size
    if pos != len(data):
        sys.exit("FAIL: subrecords overran record data")


def parse(buf: bytes, start: int, end: int, indent: str = "") -> None:
    pos = start
    while pos < end:
        sig = buf[pos:pos + 4]
        if sig == b"GRUP":
            (gsize,) = struct.unpack_from("<I", buf, pos + 4)
            label = buf[pos + 8:pos + 12]
            (gtype,) = struct.unpack_from("<i", buf, pos + 12)
            if pos + gsize > end:
                sys.exit(f"FAIL: group {label} overruns parent")
            print(f"{indent}GRUP label={label.decode()} type={gtype} size={gsize}")
            parse(buf, pos + 24, pos + gsize, indent + "  ")
            pos += gsize
        else:
            dsize, flags, formid, _, _, ver, _ = struct.unpack_from("<IIIHHHH", buf, pos + 4)
            if pos + 24 + dsize > end:
                sys.exit(f"FAIL: record {sig} overruns parent")
            print(f"{indent}{sig.decode()} formid={formid:08X} flags={flags:08X} "
                  f"ver={ver} dsize={dsize}")
            parse_subrecords(buf[pos + 24:pos + 24 + dsize], indent + "  ")
            pos += 24 + dsize
    if pos != end:
        sys.exit(f"FAIL: dangling bytes: stopped at {pos}, expected {end}")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("SunnivaFollower.esp")
    buf = path.read_bytes()
    parse(buf, 0, len(buf))
    print(f"OK: {path.name} parsed cleanly ({len(buf)} bytes)")
