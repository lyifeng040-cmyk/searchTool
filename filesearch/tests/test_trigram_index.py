import pytest

from filesearch.core.trigram_index import TrigramIndex


def make_doc(filename, dir_path="/tmp"):
    return {"filename": filename, "dir_path": dir_path, "fullpath": f"{dir_path}/{filename}", "size": 0, "mtime": 0, "type_code": 2}


def test_build_and_query_basic():
    docs = [make_doc("README.md", "/repo"), make_doc("main.py", "/repo/project"), make_doc("notes.txt", "/repo")]
    idx = TrigramIndex()
    idx.build_index(docs)
    # query README
    res = idx.query("readme")
    # ensure README doc is in results
    docs_found = idx.get_docs(res)
    fnames = [d["filename"] for d in docs_found]
    assert "README.md" in fnames


def test_add_remove_update():
    idx = TrigramIndex()
    d1 = make_doc("a_file.txt", "/x")
    id1 = idx.add_doc(d1)
    assert id1 == 1
    res = idx.query("a_file")
    assert id1 in res

    # update: ensure updated doc matches new query
    idx.update_doc(id1, make_doc("b_file.txt", "/x"))
    res3 = idx.query("b_file")
    assert id1 in res3

    # remove
    idx.remove_doc(id1)
    res4 = idx.query("b_file")
    assert id1 not in res4


def test_query_no_matches():
    idx = TrigramIndex()
    idx.build_index([make_doc("foo.txt"), make_doc("bar.txt")])
    assert idx.query("zzz") == []
