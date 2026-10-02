# 内置字体

- `NotoSansCJKsc-Regular.otf`：Noto Sans CJK SC 正文中文字体。
- `LiuyaoEditorialSerif.otf`：由 Noto Serif CJK SC SemiBold 裁剪并重命名的标题字体，保留卦名、干支和固定标题所需字符。用户问题、昵称和分析使用完整正文字体。

两者均按 [SIL Open Font License 1.1](LICENSE.txt) 分发。标题字体原版权为 `© 2017-2024 Adobe (http://www.adobe.com/).`，子集保留原字体版权及许可记录。字体只随插件使用，不单独销售。

标题字体上游：[Noto CJK Serif](https://github.com/notofonts/noto-cjk/tree/main/Serif)，[源文件](https://raw.githubusercontent.com/notofonts/noto-cjk/main/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-SemiBold.otf)。获取日期：2026-10-02；源文件 SHA-256：`d627b53dbcde61e07de1498d2623a8b287f78585ffbc90cc0618d0caaa2ed6b0`。

开发时可安装 `fonttools==4.66.1`，执行 `python scripts/subset_title_font.py /path/to/NotoSerifCJKsc-SemiBold.otf` 重新生成。该工具只读取本地源文件，不联网；fonttools 不是插件运行依赖。自定义 `chart_font_path` 时，标题和正文均采用用户指定字体。
