# 发送闭环接口与接收侧要求

## 云端接口

- POST /v2/deliveries：{"operation_id":"wf:plan:revision:time:channel","payload":{"agent_id":"a","conversation_id":"c","text":"提醒"}}。同 ID 相同 payload 复用，变更返回 409。
- GET /v2/deliveries/status?operation_id=...：查询 ready/sending/accepted/delivered/unknown_delivery 与 receipt。
- POST /v2/deliveries/dispatch 或 /reconcile：body={"operation_id":"..."}。未配置网关返回 503；未知结果只对账。

发送前持久领取，提交 token 可防并发调用。accepted 只代表下游接受；delivered 必须包含匹配的 operation_id 和非空 message_id。receipt 超时、格式损坏、错配或进程中断都不能变成成功，也不会直接重新发送。

设置 DELIVERY_GATEWAY_URL / DELIVERY_GATEWAY_TOKEN 后独立维护线程按账本发送和查询，不阻塞分类维护。默认禁用外发。部署后启用该配置就是授权向此网关投递 ready 队列，应先核对队列内容。

## 网关契约

HTTPDeliveryAdapter 使用 HTTPS 和 X-Token：

1. POST {gateway}/deliveries，请求为 operation_id 与原始 payload。返回 {"operation_id":"...","status":"accepted"}，或 {"operation_id":"...","status":"delivered","message_id":"持久消息ID"}。
2. GET {gateway}/deliveries/{URL编码的operation_id}，200 JSON 返回同一持久状态。HTTP 404、超时和空响应都表示无法确认，不能当作“从未收到”。
3. 只有明确返回 {"operation_id":"...","status":"not_found","authoritative":true,"dedupe_supported":true}，才允许云端以原 ID 再投递。该声明必须建立在可靠查询和持久幂等约束上，不能靠短期内存缓存或文字哈希。

接收侧必须在同一事务中唯一领取 operation_id 并关联实际 message_id/turn_id；重复 POST 返回原收据。模型回合结束、通知发送和外部收件方确认是不同阶段，使用实际目标通道能够证明的送达语义。查询和防重账本的保留期要覆盖任务可能重试的整个寿命。

## Operit 现场接入限制

公开 v1.12.2 的 StandardChatManagerTool.sendMessageToAI 结果含 chatId、message、aiResponse、receivedAt，没有外部消息唯一回执。把它的 success=true 直接映射为外部 delivered 会制造假确认。本次没有伪造此能力。

应在实际 APK 消息入库和目标发送通道接入上述操作 ID 与持久回执，再部署兼容网关；现有原生修改清单见 operit/NATIVE_FIX_GUIDE.md。云端协议、维护逻辑、断线对账和并发领取已通过离线测试，手机原生适配与真实收件方确认仍待提供实际构建/运行环境后验证。

现场用例：并发重复计划、发送后响应丢失、模型仍忙、网关重启、手机重启、接受后未完成、查询不可用、权威未找到、正常新计划和相同文字的新消息。逐项核对 operation_id、message_id、turn_id 与收件端账本。
