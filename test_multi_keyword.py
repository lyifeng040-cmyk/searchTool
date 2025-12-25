import sys
sys.path.insert(0, '.')

print('Testing Rust search with multi-keywords...')

# 模拟搜索 '华润'
from filesearch.core.rust_search import get_search_engine

engine = get_search_engine()
if engine.init_drive('C'):
    print('✅ Rust engine initialized')
    
    # 测试搜索 '华'
    results = engine.search('华', 'C', max_results=1000)
    print(f'Found {len(results)} results for 华')
    
    # 过滤包含 '润' 的结果
    filtered = [r for r in results if '润' in r['name'].lower()]
    print(f'After filtering for 润: {len(filtered)} results')
    
    if filtered:
        print('Sample results:')
        for item in filtered[:5]:
            print(f'  - {item["name"]}')
else:
    print('❌ Failed to initialize')
