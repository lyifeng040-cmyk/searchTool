# Everything 风格搜索语法说明

本文件搜索工具现在支持 Everything 风格的高级搜索语法，包括布尔运算符、通配符和丰富的过滤器。

## 基本搜索

### 简单模式 vs 高级模式

- **简单模式**（默认）：关键词同时匹配文件名和完整路径
  - 例如：搜索 `Documents` 会匹配 `C:\Documents\file.txt` 和 `C:\Users\test\Documents\report.pdf`

- **高级模式**：仅匹配文件名
  - 在设置中切换

## 布尔运算符

### OR 运算符 (`|`)

使用 `|` 符号搜索多个备选关键词（逻辑 OR）：

```
jpg|png|gif          # 匹配任何 jpg、png 或 gif 文件
test|demo|sample     # 匹配包含 test、demo 或 sample 的文件
```

### NOT 运算符 (`!`)

使用 `!` 前缀排除包含特定关键词的结果：

```
test !backup         # 包含 test 但不包含 backup
photo !thumbnail     # 包含 photo 但不包含 thumbnail
*.jpg !cache         # 所有 jpg 文件，但排除路径中包含 cache 的
```

### AND 运算符（空格）

空格分隔的关键词会被视为 AND 条件（必须全部匹配）：

```
project report 2024  # 必须同时包含这三个词
```

## 通配符

### 星号 (`*`)

匹配任意数量的字符（包括零个）：

```
test*                # test, test.txt, testing.log, test_file.docx
*.jpg                # 所有 jpg 文件
report*.pdf          # report.pdf, report_2024.pdf, report_final.pdf
```

### 问号 (`?`)

匹配单个字符：

```
test?.txt            # test1.txt, test2.txt, testA.txt
file_?.log           # file_1.log, file_a.log
20??-12-22.txt       # 2024-12-22.txt, 2025-12-22.txt
```

### 组合使用

```
test*.?xt            # test.txt, testing.doc, test_data.pptx
```

## 过滤器

### 扩展名过滤器 (`ext:`)

按文件扩展名筛选：

```
ext:jpg              # 仅 jpg 文件
ext:pdf              # 仅 pdf 文件
ext:doc|docx         # doc 或 docx 文件（支持 OR）
```

### 大小过滤器 (`size:`)

按文件大小筛选：

```
size:>1mb            # 大于 1MB 的文件
size:<500kb          # 小于 500KB 的文件
size:1mb..10mb       # 1MB 到 10MB 之间的文件（范围语法）
```

支持的单位：`kb`（千字节）、`mb`（兆字节）、`gb`（吉字节）

### 修改时间过滤器 (`dm:` 或 `datemodified:`)

按文件修改时间筛选：

```
dm:today             # 今天修改的文件
dm:7d                # 最近 7 天修改的文件
dm:24h               # 最近 24 小时修改的文件
dm:2024-12-22        # 特定日期修改的文件
dm:2024-12-01..2024-12-22   # 日期范围
```

### 路径长度过滤器 (`len:`)

按完整路径长度筛选：

```
len:>100             # 路径长度超过 100 个字符
len:<50              # 路径长度小于 50 个字符
```

### 文件类型过滤器

```
file:                # 仅文件（不包括文件夹）
folder:              # 仅文件夹
file:report          # 文件名包含 report 的文件
folder:backup        # 文件夹名包含 backup 的文件夹
```

### 路径包含过滤器 (`path:`)

筛选路径中包含特定文本的文件：

```
path:Documents       # 路径中包含 Documents
path:C:\Users\Admin  # 指定路径下的文件
```

### 文件属性过滤器 (`attrib:`)

按文件属性筛选（Windows）：

```
attrib:h             # 隐藏文件
attrib:r             # 只读文件
attrib:hr            # 既隐藏又只读的文件
```

## 组合搜索示例

### 示例 1：查找大型图片文件

```
ext:jpg|png size:>5mb
```

匹配所有大于 5MB 的 jpg 或 png 文件。

### 示例 2：查找最近的项目文档

```
project dm:7d ext:docx !backup
```

匹配最近 7 天修改的、包含 "project"、扩展名为 docx、但不包含 "backup" 的文件。

### 示例 3：查找特定命名模式的文件

```
report_20*.pdf path:Documents
```

匹配 Documents 路径下、文件名以 "report_20" 开头的所有 PDF 文件。

### 示例 4：排除临时文件

```
*.txt !temp !tmp
```

匹配所有 txt 文件，但排除路径或文件名中包含 "temp" 或 "tmp" 的。

### 示例 5：精确日期范围

```
size:>1mb dm:2024-12-01..2024-12-22 ext:pdf|docx
```

匹配在 2024 年 12 月 1 日到 22 日之间修改的、大于 1MB 的 PDF 或 DOCX 文件。

## 语法优先级

1. **过滤器**（`ext:`, `size:`, `dm:` 等）首先应用
2. **NOT 运算符**（`!`）排除不需要的结果
3. **OR 运算符**（`|`）创建备选匹配
4. **AND 运算符**（空格）确保所有关键词都存在

## 注意事项

- 关键词和过滤器不区分大小写
- 通配符 `*` 和 `?` 在关键词中自动转换为 SQL LIKE 模式
- 过滤器可以任意组合使用
- 简单模式下，搜索会同时查找文件名和路径；高级模式下仅查找文件名
