# 视觉素材

## 插件 Logo

根目录的 [`logo.png`](../logo.png) 是六爻起卦插件的方形图标，2026-11-17 使用内置 imagegen 工具生成，保留原始 PNG。主体是玉环与六道阴阳爻，朱红圆点呼应动爻；不含文字，便于用作插件列表图标和头像。配色沿用排盘渲染器的玉绿 `#234A3B`、米白 `#F6F3EA` 和朱红 `#B34F40`。

生成后检查了方形尺寸、完整六爻、图形留白和文件可读性；README 引用同一文件。此图标为视觉标识，不作为实际排盘结果。

完整生成提示词：

```text
Use case: logo-brand.
Asset type: finished square plugin logo for the Chinese Liuyao divination plugin 六爻起卦 in AstrBot, designed to remain clear at small avatar sizes.
Primary request: create one elegant original emblem that combines an open jade circular ring with six yin-yang hexagram lines. The identity should feel calm, scholarly, contemporary Chinese, carefully proportioned and memorable.
Scene/backdrop: uniform opaque warm rice-paper ivory #F6F3EA, no paper texture.
Subject: a bold jade ring surrounds a centered vertical stack of EXACTLY SIX horizontal hexagram rows. From TOP to BOTTOM: 1 unbroken bar, 2 broken bar (two equal halves), 3 unbroken bar, 4 broken bar (two equal halves), 5 unbroken bar, 6 broken bar (two equal halves). All six rows have equal widths, consistent generous vertical spacing, moderately thick strokes with subtly softened corners, and crisp clear gaps. The stack must read as six separate lines, never five or seven. One small cinnabar red circular moving-line mark sits beside the fifth row and is integrated into a deliberate opening in the surrounding jade ring. The ring is a refined abstract jade bi-disc / coin reference, with a strong smooth silhouette and balanced white space.
Style/medium: exceptionally polished flat vector-like logo artwork with precise geometry, restrained modern Eastern visual identity, no lettering.
Composition/framing: 1:1 square canvas, a single centered large emblem occupying about 78 percent of the canvas, comfortable equal safe margins. The ring and hexagram must feel like one unified mark, with no overlap between ring and bars.
Color palette: existing plugin colors only: deep jade green #234A3B for ring and all six rows, warm ivory #F6F3EA for background and negative space, muted cinnabar #B34F40 for the single small moving-line dot.
Constraints: deliver only the standalone logo, no mockup, no brand presentation sheet, no variations, no text, no Chinese characters, no letters, no numbers, no watermark, no surrounding trigrams, no extra symbols, no bamboo decoration, no thin ornamental lines, no gradients, no shadows, no glow, no 3D, no metallic effects. Clean antialiased edges and a simple silhouette that works at 32 to 64 pixels.
```

## 排盘装饰素材

`bamboo.png`：2026-10-02 使用内置 imagegen 工具生成的透明水墨竹子，原始尺寸 1024 × 1536，随插件本地加载，无运行时网络请求。渲染器按原始透明通道叠加：右侧 8%、左侧 5% 不透明度，竹竿末端渐隐。不会覆盖前景文字；素材缺失或无法读取时保留米白底。

完整生成提示词：

> Use case: stylized-concept. Asset type: transparent decorative bamboo overlay for a Chinese Liuyao divination report. Create a refined Chinese ink-wash bamboo composition: two slender jointed bamboo stalks emerging from bottom right, graceful arching branches with sparse clusters of long pointed leaves, flowing toward upper left. Jade green and muted sage ink, elegant natural brush strokes, restrained modern Eastern editorial aesthetic. Portrait 2:3 composition, subject concentrated in right half and lower right, generous fully transparent negative space to the left. Clearly visible medium-tone bamboo; the application will reduce opacity. Transparent background with genuine alpha, no paper background, no text, no seal, no border, no unrelated objects.

字体来源及许可另见 [fonts/README.md](fonts/README.md)。
