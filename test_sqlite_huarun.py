import sys
sys.path.insert(0, '.')
from filesearch.core.index_manager import IndexManager
from filesearch.config import ConfigManager
import time

config = ConfigManager()
mgr = IndexManager(config_mgr=config)
time.sleep(1)

cursor = mgr.conn.cursor()

# 检查有多少 C 盘文件
cursor.execute("SELECT COUNT(*) FROM files WHERE full_path LIKE 'C:%'")
c_count = cursor.fetchone()[0]
print(f'C盘文件数: {c_count}')

# 检查包含'华润'的文件
cursor.execute("SELECT COUNT(*) FROM files WHERE filename LIKE '%华%' AND filename LIKE '%润%'")
huarun_count = cursor.fetchone()[0]
print(f'包含华润的文件数: {huarun_count}')

# 显示这些文件
if huarun_count > 0:
    cursor.execute("SELECT filename, full_path FROM files WHERE filename LIKE '%华%' AND filename LIKE '%润%' LIMIT 5")
    print('\n前 5 个华润文件:')
    for fn, fp in cursor:
        print(f'  {fn}')
        print(f'    {fp}')
