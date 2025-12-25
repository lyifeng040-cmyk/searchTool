# 🚀 Rust 搜索引擎快速开始

## 一分钟上手

### 1. 运行基础测试
```powershell
cd c:\Users\Administrator\Desktop\SearchTool\filesearch\tests
python simple_rust_test.py
```

**预期输出**:
```
✅ 初始化成功 (2.13s)
找到 3 个结果 (0.06ms)
  1. 📁 python
  2. 📄 python-3.13.9-amd64.exe
  ...
```

### 2. 运行性能测试
```powershell
python benchmark_rust_vs_python.py
```

**查看性能**:
- 前缀搜索: ~0.03ms
- 模糊搜索: ~0.4ms
- QPS: 240,000+

### 3. 交互式演示
```powershell
cd c:\Users\Administrator\Desktop\SearchTool\filesearch
python demo_rust_search.py
```

## API 快速参考

### 初始化
```python
from core.rust_search import get_search_engine

engine = get_search_engine()
engine.init_drive('C')  # 初始化 C 盘索引，约 2 秒
```

### 搜索
```python
# 前缀搜索（最快，~0.03ms）
results = engine.search_prefix("python")

# 模糊搜索（稍慢，~0.4ms）
results = engine.search("test")

# 扩展名搜索
results = engine.search_by_extension("txt")
```

### 结果格式
```python
[
    {
        'name': 'test.py',
        'path': 'C:\\Users\\...\\test.py',
        'size': 1024,
        'is_dir': False
    },
    ...
]
```

### 增量更新
```python
# 添加
engine.add_file('C', 'new.txt', 'C:\\new.txt', 
                file_ref=999, parent_ref=5, 
                size=100, is_dir=False)

# 删除
engine.remove_file('C', file_ref=999)

# 保存索引
engine.save_index('C')
```

## 性能对比

| 操作 | 耗时 | 说明 |
|------|------|------|
| 索引初始化 | ~2s | 首次构建（百万文件） |
| 前缀搜索 | 0.03ms | Trie 索引查找 |
| 模糊搜索 | 0.4ms | 并行全表扫描 |
| 扩展名搜索 | 0.01ms | 倒排索引 |

## 常见问题

**Q: 找不到 DLL?**
```powershell
# 检查 DLL 是否存在
Test-Path "C:\Users\Administrator\Desktop\rust_engine\scanner\target\release\file_scanner_engine.dll"

# 重新编译
cd C:\Users\Administrator\Desktop\rust_engine\scanner
cargo build --release
```

**Q: 初始化失败?**
- 确保有管理员权限
- 检查磁盘驱动器存在

**Q: 搜索结果为空?**
- 确保先调用 `init_drive()`
- 检查查询关键词是否正确

## 下一步

1. 集成到主程序 `filesearch/main.py`
2. 实现 UI 绑定
3. 设置后台索引更新
4. 多盘并行索引

---

🎉 享受极速搜索体验！
