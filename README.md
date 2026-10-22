# 需求看板 AI 助手

> 研发团队的需求全流程管理看板：多状态流转、AI 分类打标、相似需求检索，以及一个能调用工具、可多轮对话的需求检索智能体。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.34%2B-FF4B4B?logo=streamlit&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-4169E1?logo=postgresql&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

面向研发团队的需求全流程管理看板。从需求提出、评审、排期、执行到验收，配合 AI 完成信息抽取、分类打标、相似需求检索、周报生成等重复性工作。

内置的对话式检索助手可以直接用自然语言提问，例如「这个月还有哪些 P1 需求卡在评审」「员工06 手上没做完的几条分别是什么」，它会自行决定查什么、查完再答，并且**回答里引用的需求编号都会和真实数据核对**。

> 本仓库为脱敏示例版：人员、客户、配置与业务数据均为虚构或占位内容，可直接替换后使用。

## 功能特性

### 需求全流程

- **7 种状态**：待确认 / 需求池 / 待启动 / 进行中 / 暂停 / 已完成 / 已终止
- **来源按提出部门归类**：总经办 / 售前 / 销售 / 客服 / 研发 / 工程 / 生产 / 研究院 / 其他
- **优先级与标签**：P0–P3 优先级；设备类标签 + 技术域标签 + 类型标签三段式打标
- **书面留痕**：佐证材料必填（邮件 / 聊天截图 / 技术文档 / 会议纪要 / 电子表格 / 文件 / 其他）
- **责任人分层**：建议责任人与实际责任人分开记录
- **角色分工**：管理层 / 主管 / 工程师 / 项目经理 / 售前 / 销售 / 实习生 / 需求方

### 视图

| 视图 | 说明 |
|---|---|
| 我的看板 | 按角色与身份呈现本人相关的需求与待办 |
| 项目视图 | 按大项目分组汇总，未关联的需求可按标题自动归类 |
| 周报 | 按周汇总进展与阻塞项 |
| 中心总览 | 全局指标与分布 |

### AI 能力

| 编号 | 能力 | 说明 |
|---|---|---|
| A1 | 需求信息抽取 | 粘贴聊天 / 邮件 / 描述，自动抽取客户、部门、优先级、标签等结构化字段并预填表单 |
| A2 | 相似需求检测 | 关键词重合度 + LLM 复核，提交前提示可能重复的需求 |
| A3 | 需求清晰度检查 | 提交前指出描述缺失的关键信息 |
| A4 | 自动分类与打标签 | 按标签体系推荐分类 |
| A5 / A5C | 自然语言搜索 / 多轮对话 | A5 单轮模板解析为兜底管道；A5C 为多轮智能体检索 |
| A6 | 周报自动生成 | 按周汇总，支持个人简报与中心简报 |

### 对话式检索智能体（A5C）

这是本项目工程量最大的一块，不是"把问题丢给模型"那么简单：

- **ReAct 式工具循环**：模型每轮自行决定输出 JSON 工具调用（`search` / `detail`）还是最终回答；工具在本地执行、结果回填后继续推理，最终答案流式输出
- **确定性防幻觉**：回答中引用的需求编号会与工具真实返回的编号做机械比对，命中未知名时直接提示"自校验未通过"并列出本轮真实命中的编号——比换更强的模型更有效，且零成本
- **跨轮白名单**：此前各轮已验证过的编号在后续轮次被再次引用（如"那条呢"）不算编造，避免跨轮误报
- **统计口径复用**：对话内的聚合数字全部复用页面已有函数，不另写一套，避免同一指标在对话与页面里对不上
- **降级兜底**：未配置 API Key 时自动退回本地关键词检索

### 其他

- **评估闭环**：需求方满意度星级 + 使用体验问卷（自研 KUI 指数）+ 改进项跟踪
- **使用统计**：登录次数、活跃天数、有效停留时长、功能分布、导入导出计数
- **通知**：钉钉群机器人（按需求标题业务标签分流到不同群）+ SMTP 邮件

## 技术栈

| 层 | 技术 |
|---|---|
| 界面 | Streamlit 单页应用 |
| 数据库 | PostgreSQL（`psycopg2` 连接池） |
| AI | 多提供商网关：DeepSeek / 通义千问 / 智谱 GLM / OpenAI，任选其一 |
| 通知 | 钉钉自定义机器人（加签）、SMTP |

## 快速开始

详细步骤见 [QUICKSTART.md](QUICKSTART.md)，简要流程：

1. 安装 Python 3.10+（安装时勾选 *Add Python to PATH*）
2. 准备 PostgreSQL，用超级用户执行 `setup_postgres.sql` 创建账号与数据库
3. 复制 `.env.example` 为 `.env`，填写数据库连接与 LLM API Key
4. `pip install -r requirements.txt`
5. 双击 `start.bat`，或执行 `streamlit run app.py`

首次启动会按 `sample_data.py` 的 `SEED_USERS` 建初始账号，初始密码 `123456`，登录后请在「用户管理」修改。

## 配置

`.env` 关键项：

| 变量 | 说明 |
|---|---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | PostgreSQL 连接 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（推荐） |
| `DASHSCOPE_API_KEY` / `ZHIPU_API_KEY` / `OPENAI_API_KEY` | 其他 LLM 提供商（任选其一） |
| `DINGTALK_WEBHOOK` / `DINGTALK_SECRET` | 钉钉默认群机器人 |
| `DINGTALK_WEBHOOK_A/B/C` / `DINGTALK_SECRET_A/B/C` | 按业务标签分流的群机器人（选填） |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | 邮件提醒（选填） |
| `USER_EMAIL_DOMAIN` | 用户邮箱默认域名 |

> `.env` 已被 `.gitignore` 忽略，仓库里只提供 `.env.example`。

## 目录结构

```
app.py                  主应用（Streamlit 入口，含 A1~A6 各 AI 能力与对话智能体）
ai_helper.py            AI 辅助逻辑（抽取、相似检测、清晰度检查、检索规划、周报）
llm_gateway.py          多提供商 LLM 网关
db.py                   数据访问层与建库逻辑
auth.py                 登录与会话
dingtalk.py             钉钉群机器人
email_notify.py         邮件提醒
sample_data.py          种子账号与业务枚举
setup_postgres.sql      数据库与账号初始化脚本
requirements.txt        依赖
data/kanban.db          本地示例数据库（主库为 PostgreSQL）
tools/                  自检与 UI 探测脚本
```

## 实现约定

- **主库是 PostgreSQL**，`data/kanban.db` 仅作本地示例与离线查看
- **问卷题干冻结**：改题干等于换指标，必须递增 `SURVEY_QUESTION_SET` 版本号并按版本分组统计，禁止跨版本混算均分
- **工作日 / 统计口径统一**：所有聚合走同一套函数，不在视图层另算

## License

[MIT](LICENSE)
