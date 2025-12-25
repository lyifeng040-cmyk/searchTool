"""
颜色工具和单位转换器
"""
import re


class ColorTool:
	"""颜色转换工具"""
	
	@staticmethod
	def is_color(text):
		"""检测是否为颜色值"""
		text = text.strip()
		# 十六进制颜色
		if re.match(r'^#[0-9A-Fa-f]{3}$', text) or re.match(r'^#[0-9A-Fa-f]{6}$', text):
			return True
		# RGB
		if re.match(r'^rgb\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)$', text, re.IGNORECASE):
			return True
		# RGBA
		if re.match(r'^rgba\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*[\d.]+\s*\)$', text, re.IGNORECASE):
			return True
		return False
	
	@staticmethod
	def parse_color(text):
		"""解析颜色值并转换为多种格式"""
		text = text.strip()
		
		# 解析十六进制
		hex_match = re.match(r'^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$', text)
		if hex_match:
			hex_color = hex_match.group(1)
			if len(hex_color) == 3:
				hex_color = ''.join([c*2 for c in hex_color])
			r = int(hex_color[0:2], 16)
			g = int(hex_color[2:4], 16)
			b = int(hex_color[4:6], 16)
			return ColorTool._convert_rgb(r, g, b, hex_color)
		
		# 解析 RGB
		rgb_match = re.match(r'^rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$', text, re.IGNORECASE)
		if rgb_match:
			r, g, b = map(int, rgb_match.groups())
			hex_color = f"{r:02x}{g:02x}{b:02x}"
			return ColorTool._convert_rgb(r, g, b, hex_color)
		
		return None
	
	@staticmethod
	def _convert_rgb(r, g, b, hex_color):
		"""转换 RGB 到各种格式"""
		# HSL 转换
		r_norm, g_norm, b_norm = r/255, g/255, b/255
		max_val = max(r_norm, g_norm, b_norm)
		min_val = min(r_norm, g_norm, b_norm)
		diff = max_val - min_val
		
		# Lightness
		l = (max_val + min_val) / 2
		
		# Saturation
		if diff == 0:
			h = s = 0
		else:
			s = diff / (2 - max_val - min_val) if l > 0.5 else diff / (max_val + min_val)
			
			# Hue
			if max_val == r_norm:
				h = ((g_norm - b_norm) / diff + (6 if g_norm < b_norm else 0)) / 6
			elif max_val == g_norm:
				h = ((b_norm - r_norm) / diff + 2) / 6
			else:
				h = ((r_norm - g_norm) / diff + 4) / 6
		
		h = int(h * 360)
		s = int(s * 100)
		l_percent = int(l * 100)
		
		return {
			'hex': f"#{hex_color.upper()}",
			'rgb': f"rgb({r}, {g}, {b})",
			'rgba': f"rgba({r}, {g}, {b}, 1.0)",
			'hsl': f"hsl({h}, {s}%, {l_percent}%)",
			'r': r,
			'g': g,
			'b': b,
			'h': h,
			's': s,
			'l': l_percent
		}


class UnitConverter:
	"""单位转换器"""
	
	# 转换规则
	CONVERSIONS = {
		# 长度
		'length': {
			'mm': 1,
			'cm': 10,
			'm': 1000,
			'km': 1000000,
			'inch': 25.4,
			'ft': 304.8,
			'yard': 914.4,
			'mile': 1609344,
		},
		# 重量
		'weight': {
			'mg': 1,
			'g': 1000,
			'kg': 1000000,
			'oz': 28349.5,
			'lb': 453592,
		},
		# 温度特殊处理
		# 数据大小
		'data': {
			'b': 1,
			'byte': 1,
			'kb': 1024,
			'mb': 1024**2,
			'gb': 1024**3,
			'tb': 1024**4,
		},
		# 时间
		'time': {
			'ms': 1,
			's': 1000,
			'min': 60000,
			'hour': 3600000,
			'day': 86400000,
		},
	}
	
	@staticmethod
	def is_conversion(text):
		"""检测是否为单位转换请求"""
		# 匹配格式: "100 km to miles" 或 "32F to C"
		pattern = r'^\s*[\d.]+\s*[a-zA-Z]+\s+to\s+[a-zA-Z]+\s*$'
		return bool(re.match(pattern, text, re.IGNORECASE))
	
	@staticmethod
	def convert(text):
		"""执行单位转换"""
		text = text.strip()
		
		# 温度转换
		temp_match = re.match(r'^\s*([\d.]+)\s*([FCK])\s+to\s+([FCK])\s*$', text, re.IGNORECASE)
		if temp_match:
			value = float(temp_match.group(1))
			from_unit = temp_match.group(2).upper()
			to_unit = temp_match.group(3).upper()
			result = UnitConverter._convert_temperature(value, from_unit, to_unit)
			if result is not None:
				return True, f"{value}°{from_unit} = {result:.2f}°{to_unit}"
		
		# 其他单位转换
		match = re.match(r'^\s*([\d.]+)\s*([a-zA-Z]+)\s+to\s+([a-zA-Z]+)\s*$', text, re.IGNORECASE)
		if not match:
			return False, "格式错误，请使用: 100 km to miles"
		
		value = float(match.group(1))
		from_unit = match.group(2).lower()
		to_unit = match.group(3).lower()
		
		# 查找适用的转换类别
		for category, units in UnitConverter.CONVERSIONS.items():
			if from_unit in units and to_unit in units:
				from_base = value * units[from_unit]
				to_value = from_base / units[to_unit]
				return True, f"{value} {from_unit} = {to_value:.4f} {to_unit}"
		
		return False, f"不支持 {from_unit} 到 {to_unit} 的转换"
	
	@staticmethod
	def _convert_temperature(value, from_unit, to_unit):
		"""温度转换"""
		if from_unit == to_unit:
			return value
		
		# 先转换为摄氏度
		if from_unit == 'F':
			celsius = (value - 32) * 5/9
		elif from_unit == 'K':
			celsius = value - 273.15
		else:
			celsius = value
		
		# 再转换为目标单位
		if to_unit == 'F':
			return celsius * 9/5 + 32
		elif to_unit == 'K':
			return celsius + 273.15
		else:
			return celsius
