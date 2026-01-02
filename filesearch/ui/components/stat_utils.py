import os
from typing import List, Dict, Iterable


def build_batch_entries(page_items: Iterable[Dict]) -> List[List]:
    """Create tmp entries expected by `_batch_stat_files` from page_items.

    Each entry follows the existing legacy structure used by the app.
    Returns a list of lists which can be passed to the batch stat function.
    """
    tmp = []
    for it in page_items:
        fullpath = it.get("fullpath", "")
        filename = it.get("filename", "")
        dir_path = it.get("dir_path", "")
        is_dir = 1 if it.get("type_code") == 0 else 0
        ext = "" if is_dir else os.path.splitext(filename)[1].lower()
        tmp.append([
            filename,
            filename.lower(),
            fullpath,
            dir_path,
            ext,
            int(it.get("size", 0) or 0),
            float(it.get("mtime", 0) or 0),
            is_dir,
        ])
    return tmp


def apply_batch_results(page_items: List[Dict], tmp: List[List]) -> None:
    """Apply batch stat results (tmp list) back into `page_items` in-place.

    Assumes `tmp` has been updated in-place by the stat backend (size at index 5,
    mtime at index 6).
    """
    for it, t in zip(page_items, tmp):
        it["size"] = t[5]
        it["mtime"] = t[6]
