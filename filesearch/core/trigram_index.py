"""Lightweight in-memory trigram inverted index prototype.

This is a non-persistent, in-memory implementation intended as a prototype
for candidate selection. It provides simple APIs:

- build_index(docs_iter): builds index from iterable of doc dicts
- add_doc(doc): add single doc (assigns doc_id)
- remove_doc(doc_id): remove doc and its postings
- update_doc(doc_id, doc): update metadata and postings
- query(qstr, top_k): return list of doc_ids ranked by shared trigram count
- get_docs(doc_ids): return metadata list

Doc format used here (for prototype):
  { 'filename': str, 'dir_path': str, 'fullpath': str, 'size': int, 'mtime': int, 'type_code': int }

This module intentionally keeps memory structures simple (dicts/sets) for clarity.
Persistence and compression are left for later stages.
"""

from typing import Iterable, List, Dict, Set, Tuple
import re


def _normalize_text(s: str) -> str:
    if s is None:
        return ""
    return str(s).lower()


def _make_ngrams(s: str, n: int = 3) -> Set[str]:
    """Return set of n-grams for given string. For short strings, includes shorter n-grams."""
    s = _normalize_text(s)
    # collapse whitespace
    s = re.sub(r"\s+", " ", s)
    out = set()
    L = len(s)
    if L == 0:
        return out
    # if too short, include all prefix ngrams
    for k in range(1, min(n, L) + 1):
        for i in range(0, L - k + 1):
            out.add(s[i:i + k])
    if L > n:
        for i in range(0, L - n + 1):
            out.add(s[i:i + n])
    return out


class TrigramIndex:
    def __init__(self):
        # doc_id counter
        self._next_id = 1
        # doc_id -> doc metadata dict
        self.docs: Dict[int, Dict] = {}
        # trigram -> set(doc_id)
        self.inv: Dict[str, Set[int]] = {}
        # mapping doc_id -> set(trigrams) for fast update
        self._doc_trigrams: Dict[int, Set[str]] = {}

    def _add_posting(self, trig: str, doc_id: int):
        self.inv.setdefault(trig, set()).add(doc_id)

    def _remove_posting(self, trig: str, doc_id: int):
        s = self.inv.get(trig)
        if not s:
            return
        s.discard(doc_id)
        if not s:
            self.inv.pop(trig, None)

    def add_doc(self, doc: Dict) -> int:
        """Add a single doc and return assigned doc_id."""
        doc_id = self._next_id
        self._next_id += 1
        self.docs[doc_id] = dict(doc)
        # index filename and dir_path
        text = f"{doc.get('filename','')} {doc.get('dir_path','')}"
        trigs = _make_ngrams(text)
        self._doc_trigrams[doc_id] = trigs
        for t in trigs:
            self._add_posting(t, doc_id)
        return doc_id

    def remove_doc(self, doc_id: int):
        if doc_id not in self.docs:
            return
        trigs = self._doc_trigrams.get(doc_id, set())
        for t in trigs:
            self._remove_posting(t, doc_id)
        self._doc_trigrams.pop(doc_id, None)
        self.docs.pop(doc_id, None)

    def update_doc(self, doc_id: int, doc: Dict):
        # simple replace: remove old postings and add new
        if doc_id not in self.docs:
            raise KeyError(doc_id)
        old_trigs = self._doc_trigrams.get(doc_id, set())
        for t in old_trigs:
            self._remove_posting(t, doc_id)
        text = f"{doc.get('filename','')} {doc.get('dir_path','')}"
        new_trigs = _make_ngrams(text)
        self._doc_trigrams[doc_id] = new_trigs
        for t in new_trigs:
            self._add_posting(t, doc_id)
        self.docs[doc_id] = dict(doc)

    def build_index(self, docs_iter: Iterable[Dict]) -> None:
        # reset
        self._next_id = 1
        self.docs.clear()
        self.inv.clear()
        self._doc_trigrams.clear()
        for doc in docs_iter:
            self.add_doc(doc)

    def query(self, q: str, top_k: int = 200) -> List[int]:
        """Return list of doc_ids ranked by shared ngram count (descending).

        This is a simple scoring: count of shared trigrams between query and doc.
        """
        if not q:
            return list(self.docs.keys())[:top_k]
        trigs = _make_ngrams(q)
        if not trigs:
            return []
        # accumulate counts
        counts: Dict[int, int] = {}
        for t in trigs:
            posting = self.inv.get(t)
            if not posting:
                continue
            for doc_id in posting:
                counts[doc_id] = counts.get(doc_id, 0) + 1
        if not counts:
            return []
        # convert to list and normalize by candidate trig size (simple)
        scored: List[Tuple[int, float]] = []
        for doc_id, cnt in counts.items():
            denom = max(1, len(self._doc_trigrams.get(doc_id, ())))
            score = cnt / denom
            scored.append((doc_id, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [doc_id for doc_id, sc in scored[:top_k]]

    def get_docs(self, doc_ids: List[int]) -> List[Dict]:
        out = []
        for did in doc_ids:
            d = self.docs.get(did)
            if d is not None:
                out.append(dict(d))
        return out


__all__ = ["TrigramIndex"]
