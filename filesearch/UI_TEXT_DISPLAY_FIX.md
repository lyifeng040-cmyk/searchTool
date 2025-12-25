# UI 文本显示优化

## 问题描述

在搜索结果列表中，当文件名或路径过长时，文本会超出单元格范围但不换行，导致显示不完整，用户无法看清完整内容。

## 解决方案

### 1. 文本省略模式 (Text Elide Mode)

在 `ui/components/ui_builder.py` 中设置 `QTreeWidget` 的文本省略模式：

```python
main.tree.setTextElideMode(Qt.ElideMiddle)
```

**效果**：
- 当文本超过列宽时，中间部分会被省略号 `...` 替代
- 例如：`这是一个很长很长...的文件名.txt`
- 保留开头和结尾，方便识别文件类型和前缀

### 2. 统一行高 (Uniform Row Heights)

```python
main.tree.setUniformRowHeights(True)
```

**效果**：
- 所有行高度一致，避免因文本溢出导致的布局问题
- 提升渲染性能（Qt 优化）

### 3. 固定行高样式

在样式表中设置：

```css
QTreeWidget::item { 
    padding: 2px;
    height: 24px;
}
```

**效果**：
- 确保每行高度固定为 24px
- 防止文本垂直溢出

### 4. Tooltip 提示

在 `ui/components/result_renderer.py` 中为每个单元格添加 tooltip：

```python
q_item.setToolTip(0, filename)
q_item.setToolTip(1, dir_path)
```

**效果**：
- 鼠标悬停在单元格上时，会显示完整的文件名或路径
- 即使显示被省略，也能看到完整内容

## 省略模式对比

Qt 提供三种省略模式：

| 模式 | 说明 | 示例 |
|------|------|------|
| `Qt.ElideLeft` | 左侧省略 | `...长文件名.txt` |
| `Qt.ElideMiddle` | 中间省略 | `很长的文...名.txt` ✅ **推荐** |
| `Qt.ElideRight` | 右侧省略 | `很长的文件名...` |

**选择 ElideMiddle 的原因**：
- 保留文件名开头（通常包含关键信息）
- 保留文件扩展名（便于识别文件类型）
- 符合 Everything 和 Windows 资源管理器的习惯

## 用户体验改进

### 之前
```
华润AB地块昌都8-9_recover_recover_recover_1_1_7088.sv$_t8_1_1_5401_recover.d
```
文本溢出，无法看清完整内容

### 之后
```
华润AB地块昌都8-9_recover_...5401_recover.dwg
```
中间省略，保留关键信息，鼠标悬停显示完整内容

## 技术细节

### 修改的文件

1. **ui/components/ui_builder.py**
   - 添加 `setTextElideMode(Qt.ElideMiddle)`
   - 添加 `setUniformRowHeights(True)`
   - 更新样式表，添加固定行高

2. **ui/components/result_renderer.py**
   - 为文件名和路径列添加 tooltip
   - 鼠标悬停显示完整内容

### 性能影响

- **正面影响**：
  - `setUniformRowHeights(True)` 提升渲染性能
  - 文本省略由 Qt 原生处理，无额外开销
  
- **无负面影响**：
  - Tooltip 仅在鼠标悬停时触发
  - 不影响搜索和排序性能

## 测试验证

所有 28 个测试用例通过：
```bash
pytest tests/ -v
========================== 28 passed in 0.61s ==========================
```

## 兼容性

- ✅ Windows 10/11
- ✅ PySide6 (Qt6)
- ✅ 向后兼容，不影响现有功能
- ✅ Everything 风格搜索语法完全兼容
