import os
import wave
import shutil
import struct
import platform
import subprocess
import numpy as np

HEVAG_F = (
    (0.0, 0.0),
    (60.0 / 64.0, 0.0),
    (115.0 / 64.0, -52.0 / 64.0),
    (98.0 / 64.0, -55.0 / 64.0),
    (122.0 / 64.0, -60.0 / 64.0),
    (60.0 / 64.0, 0.0),
    (115.0 / 64.0, -52.0 / 64.0),
    (98.0 / 64.0, -55.0 / 64.0),
)

_current_player_proc = None

def stop_sound() -> None:
    """Останавливает текущее воспроизведение звука."""
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

def decode_vag_to_wav(vag_path: str, wav_path: str) -> tuple[int, int]:
    """Декодирует Sony PS Vita HEVAG/VAG (.vag) в 16-bit PCM WAV. Возвращает (sample_rate, channels)."""
    with open(vag_path, "rb") as f:
        hdr = f.read(64)
        if len(hdr) < 64 or hdr[:4] != b"VAGp":
            raise ValueError(f"Invalid VAG header in {os.path.basename(vag_path)}")

        ver_major = hdr.__getitem__(5)
        is_hevag = ver_major >= 2
        data_size, sample_rate = struct.unpack(">II", hdr[12:20])
        channels = hdr.__getitem__(30)
        if channels not in (1, 2):
            channels = 1

        if not is_hevag:
            f.seek(48)
        raw_blocks = f.read(data_size if data_size > 0 else -1)

    if sample_rate == 0:
        sample_rate = 44100

    os.makedirs(os.path.dirname(os.path.abspath(wav_path)), exist_ok=True)

    # Поканальное декодирование с 16-байтным интерливингом (блок 0 = L, блок 1 = R)
    ch_samples = [[] for _ in range(channels)]
    hist = [[0.0, 0.0] for _ in range(channels)]

    total_blocks = len(raw_blocks) // 16
    for b_idx in range(total_blocks):
        ch = b_idx % channels
        block = raw_blocks[b_idx * 16 : (b_idx + 1) * 16]

        coef_byte = block.__getitem__(0)
        flags = block.__getitem__(1)

        if (flags & 0x0F) == 7:
            if channels == 1:
                break
            continue

        predict_nr = (coef_byte >> 4) & 0x07
        shift_factor = coef_byte & 0x0F
        if shift_factor > 12:
            shift_factor = 9

        f0, f1 = HEVAG_F.__getitem__(predict_nr)
        s_1, s_2 = hist.__getitem__(ch)
        out_list = ch_samples.__getitem__(ch)

        for i in range(2, 16):
            byte_val = block.__getitem__(i)
            # В PS ADPCM / HEVAG младший полубайт (lo) всегда декодируется первым, старший (hi) — вторым
            for nibble in (byte_val & 0x0F, (byte_val >> 4) & 0x0F):
                if nibble >= 8:
                    nibble -= 16
                s_val = float(nibble << 12) / float(1 << shift_factor)
                sample = s_val + s_1 * f0 + s_2 * f1
                s_2 = s_1
                s_1 = sample
                out_list.append(max(-32768, min(32767, int(round(sample)))))

        hist[ch] = [s_1, s_2]

    if channels == 2:
        min_len = min(len(ch_samples.__getitem__(0)), len(ch_samples.__getitem__(1)))
        left = np.array(ch_samples.__getitem__(0)[:min_len], dtype=np.int16)
        right = np.array(ch_samples.__getitem__(1)[:min_len], dtype=np.int16)
        pcm_bytes = np.column_stack((left, right)).ravel().tobytes()
    else:
        pcm_bytes = np.array(ch_samples.__getitem__(0), dtype=np.int16).tobytes()

    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)

    return sample_rate, channels

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

        for pred_i in range(5):
            f0, f1 = HEVAG_F.__getitem__(pred_i)
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
        block_bytes = ((best_pred & 0x0F) << 4) | (best_shift & 0x0F)
        block_bytes = flag
        for j in range(14):
            lo = best_nibbles.__getitem__(j * 2)
            hi = best_nibbles.__getitem__(j * 2 + 1)
            block_bytes[2 + j] = (hi << 4) | lo
        vag_body.extend(block_bytes)

    vag_body.extend(b"\x00\x07" + b"\x00" * 14)
    return vag_body


def extract_xnb_sound_to_wav(xnb_path: str, out_wav_path: str) -> bool:
    """Извлекает аудиопоток PCM из XNA SoundEffect .xnb файла в стандартный .wav."""
    with open(xnb_path, "rb") as f:
        data = f.read()

    if len(data) < 40 or data[:3] != b"XNB":
        return False

    # Находим стандартный 18-байтный заголовок WAVEFORMATEX (tag = 1 для PCM, 1 или 2 канала)
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

def convert_any_sound_to_vag(src_sound_path: str, out_vag_path: str, target_rate: int, target_channels: int, temp_dir: str) -> bool:
    """Конвертирует .wav или .xnb звук в PS Vita .vag."""
    ext = os.path.splitext(src_sound_path).__getitem__(1).lower()
    if ext == ".wav":
        encode_wav_to_vag(src_sound_path, out_vag_path, target_rate=target_rate, target_channels=target_channels)
        return True
    elif ext == ".xnb":
        tmp_wav = os.path.join(temp_dir, "_tmp_xnb_sound.wav")
        if extract_xnb_sound_to_wav(src_sound_path, tmp_wav):
            encode_wav_to_vag(tmp_wav, out_vag_path, target_rate=target_rate, target_channels=target_channels)
            return True
    return False


def encode_wav_to_vag(wav_path: str, vag_path: str, target_rate: int = 0, target_channels: int = 2) -> None:
    """Кодирует WAV в PS Vita HEVAG (.vag) с 16-байтным интерливингом."""
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

def play_wav_async(wav_path: str, volume: float = 0.4) -> None:
    """Останавливает прошлый звук, применяет громкость (0.0..1.0) и проигрывает WAV."""
    global _current_player_proc
    if not os.path.exists(wav_path):
        return

    stop_sound()

    # Создаём временный файл с отрегулированной громкостью, чтобы не било по ушам
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

def decode_audio_any_to_wav(src_path: str, wav_path: str) -> tuple[int, int]:
    """Универсальный декодер для .vag (HEVAG) и .at9 (ATRAC9) в WAV."""
    os.makedirs(os.path.dirname(os.path.abspath(wav_path)), exist_ok=True)
    ext = os.path.splitext(src_path)[1].lower()

    # Если рядом лежит vgmstream-cli — используем его в приоритете и для .at9, и для .vag (100% студийное качество)
    vgm_exe = _find_external_tool(("vgmstream-cli", "vgmstream-cli.exe"))
    if vgm_exe:
        res = subprocess.run([vgm_exe, "-i", "-o", wav_path, src_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(wav_path):
            with wave.open(wav_path, "rb") as wf:
                return wf.getframerate(), wf.getnchannels()

    if ext == ".vag":
        return decode_vag_to_wav(src_path, wav_path)

    at9_exe = _find_external_tool(("at9tool", "at9tool.exe"))
    if at9_exe:
        res = subprocess.run([at9_exe, "-d", src_path, wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(wav_path):
            with wave.open(wav_path, "rb") as wf:
                return wf.getframerate(), wf.getnchannels()

    raise RuntimeError(
        "To play or decode Sony ATRAC9 (.at9) music (and get lossless .vag playback), "
        "download vgmstream CLI from:\nhttps://vgmstream.org/\n\n"
        "Place 'vgmstream-cli' (or 'vgmstream-cli.exe') into the OCRAM root folder or 'tools/' folder."
    )

