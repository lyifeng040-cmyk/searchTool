"""测试磁盘索引的保存和加载性能"""
import sys
import os
import time

sys.path.insert(0, r'C:\Users\Administrator\Desktop\SearchTool')
os.chdir(r'C:\Users\Administrator\Desktop\SearchTool\filesearch')

def test_disk_index():
    print("=" * 60)
    print("测试磁盘索引性能")
    print("=" * 60)
    
    from filesearch.core.rust_search import RustSearchEngine
    
    # 场景 1: 第一次运行（需要构建索引）
    print("\n【场景 1】第一次运行 - 构建索引")
    print("-" * 60)
    
    engine1 = RustSearchEngine()
    
    start = time.time()
    engine1.init_drive('D')  # 选择 D 盘测试
    init_time = time.time() - start
    print(f"✓ 初始化耗时: {init_time:.3f}s")
    
    start = time.time()
    results = engine1.search("华润", 'D', max_results=10000)
    search_time = time.time() - start
    print(f"✓ 搜索耗时: {search_time:.3f}s")
    print(f"✓ 找到结果: {len(results)} 个")
    
    # 检查索引文件是否存在
    index_path = r"D:\.search_index.bin"
    if os.path.exists(index_path):
        size_mb = os.path.getsize(index_path) / 1024 / 1024
        print(f"✓ 索引文件已保存: {index_path} ({size_mb:.2f} MB)")
    else:
        print("✗ 索引文件未找到")
    
    # 场景 2: 第二次运行（从磁盘加载）
    print("\n【场景 2】第二次运行 - 从磁盘加载")
    print("-" * 60)
    
    # 创建新的引擎实例（模拟重启应用）
    engine2 = RustSearchEngine()
    
    start = time.time()
    engine2.init_drive('D')
    load_time = time.time() - start
    print(f"✓ 加载耗时: {load_time:.3f}s")
    
    start = time.time()
    results2 = engine2.search("华润", 'D', max_results=10000)
    search2_time = time.time() - start
    print(f"✓ 搜索耗时: {search2_time:.3f}s")
    print(f"✓ 找到结果: {len(results2)} 个")
    
    # 性能对比
    print("\n" + "=" * 60)
    print("性能对比")
    print("=" * 60)
    print(f"首次运行（构建索引）: {init_time:.3f}s")
    print(f"第二次运行（磁盘加载）: {load_time:.3f}s")
    print(f"性能提升: {init_time / load_time:.1f}x 倍")
    print(f"节省时间: {init_time - load_time:.3f}s")
    print(f"\n目标: 加载时间应 < 0.1s（接近 SQLite 的即时响应）")

if __name__ == "__main__":
    test_disk_index()
