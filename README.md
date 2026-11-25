# gizmo

A Python tool that extracts textures, sprites, 3D models, level maps and interface graphics from Normality (Gremlin Interactive, 1996), built on Antony Crowther's True3D engine.

## What it does

Point it at a folder with the game's data files and it converts everything it recognises into common formats:

- **Textures and sprites** from the texture packs (`.MGL` / `.DAS`) are saved as GIF files. Animated textures stay animated, and transparency is kept.
- **3D models** stored inside the packs (for example the sofa and the broken pipe) are exported as Wavefront OBJ files with an MTL material file and PNG textures.
- **Level maps** (`.RAW`) are drawn as floor plans in PNG format: textured floors, solid walls, steps, portals between sectors and the outlines of objects.
- **Full-screen pictures** (papers, menus, manuals), the **inventory icons** and the **bitmap fonts** are saved as GIF files.

## Requirements

- Python 3.7 or newer
- [Pillow](https://pypi.org/project/pillow/) (`pip install pillow`)

## Usage

1. Copy `gizmo.py` into a folder that contains the game's `.MGL` and/or `.RAW` files (for example the game's `GFX` or `GFX1` folder, or a copy of it).
2. Run it:

```
python gizmo.py
```

It processes every `.MGL` and `.RAW` file next to the script and prints what it found. A file it does not understand is skipped with a short message.

To draw a level map the script needs the matching texture pack in the same folder (the `.MGL` or the unpacked `.DAS` file). Several maps share one pack: for example `FACTOUT1` uses the `FACTOUT` pack and `CELLS2`..`CELLS6` use `CELLS`.

## Output

Everything is written next to the script:

| Folder | Contents |
| --- | --- |
| `gifs/<PACK>/` | one GIF per texture; sprites go to `gifs/<PACK>/sprites/` |
| `gifs/` | full-screen pictures, `ICONS.gif` with every icon (and each icon separately in `gifs/ICONS/`), font sheets |
| `maps/` | one PNG floor plan per level map |
| `models/<PACK>/` | `.obj`, `.mtl` and the PNG textures of every 3D model in the pack |

## How it works

- **MGL** files are compressed with a small LZ77-style scheme (literal runs, back-references and arithmetic progressions). The script decompresses them; the result is a `.DAS` file.
- **DAS** files ("DASP") are texture packs: a record table, a 256-colour palette and a name table. Pixels are stored column by column, and animations are stored as a first frame plus deltas.
- **RAW** files are level maps made of sectors (floor and ceiling heights and textures) joined by edges. Edges that connect two sectors form portals. Floor textures are laid over the sectors with a repeat period that depends on the sector's scale setting.
- **3D models** are records inside the packs (flag `0x8000`): a table of 16-bit vertices followed by quad and triangle faces that reference a texture or a flat palette colour. The data has no texture coordinates; the game tiles the texture over each face.

The format knowledge comes from earlier community work (see below), from analysing the data files and from reading the game executable.

## Settings

A few options are at the top of `gizmo.py`, for example the size of the map images, whether sector numbers are drawn on the maps, and the texture scale used when exporting 3D models (`MODEL_TEXELS_PER_UNIT`).

## Limitations

- Ceilings are not drawn on the map images, and objects are not placed on the maps (their data block is not decoded).
- Texture coordinates in the exported OBJ files are an approximation: a box projection along the model axes. The tiling scale may differ from the game.
- Some files in the game folders are in formats that are not supported yet and are skipped.
- Models are only extracted from `.MGL` files, not from a standalone `.DAS`.

## Credits

This tool builds on earlier work by [Denzquix](https://github.com/radishengine/denormalize), who first worked out the MGL compression and the DAS data files, and [msikma](https://github.com/msikma/denormalize). `gizmo` is an independent implementation in Python and contains no code from those projects.

The True3D engine was created by Antony Crowther.

## Disclaimer

This project is not affiliated with or endorsed by Gremlin Interactive or the rights holders of Normality. It contains no game data: you need your own copy of the game, and the files it produces are for personal use.

## License

MIT, see [LICENSE](LICENSE).
