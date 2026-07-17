# Quick Lookup

轻量 Windows 划词词典：划选英文后显示中文翻译；单词额外展示音标、词性、英文释义与例句。默认使用在线 API，也可切换为完全离线模式。

## 使用

```powershell
python -m pip install -r requirements.txt
python quick_translate.py
```

也可双击 `启动快速翻译.bat` 静默启动。

- 拖选英文短语后松开鼠标：浮窗翻译。
- 双击英文单词：翻译 + 词典释义。
- `Ctrl + Alt + P`：在**划词右侧**和**屏幕居中**之间切换浮窗位置。
- `Ctrl + Alt + S`：打开统一设置入口，可调整浮窗位置、翻译模式、主题、字体、字号、文字/背景颜色和当前用户的开机启动。
- `Ctrl + Alt + Q`：退出。

程序会模拟 `Ctrl+C` 读取当前选择内容，因此仅适用于支持复制文字的应用。

## 离线词库

离线模式的翻译来自项目内的 `offline_dictionary.json`，无需 API 密钥。默认词库收录常用技术、界面与日常英语单词和短语；未收录内容会显示提示。

你可以直接编辑 `offline_dictionary.json` 来补充词条。每个词条可包含 `zh`、`ipa`、`part_of_speech`、`definitions` 和 `examples`。

### 翻译模式

在 `quick_lookup_config.json` 设置 `translation_mode`：

- `api`：在线翻译 + 在线词典释义（默认，需要网络）。
- `smart`：优先匹配整个单词或短语；未收录的短语再逐词翻译（离线）。
- `exact`：只匹配完整词条，未收录即提示。
- `word_by_word`：短语始终按单词拆分翻译。

`smart`、`exact` 和 `word_by_word` 只读取本地词库，不会联网。

### 主题

`theme` 可选 `dark`、`light`、`ocean` 和 `forest`。主题定义保存在 `themes.json`，可以新增自己的预设。若只想微调某个颜色，请在 `theme_overrides` 中写入颜色值；它会覆盖当前主题而不影响其他颜色。

## 配置

`quick_lookup_config.json`：

```json
{
  "popup_position": "selection_right",
  "translation_mode": "api",
  "theme": "ocean",
  "theme_overrides": {
    "translation_text_color": "#8BE9FD"
  },
  "font_family": "Microsoft YaHei UI",
  "font_size": 11,
  "run_at_startup": false
}
```

颜色为 `#RRGGBB` 格式。`theme_overrides` 可分别调整浮窗背景、标题、译文、释义、示例/提示和页脚颜色；`font_family` 与 `font_size` 控制字体。

设置窗口保存的个人偏好写入 `quick_lookup_config.local.json`，不会提交到 GitHub。开机启动仅写入当前 Windows 用户的启动项，可随时在 `Ctrl + Alt + S` 中关闭。

运行日志位于 `quick_translate.log`，不会记录你选中的原文。

## 打包与发布

本地构建 Windows 单文件程序：

```powershell
.\scripts\build_release.ps1
```

构建产物为 `dist\QuickLookup.exe`。打包版会把默认词库和主题内置；个人配置、日志和设置保存在 `%LOCALAPPDATA%\QuickLookup`，不会因升级而丢失。

GitHub Actions 工作流位于 `.github/workflows/release.yml`：推送形如 `v0.3.0` 的标签会自动构建 `QuickLookup.exe`，创建对应的 GitHub Release 并上传该文件。也可以在 Actions 页面手动运行工作流并填写版本标签。

## 开源

本项目采用 [MIT License](LICENSE)。欢迎提交 issue、词典提供方适配器、界面主题和多语言支持。
