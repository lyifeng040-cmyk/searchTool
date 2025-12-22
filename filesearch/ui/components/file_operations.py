import os
import shutil
import struct
import subprocess
from typing import List, Tuple

try:
    import win32clipboard  # type: ignore
    import win32con  # type: ignore
except Exception:
    win32clipboard = None
    win32con = None

try:
    import send2trash  # type: ignore
except Exception:
    send2trash = None


def open_file(path: str):
    if not path:
        return
    try:
        os.startfile(path)
    except Exception as e:
        raise


def open_folder_and_select(path: str):
    if not path:
        return
    try:
        subprocess_cmd = f'explorer /select,"{path}"'
        subprocess.Popen(subprocess_cmd)
    except Exception:
        raise


def copy_paths_to_clipboard(app, paths: List[str]):
    """Copy plain paths to clipboard"""
    text = "\n".join(paths)
    app.clipboard().setText(text)


def copy_files_to_clipboard_win32(paths: List[str]) -> None:
    if not win32clipboard or not win32con:
        raise RuntimeError("pywin32 is not available")
    files = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not files:
        return
    file_str = "\0".join(files) + "\0\0"
    data = struct.pack("IIIII", 20, 0, 0, 0, 1) + file_str.encode("utf-16le")
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
    win32clipboard.CloseClipboard()


def delete_items(items: List[dict], use_send2trash: bool = True) -> Tuple[int, List[str], set, List[str]]:
    """Delete items (files/folders).
    Returns (deleted_count, failed_names, remove_exact_set, remove_prefix_list)
    """
    deleted = 0
    failed = []
    remove_exact = set()
    remove_prefix = []

    for item in items:
        fp = os.path.normpath(item["fullpath"])
        remove_exact.add(fp)
        if item.get("type_code") == 0 or item.get("is_dir") == 1:
            prefix = fp.rstrip("\\/") + os.sep
            remove_prefix.append(prefix)

    for item in items:
        try:
            if use_send2trash and send2trash:
                send2trash.send2trash(item["fullpath"])
            else:
                if item.get("type_code") == 0 or item.get("is_dir") == 1:
                    shutil.rmtree(item["fullpath"])
                else:
                    os.remove(item["fullpath"])
            deleted += 1
        except Exception:
            failed.append(item.get("filename", os.path.basename(item.get("fullpath", ""))))

    return deleted, failed, remove_exact, remove_prefix
