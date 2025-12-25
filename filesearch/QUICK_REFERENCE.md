# Everything 风格搜索语法快速参考

## 布尔运算符
| 运算符 | 语法 | 示例 | 说明 |
|--------|------|------|------|
| OR | `\|` | `jpg\|png\|gif` | 匹配任意一个 |
| NOT | `!` | `test !backup` | 排除匹配 |
| AND | 空格 | `project report` | 全部匹配 |

## 通配符
| 符号 | 匹配 | 示例 | 匹配结果 |
|------|------|------|----------|
| `*` | 任意字符 | `test*` | test.txt, testing.log |
| `?` | 单个字符 | `file?.txt` | file1.txt, fileA.txt |

## 核心过滤器
| 过滤器 | 语法 | 示例 |
|--------|------|------|
| 扩展名 | `ext:` | `ext:jpg\|png` |
| 大小 | `size:` | `size:>1mb` `size:1mb..10mb` |
| 修改时间 | `dm:` | `dm:today` `dm:7d` `dm:2024-12-22` |
| 路径长度 | `len:` | `len:>100` |
| 文件类型 | `file:` `folder:` | `file:` `folder:backup` |
| 路径包含 | `path:` | `path:Documents` |

## 常用组合
```
ext:jpg|png size:>5mb              # 大型图片
project dm:7d !backup              # 最近项目（非备份）
*.txt !temp !cache                 # 文本文件（排除临时）
report_20*.pdf path:Documents      # 特定命名模式
```

## 大小单位
- `kb` = 千字节
- `mb` = 兆字节
- `gb` = 吉字节

## 时间单位
- `today` = 今天
- `7d` = 7 天
- `24h` = 24 小时
- `2024-12-22` = 特定日期
- `2024-12-01..2024-12-22` = 日期范围
