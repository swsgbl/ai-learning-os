# NOTICE — 第三方组件与许可证声明

本项目（AI Learning OS）主体以 [MIT License](LICENSE) 提供，但**部分组件的
依赖具有更强许可证条款**，分发或网络服务运营时须一并遵守。逐项如下。

## 许可证组合结构

| 范围 | 许可证 | 说明 |
|---|---|---|
| `apps/android/`、`apps/harmony/`、`apps/web/`、`services/console/`、文档 | MIT | 客户端与控制面，无传染性依赖 |
| `services/api/` | **AGPL-3.0 组合义务** | 进程内链接 PyMuPDF（AGPL-3.0）——见下节 |

### services/api 的 AGPL-3.0 传染说明

`services/api` 使用 [PyMuPDF](https://pymupdf.readthedocs.io/)（AGPL-3.0）
解析 PDF 教材。AGPL-3.0 第 13 条要求：**通过网络向用户提供本服务时，
必须向该用户提供本服务完整对应源码**（含你的修改）。

因此：

- 你自行部署 `services/api` 并向公网提供服务时，你的部署所对应的
  `services/api` 全部源码（含修改）须按 AGPL-3.0 向使用者提供；
- 仅私有本地使用（不向第三方提供网络服务）不触发网络服务条款；
- 如需规避 AGPL 义务，可向 Artifex 购买 PyMuPDF 商业许可后自行移除
  本声明中的 AGPL 组合义务，或将 PyMuPDF 依赖替换为其他许可的解析器。

本项目仓库本身即完整开源，满足 AGPL 对应源码要求；本声明旨在让
下游部署者知悉其自身义务。

## 主要第三方组件

### services/api（Python）

| 组件 | 许可证 | 用途 |
|---|---|---|
| PyMuPDF | **AGPL-3.0** | PDF 教材解析（页锚点/outline） |
| py-fsrs | MIT | FSRS 间隔重复调度（Murray 2025） |
| rapidocr-onnxruntime | Apache-2.0 | 扫描版教材 OCR |
| onnxruntime | MIT | OCR 推理运行时 |
| FastAPI / Starlette / Pydantic | MIT | Web 框架与数据校验 |
| SQLAlchemy | MIT | 数据访问 |
| Alembic | MIT | 数据库迁移 |
| httpx | BSD-3 | 出网 HTTP（LLM 网关/探测） |
| funasr（可选，本地部署） | MIT | 语音识别 |
| CosyVoice（可选，本地部署） | Apache-2.0 | 语音合成 |

### 客户端与构建

| 组件 | 许可证 | 用途 |
|---|---|---|
| React / Vite / TypeScript（`apps/web`） | MIT | Web 前端 |
| Kotlin / Jetpack Compose（`apps/android`） | Apache-2.0（AndroidX） | Android 客户端 |
| ArkTS / ArkUI（`apps/harmony`） | — （HarmonyOS SDK 自带条款） | 深开鸿客户端 |

### 数据与内容

- **ChinaTextbook 目录**：仅收录**元数据与官方外链**，不托管任何教材
  PDF 原文（Mode A）；私有导入（Mode B）要求导入者声明使用权，分片
  加密存于其私有空间，平台不向第三方分发。
- 学习科学基准数据（检索练习效应 g≈0.5-0.7，Adesope et al. 2017；
  间隔效应 g≈0.28）引用自公开文献，详见 `docs/LEARNING_EFFECT_AUDIT.md`。

完整依赖树许可清单可随时用以下命令生成复核：

```powershell
pip-licenses   # services/api
npm ls --json  # 各前端包
```

## 商标与名称

「AI Learning OS」「砚席」为项目自用名称，未注册商标；本项目与
Anthropic、OpenAI、华为、Google 等任何模型/设备厂商无从属关系，
所涉厂商名称与产品名归其各自权利人。
