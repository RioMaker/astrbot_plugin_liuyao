# 分类研判参考索引

版本：1；2026-09-24。每类一份，运行时不联网，启动时缓存；不将全部类别同时传给模型。

| 类别键 | 文档 | 当前深度 |
| --- | --- | --- |
| common | [通则](common.md) | 数据能力边界、取用顺序、证据与输出规则 |
| weather | [天气](weather.md) | 六亲、动变、流派分歧、应期条件、输出和复盘，重点整理 |
| shefu | [射覆](shefu.md) | 物象依据、属性画像、候选排序和揭晓复盘 |
| general | [综合](general.md) | 明确问题、分类及通用判断框架 |
| career | [事业](career.md) | 功名取用与现代职位问题的边界 |
| relationship | [感情](relationship.md) | 关系对象、传统角色及现代适用限制 |
| wealth | [财富](wealth.md) | 求财、到账与风险区分 |
| study | [学业](study.md) | 文书、功名与现代考试分层 |
| health | [健康](health.md) | 传统取用介绍与不作医学诊断的边界 |
| family | [家庭](family.md) | 亲属、宅舍、家庭事务分开取用 |
| travel | [出行](travel.md) | 行程、舟车票证与天气区分 |

天气是此次重点；其余各类为有来源的首版框架，不声称已经穷尽各门古籍或所有特殊断法。“类型”指插件方向分类，不是另写64份重复卦解；64卦经文仍由既有 `zhouyi.json` 提供。

`ReadingService.render(for_agent=True)` 返回通则和对应正文，并附当次卦爻辞。短评生成也使用当前分类参考；普通指令不向群里倾倒参考全文。文档缺失/损坏时明确告知 Agent 未加载，起卦与图片流程继续可用。

新增方向必须同步 `intents.json`、命令别名/检测及同名 Markdown，跑齐覆盖测试。任何参考文件上限为60KB且正文不超过14000字符；不允许类别路径越界，不接受工具参数直接指定文件。

来源与校核状态见 [候选区](../../docs/candidates/WEATHER_SOURCES.md)。古籍原文属公版；本目录的数字摘录与现代整理整体以 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.zh-hans) 提供。现代整理：Rio / astrbot_plugin_liuyao；维基文库数字文本贡献者见来源页面历史。
