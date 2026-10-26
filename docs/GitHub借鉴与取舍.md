# GitHub 实现比较与采用决定

核对日期：2026-10-18。先检索，再通过 GitHub API 读取固定提交中的代码与许可证。完整提交号见 `github_sources.json`。本次没有第三方源码复制，没有新增第三方运行依赖，也没有下载模型。下面是根据实际接入条件作出的工程选择，不是六个项目的实测排行榜。

| 项目与固定提交 | 核对到的实现 | 本版采用与取舍 |
| --- | --- | --- |
| [MaiBot](https://github.com/Mai-with-u/MaiBot/tree/ac6ed61b7b9c971ad94921de3b16fa6f63c36a20)，GPL-3.0 | 表达记录区分情境与说法；replyer 按对话选择表达，也允许空选择；学习器排除自己的发言和提示示例泄漏 | 采用情境化的可选习惯、允许不用、抑制示例污染；未移植群聊学习、向量数据库及其整套框架 |
| [SillyTavern](https://github.com/SillyTavern/SillyTavern/tree/06bde939fb1e9c4c8d8641d810f0a916b5bce127)，AGPL-3.0 | 角色信息、示例对话与历史分开组织；提供多供应商消息转换 | 采用稳定角色与动态提示分层；不替换 Operit；云端实测发现直接示例会被照抄，因此最终只编译示例的句子节奏 |
| [AIRI](https://github.com/moeru-ai/airi/tree/266e8d9467197f9562279d6291eddae758b11fe2)，MIT | `use-airi-runtime-prompt.ts` 为请求组装动态情绪及表情提示 | 采用每轮追加动态表达上下文；没有搬入 Live2D、语音、桌面运行时或把表情标签当心理模型 |
| [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/tree/992309c0aa19845960228f880013d4685fde93b5)，MIT | 可插入 concise/speakable 风格提示 | 采用表达指导独立组装；不采用其频繁追问要求，也不把文本代码、数字一律改成口语读法 |
| [persona-engine](https://github.com/caty-ai/persona-engine/tree/da423d77b83c2780662d107b84380694dfa1c1e9)，MIT | 模式与词汇材料编译、有预算、路由与状态域边界 | 采用有界提示与明确角色/会话绑定；不引入整套 TS 模式切换和权限系统，不把熟悉度当切换亲密身份的许可 |
| [emotion-engine](https://github.com/pioneerjeff-labs/emotion-engine/tree/e7fab5c3984a4885ac74c972d573b05b45a8c496)，MIT | 持久情绪连续性与给 LLM 的提示预览分开；预览声明不是实际回复 | 采用状态指导表达的分层；保留本项目已有多事实归因、混合情绪和分维度关系，避免另添相互冲突的状态引擎 |

主要源码入口：

- [MaiBot 表达选择器](https://github.com/Mai-with-u/MaiBot/blob/ac6ed61b7b9c971ad94921de3b16fa6f63c36a20/src/chat/replyer/maisaka_expression_selector.py)、[示例污染过滤](https://github.com/Mai-with-u/MaiBot/blob/ac6ed61b7b9c971ad94921de3b16fa6f63c36a20/src/learners/expression_style_utils.py)。
- [AIRI 动态提示](https://github.com/moeru-ai/airi/blob/266e8d9467197f9562279d6291eddae758b11fe2/packages/stage-ui/src/composables/use-airi-runtime-prompt.ts)。
- [VTuber 简短风格提示](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/blob/992309c0aa19845960228f880013d4685fde93b5/prompts/utils/concise_style_prompt.txt)。
- [persona-engine 提示预算](https://github.com/caty-ai/persona-engine/blob/da423d77b83c2780662d107b84380694dfa1c1e9/packages/core/src/compile/budget.ts)。
- [emotion-engine 提示预览](https://github.com/pioneerjeff-labs/emotion-engine/blob/e7fab5c3984a4885ac74c972d573b05b45a8c496/scripts/prompt_preview.py)。

为什么没有整套照搬：本项目已具备事件去重、时间衰减、对象归因、关系证据和云端语义调用。此次缺口是表达编译与生成前接入。直接接入另一个完整机器人会同时更换这些接口，却没有证据证明表达更好。按接口重写适配层更便于保留已有验证；若未来具体组件的对照实测更好且接口适合，再在对应许可证下引入。当前不能宣称这个组合已经是所有方案中效果最好。
