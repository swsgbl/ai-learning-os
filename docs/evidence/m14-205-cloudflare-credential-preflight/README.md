# M14-205：Cloudflare ingress 凭据/zone 只读 preflight 工具（实现 + 测试 + docs；零真实云请求）

> **rework round 1（supervisor 审计修正）**：Codex 审计发现 RealTransport
> 的 HTTP 2xx + 畸形 JSON 路径会以 `json.JSONDecodeError` 裸逃逸（不在
> 任何 except 契约内），违反 fail-closed JSON 报告承诺。修正：`_parse_json_body`
> 将不可解码/非 JSON 一律归一 `body=None`（HTTP 状态保留、零重试零额外
> 请求）；同族缺陷 `_result_object` 返回 `None` 被编排层 `.get()` 解引用
> （HTTPError 空 body 路径原本潜伏）一并修正为恒返回 dict（malformed →
> 空 dict 走既有 FAIL 分类）。新增 4 项边界回归测试（真实 RealTransport +
> urllib opener 打补丁，零外网）钉住：2xx 畸形归一 None、2xx 合法 JSON
> 控制组、端到端 4 检查全 FAIL 无逃逸（恰 2 次真实 open——ssl/dns 为
> skipped 不发请求）、main 级产出 fail JSON（exit 1）。详见 §2/§3/§4。

- 切片：worktree `ai-learning-os-worktrees/m14-205-cloudflare-credential-preflight`，
  分支 `ops/m14-205-cloudflare-credential-preflight`，基于本地 `f9ddec3`
  （M14-204；其 tree `46973f6` 与远端 main merge `6404a91` 一致，由
  supervisor 核验，本切片未 fetch）。
- 定性：纯实现切片——交付 `tools/ops/cloudflare_ingress_preflight.py`
  （fail-closed、value-free、默认零网络）与其契约测试，解决 M14-203
  E1 执行前"凭据与 zone 可用性无法安全验证"的缺口，为后续受控 E1
  DNS 变更提供安全前置。**本切片零真实 Cloudflare API 请求、零 DNS
  变更、零生产 manifest 请求、零凭据接触**；全部 HTTP 行为在测试
  FakeTransport 注入点伪造（零网络零子进程）。

## 1. 零执行声明与本地环境事实（如实）

- 本切片未调用任何真实 Cloudflare API：本机当前 shell 环境无
  `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_EMAIL`/`AIOS_CLOUDFLARE_CONFIG`
  等任何凭据环境变量，无 `~/.wrangler`/`~/.cloudflared` 配置（未
  安装/未登录），因此不存在"顺手验证真 token"的路径，也**未执行**
  真实验证——工具的真实 execute 属后续 supervisor 批准切片。
- 全部 225 项 tests/ops 测试零网络零子进程：execute 路径经
  `transport_factory` 注入点注入 `FakeTransport`（按脚本逐次应答，
  超出脚本即抛错=零重试结构证明）；plan/blocked 路径注入"调用即
  抛错"哨兵工厂，钉死"plan 零 Transport"。
- 本切片不触碰 DNS、VPS、Nginx、Docker、生产服务、Cloudflare
  控制台、手机、模拟器、Harmony/Android 工程、CC Switch、代理。

## 2. 工具契约（tools/ops/cloudflare_ingress_preflight.py）

- **配置输入**：仅显式 `--config <path>` 或 `AIOS_CLOUDFLARE_CONFIG`
  环境变量（CLI 优先）；两者皆无 → `blocked/missing-config` exit 2，
  绝不发明默认凭据路径。配置文件必须常规文件（lstat 拒绝
  symlink/reparse point/目录/设备）、大小硬顶 8 KiB（stat 判定后
  限额读取双保险）、严格 JSON（任何层级重复 key 拒绝）、schema
  固定：`schema_version=1` + `zone_name` + `api_token` 必填，可选
  `execute_timeout_s`（1–60s）与 `user_agent`（≤200 字符），未知
  字段拒绝。
- **名称校验**：`zone_name` 仅公网 DNS 名称形态（≥2 个合法 label、
  总长 ≤253、首尾字母数字+连字符、禁 IP 字面量 v4/v6、禁
  scheme/userinfo/path/query、禁 RFC 2606/6761 保留域及其子孙域）。
  `api_token` 非空校验，但值绝不进入任何输出/日志/异常文本（报告
  只记 `api_token_present` 布尔）。
- **plan 模式（默认）**：解析与本地校验配置，输出
  `config_present`/`config_source`/`zone_name`/`zone_name_shape`/
  `api_token_present` 与 `ready-to-execute`（exit 0）或 `blocked`
  + 原因码（exit 2）；**不构造 Transport、不发任何请求**。
- **execute 门**：必须同时 `--execute` + `--confirm` 精确短语
  `EXECUTE CLOUDFLARE INGRESS READONLY PREFLIGHT` + 可用配置；缺一
  即 exit 2 且零网络。
- **execute 序列**（恰好 4 个只读 GET、单次、零重试、固定超时）：
  1. `GET /user/tokens/verify`——要求 `result.status=active`；
  2. `GET /zones?name=<zone_name>`——要求 result 恰 1 条并记
     `zone_found`；zone id 只用于后续请求路径，**绝不进入输出**；
  3. `GET /zones/{id}/settings/ssl`——记录 `ssl_mode`（API 值
     `strict` 归一显示 `full_strict`，M14-204 V5a 事实；白名单
     off/flexible/full/full_strict 之外记 `unknown` 且 FAIL；读取
     失败记 `unknown` 且 FAIL）；
  4. `GET /zones/{id}/dns_records?per_page=100`——只统计记录数
     （优先 `result_info.total_count`）与 candidate hostname
     （`--hostname` 显式传入，默认不指定）是否存在于首页；
     **绝不输出任何 record content/IP/target**。zone 查找失败时
     3/4 显式记 skipped 且 FAIL，绝不静默跳过。
- **方法白名单三层**：编排层结构上只发 GET；RealTransport 收到非
  GET 直接拒绝（不存在任何写路径）；测试 FakeTransport 逐请求断言
  method==GET 且 path 匹配四个白名单形态、query 恰为预期
  （`/zones` 必带 `name=`、dns_records 必带 `per_page=100`、其余无
  query）。
- **malformed 2xx 归一（rework round 1）**：HTTP 2xx + 畸形/不可解码
  JSON 统一归一为 `TransportResponse(status=<实际状态>, body=None)`，
  编排层按既有 malformed 分类落 FAIL（token 非 active / zone 不存在 /
  ssl unknown / dns 应答畸形）——绝不以裸异常逃逸，HTTP 状态不丢，
  零重试零额外网络调用；`_result_object` 恒返回 dict（malformed →
  空 dict），编排层零 `None` 解引用风险。
- **输出纪律**：确定性 JSON（sort_keys、无时间戳——两次运行输出
  逐字节一致由测试钉住）、无 token、无 zone/account id、无 record
  value、无本机绝对路径；`requests_made` 只含 endpoint 标签（不含
  zone id）；HTTP 错误只给 `http_status` 与 Cloudflare error code
  数字列表（`api_error_codes`），绝不回显 response body 或 error
  message。
- **代理语义（有意差异，docstring 声明）**：RealTransport 走
  urllib 默认 opener（跟随执行环境标准代理 env）——验收对象是
  Cloudflare API 凭据而非本机网络路径，与 public_edge_preflight
  的"直连验收边缘"契约不同且为此差异显式记录。
- 退出码：0 = 全部通过（plan=ready-to-execute）；1 = 任一检查
  FAIL；2 = blocked（配置缺失/不可读/schema 非法/确认缺失；argparse
  参数错误沿用默认码 2）。

## 3. 测试覆盖（tests/ops/test_cloudflare_ingress_preflight.py，65 项）

- 名称校验纯函数：16 组非法输入（IPv4/IPv6/保留域及子孙域/单
  label/下划线/前导连字符/scheme/path/query/userinfo/超长/空/非
  字符串）+ 合法归一化（大写/尾点 → 小写无点）；
- 配置 schema：缺字段/错版本/空 token/未知字段/类型错/可选字段
  越界（`execute_timeout_s=0.5`、`user_agent=""`）/zone_name 为 IP
  或保留域（reason 归类断言）；可选字段合法通过；
- 路径安全：非 JSON、重复 key（手工构造双 `zone_name`）、超 8 KiB
  （padding 撑大）、目录当配置、symlink（平台无权限则 skip——
  Windows 需开发者模式，诚实跳过）；
- 确认门（全部零网络，哨兵工厂证明不构造 Transport）：`--execute`
  无 confirm/错短语/小写短语 → exit 2；有短语无配置 → exit 2；
  配置非法（zone-name-invalid）→ exit 2；`--hostname 1.2.3.4` →
  argparse exit 2（SystemExit 断言）；
- FakeTransport execute：全过（含 `strict→full_strict` 归一、
  `requests_made` 四标签、恰好 4 次调用=零重试行为证据）；token
  inactive（FAIL 但仍完成 4 检查、无重试）；zones 0 条（ssl/dns
  skipped+FAIL、仅 2 次调用）；zones 2 条（ambiguous FAIL）；SSL
  HTTP 403（`http_status`+`api_error_codes=[9109]`，message 不进
  输出）；SSL 未知值（`unknown`+FAIL）；off/flexible/full 已知值
  全过；DNS transport error（FAIL 分类）；首个请求 transport
  error（`failure_class=transport-error`）；candidate hostname
  存在/不存在；`total_count=250` 优先于首页扫描数；
- 输出脱敏：以可识别标记串（token/zone id/record content/account
  id/错误 message）断言绝不出现于 stdout JSON 与 `--output` 文件；
  blocked 输出同样脱敏；确定性（两次 plan 输出逐字节一致、无任何
  时间键）；
- 单元：RealTransport 拒绝 POST/DELETE；`load_config` 往返；
- **rework round 1 边界回归（真实 RealTransport，monkeypatch
  `build_opener` 返回假 opener/假 response，零外网零子进程）**：
  2xx + 截断 JSON 归一 `status=200, body=None`（单次 open=零重试）；
  2xx 合法 JSON 控制组仍正常解析；端到端 4 请求全 200+畸形 → 4 检查
  全 FAIL 且无异常逃逸（真实 open 恰 2 次——ssl/dns 为 skipped 不发
  请求的既定契约）；main 级 execute 产出承诺的 fail JSON 报告
  （exit 1，非 traceback）。

## 4. 验证结果（本切片实际执行；rework round 1 后为第二轮全量）

| 检查 | 结果 |
| --- | --- |
| `pytest tests/ops/test_cloudflare_ingress_preflight.py -q` | 65 通过（61 基线 + 4 rework 边界回归） |
| `pytest tests/ops -q`（含既有 164） | 229 通过 |
| `pytest services/api/tests/test_versioning_rollback.py -q` | 9 通过（用主仓库 venv python——worktree 离线 uv 环境无 sqlalchemy，环境差异如实记录；代码为 worktree 版本） |
| `ruff check`（新工具+新测试） | 通过 |
| `ruff format --check` | 应用 format 后通过 |
| `py_compile` | 通过 |
| `--help` / 无配置 plan 冒烟 | usage 正常；blocked/missing-config exit 2 |
| `git diff --check` | 干净 |
| 新增行泄漏扫描（U+FFFD/绝对路径/secret 形态/IPv4/账号别名） | 0 真实命中 |

## 5. 对 E1 的影响与后续（不授权）

- 本工具补齐 M14-203 §7 门禁表中"Cloudflare 账号/zone 操作"的前置
  验证能力：E1 执行切片获批后，可先以 plan（零网络）校验配置形态，
  再以 execute（4 个只读 GET）验证 token/zone/SSL/DNS 面只读可达，
  然后才进入任何 DNS 变更讨论。**本切片本身不构成 E1 授权**：G0
  （supervisor 批准执行切片与测量预算）不变，DNS/Cloudflare 变更
  门禁表不变。
- 真实 execute 的前置（未来切片）：supervisor 提供 scoped API token
  配置（建议最小权限 Zone.Zone/Zone.DNS 的 read + User API Token
  verify 所需权限）、执行环境网络路径（直连或标准代理 env）；
  execute 输出落 `.verify/`（gitignored），只摘录结论入库。
- 与 M14-204 结论的衔接：`ssl_mode` 记录值与 E1 目标态（Full
  strict）比对属 E1 执行切片判定，本工具只如实记录现状。

## 6. 诚实边界

1. 未执行真实 API（§1 环境事实）；工具的真实行为（超时/限速/
   代理路径）只能由未来授权切片回答，FakeTransport 覆盖的是契约
   不是网络现实。
2. symlink 拒绝测试在无开发者模式的 Windows 上 skip（平台限制，
   非工具缺口——lstat 判定逻辑本身由目录/超大等用例覆盖）。
3. DNS candidate 扫描仅首页（per_page=100）：记录数 >100 时
   candidate 结论限定首页范围，报告以 `dns_record_count`（全量）
   与 `dns_records_scanned`（首页数）区分，不冒充全量扫描。
4. 本切片 docs + 实现 + 测试；rework round 1 为同分支第二个 local
   commit，不推送、不开 PR；supervisor 审查与 remote 发布（PR 开合/
   合并/release 门禁）在其后进行。
5. rework round 1 残余边界（如实）：畸形 zone 应答（HTTP 200 + 非
   JSON body）下 zone-lookup 的 detail 文案归为"zone 不存在（0
   条）"——fail-closed 判定与退出码正确，但诊断文案未区分"0 条"
   与"应答畸形"（分类粒度有限，不影响门判定，如需区分属未来增强）；
   2xx 畸形回归以截断 JSON 为代表形态，未穷尽全部畸形字节序列。
