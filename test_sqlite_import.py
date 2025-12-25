"""测试从 SQLite 导入到 Rust"""
import sys
sys.path.insert(0, '.')

from filesearch.core.rust_search import get_search_engine
import time

print("初始化 Rust 引擎...")
engine = get_search_engine()

if engine.init_drive('C'):
    print("等待导入完成...")
    time.sleep(3)
    
    # 检查导入后的文件数
    results = engine.search('', 'C', max_results=100000)
    print(f"\n导入后 Rust 索引有 {len(results)} 个文件")
    
    # 测试搜索"华"
    results = engine.search('华', 'C', max_results=1000)
    print(f"搜索'华': {len(results)} 个结果")
    
    # 过滤"华润"
    filtered = [r for r in results if '华' in r['name'] and '润' in r['name']]
    print(f"包含'华润'的结果: {len(filtered)}")
    
    if filtered:
        print("\n前 5 个结果:")
        for item in filtered[:5]:
            print(f"  - {item['name']}")
else:
    print("初始化失败")
