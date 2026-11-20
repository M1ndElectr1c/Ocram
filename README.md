# OCRAM — Old-gen Console Resource & Asset Manager (Beta)

**OCRAM** is a standalone, cross-platform GUI modding suite designed to port modern PC **Terraria (1.4.x)** texture packs, custom `.wav` sound effects, music, and localizations to **Terraria on PS Vita (Legacy 1.2.4)**.

> **Status:** Public Beta (`v0.9-beta`). Core VFS2 unpacking/repacking, GXT palette encoding, smart-fit sprite patching, audio conversion, and binary `.str` editing are functional. Community testing on real PS Vita hardware and Vita3K is welcome!

---

## Why PC and PS Vita Texture Packs Are Incompatible Out of the Box

Porting assets from modern PC Terraria to the PS Vita edition is not a simple drag-and-drop process due to fundamental architectural differences between the versions:

1. **Loose PNGs vs. Shared Hardware GXT Atlases:**
   - PC Terraria (1.4.x) stores every item, NPC, block, wall, and background as thousands of individual `.png` files.
   - The PS Vita version (ported by Engine Software based on Legacy 1.2.4) packs thousands of sprites tightly into shared Sony `.gxt` texture atlases (`_sheetSprites`, `_sheetNPC`, `_sheetTiles`, `_sheetWalls`, `_sheetSolid`, `_sheetBack`) using hardware-swizzled Morton Z-order layouts.
2. **Strict Color Bit-Depth & Palette Limits:**
   - Most PS Vita sheets use 16-bit indexed palettes (with normalized UV coordinates mapped to `256x128`, `128x128`, `128x64`, or `32x32` grids), while `_sheetNPC` and standalone backgrounds (`_sheetBack93`, `94`, `180`) are restricted to 8-bit (256-color) indexed palettes.
   - Large biome backgrounds (`_sheetBack1..9`) and clouds (`_sheetClouds`) use hardware **DXT1/BC1** and **PVRTC 2bpp** compression.
3. **Sprite Orientation, Frame Counts & Dimensions:**
   - Many projectiles and items are rotated 90 degrees inside PS Vita atlases.
   - Modern 1.4.x sprites often feature larger canvas sizes or extra animation frames compared to their 1.2.4 console counterparts.
4. **Proprietary Console Container & Audio/Text Formats:**
   - All assets reside inside a custom `VFS2` archive (`data.vfs`).
   - Sound effects use Sony **HEVAG / PS-ADPCM** (`.vag`), background music uses **ATRAC9** (`.at9`), and localization text is stored in custom binary `.str` tables with 7-bit variable-length offsets.

---

## What OCRAM Does Under the Hood

- **Native VFS2 Unpacker & Repacker (`vfs_handler.py`):** Extracts `data.vfs` (handling both official archives and third-party packs) and rebuilds valid `VFS2` archives with proper `zlib` compression and uncompressed `.at9` alignment.
- **Multi-Format GXT Texture Engine (`gxt_engine.py`):**
  - Decodes and re-encodes 8-bit indexed, 16-bit Morton Z-order indexed, 32-bit `BGRA8888`, swizzled `DXT1/BC1`, and `PVRTC 2bpp` textures.
  - **Smart-Fit Patching:** Automatically trims empty transparent padding around modern PC sprites and proportionally downscales oversized sprites using Nearest-Neighbor filtering so they fit cleanly into PS Vita slots without clipping.
  - **Dynamic Palette Allocation:** Extracts new colors from modded sprites and allocates them into unused rows of the `.gxt` palette without corrupting vanilla colors.
- **Pre-Mapped Coordinate Database (`vita_atlas_map.json`):** Ships with thousands of pre-indexed item, tile, wall, projectile, and boss coordinates so regular users can port texture packs immediately.
- **Human-Readable Asset Search (`terraria_names.json`):** Search sprites by their actual in-game names (e.g., *Terra Blade*, *Skeletron Prime*) alongside internal file IDs.
- **Visual Sprite Editor & Live Overlay:** Inspect atlases, toggle live mod overlay previews, zoom smoothly (`0.25x`–`6.0x` via `Ctrl + Wheel`), scroll vertically/horizontally (`Wheel` / `Shift + Wheel`), adjust animation frame offsets (`Frame Y Offset`), and replace full standalone sheets (`logo*`, UI, backgrounds).
- **Audio & Music Manager (`audio_vag.py`):** Preview original `.vag` sound effects and `.at9` music tracks, and batch-convert PC `.wav` files into PS Vita `.vag` format.
- **Binary Text & Localization Editor (`str_handler.py`):** Search, edit, and export/import `strings_*.str` localization files to/from JSON with automatic 7-bit offset recalculation.

---

## Installation & Setup

### 1. Python Dependencies
Ensure you have **Python 3.10+** installed, then install the required packages:
```bash
pip install -r requirements.txt
```

### 2. Optional Audio Tools (`vgmstream`) (IMPORTANT)
OCRAM includes a built-in Python codec for `.vag` sound effects, but it kinda sucks. for **lossless playback of all `.vag` sounds and Sony `.at9` music tracks**, download the **vgmstream CLI** build for your OS from the official site:
- Download link: **[https://vgmstream.org/](https://vgmstream.org/)** *(choose "Command-line" for Windows, Linux static build, or macOS static build)*.
- Place `vgmstream-cli` (or `vgmstream-cli.exe`) directly into the **OCRAM root folder** (next to `main.py`) or inside a `tools/` subfolder.

---

## How to Use

### Step 1: Obtain Your Decrypted `data.vfs`
You must provide your own legally dumped game archive:
- **On PS Vita (via VitaShell):**
  1. Open **VitaShell** and navigate to `ux0:patch/<TITLE_ID>/` (e.g., `PCSE00317` for US or `PCSB00405` for EU — use `patch/` if official updates are installed, or `app/` for the base game).
  2. Highlight the folder, press **Triangle**, and select **Open decrypted** *(mandatory — otherwise `data.vfs` remains PFS-encrypted)*.
  3. Copy the decrypted `data.vfs` to your PC.
- **On Vita3K (Emulator):**
  - Vita3K decrypts game files automatically upon installation. Copy `data.vfs` directly from your Vita3K `ux0/patch/<TITLE_ID>/` or `ux0/app/<TITLE_ID>/` directory.

### Step 2: Port a Texture Pack
1. Launch OCRAM:
   ```bash
   python main.py
   ```
2. In the **Quick Port** tab:
   - Browse for your decrypted `data.vfs` and click **Extract Base**.
   - Browse for your PC Texture Pack folder (either the pack root or `Content/Images`).
   - Click **Build VFS** to generate your modded `data.vfs`.
3. Copy the resulting `data.vfs` to `ux0:rePatch/<TITLE_ID>/data.vfs` on your PS Vita (requires the **rePatch** plugin) or into Vita3K's `rePatch` folder.

---

## Contributing & Testing (Beta)
Since PS Vita hardware testing is ongoing, community feedback and pull requests are highly appreciated! Areas where help is especially welcome:
- Testing generated `data.vfs` archives on real PS Vita hardware (with `rePatch`) and Vita3K.
- Expanding `vita_atlas_map.json` for unmapped 1.2.4 sprites, furniture (`_sheetSolid`), and biome backgrounds.
- Adding `.wav` to `.at9` encoding support via `at9tool` integration.

---

## Legal Disclaimer
This repository contains **zero copyrighted assets** from Terraria, Re-Logic, 505 Games, or Engine Software. All trademarks and game files belong to their respective owners. Users must supply their own legally obtained `data.vfs` and third-party texture packs.

## Contacts
With any questions you can contact me in Telegram - @memoryburner
