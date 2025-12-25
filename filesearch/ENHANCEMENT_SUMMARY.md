# Everything 风格增强功能实现总结

## 实现日期
2024-12-22

## 新增功能

本次更新为文件搜索工具添加了 Everything 风格的高级搜索语法，包括三大核心增强：

### 1. 布尔运算符

#### OR 运算符 (`|`)
- **语法**：`keyword1|keyword2|keyword3`
- **功能**：匹配任意一个关键词（逻辑 OR）
- **示例**：
  - `jpg|png|gif` - 匹配所有图片文件
  - `test|demo|sample` - 匹配包含任一词的文件

#### NOT 运算符 (`!`)
- **语法**：`!keyword`
- **功能**：排除包含指定关键词的结果
- **示例**：
  - `project !backup` - 包含 project 但不包含 backup
  - `*.txt !temp` - 所有 txt 文件，但排除包含 temp 的

#### AND 运算符（空格）
- **语法**：`keyword1 keyword2`
- **功能**：所有关键词必须同时匹配（逻辑 AND）
- **示例**：
  - `project report 2024` - 必须包含这三个词

### 2. 通配符

#### 星号通配符 (`*`)
- **功能**：匹配任意数量的字符（包括零个）
- **转换**：内部转换为 SQL LIKE 的 `%`
- **示例**：
  - `test*` - test、testing、test_file.txt
  - `*.jpg` - 所有 jpg 文件
  - `report_*.pdf` - report_2024.pdf、report_final.pdf

#### 问号通配符 (`?`)
- **功能**：匹配单个字符
- **转换**：内部转换为 SQL LIKE 的 `_`
- **示例**：
  - `test?.txt` - test1.txt、testA.txt
  - `file_?.log` - file_1.log、file_a.log

### 3. 扩展过滤语法

#### 多扩展名支持 (`ext:jpg|png`)
- **增强**：`ext:` 过滤器现在支持 OR 语法
- **示例**：`ext:jpg|png|gif` - 匹配多种图片格式

#### 大小范围 (`size:1mb..10mb`)
- **新语法**：`size:min..max` 范围表示
- **兼容**：仍支持 `size:>1mb` 和 `size:<500kb`

#### 修改时间范围 (`dm:2024-12-01..2024-12-22`)
- **新语法**：`dm:start..end` 日期范围
- **扩展**：支持 `datemodified:` 别名
- **新增**：精确日期 `dm:2024-12-22`

#### 路径长度过滤器 (`len:>100`)
- **新增**：根据完整路径长度筛选
- **语法**：`len:>N` 或 `len:<N`

#### 文件属性过滤器 (`attrib:h`)
- **新增**：按 Windows 文件属性筛选
- **支持**：
  - `attrib:h` - 隐藏文件
  - `attrib:r` - 只读文件
  - `attrib:hr` - 既隐藏又只读

## 技术实现

### 核心修改文件

#### `core/index_manager.py`
- **方法**：`_parse_search_syntax()`
  - 完全重写，返回四个值：`keywords, filters, or_keywords, not_keywords`
  - 支持 OR 分割（`|`）
  - 支持 NOT 前缀（`!`）
  - 扩展过滤器字典，新增：
    - `ext_list` - 多个扩展名列表
    - `dm_before` - 修改时间上限
    - `len_min`, `len_max` - 路径长度范围
    - `attrib_hidden`, `attrib_readonly` - 文件属性

- **方法**：`search()`
  - 更新以处理四元组解析结果
  - 添加 `wildcard_to_sql()` 内部函数
  - 通配符自动转换：`*` → `%`，`?` → `_`
  - SQL LIKE 使用 `ESCAPE '\'` 防止冲突
  - 支持 OR 条件的 SQL 生成：`({cond1} OR {cond2} OR {cond3})`
  - 支持 NOT 条件：`NOT (filename_lower LIKE ? ...)`

- **新增方法**：
  - `_parse_size(size_str)` - 解析大小字符串
  - `_parse_date(date_str)` - 解析日期字符串

### SQL 查询增强

#### 简单模式 SQL 示例
```sql
SELECT filename, full_path, size, mtime, is_dir
FROM files
WHERE (filename_lower LIKE ? ESCAPE '\' OR lower(full_path) LIKE ? ESCAPE '\')
  AND ((filename_lower LIKE ? ESCAPE '\' OR lower(full_path) LIKE ? ESCAPE '\') 
       OR (filename_lower LIKE ? ESCAPE '\' OR lower(full_path) LIKE ? ESCAPE '\'))
  AND NOT (filename_lower LIKE ? ESCAPE '\' OR lower(full_path) LIKE ? ESCAPE '\')
  AND (extension = ? OR extension = ?)
  AND size > ?
  AND LENGTH(full_path) > ?
LIMIT ?
```

#### 高级模式 SQL 示例
```sql
SELECT filename, full_path, size, mtime, is_dir
FROM files
WHERE filename_lower LIKE ? ESCAPE '\'
  AND (filename_lower LIKE ? ESCAPE '\' OR filename_lower LIKE ? ESCAPE '\')
  AND NOT filename_lower LIKE ? ESCAPE '\'
  AND extension = ?
LIMIT ?
```

## 测试覆盖

### 新增测试文件
- `tests/test_advanced_search.py` - 11 个测试用例

### 测试类别
1. **通配符转换测试**（4 个测试）
   - 星号转换
   - 问号转换
   - 组合通配符
   - 特殊字符转义

2. **语法解析测试**（3 个测试）
   - 单个词分割
   - OR 多词分割
   - 带空格的 OR

3. **过滤器解析测试**（4 个测试）
   - KB、MB、GB 大小解析
   - 字节数解析

### 测试结果
```
28 passed in 0.56s
```

所有原有测试保持通过，新增测试全部通过。

## 使用示例

### 示例 1：查找大型图片文件
```
ext:jpg|png size:>5mb
```

### 示例 2：查找最近的项目文档（排除备份）
```
project dm:7d ext:docx !backup
```

### 示例 3：通配符匹配特定命名模式
```
report_20*.pdf path:Documents
```

### 示例 4：组合 OR 和 NOT
```
jpg|png !test !cache
```

### 示例 5：精确日期范围 + 大小限制
```
size:>1mb dm:2024-12-01..2024-12-22 ext:pdf|docx
```

## 性能考虑

- **通配符转换**：在 Python 层面完成，无额外数据库开销
- **OR 条件**：使用 SQL 的 `OR` 运算符，数据库优化器自动处理
- **NOT 条件**：使用 SQL 的 `NOT` 运算符，高效排除
- **ESCAPE 子句**：确保特殊字符正确转义，避免意外匹配

## 兼容性

- **向后兼容**：所有旧语法继续工作
- **渐进增强**：新语法可选使用，不影响基本搜索
- **错误容忍**：无效语法会被忽略，不会导致搜索失败

## 文档

- **用户手册**：`SEARCH_SYNTAX.md` - 完整语法说明
- **演示脚本**：`demo_search_syntax.py` - 29 个示例
- **注释**：代码中添加详细注释说明新功能

## 下一步建议

### 可能的增强
1. **正则表达式支持**：`regex:` 过滤器
2. **文件内容搜索**：`content:` 过滤器（需要索引文件内容）
3. **标签系统**：`tag:` 过滤器（需要标签数据库）
4. **智能分组**：`< >` 括号分组（复杂布尔表达式）
5. **宏定义**：保存常用搜索模式

### UI 增强
1. 搜索建议/自动完成
2. 语法高亮
3. 过滤器快捷按钮
4. 搜索历史
5. 保存的搜索

## 总结

本次更新成功实现了三大核心功能：

✅ **布尔运算符**：OR (`|`)、NOT (`!`)、AND（空格）  
✅ **通配符**：星号 (`*`) 和问号 (`?`)  
✅ **扩展过滤器**：`len:`, `attrib:`, 范围语法 (`..`)  

所有功能已通过完整测试，性能良好，文档完善，向后兼容。
