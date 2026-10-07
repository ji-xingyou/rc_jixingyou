# Production Readiness Checklist

当前仓库是可运行 MVP，不应未经加固直接承载生产敏感流量。上线评审至少确认：

## 必须完成

- [ ] API Gateway 或 mTLS 已鉴权，并有 tenant 身份；幂等唯一键改为 tenant 维度。
- [ ] egress proxy / firewall 限定供应商地址；DNS 解析后的所有 IP 经过 SSRF 检查。
- [ ] Authorization 不再明文持久化，改为 Vault/KMS credential reference；数据库磁盘和备份加密。
- [ ] API 与 worker 分离部署；优雅停机时间大于请求超时，租约大于最坏外调时间。
- [ ] 数据库备份、恢复演练、schema migration 和数据保留/删除策略就绪。
- [ ] 指标、dashboard 和告警覆盖 queue depth、oldest age、dead、成功率和延迟。
- [ ] 每供应商并发限制和速率限制已按合同配置，重试不会形成放大攻击。
- [ ] 端到端故障注入覆盖 worker kill、数据库重启、供应商超时和重复投递。

## 需要产品/业务确认

- [ ] 供应商是否支持 `X-Notification-Id` 幂等；若不支持，重复的业务影响是否可接受。
- [ ] 自动重试次数、最长延迟、dead 保留期和人工重放权限。
- [ ] 是否存在同一实体严格顺序要求；若有，定义 ordering key 与乱序补偿方式。
- [ ] 哪些 Header/Body 属于个人信息或支付数据，相应的审计、地域与删除要求。

