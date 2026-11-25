#!/usr/bin/env python3
"""
gizmo.py - extract graphics from the game "Normality" (Gremlin, 1996).

Put this script into a folder that contains the game's .MGL and/or .RAW files and run it:

    python gizmo.py

It looks at every *.MGL and *.RAW file lying next to the script and writes:

  gifs/<PACK>/<TEXTURE>.gif            texture packs (DASP): one GIF per texture, animated textures stay
  gifs/<PACK>/sprites/<SPRITE>.gif     animated; index 0 is transparent for sprites and stencil textures
  models/<PACK>/<NAME>.obj             3D models stored inside the packs (SOFA, BPIPE, ...) as Wavefront OBJ with
  models/<PACK>/<NAME>.mtl, *.png      a material file and the PNG textures they use
  gifs/<NAME>.gif                      full-screen pictures (papers, menus, manuals: ASHES, REQ, ...)
  gifs/ICONS.gif, gifs/ICONS/NNN.gif   the inventory icon sheet and every icon separately
  gifs/<FONT>.gif                      bitmap fonts: one sheet with all glyphs
  maps/<NAME>.png                      floor plans of level maps (.RAW): textured floors, walls, steps

For a level map the texture pack is looked up next to the map as a .DAS or .MGL file. Several maps share one
pack (see MAP_PACKS below), so keep the pack next to the maps that use it.

Requirements: Python 3.7+ and Pillow (pip install pillow).

Author: hohmalow

Credits and sources
  * The development of this script is based on the earlier work of Denzquix
    (https://github.com/radishengine/denormalize) and msikma (https://github.com/msikma/denormalize).
    MGL compression and the DAS texture pack format follow the reverse engineering by Denzquix, first
    published in 2012 on the Salt World forums. This script is an independent implementation in Python and
    contains no code from those projects.
  * Everything about .RAW maps, full-screen pictures, icons and fonts was worked out from the files
    themselves and from the disassembly of the game executable (NORM.EXE).
"""
import colorsys
import math
import re
import struct
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError:
    sys.exit("Pillow is not installed. Install it with:  pip install pillow")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


# ==========================================================================
# Settings
# ==========================================================================

# Full-screen pictures, icons and fonts store 15-bit palette entries with BLUE in the high bits (this makes
# the palettes of REQ and BDMANLO match the DAS texture palette). Set to False if the colours look swapped.
SWAP_RED_BLUE = True

# Size of the longest side of a rendered map, in pixels. Maps with more sectors than LARGE_MAP_SECTORS
# (for example the outdoor locations) use the larger size.
MAP_MAX_SIDE = 1800
MAP_MAX_SIDE_LARGE = 3200
LARGE_MAP_SECTORS = 250

# The map frame is given by the vertices between MAP_CROP_PERCENT and 1 - MAP_CROP_PERCENT, so that far-away
# backdrop sectors (huge outer polygons) do not squeeze the interesting part into a corner.
MAP_CROP_PERCENT = 0.02

# Floor textures are stretched over a square of P map units, with P = 2^(7 + scale) * min(1, width / this).
# Textures that are 128 pixels wide or more get the full period; narrower ones get a proportionally shorter
# one (verified in the game: the 32 px bed cover at scale 1 repeats every 64 units).
FULL_PERIOD_MIN_WIDTH = 128

# Write the sector number in the middle of every sector on the map.
DRAW_SECTOR_NUMBERS = False

# 3D models: the game has no texture coordinates in its model data; textures simply tile over the faces. The
# exported OBJ files use a box projection aligned with the model axes (the same for every face, so the
# pattern continues across edges) with this many texels per model unit. 1.0 was estimated from game
# screenshots (the exact rule of the game's renderer is not decoded).
MODEL_TEXELS_PER_UNIT = 1.0


# ==========================================================================
# Data tables
# ==========================================================================

# Approximate duration of one animation slot in milliseconds, by the animation speed byte of a texture.
SPEED_MS = {0x02: 70, 0x03: 60, 0x04: 100, 0x06: 130, 0x07: 170,
            0x08: 190, 0x0A: 210, 0x0E: 270, 0x10: 60}

# Full-screen pictures: size of the pixel data (without the 512 byte palette) -> (width, height).
# 320x200 (the "...LO" files) and 640x400 are verified on real files; the other two are a guess based on
# the standard video modes.
SCREEN_SIZES = {64000: (320, 200), 256000: (640, 400),
                76800: (320, 240), 307200: (640, 480)}

# Files that are a vertical stack of equally sized cells: file name -> (cell width, cell height).
SPRITE_SHEETS = {"ICONS": (32, 32)}

# Glyph cell size of the bitmap fonts, by the size of the decompressed data (verified on the real files).
# Other sizes are guessed automatically.
KNOWN_FONTS = {26208: (9, 13), 56000: (14, 20), 82320: (21, 20)}

# Which texture pack every level map uses. NORM.EXE keeps two parallel tables (map names and pack names under
# the same index), so several maps share one pack (FACTOUT1 -> FACTOUT, CELLS2..6 -> CELLS, YELOMALL ->
# MINTMALL). A map that is not listed here uses the pack with its own name.
MAP_PACKS = {
    "EUREKA0": "EUREKA0", "EUREKA1": "EUREKA1", "DOCKS": "DOCKS", "WINBOX": "WINBOX", "SEWER": "SEWER",
    "LANDING": "LANDING", "FACRECP": "FACRECP", "BLOWNUP": "BLOWNUP", "YELOMALL": "MINTMALL",
    "FACTORY": "FACTORY", "FACTOUT": "FACTOUT", "TURBINE": "TURBINE", "ABOVETV": "ABOVETV",
    "TRUCKTV": "TRUCKTV", "MINTMALL": "MINTMALL", "DEN": "DEN", "FILTER": "FILTER", "DUCT": "DUCT",
    "OUTPOST": "OUTPOST", "LABS": "LABS", "FACTOUT1": "FACTOUT", "PROCESS": "PROCESS", "CELLS": "CELLS",
    "CELLS2": "CELLS", "CELLS3": "CELLS", "CELLS4": "CELLS", "CELLS5": "CELLS", "CELLS6": "CELLS",
    "STADIUM": "STADIUM", "MISSILE": "MISSILE",
}

# The common interface palette: 256 little-endian 15-bit entries (hex, 4 digits each). It is the part of the
# palettes of REQ and BDMANLO that is identical (223 of 256 entries), with the last entry set to white.
# Icons and fonts contain only palette indices, and the game draws them with these colours.
UI_PALETTE_HEX = (
    "00007FFF77BD6F7B63185AD6529446313DEF35AD2D6B210818C6108404210000"
    "7FFF6B5F5ADF463F359F211F107F0C5C085904350412000F000C000900050002"
    "62FF5ABE527C4A3B41F939B83596293521131CD214900C6F084D042C000B0009"
    "7FDC7F987F557F127EAF7E6C7E097DA67144650358A24C614040342028001C00"
    "0B5F0B1F06DE069E067E023D01FD01DD019D017B015A0138011700F500D400D3"
    "6BFD53B83F542B101AAD0E69020601E501C401A30182016101410120010000E0"
    "7F9F7F3F7AFF769F6E3F69FF5D9C553A4CF740B43871304F282C200914060C04"
    "7FFD77B96F7567325ACF528D4A4A462941E73DA6398535432D2228E224C120A0"
    "7F9F775D6F1C66DA5EB8567652354A1341B23D70352F310D2CCC288A24692048"
    "77FE6BBC5F7957174AD542933A51320F25CD1DAA15680D46090404E300C10080"
    "7BDF739F6B5F631F5ADF527F4E3F45FF3D9C353A30F828B6247420321C10180E"
    "73BE675C5F1B56D94A9742753A342DF225B01D6E152D0D0B08C900A800660044"
    "635F4EFE3E9C2E3B21F915B809960155013300F100CF00AD008B006900470025"
    "7FDE777C73396AF766B55E935A71520E49CB45883D46390430E22CA124802060"
    "4673425242513E303E0F39EE35CD31CD31AC2D8B2D6A296A2549252821081D07"
    "1CE618C514A410830C6353BF3BBF0FBF0B5F0AFF069F065F021E01DD019D7FFF"
)


# ==========================================================================
# MGL: the compression used by every game file
# ==========================================================================

def mgl_decompress(src: bytes) -> bytes:
    """Decompress an MGL stream (a small LZ77 variant with literal runs and arithmetic progressions).

    Opcode (high nibble of the first byte):
      0x00        end of data
      0x01-0x3F   copy that many literal bytes
      0x4_        arithmetic progression of bytes, 3..18 values, step taken from the last two bytes
      0x5_        arithmetic progression of 16-bit words, 2..17 values
      0x6_        repeat the last byte 3..18 times
      0x7_        repeat the last two bytes 2..17 times
      0x8_-0xB_   copy 3 bytes from 3..66 bytes back
      0xC_-0xD_   copy 4..11 bytes from 3..1026 bytes back (one extra byte)
      0xE_-0xF_   copy 5..260 bytes from 3..8194 bytes back (two extra bytes)
    """
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        b = src[i]
        i += 1
        hi = b >> 4
        if hi <= 3:                      # 0x00 = end, 0x01..0x3F = literal run
            if b == 0:
                break
            if i + b > n:
                raise ValueError("truncated literal run")
            out += src[i:i + b]
            i += b
            continue
        if hi == 4:                      # arithmetic progression of bytes
            count = 3 + (b & 0xF)
            if len(out) < 2:
                raise ValueError("progression at the very start of the data")
            state = out[-1]
            step = state - out[-2]
            for _ in range(count):
                state = (state + step) & 0xFF
                out.append(state)
            continue
        if hi == 5:                      # arithmetic progression of words
            count = 2 + (b & 0xF)
            if len(out) < 4:
                raise ValueError("progression at the very start of the data")
            state = out[-2] | (out[-1] << 8)
            step = state - (out[-4] | (out[-3] << 8))
            for _ in range(count):
                state = (state + step) & 0xFFFF
                out += struct.pack("<H", state)
            continue
        if hi == 6:
            offset, total = 1, 3 + (b & 0xF)
        elif hi == 7:
            offset, total = 2, 2 * (2 + (b & 0xF))
        elif hi <= 0xB:
            offset, total = 3 + (b & 0x3F), 3
        elif hi <= 0xD:
            offset = 3 + (((b & 3) << 8) | src[i])
            i += 1
            total = 4 + ((b >> 2) & 7)
        else:
            offset = 3 + (((b & 0x1F) << 8) | src[i])
            total = 5 + src[i + 1]
            i += 2
        if offset > len(out):
            raise ValueError("back-reference before the start of the data")
        if offset >= total:
            start = len(out) - offset
            out += out[start:start + total]
        else:                            # overlapping copy = repeat the last `offset` bytes
            pattern = bytes(out[-offset:])
            out += (pattern * (total // offset + 1))[:total]
    return bytes(out)


# ==========================================================================
# DAS: texture packs ("DASP")
#
# A pack holds an image record table, a 256 colour palette (6-bit VGA values) and a name table. Pixels are
# stored column by column (all pixels of column 0, then column 1, ...). Animated textures store the first
# frame in full and every following frame as a delta against the previous one.
# ==========================================================================

def apply_delta(prev: bytes, data: bytes, pos: int) -> bytes:
    """Apply one animation delta, stored at `pos` in `data`, to the previous frame.

    Opcodes of the delta stream:
      00 <n> <v>             write value v, n times
      01..7F <bytes>         copy that many literal bytes
      81..FE                 skip (opcode - 0x80) pixels
      FF <b> (b > 0x80)      skip b - 1 pixels (128..254)
      80 <u16 n>             skip n pixels
      80 <u16 0xC000|n> <v>  write value v, n times (long run)
      80 00 00               end of the delta
    """
    out = bytearray(prev)
    size = len(out)
    o = 0
    p = pos
    while True:
        code = data[p]
        p += 1
        if code == 0:                                    # run of one value
            count, value = data[p], data[p + 1]
            p += 2
            out[o:o + count] = bytes([value]) * count
            o += count
        elif code == 0x80:
            param = struct.unpack_from("<H", data, p)[0]
            p += 2
            if param == 0:                               # end of the delta
                break
            if (param & 0xC000) == 0xC000:               # long run of one value
                count = param & 0x3FFF
                out[o:o + count] = bytes([data[p]]) * count
                p += 1
                o += count
            else:                                        # long skip
                o += param
        elif code > 0x80:
            if code == 0xFF and data[p] > 0x80:          # skip 128..254 pixels
                o += data[p] - 1
                p += 1
            else:                                        # skip 1..127 pixels
                o += code - 0x80
        else:                                            # literal bytes
            out[o:o + code] = data[p:p + code]
            p += code
            o += code
        if len(out) != size:
            raise ValueError("animation delta ran past the end of the frame")
    return bytes(out)


class Das:
    """A parsed texture pack."""

    def __init__(self, data: bytes):
        if data[:4] != b"DASP":
            raise ValueError("not a DAS texture pack (missing DASP signature)")
        self.d = data
        rec_len = struct.unpack_from("<H", data, 6)[0]
        rec_off = struct.unpack_from("<I", data, 8)[0]
        pal_off = struct.unpack_from("<I", data, 12)[0]
        names_off = struct.unpack_from("<I", data, 20)[0]
        self.names_offset = names_off
        self._starts = None
        # Two 32-bit values per image index: the offset of the image data (0 = no image) and a size field.
        self.recs = struct.unpack_from("<%dI" % (rec_len // 4), data, rec_off)
        palette = data[pal_off:pal_off + 768]
        flat = []
        for k in range(0, 768, 3):               # 6-bit VGA palette -> 8-bit
            for v in palette[k:k + 3]:
                flat.append((v << 2) | (v >> 4))
        self.palette = flat
        self.names = self._read_names(names_off)

    def _read_names(self, o):
        """Return a list of (image index, short name, long name). Indices from 0x1000 up are sprites."""
        d = self.d
        texture_count, sprite_count = struct.unpack_from("<HH", d, o)
        o += 4
        names = []
        for _ in range(texture_count + sprite_count):
            _unknown, index = struct.unpack_from("<HH", d, o)
            o += 4
            end = d.index(b"\0", o)
            short = d[o:end].decode("latin1")
            o = end + 1
            end = d.index(b"\0", o)
            long_ = d[o:end].decode("latin1")
            o = end + 1
            names.append((index, short, long_))
        return names

    def record_span(self, index: int):
        """Byte range (start, end) of the record of an image index; it ends where the next record starts."""
        if self._starts is None:
            self._starts = sorted({off for off in self.recs[0::2] if off})
        start = self.recs[index * 2]
        pos = self._starts.index(start)
        end = self._starts[pos + 1] if pos + 1 < len(self._starts) else self.names_offset
        return start, end

    def is_model(self, index: int) -> bool:
        """True if the record is a 3D model (flags 0x8000) and not an image."""
        off = self.recs[index * 2]
        return bool(off) and struct.unpack_from("<H", self.d, off)[0] == 0x8000

    def decode(self, index: int):
        """Decode one image.

        Returns (width, height, has_transparency, frames) where frames is a list of
        (pixels stored column by column, duration in ms); a still image has one frame.
        """
        d = self.d
        off = self.recs[index * 2]
        flags, h, w = struct.unpack_from("<HHH", d, off)
        animated = bool(flags & 0x100)
        stencil = not (flags & 0x200)
        sprite = index >= 0x1000
        transparent = sprite or stencil                  # palette index 0 is the transparent colour
        if not animated:
            return w, h, transparent, [(d[off + 6:off + 6 + w * h], 0)]

        # Animated: a header with the animation speed and one delta offset per slot, then the first frame.
        _total, header_len, slot_count = struct.unpack_from("<IHH", d, off + 6)
        ms = SPEED_MS.get(d[off + 6 + 11], 100)
        offsets = list(struct.unpack_from("<%dI" % slot_count, d, off + 6 + 12))
        base = d[off + 6 + header_len:off + 6 + header_len + w * h]
        last = max((i for i, v in enumerate(offsets) if v), default=-1)
        if last < 0:
            return w, h, transparent, [(base, 0)]
        # Slot k shows the state after delta k-1. The last non-zero delta only leads back to the first frame
        # (the loop), so it is never shown. A zero offset means "no change": the frame is held longer.
        slots = [None] + offsets[:last]
        frames = []
        current = base
        i = 0
        while i < len(slots):
            if slots[i] is not None:
                current = apply_delta(current, d, off + slots[i] - 10)
            duration = ms
            j = i + 1
            while j < len(slots) and slots[j] == 0:
                duration += ms
                j += 1
            frames.append((current, duration))
            i = j
        return w, h, transparent, frames


# ==========================================================================
# 3D models inside texture packs
#
# A record whose flags word is 0x8000 is not an image but a polygon model (for example SOFA and BPIPE in
# EUREKA0; its long name even says "3D PIPE!!!"). Layout, all values little-endian int16 unless noted:
#
#   +0   0x8000 (u16)   +2  half width (X extent)   +4  half depth (Z extent)   then six more header words
#   +20  vertex table: N records of 16 bytes. The first record starts with the vertex count N in place of
#        its first word; every record is [0, X, Y, Z, 0, 0, 0, 0] (Y is up, units are probably centimetres)
#   after the vertex table: a 64 byte tag record ("EXPL" = the object can be destroyed, it then turns into
#        its "-BRK" variant), then the faces, one after another
#   face record: [vertex offsets..., repeated first offset (quads only)] [size] [4 words] [n] [texture word] ...
#        - a vertex offset is the vertex index * 16 (an offset into the vertex table)
#        - the record length is 2 * (number of list entries incl. the closing one) + 54 bytes (62 or 64)
#        - n is the number of vertices of the face (3 or 4)
#        - texture word: top bit set = a texture index (low 15 bits); otherwise a flat palette colour in the
#          high byte (for example 0x5400 = colour 0x54)
#        - the remaining words (per-vertex shading values and other parameters) are not decoded; there are no
#          texture coordinates at all: the game tiles the texture over the face
#   the data ends with a last unused face (n = 0) and a few bytes of an incomplete record
# ==========================================================================

def parse_model(blob: bytes):
    """Parse a 3D model record. Returns (vertices, faces); a face is (vertex indices, texture word)."""
    flags = struct.unpack_from("<H", blob, 0)[0]
    if flags != 0x8000:
        raise ValueError("not a 3D model record")
    count = struct.unpack_from("<h", blob, 20)[0]
    if not 0 < count < 2000 or 22 + 16 * count > len(blob):
        raise ValueError("bad vertex count")
    vertices = [struct.unpack_from("<hhh", blob, 22 + 16 * k) for k in range(count)]
    pos = 20 + 16 * count + 64                       # skip the vertex table and the 64 byte tag record
    faces = []
    while pos + 24 <= len(blob):
        w = struct.unpack_from("<%dh" % min(40, (len(blob) - pos) // 2), blob, pos)
        j = next((k for k in range(3, 9) if k < len(w) and w[k] == w[0]), None)    # entry that closes the list
        if j is None or j + 7 >= len(w):
            break
        n, texture = w[j + 6], w[j + 7]
        if not 3 <= n <= j + 1 or not all(r % 16 == 0 and 0 <= r < 16 * count for r in w[:n]):
            break
        faces.append(([r // 16 for r in w[:n]], texture))
        pos += 2 * (j + 1) + 54
    return vertices, faces


def export_model(das, index: int, name: str, folder: Path) -> str:
    """Write a 3D model as <name>.obj + <name>.mtl (+ PNG textures) into `folder`."""
    start, end = das.record_span(index)
    vertices, faces = parse_model(das.d[start:end])
    folder.mkdir(parents=True, exist_ok=True)
    base = safe_name(name, "%04X" % index)
    names = {i: short for i, short, _ in das.names}
    materials = {}                                    # material name -> .mtl lines
    sizes = {}                                        # material name -> texture size in pixels (w, h)

    def material_for(texture_word):
        """Return (material name, texture width, texture height) for the texture word of a face."""
        if texture_word & 0x8000:                     # textured face
            idx = texture_word & 0x7FFF
            tex_name = safe_name(names.get(idx, "tex%04X" % idx), "tex%04X" % idx)
            mat = "tex_" + tex_name
            if mat not in materials:
                lines = ["newmtl " + mat, "Kd 1.0 1.0 1.0"]
                sizes[mat] = (32, 32)
                if idx < len(das.recs) // 2 and das.recs[idx * 2] and not das.is_model(idx):
                    w, h, _transparent, frames = das.decode(idx)
                    to_image(frames[0][0], w, h, das.palette).convert("RGB").save(folder / (tex_name + ".png"))
                    lines.append("map_Kd " + tex_name + ".png")
                    sizes[mat] = (w, h)
                materials[mat] = lines
            return (mat,) + sizes[mat]
        ci = (texture_word >> 8) & 0xFF                # flat colour
        mat = "color_%d" % ci
        if mat not in materials:
            r, g, b = das.palette[ci * 3:ci * 3 + 3]
            materials[mat] = ["newmtl " + mat, "Kd %.4f %.4f %.4f" % (r / 255.0, g / 255.0, b / 255.0)]
            sizes[mat] = (32, 32)
        return (mat,) + sizes[mat]

    obj = ["# %s: 3D model extracted from the game files (units as in the file, Y is up)" % name,
           "mtllib %s.mtl" % base, "o %s" % base]
    obj += ["v %d %d %d" % v for v in vertices]
    body = []
    uv_count = 0
    normals = []
    for idxs, texture in faces:
        pts = [vertices[i] for i in idxs]
        e1 = [pts[1][k] - pts[0][k] for k in range(3)]
        e2 = [pts[2][k] - pts[0][k] for k in range(3)]
        nx, ny, nz = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
        length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        normal = (nx / length, ny / length, nz / length)
        normals.append(normal)
        # Box projection along the dominant axis of the face normal, in absolute model coordinates, so that the
        # texture tiles seamlessly over the whole model: +-Y faces use (X, Z), +-Z faces (X, Y), +-X faces (Z, Y).
        mat, tex_w, tex_h = material_for(texture)
        axis = max(range(3), key=lambda a: abs(normal[a]))
        ua, va = {0: (2, 1), 1: (0, 2), 2: (0, 1)}[axis]
        refs = []
        for p in pts:
            obj.append("vt %.5f %.5f" % (p[ua] * MODEL_TEXELS_PER_UNIT / tex_w, p[va] * MODEL_TEXELS_PER_UNIT / tex_h))
            uv_count += 1
            refs.append(uv_count)
        body.append((mat, idxs, refs, len(normals)))
    obj += ["vn %.5f %.5f %.5f" % n for n in normals]
    current = None
    for mat, idxs, refs, ni in body:
        if mat != current:
            obj.append("usemtl " + mat)
            current = mat
        obj.append("f " + " ".join("%d/%d/%d" % (i + 1, r, ni) for i, r in zip(idxs, refs)))
    (folder / (base + ".obj")).write_text("\n".join(obj) + "\n")
    mtl = []
    for lines in materials.values():
        mtl += lines + [""]
    (folder / (base + ".mtl")).write_text("\n".join(mtl))
    return "%d vertices, %d faces" % (len(vertices), len(faces))


# ==========================================================================
# Image helpers
# ==========================================================================

_TRANSPOSE = getattr(Image, "Transpose", Image).TRANSPOSE


def to_image(colmajor: bytes, w: int, h: int, palette) -> Image.Image:
    """Make a palette image from pixels stored column by column."""
    img = Image.frombytes("P", (h, w), colmajor).transpose(_TRANSPOSE)
    img.putpalette(palette)
    return img


def save_gif(path: Path, w, h, transparent, frames, palette):
    """Save a texture (still or animated) as a GIF."""
    images = [to_image(f, w, h, palette) for f, _ in frames]
    options = {"optimize": False}
    if transparent:
        options["transparency"] = 0
    if len(images) == 1:
        images[0].save(path, **options)
        return
    images[0].save(
        path,
        save_all=True,
        append_images=images[1:],
        duration=[ms for _, ms in frames],
        loop=0,
        disposal=2 if transparent else 1,
        **options,
    )


def save_indexed(path: Path, w, h, pix: bytes, palette, transparent=False):
    """Save row-by-row palette pixels as a GIF."""
    img = Image.frombytes("P", (w, h), pix)
    img.putpalette(palette)
    img.save(path, optimize=False, **({"transparency": 0} if transparent else {}))


def safe_name(name: str, fallback: str) -> str:
    """Make a string usable as a file name."""
    name = re.sub(r"[^A-Za-z0-9_.\-]+", "_", name.strip()).strip("._")
    return name or fallback


def words_to_palette(words):
    """Convert 256 15-bit colours (5-5-5) to a flat list of 8-bit R, G, B values."""
    flat = []
    for v in words:
        r, g, b = (v >> 10) & 31, (v >> 5) & 31, v & 31
        if SWAP_RED_BLUE:
            r, b = b, r
        flat += [(r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2)]
    return flat


def ui_palette():
    return words_to_palette([int(UI_PALETTE_HEX[k * 4:k * 4 + 4], 16) for k in range(256)])


# ==========================================================================
# Full-screen pictures, icon sheets and bitmap fonts
# ==========================================================================

def parse_screen(data: bytes):
    """Recognise a full-screen picture: width * height palette indices (row by row) followed by a 512 byte
    palette of 256 15-bit entries. Returns (width, height, pixels, palette) or None."""
    n = len(data) - 512
    if n not in SCREEN_SIZES:
        return None
    words = struct.unpack("<256H", data[n:])
    if any(v & 0x8000 for v in words):                 # the top bit of a 15-bit colour is always 0
        return None
    w, h = SCREEN_SIZES[n]
    return w, h, data[:n], words_to_palette(words)


def tile_sheet(pix: bytes, w: int, h: int, cols: int):
    """Arrange the w*h cells of a stream into one sheet with `cols` cells per row.
    Returns (sheet width, sheet height, pixels)."""
    count = len(pix) // (w * h)
    rows = (count + cols - 1) // cols
    sheet = bytearray(cols * w * rows * h)
    for i in range(count):
        cell = pix[i * w * h:(i + 1) * w * h]
        x0, y0 = (i % cols) * w, (i // cols) * h
        for y in range(h):
            o = (y0 + y) * cols * w + x0
            sheet[o:o + w] = cell[y * w:(y + 1) * w]
    return cols * w, rows * h, bytes(sheet)


def guess_glyph_size(data: bytes):
    """Guess the glyph cell size of a bitmap font that is stored as a stream of equally sized cells.

    With the right cell size almost no ink touches the border of a cell and neighbouring pixels agree. Two
    stacked rows of glyphs look just as "clean", so among nearly equal candidates the smaller cell wins."""
    n = len(data)
    scored = {}
    for w in range(5, 41):
        for h in range(8, 41):
            size = w * h
            if n % size or not 90 <= n // size <= 260:
                continue
            ink = border = 0
            for i, v in enumerate(data):
                if v:
                    ink += 1
                    x, y = i % w, (i // w) % h
                    if x in (0, w - 1) or y in (0, h - 1):
                        border += 1
            diff = 0
            for i in range(n - w):
                if data[i] != data[i + 1] and (i + 1) % w:
                    diff += 1
                if data[i] != data[i + w] and (i % size) < size - w:
                    diff += 1
            if ink:
                scored[(w, h)] = border / ink + 0.5 * (diff / n)
    if not scored:
        raise ValueError("could not determine the glyph size")
    (w, h) = min(scored, key=scored.get)
    best = scored[(w, h)]
    changed = True
    while changed:                                     # try halving the cell
        changed = False
        for candidate in ((w // 2, h), (w, h // 2)):
            if candidate in scored and candidate != (w, h) and scored[candidate] <= best * 1.1 + 0.01:
                (w, h), best, changed = candidate, scored[candidate], True
                break
    return w, h


# ==========================================================================
# Converting one .MGL file to GIFs
# ==========================================================================

def convert_pack(mgl_path: Path, out_root: Path, data: bytes, model_root: Path):
    """Save every texture and sprite of a texture pack as GIF and every 3D model as OBJ.
    Returns (images saved, failures, models saved)."""
    das = Das(data)
    pack_dir = out_root / mgl_path.stem
    used = set()
    saved = failed = models = 0
    for index, short, _long in das.names:
        if index * 2 >= len(das.recs) or das.recs[index * 2] == 0:
            continue                                   # the record has no data
        if das.is_model(index):                        # a 3D model, not an image
            try:
                export_model(das, index, short, model_root / mgl_path.stem)
                models += 1
            except Exception as e:
                failed += 1
                print("    ! %s (index 0x%X): %s" % (short, index, e))
            continue
        sprite = index >= 0x1000
        folder = pack_dir / "sprites" if sprite else pack_dir
        name = safe_name(short, "%04X" % index)
        if (folder, name.lower()) in used:             # duplicate name inside the pack
            name = "%s_%04X" % (name, index)
        used.add((folder, name.lower()))
        try:
            w, h, transparent, frames = das.decode(index)
            folder.mkdir(parents=True, exist_ok=True)
            save_gif(folder / (name + ".gif"), w, h, transparent, frames, das.palette)
            saved += 1
        except Exception as e:                         # one broken record must not stop the rest
            failed += 1
            print("    ! %s (index 0x%X): %s" % (short, index, e))
    return saved, failed, models


def convert_sprite_sheet(data: bytes, stem: str, cell, out_root: Path) -> str:
    """Save an icon sheet: one combined GIF and every non-empty cell as its own transparent GIF."""
    w, h = cell
    count = len(data) // (w * h)
    palette = ui_palette()
    out_root.mkdir(exist_ok=True)
    cols = 12 if count % 12 == 0 else 10
    sheet_w, sheet_h, sheet = tile_sheet(data, w, h, cols)
    save_indexed(out_root / (stem + ".gif"), sheet_w, sheet_h, sheet, palette)
    folder = out_root / stem
    folder.mkdir(exist_ok=True)
    saved = 0
    for i in range(count):
        pixels = data[i * w * h:(i + 1) * w * h]
        if any(pixels):                                # skip empty cells
            save_indexed(folder / ("%03d.gif" % i), w, h, pixels, palette, transparent=True)
            saved += 1
    return "sheet of %d cells of %dx%d (%d non-empty, one file each in folder %s)" % (count, w, h, saved, stem)


def convert_font(data: bytes, out_path: Path) -> str:
    """Save a bitmap font as one sheet of 16 glyphs per row."""
    known = len(data) in KNOWN_FONTS
    w, h = KNOWN_FONTS[len(data)] if known else guess_glyph_size(data)
    count = len(data) // (w * h)
    sheet_w, sheet_h, sheet = tile_sheet(data, w, h, 16)
    out_path.parent.mkdir(exist_ok=True)
    save_indexed(out_path, sheet_w, sheet_h, sheet, ui_palette())
    return "bitmap font, %d glyphs of %dx%d%s" % (count, w, h, "" if known else " (glyph size guessed automatically)")


def convert_screen(parsed, out_path: Path):
    w, h, pixels, palette = parsed
    save_indexed(out_path, w, h, pixels, palette)


def convert_file(mgl_path: Path, out_root: Path, model_root: Path) -> str:
    """Convert one .MGL file; returns a short description of what it contained."""
    data = mgl_decompress(mgl_path.read_bytes())
    if data[:4] == b"DASP":
        try:
            saved, failed, models = convert_pack(mgl_path, out_root, data, model_root)
        except (struct.error, IndexError, ValueError):
            raise ValueError("the texture pack is corrupted or truncated")
        return "texture pack: %d GIF(s)%s%s" % (saved, (", %d 3D model(s)" % models) if models else "",
                                                (", %d error(s)" % failed) if failed else "")
    screen = parse_screen(data)
    if screen:
        out_root.mkdir(exist_ok=True)
        convert_screen(screen, out_root / (mgl_path.stem + ".gif"))
        return "full-screen picture %dx%d" % screen[:2]
    stem = mgl_path.stem
    if stem.upper() in SPRITE_SHEETS:
        w, h = SPRITE_SHEETS[stem.upper()]
        if len(data) % (w * h) == 0:
            return convert_sprite_sheet(data, stem, SPRITE_SHEETS[stem.upper()], out_root)
    if len(data) >= 1000 and max(data) <= 2 and any(data):
        return convert_font(data, out_root / (stem + ".gif"))
    raise ValueError("unrecognised format: %d bytes after decompression, starts with: %s"
                     % (len(data), data[:12].hex(" ")))


# ==========================================================================
# Level maps (.RAW)
#
# A map is a set of convex sectors (rooms and pieces of rooms) that are glued together through portals.
# All numbers are little-endian; coordinates are signed 16-bit. File layout:
#
#   ROOM block (header, sectors, edges, wall properties, start info)  w0 bytes
#   VERT block (vertex table)                                        w10 bytes
#   SEG, OBJ, SND blocks (not decoded)                               w9, w11, w12 bytes
#
# Header: 14 words (28 bytes)
#   w0   size of the ROOM block = file offset of the VERT block
#   w2   0x1C, the header size = file offset of the first sector record
#   w3   file offset where the edge records start (= end of the sector records)
#   w4   file offset where the wall property records start (= end of the edge records)
#   w5   file offset of the start block (initial camera X, height, Y)
#   w7   the magic "WR" (0x5257)
#   w13  number of sectors
#
# Sector record: 13 words (26 bytes), stored one after another
#   w0   ceiling height          w1   floor height (both signed)
#   w3   ceiling texture         w4   floor texture
#   w5   low byte: bits 2-3 = ceiling scale index, bits 4-5 = floor scale index (0..3)
#   w8   ceiling texture offset  w9   floor texture offset (two bytes each: byte 1 for X, byte 2 for Y)
#   (the remaining words are not decoded)
#   A texture value >= 0 is an image index in the texture pack. A negative value means a flat colour:
#   the palette index is (value & 0xFF).
#
# Edge record: 12 bytes = vertex offset A, vertex offset B (offsets into the VERT block), offset of the wall
#   property record, file offset of the owning sector, file offset of the paired edge in the neighbouring
#   sector (-1 = solid wall, otherwise a portal), flags. The edges of one sector form a closed polygon.
#
# Wall property record: 5 signed words = [flags, middle, upper, lower texture, ...]; -1 means "none" and
#   other negative values are flat colours, like for floors.
#
# VERT block: header of 8 bytes (the vertex count is the word at +6), then records of 12 bytes; the X and Y
#   of a vertex are two int16 at (block start + 8 + vertex offset).
#
# Floor texture mapping (taken from the game's floor renderer and verified against the game):
#   P      = 2^(7 + scale) * min(1, texture width / FULL_PERIOD_MIN_WIDTH)   map units per texture repeat
#   column = (Y - byte2 * P / 256) * width  / P
#   row    = (-X + byte1 * P / 256) * height / P
#   where "column" is the first index of the stored (column by column) pixel data.
# ==========================================================================

def _s16(v):
    return v - 65536 if v >= 32768 else v


def parse_raw_map(d: bytes):
    """Parse a .RAW level map. Returns (sectors, edges_by_offset, vertex_count).

    Every sector is a dict with its heights, textures, scale indices, texture offsets and its list of edges
    ("edges") and the polygon of its vertices ("poly"). Every edge is a dict with its two end points ("a",
    "b"), its paired edge offset ("link"), the owning sector offset ("owner") and the wall textures
    ("mid", "upper", "lower")."""
    h = struct.unpack_from("<14H", d, 0)
    if h[2] != 0x1C or h[7] != 0x5257:
        raise ValueError("not a level map (missing WR header)")
    vert_block, sector_end, edge_end = h[0], h[3], h[4]
    vertex_count = struct.unpack_from("<H", d, vert_block + 6)[0]

    def vertex(offset):
        return struct.unpack_from("<hh", d, vert_block + 8 + offset)

    sectors = []
    o = h[2]
    while o + 26 <= sector_end:
        w = struct.unpack_from("<13H", d, o)
        sectors.append({
            "off": o, "ceil": _s16(w[0]), "floor": _s16(w[1]),
            "ceil_tex": _s16(w[3]), "floor_tex": _s16(w[4]), "edges": [],
            "floor_scale": (w[5] >> 4) & 3, "ceil_scale": (w[5] >> 2) & 3,
            "ceil_off": (w[8] & 0xFE, (w[8] >> 8) & 0xFE),
            "floor_off": (w[9] & 0xFE, (w[9] >> 8) & 0xFE),
        })
        o += 26
    by_offset = {s["off"]: s for s in sectors}
    edges = {}
    o = sector_end
    while o + 12 <= edge_end:
        a, b, prop, owner, link, _flags = struct.unpack_from("<hhHHhH", d, o)
        e = {"off": o, "a": vertex(a), "b": vertex(b), "link": link, "owner": owner,
             "mid": -1, "upper": -1, "lower": -1}
        if edge_end <= prop + 10 <= len(d):               # wall property record: 5 words
            _w0, e["mid"], e["upper"], e["lower"], _w4 = struct.unpack_from("<5h", d, prop)
        edges[o] = e
        if owner in by_offset:
            by_offset[owner]["edges"].append(e)
        o += 12
    for s in sectors:
        s["poly"] = [e["a"] for e in s["edges"]]
    return sectors, edges, vertex_count


def find_texture_pack(raw_path: Path):
    """Find the texture pack of a map next to it (a .DAS file, otherwise a .MGL file), using MAP_PACKS.
    Returns (Das or None, pack name)."""
    pack_stem = MAP_PACKS.get(raw_path.stem.upper(), raw_path.stem)
    found = {}
    for p in raw_path.parent.iterdir():
        if p.is_file() and p.stem.lower() == pack_stem.lower() and p.suffix.lower() in (".das", ".mgl"):
            found[p.suffix.lower()] = p
    try:
        if ".das" in found:
            data = found[".das"].read_bytes()
        elif ".mgl" in found:
            data = mgl_decompress(found[".mgl"].read_bytes())
        else:
            return None, pack_stem
        return (Das(data) if data[:4] == b"DASP" else None), pack_stem
    except Exception:
        return None, pack_stem


def render_map(sectors, edges, das, title):
    """Draw the floor plan of a map as a PIL image (X to the right, Y up).

    Floors use their real textures when the pack is available, flat colours otherwise. White lines are solid
    walls; a coloured line marks a step or the side of an object (the colour is the average colour of the side
    texture); thin dark lines are ordinary portals between sectors."""
    shown = [s for s in sectors if len(s["poly"]) >= 3]

    # Map frame: percentiles of the vertices, see MAP_CROP_PERCENT. The sky ceiling that most outdoor sectors
    # have plays no role here: such sectors have ordinary floors.
    xs_all = sorted(p[0] for s in shown for p in s["poly"])
    ys_all = sorted(p[1] for s in shown for p in s["poly"])

    def percentile(values, q):
        return values[min(len(values) - 1, max(0, int(q * (len(values) - 1))))]

    x0, x1 = percentile(xs_all, MAP_CROP_PERCENT), percentile(xs_all, 1 - MAP_CROP_PERCENT)
    y0, y1 = percentile(ys_all, MAP_CROP_PERCENT), percentile(ys_all, 1 - MAP_CROP_PERCENT)
    if (x1 - x0) * (y1 - y0) > 0.8 * (xs_all[-1] - xs_all[0]) * (ys_all[-1] - ys_all[0]):    # almost nothing to crop
        x0, x1, y0, y1 = xs_all[0], xs_all[-1], ys_all[0], ys_all[-1]
    pad = 0.04 * max(x1 - x0, y1 - y0, 1)
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    side = MAP_MAX_SIDE_LARGE if len(shown) > LARGE_MAP_SECTORS else MAP_MAX_SIDE
    k = side / max(x1 - x0, y1 - y0)                     # pixels per map unit
    W, H = int((x1 - x0) * k) + 1, int((y1 - y0) * k) + 1

    def to_screen(p):                                    # map -> pixels (the Y axis points up)
        return ((p[0] - x0) * k, (y1 - p[1]) * k)

    img = Image.new("RGB", (W, H), (16, 16, 22))
    draw = ImageDraw.Draw(img)

    texture_cache = {}                                   # image index -> (oriented tile, average colour, w, h)
    sized_cache = {}                                     # (image index, width, height) -> resized tile

    def get_texture(idx):
        if idx not in texture_cache:
            value = None
            try:
                if das and idx >= 0 and das.recs[idx * 2]:
                    w, h, _transparent, frames = das.decode(idx)
                    tex = to_image(frames[0][0], w, h, das.palette).convert("RGB")
                    # On screen X runs along the texture rows (backwards) and Y along the columns (upwards),
                    # so the tile is transposed and mirrored in both directions.
                    oriented = ImageOps.flip(ImageOps.mirror(tex.transpose(_TRANSPOSE)))
                    value = (oriented, tex.resize((1, 1), Image.BOX).getpixel((0, 0)), w, h)
            except Exception:
                value = None
            texture_cache[idx] = value
        return texture_cache[idx]

    def floor_patch(idx, scale_idx, off, bx0, by0, bx1, by1):
        """The textured floor for the pixel rectangle (bx0, by0)-(bx1, by1), or None if there is no texture.
        See the description of the floor texture mapping above the map code."""
        t = get_texture(idx)
        if t is None:
            return None
        oriented, average, w, h = t
        period = 2 ** (7 + scale_idx) * min(1.0, w / FULL_PERIOD_MIN_WIDTH)   # map units per repeat
        tile_size = period * k                                                 # repeat size in pixels
        pw, ph = bx1 - bx0, by1 - by0
        if tile_size < 6:                                                      # too small to draw: average colour
            return Image.new("RGB", (pw, ph), average)
        ox = ((off[0] * period / 256.0) - x0) * k                              # left edge of some repeat
        oy = (y1 - off[1] * period / 256.0) * k                                # top edge of some repeat
        i0, i1 = math.floor((bx0 - ox) / tile_size) - 1, math.ceil((bx1 - ox) / tile_size) + 1
        j0, j1 = math.floor((by0 - oy) / tile_size) - 1, math.ceil((by1 - oy) / tile_size) + 1
        xs = [round(ox + i * tile_size) for i in range(i0, i1 + 1)]            # boundaries are computed from the
        ys = [round(oy + j * tile_size) for j in range(j0, j1 + 1)]            # origin, so errors do not add up
        patch = Image.new("RGB", (pw, ph))
        for yi in range(len(ys) - 1):
            tile_h = ys[yi + 1] - ys[yi]
            for xi in range(len(xs) - 1):
                tile_w = xs[xi + 1] - xs[xi]
                if tile_w < 1 or tile_h < 1:
                    continue
                key = (idx, tile_w, tile_h)
                tile = sized_cache.get(key)
                if tile is None:
                    tile = sized_cache[key] = oriented.resize((tile_w, tile_h), Image.NEAREST)
                patch.paste(tile, (xs[xi] - bx0, ys[yi] - by0))
        return patch

    # Floors
    for i, s in enumerate(shown):
        poly = [to_screen(p) for p in s["poly"]]
        bx0, by0 = max(0, int(min(p[0] for p in poly))), max(0, int(min(p[1] for p in poly)))
        bx1, by1 = min(W, int(max(p[0] for p in poly)) + 2), min(H, int(max(p[1] for p in poly)) + 2)
        if bx1 <= bx0 or by1 <= by0:
            continue                                                            # the sector is outside the frame
        flat = s["floor_tex"] < 0                        # a negative value is a flat palette colour
        patch = None if flat else floor_patch(s["floor_tex"], s["floor_scale"], s["floor_off"], bx0, by0, bx1, by1)
        if patch is None:
            if flat and das:
                ci = (s["floor_tex"] & 0xFF) * 3
                color = tuple(das.palette[ci:ci + 3])
            else:                                        # no texture pack: a distinct colour per sector
                color = tuple(int(90 + 80 * c) for c in colorsys.hsv_to_rgb((i * 0.137) % 1, 0.35, 0.6))
            draw.polygon(poly, fill=color)
        else:
            mask = Image.new("L", (bx1 - bx0, by1 - by0), 0)
            ImageDraw.Draw(mask).polygon([(x - bx0, y - by0) for x, y in poly], fill=255)
            img.paste(patch, (bx0, by0), mask)

    # Edges
    by_offset = {s["off"]: s for s in sectors}
    average_cache = {}

    def side_color(idx):
        """Colour of a wall side: the average colour of its texture; for a negative value (other than -1,
        which means "none") the flat palette colour (value & 0xFF); None if there is no data."""
        if idx not in average_cache:
            color = None
            try:
                if idx < -1 and das:
                    ci = (idx & 0xFF) * 3
                    color = tuple(das.palette[ci:ci + 3])
                elif das and idx >= 0 and das.recs[idx * 2]:
                    w, h, _transparent, frames = das.decode(idx)
                    color = to_image(frames[0][0], w, h, das.palette).convert("RGB").resize(
                        (1, 1), Image.BOX).getpixel((0, 0))
            except Exception:
                color = None
            average_cache[idx] = color
        return average_cache[idx]

    for s in shown:
        for e in s["edges"]:
            a, b = to_screen(e["a"]), to_screen(e["b"])
            if e["link"] < 0:                                         # solid wall
                draw.line([a, b], fill=(255, 255, 255), width=2)
                continue
            neighbour = edges.get(e["link"])
            other = neighbour and by_offset.get(neighbour["owner"])
            if other and other["floor"] > s["floor"]:                 # step or side of an object, seen from here
                tex_idx = e["lower"] if e["lower"] != -1 else e["mid"]
                color = side_color(tex_idx) or (255, 150, 0)          # orange only when there is no data at all
                draw.line([a, b], fill=color, width=3)
            elif not (other and other["floor"] < s["floor"]):         # ordinary portal
                draw.line([a, b], fill=(50, 50, 60), width=1)

    if DRAW_SECTOR_NUMBERS:
        for i, s in enumerate(sectors):
            if len(s["poly"]) >= 3:
                cx = sum(p[0] for p in s["poly"]) / len(s["poly"])
                cy = sum(p[1] for p in s["poly"]) / len(s["poly"])
                draw.text(to_screen((cx, cy)), str(i), fill=(255, 255, 0))

    draw.rectangle([0, 0, 560, 16], fill=(0, 0, 0))
    draw.text((4, 2), "%s: %d sectors | white=wall, coloured line=step/object side" % (title, len(sectors)),
              fill=(220, 220, 220))
    return img


def convert_map(raw_path: Path, out_root: Path) -> str:
    """Render one .RAW map to maps/<name>.png; returns a short description."""
    try:
        sectors, edges, vertex_count = parse_raw_map(raw_path.read_bytes())
    except (struct.error, IndexError):
        raise ValueError("the file is corrupted or truncated")
    das, pack_stem = find_texture_pack(raw_path)
    out_root.mkdir(exist_ok=True)
    render_map(sectors, edges, das, raw_path.stem).save(out_root / (raw_path.stem + ".png"))
    if not das:
        note = " (texture pack %s not found next to the map, floors are flat-coloured)" % pack_stem
    elif pack_stem.upper() != raw_path.stem.upper():
        note = " (textures taken from pack %s)" % pack_stem
    else:
        note = ""
    return "level map: %d sectors, %d vertices%s" % (len(sectors), vertex_count, note)


# ==========================================================================
# Main
# ==========================================================================

def main():
    here = Path(__file__).resolve().parent
    gif_root = here / "gifs"
    map_root = here / "maps"
    model_root = here / "models"
    files = [p for p in here.iterdir() if p.is_file()]
    packs = sorted(p for p in files if p.suffix.lower() == ".mgl")
    maps = sorted(p for p in files if p.suffix.lower() == ".raw")
    if not packs and not maps:
        print("There is no .MGL or .RAW file next to the script.")
        return 1
    if packs:
        gif_root.mkdir(exist_ok=True)
    for p in packs:
        print("%s ..." % p.name)
        try:
            print("    done: %s" % convert_file(p, gif_root, model_root))
        except Exception as e:
            print("    skipped: %s" % e)
    for p in maps:
        print("%s ..." % p.name)
        try:
            print("    done: %s" % convert_map(p, map_root))
        except Exception as e:
            print("    skipped: %s" % e)
    if model_root.exists():
        print("3D model folder: %s" % model_root)
    if packs:
        print("GIF folder: %s" % gif_root)
    if maps:
        print("Map folder: %s" % map_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
