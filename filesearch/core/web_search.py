"""
网页搜索集成 - 支持快速跳转到各大搜索引擎
"""
import webbrowser
import urllib.parse


class WebSearchEngine:
	"""网页搜索引擎管理器"""
	
	# 预定义的搜索引擎
	ENGINES = {
		'g': {
			'name': 'Google',
			'url': 'https://www.google.com/search?q={query}',
			'icon': '🔍'
		},
		'bd': {
			'name': '百度',
			'url': 'https://www.baidu.com/s?wd={query}',
			'icon': '🔍'
		},
		'bing': {
			'name': 'Bing',
			'url': 'https://www.bing.com/search?q={query}',
			'icon': '🔍'
		},
		'gh': {
			'name': 'GitHub',
			'url': 'https://github.com/search?q={query}',
			'icon': '💻'
		},
		'so': {
			'name': 'Stack Overflow',
			'url': 'https://stackoverflow.com/search?q={query}',
			'icon': '📚'
		},
		'yt': {
			'name': 'YouTube',
			'url': 'https://www.youtube.com/results?search_query={query}',
			'icon': '🎬'
		},
		'wiki': {
			'name': 'Wikipedia',
			'url': 'https://zh.wikipedia.org/wiki/Special:Search?search={query}',
			'icon': '📖'
		},
		'zhihu': {
			'name': '知乎',
			'url': 'https://www.zhihu.com/search?q={query}',
			'icon': '💡'
		},
		'taobao': {
			'name': '淘宝',
			'url': 'https://s.taobao.com/search?q={query}',
			'icon': '🛒'
		},
		'jd': {
			'name': '京东',
			'url': 'https://search.jd.com/Search?keyword={query}',
			'icon': '🛍️'
		},
		'bilibili': {
			'name': 'B站',
			'url': 'https://search.bilibili.com/all?keyword={query}',
			'icon': '📺'
		},
		'douban': {
			'name': '豆瓣',
			'url': 'https://www.douban.com/search?q={query}',
			'icon': '📖'
		},
		'maps': {
			'name': 'Google Maps',
			'url': 'https://www.google.com/maps/search/{query}',
			'icon': '🗺️'
		},
		'translate': {
			'name': 'Google Translate',
			'url': 'https://translate.google.com/?text={query}',
			'icon': '🌐'
		},
	}
	
	@classmethod
	def parse_query(cls, text):
		"""
		解析查询文本，检测是否为网页搜索命令
		
		返回: (engine_key, query) 或 (None, None)
		
		示例:
		  "g: python tutorial" -> ('g', 'python tutorial')
		  "bd: 北京天气" -> ('bd', '北京天气')
		  "normal search" -> (None, None)
		"""
		text = text.strip()
		if not text:
			return None, None
		
		# 检测前缀格式: "prefix: query" 或 "prefix:query"
		for prefix in cls.ENGINES.keys():
			# 支持 "g:" 或 "g: " 格式
			if text.startswith(prefix + ':'):
				query = text[len(prefix) + 1:].strip()
				if query:
					return prefix, query
		
		return None, None
	
	@classmethod
	def search(cls, engine_key, query):
		"""
		在指定搜索引擎中搜索
		
		Args:
		    engine_key: 搜索引擎键（如 'g', 'bd'）
		    query: 搜索查询
		
		Returns:
		    bool: 是否成功打开
		"""
		if engine_key not in cls.ENGINES:
			return False
		
		engine = cls.ENGINES[engine_key]
		encoded_query = urllib.parse.quote(query)
		url = engine['url'].format(query=encoded_query)
		
		try:
			webbrowser.open(url)
			return True
		except Exception:
			return False
	
	@classmethod
	def get_engine_info(cls, engine_key):
		"""获取搜索引擎信息"""
		return cls.ENGINES.get(engine_key)
	
	@classmethod
	def get_all_engines(cls):
		"""获取所有搜索引擎列表"""
		return [(key, info['name'], info['icon']) for key, info in cls.ENGINES.items()]
	
	@classmethod
	def get_help_text(cls):
		"""生成帮助文本"""
		lines = ["支持的网页搜索前缀：\n"]
		for key, info in sorted(cls.ENGINES.items()):
			lines.append(f"  {info['icon']} {key}: - {info['name']}")
		lines.append("\n示例：")
		lines.append("  g: python tutorial  → Google 搜索")
		lines.append("  bd: 北京天气        → 百度搜索")
		lines.append("  gh: microsoft/vscode → GitHub 搜索")
		return "\n".join(lines)
