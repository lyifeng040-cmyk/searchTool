# 图标文件夹

请将应用图标放在此目录：

- `32x32.png` - 32x32像素PNG图标
- `128x128.png` - 128x128像素PNG图标
- `128x128@2x.png` - 256x256像素PNG图标（高DPI）
- `icon.icns` - macOS图标
- `icon.ico` - Windows图标

## 生成图标

可以使用在线工具或命令行工具生成：

```bash
# 使用ImageMagick
convert icon.png -resize 32x32 32x32.png
convert icon.png -resize 128x128 128x128.png
convert icon.png -resize 256x256 128x128@2x.png

# 生成Windows ICO
convert icon.png -define icon:auto-resize=256,128,64,48,32,16 icon.ico

# 生成macOS ICNS（需要iconutil）
mkdir icon.iconset
sips -z 16 16 icon.png --out icon.iconset/icon_16x16.png
sips -z 32 32 icon.png --out icon.iconset/icon_16x16@2x.png
# ... 其他尺寸
iconutil -c icns icon.iconset
```

## 推荐尺寸

- 16x16
- 32x32
- 48x48
- 64x64
- 128x128
- 256x256
- 512x512
- 1024x1024

