import os
import shutil
import struct
import cv2
import numpy as np
from PIL import Image

ATLAS_SHEETS = {
    "sprites": "_sheetSprites",
    "tiles":   "_sheetTiles",
    "walls":   "_sheetWalls",
    "npc":     "_sheetNPC",
    "solid":   "_sheetSolid",
    "back":    "_sheetBack",
}

def read_gxt(path: str):
    with open(path, "rb") as f:
        hdr = bytearray(f.read(64))
        raw = bytearray(f.read())
    sz, = struct.unpack("<I", hdr[16:20])
    tex_type, tex_fmt, w, h = struct.unpack("<IIHH", hdr[48:60])
    return hdr, raw, sz, tex_type, tex_fmt, w, h

def morton_offset(flat_idx: int, width: int = 256, height: int = 128) -> int:
    y, x = flat_idx // width, flat_idx % width
    k = int(np.log2(min(width, height)))
    m = 0
    for i in range(k):
        m |= ((y >> i) & 1) << (2 * i)
        m |= ((x >> i) & 1) << (2 * i + 1)
    high = ((y >> k) * (width >> k) + (x >> k)) << (2 * k)
    return (high | m) * 4

def next_pow2(v: int) -> int:
    p = 1
    while p < v:
        p <<= 1
    return p

def unswizzle_blocks(raw_bytes: bytes, bw: int, bh: int, block_bytes: int) -> bytes:
    """Раскладывает Morton-упакованные блоки 4x4 PS Vita SGX543 (Y в чётных битах, X в нечётных)."""
    src = np.frombuffer(raw_bytes[:bw * bh * block_bytes], dtype=np.uint8).reshape((-1, block_bytes))
    dst = np.zeros((bh, bw, block_bytes), dtype=np.uint8)
    k = int(np.log2(min(bw, bh)))
    for y in range(bh):
        for x in range(bw):
            m = 0
            for i in range(k):
                m |= ((y >> i) & 1) << (2 * i)
                m |= ((x >> i) & 1) << (2 * i + 1)
            high = ((y >> k) * (bw >> k) + (x >> k)) << (2 * k)
            idx = high | m
            if idx < len(src):
                dst[y, x] = src[idx]
    return dst.tobytes()


def _gxm_swizzle_indices(w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Генерирует точные таблицы координат (xs, ys) для PS Vita GXM Swizzle (0x60000000)."""
    pw = next_pow2(w)
    ph = next_pow2(h)
    k = int(np.log2(min(pw, ph)))
    max_m = ((pw >> k) * (ph >> k)) << (2 * k)

    xs, ys = [], []
    for idx in range(max_m):
        m = idx & ((1 << (2 * k)) - 1)
        high = idx >> (2 * k)
        x = (high % (pw >> k)) << k
        y = (high // (pw >> k)) << k
        for i in range(k):
            x |= ((m >> (2 * i)) & 1) << i
            y |= ((m >> (2 * i + 1)) & 1) << i
        if x < w and y < h:
            xs.append(x)
            ys.append(y)
    return np.array(xs, dtype=np.int32), np.array(ys, dtype=np.int32)


def decode_single_gxt(gxt_path: str, out_png_path: str) -> np.ndarray | None:
    _, raw, sz, ttype, tfmt, w, h = read_gxt(gxt_path)
    data = bytes(raw[:sz])

    if tfmt == 0x0c001000:  # 32-bit BGRA8888 (Linear / Linear Strided 0x60000000)
        stride = sz // h
        rows = np.frombuffer(data, dtype=np.uint8).reshape((h, stride))[:, :w * 4].reshape((h, w, 4))
        b, g, r, a = rows[..., 0], rows[..., 1], rows[..., 2], rows[..., 3]
        rgba = np.stack([r, g, b, a], axis=-1)
    elif tfmt == 0x85000000:  # DXT1 / BC1 (4 bpp)
        pw_tex = next_pow2(w)
        ph_tex = next_pow2(h)
        bw = max(1, pw_tex // 4)
        bh = max(1, ph_tex // 4)
        expected_bytes = bw * bh * 8

        if len(data) < expected_bytes:
            data = data + b"\x00" * (expected_bytes - len(data))

        if (ttype & 0x60000000) != 0:
            linear_blocks = unswizzle_blocks(data, bw, bh, 8)
        else:
            linear_blocks = data[:expected_bytes]

        img_dec = Image.frombytes("RGBA", (bw * 4, bh * 4), linear_blocks, "bcn", 1).crop((0, 0, w, h))
        rgba = np.array(img_dec, dtype=np.uint8)

    elif tfmt == 0x82000000:  # PVRTC 2bpp (2 bpp — _sheetClouds.gxt)
        import texture2ddecoder
        dec_bgra = texture2ddecoder.decode_pvrtc(data, w, h, True)
        arr = np.frombuffer(dec_bgra, dtype=np.uint8).reshape((h, w, 4))
        rgba = np.stack([arr[..., 2], arr[..., 1], arr[..., 0], arr[..., 3]], axis=-1)

    else:
        return None

    os.makedirs(os.path.dirname(os.path.abspath(out_png_path)), exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(out_png_path)
    return rgba

def decode_vita_atlas(vita_images_dir: str, base_name: str, out_png_dir: str) -> np.ndarray:
    idx_path = os.path.join(vita_images_dir, f"{base_name}_indices.gxt")
    pal_path = os.path.join(vita_images_dir, f"{base_name}_palette.gxt")
    _, iraw, isz, _, _, w, h = read_gxt(idx_path)
    _, praw, _, p_type, _, pw, ph = read_gxt(pal_path)

    pal_src = np.frombuffer(praw[:pw * ph * 4], dtype=np.uint8).reshape((-1, 4))
    if p_type == 0x00000000 and pw > 1 and ph > 1:
        pal_2d = np.zeros((ph, pw, 4), dtype=np.uint8)
        k = int(np.log2(min(pw, ph)))
        for y in range(ph):
            for x in range(pw):
                m = 0
                for i in range(k):
                    m |= ((y >> i) & 1) << (2 * i)
                    m |= ((x >> i) & 1) << (2 * i + 1)
                high = ((y >> k) * (pw >> k) + (x >> k)) << (2 * k)
                pal_2d[y, x] = pal_src[high | m]
    else:
        pal_2d = pal_src.reshape((ph, pw, 4))

    b, g, r, a = pal_2d[..., 0], pal_2d[..., 1], pal_2d[..., 2], pal_2d[..., 3]
    pal_rgba = np.stack([r, g, b, a], axis=-1)

    stride = isz // h
    rows = np.frombuffer(iraw[:isz], dtype=np.uint8).reshape((h, stride))
    if stride >= w * 2:
        valid = rows[:, :w * 2].copy()
        u16 = np.frombuffer(valid.tobytes(), dtype=np.uint16).reshape((h, w))
        # Нормализованные UV-координаты палитры (0..255 по X -> 0..pw-1, 0..127 по Y -> 0..ph-1)
        px = np.clip(((u16 & 0xFF).astype(np.uint32) * pw) >> 8, 0, pw - 1)
        py = np.clip(((u16 >> 9).astype(np.uint32) * ph) >> 7, 0, ph - 1)
        rgba = pal_rgba[py, px]
        rgba[u16 == 0] = (0, 0, 0, 0)
    else:
        idx = rows[:, :w]
        rgba = pal_rgba.reshape((-1, 4))[idx]
        rgba[idx == 0] = (0, 0, 0, 0)

    os.makedirs(out_png_dir, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(os.path.join(out_png_dir, f"{base_name}.png"))
    return rgba


def decode_all_atlases(vita_dir: str, out_png_dir: str, log_cb=None) -> dict:
    """Рекурсивно распаковывает ВСЕ графические GXT из всех папок data.vfs."""
    result = {}
    if not os.path.exists(vita_dir):
        return result

    for root, _, files in os.walk(vita_dir):
        gxt_files = sorted([f for f in files if f.lower().endswith(".gxt")])
        if not gxt_files:
            continue

        rel_dir = os.path.relpath(root, vita_dir)
        target_sub_dir = out_png_dir if rel_dir == "." else os.path.join(out_png_dir, rel_dir)

        for f in gxt_files:
            if f.endswith("_indices.gxt"):
                base = f[:-12]
                if os.path.exists(os.path.join(root, f"{base}_palette.gxt")):
                    if log_cb:
                        log_cb(f"Decoding palette sheet {base}...")
                    arr = decode_vita_atlas(root, base, target_sub_dir)
                    if rel_dir == "images":
                        for k, v in ATLAS_SHEETS.items():
                            if v == base:
                                result[k] = arr

        for f in gxt_files:
            if f.endswith("_indices.gxt") or "_palette" in f:
                continue
            base = f[:-4]
            rel_label = f if rel_dir == "." else f"{rel_dir}/{f}"
            if log_cb:
                log_cb(f"Decoding {rel_label}...")
            decode_single_gxt(os.path.join(root, f), os.path.join(target_sub_dir, f"{base}.png"))

    return result

def prepare_sprite_patch(src_arr: np.ndarray, info: dict) -> np.ndarray:
    sh_src, sw_src, _ = src_arr.shape
    tw, th = info["w"], info["h"]
    ox = info.get("src_x", 0)
    oy = info.get("src_y", 0)
    rot90 = info.get("rot90", False)

    sub = src_arr[min(oy, max(0, sh_src - 1)):, min(ox, max(0, sw_src - 1)):]
    if sub.shape.__getitem__(0) == 0 or sub.shape.__getitem__(1) == 0:
        return np.zeros((th, tw, 4), dtype=np.uint8)

    if "src_w" in info and "src_h" in info:
        crop = sub[:info["src_h"], :info["src_w"]]
    else:
        ys, xs = np.nonzero(sub[..., 3] > 128)
        if len(xs) == 0:
            return np.zeros((th, tw, 4), dtype=np.uint8)
        if th <= tw * 1.5 and sub.shape.__getitem__(0) <= sub.shape.__getitem__(1) * 1.5:
            crop = sub[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        else:
            crop = sub[:th, :tw] if not rot90 else sub[:tw, :th]

    if rot90:
        crop = np.rot90(crop)

    ch, cw, _ = crop.shape
    if ch == 0 or cw == 0:
        return np.zeros((th, tw, 4), dtype=np.uint8)

    if cw > tw or ch > th:
        scale = min(tw / cw, th / ch)
        nw = max(1, int(round(cw * scale)))
        nh = max(1, int(round(ch * scale)))
        crop = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_NEAREST)
        ch, cw, _ = crop.shape

    canvas = np.zeros((th, tw, 4), dtype=np.uint8)
    px0 = max(0, (tw - cw) // 2)
    py0 = max(0, (th - ch) // 2)
    canvas[py0 : py0 + ch, px0 : px0 + cw] = crop[:th - py0, :tw - px0]
    return canvas


def pack_32bit_direct(vanilla_gxt: str, src_png: str, out_gxt: str) -> None:
    """Записывает PNG в одиночный 32-bit GXT (сохраняя родной заголовок и stride для BGRA8888)."""
    hdr, raw, sz, ttype, tfmt, w, h = read_gxt(vanilla_gxt)
    img = Image.open(src_png).convert("RGBA").resize((w, h), Image.Resampling.LANCZOS)
    arr = np.array(img, dtype=np.uint8)
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    bgra = np.stack([b, g, r, a], axis=-1)

    if tfmt == 0x0c001000 and sz >= w * h * 4:
        # Оригинал уже 32-bit BGRA (все логотипы и UI) — пишем построчно с родным stride, не трогая заголовок!
        stride = sz // h
        for y in range(h):
            raw[y * stride : y * stride + w * 4] = bgra[y].tobytes()
        with open(out_gxt, "wb") as f:
            f.write(hdr)
            f.write(raw)
    else:
        # Для бывших сжатых текстур (DXT1/PVRTC) пишем выровненный Linear BGRA8888
        aligned_w = (w + 7) & ~7
        stride = aligned_w * 4
        new_data = bytearray(stride * h)
        for y in range(h):
            new_data[y * stride : y * stride + w * 4] = bgra[y].tobytes()
        struct.pack_into("<I", hdr, 16, len(new_data))
        struct.pack_into("<I", hdr, 36, len(new_data))
        struct.pack_into("<II", hdr, 48, 0x00000000, 0x0c001000)
        with open(out_gxt, "wb") as f:
            f.write(hdr)
            f.write(new_data)






def pack_8bit_single_bg(vanilla_base: str, src_png: str, out_base: str) -> None:
    idx_hdr, idx_orig, idx_sz, _, _, w, h = read_gxt(f"{vanilla_base}_indices.gxt")
    pal_hdr, _, _, _, _, _, _ = read_gxt(f"{vanilla_base}_palette.gxt")
    img = Image.open(src_png).convert("RGBA").resize((w, h), Image.Resampling.NEAREST)
    arr = np.array(img, dtype=np.uint8)
    alpha_mask = arr[..., 3] < 128

    rgb_q = Image.fromarray(arr[..., :3], "RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    indices = np.array(rgb_q, dtype=np.uint8) + 1
    indices[alpha_mask] = 0

    raw_pal = rgb_q.getpalette()
    pal_bgra = np.zeros((256, 4), dtype=np.uint8)
    for i in range(255):
        pal_bgra[i + 1] = (raw_pal[i * 3 + 2], raw_pal[i * 3 + 1], raw_pal[i * 3], 255)

    stride = idx_sz // h
    out_idx = bytearray(idx_orig[:idx_sz])
    for y in range(h):
        out_idx[y * stride : y * stride + w] = indices[y].tobytes()

    with open(f"{out_base}_indices.gxt", "wb") as f:
        f.write(idx_hdr)
        f.write(out_idx)
    with open(f"{out_base}_palette.gxt", "wb") as f:
        f.write(pal_hdr)
        f.write(pal_bgra.tobytes())

def apply_texture_pack(tp_dir: str, vita_dir: str, unpacked_dir: str, out_dir: str, atlas_map: dict, log_cb, prog_cb) -> None:
    edits = {k: [] for k in ATLAS_SHEETS}
    for fname, info in atlas_map.items():
        p = os.path.join(tp_dir, fname) if tp_dir else ""
        sname = info.get("sheet", "")
        if sname in edits and p and os.path.exists(p):
            edits[sname].append((fname, p, info))

    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(vita_dir, out_dir)

    step_idx = 0
    total_steps = sum(len(v) for v in edits.values()) + 1

    # 1. 16-битные палитровые атласы (sprites, tiles, walls, solid, back)
    for sname in ("sprites", "tiles", "walls", "solid", "back"):
        items = edits[sname]
        base_gxt = ATLAS_SHEETS[sname]
        idx_in = os.path.join(vita_dir, "images", f"{base_gxt}_indices.gxt")
        if not items or not os.path.exists(idx_in):
            continue

        idx_hdr, idx_raw, idx_sz, _, _, w, h = read_gxt(idx_in)
        pal_hdr, pal_raw, _, _, _, pw, ph = read_gxt(os.path.join(vita_dir, "images", f"{base_gxt}_palette.gxt"))

        stride = idx_sz // h
        rows = np.frombuffer(idx_raw[:idx_sz], dtype=np.uint8).reshape((h, stride)).copy()
        u16 = np.frombuffer(rows[:, :w * 2].tobytes(), dtype=np.uint16).reshape((h, w)).copy()

        shift_x = int(np.log2(pw))
        max_used_row = int((((u16 >> 9).astype(np.uint32) * ph) >> 7).max())
        start_row = min(ph - 1, max_used_row + 2)

        color_to_u16 = {}
        state = {"row": start_row, "col": 1}

        def alloc_color(r, g, b, a):
            key = (int(r), int(g), int(b), int(a))
            if key in color_to_u16:
                return color_to_u16[key]
            if state["row"] >= ph:
                arr_keys = np.array(list(color_to_u16.keys()), dtype=np.int32)
                diffs = np.abs(arr_keys - np.array(key, dtype=np.int32)).sum(axis=1)
                return color_to_u16[tuple(arr_keys[int(np.argmin(diffs))].tolist())]

            off = morton_offset(state["row"] * pw + state["col"], pw, ph)
            pal_raw[off : off + 4] = struct.pack("<4B", b, g, r, a)
            norm_x = min(255, (state["col"] * 256) // pw)
            norm_y = min(127, (state["row"] * 128) // ph)
            encoded = (norm_y << 9) | norm_x
            color_to_u16[key] = encoded
            state["col"] += 1
            if state["col"] >= pw:
                state["col"] = 1
                state["row"] += 1
            return encoded

        for fname, mod_path, info in items:
            step_idx += 1
            if step_idx % 40 == 0:
                prog_cb(step_idx, total_steps)
            src_arr = np.array(Image.open(mod_path).convert("RGBA"))
            sh_src, sw_src, _ = src_arr.shape

            if info["type"] == "grid":
                for slot in info["slots"]:
                    sx, sy, vx, vy, csz = slot["src_x"], slot["src_y"], slot["vita_x"], slot["vita_y"], slot["size"]
                    src_sz = slot.get("src_size", csz)
                    if sy + src_sz > sh_src or sx + src_sz > sw_src:
                        continue
                    raw_patch = src_arr[sy : sy + src_sz, sx : sx + src_sz]
                    patch = cv2.resize(raw_patch, (csz, csz), interpolation=cv2.INTER_NEAREST) if src_sz != csz else raw_patch
                    for py in range(csz):
                        if vy + py >= h:
                            break
                        for px in range(csz):
                            if vx + px >= w:
                                break
                            r, g, b, a = patch[py, px]
                            u16[vy + py, vx + px] = 0 if a < 128 else alloc_color(r, g, b, 255)
            else:
                tw, th = info["w"], info["h"]
                vx, vy = info["vita_x"], info["vita_y"]
                patch = prepare_sprite_patch(src_arr, info)
                for py in range(th):
                    if vy + py >= h:
                        break
                    for px in range(tw):
                        if vx + px >= w:
                            break
                        r, g, b, a = patch[py, px]
                        u16[vy + py, vx + px] = 0 if a < 128 else alloc_color(r, g, b, 255)

        rows[:, :w * 2] = np.frombuffer(u16.tobytes(), dtype=np.uint8).reshape((h, w * 2))
        idx_raw[:idx_sz] = rows.tobytes()

        with open(os.path.join(out_dir, "images", f"{base_gxt}_indices.gxt"), "wb") as f:
            f.write(idx_hdr)
            f.write(idx_raw)
        with open(os.path.join(out_dir, "images", f"{base_gxt}_palette.gxt"), "wb") as f:
            f.write(pal_hdr)
            f.write(pal_raw)
        log_cb(f"[OK] {base_gxt}: patched {len(items)} textures ({len(color_to_u16)} colors)")

    # 2. 8-битный атлас NPC
    npc_items = edits["npc"]
    npc_png_path = os.path.join(unpacked_dir, "_sheetNPC.png")
    if npc_items and os.path.exists(npc_png_path):
        npc_canvas = Image.open(npc_png_path).convert("RGBA")
        for fname, mod_path, info in npc_items:
            step_idx += 1
            src_arr = np.array(Image.open(mod_path).convert("RGBA"))
            patch_arr = prepare_sprite_patch(src_arr, info)
            patch_im = Image.fromarray(patch_arr, "RGBA")
            vx, vy, tw, th = info["vita_x"], info["vita_y"], info["w"], info["h"]
            npc_canvas.paste((0, 0, 0, 0), (vx, vy, vx + tw, vy + th))
            npc_canvas.paste(patch_im, (vx, vy), patch_im)

        arr = np.array(npc_canvas, dtype=np.uint8)
        alpha_mask = arr[..., 3] < 128
        rgb_q = Image.fromarray(arr[..., :3], "RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        indices = np.array(rgb_q, dtype=np.uint8) + 1
        indices[alpha_mask] = 0

        raw_pal = rgb_q.getpalette()
        pal_bgra = np.zeros((256, 4), dtype=np.uint8)
        for i in range(255):
            pal_bgra[i + 1] = (raw_pal[i * 3 + 2], raw_pal[i * 3 + 1], raw_pal[i * 3], 255)

        idx_hdr, idx_orig, idx_sz, _, _, w, h = read_gxt(os.path.join(vita_dir, "images", "_sheetNPC_indices.gxt"))
        pal_hdr, _, _, _, _, _, _ = read_gxt(os.path.join(vita_dir, "images", "_sheetNPC_palette.gxt"))

        stride = idx_sz // h
        out_idx = bytearray(idx_orig[:idx_sz])
        for y in range(h):
            out_idx[y * stride : y * stride + w] = indices[y].tobytes()

        with open(os.path.join(out_dir, "images", "_sheetNPC_indices.gxt"), "wb") as f:
            f.write(idx_hdr)
            f.write(out_idx)
        with open(os.path.join(out_dir, "images", "_sheetNPC_palette.gxt"), "wb") as f:
            f.write(pal_hdr)
            f.write(pal_bgra.tobytes())
        log_cb(f"[OK] _sheetNPC: patched {len(npc_items)} bosses & NPCs")

    # 3. Автоподхват логотипов и фонов 93/94/180 из ПК-пака
    if tp_dir and os.path.exists(tp_dir):
        tp_files_lower = {f.lower(): f for f in os.listdir(tp_dir)}
        for png_low, gxt_name in [("logo.png", "logo.gxt"), ("logo2.png", "logo2.gxt"), ("logo_1.png", "logo_1.gxt"), ("logo_2.png", "logo_2.gxt"), ("logo_3.png", "logo_3.gxt")]:
            v_gxt = os.path.join(vita_dir, "images", gxt_name)
            if png_low in tp_files_lower and os.path.exists(v_gxt):
                pack_32bit_direct(v_gxt, os.path.join(tp_dir, tp_files_lower[png_low]), os.path.join(out_dir, "images", gxt_name))
                log_cb(f"[OK] Logo {gxt_name} updated")

        for bg_id in ("93", "94", "180"):
            png_low = f"background_{bg_id}.png"
            v_base = os.path.join(vita_dir, "images", f"_sheetBack{bg_id}")
            if png_low in tp_files_lower and os.path.exists(f"{v_base}_indices.gxt"):
                pack_8bit_single_bg(v_base, os.path.join(tp_dir, tp_files_lower[png_low]), os.path.join(out_dir, "images", f"_sheetBack{bg_id}"))
                log_cb(f"[OK] Background _sheetBack{bg_id} updated")

    # 4. Ручные замены любых полных листов из workspace/custom_sheets (включая вложенные папки ui/ и др.)
    custom_sheets_dir = os.path.join(os.path.dirname(os.path.abspath(out_dir)), "custom_sheets")
    if os.path.exists(custom_sheets_dir):
        for root, _, files in os.walk(custom_sheets_dir):
            rel_dir = os.path.relpath(root, custom_sheets_dir)
            for fname in files:
                if not fname.endswith(".png"):
                    continue
                sheet_key = fname[:-4]
                custom_png = os.path.join(root, fname)
                
                v_sub_dir = vita_dir if rel_dir == "." else os.path.join(vita_dir, rel_dir)
                out_sub_dir = out_dir if rel_dir == "." else os.path.join(out_dir, rel_dir)

                v_pal_base = os.path.join(v_sub_dir, sheet_key)
                v_single_gxt = os.path.join(v_sub_dir, f"{sheet_key}.gxt")
                
                if os.path.exists(f"{v_pal_base}_indices.gxt"):
                    pack_8bit_single_bg(v_pal_base, custom_png, os.path.join(out_sub_dir, sheet_key))
                    log_cb(f"[OK] Custom palette sheet {sheet_key} applied")
                elif os.path.exists(v_single_gxt):
                    pack_32bit_direct(v_single_gxt, custom_png, os.path.join(out_sub_dir, f"{sheet_key}.gxt"))
                    log_cb(f"[OK] Custom standalone sheet {sheet_key}.gxt applied")

    prog_cb(total_steps, total_steps)



