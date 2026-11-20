import os
import zlib
from io import BytesIO
from struct import pack, unpack
from dataclasses import dataclass, field
from typing import BinaryIO, Optional

def hash_name(string: str, do_preceding_path_check: bool = True) -> int:
    if not string.startswith('/') and do_preceding_path_check:
        string = '/' + string
    hashed = 5381
    for char in string.lower():
        hashed = ((hashed << 5) + hashed) + ord(char)
    return (hashed & 0x3FFFFFFF) | 0x40000000

def read_int(fp: BinaryIO) -> int:
    return unpack('<i', fp.read(4))[0]

def read_string(fp: BinaryIO) -> str:
    str_len = read_int(fp)
    return fp.read(str_len).decode('utf-8', errors='replace')

def write_int(fp: BinaryIO, val: int) -> None:
    fp.write(pack('<i', val))

def write_string(fp: BinaryIO, string: str) -> None:
    encoded = string.encode('utf-8')
    write_int(fp, len(encoded))
    fp.write(encoded)

@dataclass
class VFile:
    name_hash: int
    id: int
    compress_type: int
    parent_id: int
    offset: int
    size: int
    name: str = ""
    parent: Optional['VDirectory'] = field(default=None, repr=False)

    @property
    def rel_path(self) -> str:
        return os.path.join(self.parent.rel_path, self.name) if self.parent else self.name

    def write(self, fp: BinaryIO) -> None:
        write_int(fp, self.name_hash)
        write_int(fp, self.id)
        write_int(fp, self.compress_type)
        write_int(fp, self.parent_id)
        write_int(fp, self.offset)
        write_int(fp, self.size)

@dataclass
class VDirectory:
    name_hash: int
    id: int
    parent_id: int
    unk1: int
    file_id_start: int
    name: str = ""
    parent: Optional['VDirectory'] = field(default=None, repr=False)
    folders: list['VDirectory'] = field(default_factory=list, repr=False)
    files: list[VFile] = field(default_factory=list, repr=False)

    @property
    def rel_path(self) -> str:
        if self.parent is None:
            return ""
        return os.path.join(self.parent.rel_path, self.name)

    def write(self, fp: BinaryIO) -> None:
        write_int(fp, self.name_hash)
        write_int(fp, self.id)
        write_int(fp, self.parent_id)
        write_int(fp, self.unk1)
        write_int(fp, self.file_id_start)

class VFSArchive:
    def __init__(self) -> None:
        self.folders: list[VDirectory] = []
        self.files: list[VFile] = []
        self.root_id: int = 0

    def unpack_vfs(self, vfs_path: str, out_dir: str, log_cb=None) -> None:
        self.folders.clear()
        self.files.clear()
        with open(vfs_path, 'rb') as fp:
            if fp.read(4) != b'VFS2':
                raise ValueError("Selected file is not a valid PS Vita VFS2 archive.")

            num_folders = read_int(fp)
            for _ in range(num_folders):
                self.folders.append(VDirectory(
                    name_hash=read_int(fp), id=read_int(fp), parent_id=read_int(fp),
                    unk1=read_int(fp), file_id_start=read_int(fp)
                ))
            self.folders.sort(key=lambda f: f.id)

            num_files = read_int(fp)
            for _ in range(num_files):
                self.files.append(VFile(
                    name_hash=read_int(fp), id=read_int(fp), compress_type=read_int(fp),
                    parent_id=read_int(fp), offset=read_int(fp), size=read_int(fp)
                ))
            self.files.sort(key=lambda f: f.id)

            name_table_offset = read_int(fp)
            data_offset = fp.tell()

            fp.seek(name_table_offset, 0)
            if read_int(fp) != num_files:
                raise ValueError("Corrupted VFS2 file name table.")
            for f_obj in self.files:
                f_obj.name = read_string(fp)

            if read_int(fp) != num_folders:
                raise ValueError("Corrupted VFS2 folder name table.")
            for d_obj in self.folders:
                d_obj.name = read_string(fp)

            self._link_tree()
            os.makedirs(out_dir, exist_ok=True)
            if log_cb:
                log_cb(f"Unpacking {num_files} files from {os.path.basename(vfs_path)}...")

            for f_obj in self.files:
                fp.seek(data_offset + f_obj.offset, 0)
                decomp_size = read_int(fp) if f_obj.compress_type > 0 else 0
                raw_data = fp.read(f_obj.size)
                if f_obj.compress_type == 2:
                    try:
                        raw_data = zlib.decompress(raw_data)
                    except zlib.error:
                        # Обход нулевой чексуммы Adler-32 в сторонних сборках (пропускаем 2-байтный заголовок zlib)
                        raw_data = zlib.decompressobj(-15).decompress(raw_data[2:])
                    if len(raw_data) != decomp_size:
                        raise ValueError(f"Size mismatch decompressing {f_obj.name}: {len(raw_data)} != {decomp_size}")


                dest_path = os.path.join(out_dir, f_obj.rel_path)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with open(dest_path, 'wb') as out_fp:
                    out_fp.write(raw_data)

    def pack_vfs(self, src_dir: str, out_vfs_path: str, log_cb=None) -> None:
        self.folders.clear()
        self.files.clear()
        self.root_id = self._add_folder('', -1, 0)

        walk_list = list(os.walk(src_dir, topdown=True))
        walk_list.sort(key=lambda t: os.path.relpath(t[0], src_dir).count(os.sep))

        folder_map = {'': self.root_id}
        for root, dirs, files in walk_list:
            dirs.sort()
            files.sort()
            rel_dir = os.path.relpath(root, src_dir)
            if rel_dir == '.':
                rel_dir = ''
            posix_dir = rel_dir.replace(os.sep, '/')
            parent_id = folder_map[posix_dir]

            for d_name in dirs:
                sub_posix = f"{posix_dir}/{d_name}" if posix_dir else d_name
                folder_map[sub_posix] = self._add_folder(d_name, parent_id, hash_name(sub_posix))

            for f_name in files:
                if f_name in ('.DS_Store', 'Thumbs.db'):
                    continue
                file_posix = f"{posix_dir}/{f_name}" if posix_dir else f_name
                comp_type = 0 if f_name.lower().endswith('.at9') else 2
                self.files.append(VFile(
                    name_hash=hash_name(file_posix), id=len(self.files),
                    compress_type=comp_type, parent_id=parent_id, offset=-1, size=-1, name=f_name
                ))

        self._link_tree()
        if log_cb:
            log_cb(f"Compressing {len(self.files)} files into {os.path.basename(out_vfs_path)}...")

        buf = BytesIO()
        self._write_folder_data(buf, self.folders[self.root_id], src_dir)

        os.makedirs(os.path.dirname(os.path.abspath(out_vfs_path)), exist_ok=True)
        with open(out_vfs_path, 'wb') as fp:
            fp.write(b'VFS2')
            write_int(fp, len(self.folders))
            for d_obj in self.folders:
                d_obj.write(fp)
            write_int(fp, len(self.files))
            for f_obj in self.files:
                f_obj.write(fp)

            name_table_offset = fp.tell() + 4 + buf.getbuffer().nbytes
            write_int(fp, name_table_offset)
            buf.seek(0)
            fp.write(buf.read())

            write_int(fp, len(self.files))
            for f_obj in self.files:
                write_string(fp, f_obj.name)
            write_int(fp, len(self.folders))
            for d_obj in self.folders:
                write_string(fp, d_obj.name)

    def _add_folder(self, name: str, parent_id: int, name_hash: int) -> int:
        fid = len(self.folders)
        unk1 = 1 if name == '' else (9 if name == 'ui' else -1)
        self.folders.append(VDirectory(
            name_hash=name_hash, id=fid, parent_id=parent_id, unk1=unk1, file_id_start=-1, name=name
        ))
        return fid

    def _link_tree(self) -> None:
        for d_obj in self.folders:
            if d_obj.parent_id >= 0:
                parent = self.folders[d_obj.parent_id]
                d_obj.parent = parent
                parent.folders.append(d_obj)
            else:
                self.root_id = d_obj.id
        for f_obj in self.files:
            parent = self.folders[f_obj.parent_id]
            f_obj.parent = parent
            parent.files.append(f_obj)

    def _write_folder_data(self, buf: BytesIO, folder: VDirectory, src_dir: str) -> None:
        for f_obj in folder.files:
            if folder.file_id_start < 0:
                folder.file_id_start = f_obj.id
            offset = buf.tell()
            full_path = os.path.join(src_dir, f_obj.rel_path)
            with open(full_path, 'rb') as fp:
                raw = fp.read()
            if f_obj.compress_type == 2:
                write_int(buf, len(raw))
                raw = zlib.compress(raw, level=1)
            buf.write(raw)
            f_obj.offset = offset
            f_obj.size = len(raw)

        for sub in folder.folders:
            self._write_folder_data(buf, sub, src_dir)
