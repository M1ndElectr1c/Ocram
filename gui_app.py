import os
import json
import shutil
import threading
import cv2
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk, ImageDraw

import str_handler
import vfs_handler
import gxt_engine
import index_builder
import audio_vag

CONFIG_FILE = "ocram_config.json"
MAP_FILE = "vita_atlas_map.json"
NAMES_FILE = "terraria_names.json"


ALL_EDITOR_SHEETS = {
    "sprites": "_sheetSprites",
    "tiles":   "_sheetTiles",
    "walls":   "_sheetWalls",
    "npc":     "_sheetNPC",
    "solid":   "_sheetSolid",
    "back":    "_sheetBack",
    "back93":  "_sheetBack93",
    "back94":  "_sheetBack94",
    "back180": "_sheetBack180",
    "logo":    "logo",
    "logo2":   "logo2",
    "logo_1":  "logo_1",
    "logo_2":  "logo_2",
    "logo_3":  "logo_3",
}


DEFAULT_CFG = {
    "vanilla_vfs": "",
    "workspace_dir": "workspace",
    "pc_pack_dir": "",
    "out_vfs": "data.vfs",
    "ref_primary": "",
    "ref_secondary": "",
    "remind_audio_tools": True
}

def scale_pixel_art(pil_img, max_side=130):
    w, h = pil_img.size
    if w == 0 or h == 0:
        return pil_img
    if h > w * 2:
        pil_img = pil_img.crop((0, 0, w, min(h, int(w * 1.6))))
        w, h = pil_img.size
    scale = max(1, min(4, max_side // max(w, h)))
    return pil_img.resize((w * scale, h * scale), Image.Resampling.NEAREST)

class OcramApp:
    def __init__(self, root):
        self.root = root
        self.root.title("OCRAM — Old-gen Console Resource & Asset Manager")
        self.root.geometry("1260x800")

        # Поддержка пользовательской иконки icon.png рядом с main.py
        if os.path.exists("icon.gif"):
            try:
                icon_img = ImageTk.PhotoImage(Image.open("icon.gif"))
                self.root.iconphoto(True, icon_img)
                self._icon_ref = icon_img
            except Exception:
                pass

        self.cfg = DEFAULT_CFG.copy()
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                self.cfg.update(json.load(f))

        self.atlas_map = {}
        if os.path.exists(MAP_FILE):
            with open(MAP_FILE, "r", encoding="utf-8") as f:
                self.atlas_map = json.load(f)

        self.names_db = {}
        if os.path.exists(NAMES_FILE):
            with open(NAMES_FILE, "r", encoding="utf-8") as f:
                self.names_db = json.load(f)

        self.cur_sheet = "sprites"
        self.zoom = 1
        self.sel_fname = None
        self.sel_rect_id = None
        self.base_pil = None
        self.base_rgba = None
        self.alpha_np = None

        self.build_main_layout()
        # Автоматическая подгрузка распакованных данных с прошлого запуска
        self.root.after(100, self.auto_load_workspace)

    def refresh_sheet_dropdown(self):
        unpacked_dir = os.path.join(self.cfg["workspace_dir"], "unpacked_png")
        if os.path.exists(unpacked_dir):
            sheets = []
            for root, _, files in os.walk(unpacked_dir):
                rel_dir = os.path.relpath(root, unpacked_dir)
                for f in files:
                    if f.lower().endswith(".png"):
                        name = f[:-4]
                        full_key = name if rel_dir == "." else f"{rel_dir.replace(os.sep, '/')}/{name}"
                        sheets.append(full_key)
            sheets.sort()
            if sheets:
                self.cb_sheet["values"] = sheets

    def auto_load_workspace(self):
        ws = self.cfg["workspace_dir"]
        if os.path.exists(os.path.join(ws, "unpacked_png")):
            self.refresh_sheet_dropdown()
            vals = self.cb_sheet["values"]
            default_sheet = "images/_sheetSprites" if "images/_sheetSprites" in vals else vals[0]
            self.cb_sheet.set(default_sheet)
            self.load_editor_sheet(default_sheet)
        if os.path.exists(os.path.join(ws, "vanilla")):
            self.refresh_audio_list()
            self.refresh_str_dropdown()
        self.refresh_sprite_list()



    def save_cfg(self):
        for k, v in self.vars.items():
            self.cfg[k] = v.get()
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2, ensure_ascii=False)

    def build_main_layout(self):
        self.vars = {k: tk.StringVar(value=v) for k, v in self.cfg.items()}

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        

        self.tab_build = ttk.Frame(self.notebook)
        self.tab_editor = ttk.Frame(self.notebook)
        self.tab_audio = ttk.Frame(self.notebook)
        self.tab_text = ttk.Frame(self.notebook)
        self.tab_indexer = ttk.Frame(self.notebook)
        self.tab_guide = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_build, text="  Quick Port  ")
        self.notebook.add(self.tab_editor, text="  Sprite Editor  ")
        self.notebook.add(self.tab_audio, text="  Audio & Music  ")
        self.notebook.add(self.tab_text, text="  Text Editor (.STR)  ")
        self.notebook.add(self.tab_indexer, text="  Auto-Indexer  ")
        self.notebook.add(self.tab_guide, text="  Guide  ")

        self.build_tab_quick_port()
        self.build_tab_editor()
        self.build_tab_audio()
        self.build_tab_text()
        self.build_tab_indexer()
        self.build_tab_guide()


    # ================= TAB 1: QUICK PORT =================
    def build_tab_quick_port(self):
        pad = {"padx": 12, "pady": 6}
        f_step1 = ttk.LabelFrame(self.tab_build, text="1. PS Vita Base Archive (data.vfs)")
        f_step1.pack(fill="x", **pad)

        ttk.Label(f_step1, text="data.vfs:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(f_step1, textvariable=self.vars["vanilla_vfs"], width=72).grid(row=0, column=1, padx=4)
        ttk.Button(f_step1, text="Browse...", command=self.browse_vfs).grid(row=0, column=2, padx=4)
        ttk.Button(f_step1, text="Extract Base", command=self.start_extract_base).grid(row=0, column=3, padx=6)

        f_step2 = ttk.LabelFrame(self.tab_build, text="2. PC Texture Pack")
        f_step2.pack(fill="x", **pad)

        ttk.Label(f_step2, text="Pack Folder:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(f_step2, textvariable=self.vars["pc_pack_dir"], width=72).grid(row=0, column=1, padx=4)
        ttk.Button(f_step2, text="Browse...", command=lambda: self.browse_dir("pc_pack_dir")).grid(row=0, column=2, padx=4)

        f_step3 = ttk.LabelFrame(self.tab_build, text="3. Export Mod")
        f_step3.pack(fill="x", **pad)

        ttk.Label(f_step3, text="Output File:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(f_step3, textvariable=self.vars["out_vfs"], width=40).grid(row=0, column=1, sticky="w", padx=4)
        self.var_auto_sounds = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_step3, text="Auto-convert Pack Sounds (.wav / .xnb)", variable=self.var_auto_sounds).grid(row=0, column=2, padx=8)
        ttk.Button(f_step3, text="Build VFS", command=self.start_build_mod).grid(row=0, column=3, padx=10, ipady=4, ipadx=12)


        self.prog_main = ttk.Progressbar(self.tab_build, mode="determinate")
        self.prog_main.pack(fill="x", padx=12, pady=4)

        self.log_box = tk.Text(self.tab_build, height=18, state="disabled", bg="#1a1a1a", fg="#e0e0e0", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=12, pady=6)

    # ================= TAB 4: AUTO-INDEXER =================
    def build_tab_indexer(self):
        pad = {"padx": 12, "pady": 6}
        note_box = ttk.LabelFrame(self.tab_indexer, text="How Auto-Indexing Works")
        note_box.pack(fill="x", **pad)
        ttk.Label(
            note_box,
            text="OCRAM ships with a ready-to-use vita_atlas_map.json, so standard users can skip this tab.\n\n"
                 "If you want to rebuild or expand the coordinate map from clean reference packs:\n"
                 "• Primary Reference: Matched first with highest priority (ideal for older/legacy sprite sheets that share original silhouettes).\n"
                 "• Secondary Reference: Used as a fallback to map any remaining sprites, tiles, or backgrounds not covered by the primary pack.",
            justify="left", wraplength=1180
        ).pack(anchor="w", padx=8, pady=6)

        f_ref = ttk.LabelFrame(self.tab_indexer, text="Reference Sprite Folders")
        f_ref.pack(fill="x", **pad)

        ttk.Label(f_ref, text="Primary Reference:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(f_ref, textvariable=self.vars["ref_primary"], width=72).grid(row=0, column=1, padx=4)
        ttk.Button(f_ref, text="Browse...", command=lambda: self.browse_dir("ref_primary")).grid(row=0, column=2, padx=6)

        ttk.Label(f_ref, text="Secondary Reference:").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(f_ref, textvariable=self.vars["ref_secondary"], width=72).grid(row=1, column=1, padx=4)
        ttk.Button(f_ref, text="Browse...", command=lambda: self.browse_dir("ref_secondary")).grid(row=1, column=2, padx=6)

        ttk.Button(self.tab_indexer, text="Run Auto-Indexer", command=self.start_indexer).pack(pady=10, ipady=5, ipadx=16)

    # ================= TAB 5: IN-APP GUIDE =================
    def build_tab_guide(self):
        txt = tk.Text(self.tab_guide, wrap="word", font=("Segoe UI", 10), padx=14, pady=12, relief="flat")
        txt.pack(fill="both", expand=True)
        guide_content = (
            "OCRAM — User Guide & Technical Overview\n"
            "========================================\n\n"
            "1. Why PS Vita and PC Texture Packs Differ\n"
            "• PC Terraria (1.4.x) stores every item, NPC, tile, and background as an individual PNG file.\n"
            "• PS Vita Terraria (based on Legacy 1.2.4) packs thousands of sprites tightly into shared GXT texture atlases "
            "(_sheetSprites, _sheetNPC, _sheetTiles, _sheetWalls, _sheetSolid, _sheetBack) using 8-bit and 16-bit indexed palettes.\n"
            "• Additionally, some sprites on PS Vita are rotated 90 degrees or have fewer animation frames than modern PC versions.\n"
            "• OCRAM bridges this gap using vita_atlas_map.json to automatically crop, rotate, downscale, quantize colors, and pack PC assets into PS Vita formats.\n\n"
            "2. Quick Port Workflow (3 Steps)\n"
            "• Step 1: Dump your decrypted data.vfs from PS Vita via VitaShell (Open decrypted -> ux0:patch/<TITLE_ID>/data.vfs), select it in 'Quick Port', and click 'Extract Base'.\n"
            "• Step 2: Select any PC Texture Pack folder (root folder or Content/Images). Matching sprites and sounds (.wav / .xnb) will be detected automatically.\n"
            "• Step 3: Enable 'Auto-convert Pack Sounds' if desired and click 'Build VFS' to generate your modded data.vfs, then place it into ux0:rePatch/<TITLE_ID>/data.vfs (or Vita3K).\n\n"
            "3. Using the Visual Sprite Editor\n"
            "• Switch between any unpacked GXT sheet or UI texture using the top Atlas dropdown.\n"
            "• Click any green box to inspect its mapped PC sprite, or click an unmapped sprite to auto-detect its contour.\n"
            "• Hold Ctrl + Mouse Wheel to zoom (0.25x–6.0x); use Mouse Wheel / Shift + Wheel to scroll.\n"
            "• Enable 'Overlay Preview' to preview modded sprites directly on the PS Vita sheet before building.\n"
            "• Use 'Replace Full Sheet...' on standalone sheets (logos, UI elements, single backgrounds) to replace them directly.\n\n"
            "4. Audio & Music Manager (.VAG / .AT9)\n"
            "• Sound effects (.vag) are encoded natively in Python from .wav or .xnb files.\n"
            "• When opening 'Audio & Music' for the first time, click 'Download Automatically' to install vgmstream-cli (for audio preview) and at9tool.exe (for .wav -> .at9 music encoding).\n"
            "• On Linux/macOS, OCRAM automatically runs at9tool.exe via 'mono' or falls back to 'wine'.\n"
            "• Select any sound or music track and click 'Replace Audio...' — OCRAM automatically converts and renames your file to match the target slot.\n\n"
            "5. Binary Text & Localization Editor (.STR)\n"
            "• Open 'Text Editor (.STR)' to search, edit, or export/import localization files (strings_*.str) to JSON with automatic offset recalculation."
        )

        txt.insert("1.0", guide_content)
        txt.configure(state="disabled")

    # ================= TAB 2: VISUAL SPRITE EDITOR =================
    def build_tab_editor(self):
        top = ttk.Frame(self.tab_editor)
        top.pack(fill="x", padx=8, pady=4)

        ttk.Label(top, text="Atlas:").pack(side="left")
        self.cb_sheet = ttk.Combobox(top, values=list(ALL_EDITOR_SHEETS.keys()), state="readonly", width=34)
        self.cb_sheet.set("sprites")
        self.cb_sheet.pack(side="left", padx=4)
        self.cb_sheet.bind("<<ComboboxSelected>>", self.on_sheet_combo_change)

        self.lbl_zoom = ttk.Label(top, text="Zoom: 1.00x", font=("Consolas", 9, "bold"))
        self.lbl_zoom.pack(side="left", padx=(8, 2))
        ttk.Button(top, text="1x", width=3, command=self.reset_zoom).pack(side="left", padx=2)

        self.var_boxes = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Show Boxes", variable=self.var_boxes, command=self.rebuild_editor_bg).pack(side="left", padx=8)

        self.var_overlay = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Overlay Preview", variable=self.var_overlay, command=self.rebuild_editor_bg).pack(side="left", padx=8)

        ttk.Button(top, text="Save Map", command=self.save_atlas_map).pack(side="right", padx=4)
        ttk.Button(top, text="Replace Full Sheet...", command=self.replace_full_sheet).pack(side="right", padx=4)

        pane = ttk.PanedWindow(self.tab_editor, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(pane)
        pane.add(left, weight=3)
        self.canvas = tk.Canvas(left, bg="#1e1e1e", cursor="crosshair")
        hb = ttk.Scrollbar(left, orient="horizontal", command=self.canvas.xview)
        vb = ttk.Scrollbar(left, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hb.set, yscrollcommand=vb.set)
        hb.pack(side="bottom", fill="x")
        vb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.canvas.bind("<Button-1>", self.on_editor_click)
        self.canvas.bind("<Control-MouseWheel>", self.on_ctrl_wheel)
        self.canvas.bind("<Control-Button-4>", lambda e: self.change_zoom(1.15))
        self.canvas.bind("<Control-Button-5>", lambda e: self.change_zoom(1 / 1.15))

        self.canvas.bind("<Shift-MouseWheel>", self.on_shift_wheel)
        self.canvas.bind("<Shift-Button-4>", lambda e: self.canvas.xview_scroll(-3, "units"))
        self.canvas.bind("<Shift-Button-5>", lambda e: self.canvas.xview_scroll(3, "units"))

        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))


        right = ttk.Frame(pane, width=400)
        pane.add(right, weight=1)

        ttk.Label(right, text="Search (Name or ID):").pack(anchor="w")
        self.var_q = tk.StringVar()
        self.var_q.trace_add("write", lambda *a: self.refresh_sprite_list())
        ttk.Entry(right, textvariable=self.var_q).pack(fill="x", pady=2)

        self.var_unmapped = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="Unmapped Only", variable=self.var_unmapped, command=self.refresh_sprite_list).pack(anchor="w")

        lf = ttk.Frame(right)
        lf.pack(fill="both", expand=True, pady=4)
        self.lb_sprites = tk.Listbox(lf, font=("Consolas", 9))
        ls = ttk.Scrollbar(lf, orient="vertical", command=self.lb_sprites.yview)
        self.lb_sprites.configure(yscrollcommand=ls.set)
        self.lb_sprites.pack(side="left", fill="both", expand=True)
        ls.pack(side="right", fill="y")
        self.lb_sprites.bind("<<ListboxSelect>>", self.on_sprite_select)

        box = ttk.LabelFrame(right, text="Slot Properties")
        box.pack(fill="x", pady=4)

        prev_row = ttk.Frame(box)
        prev_row.pack(fill="x", pady=4)
        self.lbl_prev_van = ttk.Label(prev_row, text="Reference", compound="top")
        self.lbl_prev_van.pack(side="left", expand=True)
        self.lbl_prev_mod = ttk.Label(prev_row, text="Mod Frame", compound="top")
        self.lbl_prev_mod.pack(side="right", expand=True)

        self.lbl_st = ttk.Label(box, text="No sprite selected", wraplength=370, font=("Consolas", 9, "bold"))
        self.lbl_st.pack(anchor="w", padx=6, pady=2)

        self.lbl_frames = ttk.Label(box, text="Dimensions: -", wraplength=370, foreground="#0055aa")
        self.lbl_frames.pack(anchor="w", padx=6, pady=2)

        coords_row = ttk.Frame(box)
        coords_row.pack(fill="x", padx=6, pady=4)

        self.var_x = tk.StringVar(value="0")
        self.var_y = tk.StringVar(value="0")
        self.var_w = tk.StringVar(value="32")
        self.var_h = tk.StringVar(value="32")

        for lbl_txt, var_obj, max_v in (("X:", self.var_x, 4096), ("Y:", self.var_y, 4096), ("W:", self.var_w, 1024), ("H:", self.var_h, 2048)):
            ttk.Label(coords_row, text=lbl_txt).pack(side="left", padx=(3, 1))
            sp = ttk.Spinbox(coords_row, from_=0, to=max_v, textvariable=var_obj, width=5, command=self.draw_selection_only)
            sp.pack(side="left", padx=2)
            sp.bind("<KeyRelease>", lambda e: self.draw_selection_only())

        src_row = ttk.Frame(box)
        src_row.pack(fill="x", padx=6, pady=3)
        ttk.Label(src_row, text="Frame Y Offset:").pack(side="left")
        self.var_src_y = tk.StringVar(value="0")
        sp_sy = ttk.Spinbox(src_row, from_=0, to=4096, increment=2, textvariable=self.var_src_y, width=6, command=self.update_mod_preview)
        sp_sy.pack(side="left", padx=4)
        sp_sy.bind("<KeyRelease>", lambda e: self.update_mod_preview())

        self.var_rot = tk.BooleanVar(value=False)
        ttk.Checkbutton(src_row, text="Rotate 90°", variable=self.var_rot, command=self.update_mod_preview).pack(side="right")

        row_btn = ttk.Frame(box)
        row_btn.pack(fill="x", padx=6, pady=6)
        ttk.Button(row_btn, text="Bind", command=self.bind_slot).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(row_btn, text="Unbind", command=self.del_slot).pack(side="right", expand=True, fill="x", padx=2)

    # ================= TAB 3: AUDIO REPLACER =================
    def build_tab_audio(self):
        top = ttk.Frame(self.tab_audio)
        top.pack(fill="x", padx=10, pady=6)
        ttk.Label(top, text="Search:").pack(side="left")
        self.var_audio_q = tk.StringVar()
        self.var_audio_q.trace_add("write", lambda *a: self.refresh_audio_list())
        ttk.Entry(top, textvariable=self.var_audio_q, width=32).pack(side="left", padx=6)

        mid = ttk.Frame(self.tab_audio)
        mid.pack(fill="both", expand=True, padx=10, pady=4)

        self.lb_audio = tk.Listbox(mid, font=("Consolas", 10))
        ascroll = ttk.Scrollbar(mid, orient="vertical", command=self.lb_audio.yview)
        self.lb_audio.configure(yscrollcommand=ascroll.set)
        self.lb_audio.pack(side="left", fill="both", expand=True)
        ascroll.pack(side="right", fill="y")
        ctrl = ttk.LabelFrame(self.tab_audio, text="Audio Controls")
        ctrl.pack(fill="x", padx=10, pady=8)

        ttk.Button(ctrl, text="Play Vita", command=self.play_vita_sound).pack(side="left", padx=6, pady=6, ipady=3, ipadx=8)
        ttk.Button(ctrl, text="Play Pack", command=self.play_mod_sound).pack(side="left", padx=6, pady=6, ipady=3, ipadx=8)
        ttk.Button(ctrl, text="Stop", command=audio_vag.stop_sound).pack(side="left", padx=6, pady=6, ipady=3, ipadx=8)

        ttk.Label(ctrl, text="Volume:").pack(side="left", padx=(14, 4))
        self.var_volume = tk.DoubleVar(value=0.35)
        ttk.Scale(ctrl, from_=0.0, to=1.0, variable=self.var_volume, orient="horizontal", length=100).pack(side="left", padx=4)

        ttk.Label(ctrl, text="AT9 Bitrate:").pack(side="left", padx=(14, 4))
        self.cb_at9_br = ttk.Combobox(
            ctrl,
            values=["96 kbps", "128 kbps", "144 kbps (Recommended)", "168 kbps", "192 kbps"],
            state="readonly",
            width=23
        )
        self.cb_at9_br.set("144 kbps (Recommended)")
        self.cb_at9_br.pack(side="left", padx=4)

        ttk.Button(ctrl, text="Replace Audio...", command=self.replace_custom_wav).pack(side="right", padx=6, pady=6, ipady=3, ipadx=8)
        ttk.Button(ctrl, text="Replace All", command=self.batch_replace_audio).pack(side="right", padx=6, pady=6, ipady=3, ipadx=8)


    # ================= TAB 4: TEXT EDITOR (.STR) =================
    def build_tab_text(self):
        top = ttk.Frame(self.tab_text)
        top.pack(fill="x", padx=10, pady=6)

        ttk.Label(top, text="Language File:").pack(side="left")
        self.cb_str_file = ttk.Combobox(top, state="readonly", width=22)
        self.cb_str_file.pack(side="left", padx=4)
        self.cb_str_file.bind("<<ComboboxSelected>>", lambda e: self.load_str_content())

        ttk.Label(top, text="Search text:").pack(side="left", padx=(12, 4))
        self.var_str_q = tk.StringVar()
        self.var_str_q.trace_add("write", lambda *a: self.filter_str_list())
        ttk.Entry(top, textvariable=self.var_str_q, width=28).pack(side="left")

        ttk.Button(top, text="Save .STR", command=self.save_str_content).pack(side="right", padx=4)
        ttk.Button(top, text="Import JSON...", command=self.import_str_json).pack(side="right", padx=4)
        ttk.Button(top, text="Export JSON...", command=self.export_str_json).pack(side="right", padx=4)

        mid = ttk.Frame(self.tab_text)
        mid.pack(fill="both", expand=True, padx=10, pady=4)

        self.lb_str = tk.Listbox(mid, font=("Consolas", 10))
        s_scroll = ttk.Scrollbar(mid, orient="vertical", command=self.lb_str.yview)
        self.lb_str.configure(yscrollcommand=s_scroll.set)
        self.lb_str.pack(side="left", fill="both", expand=True)
        s_scroll.pack(side="right", fill="y")
        self.lb_str.bind("<<ListboxSelect>>", self.on_str_select)

        edit_box = ttk.LabelFrame(self.tab_text, text="Edit Selected Line")
        edit_box.pack(fill="x", padx=10, pady=8)

        self.var_str_edit = tk.StringVar()
        ent_edit = ttk.Entry(edit_box, textvariable=self.var_str_edit, font=("Segoe UI", 10))
        ent_edit.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        ent_edit.bind("<Return>", lambda e: self.apply_str_line_edit())
        ttk.Button(edit_box, text="Apply Line", command=self.apply_str_line_edit).pack(side="right", padx=6, pady=6)


    # ================= HELPERS & WORKERS =================
    def log(self, msg):
        self.root.after(0, self._write_log, msg)

    def _write_log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_prog(self, val, max_v):
        self.root.after(0, lambda: (self.prog_main.configure(maximum=max_v), self.prog_main.configure(value=val)))

    def browse_vfs(self):
        p = filedialog.askopenfilename(filetypes=[("PS Vita VFS2 Archive", "*.vfs"), ("All files", "*.*")])
        if p:
            self.vars["vanilla_vfs"].set(p)
            self.save_cfg()

    def browse_dir(self, key):
        p = filedialog.askdirectory(initialdir=self.vars[key].get() or ".")
        if p:
            self.vars[key].set(p)
            self.save_cfg()
            # Автообновление всех списков и превью сразу после выбора папки текстурпака
            if key == "pc_pack_dir":
                self.refresh_sprite_list()
                self.refresh_audio_list()
                if self.base_pil is not None:
                    self.rebuild_editor_bg()

    def resolve_images_dir(self, base_dir):
        if not base_dir or not os.path.exists(base_dir):
            return ""
        for sub in ("", "Images", os.path.join("Content", "Images")):
            cand = os.path.join(base_dir, sub) if sub else base_dir
            if os.path.isdir(cand) and any(f.endswith(".png") for f in os.listdir(cand)):
                return cand
        return base_dir

    def resolve_sounds_dir(self, base_dir):
        if not base_dir or not os.path.exists(base_dir):
            return ""
        parent = os.path.dirname(base_dir.rstrip(os.sep))
        for cand in (
            os.path.join(base_dir, "Sounds"),
            os.path.join(base_dir, "Content", "Sounds"),
            os.path.join(parent, "Sounds"),
        ):
            if os.path.isdir(cand):
                return cand
        return ""

    def start_extract_base(self):
        self.save_cfg()
        vfs_p = self.cfg["vanilla_vfs"]
        if not vfs_p or not os.path.exists(vfs_p):
            messagebox.showwarning("Missing File", "Please select your dumped PS Vita data.vfs file first.")
            return
        threading.Thread(target=self.worker_extract, daemon=True).start()

    def worker_extract(self):
        try:
            ws = self.cfg["workspace_dir"]
            vanilla_dir = os.path.join(ws, "vanilla")
            unpacked_dir = os.path.join(ws, "unpacked_png")
            arch = vfs_handler.VFSArchive()
            arch.unpack_vfs(self.cfg["vanilla_vfs"], vanilla_dir, self.log)
            gxt_engine.decode_all_atlases(vanilla_dir, unpacked_dir, self.log)
            self.log("=== Base extraction & GXT decoding finished! ===")
            self.root.after(0, self.auto_load_workspace)
        except Exception as e:
            self.log(f"[ERROR] {e}")

    def start_build_mod(self):
        self.save_cfg()
        if not self.atlas_map:
            messagebox.showwarning("No Map", "vita_atlas_map.json is empty or missing.")
            return
        threading.Thread(target=self.worker_build, daemon=True).start()

    def collect_pack_sounds_map(self) -> dict[str, str]:
        """Рекурсивно собирает все .wav и .xnb из папки Sounds текстурпака (включая Sounds/Custom)."""
        pc_snd_dir = self.resolve_sounds_dir(self.cfg["pc_pack_dir"])
        found = {}
        if not pc_snd_dir or not os.path.exists(pc_snd_dir):
            return found
        for root, _, files in os.walk(pc_snd_dir):
            for f in sorted(files):
                low = f.lower()
                if low.endswith((".wav", ".xnb")):
                    stem = os.path.splitext(low).__getitem__(0)
                    if stem not in found or low.endswith(".wav"):
                        found[stem] = os.path.join(root, f)
        return found



    def worker_build(self):
        try:
            ws = self.cfg["workspace_dir"]
            vanilla_dir = os.path.join(ws, "vanilla")
            unpacked_dir = os.path.join(ws, "unpacked_png")
            mod_dir = os.path.join(ws, "mod_vfs")
            tp_imgs = self.resolve_images_dir(self.cfg["pc_pack_dir"])

            if not os.path.exists(vanilla_dir):
                self.log("Vanilla workspace not found, extracting data.vfs first...")
                self.worker_extract()

            self.log("=== Applying PC Texture Pack to PS Vita Atlases ===")
            gxt_engine.apply_texture_pack(tp_imgs, vanilla_dir, unpacked_dir, mod_dir, self.atlas_map, self.log, self.set_prog)

            if self.var_auto_sounds.get():
                pack_sounds = self.collect_pack_sounds_map()
                if pack_sounds:
                    self.log(f"=== Converting matching PC sounds ({len(pack_sounds)} files in pack) ===")
                    snd_dir = os.path.join(vanilla_dir, "sounds")
                    converted_cnt = 0
                    for vag_name in sorted(os.listdir(snd_dir)):
                        if not vag_name.lower().endswith(".vag"):
                            continue
                        stem = os.path.splitext(vag_name).__getitem__(0).lower()
                        if stem in pack_sounds:
                            orig_vag = os.path.join(snd_dir, vag_name)
                            orig_rate, orig_ch = audio_vag.get_vag_header_info(orig_vag)
                            out_vag = os.path.join(mod_dir, "sounds", vag_name)
                            if audio_vag.convert_any_sound_to_vag(pack_sounds[stem], out_vag, orig_rate, orig_ch, ws):
                                converted_cnt += 1
                    self.log(f"[OK] Auto-converted {converted_cnt} sound effects from texture pack")

            custom_snd = os.path.join(ws, "custom_sounds")
            if os.path.exists(custom_snd):
                for f in os.listdir(custom_snd):
                    if f.endswith(".vag"):
                        shutil.copy2(os.path.join(custom_snd, f), os.path.join(mod_dir, "sounds", f))


            custom_mus = os.path.join(ws, "custom_music")
            if os.path.exists(custom_mus):
                for f in os.listdir(custom_mus):
                    if f.endswith(".at9"):
                        shutil.copy2(os.path.join(custom_mus, f), os.path.join(mod_dir, "music", f))

            custom_str = os.path.join(ws, "custom_strings")
            if os.path.exists(custom_str):
                for f in os.listdir(custom_str):
                    if f.endswith(".str"):
                        shutil.copy2(os.path.join(custom_str, f), os.path.join(mod_dir, "strings", f))


            out_vfs = self.cfg["out_vfs"]
            arch = vfs_handler.VFSArchive()
            arch.pack_vfs(mod_dir, out_vfs, self.log)
            self.log(f"=== SUCCESS! Built {out_vfs} ===")
        except Exception as e:
            self.log(f"[ERROR] {e}")

    def start_indexer(self):
        self.save_cfg()
        threading.Thread(target=self.worker_indexer, daemon=True).start()

    def worker_indexer(self):
        try:
            ws = self.cfg["workspace_dir"]
            vanilla_dir = os.path.join(ws, "vanilla")
            unpacked_dir = os.path.join(ws, "unpacked_png")
            p_dir = self.resolve_images_dir(self.cfg["ref_primary"])
            s_dir = self.resolve_images_dir(self.cfg["ref_secondary"])

            stats = index_builder.run_auto_indexer(p_dir, s_dir, vanilla_dir, unpacked_dir, MAP_FILE, self.log, self.set_prog)
            with open(MAP_FILE, "r", encoding="utf-8") as f:
                self.atlas_map = json.load(f)
            self.log(f"=== Auto-Indexing Complete: {stats} ===")
            self.root.after(0, lambda: (self.load_editor_sheet(self.cur_sheet), self.refresh_sprite_list()))
        except Exception as e:
            self.log(f"[ERROR] {e}")

    # ================= VISUAL EDITOR METHODS =================
    def on_mouse_wheel(self, event):
        step = -3 if event.delta > 0 else 3
        self.canvas.yview_scroll(step, "units")

    def on_shift_wheel(self, event):
        step = -3 if event.delta > 0 else 3
        self.canvas.xview_scroll(step, "units")

    def on_ctrl_wheel(self, event):
        factor = 1.15 if event.delta > 0 else (1.0 / 1.15)
        self.change_zoom(factor)

    def reset_zoom(self):
        if abs(self.zoom - 1.0) > 0.01:
            self.zoom = 1.0
            self.lbl_zoom.configure(text="Zoom: 1.00x")
            self.rebuild_editor_bg(preserve_scroll=True)

    def change_zoom(self, factor):
        new_z = max(0.25, min(6.0, round(self.zoom * factor, 3)))
        if abs(new_z - self.zoom) > 0.005:
            self.zoom = new_z
            self.lbl_zoom.configure(text=f"Zoom: {self.zoom:.2f}x")
            self.rebuild_editor_bg(preserve_scroll=True)

    def on_sheet_combo_change(self, event=None):
        selected = self.cb_sheet.get()
        self.load_editor_sheet(selected)

    def get_short_sheet_key(self):
        base_name = os.path.basename(self.cur_sheet)
        for k, v in gxt_engine.ATLAS_SHEETS.items():
            if v == base_name:
                return k
        return self.cur_sheet


    def load_editor_sheet(self, name):
        self.cur_sheet = name
        ws = self.cfg["workspace_dir"]
        unpacked_dir = os.path.join(ws, "unpacked_png")
        custom_png = os.path.join(ws, "custom_sheets", f"{name}.png")
        default_png = os.path.join(unpacked_dir, f"{name}.png")

        if not os.path.exists(default_png) and name in gxt_engine.ATLAS_SHEETS:
            mapped_name = gxt_engine.ATLAS_SHEETS[name]
            self.cur_sheet = mapped_name
            self.cb_sheet.set(mapped_name)
            custom_png = os.path.join(ws, "custom_sheets", f"{mapped_name}.png")
            default_png = os.path.join(unpacked_dir, f"{mapped_name}.png")

        path = custom_png if os.path.exists(custom_png) else default_png
        if not os.path.exists(path):
            self.canvas.delete("all")
            self.base_pil = None
            return

        pil_rgba = Image.open(path).convert("RGBA")
        self.base_pil = pil_rgba.convert("RGB")
        self.base_rgba = pil_rgba
        alpha_raw = np.array(pil_rgba.split().__getitem__(3))
        self.alpha_np = (alpha_raw > 128).astype(np.uint8)

        self.rebuild_editor_bg(preserve_scroll=False)
        self.canvas.xview_moveto(0.0)
        self.canvas.yview_moveto(0.0)
        self.canvas.update_idletasks()
        self.refresh_sprite_list()

    def rebuild_editor_bg(self, preserve_scroll=True):
        if self.base_pil is None:
            return
        # Запоминаем текущую позицию прокрутки перед перерисовкой
        old_x = self.canvas.xview().__getitem__(0) if preserve_scroll else 0.0
        old_y = self.canvas.yview().__getitem__(0) if preserve_scroll else 0.0

        short_key = self.get_short_sheet_key()
        tp_imgs = self.resolve_images_dir(self.cfg["pc_pack_dir"])
        if self.var_overlay.get() and tp_imgs and os.path.exists(tp_imgs):
            canvas_img = self.base_rgba.copy()
            for fname, info in self.atlas_map.items():
                if info.get("sheet") != short_key:
                    continue
                mod_p = os.path.join(tp_imgs, fname)
                if not os.path.exists(mod_p):
                    continue
                src_arr = np.array(Image.open(mod_p).convert("RGBA"))
                if info["type"] == "sprite":
                    patch_arr = gxt_engine.prepare_sprite_patch(src_arr, info)
                    patch_im = Image.fromarray(patch_arr, "RGBA")
                    tw, th = info["w"], info["h"]
                    canvas_img.paste((0, 0, 0, 0), (info["vita_x"], info["vita_y"], info["vita_x"] + tw, info["vita_y"] + th))
                    canvas_img.paste(patch_im, (info["vita_x"], info["vita_y"]), patch_im)
                elif info["type"] == "grid":
                    src_im = Image.fromarray(src_arr, "RGBA")
                    sw, sh = src_im.size
                    for s in info["slots"]:
                        sx, sy, vx, vy, csz = s["src_x"], s["src_y"], s["vita_x"], s["vita_y"], s["size"]
                        ssz = s.get("src_size", csz)
                        cell = src_im.crop((sx, sy, min(sw, sx + ssz), min(sh, sy + ssz)))
                        if ssz != csz:
                            cell = cell.resize((csz, csz), Image.Resampling.NEAREST)
                        canvas_img.paste((0, 0, 0, 0), (vx, vy, vx + csz, vy + csz))
                        canvas_img.paste(cell, (vx, vy), cell)
            img = canvas_img.convert("RGB")
        else:
            img = self.base_pil.copy()

        if self.var_boxes.get():
            draw = ImageDraw.Draw(img)
            for info in self.atlas_map.values():
                if info.get("sheet") != short_key:
                    continue
                if info["type"] == "sprite":
                    x, y, w, h = info["vita_x"], info["vita_y"], info["w"], info["h"]
                    draw.rectangle((x, y, x + w - 1, y + h - 1), outline=(0, 255, 100))
                elif info["type"] == "grid":
                    for s in info["slots"]:
                        x, y, sz = s["vita_x"], s["vita_y"], s["size"]
                        draw.rectangle((x, y, x + sz - 1, y + sz - 1), outline=(0, 200, 255))

        w, h = img.size
        zw = max(1, int(round(w * self.zoom)))
        zh = max(1, int(round(h * self.zoom)))
        if abs(self.zoom - 1.0) > 0.005:
            img = img.resize((zw, zh), Image.Resampling.NEAREST)
        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.sel_rect_id = None
        self.canvas.configure(scrollregion=(0, 0, zw, zh))
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)
        self.draw_selection_only()

        if preserve_scroll:
            self.canvas.xview_moveto(old_x)
            self.canvas.yview_moveto(old_y)

    def replace_full_sheet(self):
        if os.path.basename(self.cur_sheet) in ("_sheetSprites", "_sheetTiles", "_sheetWalls", "_sheetNPC", "_sheetSolid", "_sheetBack"):
            messagebox.showinfo(
                "Packed Atlas",
                "This is a multi-sprite atlas. Map individual sprites using the list on the right."
            )
            return

        png_path = filedialog.askopenfilename(filetypes=[("PNG Image", "*.png")])
        if not png_path:
            return

        ws = self.cfg["workspace_dir"]
        dest_png = os.path.join(ws, "custom_sheets", f"{self.cur_sheet.replace('/', os.sep)}.png")
        os.makedirs(os.path.dirname(dest_png), exist_ok=True)
        shutil.copy2(png_path, dest_png)

        self.load_editor_sheet(self.cur_sheet)
        messagebox.showinfo("Replaced", f"Custom image assigned to '{self.cur_sheet}'.")


    def get_box_from_vars(self):
        try:
            return int(self.var_x.get()), int(self.var_y.get()), int(self.var_w.get()), int(self.var_h.get())
        except ValueError:
            return None

    def set_box_to_vars(self, x, y, w, h):
        self.var_x.set(str(x))
        self.var_y.set(str(y))
        self.var_w.set(str(w))
        self.var_h.set(str(h))
        self.draw_selection_only()

    def draw_selection_only(self):
        if self.sel_rect_id is not None:
            self.canvas.delete(self.sel_rect_id)
            self.sel_rect_id = None
        box = self.get_box_from_vars()
        if box is not None and box.__getitem__(2) > 0 and box.__getitem__(3) > 0:
            x, y, w, h = [v * self.zoom for v in box]
            self.sel_rect_id = self.canvas.create_rectangle(x, y, x + w, y + h, outline="#ff3333", width=2)

    def on_editor_click(self, ev):
        if self.alpha_np is None:
            return
        cx = int(self.canvas.canvasx(ev.x) // self.zoom)
        cy = int(self.canvas.canvasy(ev.y) // self.zoom)
        h, w = self.alpha_np.shape
        if not (0 <= cx < w and 0 <= cy < h):
            return

        short_key = self.get_short_sheet_key()
        hits = []
        for fname, info in self.atlas_map.items():
            if info.get("sheet") != short_key:
                continue
            if info["type"] == "sprite":
                vx, vy, vw, vh = info["vita_x"], info["vita_y"], info["w"], info["h"]
                if (vx - 1) <= cx <= (vx + vw) and (vy - 1) <= cy <= (vy + vh):
                    hits.append((vw * vh, fname, vx, vy, vw, vh))
            elif info["type"] == "grid":
                for s in info["slots"]:
                    vx, vy, sz = s["vita_x"], s["vita_y"], s["size"]
                    if (vx - 1) <= cx <= (vx + sz) and (vy - 1) <= cy <= (vy + sz):
                        hits.append((sz * sz, fname, vx, vy, sz, sz))

        if hits:
            hits.sort(key=lambda item: item.__getitem__(0))
            _, hit_fname, hx, hy, hw, hh = hits.__getitem__(0)
            self.set_box_to_vars(hx, hy, hw, hh)
            self.select_sprite_by_name(hit_fname)
            return

        if self.alpha_np[cy, cx] > 0:
            mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
            _, _, _, rect = cv2.floodFill(self.alpha_np.copy(), mask, (cx, cy), 2, flags=4 | (2 << 8) | cv2.FLOODFILL_MASK_ONLY)
            rx, ry, rw, rh = [int(v) for v in rect]
            self.set_box_to_vars(rx, ry, rw, rh)
        else:
            box = self.get_box_from_vars()
            cw = box.__getitem__(2) if (box and box.__getitem__(2) > 0) else 32
            ch = box.__getitem__(3) if (box and box.__getitem__(3) > 0) else 32
            self.set_box_to_vars(cx, cy, cw, ch)

    def format_entry_label(self, fname):
        prefix = "[OK] " if fname in self.atlas_map else "[--] "
        human = self.names_db.get(fname, "")
        return f"{prefix}{fname} — {human}" if human else f"{prefix}{fname}"

    def refresh_sprite_list(self):
        q = self.var_q.get().strip().lower()
        tp_imgs = self.resolve_images_dir(self.cfg["pc_pack_dir"])
        if not tp_imgs or not os.path.exists(tp_imgs):
            return
        self.lb_sprites.delete(0, "end")
        self.displayed_fnames = []
        for f in sorted(os.listdir(tp_imgs)):
            if not f.endswith(".png"):
                continue
            label = self.format_entry_label(f)
            if q and q not in label.lower():
                continue
            if self.var_unmapped.get() and f in self.atlas_map:
                continue
            self.displayed_fnames.append(f)
            self.lb_sprites.insert("end", label)

    def select_sprite_by_name(self, target_fname):
        self.var_q.set("")
        self.var_unmapped.set(False)
        self.refresh_sprite_list()
        if target_fname in self.displayed_fnames:
            idx = self.displayed_fnames.index(target_fname)
            self.lb_sprites.selection_clear(0, "end")
            self.lb_sprites.selection_set(idx)
            self.lb_sprites.see(idx)
        self.load_sprite_details(target_fname, auto_scroll=False)

    def on_sprite_select(self, ev):
        sel = self.lb_sprites.curselection()
        if not sel:
            return
        fname = self.displayed_fnames.__getitem__(sel.__getitem__(0))
        self.load_sprite_details(fname, auto_scroll=True)

    def update_mod_preview(self):
        if not self.sel_fname:
            return
        tp_imgs = self.resolve_images_dir(self.cfg["pc_pack_dir"])
        mod_path = os.path.join(tp_imgs, self.sel_fname)
        if not os.path.exists(mod_path):
            return
        raw_m = Image.open(mod_path).convert("RGBA")
        try:
            sy = int(self.var_src_y.get())
        except ValueError:
            sy = 0
        w, h = raw_m.size
        cropped = raw_m.crop((0, min(sy, max(0, h - 1)), w, h))
        if self.var_rot.get():
            cropped = cropped.rotate(90, expand=True)
        im_m = scale_pixel_art(cropped, 130)
        self.tk_prev_mod = ImageTk.PhotoImage(im_m)
        self.lbl_prev_mod.configure(image=self.tk_prev_mod, text=f"Frame Y={sy}")

    def load_sprite_details(self, fname, auto_scroll=True):
        self.sel_fname = fname
        human_name = self.names_db.get(fname, "Unknown")

        van_path = ""
        for d_key in ("ref_primary", "ref_secondary"):
            ref_dir = self.resolve_images_dir(self.cfg.get(d_key, ""))
            cand = os.path.join(ref_dir, fname) if ref_dir else ""
            if cand and os.path.exists(cand):
                van_path = cand
                break

        van_size_str = "-"
        if van_path:
            raw_v = Image.open(van_path).convert("RGBA")
            van_size_str = f"{raw_v.size.__getitem__(0)}x{raw_v.size.__getitem__(1)}px"
            im_v = scale_pixel_art(raw_v, 130)
            self.tk_prev_van = ImageTk.PhotoImage(im_v)
            self.lbl_prev_van.configure(image=self.tk_prev_van, text="Reference")
        else:
            self.lbl_prev_van.configure(image="", text="No Reference\n(Set in Auto-Indexer)", justify="center")

        tp_imgs = self.resolve_images_dir(self.cfg["pc_pack_dir"])
        mod_path = os.path.join(tp_imgs, fname)
        mod_size_str = "-"
        if os.path.exists(mod_path):
            raw_m = Image.open(mod_path).convert("RGBA")
            mod_size_str = f"{raw_m.size.__getitem__(0)}x{raw_m.size.__getitem__(1)}px"

        if fname in self.atlas_map:
            info = self.atlas_map[fname]
            short_s = info["sheet"]
            target_sheet = f"images/{gxt_engine.ATLAS_SHEETS[short_s]}" if short_s in gxt_engine.ATLAS_SHEETS else short_s

            if target_sheet != self.cur_sheet and target_sheet in self.cb_sheet["values"]:
                self.cb_sheet.set(target_sheet)
                self.load_editor_sheet(target_sheet)

            if info["type"] == "sprite":
                self.var_rot.set(info.get("rot90", False))
                self.var_src_y.set(str(info.get("src_y", 0)))
                self.set_box_to_vars(info["vita_x"], info["vita_y"], info["w"], info["h"])
                self.lbl_st.configure(text=f"[MAPPED] {fname} ({human_name})")
                self.lbl_frames.configure(text=f"Ref: {van_size_str} | Pack: {mod_size_str} | Slot: {info['w']}x{info['h']}px")
                if auto_scroll and self.base_pil is not None:
                    aw, ah = self.base_pil.size
                    self.canvas.xview_moveto(max(0.0, (info["vita_x"] - 180) / aw))
                    self.canvas.yview_moveto(max(0.0, (info["vita_y"] - 180) / ah))
            else:
                self.lbl_st.configure(text=f"[GRID] {fname} ({human_name}, {len(info['slots'])} tiles)")
                self.lbl_frames.configure(text=f"Ref: {van_size_str} | Pack: {mod_size_str}")
        else:
            self.var_src_y.set("0")
            self.lbl_st.configure(text=f"[UNMAPPED] {fname} ({human_name})")
            self.lbl_frames.configure(text=f"Ref: {van_size_str} | Pack: {mod_size_str}")

        self.update_mod_preview()


    def bind_slot(self):
        box = self.get_box_from_vars()
        if not self.sel_fname or box is None:
            return
        bx, by, bw, bh = box
        if bw <= 0 or bh <= 0:
            return
        try:
            sy = int(self.var_src_y.get())
        except ValueError:
            sy = 0

        self.atlas_map[self.sel_fname] = {
            "type": "sprite", "sheet": self.get_short_sheet_key(), "vita_x": bx, "vita_y": by,

            "w": bw, "h": bh, "src_x": 0, "src_y": sy, "rot90": self.var_rot.get()
        }
        self.refresh_sprite_list()
        self.rebuild_editor_bg()

    def del_slot(self):
        if self.sel_fname and self.sel_fname in self.atlas_map:
            del self.atlas_map[self.sel_fname]
            self.refresh_sprite_list()
            self.rebuild_editor_bg()

    def save_atlas_map(self):
        with open(MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(self.atlas_map, f, indent=2)
        messagebox.showinfo("Saved", "vita_atlas_map.json updated!")

    def on_tab_changed(self, event=None):
        if self.notebook.select() == str(self.tab_audio):
            self.check_and_prompt_audio_tools()

    def check_and_prompt_audio_tools(self):
        if not self.cfg.get("remind_audio_tools", True):
            return
        has_dec, has_enc = audio_vag.check_audio_tools_status()
        if has_dec and has_enc:
            return

        lines = [
            "Optional external audio tools are missing from the OCRAM folder or 'tools/' directory:\n"
        ]
        if not has_dec:
            lines.append("• vgmstream-cli — required to play/preview PS Vita .vag & .at9 audio.\n  Manual link: https://vgmstream.org")
        if not has_enc:
            lines.append(
                "• at9tool.exe (from ATRACTool) — required to encode .wav into PS Vita .at9 music.\n"
                "  (Note: on Linux/macOS, OCRAM runs at9tool.exe via 'mono', or falls back to 'wine' if mono fails.)"
            )

        dlg = tk.Toplevel(self.root)
        dlg.title("External Audio Tools Notice")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.wait_visibility()
        dlg.grab_set()

        lbl_info = ttk.Label(dlg, text="\n\n".join(lines), wraplength=510, justify="left")
        lbl_info.pack(padx=18, pady=(16, 10), anchor="w")

        lbl_status = ttk.Label(dlg, text="", foreground="#0066cc", font=("Segoe UI", 9, "bold"))
        lbl_status.pack(padx=18, pady=(0, 6), anchor="w")

        var_rem = tk.BooleanVar(value=True)
        ttk.Checkbutton(dlg, text="Remind me every time", variable=var_rem).pack(padx=18, pady=4, anchor="w")

        btn_row = ttk.Frame(dlg)
        btn_row.pack(fill="x", padx=18, pady=(8, 16))

        def on_close():
            self.cfg["remind_audio_tools"] = bool(var_rem.get())
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2, ensure_ascii=False)
            dlg.destroy()

        def on_download():
            btn_dl.configure(state="disabled")
            lbl_status.configure(text="Downloading missing tools, please wait...")

            def worker():
                try:
                    res = audio_vag.download_missing_audio_tools(lambda m: self.root.after(0, lambda msg=m: lbl_status.configure(text=msg)))
                    def done():
                        if res:
                            messagebox.showinfo("Download Complete", "Successfully installed into 'tools/':\n• " + "\n• ".join(res), parent=dlg)
                            on_close()
                        else:
                            lbl_status.configure(text="Could not download automatically. Please install manually.")
                            btn_dl.configure(state="normal")
                    self.root.after(0, done)
                except Exception as exc:
                    err_msg = str(exc)
                    self.root.after(0, lambda m=err_msg: (
                        lbl_status.configure(text=f"Download error: {m}"),
                        btn_dl.configure(state="normal")
                    ))

            threading.Thread(target=worker, daemon=True).start()

        btn_dl = ttk.Button(btn_row, text="Download Automatically", command=on_download)
        btn_dl.pack(side="left")

        ttk.Button(btn_row, text="Close", command=on_close, width=12).pack(side="right")


    # ================= AUDIO & MUSIC METHODS =================
    def get_selected_audio_rel(self):
        sel = self.lb_audio.curselection()
        if not sel:
            return None
        return self.audio_files[sel[0]]

    def refresh_audio_list(self):
        q = self.var_audio_q.get().strip().lower()
        ws = self.cfg["workspace_dir"]
        self.lb_audio.delete(0, "end")
        self.audio_files = []
        pack_sounds = self.collect_pack_sounds_map()

        for sub_folder, ext, custom_sub in (("sounds", ".vag", "custom_sounds"), ("music", ".at9", "custom_music")):
            folder_p = os.path.join(ws, "vanilla", sub_folder)
            if not os.path.exists(folder_p):
                continue
            for f in sorted(os.listdir(folder_p)):
                if not f.lower().endswith(ext):
                    continue
                rel_item = f"{sub_folder}/{f}"
                human = audio_vag.get_human_audio_name(f)
                display_str = f"{rel_item} — {human}" if human else rel_item
                if q and q not in display_str.lower():
                    continue
                base_no_ext = os.path.splitext(f)[0].lower()
                has_custom = os.path.exists(os.path.join(ws, custom_sub, f))
                has_pc = (sub_folder == "sounds" and base_no_ext in pack_sounds)
                tag = "[CUSTOM] " if has_custom else ("[PACK AUDIO] " if has_pc else f"[{sub_folder.upper()}] ")
                self.audio_files.append(rel_item)
                self.lb_audio.insert("end", tag + display_str)

    def play_vita_sound(self):
        rel_name = self.get_selected_audio_rel()
        if not rel_name:
            return
        ws = self.cfg["workspace_dir"]
        sub_folder, fname = rel_name.split("/", 1)
        custom_sub = "custom_sounds" if sub_folder == "sounds" else "custom_music"
        custom_p = os.path.join(ws, custom_sub, fname)
        src_audio = custom_p if os.path.exists(custom_p) else os.path.join(ws, "vanilla", sub_folder, fname)
        tmp_wav = os.path.join(ws, "tmp_preview.wav")
        try:
            audio_vag.decode_with_vgmstream(src_audio, tmp_wav)
            audio_vag.play_wav_async(tmp_wav, volume=self.var_volume.get())
        except Exception as e:
            messagebox.showinfo("vgmstream Required", str(e))

    def play_mod_sound(self):
        rel_name = self.get_selected_audio_rel()
        if not rel_name or not rel_name.startswith("sounds/"):
            return
        vag_name = rel_name.split("/", 1)[1]
        stem = os.path.splitext(vag_name)[0].lower()
        pack_sounds = self.collect_pack_sounds_map()
        if stem not in pack_sounds:
            messagebox.showinfo("Not Found", f"No matching {stem}.wav or {stem}.xnb in PC Texture Pack.")
            return
        src_p = pack_sounds[stem]
        if src_p.lower().endswith(".xnb"):
            tmp_wav = os.path.join(self.cfg["workspace_dir"], "_tmp_xnb_play.wav")
            audio_vag.extract_xnb_sound_to_wav(src_p, tmp_wav)
            audio_vag.play_wav_async(tmp_wav, volume=self.var_volume.get())
        else:
            audio_vag.play_wav_async(src_p, volume=self.var_volume.get())

    def replace_custom_wav(self):
        rel_name = self.get_selected_audio_rel()
        if not rel_name:
            messagebox.showinfo("Select Track", "Please select a sound (.vag) or music track (.at9) from the list first.")
            return

        sub_folder, target_fname = rel_name.split("/", 1)
        ws = self.cfg["workspace_dir"]

        if sub_folder == "sounds":
            src_p = filedialog.askopenfilename(
                title=f"Replace {target_fname}",
                filetypes=[("Audio Files", "*.wav *.xnb *.vag"), ("All files", "*.*")]
            )
            if not src_p:
                return
            orig_vag = os.path.join(ws, "vanilla", "sounds", target_fname)
            orig_rate, orig_ch = audio_vag.get_vag_header_info(orig_vag)
            out_vag = os.path.join(ws, "custom_sounds", target_fname)
            if audio_vag.convert_any_sound_to_vag(src_p, out_vag, orig_rate, orig_ch, ws):
                self.refresh_audio_list()
                messagebox.showinfo("Replaced", f"Assigned '{os.path.basename(src_p)}' -> '{target_fname}'!\nClick 'Build VFS' to pack.")
            else:
                messagebox.showerror("Error", "Failed to convert selected audio file.")
        else:
            src_p = filedialog.askopenfilename(
                title=f"Replace Music Track {target_fname}",
                filetypes=[("Audio Files (.at9 / .wav / .xnb)", "*.at9 *.wav *.xnb"), ("All files", "*.*")]
            )
            if not src_p:
                return
            out_at9 = os.path.join(ws, "custom_music", target_fname)
            os.makedirs(os.path.dirname(out_at9), exist_ok=True)
            low_src = src_p.lower()
            br_val = int(self.cb_at9_br.get().split().__getitem__(0))
            try:
                if low_src.endswith(".at9"):
                    shutil.copy2(src_p, out_at9)
                elif low_src.endswith(".wav"):
                    audio_vag.encode_wav_to_at9(src_p, out_at9, bitrate=br_val)
                elif low_src.endswith(".xnb"):
                    tmp_wav = os.path.join(ws, "_tmp_xnb_music.wav")
                    if not audio_vag.extract_xnb_sound_to_wav(src_p, tmp_wav):
                        raise RuntimeError("Failed to extract PCM audio from .xnb file.")
                    audio_vag.encode_wav_to_at9(tmp_wav, out_at9, bitrate=br_val)
                else:
                    messagebox.showwarning("Unsupported Format", "Please select a .at9, .wav, or .xnb audio file.")
                    return
                self.refresh_audio_list()
                messagebox.showinfo("Replaced", f"Assigned '{os.path.basename(src_p)}' -> '{target_fname}' ({br_val} kbps)!\nClick 'Build VFS' to pack.")
            except Exception as e:
                messagebox.showerror("ATRAC9 Encoding Error", str(e))


    def batch_replace_audio(self):
        pack_sounds = self.collect_pack_sounds_map()
        if not pack_sounds:
            messagebox.showwarning("No Sounds", "No .wav or .xnb files found in PC Texture Pack Sounds folder.")
            return
        ws = self.cfg["workspace_dir"]
        snd_dir = os.path.join(ws, "vanilla", "sounds")
        replaced = 0
        for vag_name in sorted(os.listdir(snd_dir)):
            if not vag_name.lower().endswith(".vag"):
                continue
            stem = os.path.splitext(vag_name)[0].lower()
            if stem in pack_sounds:
                orig_vag = os.path.join(snd_dir, vag_name)
                orig_rate, orig_ch = audio_vag.get_vag_header_info(orig_vag)
                out_vag = os.path.join(ws, "custom_sounds", vag_name)
                if audio_vag.convert_any_sound_to_vag(pack_sounds[stem], out_vag, orig_rate, orig_ch, ws):
                    replaced += 1
        self.refresh_audio_list()
        messagebox.showinfo("Batch Audio", f"Converted and replaced {replaced} sound effects!")

    # ================= TEXT EDITOR (.STR) METHODS =================
    def refresh_str_dropdown(self):
        sdir = os.path.join(self.cfg["workspace_dir"], "vanilla", "strings")
        if os.path.exists(sdir):
            files = sorted([f for f in os.listdir(sdir) if f.lower().endswith(".str")])
            if files:
                self.cb_str_file["values"] = files
                default_f = "strings_en.str" if "strings_en.str" in files else files[0]
                self.cb_str_file.set(default_f)
                self.load_str_content()

    def load_str_content(self):
        fname = self.cb_str_file.get()
        if not fname:
            return
        ws = self.cfg["workspace_dir"]
        custom_p = os.path.join(ws, "custom_strings", fname)
        orig_p = os.path.join(ws, "vanilla", "strings", fname)
        target_p = custom_p if os.path.exists(custom_p) else orig_p
        if not os.path.exists(target_p):
            return
        self.current_strings = str_handler.load_str_file(target_p)
        self.filter_str_list()

    def filter_str_list(self):
        if not hasattr(self, "current_strings"):
            return
        q = self.var_str_q.get().strip().lower()
        self.lb_str.delete(0, "end")
        self.displayed_str_indices = []
        for idx, text in enumerate(self.current_strings):
            if q and q not in text.lower() and q != str(idx):
                continue
            self.displayed_str_indices.append(idx)
            preview = text.replace("\n", "\\n")
            self.lb_str.insert("end", f"[{idx:04d}] {preview}")

    def on_str_select(self, ev):
        sel = self.lb_str.curselection()
        if not sel:
            return
        real_idx = self.displayed_str_indices[sel[0]]
        self.selected_str_idx = real_idx
        self.var_str_edit.set(self.current_strings[real_idx])

    def apply_str_line_edit(self):
        if not hasattr(self, "selected_str_idx") or self.selected_str_idx is None:
            return
        new_val = self.var_str_edit.get()
        self.current_strings[self.selected_str_idx] = new_val
        self.filter_str_list()

    def save_str_content(self):
        fname = self.cb_str_file.get()
        if not fname or not hasattr(self, "current_strings"):
            return
        out_p = os.path.join(self.cfg["workspace_dir"], "custom_strings", fname)
        str_handler.save_str_file(out_p, self.current_strings)
        messagebox.showinfo("Saved", f"Saved {fname} with {len(self.current_strings)} lines!\nClick 'Build VFS' to pack it.")

    def export_str_json(self):
        fname = self.cb_str_file.get()
        if not fname or not hasattr(self, "current_strings"):
            return
        p = filedialog.asksaveasfilename(defaultextension=".json", initialfile=f"{fname}.json", filetypes=[("JSON File", "*.json")])
        if p:
            data = {str(i): s for i, s in enumerate(self.current_strings)}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("Exported", "Strings exported to JSON!")

    def import_str_json(self):
        if not hasattr(self, "current_strings"):
            return
        p = filedialog.askopenfilename(filetypes=[("JSON File", "*.json")])
        if p:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                idx = int(k)
                if 0 <= idx < len(self.current_strings):
                    self.current_strings[idx] = str(v)
            self.filter_str_list()
            messagebox.showinfo("Imported", "Strings updated from JSON! Click 'Save .STR' to keep changes.")
