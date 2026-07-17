# Quick Lookup

轻量 Windows 划词词典：划选英文后显示中文翻译；单词额外展示音标、词性、英文释义与例句。

## 使用

```powershell
python -m pip install -r requirements.txt
python quick_translate.py
```

也可双击 `启动快速翻译.bat` 静默启动。

- 拖选英文短语后松开鼠标：浮窗翻译。
- 双击英文单词：翻译 + 词典释义。
- `Ctrl + Alt + P`：在**划词右侧**和**屏幕居中**之间切换浮窗位置。
- `Ctrl + Alt + Q`：退出。

程序会模拟 `Ctrl+C` 读取当前选择内容，因此仅适用于支持复制文字的应用。

## 词典提供方

默认提供方为 [Free Dictionary API](https://dictionaryapi.dev/)，无需密钥。短语使用翻译服务；词典释义只在单词查询时展示。

可选使用 Oxford Dictionaries API。它是需要凭据的官方服务，项目不会抓取 Oxford 网站内容。请在环境变量中设置自己的密钥：

```powershell
$env:OXFORD_APP_ID = "your-app-id"
$env:OXFORD_APP_KEY = "your-app-key"
```

然后将 `quick_lookup_config.json` 的 `dictionary_provider` 改为 `"oxford"`。可选的 `language` 为 `en-gb` 或 `en-us`。

## 配置

`quick_lookup_config.json`：

```json
{
  "popup_position": "selection_right",
  "dictionary_provider": "free",
  "language": "en-gb"
}
```

运行日志位于 `quick_translate.log`，不会记录你选中的原文。

## 开源

本项目采用 [MIT License](LICENSE)。欢迎提交 issue、词典提供方适配器、界面主题和多语言支持。
