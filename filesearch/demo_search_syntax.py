"""
演示 Everything 风格搜索语法的示例脚本

运行此脚本可以测试新增的布尔运算符、通配符和过滤器功能。
"""

import sys
import os

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def demonstrate_search_syntax():
    """演示各种搜索语法"""
    
    print("=" * 70)
    print("Everything 风格搜索语法演示")
    print("=" * 70)
    
    examples = [
        # 基本搜索
        ("基本关键词搜索", "test"),
        
        # OR 运算符
        ("OR 运算符 - 图片文件", "jpg|png|gif"),
        ("OR 运算符 - 文档类型", "doc|docx|pdf"),
        
        # NOT 运算符
        ("NOT 运算符 - 排除备份", "project !backup"),
        ("NOT 运算符 - 排除临时文件", "*.txt !temp"),
        
        # 通配符
        ("通配符 * - 匹配任意字符", "test*"),
        ("通配符 ? - 匹配单个字符", "file?.txt"),
        ("通配符组合", "report_20*.pdf"),
        
        # 扩展名过滤器
        ("扩展名过滤器 - 单个", "ext:jpg"),
        ("扩展名过滤器 - 多个（OR）", "ext:jpg|png|gif"),
        
        # 大小过滤器
        ("大小过滤器 - 大于", "size:>1mb"),
        ("大小过滤器 - 小于", "size:<500kb"),
        ("大小过滤器 - 范围", "size:1mb..10mb"),
        
        # 修改时间过滤器
        ("修改时间 - 今天", "dm:today"),
        ("修改时间 - 最近7天", "dm:7d"),
        ("修改时间 - 最近24小时", "dm:24h"),
        ("修改时间 - 特定日期", "dm:2024-12-22"),
        ("修改时间 - 日期范围", "dm:2024-12-01..2024-12-22"),
        
        # 路径长度过滤器
        ("路径长度 - 大于", "len:>100"),
        ("路径长度 - 小于", "len:<50"),
        
        # 文件类型过滤器
        ("文件类型 - 仅文件", "file:"),
        ("文件类型 - 仅文件夹", "folder:"),
        ("文件类型 - 文件名包含", "file:report"),
        
        # 路径过滤器
        ("路径包含", "path:Documents"),
        
        # 组合搜索
        ("组合1 - 大图片文件", "ext:jpg|png size:>5mb"),
        ("组合2 - 最近项目文档", "project dm:7d ext:docx !backup"),
        ("组合3 - 特定命名模式", "report_20*.pdf path:Documents"),
        ("组合4 - 排除临时文件", "*.txt !temp !tmp"),
        ("组合5 - 精确日期范围", "size:>1mb dm:2024-12-01..2024-12-22 ext:pdf|docx"),
    ]
    
    print("\n以下是支持的搜索语法示例：\n")
    
    for i, (description, query) in enumerate(examples, 1):
        print(f"{i:2d}. {description:30s} → {query}")
    
    print("\n" + "=" * 70)
    print("提示：")
    print("  - 在应用程序的搜索框中直接使用这些语法")
    print("  - 可以自由组合多个过滤器和运算符")
    print("  - 简单模式下搜索文件名和路径，高级模式下仅搜索文件名")
    print("  - 查看 SEARCH_SYNTAX.md 获取完整文档")
    print("=" * 70)
    
    print("\n快速测试建议：")
    print("  1. 搜索 'jpg' - 应该返回所有 jpg 文件")
    print("  2. 搜索 'jpg|png' - 应该返回 jpg 和 png 文件")
    print("  3. 搜索 'test !backup' - 应该返回包含 test 但不包含 backup 的文件")
    print("  4. 搜索 'ext:jpg size:>1mb' - 应该返回大于 1MB 的 jpg 文件")
    print("  5. 搜索 'test*' - 应该返回以 test 开头的文件")
    print()


if __name__ == "__main__":
    demonstrate_search_syntax()
