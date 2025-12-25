"""
计算器功能 - 支持数学表达式计算
"""
import math
import re


class Calculator:
	"""数学表达式计算器"""
	
	# 支持的数学函数
	MATH_FUNCTIONS = {
		'sin': math.sin,
		'cos': math.cos,
		'tan': math.tan,
		'asin': math.asin,
		'acos': math.acos,
		'atan': math.atan,
		'sqrt': math.sqrt,
		'abs': abs,
		'ceil': math.ceil,
		'floor': math.floor,
		'round': round,
		'log': math.log,
		'log10': math.log10,
		'exp': math.exp,
		'pow': pow,
	}
	
	# 数学常量
	CONSTANTS = {
		'pi': math.pi,
		'e': math.e,
	}
	
	@classmethod
	def is_expression(cls, text):
		"""
		检测文本是否为数学表达式
		
		支持的格式:
		  - 基本运算: 2+2, 10*5, 100/4
		  - 括号: (2+3)*4
		  - 函数: sqrt(144), sin(45)
		  - 常量: pi, e
		"""
		text = text.strip()
		if not text:
			return False
		
		# 排除文件搜索语法
		if any(prefix in text.lower() for prefix in ['ext:', 'size:', 'dm:', 'path:', 'name:', 'dir:']):
			return False
		
		# 排除网页搜索前缀
		if re.match(r'^[a-z]+:\s*.+', text):
			return False
		
		# 检测是否包含数学运算符或函数
		math_pattern = r'[\+\-\*/\(\)\d\.]|' + '|'.join(cls.MATH_FUNCTIONS.keys())
		if not re.search(math_pattern, text, re.IGNORECASE):
			return False
		
		# 至少包含一个数字或数学符号
		if not re.search(r'[\d\+\-\*/\(\)]', text):
			return False
		
		return True
	
	@classmethod
	def calculate(cls, expression):
		"""
		计算数学表达式
		
		Args:
		    expression: 数学表达式字符串
		
		Returns:
		    (success: bool, result: float/str)
		    success=True 时 result 为计算结果
		    success=False 时 result 为错误信息
		"""
		try:
			# 清理表达式
			expr = expression.strip()
			
			# 替换常量
			for name, value in cls.CONSTANTS.items():
				expr = re.sub(r'\b' + name + r'\b', str(value), expr, flags=re.IGNORECASE)
			
			# 构建安全的命名空间
			safe_dict = {
				'__builtins__': {},
			}
			safe_dict.update(cls.MATH_FUNCTIONS)
			safe_dict.update(cls.CONSTANTS)
			
			# 计算表达式
			result = eval(expr, safe_dict)
			
			# 格式化结果
			if isinstance(result, float):
				# 如果是整数，显示为整数
				if result.is_integer():
					return True, int(result)
				# 否则保留合适的小数位
				return True, round(result, 10)
			
			return True, result
			
		except ZeroDivisionError:
			return False, "错误: 除数不能为零"
		except NameError as e:
			return False, f"错误: 未知的函数或常量 ({e})"
		except SyntaxError:
			return False, "错误: 表达式语法错误"
		except Exception as e:
			return False, f"错误: {str(e)}"
	
	@classmethod
	def get_help_text(cls):
		"""生成帮助文本"""
		lines = ["计算器功能：\n"]
		lines.append("基本运算：")
		lines.append("  2+2*3      → 8")
		lines.append("  100/4      → 25")
		lines.append("  (2+3)*4    → 20")
		lines.append("\n数学函数：")
		lines.append("  sqrt(144)  → 12")
		lines.append("  sin(0)     → 0")
		lines.append("  log10(100) → 2")
		lines.append("  pow(2,8)   → 256")
		lines.append("\n数学常量：")
		lines.append("  pi         → 3.141592...")
		lines.append("  e          → 2.718281...")
		lines.append("\n支持的函数：")
		funcs = ', '.join(sorted(cls.MATH_FUNCTIONS.keys()))
		lines.append(f"  {funcs}")
		return "\n".join(lines)
