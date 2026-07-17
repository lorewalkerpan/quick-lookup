# Quick Lookup

轻量 Windows 划词词典：划选英文后显示中文翻译；单词额外展示音标、词性、英文释义与例句。运行时完全不调用翻译或词典 API。

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

## 离线词库

所有翻译均来自项目内的 `offline_dictionary.json`，程序不会联网，也不需要 API 密钥。默认词库收录常用技术、界面与日常英语单词和短语；未收录内容会显示提示，而不会转而访问网络。

你可以直接编辑 `offline_dictionary.json` 来补充词条。每个词条可包含 `zh`、`ipa`、`part_of_speech`、`definitions` 和 `examples`。

## 配置

`quick_lookup_config.json`：

```json
{
  "popup_position": "selection_right"
}
```

运行日志位于 `quick_translate.log`，不会记录你选中的原文。

## 开源

本项目采用 [MIT License](LICENSE)。欢迎提交 issue、词典提供方适配器、界面主题和多语言支持。
