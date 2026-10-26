# APK 内部排查与修复边界

本次没有构建或安装 APK。源码参考为 Operit v1.12.2、提交 `dbf71916fae9750cfdc9f9a774f5a0fee56633fb`。手机二进制是否完全匹配该提交，需要构建信息或 APK 校验确认。

已生成的 `daily_life.guarded.js` 和六份 JSON 是调用层缓解；它们不能证明 APK 内多次提交的具体起点已经修复。

建议按以下顺序完善原生工程：

1. 在 `WorkflowWorker.doWork` 记录 WorkSpec ID、attempt、输入中的 occurrence ID、开始/结束/停止及返回类型。不要把 failure、retry、cancelled 合并成一个字符串。
2. `WorkflowRepository.triggerWorkflowInternal` 的“检查运行中并注册”需要原子化；读取运行列表与注册分离存在并发窗口。原子注册只防同时运行，不能替代跨重启的完成账本。
3. 在 `StandardChatManagerTool.startMessageToAIStream` 等待对话空闲之前记录请求 ID 和调用来源；在 `core.sendUserMessage` 前后再记录一次。这样能看见重复调用是提前排队，还是在回合完成后新生成。
4. 把 `occurrence_id = workflow + planned_time + plan_revision` 传到原生消息入库层。该层以事务唯一约束领取，生成一份用户输入和一份 turn；相同 ID 的请求返回原消息 ID/状态，而不是重新发送。原生重试不得更换 occurrence ID。
5. 把消息“已接受、输入已落库、模型回合完成、外部通知已确认”分阶段记账；状态不确定时先对账。不要把“消息已提交但清理被取消”当作必须完整重发的理由。
6. 一次性任务完成先写完成标记，清理交给独立维护路径；审查 `deleteWorkflow → unscheduleWorkflow → cancelUniqueWork` 对自身运行的影响。保留执行记录，避免删除定义时一并抹掉早期证据。
7. 审查 `WorkflowSchedulerInitializer` 启动恢复与 `REPLACE` 的使用；只有配置确实变化时替换计划。`KEEP` 是同名任务冲突策略，不是“关闭失败重试”的开关。

不能采用的修复：吞掉所有协程取消；把所有失败强行返回成功；看到 WAL 中有 UUID 就清库；仅靠 AI 在提示词里识别“这条好像发过”；按文字永久合并所有真实重复表达。

现场验证：同一逻辑触发并发调用、回复过程中进后台/重启、模型已完成但清理取消、提交成功后确认丢失、人工正常重复发送、真正的新计划版本。每种情况都核对 occurrence ID、消息 ID、turn ID 与 Worker attempt。

QQ 停用是独立事项。日志能看到插件运行目标为空，不构成其成功发消息的证明。应停用插件监听/轮询/前台恢复，不能直接删除其他功能共用的发送层或 MCP。

## v5 发送闭环

云端已实现 outbox、回执状态和主动对账，接口与手机接收侧约束见 ../docs/delivery_protocol.md。公开参考的消息工具不提供外部送达 message_id；原生接入仍须在实际 APK 上完成和验证。
