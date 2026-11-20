import os
import json
import time
import cv2
import numpy as np
from PIL import Image
import gxt_engine

cv2.setNumThreads(1)

def split_vertical_frames(alpha_bool: np.ndarray) -> int:
    h, w = alpha_bool.shape
    if h <= w * 1.5:
        return h
    row_sums = alpha_bool.sum(axis=1)
    in_sprite = False
    for y in range(h):
        if row_sums[y] > 0:
            in_sprite = True
        elif in_sprite and row_sums[y] == 0:
            if y >= 16 and (h % y == 0 or h % (y + 2) == 0):
                return y if h % y == 0 else y + 2
    return h

def run_auto_indexer(primary_dir: str, secondary_dir: str, vita_dir: str, unpacked_dir: str, out_map_path: str, log_cb, prog_cb) -> dict:
    vita_rgba = gxt_engine.decode_all_atlases(vita_dir, unpacked_dir, log_cb)
    occupied = {k: np.zeros(img.shape[:2], dtype=bool) for k, img in vita_rgba.items()}
    raw_alpha = {k: (img[..., 3] > 128) for k, img in vita_rgba.items()}

    mapping = {}
    stats = {k: 0 for k in vita_rgba}

    sources = []
    if primary_dir and os.path.exists(primary_dir):
        sources.append(("Primary Reference", primary_dir))
    if secondary_dir and os.path.exists(secondary_dir):
        sources.append(("Secondary Reference", secondary_dir))

    if not sources:
        raise ValueError("Please specify at least one valid reference folder.")

    # Хеш-таблицы для тайлов (16x16) и стен (16x16 и 32x32)
    grid_hashes = {}
    for sname, sizes in (("tiles", (16,)), ("walls", (16, 32))):
        if sname not in vita_rgba:
            continue
        v_img = vita_rgba[sname]
        v_alp = raw_alpha[sname]
        vh, vw, _ = v_img.shape
        v_quant = (v_img[..., :3] >> 4).astype(np.uint8)
        for csize in sizes:
            hmap = {}
            pad = csize // 4
            for vy in range(0, vh - csize + 1, 2):
                for vx in range(0, vw - csize + 1, 2):
                    if v_alp[vy : vy + csize, vx : vx + csize].sum() >= (csize * csize // 8):
                        key = v_quant[vy + pad : vy + csize - pad, vx + pad : vx + csize - pad].tobytes()
                        hmap.setdefault(key, []).append((vx, vy))
            grid_hashes[(sname, csize)] = hmap

    # Связные островки для sprites, npc, solid, back
    islands_by_size = {}
    for sname in ("sprites", "npc", "solid", "back"):
        if sname not in vita_rgba:
            continue
        num_labels, _, stats_cc, _ = cv2.connectedComponentsWithStats(raw_alpha[sname].astype(np.uint8), connectivity=4)
        by_size = {}
        for lbl in range(1, num_labels):
            ix, iy, iw, ih, area = [int(v) for v in stats_cc[lbl]]
            if area >= 20:
                by_size.setdefault((iw, ih), []).append((ix, iy))
        islands_by_size[sname] = by_size

    for src_label, src_dir in sources:
        log_cb(f"\n=== Indexing pass: {src_label} ===")
        all_files = sorted([f for f in os.listdir(src_dir) if f.endswith(".png")])

        # 1. Блоки и стены
        for prefix, sname, src_csize, src_step in (("Tiles_", "tiles", 16, 18), ("Wall_", "walls", 32, 36)):
            if sname not in vita_rgba:
                continue
            v_img = vita_rgba[sname]
            v_occ = occupied[sname]
            grid_files = [f for f in all_files if f.startswith(prefix) and f not in mapping]

            for fname in grid_files:
                time.sleep(0.008)
                pc_arr = np.array(Image.open(os.path.join(src_dir, fname)).convert("RGBA"))
                gh, gw, _ = pc_arr.shape
                sub_slots = []

                for gy in range(0, gh - src_csize + 1, src_step):
                    for gx in range(0, gw - src_csize + 1, src_step):
                        raw_cell = pc_arr[gy : gy + src_csize, gx : gx + src_csize]
                        if (raw_cell[..., 3] > 128).sum() < (src_csize * src_csize // 8):
                            continue

                        try_sizes = (16, 32) if sname == "walls" else (16,)
                        for target_sz in try_sizes:
                            cell = cv2.resize(raw_cell, (target_sz, target_sz), interpolation=cv2.INTER_NEAREST) if target_sz != src_csize else raw_cell
                            c_mask = cell[..., 3] > 128
                            c_quant = (cell[..., :3] >> 4).astype(np.uint8)
                            pad = target_sz // 4
                            cands = grid_hashes[(sname, target_sz)].get(c_quant[pad : target_sz - pad, pad : target_sz - pad].tobytes())
                            if not cands:
                                continue

                            best_pos, best_err = None, 38.0
                            for vx, vy in cands[:12]:
                                if v_occ[vy : vy + target_sz, vx : vx + target_sz].any():
                                    continue
                                v_patch = v_img[vy : vy + target_sz, vx : vx + target_sz]
                                v_mask = v_patch[..., 3] > 128
                                iou = np.logical_and(c_mask, v_mask).sum() / max(1, np.logical_or(c_mask, v_mask).sum())
                                if iou < 0.85:
                                    continue
                                err = np.abs(v_patch[c_mask, :3].astype(np.int32) - cell[c_mask, :3].astype(np.int32)).mean()
                                if err < best_err:
                                    best_err, best_pos = err, (vx, vy)

                            if best_pos is not None:
                                vx, vy = best_pos
                                v_occ[vy : vy + target_sz, vx : vx + target_sz] = True
                                sub_slots.append({
                                    "src_x": int(gx), "src_y": int(gy), "src_size": src_csize,
                                    "vita_x": int(vx), "vita_y": int(vy), "size": target_sz
                                })
                                break

                if sub_slots:
                    mapping[fname] = {"type": "grid", "sheet": sname, "slots": sub_slots}
                    stats[sname] += len(sub_slots)

        # 2. Предметы, NPC, мебель (_sheetSolid) и фоны (_sheetBack)
        tasks = []
        for fname in all_files:
            if fname in mapping:
                continue
            if fname.startswith(("Item_", "Projectile_", "Gore_", "Acc_", "UI_")):
                target_sheets = ("sprites",)
            elif fname.startswith("NPC_"):
                target_sheets = ("npc",)
            elif fname.startswith("Tiles_"):
                target_sheets = ("solid",)
            elif fname.startswith("Background_"):
                target_sheets = ("back",)
            else:
                continue

            pc_arr = np.array(Image.open(os.path.join(src_dir, fname)).convert("RGBA"))
            ph_img, pw_img, _ = pc_arr.shape
            alpha_b = pc_arr[..., 3] > 128
            fh = split_vertical_frames(alpha_b)
            ys, xs = np.nonzero(alpha_b[:fh, :])
            if len(xs) == 0:
                continue

            x0, x1, y0, y1 = int(xs.min()), int(xs.max() + 1), int(ys.min()), int(ys.max() + 1)
            crop_rgba = pc_arr[y0:y1, x0:x1]
            ch, cw, _ = crop_rgba.shape
            if ch * cw >= 25:
                est_frames = max(1, ph_img // fh)
                tasks.append((ch * cw * est_frames, fname, target_sheets, x0, y0, crop_rgba, int(fh), int(ph_img)))

        tasks.sort(key=lambda item: -item.__getitem__(0))
        log_cb(f"Scanning {len(tasks)} sprites from {src_label}...")

        for idx_t, (_, fname, target_sheets, ox, oy, crop_rgba, frame_h, total_pc_h) in enumerate(tasks):
            time.sleep(0.006)
            if idx_t % 100 == 0:
                prog_cb(idx_t, len(tasks))

            for sname in target_sheets:
                if sname not in vita_rgba or fname in mapping:
                    continue
                v_img = vita_rgba[sname]
                v_occ = occupied[sname]
                v_alp = raw_alpha[sname]
                vh, vw, _ = v_img.shape
                by_size = islands_by_size[sname]

                rots = [(False, crop_rgba)]
                if sname == "sprites" and crop_rgba.shape.__getitem__(0) != crop_rgba.shape.__getitem__(1):
                    rots.append((True, np.rot90(crop_rgba)))

                best_match, best_score = None, 999.0
                for rot_flag, tpl in rots:
                    th, tw, _ = tpl.shape
                    cands = by_size.get((tw, th))
                    if not cands:
                        continue
                    tpl_mask_b = tpl[..., 3] > 128

                    for lx, ly in cands:
                        if v_occ[ly : ly + th, lx : lx + tw].any():
                            continue
                        v_patch = v_img[ly : ly + th, lx : lx + tw]
                        v_mask = v_patch[..., 3] > 128
                        iou = np.logical_and(tpl_mask_b, v_mask).sum() / max(1, np.logical_or(tpl_mask_b, v_mask).sum())
                        if iou < 0.88:
                            continue

                        err = np.abs(v_patch[tpl_mask_b, :3].astype(np.int32) - tpl[tpl_mask_b, :3].astype(np.int32)).mean()
                        # Штраф, если выше на frame_h есть похожий кадр (защита от попадания в середину анимации)
                        penalty = 0.0
                        if not rot_flag and total_pc_h > frame_h and ly >= frame_h:
                            above_alp = v_alp[ly - frame_h : ly - frame_h + th, lx : lx + tw]
                            if above_alp.shape == tpl_mask_b.shape:
                                above_iou = np.logical_and(tpl_mask_b, above_alp).sum() / max(1, np.logical_or(tpl_mask_b, above_alp).sum())
                                if above_iou > 0.65:
                                    penalty = 35.0

                        score = err + penalty
                        if err < 45.0 and score < best_score:
                            best_score = score
                            best_match = (lx, ly, tw, th, rot_flag, tpl_mask_b)

                if best_match is not None:
                    lx, ly, tw, th, rot_flag, first_mask = best_match
                    vita_slot_h = th
                    if not rot_flag and total_pc_h > frame_h:
                        matched_f = 1
                        for f_i in range(1, total_pc_h // frame_h):
                            ny = ly + f_i * frame_h
                            if ny + th > vh or v_occ[ny : ny + th, lx : lx + tw].any():
                                break
                            next_alp = v_alp[ny : ny + th, lx : lx + tw]
                            iou = np.logical_and(first_mask, next_alp).sum() / max(1, np.logical_or(first_mask, next_alp).sum())
                            if iou >= 0.55:
                                matched_f += 1
                            else:
                                break
                        vita_slot_h = matched_f * frame_h

                    v_occ[ly : ly + vita_slot_h, lx : lx + tw] = True
                    mapping[fname] = {
                        "type": "sprite", "sheet": sname, "vita_x": lx, "vita_y": ly,
                        "w": tw, "h": vita_slot_h, "src_x": ox, "src_y": oy, "rot90": rot_flag
                    }
                    stats[sname] += 1

    with open(out_map_path, "w", encoding="utf-8") as out:
        json.dump(mapping, out, indent=2)
    prog_cb(100, 100)
    return stats

