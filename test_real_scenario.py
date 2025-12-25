"""模拟真实场景：应用启动后用户立即搜索"""
import sys
import os
import time
import threading

sys.path.insert(0, r'C:\Users\Administrator\Desktop\SearchTool')
os.chdir(r'C:\Users\Administrator\Desktop\SearchTool\filesearch')

def test_real_scenario():
    print("=" * 60)
    print("真实场景测试：应用启动后用户操作")
    print("=" * 60)
    
    from filesearch.core.rust_search import get_search_engine
    import string
    
    # 检测所有盘符
    drives = []
    for drive in string.ascii_uppercase:
        if os.path.exists(f"{drive}:\\"):
            drives.append(drive)
    
    print(f"\n检测到盘符: {drives}")
    
    # 模拟应用启动
    print("\n[T+0.0s] 应用启动...")
    app_start = time.time()
    
    engine = get_search_engine()
    
    # 后台预初始化线程
    init_complete = threading.Event()
    
    def background_init():
        print(f"[T+{time.time()-app_start:.1f}s] 后台开始预加载索引...")
        for drive in drives:
            engine.init_drive(drive)
        print(f"[T+{time.time()-app_start:.1f}s] 后台预加载完成")
        init_complete.set()
    
    # 启动后台线程
    threading.Thread(target=background_init, daemon=True).start()
    
    # 场景1: 用户等待 0.5 秒后搜索（预加载可能未完成）
    print(f"\n[T+0.5s] 场景1: 用户输入'华润'并搜索（预加载可能未完成）")
    time.sleep(0.5)
    
    search_start = time.time()
    results1 = []
    for drive in drives:
        results1.extend(engine.search("华润", drive, max_results=10000))
    search_time1 = time.time() - search_start
    
    print(f"  ✓ 搜索响应时间: {search_time1:.3f}s")
    print(f"  ✓ 找到结果: {len(results1)} 个")
    print(f"  ✓ 从应用启动到结果显示: {time.time()-app_start:.3f}s")
    
    # 等待后台初始化完成
    init_complete.wait()
    
    # 场景2: 用户再次搜索（索引已完全加载）
    print(f"\n[T+{time.time()-app_start:.1f}s] 场景2: 用户再次搜索'测试'（索引已加载）")
    
    search_start = time.time()
    results2 = []
    for drive in drives:
        results2.extend(engine.search("测试", drive, max_results=10000))
    search_time2 = time.time() - search_start
    
    print(f"  ✓ 搜索响应时间: {search_time2:.3f}s")
    print(f"  ✓ 找到结果: {len(results2)} 个")
    
    # 总结
    print("\n" + "=" * 60)
    print("性能分析")
    print("=" * 60)
    print(f"首次搜索（预加载期间）: {search_time1:.3f}s")
    print(f"后续搜索（索引已加载）: {search_time2:.3f}s")
    print(f"\n对比 SQLite:")
    print(f"  SQLite 搜索: ~0.01s（索引在数据库中）")
    print(f"  Rust 首次: {search_time1:.3f}s（{search_time1/0.01:.0f}x 慢）")
    print(f"  Rust 后续: {search_time2:.3f}s（{search_time2/0.01:.1f}x {'快' if search_time2 < 0.01 else '慢'}）")
    
    if search_time2 < 0.01:
        print(f"\n✅ 成功！Rust 后续搜索比 SQLite 快 {0.01/search_time2:.1f}x 倍！")
    else:
        print(f"\n⚠️ Rust 搜索仍比 SQLite 慢，需要继续优化")

if __name__ == "__main__":
    test_real_scenario()
