# M0 环境检查与候选方案

状态：待决策草案。检查日期：2026-10-19。只进行了只读检查，未安装依赖或启动服务。

历史说明：本文记录初始化之前的检查，不代表当前环境。Docker 引擎、独立数据库、镜像、API/Worker/前端/预览及真实模型后续均已接通；当前启动方式见 docs/11，实际验收见 docs/17。

## 本机检查结果

| 工具 | 实际结果 | 说明 |
| --- | --- | --- |
| Git | 2.52.0.windows.1 | 命令可用 |
| Node.js | v24.19.0 | 命令可用；前端依赖兼容性初始化时验证 |
| Python | 3.14.3 | 命令可用；不据此认定所有后端依赖兼容 |
| Docker CLI | 29.8.0，context=desktop-linux | 客户端可用 |
| Docker Engine | 当前无法连接 | desktop-linux 对应的命名管道不存在；尚不能确认是引擎未启动还是上下文配置问题 |

Docker 的结果只证明当前连接不可用，不证明 Docker 未安装。后续开始容器验证前需恢复引擎连接。

## 候选技术组合

继续保留 React + TypeScript、FastAPI、PostgreSQL + SQLAlchemy + Alembic 的候选组合。LangChain、LangGraph、Docker、Git worktree 和 Playwright 沿用已确认基线。

执行器建议独立于 HTTP 请求生命周期，初期评估 PostgreSQL 持久工作表、原子领取、租约与 outbox。数据库锁只解决领取的一部分问题，不能代替执行去重、进程管理或故障恢复。

检查点建议评估 PostgreSQL 持久化后端，并和业务数据明确职责。后端 Python 版本在核心依赖兼容性验证后锁定，不直接沿用系统版本。具体依赖版本仍未选定。

## 用户已确认的信息

1. 首版供个人使用，不做注册和团队功能。
2. 当前 Windows 本机运行，Docker 执行业务项目。
3. 模型通过平台配置页设置 Key、Base URL 和类型/型号，并可切换；不固定供应商。

模型配置细化方案见 docs/07-model-configuration.md。后续继续明确前后端、数据库、执行器和首个验收项目选型，首次真实联调时通过配置页添加模型；不在聊天中索取 API Key。

## 参考资料

- LangGraph durable execution：https://docs.langchain.com/oss/python/langgraph/durable-execution
- FastAPI background tasks：https://fastapi.tiangolo.com/tutorial/background-tasks/
- PostgreSQL SELECT / locking：https://www.postgresql.org/docs/current/sql-select.html
