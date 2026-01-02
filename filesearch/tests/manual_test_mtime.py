import time
from filesearch.core.rust_search import get_rust_search_engine

if __name__ == '__main__':
    eng = get_rust_search_engine()
    assert eng is not None, 'Rust engine not available'
    now = time.time()
    seven_days_ago = now - 7*24*3600
    # Test on C drive only to keep output short
    res = eng.search_by_mtime_range('C', seven_days_ago, 9e18, 5000)
    print(f"Got {len(res)} results in last 7 days on C:")
    for r in res[:10]:
        print(r)
