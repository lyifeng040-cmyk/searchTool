import sys
sys.path.insert(0, '.')

from filesearch.core.rust_search import get_search_engine

engine = get_search_engine()
if engine.init_drive('C'):
    print('✅ Rust engine initialized')
    
    # 获取所有文件（空搜索）
    all_results = engine.search('', 'C', max_results=100000)
    print(f'Total files in index: {len(all_results)}')
    
    # 测试搜索常见字符
    for query in ['a', 'test', 'python', '华']:
        results = engine.search(query, 'C', max_results=1000)
        print(f'Search "{query}": {len(results)} results')
        if results and len(results) <= 5:
            for r in results:
                print(f'  - {r["name"]}')
else:
    print('❌ Failed to initialize')
