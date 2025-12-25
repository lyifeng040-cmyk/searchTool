import sys
sys.path.insert(0, '.')
from filesearch.core.rust_search import get_search_engine
import time

engine = get_search_engine()
engine.init_drive('C')
time.sleep(2)

# 获取所有文件
all_files = engine.search('', 'C', 100000)
print(f'Total files: {len(all_files)}')

# 检查有多少包含中文的文件
chinese_files = [f for f in all_files if any('\u4e00' <= c <= '\u9fff' for c in f['name'])]
print(f'Files with Chinese chars: {len(chinese_files)}')

# 显示前几个
if chinese_files:
    print('Sample Chinese files:')
    for f in chinese_files[:5]:
        print(f"  {f['name']}")

# 手动测试搜索中文
print("\nManual search for files containing '华':")
matching = [f for f in all_files if '华' in f['name']]
print(f"Found {len(matching)} files containing '华'")
if matching:
    for f in matching[:5]:
        print(f"  {f['name']}")
