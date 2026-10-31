# Docker Desktop 运行 socket 故障恢复记录

日期：2026-10-22。此次为环境修复，未修改业务源码、验收规则或发布包。

## 原因与证据

Docker Desktop 4.91.0 启动在 Windows 主机服务阶段失败，尚未建立 Linux Engine 管道。日志先报 `Docker/run/sailor-ingest.sock`，随后报 `docker-secrets-engine/engine.sock` 无法重命名为 `.stale`。本机 `fsutil reparsepoint query` 同样返回 Error 1920；对象为零字节 ReparsePoint。仅退出重开会重新遇到原对象，不能解决此故障。

确认的直接原因是遗留 AF_UNIX 通信对象无法由 Windows 正常访问，Docker 的逐文件重命名恢复路径随之失败。此次没有证据能确定最初由异常关机、Windows 兼容性还是其他因素造成，不能断言具体首次触发源，也不能把目录恢复称为上游缺陷永久修复。

同类上游报告：https://github.com/docker/desktop-feedback/issues/554 和 https://github.com/docker/desktop-feedback/issues/676 。

## 已执行恢复

1. 核对引擎不可用和故障日志；尝试正常停止命令未及时结束后，仅结束已确认安装路径下的 Docker Desktop/backend 故障进程。
2. 在进程已退出后，检查指定目录仅含零字节 socket 重解析对象，父目录不是重解析点；将父目录原地改名保存，不遍历或删除 socket。
3. `C:/Users/32975/AppData/Local/Docker/run` 的现场保存在相邻 `run.socket-recovery-20261022-092314`、`run.socket-recovery-20261022-092413`；另一个现场保存在 `C:/Users/32975/AppData/Local/docker-secrets-engine.socket-recovery-20261022-092349`。分步恢复时重启会产生新 socket，因此最后在同一次启动前排除两处旧对象。
4. 启动 Docker Desktop，随后使用项目 `platform.ps1 start` 启动原受管服务。

未恢复出厂、未删除 Docker 数据目录或 socket、未注销 WSL、未新建数据库、未改动原数据盘、容器卷及模型凭据。没有更新 Docker 或修改全局启动设置。

## 实际验证

- `docker info` 成功，Engine 29.8.0。
- 原容器 `agent-company-postgres-1` 为 running/healthy，原卷 `agent-company_company_db`。
- 原数据库迁移 `64bb01a3c208`，公共项目 11 个，可正常查询；此次未逐个重新验收历史项目内容。
- `platform.ps1 start/status`：API、独立预览、前端、Worker 四项均 managed_alive=true。
- 工作台 5173 首页、8000 OpenAPI 均 HTTP 200；已请求在 Codex 内打开工作台，工具返回 queued，不能将此当作已完成可视浏览器检查。

## 当日后续：官方补丁升级与复验

用户明确授权操作后，通过 `docker desktop update --check-only` 确认官方通道提供 4.96.1，使用 `docker desktop update --quiet` 升级；更新包及安装后 Docker Desktop 的 Authenticode 签名均为 Valid / Docker Inc。安装日志 2026-10-22 09:30:50（北京时间）明确记录 `Installation succeeded`，文件版本为 `4.96.1.244132`，Engine 为 `29.9.2`。更新 CLI 在旧 backend 退出时返回 1，因此以独立安装日志、实际文件版本及新引擎响应核实成功，没有将该 CLI 返回误记为成功。

升级前使用受管停止入口安全停止平台，并一致备份公共 schema 和 11 个项目文件到 `.local/backups/docker-upgrade-20261022`；备份 ID `36358c60-7463-4cee-b095-5295fdd2e9ea`，verify 通过。数据库原容器正常停止后才升级。

实际验证：

- 安装后首次启动成功，再执行两轮 `docker desktop stop --timeout 45` / `docker desktop start --detach`，每轮均正常返回且 Engine 响应成功；没有人工改名或删除本轮新建 socket。
- backend 日志在北京时间 09:30:53、09:31:55、09:32:30 三次记录自动将 `Docker/run` 和 `docker-secrets-engine` 移到各自 `.stale/<时间>` 目录，随后继续启动。证明上游的目录回退恢复逻辑已在本机实际生效。
- 原 PostgreSQL 容器 ID `14aeef3fc76a819423ded743c454f625b3e120b24078a8c130ab921471f9d71b`、原卷均保持，healthy；现有全部镜像 ID 与升级前列表一致。
- 与升级前备份逐表核对 34 张公共 schema 表，changed_tables=[]；11 个项目的文件树及摘要全部一致。
- 工作台四项服务重新启动，均 managed_alive=true；工作台首页、API OpenAPI HTTP 200。没有为环境验证新建业务项目或调用模型。

官方问题记录指出修复已进入 4.92，4.93 的重复启动已验证：https://github.com/docker/desktop-feedback/issues/676 。先前“上游仍需修复”判断已由本次核对纠正；本机旧 4.91 尚未包含补丁。

## 剩余边界

升级前仅恢复了当前启动；上述后续升级已补做两轮完整启停验证，并证实官方自动恢复生效。此次未做 Windows 整机重启或长期稳定性验证，也不保证消除所有其他 Docker 故障。旧故障现场及备份均保留，没有自动清理。若再报错应按新日志重新诊断，禁止盲目删除整个 Docker 目录、重建空数据库或将早先人工改名流程未经检查自动执行。
