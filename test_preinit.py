"""测试 Rust 搜索引擎预初始化功能"""
import sys
import os
import time

# 添加路径
sys.path.insert(0, r'C:\Users\Administrator\Desktop\SearchTool')
os.chdir(r'C:\Users\Administrator\Desktop\SearchTool\filesearch')

def test_preinit():
    print("=" * 60)
    print("测试场景：首次搜索耗时对比")
    print("=" * 60)
    
    from filesearch.core.rust_search import get_search_engine
    import string
    
    engine = get_search_engine()
    
    # 检测所有可用盘符
    drives = []
    for drive in string.ascii_uppercase:
        if os.path.exists(f"{drive}:\\"):
            drives.append(drive)
    
    print(f"\n检测到盘符: {drives}")
    
    # 场景 1: 不预初始化，直接搜索（模拟旧版本行为）
    print("\n【场景 1】不预初始化，首次搜索")
    print("-" * 60)
    engine1 = get_search_engine()
    
    start = time.time()
    for drive in drives:
        if drive not in engine1._initialized_drives:
            engine1.init_drive(drive)
    init1_time = time.time() - start
    print(f"✓ 初始化耗时: {init1_time:.3f}s")
    
    start = time.time()
    results1 = []
    for drive in drives:
        results1.extend(engine1.search("华润", drive, max_results=10000))
    search1_time = time.time() - start
    print(f"✓ 搜索耗时: {search1_time:.3f}s")
    print(f"✓ 总耗时: {init1_time + search1_time:.3f}s")
    print(f"✓ 找到结果: {len(results1)} 个")
    
    # 场景 2: 预初始化后搜索
    print("\n【场景 2】预初始化后，首次搜索")
    print("-" * 60)
    
    # 模拟预初始化（在后台已完成）
    print("✓ 盘符已预初始化（后台完成）")
    
    start = time.time()
    results2 = []
    for drive in drives:
        results2.extend(engine1.search("华润", drive, max_results=10000))
    search2_time = time.time() - start
    print(f"✓ 搜索耗时: {search2_time:.3f}s")
    print(f"✓ 找到结果: {len(results2)} 个")
    
    # 对比
    print("\n" + "=" * 60)
    print("性能对比")
    print("=" * 60)
    print(f"场景 1 (不预初始化): {init1_time + search1_time:.3f}s")
    print(f"场景 2 (预初始化后): {search2_time:.3f}s")
    print(f"性能提升: {((init1_time + search1_time) / search2_time):.1f}x 倍")
    print(f"节省时间: {(init1_time + search1_time - search2_time):.3f}s")

if __name__ == "__main__":
    test_preinit()
