import os
import json
import struct

def read_7bit_int(buf: bytes, pos: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while pos < len(buf):
        b = buf.__getitem__(pos)
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return val, pos

def write_7bit_int(val: int) -> bytearray:
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val > 0:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return out

def load_str_file(str_path: str) -> list[str]:
    """Читает бинарный файл strings_*.str от PS Vita Terraria и возвращает список строк."""
    with open(str_path, "rb") as f:
        data = f.read()

    ver, block_sz = struct.unpack("<II", data[:8])
    str_blob = data[8 : 8 + block_sz]

    strings = []
    pos = 0
    while pos < len(str_blob):
        slen, pos = read_7bit_int(str_blob, pos)
        raw_s = str_blob[pos : pos + slen]
        pos += slen
        strings.append(raw_s.decode("utf-8", errors="replace"))

    return strings

def save_str_file(str_path: str, strings: list[str]) -> None:
    """Упаковывает список строк обратно в бинарный strings_*.str с пересчётом таблицы смещений."""
    str_blob = bytearray()
    offsets = []

    for s in strings:
        offsets.append(len(str_blob))
        raw_s = s.encode("utf-8")
        str_blob.extend(write_7bit_int(len(raw_s)))
        str_blob.extend(raw_s)

    # Перестраиваем таблицу смещений в хвосте файла (начиная со смещений >= 128 или всех смещений)
    tail_blob = bytearray()
    for off in offsets:
        if off >= 128:
            tail_blob.extend(write_7bit_int(off))

    header = struct.pack("<II", 1, len(str_blob))
    os.makedirs(os.path.dirname(os.path.abspath(str_path)), exist_ok=True)
    with open(str_path, "wb") as f:
        f.write(header)
        f.write(str_blob)
        f.write(tail_blob)
