# 视觉素材

## 插件 Logo

根目录的 [`logo.png`](../logo.png) 是六爻起卦插件的方形图标，2026-11-17 使用内置 imagegen 工具生成并按用户要求编辑，保留 1254 × 1254 原始 PNG。主体仅保留旅卦（上离下艮），去除外环、朱红圆点和其他装饰，将半透明山水效果嵌入六道爻线内部，阴爻断口与爻间留白保持干净。配色沿用排盘渲染器的玉绿 `#234A3B` 与米白 `#F6F3EA`；背景不透明，山水半透体现为图像内的叠色效果。

依据 `data/zhouyi.json` 的第 56 卦核对爻序：自上而下为阳、阴、阳、阳、阴、阴（底向上的二进制为 `001101`）。已目视确认六爻结构、山水位于爻线内部及无外圈圆点，并检查方形尺寸、图形留白和文件可读性；README 引用同一文件。

最终编辑提示词：

```text
Use case: precise-object-edit.
Asset type: finished square logo for the 六爻起卦 plugin.
Input image: the existing plugin logo is the edit target.
Primary request: redesign the provided logo as ONLY the Fire-over-Mountain hexagram 旅, with a subtle semi-transparent Chinese shanshui landscape embedded inside its six line shapes. Remove the surrounding circular ring and the red dot completely.
Hexagram geometry is mandatory: exactly SIX horizontal rows, read from TOP to BOTTOM:
Row 1: one continuous solid yang bar.
Row 2: broken yin bar, two equal segments separated by a clean central gap.
Row 3: one continuous solid yang bar.
Row 4: one continuous solid yang bar.
Row 5: broken yin bar, two equal segments separated by a clean central gap.
Row 6: broken yin bar, two equal segments separated by a clean central gap.
This is 旅 / ䷷, upper trigram Li (solid-broken-solid), lower trigram Gen (solid-broken-broken from top to bottom). Do not print these words, the symbol, or row numbers.
Composition: a single large centered hexagram on a square canvas, around 76 percent of the canvas width and 68 percent of its height, generous equal outer margins. Six thick horizontal bars of identical overall width and thickness with consistent vertical spacing and subtly softened corners. All broken bars have an identical central gap. The hexagram itself is the entire logo.
Landscape treatment: retain the original deep jade-green identity for the bars, and add a delicate, clearly visible translucent ink-wash mountain-and-water landscape over the jade fill at about 35–45 percent visual opacity. The mountains should form one continuous composition across the whole stack, with misty layered ridges above and a quiet lake below. Clip this landscape strictly INSIDE the six jade bar shapes: absolutely no landscape outside the bars, in the yin gaps, or in the spaces between rows. The landscape adds depth and poetic atmosphere while the dark jade silhouette of each bar remains crisp and easy to recognize.
Preserve: warm ivory background #F6F3EA, jade-green base #234A3B, restrained elegant Chinese aesthetic, square canvas, smooth antialiased edges.
Remove: the whole jade ring, red dot, border, seals, all text, numerals, ornaments, extra symbols, bamboo, people, buildings, boats, sun, moon, watermarks, mockups, presentation layouts. No new surrounding elements. No 3D, cast shadows, bevels, glow, or metallic effects. Only the correct six-line 旅 hexagram containing the semi-transparent landscape, on plain ivory.
```

## 排盘装饰素材

`bamboo.png`：2026-10-02 使用内置 imagegen 工具生成的透明水墨竹子，原始尺寸 1024 × 1536，随插件本地加载，无运行时网络请求。渲染器按原始透明通道叠加：右侧 8%、左侧 5% 不透明度，竹竿末端渐隐。不会覆盖前景文字；素材缺失或无法读取时保留米白底。

完整生成提示词：

> Use case: stylized-concept. Asset type: transparent decorative bamboo overlay for a Chinese Liuyao divination report. Create a refined Chinese ink-wash bamboo composition: two slender jointed bamboo stalks emerging from bottom right, graceful arching branches with sparse clusters of long pointed leaves, flowing toward upper left. Jade green and muted sage ink, elegant natural brush strokes, restrained modern Eastern editorial aesthetic. Portrait 2:3 composition, subject concentrated in right half and lower right, generous fully transparent negative space to the left. Clearly visible medium-tone bamboo; the application will reduce opacity. Transparent background with genuine alpha, no paper background, no text, no seal, no border, no unrelated objects.

字体来源及许可另见 [fonts/README.md](fonts/README.md)。
