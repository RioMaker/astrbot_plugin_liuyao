# astrbot_plugin_liuyao

面向 QQ 群（OneBot v11 / aiocqhttp）的六爻问卦插件。支持即时天机、手动铜币、群主级群开关，并把起卦和《周易》原文查询注册为 AstrBot Agent Tool。

## 功能

- 即时起卦：用系统安全随机源模拟每爻三枚铜币，保持 6/7/8/9 的 1:3:3:1 概率。
- 手动起卦：接受六个 6/7/8/9，或六组三枚铜币字符。
- 离线古籍：内置文王卦序 64 卦、64 条卦辞和 384 条基础爻辞；乾、坤另保留用九/用六。
- 意图方向：综合、事业、感情、财富、学业、健康、家庭、出行。
- 群主开关：默认关闭，严格识别 OneBot 的群主角色；群管理员和 AstrBot 管理员不会被自动视作群主。
- Agent 调用：提供 `cast_liuyao` 和 `lookup_zhouyi_text` 两个工具。

## 指令

```text
/问卦 即时 [方向] [问题]
/问卦 手动 <六爻> [方向] [问题]
/问卦 开关 开|关|状态
/问卦 help
```

示例：

```text
/问卦 即时 事业 今年是否适合换工作
/问卦 手动 7 8 9 6 7 8 感情 这段关系该如何推进
/问卦 手动 正反反 正正反 正正正 反反反 正反反 正正反 财富
```

六爻输入顺序固定为初爻到上爻，即自下而上。铜币记法约定为：

- 正 / 字 / 阳 / H = 3
- 反 / 花 / 阴 / T = 2
- 合计 6=老阴、7=少阳、8=少阴、9=老阳

## Agent 用法

用户可以直接说“为我起一卦问事业”。Agent 可调用：

- `cast_liuyao(mode, intent, question, manual_lines)`：即时或手动起卦，返回本卦、动爻、之卦、古籍原文和解读约束。
- `lookup_zhouyi_text(hexagram, line)`：按文王卦序查询卦辞或某一爻原文。

工具只在已启用的 QQ 群内工作。插件不自动调用模型，因此普通指令不会额外产生 LLM 费用；通过 Agent 使用时，由当前 Agent 对工具结果进行解释。

## 安装与配置

把本目录放入 AstrBot 的 `data/plugins/`，在 WebUI 重载插件。最低 AstrBot 版本为 4.9.2。

配置项：

- `default_enabled`：新群默认状态，建议保持 `false`。
- `allow_owner_api_lookup`：原始事件缺少 `sender.role` 时，读取 OneBot 群成员资料核验群主。
- `max_question_length`：问题最大长度。
- `show_disclaimer`：普通指令是否显示传统文化参考提示。

## 数据与扩充

`data/zhouyi.json` 是运行时离线数据；`scripts/build_zhouyi_corpus.py` 只在开发阶段从维基文库公开 API 重建它。意图结构与候选签词在 `data/intents.json`，可继续按“卦序 + 意图”增补。

来源、版本和后续古籍候选见 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)。引用古籍原文时请保留来源说明；不要把现代网络解签内容未经许可复制进数据集。

## 测试

```bash
python -m pytest
python -m ruff check .
```

## 说明

问卦内容用于传统文化体验与自我反思，不应替代医疗、法律、投资或其他专业意见，也不应被表述为必然预言。
