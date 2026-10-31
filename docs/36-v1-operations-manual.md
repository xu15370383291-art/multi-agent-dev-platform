# 本机个人版V1：安装、日常运行与恢复

2026-10-23：用户已批准受控自动质量修复，合同见docs/46、实际进度见docs/47。新版progress QA默认镜像m9，初次安装需先按docs/41准备m6，再依次执行`docker build -f infra/quality/Dockerfile.m7 -t agent-company-quality:m7 infra/quality`、`docker build -f infra/quality/Dockerfile.m8 -t agent-company-quality:m8 infra/quality`和`docker build -f infra/quality/Dockerfile.m9 -t agent-company-quality:m9 infra/quality`。m8提供隔离世界独立帧率测量，m9把已验证断言进展改为独立凭据，避免持续测试的进度日志撑满输出缓冲区；旧镜像/报告绑定保留不覆盖。测试API最小修订的语法树校验还使用安装步骤中的Node及frontend/node_modules/typescript，不执行待验脚本；缺少解析器时拒绝应用修订。持续操作验收需真实运行相应时长，不能用快速模拟替代。

2026-10-23 自动纠错增强：受控的普通故障可按原批准需求自动分析、检查并续接，无需反复点击继续；具体合同与实际验收进度见[docs/44](44-autonomous-correction-plan.md)和[docs/45](45-autonomous-correction-acceptance.md)。工作台会显示简单原因及正在分析/检查/继续的进度，可随时请求暂停自动处理或取消。未知操作必须先核实回执，人工暂停不会自动恢复，需求审批、错误测试断言的人工核对、独立质量验收仍保留。下文旧人工新策略入口仍可使用，但不表示所有错误都必须手工处理。

2026-10-21 全局策略v2覆盖旧累计预算：正常进展时模型/工具总次数、活动总时长和正常修订轮数均无硬上限；保留重复/停滞核对、取消/失租、资源边界及质量合同。旧历史快照保留，非终态旧运行显式审计迁移。见[全局执行合同](40-global-progress-plan.md)及[实施验收](41-global-progress-acceptance.md)，其余旧数值仅指历史策略。

2026-10-21，M5及本机个人版V1已通过验收，历史结果见[最终验收报告](37-m5-acceptance-report.md)。随后按进展执行增强已部署公共207，后端433项、前端90项及一个真实项目通过，见[增强验收报告](39-progress-policy-acceptance.md)。增强封存包位于本电脑`D:\muti-agent\.local\releases\progress-v1-local\platform.zip`，最终安装与一致性须核对该报告所列回执；原M5包`D:\muti-agent\.local\releases\m5-v1-local\platform.zip`保留。平台运行在Windows，PostgreSQL及业务开发/QA容器运行在Docker。工作台、API和预览只监听本机。摘要与文件清单在对应发布目录，包内release-manifest.json可逐项核对，不含用户数据或模型Key。

## 1. 当前电脑与数据位置

- 项目目录：`D:\muti-agent`。
- 工作台：`http://127.0.0.1:5173/`；API：8000；独立预览：8001；数据库：55432。
- 平台进程记录与日志：`.local/platform/`；项目文件：`.local/projects/<项目ID>/`。
- 本机Docker原数据盘：`D:\docker\DockerDesktopWSL\disk\docker_data.vhdx`，数据库卷`agent-company_company_db`。
- 不需要为了日常运行本机容器登录Docker账号；镜像下载若受到仓库限制，应按具体错误处理，不能通过重置Docker解决。

以下命令均在项目目录执行；需要维护的命令在backend目录另有标注。多个浏览器可同时使用，不存在单工作台锁。

## 2. 已安装环境的日常启停

先从Windows开始菜单启动Docker Desktop，确认引擎正常，并核对原数据库容器/卷仍在。数据库未运行时：

```powershell
.\platform.ps1 preflight
.\platform.ps1 start
.\platform.ps1 status
```

`preflight`只检查、不修改；数据库停止时会提示不可用，可直接使用`start`。启动入口核对原Compose容器与卷身份，必要时启动已有数据库并等待健康，再启动API、独立预览、正式前端和Worker；不会新建缺失的容器/数据卷。打开工作台即可使用，不需分别打开四个终端。重复启动会返回已有管理实例；端口被未知进程占用时拒绝接管。原手工开发服务不自动收编，首次切换须核对并停止原实例。

停止前先在工作台处理活动任务：请求暂停/取消，等待真正停止并核对未知结果。之后：

```powershell
.\platform.ps1 stop
docker compose -f infra/compose.yaml stop postgres
```

平台停止只处理自己登记且身份仍匹配的进程；有活动或待领运行时拒绝直接停止。停止不清理数据库、Docker卷或项目文件。预览服务停止后链接暂时不可用，ZIP和历史仍保留。

## 3. 新环境安装

1. 准备Windows、Docker Desktop/WSL2、Python3.12及uv、Node/npm、Git；业务验证使用镜像内Node22/Playwright，平台浏览器测试使用本机Edge。
2. 解包版本化平台ZIP，逐项核对release-manifest文件摘要；源码清单包括尚未Git提交的文件，不以Git HEAD代替实际版本。
3. 在backend执行`uv sync --locked`，在frontend执行`npm ci`。配置backend/.env可从`.env.example`复制；不要把模型Key写入文件。
4. 在全新专用环境才使用`docker compose -f infra/compose.yaml up -d postgres`创建数据库；已有电脑若容器/卷突然消失，应先排查原数据盘，不能把新建空库当恢复。
5. 在backend运行`uv run --locked python -m scripts.platform initialize`。它只允许空的专用数据库，检测到已有表/其他schema会拒绝；绝不用于修复或覆盖旧数据库。
6. 在项目根目录构建业务执行及QA镜像，再构建工作台：

```powershell
docker build -t agent-company-executor:m3 infra/executor
docker build -t agent-company-quality:m1 infra/quality
docker build -f infra/quality/Dockerfile.m5 -t agent-company-quality:m5 infra/quality
docker build -f infra/quality/Dockerfile.m5.1 -t agent-company-quality:m5.1 infra/quality
.\platform.ps1 build
.\platform.ps1 preflight
.\platform.ps1 start
```

M5 QA镜像在历史m1镜像基础上增加固定候选Node单测证据；m5.1再提供控制端candidateSHA参数与精确执行版本回执，预检拒绝把旧SHA字面量写进可复用单测版本断言。保留m1、m5标签和独立构建源供旧报告核对，不覆盖旧标签。新默认使用m5.1。浏览器通过不能覆盖实际单测失败、跳过或候选文件被修改。

7. 在工作台模型配置中本机录入Key、Base URL和模型ID，验证能力并设置默认/角色配置。Key存Windows Credential Manager，不在安装包/数据库备份中分发。测试能力会产生真实API调用。

镜像标签仅为默认入口。正式候选/报告仍固定实际镜像ID；新机器按Dockerfile构建可能得到新ID，不得冒充历史环境，历史重验须准备对应镜像或明确未验证。可在原电脑用`docker save --output <独立文件> <具体镜像ID>`保留镜像，`docker load`导入；摘要与版本清单一起保存，不重打活动验证镜像标签。

当前支持约定的静态Web软件与浏览器小游戏；不代表任意业务后端、任意依赖安装或云部署已支持。

## 4. 更新与配置

### 按进展执行增强版（207）

用户已批准docs/38，新版验收和正式部署状态见docs/39。新运行显示“已调用N次 · 按任务进展执行”，没有模型总次数硬上限；旧运行显示原N/上限。3600活动秒、每任务80次工具、单工具60秒和系统2个Docker槽继续生效。进展检测不是无限资源授权。

206升级207前，使用配套206代码安全停止旧服务并做一致备份；不能先用207 ORM访问206维护接口。确认空闲、备份完成后，在新版backend运行`uv run --locked alembic upgrade head`，核对旧表原有列内容、历史额度、计数、活动时间、暂停状态及旧ZIP，再启动新版。207新增策略/检测/会话历史后禁止破坏性downgrade；回退使用配套旧包和恢复到新目标的旧备份，原环境保留。

旧暂停运行不会自动改模式。“采用按进展执行（高级）”需要明确原因和确认，登记原限额及累计快照，迁移后还需正常恢复核验；不会自动继续。发生重复/停滞时，在运行详情查看具体分支、原工具证据、最近变化及原因。只有核对已有证据并提出不同且有依据的下一步时，才使用“新策略”表单，确认准确状态摘要；它只登记策略，随后仍走恢复检查。切模型或反复点继续不能清空历史，unknown仍需原回执核对。

策略、进度记录和完整会话随数据库配套备份。恢复工具只接受相同迁移版本并核对文件/表摘要，始终恢复到新隔离目标且不启动Worker。M5既有206包与备份继续按206工具验证，不能冒充207格式。

数据库和文件是一个恢复单元。更新前停止平台写入，按第5节备份并校验；保存旧平台版本及固定镜像，再安装新文件并按该版本迁移说明升级。M5当前仍使用206，不需要为了M5再迁移数据库；低于206按对应阶段迁移台账处理，不能跳过备份或直接启动不兼容版本。

配置见backend/.env.example。M5历史发布默认60次；207增强版默认`COMPANY_RUN_CALL_POLICY=progress_guarded`，新运行不设置总调用上限，`COMPANY_RUN_MAX_MODEL_CALLS=60`仅用于显式fixed模式。旧运行读取自身持久快照，不随新默认变化。3600活动秒、2个Docker槽以及现有CPU/内存/PID/网络限制保留；修改已确认限制需要用户明确决定。换端口须保持前端来源、API与独立预览地址一致，使用backend的`scripts.platform start --api ... --preview ... --frontend ... --state ...`统一生成进程环境。

回退不是盲目运行`alembic downgrade`：先核对代码/数据库兼容，206事件序号历史不允许破坏性回退。必要时把配套旧备份恢复到新隔离目标，验证后再显式切换配置；原环境保留。

## 5. 一致备份与隔离恢复

先安全停止平台。其他未纳管的API/Worker也需核对停止，不应在备份期间写入。工具还会锁定业务/检查点表，拒绝活动任务，使用PG导出快照并检查复制前后文件摘要。

backend目录运行，目标备份目录必须不存在、且在业务工作区之外：

```powershell
uv run --locked python -m scripts.maintenance backup --path ../.local/backups/my-backup
uv run --locked python -m scripts.maintenance verify --path ../.local/backups/my-backup
uv run --locked python -m scripts.maintenance restore --path ../.local/backups/my-backup --target ../.local/restores/my-restore
```

备份范围是当前配置所指schema及其中登记项目的完整工作区，包含审批、账本、事件、检查点、源码、日志、报告、交付文件。不同验收schema/工作区须分别备份；不是整台Windows或全部Docker镜像备份。配置中的非秘密参数、平台版本和镜像清单应另外保留；.env私密值及Windows凭据不导出。

恢复仅接受本平台生成且信任来源的备份。先验证格式、版本和全文件摘要，再创建随机新数据库`m5_restore_<UUID>`及全新目录，不覆盖既有数据库/文件。`restore.json`标记state=verified后，才可把数据库配置指向此新库、workspace_root指向恢复的projects目录。非public源schema还须保留search_path。工具不自动启动Worker，不自动批准或继续运行。

恢复后先读项目、审批状态、暂停原因、报告与ZIP，核对版本和预算。换Windows账户/机器时重新本机录入Key并验证能力；DB中的credential_ref不是可移植Key。历史预览URL保留原发布记录，必要时通过新环境的本机预览入口读取原ZIP内容，不能改写旧manifest伪装原地址仍有效。

备份中断保留INCOMPLETE标记，不能用于恢复。文件缺失、摘要不符或版本不兼容会拒绝；失败的隔离恢复现场保留为诊断证据，不自动删库掩盖失败。当前兼容性检查只接受同一数据库迁移版本的备份；旧版本恢复先使用对应平台版本。

## 6. 用量和显式清理

工作台侧栏“维护”查看各项目文件量、数据库整体量与磁盘可用空间，也可执行`.\platform.ps1 usage`。无法统计会明确标为不可用；数据库数字包含其他schema，不是单项目用量。统计不会调用模型。

确认规则：源码、审批、历史、日志、报告、ZIP、数据库与备份不自动删除。仅控制端登记的新临时目录可进入候选清单，且运行和同项目关联运行须已结束、无活动执行/未知结果/待领任务，也不能被恢复、候选或交付记录引用。

控制端登记回执位于`.local/platform/temporary-registry/<ID>.json`；不把旧未知tmp目录自动纳管。用户逐次检查清单，再提交相同摘要：

```powershell
# backend目录；使用真实平台登记回执，不能任意填写目录
uv run --locked python -m scripts.maintenance cleanup-plan --path ../.local/platform/temporary-registry/<ID>.json
uv run --locked python -m scripts.maintenance cleanup --path ../.local/platform/temporary-registry/<ID>.json --confirm <清单confirmation摘要>
```

执行前重新检查状态、引用、路径及内容；清单变化或过期即拒绝。重解析点/跨项目路径拒绝。中断后的removing状态需人工检查隔离现场与审计回执，不允许盲目重复删除。没有符合条件的登记项时不提供清理；不能为腾空间登记既有历史目录，也不能全局Docker prune。

工具输出保持原64KiB等限制；新平台服务诊断stdout/stderr各1MiB后明确截断，不旋转删除旧日志，业务工具证据仍在持久记录中。长期磁盘增长需要用户按真实用量处理，不能宣称自动清理已解决历史存储容量问题。

## 7. 常见问题

2026-10-21最新确认：新运行默认60次模型调用，当前两项M5验收也为60；已用次数不清零，历史运行保留原固定额度。`COMPANY_RUN_MAX_MODEL_CALLS=60`控制以后新运行的默认值，不会自动提高已有记录；运行活动时间仍3600秒、Docker总槽2。重启不会补充额度，也不会自动重试模型超时。

遇到测试脚本自身与批准需求冲突，可在质量验收的“核对测试脚本（高级）”逐项核对修订清单。清单绑定候选SHA、审批摘要、原QA/Reviewer及旧脚本摘要；先预览再明确确认。它会新增一轮实际QA与独立审查，保留原失败，不能处理未知副作用，也不能代替源码缺陷修复。该规则已由用户在2026-10-21明确批准。

- **Docker sailor-ingest.sock错误**：退出报错Docker，从Windows开始菜单重开；核对D盘原数据及原容器/卷。不要恢复出厂、删除Docker目录/socket或注销WSL。
- **端口占用**：状态入口只识别平台登记进程。核对占用者；未知进程不会被自动杀掉。若旧手工服务仍在，应先确认身份再停止。
- **数据库版本不符/初始化被拒绝**：已有数据使用备份与版本迁移流程，不使用initialize；数据库全空先排查原数据盘和配置。
- **Worker停止或运行暂停**：查看工作台的具体原因；核对免费，不等于继续。unknown缺回执时保持阻断；30/30预算不会因重启清零。
- **实时连接中断**：工作台查询补偿并重连，不会自动重新提交业务操作；必要时查看API和前端服务状态。
- **提示未收到操作确认**：请求可能已经生效，界面会先读取状态，不会自动重发。先核对运行与最近恢复记录；相同上下文手动重试沿用原请求标识，新一轮核对使用新标识。如果操作已入队，等待后台执行，不要通过重开项目清零重做。
- **预览打不开**：检查独立预览服务和版本；ZIP仍可下载，在说明指定环境独立运行。
- **维护命令失败**：错误只输出安全类别，不含数据库密码/Key；保留失败现场，按状态和清单核对，不反复重建或删除数据。
