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

### 翻译模式

在 `quick_lookup_config.json` 设置 `translation_mode`：

- `smart`：优先匹配整个单词或短语；未收录的短语再逐词翻译（默认）。
- `exact`：只匹配完整词条，未收录即提示。
- `word_by_word`：短语始终按单词拆分翻译。

三个模式都只读取本地词库，不会联网。

## 配置

`quick_lookup_config.json`：

```json
{
  "popup_position": "selection_right",
  "translation_mode": "smart",
  "popup_background": "#202124",
  "title_text_color": "#FFFFFF",
  "translation_text_color": "#B9D4FF",
  "definition_text_color": "#E8EAED",
  "secondary_text_color": "#AEB4BC",
  "muted_text_color": "#7F8792",
  "font_family": "Microsoft YaHei UI",
  "font_size": 11
}
```

颜色为 `#RRGGBB` 格式。可分别调整浮窗背景、标题、译文、释义、示例/提示和页脚颜色；`font_family` 与 `font_size` 控制字体。

运行日志位于 `quick_translate.log`，不会记录你选中的原文。

## 开源

本项目采用 [MIT License](LICENSE)。欢迎提交 issue、词典提供方适配器、界面主题和多语言支持。
