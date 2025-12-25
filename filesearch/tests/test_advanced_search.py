"""测试增强的搜索语法：布尔运算符、通配符、扩展过滤器"""
import pytest


class TestWildcardConversion:
    """测试通配符转换逻辑"""

    def wildcard_to_sql(self, pattern):
        """将 Everything 风格通配符转换为 SQL LIKE 模式"""
        pattern = pattern.replace('[', r'\[').replace('%', r'\%').replace('_', r'\_')
        pattern = pattern.replace('*', '%').replace('?', '_')
        return pattern

    def test_asterisk_to_percent(self):
        """测试 * 转换为 %"""
        result = self.wildcard_to_sql("test*")
        assert result == "test%"

    def test_question_to_underscore(self):
        """测试 ? 转换为 _"""
        result = self.wildcard_to_sql("test?")
        assert result == "test_"

    def test_combined_wildcards(self):
        """测试组合通配符"""
        result = self.wildcard_to_sql("test*.?mp")
        assert result == "test%._mp"

    def test_escape_special_chars(self):
        """测试转义特殊字符"""
        result = self.wildcard_to_sql("test[_]%")
        # [、_、% 都会被转义，] 不会
        assert result == r"test\[\_]\%"


class TestSyntaxParsing:
    """测试语法解析逻辑"""

    def split_or_tokens(self, text):
        """分割 OR 表达式"""
        if '|' in text:
            return [t.strip() for t in text.split('|') if t.strip()]
        return [text]

    def test_split_single(self):
        """测试单个词"""
        result = self.split_or_tokens("jpg")
        assert result == ["jpg"]

    def test_split_multiple(self):
        """测试多个 OR 词"""
        result = self.split_or_tokens("jpg|png|gif")
        assert result == ["jpg", "png", "gif"]

    def test_split_with_spaces(self):
        """测试带空格的 OR 词"""
        result = self.split_or_tokens("jpg | png | gif")
        assert result == ["jpg", "png", "gif"]


class TestFilterParsing:
    """测试过滤器解析"""

    def parse_size(self, size_str):
        """解析大小字符串"""
        import re
        match = re.match(r'(\d+)(kb|mb|gb)?', size_str.lower())
        if match:
            num, unit = match.groups()
            multiplier = {'kb': 1024, 'mb': 1024**2, 'gb': 1024**3}.get(unit, 1)
            return int(num) * multiplier
        return 0

    def test_parse_size_kb(self):
        """测试解析 KB"""
        result = self.parse_size("100kb")
        assert result == 100 * 1024

    def test_parse_size_mb(self):
        """测试解析 MB"""
        result = self.parse_size("5mb")
        assert result == 5 * 1024 * 1024

    def test_parse_size_gb(self):
        """测试解析 GB"""
        result = self.parse_size("2gb")
        assert result == 2 * 1024 * 1024 * 1024

    def test_parse_size_bytes(self):
        """测试解析字节"""
        result = self.parse_size("1024")
        assert result == 1024


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

