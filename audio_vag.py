import os
import wave
import json
import shutil
import struct
import zipfile
import tarfile
import platform
import subprocess
import urllib.request
import numpy as np

AUDIO_HUMAN_NAMES: dict[str, str] = {}

VAG_ENC_F = (
    (0.0, 0.0),
    (60.0 / 64.0, 0.0),
    (115.0 / 64.0, -52.0 / 64.0),
    (98.0 / 64.0, -55.0 / 64.0),
    (122.0 / 64.0, -60.0 / 64.0),
)

_current_player_proc = None

def get_human_audio_name(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename)).__getitem__(0).lower()
    return AUDIO_HUMAN_NAMES.get(stem, "")

def stop_sound() -> None:
    global _current_player_proc
    if _current_player_proc is not None:
        try:
            _current_player_proc.terminate()
        except Exception:
            pass
        _current_player_proc = None

    if platform.system() == "Windows":
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

def _find_external_tool(names: tuple[str, ...]) -> str | None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for name in names:
        for folder in (base_dir, os.path.join(base_dir, "tools")):
            cand = os.path.join(folder, name)
            if os.path.isfile(cand):
                if platform.system() != "Windows" and not os.access(cand, os.X_OK):
                    try:
                        os.chmod(cand, os.stat(cand).st_mode | 0o111)
                    except Exception:
                        pass
                return cand
        in_path = shutil.which(name)
        if in_path:
            return in_path
    return None


def decode_with_vgmstream(src_path: str, wav_path: str) -> tuple[int, int]:
    """Декодирует .vag и .at9 в WAV через vgmstream-cli."""
    vgm_exe = _find_external_tool(("vgmstream-cli", "vgmstream-cli.exe"))
    if not vgm_exe:
        raise RuntimeError(
            "vgmstream-cli is required to preview PS Vita audio (.vag / .at9).\n\n"
            "Click 'Download Automatically' or download from:\nhttps://vgmstream.org"
        )

    os.makedirs(os.path.dirname(os.path.abspath(wav_path)), exist_ok=True)
    if os.path.exists(wav_path):
        os.remove(wav_path)

    res = subprocess.run([vgm_exe, "-i", "-o", wav_path, src_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode != 0 or not os.path.exists(wav_path):
        raise RuntimeError(f"vgmstream-cli failed to decode {os.path.basename(src_path)}")

    with wave.open(wav_path, "rb") as wf:
        return wf.getframerate(), wf.getnchannels()

def _extract_at9tool_from_atractool(atractool_path: str, out_at9tool_path: str) -> bool:
    """Извлекает нативный CLI-бинарник at9tool.exe, вшитый в ресурсы ATRACTool.exe."""
    with open(atractool_path, "rb") as f:
        blob = f.read()

    # Ищем все заголовки PE-файлов (MZ\x90\x00) внутри ATRACTool.exe после его собственного заголовка
    pos = 0x1000
    mz_positions = []
    while True:
        idx = blob.find(b"MZ\x90\x00", pos)
        if idx < 0:
            break
        if idx + 0x40 < len(blob):
            pe_off, = struct.unpack_from("<I", blob, idx + 0x3C)
            if 0 < pe_off < 0x1000 and idx + pe_off + 4 < len(blob):
                if blob[idx + pe_off : idx + pe_off + 4] == b"PE\x00\x00":
                    mz_positions.append(idx)
        pos = idx + 4

    for i, start in enumerate(mz_positions):
        end = mz_positions.__getitem__(i + 1) if i + 1 < len(mz_positions) else len(blob)
        chunk = blob[start:end]
        if b"at9tool" in chunk or b"ATRAC9" in chunk:
            with open(out_at9tool_path, "wb") as out_f:
                out_f.write(chunk)
            if platform.system() != "Windows":
                os.chmod(out_at9tool_path, 0o755)
            return True
    return False

def check_audio_tools_status() -> tuple[bool, bool]:
    """Возвращает (has_vgmstream, has_at9tool)."""
    has_dec = _find_external_tool(("vgmstream-cli", "vgmstream-cli.exe")) is not None
    has_enc = _find_external_tool(("at9tool", "at9tool.exe")) is not None
    if not has_enc:
        atrac_gui = _find_external_tool(("ATRACTool.exe",))
        if atrac_gui:
            out_cli = os.path.join(os.path.dirname(atrac_gui), "at9tool.exe")
            has_enc = _extract_at9tool_from_atractool(atrac_gui, out_cli)
    return has_dec, has_enc

def encode_wav_to_at9(wav_path: str, out_at9_path: str, bitrate: int = 144) -> None:
    """Кодирует WAV в Sony ATRAC9 (.at9) через консольный at9tool.exe (нативно на Windows или через wine на Linux/macOS)."""
    os.makedirs(os.path.dirname(os.path.abspath(out_at9_path)), exist_ok=True)
    at9_exe = _find_external_tool(("at9tool", "at9tool.exe"))
    if not at9_exe:
        atrac_gui = _find_external_tool(("ATRACTool.exe",))
        if atrac_gui:
            cand_cli = os.path.join(os.path.dirname(atrac_gui), "at9tool.exe")
            if _extract_at9tool_from_atractool(atrac_gui, cand_cli):
                at9_exe = cand_cli

    if not at9_exe:
        raise RuntimeError(
            "at9tool.exe was not found in 'tools/' or the OCRAM root folder.\n"
            "Click 'Download Automatically' on the Audio & Music notice."
        )

    br_str = str(int(bitrate))
    base_args = [at9_exe, "-e", "-br", br_str, wav_path, out_at9_path]

    if platform.system() == "Windows" or not at9_exe.lower().endswith(".exe"):
        res = subprocess.run(base_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(out_at9_path):
            return
        raise RuntimeError("at9tool failed to encode WAV to .at9 (ensure WAV is 16-bit PCM 44100/48000 Hz).")

    if not shutil.which("wine"):
        raise RuntimeError(
            "'wine' is required on Linux/macOS to run the native Windows CLI binary 'at9tool.exe'.\n"
            "Install wine (e.g. 'sudo apt install wine') and try again."
        )

    res = subprocess.run(["wine"] + base_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode == 0 and os.path.exists(out_at9_path):
        return
    raise RuntimeError("at9tool.exe failed to encode WAV via wine (ensure WAV is 16-bit PCM 44100/48000 Hz).")



def get_vag_header_info(vag_path: str) -> tuple[int, int]:
    with open(vag_path, "rb") as f:
        hdr = f.read(64)
    if len(hdr) >= 32 and hdr[:4] == b"VAGp":
        _, sample_rate = struct.unpack(">II", hdr[12:20])
        ch_byte = hdr.__getitem__(30)
        channels = ch_byte if ch_byte in (1, 2) else 1
        return (sample_rate if sample_rate > 0 else 44100), channels
    return 44100, 2

def extract_xnb_sound_to_wav(xnb_path: str, out_wav_path: str) -> bool:
    with open(xnb_path, "rb") as f:
        data = f.read()

    if len(data) < 40 or data[:3] != b"XNB":
        return False

    fmt_offset = -1
    for i in range(14, min(len(data) - 24, 256)):
        w_format_tag, n_channels, n_samples_per_sec, n_avg_bytes, n_block_align, w_bits, cb_size = struct.unpack_from("<HHIIHHH", data, i)
        if w_format_tag == 1 and n_channels in (1, 2) and 8000 <= n_samples_per_sec <= 96000 and w_bits in (8, 16):
            if n_block_align == n_channels * (w_bits // 8) and n_avg_bytes == n_samples_per_sec * n_block_align:
                fmt_offset = i
                break

    if fmt_offset < 0:
        return False

    _, n_channels, sample_rate, _, _, w_bits, cb_size = struct.unpack_from("<HHIIHHH", data, fmt_offset)
    data_len_pos = fmt_offset + 18 + cb_size
    if data_len_pos + 4 > len(data):
        return False

    pcm_size, = struct.unpack_from("<I", data, data_len_pos)
    pcm_data = data[data_len_pos + 4 : data_len_pos + 4 + pcm_size]
    if not pcm_data:
        return False

    os.makedirs(os.path.dirname(os.path.abspath(out_wav_path)), exist_ok=True)
    with wave.open(out_wav_path, "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(w_bits // 8)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return True

def _encode_single_channel_vag(pcm: np.ndarray) -> bytearray:
    pad_len = (28 - (len(pcm) % 28)) % 28
    if pad_len > 0:
        pcm = np.concatenate([pcm, np.zeros(pad_len, dtype=np.float64)])

    vag_body = bytearray(b"\x00" * 16)
    hist_1, hist_2 = 0.0, 0.0
    total_blocks = len(pcm) // 28

    for b_idx in range(total_blocks):
        chunk = pcm[b_idx * 28 : (b_idx + 1) * 28]
        best_pred, best_shift, best_err = 0, 0, 1e18
        best_nibbles = None
        best_end_hist = (hist_1, hist_2)

        for pred_i, (f0, f1) in enumerate(VAG_ENC_F):
            t_h1, t_h2 = hist_1, hist_2
            max_abs = 0.0
            for s in chunk:
                diff = s - (t_h1 * f0 + t_h2 * f1)
                if abs(diff) > max_abs:
                    max_abs = abs(diff)
                t_h2, t_h1 = t_h1, s

            shift = 0
            while shift < 12 and (max_abs * (1 << shift)) < 28672.0:
                shift += 1

            q_h1, q_h2 = hist_1, hist_2
            err_sum = 0.0
            nibbles = []
            for s in chunk:
                pred = q_h1 * f0 + q_h2 * f1
                diff = s - pred
                n_val = max(-8, min(7, int(round((diff * (1 << shift)) / 4096.0))))
                nibbles.append(n_val & 0x0F)

                rec = (float(n_val << 12) / float(1 << shift)) + pred
                rec = max(-32768.0, min(32767.0, rec))
                err_sum += (s - rec) ** 2
                q_h2, q_h1 = q_h1, rec

            if err_sum < best_err:
                best_err, best_pred, best_shift = err_sum, pred_i, shift
                best_nibbles = nibbles
                best_end_hist = (q_h1, q_h2)

        hist_1, hist_2 = best_end_hist
        flag = 1 if b_idx == total_blocks - 1 else 0
        block_bytes = bytearray(16)
        block_bytes.__setitem__(0, ((best_pred & 0x0F) << 4) | (best_shift & 0x0F))
        block_bytes.__setitem__(1, flag)
        for j in range(14):
            lo = best_nibbles.__getitem__(j * 2)
            hi = best_nibbles.__getitem__(j * 2 + 1)
            block_bytes.__setitem__(2 + j, (hi << 4) | lo)
        vag_body.extend(block_bytes)

    vag_body.extend(b"\x00\x07" + b"\x00" * 14)
    return vag_body

def encode_wav_to_vag(wav_path: str, vag_path: str, target_rate: int = 0, target_channels: int = 2) -> None:
    with wave.open(wav_path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        raw_frames = wf.readframes(wf.getnframes())

    if sampwidth == 2:
        pcm = np.frombuffer(raw_frames, dtype=np.int16).astype(np.float64)
    elif sampwidth == 1:
        pcm = (np.frombuffer(raw_frames, dtype=np.uint8).astype(np.float64) - 128.0) * 256.0
    else:
        raise ValueError("Unsupported WAV bit depth.")

    pcm = pcm.reshape((-1, n_channels)) if n_channels > 1 else pcm.reshape((-1, 1))
    out_rate = target_rate if target_rate > 0 else framerate
    out_ch = target_channels if target_channels in (1, 2) else n_channels

    if out_ch == 2 and pcm.shape.__getitem__(1) == 1:
        pcm = np.column_stack((pcm[:, 0], pcm[:, 0]))
    elif out_ch == 1 and pcm.shape.__getitem__(1) > 1:
        pcm = pcm.mean(axis=1, keepdims=True)

    if out_rate != framerate and pcm.shape.__getitem__(0) > 1:
        old_len = pcm.shape.__getitem__(0)
        new_len = max(1, int(round(old_len * out_rate / framerate)))
        resampled = [np.interp(np.linspace(0, old_len - 1, new_len), np.arange(old_len), pcm[:, c]) for c in range(out_ch)]
        pcm = np.column_stack(resampled)

    if out_ch == 2:
        left_vag = _encode_single_channel_vag(pcm[:, 0])
        right_vag = _encode_single_channel_vag(pcm[:, 1])
        n_blk = min(len(left_vag), len(right_vag)) // 16
        vag_body = bytearray()
        for b in range(n_blk):
            vag_body.extend(left_vag[b * 16 : (b + 1) * 16])
            vag_body.extend(right_vag[b * 16 : (b + 1) * 16])
    else:
        vag_body = _encode_single_channel_vag(pcm[:, 0])

    name_bytes = os.path.splitext(os.path.basename(vag_path)).__getitem__(0).encode("ascii", errors="ignore")[:32].ljust(32, b"\x00")
    header = struct.pack(">4sIIII10sBB32s", b"VAGp", 0x00020001, 0, len(vag_body), out_rate, b"\x00" * 10, out_ch, 0, name_bytes)

    os.makedirs(os.path.dirname(os.path.abspath(vag_path)), exist_ok=True)
    with open(vag_path, "wb") as f:
        f.write(header)
        f.write(vag_body)

def convert_any_sound_to_vag(src_sound_path: str, out_vag_path: str, target_rate: int, target_channels: int, temp_dir: str) -> bool:
    ext = os.path.splitext(src_sound_path).__getitem__(1).lower()
    if ext == ".vag":
        os.makedirs(os.path.dirname(os.path.abspath(out_vag_path)), exist_ok=True)
        shutil.copy2(src_sound_path, out_vag_path)
        return True
    elif ext == ".wav":
        encode_wav_to_vag(src_sound_path, out_vag_path, target_rate=target_rate, target_channels=target_channels)
        return True
    elif ext == ".xnb":
        tmp_wav = os.path.join(temp_dir, "_tmp_xnb_sound.wav")
        if extract_xnb_sound_to_wav(src_sound_path, tmp_wav):
            encode_wav_to_vag(tmp_wav, out_vag_path, target_rate=target_rate, target_channels=target_channels)
            return True
    return False

def play_wav_async(wav_path: str, volume: float = 0.4) -> None:
    global _current_player_proc
    if not os.path.exists(wav_path):
        return

    stop_sound()

    play_target = wav_path
    try:
        with wave.open(wav_path, "rb") as wf:
            params = wf.getparams()
            raw = wf.readframes(wf.getnframes())
        if params.sampwidth == 2:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            scaled = np.clip(samples * max(0.0, min(1.0, volume)), -32768, 32767).astype(np.int16)
            scaled_path = os.path.join(os.path.dirname(os.path.abspath(wav_path)), "_vol_preview.wav")
            with wave.open(scaled_path, "wb") as out_wf:
                out_wf.setparams(params)
                out_wf.writeframes(scaled.tobytes())
            play_target = scaled_path
    except Exception:
        pass

    sys_name = platform.system()
    if sys_name == "Windows":
        import winsound
        winsound.PlaySound(play_target, winsound.SND_FILENAME | winsound.SND_ASYNC)
    elif sys_name == "Darwin":
        _current_player_proc = subprocess.Popen(["afplay", play_target])
    else:
        for player in ("paplay", "aplay"):
            if shutil.which(player):
                _current_player_proc = subprocess.Popen([player, play_target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                break

def download_missing_audio_tools(log_cb=None) -> list[str]:
    """Скачивает vgmstream-cli и at9tool.exe (из ATRACTool) в папку tools/."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tools_dir = os.path.join(base_dir, "tools")
    os.makedirs(tools_dir, exist_ok=True)

    sys_name = platform.system().lower()
    has_dec, has_enc = check_audio_tools_status()
    installed = []

    def download_file(url: str, dest_path: str):
        req = urllib.request.Request(url, headers={"User-Agent": "OCRAM-ModManager"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest_path, "wb") as out_f:
            shutil.copyfileobj(resp, out_f)

    # 1. Скачиваем vgmstream-cli
    if not has_dec:
        if log_cb:
            log_cb("Downloading vgmstream-cli...")
        rel_base = "https://github.com/" + "vgmstream/vgmstream-releases/releases/download/nightly/"
        if sys_name == "windows":
            url = rel_base + "vgmstream-win64.zip"
            arc = os.path.join(tools_dir, "vgmstream-win64.zip")
        elif sys_name == "linux":
            url = rel_base + "vgmstream-linux-cli.tar.gz"
            arc = os.path.join(tools_dir, "vgmstream-linux-cli.tar.gz")
        elif sys_name == "darwin":
            url = rel_base + "vgmstream-mac-cli.tar.gz"
            arc = os.path.join(tools_dir, "vgmstream-mac-cli.tar.gz")
        else:
            url = None

        if url:
            download_file(url, arc)
            if arc.endswith(".zip"):
                with zipfile.ZipFile(arc, "r") as zf:
                    for member in zf.namelist():
                        bname = os.path.basename(member)
                        if bname.lower().endswith((".exe", ".dll")):
                            zf.extract(member, tools_dir)
                            extracted = os.path.join(tools_dir, member)
                            final_p = os.path.join(tools_dir, bname)
                            if extracted != final_p:
                                shutil.move(extracted, final_p)
            elif arc.endswith(".tar.gz"):
                with tarfile.open(arc, "r:gz") as tf:
                    for member in tf.getmembers():
                        if member.isfile() and os.path.basename(member.name) == "vgmstream-cli":
                            f_obj = tf.extractfile(member)
                            if f_obj:
                                final_p = os.path.join(tools_dir, "vgmstream-cli")
                                with open(final_p, "wb") as out_f:
                                    out_f.write(f_obj.read())
                                os.chmod(final_p, 0o755)
            if os.path.exists(arc):
                os.remove(arc)
            installed.append("vgmstream-cli")

    # 2. Скачиваем ATRACTool.exe по прямой ссылке из релиза XyLe-GBP/ATRACTool
    if not has_enc:
        if log_cb:
            log_cb("Downloading ATRACTool.exe...")
        atrac_url = "https://github.com/" + "XyLe-GBP/ATRACTool/releases/download/20210214/ATRACTool-20210214.zip"
        arc = os.path.join(tools_dir, "ATRACTool-20210214.zip")
        download_file(atrac_url, arc)
        with zipfile.ZipFile(arc, "r") as zf:
            for member in zf.namelist():
                bname = os.path.basename(member)
                if bname.lower() in ("atractool.exe", "at9tool.exe", "at9tool"):
                    zf.extract(member, tools_dir)
                    extracted = os.path.join(tools_dir, member)
                    final_p = os.path.join(tools_dir, bname)
                    if extracted != final_p:
                        shutil.move(extracted, final_p)
                    if platform.system() != "Windows":
                        os.chmod(final_p, 0o755)
        _extract_at9tool_from_atractool()
        if os.path.exists(arc):
            os.remove(arc)
        if os.path.exists(os.path.join(tools_dir, "ATRACTool.exe")):
            installed.append("ATRACTool.exe")




    return installed

