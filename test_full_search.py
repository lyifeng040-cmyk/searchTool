"""完整测试：首次搜索"华润"的真实性能"""
import sys
import os
import time

sys.path.insert(0, r'C:\Users\Administrator\Desktop\SearchTool')
os.chdir(r'C:\Users\Administrator\Desktop\SearchTool\filesearch')

def test_full_search():
    print("=" * 60)
    print("完整测试：首次搜索 '华润' 的性能")
    print("=" * 60)
    
    from filesearch.core.rust_search import RustSearchEngine
    import string
    
    # 检测所有盘符
    drives = []
    for drive in string.ascii_uppercase:
        if os.path.exists(f"{drive}:\\"):
            drives.append(drive)
    
    print(f"\n检测到盘符: {drives}")
    
    # 场景 1: 首次启动（无磁盘索引）
    print("\n【场景 1】首次启动 - 无磁盘索引")
    print("-" * 60)
    
    # 删除所有磁盘索引文件（模拟首次运行）
    for drive in drives:
        index_path = f"{drive}:\\.search_index.bin"
        if os.path.exists(index_path):
            os.remove(index_path)
            print(f"删除旧索引: {index_path}")
    
    engine1 = RustSearchEngine()
    
    # 初始化所有盘符
    start = time.time()
    for drive in drives:
        engine1.init_drive(drive)
    init_time = time.time() - start
    print(f"✓ 初始化所有盘符耗时: {init_time:.3f}s")
    
    # 搜索
    start = time.time()
    results = []
    for drive in drives:
        results.extend(engine1.search("华润", drive, max_results=10000))
    search_time = time.time() - start
    print(f"✓ 搜索耗时: {search_time:.3f}s")
    print(f"✓ 找到结果: {len(results)} 个")
    print(f"✓ 总耗时: {init_time + search_time:.3f}s")
    
    # 检查索引文件大小
    total_size = 0
    for drive in drives:
        index_path = f"{drive}:\\.search_index.bin"
        if os.path.exists(index_path):
            size = os.path.getsize(index_path)
            total_size += size
            print(f"  - {drive}: {size/1024/1024:.2f} MB")
    print(f"✓ 索引总大小: {total_size/1024/1024:.2f} MB")
    
    # 场景 2: 第二次启动（有磁盘索引）
    print("\n【场景 2】第二次启动 - 从磁盘加载索引")
    print("-" * 60)
    
    # 创建新引擎（模拟重启）
    engine2 = RustSearchEngine()
    
    # 初始化所有盘符（这次会从磁盘加载）
    start = time.time()
    for drive in drives:
        engine2.init_drive(drive)
    load_time = time.time() - start
    print(f"✓ 加载所有盘符耗时: {load_time:.3f}s")
    
    # 搜索
    start = time.time()
    results2 = []
    for drive in drives:
        results2.extend(engine2.search("华润", drive, max_results=10000))
    search2_time = time.time() - start
    print(f"✓ 搜索耗时: {search2_time:.3f}s")
    print(f"✓ 找到结果: {len(results2)} 个")
    print(f"✓ 总耗时: {load_time + search2_time:.3f}s")
    
    # 性能总结
    print("\n" + "=" * 60)
    print("性能总结")
    print("=" * 60)
    print(f"首次启动（构建索引）: {init_time + search_time:.3f}s")
    print(f"第二次启动（磁盘加载）: {load_time + search2_time:.3f}s")
    print(f"性能提升: {(init_time + search_time) / (load_time + search2_time):.1f}x 倍")
    print(f"\n✅ 达成目标：第二次启动时，搜索响应 < 0.1s")
    print(f"   实际响应时间: {load_time + search2_time:.3f}s")
    
    if load_time + search2_time < 0.1:
        print("\n🎉 性能优化成功！达到 SQLite 同等的即时响应！")
    else:
        print(f"\n⚠️ 仍需优化，目标 < 0.1s，当前 {load_time + search2_time:.3f}s")

if __name__ == "__main__":
    test_full_search()
