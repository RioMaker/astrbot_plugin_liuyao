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

## 山水太极标题素材

[`taiji-landscape.png`](taiji-landscape.png)：2026-11-17 使用内置 imagegen 工具，参照旅卦 Logo 的玉绿山水风格生成太极预览，并将圆形外部背景转为透明；此文件为用户确认的卦图预览所用原始素材，保留其 RGBA 通道。浅色阴阳鱼和鱼眼属于图形内部，保持不透明。

从 0.38.1 起，排盘图（72 × 72）、解卦笺（76 × 76）、帮助图（64 × 64）和反馈图卡（50 × 50）共用此素材。渲染时裁去透明边距、缩放到原有图标框，并保留原始透明通道合成；按路径和尺寸缓存小图，不依赖工作目录，不访问网络。素材缺失、损坏或全透明时回退到原有几何太极；显式指定其他颜色时仍使用纯色太极。替换素材后重载插件，以刷新图片缓存。

风格生成提示词：

```text
Use case: precise-object-edit.
Asset type: a preview concept for the Taiji symbol in a Chinese Liuyao divination report.
Input image: the supplied jade-and-ivory hexagram logo is the edit target and the visual style reference.
Primary request: replace the six-line hexagram with one beautifully balanced, perfectly circular Taiji yin-yang symbol, while retaining the same muted jade palette and translucent mountain-and-water landscape treatment. This is a standalone preview of the symbol.
Geometry: reproduce the traditional upright Taiji structure used in the plugin: a deep jade left half that curves into a rounded head at the top, and a pale ivory right half that curves into a rounded head at the bottom. They interlock through one smooth, symmetric vertical S-shaped boundary. Place exactly two equal-size circular eyes on the vertical centerline: one ivory eye centered in the upper jade head, and one jade eye centered in the lower ivory head. The eyes must remain clean, flat circular shapes in the opposite color. The total silhouette is a mathematically round circle, with balanced halves and recognizable classic Taiji proportions. A single very thin jade contour may define the light half against the background, but no separate enclosing ring or frame.
Landscape: like the reference image, embed a softly translucent Chinese shanshui landscape of misty layered mountains and a still lake INSIDE the circular yin-yang shape. On the dark jade half use a visible but restrained 35–45 percent-opacity misty mountain landscape. On the pale ivory half use a much softer pale sage mountain-and-water wash, around 15–25 percent opacity, to preserve a clear dark/light distinction. Let the scenery flow coherently across the circle: distant peaks toward the top, mist through the middle, calm water toward the bottom. Keep both circular eyes visually clean and crisp, not filled with scenery. No scenery outside the circle.
Palette: deep jade green #234A3B, warm ivory #F6F3EA, translucent sage tones from their blending. Match the understated elegant atmosphere of the reference.
Composition: one large centered round Taiji symbol occupying about 76 percent of a 1:1 square canvas, equal spacious margins, clean warm ivory background. Smooth antialiased edges, polished restrained modern Chinese visual identity.
Remove the hexagram entirely. No bars, text, Chinese characters, letters, seals, red dots, surrounding trigrams, ornaments, bamboo, people, buildings, boats, sun, moon, extra circles, watermarks, mockups, brand presentation sheets, 3D, metallic effects, shadows, glow, bevels or perspective. Show only the jade-and-ivory landscape Taiji preview.
```

透明素材编辑提示词：

```text
Use case: background-extraction.
Input image: the approved jade-and-ivory landscape Taiji preview is the edit target.
Primary request: make an application-ready transparent cutout of ONLY this exact circular landscape Taiji symbol for placing in the header of a Chinese divination report.
Remove only the ivory canvas OUTSIDE the outer circular silhouette, replacing it with genuine alpha transparency. Preserve the entire interior of the symbol unchanged: the jade mountains on the dark left-and-upper half, the pale sage mountains and lake on the ivory right-and-lower half, the smooth S-shaped division, both clean opposite-color circular eyes, the fine jade outline, the muted colors, and all landscape details. The light-colored half and the ivory upper eye INSIDE the circle must remain fully opaque; they are part of the artwork, not background to erase.
Use a square canvas and keep the complete circle, with tight and even transparent margins of around 2 percent of the canvas. Do not crop the circle. Clean antialiased transparent outer edge without ivory halo. No new marks, no text, no backdrop, no shadow, no border, no grid or checkerboard drawn into the image, no rearrangement, no redrawing or restyling of the artwork. Preserve the exact approved symbol as closely as possible.
```

## 排盘装饰素材

`bamboo.png`：2026-10-02 使用内置 imagegen 工具生成的透明水墨竹子，原始尺寸 1024 × 1536，随插件本地加载，无运行时网络请求。渲染器按原始透明通道叠加：右侧 8%、左侧 5% 不透明度，竹竿末端渐隐。不会覆盖前景文字；素材缺失或无法读取时保留米白底。

完整生成提示词：

> Use case: stylized-concept. Asset type: transparent decorative bamboo overlay for a Chinese Liuyao divination report. Create a refined Chinese ink-wash bamboo composition: two slender jointed bamboo stalks emerging from bottom right, graceful arching branches with sparse clusters of long pointed leaves, flowing toward upper left. Jade green and muted sage ink, elegant natural brush strokes, restrained modern Eastern editorial aesthetic. Portrait 2:3 composition, subject concentrated in right half and lower right, generous fully transparent negative space to the left. Clearly visible medium-tone bamboo; the application will reduce opacity. Transparent background with genuine alpha, no paper background, no text, no seal, no border, no unrelated objects.

字体来源及许可另见 [fonts/README.md](fonts/README.md)。
