# Reliable Notification Service

一个面向企业内部业务系统的 HTTP(S) 通知投递服务 MVP。业务方把目标地址、Header 和 JSON Body 提交给本服务；服务在请求返回前先持久化任务，再异步投递，并对暂时性失败自动重试。

> 核心承诺：**已返回 `202 Accepted` 的任务不会只存在于内存中；系统采用至少一次投递，允许重复，不承诺恰好一次。** 接收方应使用 `X-Notification-Id` 做幂等处理。

## 1. 快速开始

要求 Python 3.11+，运行时无第三方依赖。

```bash
make test
make run
```

默认监听 `127.0.0.1:8080`，数据写入 `notifications.db`。提交一条通知：

```bash
curl -i http://127.0.0.1:8080/v1/notifications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: order-paid-20261005-42' \
  -d '{
    "target_url": "https://example.com/webhooks/order",
    "method": "POST",
    "headers": {"Authorization": "Bearer demo", "X-Tenant": "acme"},
    "body": {"event": "order.paid", "order_id": "42"}
  }'
```

查询状态、投递历史与重放死信：

```bash
curl http://127.0.0.1:8080/v1/notifications/{id}
curl http://127.0.0.1:8080/v1/notifications/{id}/attempts
curl -X POST http://127.0.0.1:8080/v1/notifications/{id}/retry
```

也可以运行 `docker compose up --build`，Compose 中包含一个本地 webhook sink。生产部署应把 API 与 worker 分开启动：`notification-service api` 和 `notification-service worker`。

## 2. 问题理解与系统边界

### 2.1 要解决的问题

- 接收不同业务系统提交的 HTTP(S) 通知描述，并在确认接收前落盘。
- 将业务请求与外部供应商的延迟、抖动和故障解耦。
- 保存投递状态及每次尝试记录；进程崩溃后可继续处理。
- 区分永久失败与暂时失败，对后者指数退避重试。
- 提供业务侧提交幂等、状态查询、死信查询依据与人工重放入口。
- 支持不同 URL、`POST/PUT/PATCH`、自定义 Header，以及 JSON 或原始 UTF-8 Body（如 XML、表单文本）。

### 2.2 明确不解决的问题

| 不在 v1 范围 | 原因 / 当前做法 |
|---|---|
| 恰好一次投递 | HTTP 调用成功后、状态落库前崩溃时无法判断对端是否收到；在不控制接收方事务的前提下不可实现。采用至少一次并传递稳定通知 ID。 |
| 供应商字段映射和业务编排 | 本服务只负责可靠传输，不理解“订单”“联系人”等业务语义，避免演变为集成平台。由调用方形成最终请求。 |
| 通知顺序 | 全局或目标维度严格有序会显著降低吞吐并造成队头阻塞。需要时可增加 `ordering_key` 与分区串行消费。 |
| OAuth 刷新、签名算法插件 | 供应商差异大且涉及密钥生命周期。MVP 接受调用方准备好的 Header；生产版应改为凭据引用和受控签名插件。 |
| 超大/非 JSON 文件 | 当前最大序列化 Body 为 256 KiB。文件通知应使用对象存储 URL，避免数据库膨胀。 |
| 管理 UI、多租户计费、审批流 | 不影响核心可靠性，可由后续控制面补充。MVP 提供 API 和结构化状态。 |

## 3. 架构与模块职责

```text
Business System
      |
      | POST /v1/notifications + Idempotency-Key
      v
+-------------+      transaction       +------------------+
|  HTTP API   | ----------------------> | SQLite task store|
+-------------+       202 after commit  +------------------+
                                               ^  |
                             lease/complete    |  | claim due task
                                               |  v
                                         +-------------+
                                         |   Worker    |
                                         +-------------+
                                                |
                              timeout, retry    | HTTP(S)
                                                v
                                        External Vendor
```

代码按职责拆分：

- `api.py`：HTTP 协议、路由、输入大小与错误响应；不包含投递逻辑。
- `service.py`：用例编排、URL/Header/Body 校验和接收幂等。
- `repository.py`：事务、任务租约、状态机及尝试历史。
- `delivery.py`：单次 HTTP 投递、超时与响应分类，不做重试调度。
- `worker.py`：领取任务、退避计算、完成状态写回。
- `domain.py`：领域类型；`config.py`：集中配置；`db.py`：Schema 与连接策略。

这种拆分使 HTTP 客户端、SQLite 或调度器可以独立替换，也能用 fake client 测试失败路径。

## 4. 可靠性设计

### 4.1 投递语义与状态机

语义为 **at-least-once（至少一次）**：

```text
               claim                  2xx
  pending ----------------> in_flight -----> succeeded
     ^                         |
     | retryable + attempts    | non-retryable / exhausted
     | remain                  v
     +----------------------  dead -- manual retry --> pending

  in_flight -- lease expired after crash --> pending
```

返回 `202` 前在同一 SQLite 事务中写入完整任务。worker 使用 `BEGIN IMMEDIATE` 原子领取一条到期任务并写入租约和唯一 fencing token；其他 worker 不能同时领取。进程在租约期间崩溃，租约过期后任务重新进入 `pending`。完成更新必须携带相同 token，因此迟到的旧 worker 不能覆盖新 worker 的处理结果。

最关键的故障窗口是：供应商已处理成功，但 worker 在写入 `succeeded` 前崩溃。任务会再次投递，因此：

- 每次请求都携带稳定的 `X-Notification-Id`；
- 推荐供应商以该 ID 幂等；
- `X-Notification-Attempt` 只用于观测，不能作为幂等键。

调用方重试提交时应传 `Idempotency-Key`。相同 key 返回原任务（首次 `202`，重复提交 `200`），不会创建两条队列记录。MVP 将 key 视为全局唯一；多租户版应以 `(tenant_id, key)` 唯一。

### 4.2 失败分类

| 结果 | 处理 |
|---|---|
| HTTP 2xx | 成功，不再重试 |
| 网络错误、连接/读取超时 | 暂时失败，重试 |
| 408、425、429 | 暂时失败，重试；识别 `Retry-After` |
| 5xx | 暂时失败，重试 |
| 其他 3xx/4xx | 永久失败，进入 dead（重定向不会自动跟随） |

重试采用带稳定抖动的指数退避：`min(cap, base * 2^(attempt-1)) * [0.8, 1.2]`，默认最多 8 次、最大间隔 1 小时。稳定抖动便于复现测试，同时避免恢复时形成惊群。`Retry-After` 与本地退避取较大值并受最大间隔限制。

长期不可用时，达到次数上限进入 `dead`，保留最后错误和完整尝试历史，不再自动消耗资源。运维修复目标或确认供应商恢复后调用 `/retry`，它会清零重试计数并重新入队。生产环境应对 dead 增量、队列最老任务年龄和投递成功率告警。

### 4.3 一致性与持久性

- SQLite 开启 WAL、外键、`synchronous=FULL`，单机断电语义优于默认配置。
- 接收与 worker 竞争写入时使用短事务；实际外部 HTTP 调用绝不持有数据库事务。
- 每次尝试与最终状态在同一事务写回。
- 租约必须大于请求超时。默认 30 秒对 10 秒超时留有余量；修改配置时需保持该不变量。
- 当前领取策略按 `next_attempt_at, created_at`，避免重试任务永久饥饿。

## 5. API 约定

### `POST /v1/notifications`

Header：可选 `Idempotency-Key`（1-128 字符）。Body：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `target_url` | string | 是 | 仅 HTTP(S)，禁止 URL 内嵌用户名/密码 |
| `method` | string | 否 | `POST`（默认）、`PUT`、`PATCH` |
| `headers` | object<string,string> | 否 | 禁止 Host、Content-Length 等传输控制 Header |
| `body` | 任意 JSON | 否 | 按紧凑 JSON 序列化；默认 Content-Type 为 `application/json` |
| `raw_body` | string | 否 | 原始 UTF-8 文本；默认 Content-Type 为 `text/plain` |

`body` 与 `raw_body` 互斥，序列化后最大 256 KiB。XML 或 form body 可使用 `raw_body` 并显式设置对应 `Content-Type`。MVP 不接收二进制；应改用对象存储引用。

`202` 表示已持久化，不表示外部供应商已收到。响应中的 `status` 初始为 `pending`。

### 查询与运维

- `GET /v1/notifications/{id}`：状态和最后失败摘要，不返回凭据或 Body。
- `GET /v1/notifications/{id}/attempts`：投递尝试历史。
- `POST /v1/notifications/{id}/retry`：只允许重放 `dead` 任务。
- `GET /health/live`、`GET /health/ready`：存活与数据库就绪检查。

统一错误结构为 `{"error":{"code":"...","message":"..."}}`。

## 6. 安全、隐私与可观测性

- **SSRF**：强烈建议设置 `NOTIFY_ALLOWED_HOSTS=api.vendor-a.com,api.vendor-b.com`。未配置 allowlist 时仍拒绝直接填写私网、回环、链路本地及保留 IP；MVP 尚未解决 DNS rebinding，生产版应在解析后校验全部 IP，并在受限 egress proxy 再做网络策略。
- **凭据**：日志和查询 API 均不输出 Header/Body，但 SQLite 中为完成重试必须保存原值。磁盘需加密并限制文件权限。下一版将 `Authorization` 替换为 Vault/KMS 中的 `credential_ref`，投递时短暂解析。
- **入口鉴权**：MVP 绑定 loopback；若暴露到网络，必须由 API Gateway/mTLS 做服务身份认证、租户隔离、速率限制和审计。不能把当前无鉴权端口直接暴露公网。
- **响应处理**：只读取最多 4 KiB 后丢弃，不持久化供应商响应 Body，避免敏感数据和无界内存使用。
- **建议指标**：`accepted_total`、`delivery_attempt_total{outcome,status_class}`、`queue_depth{status}`、`oldest_pending_age`、`delivery_latency`、`dead_total`。日志以通知 ID 关联，不记录敏感 payload。

## 7. 工程决策与取舍

### 为什么 v1 选择 SQLite 队列

这是单服务 MVP，SQLite 提供事务、唯一约束、崩溃恢复和零额外运维，足以清晰验证可靠性模型。相比“API 写库 + 再发 MQ”的双写，当前路径只有一次本地事务，不会出现数据库成功但消息未发出的缝隙。租约模型也允许同一主机多个 worker 并发。

代价是单写者限制、缺少原生消息保留/分区/消费组，且共享网络文件系统不适合 SQLite。无 SQLite 时的轻量替代是 PostgreSQL 配合 `SELECT ... FOR UPDATE SKIP LOCKED`；已有消息平台时则采用 transactional outbox + Kafka/SQS/RabbitMQ relay，而不是在请求内直接双写。

### 为什么不在请求线程直接调用供应商

同步调用会把供应商延迟和故障传播给核心业务，业务超时重试又会制造更多重复。先持久化再 `202` 明确切断故障传播，也使重试策略由一个地方治理。

### 为什么不承诺“收到即最终送达”

目标可能永久消失、请求本身可能无效、凭据可能过期。无限重试会积累无效任务并持续攻击供应商。系统承诺可审计的尽力可靠投递：有界自动重试、死信保留、告警和人工重放。

## 8. 演进路线

按触发条件演进，而不是预先堆组件：

1. **单机容量或 HA 成为瓶颈**：SQLite 换 PostgreSQL；API 多副本，worker 用 `SKIP LOCKED` 横向扩展。引入迁移工具、备份恢复演练和任务保留/归档策略。
2. **写入峰值、跨地域或消费隔离明显**：采用 PostgreSQL transactional outbox，将事件可靠转发到 Kafka/SQS；按 tenant/vendor 分区，worker 消费组扩展。数据库仍作为查询投影，避免 MQ 兼任查询系统。
3. **供应商策略复杂**：按供应商配置并发上限、token bucket、熔断器、独立超时与重试预算，避免一个坏目标占满 worker。加入 `credential_ref` 和签名插件。
4. **业务需要顺序/撤回/优先级**：明确语义后再增加 `ordering_key`、优先级队列和取消状态；每项都会改变状态机，需独立 ADR 和并发测试。
5. **运营规模扩大**：控制面 UI、RBAC、批量重放、payload 脱敏查看、OpenTelemetry trace 和 SLO burn-rate 告警。

容量判断应基于到达率、供应商 p95 延迟、失败率和可接受积压恢复时间。例如平均外调 200ms 时单 worker 理论约 5 req/s；实际应为失败重试和抖动留出至少 2-3 倍余量。

## 9. 测试策略

`make test` 使用标准库 `unittest`，覆盖：

- 接收持久化和 `Idempotency-Key` 去重；
- host allowlist 与危险 Header 校验；
- 任务领取互斥；
- 2xx 成功、4xx 永久失败、5xx 重试调度。

当前测试聚焦 MVP 的状态机和决策分支。生产门槛还应加入：真实 HTTP stub 的超时/断连/429/Retry-After 契约测试，多 worker 竞争与进程 kill 的故障注入，SQLite 损坏恢复、负载和 soak test，以及 API 模糊测试。

## 10. 配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `NOTIFY_DB_PATH` | `notifications.db` | SQLite 文件 |
| `NOTIFY_BIND_HOST` / `NOTIFY_PORT` | `127.0.0.1` / `8080` | API 监听地址 |
| `NOTIFY_WORKERS` | `2` | worker 线程数 |
| `NOTIFY_REQUEST_TIMEOUT_SECONDS` | `10` | 单次外调超时 |
| `NOTIFY_LEASE_SECONDS` | `30` | worker 租约时长 |
| `NOTIFY_MAX_ATTEMPTS` | `8` | 自动尝试上限 |
| `NOTIFY_BASE_BACKOFF_SECONDS` | `2` | 首次退避基数 |
| `NOTIFY_MAX_BACKOFF_SECONDS` | `3600` | 单次最大退避 |
| `NOTIFY_MAX_BODY_BYTES` | `262144` | 序列化通知 Body 上限 |
| `NOTIFY_ALLOWED_HOSTS` | 空 | 逗号分隔目标 hostname allowlist |

从原始作业拆分的可追踪需求见 [spec/requirements.md](spec/requirements.md)，AI Agent 协作约定见 [AGENTS.md](AGENTS.md)。更完整的 AI 协作记录见 [docs/AI_USAGE.md](docs/AI_USAGE.md)，生产风险清单见 [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md)。
