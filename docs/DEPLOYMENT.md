# Windows 服务器部署与备份

本说明面向服务器管理员。模板没有安装 PostgreSQL、Caddy、WinSW，也没有注册服务、修改防火墙或创建计划任务。初次试运行先按根 README 完成虚构数据流程，再在指定服务器配置正式部署。

## 当前状态与部署目标

现有代码采用一台服务器上的模块化单体：FastAPI 提供 API，SQLAlchemy 连接服务器数据库，React 构建为静态网页。开发验证使用本机 SQLite 演示环境；生产目标为下面的 Caddy + FastAPI + PostgreSQL 结构。

| 部分 | 运行位置与作用 |
| --- | --- |
| 科室电脑 | 连接体检中心局域网、打开浏览器、访问服务器地址、登录 |
| 网页 | 浏览器执行轻量网页，构建文件由服务器 FastAPI 或 Caddy 提供 |
| API 与业务处理 | 服务器 FastAPI；查询、分页、权限检查和保存均经由 API |
| 数据库连接 | 仅服务器 SQLAlchemy 连接数据库，浏览器不能直接连接 |
| 报告、日志、备份 | 服务器私有目录；外置备份盘作为第二副本 |
| 开机自动启动 | 已有 WinSW 配置模板，尚需正式服务器安装、配置并重启验证 |

客户端无需安装 Python、Node.js、PostgreSQL、Docker、开发环境或 Electron。服务器运行打包后的前端，不需要常驻 Node.js、npm 或 Vite；这些工具仅在安装依赖、构建、测试或开发调试时使用。也可以在开发电脑构建后把 `frontend/dist` 部署到服务器。

当前本机预览不等于已经完成局域网部署。PostgreSQL 实机业务测试、固定 IP 与防火墙、HTTPS 证书信任、Windows 服务、每日备份任务和恢复演练均需在实际服务器另行验收。

## 部署结构

```text
科室电脑浏览器 → https://192.168.1.100
                  ↓
Windows 服务器：Caddy（443 / HTTP 80 自动跳转）
                  ├── frontend/dist 静态网页
                  └── /api → 127.0.0.1:8000 FastAPI
                                      ↓
                              SQLAlchemy → PostgreSQL（本机 5432）

私有目录：data/reports、logs、backups；不通过静态网页服务器公开
```

正式 PostgreSQL 和后端只监听本机。固定服务器局域网 IP，客户端通过 API 共用一个数据库。Node.js 仅在构建网页时使用，客户端没有安装步骤。

局域网演示可使用现有 `scripts/start.ps1 -Lan`：它把后端监听地址从 `127.0.0.1` 改为 `0.0.0.0`，科室浏览器访问 `http://服务器IP:8000`。还需由管理员配置对应 `ALLOWED_ORIGINS` 和局域网防火墙规则。脚本不会固定 IP、修改防火墙或安装服务；HTTP 演示使用虚构数据。正式部署通过 HTTPS 网页入口访问，后端仍只监听本机。

## 1. 建立正式服务器目录

示例安装路径为 `C:\Healthcheck`。若使用其他位置，修改所有模板路径，不要混用旧开发路径。目录应位于服务器本地磁盘，不使用工作人员个人桌面或同步网盘。

复制源代码，在这台服务器重新创建 `.venv` 并安装后端依赖，构建 `frontend/dist`。Python 虚拟环境不能通过直接复制到新电脑代替安装。部署前记录已验证的代码版本和依赖版本，后续升级先在测试环境验证。

由管理员配置专用 Windows 服务账号，授予项目读取权限和 `data`、`logs`、`backups` 写入权限。普通客户端用户不应通过 Windows 共享文件夹读取数据库、配置、日志、报告文件或备份。

## 2. PostgreSQL 和根 .env

安装仍受维护的 PostgreSQL 版本，创建空 `healthcheck` 数据库及单独的应用数据库账号。应用账号只访问该数据库，不使用 PostgreSQL 超级管理员账号。

数据库网络配置只监听 `127.0.0.1`，并在 `pg_hba.conf` 使用适合本机账号的密码认证方式。不要向科室电脑或互联网开放 5432。

根 `.env` 示例（由管理员替换占位内容）：

```dotenv
DATABASE_URL=postgresql+psycopg://healthcheck:YOUR_URL_ENCODED_PASSWORD@127.0.0.1:5432/healthcheck
DATA_DIR=data
REPORTS_DIR=data/reports
COOKIE_SECURE=true
SESSION_HOURS=8
ALLOWED_ORIGINS=["https://192.168.1.100"]
BACKUP_DIR=backups
```

URL 中的用户名、密码含 `@`、`:`、`#`、`%` 等特殊字符时需要百分号编码。`.env` 限制到管理员和运行服务账号可读。这里没有默认数据库密码或管理员密码。

从 `backend` 运行 `python -m app.cli init --username admin` 初始化结构和管理员。正式人员、科室、套餐必须按实际业务配置；当前第一版没有完整的基础数据管理页面。不要直接使用共享演示密码进入医疗业务。

仅建立演示目录供验收时，可使用 `init --username admin --catalog`，它不会创建其他演示账号。实际人员用 `create-user --username 用户名 --display-name 姓名 --role registrar` 等命令分别设密码；医生角色追加 `--department-id 实际科室编号`。管理员重置账号密码用 `set-password --username 用户名`。

SQLite 演示库迁移到 PostgreSQL 分为两件事：Alembic 建立表结构，以及把旧库数据经过校验导入新库。仅修改 `DATABASE_URL` 不会搬移数据。本版提供两个数据库的连接与结构迁移支持，尚未提供一键搬移历史数据的工具。

## 3. HTTPS 网页入口

从 [Caddy 官方发布渠道](https://caddyserver.com/download) 获取与服务器系统匹配的二进制，放在 `deploy/caddy.exe`。复制 `deploy/Caddyfile.example` 为 `deploy/Caddyfile`，修改 IP 和网页构建路径。

模板用 `handle` 保留 `/api` 前缀，前后端共用一个访问地址；只有 `frontend/dist` 可以作为静态目录。数据库、私有报告、`.env` 不能位于它下面。[Caddy 路由示例](https://caddyserver.com/docs/caddyfile/patterns)

管理员先运行配置验证：

```powershell
.\deploy\caddy.exe validate --config .\deploy\Caddyfile --adapter caddyfile
```

模板使用 `tls internal`，由 Caddy 本地证书颁发机构生成证书。管理员应将服务器实际使用的 CA 根证书通过受控方式安装至每台客户端的“受信任的根证书颁发机构”，使浏览器正常信任 HTTPS。只分发根证书，不分发任何私钥。部署后从客户端验证 `https://192.168.1.100`，不能依赖用户每次忽略证书错误。[Caddy 内网 HTTPS 文档](https://caddyserver.com/docs/automatic-https#local-https)

服务模板把 Caddy 的 `APPDATA` 固定为 `C:\Healthcheck\data\caddy`，以便证书在服务重启后保持一致。管理员需要确认 Caddy 实际使用的存储目录，并单独安全备份 CA 及配置；业务备份脚本不会复制这个目录。

Windows 防火墙为体检中心局域网网段开放网页入口 443，以及仅用于 HTTPS 跳转的 80。后端 8000 和数据库 5432 不开放到客户端。Windows 7 仅在受控内部局域网使用，升级客户端浏览器和操作系统应列入设备维护计划。

## 4. Windows 开机自动启动

从 [WinSW 官方发布渠道](https://github.com/winsw/winsw/releases) 获取符合服务器平台的服务包装程序，参考其对应版本文档。将两份二进制命名为 `healthcheck-api.exe` 和 `healthcheck-web.exe`，放入 `deploy`。

复制模板为同名 XML 文件：

| 程序 | 配置来源 | 实际配置文件 |
| --- | --- | --- |
| `healthcheck-api.exe` | `backend-service.xml.example` | `healthcheck-api.xml` |
| `healthcheck-web.exe` | `caddy-service.xml.example` | `healthcheck-web.xml` |

修改模板中路径。后端模板预留 PostgreSQL 服务依赖，必须使用“服务”管理器中真实的服务名称，不能照抄示例版本号。两个模板采用自动延迟启动、故障重启和运行日志轮换。服务处于运行状态仍需通过 `/api/health` 确认后端存活、通过 `/api/ready` 确认数据库连接与基础表就绪。数据库短暂不可用时，就绪检查及相关业务 API 返回错误并允许前端重试，存活检查仍可返回 200；本版没有在后端启动时阻塞等待数据库的逻辑。

以下操作会注册真实机器服务，只有管理员完成上述配置后才执行：

```powershell
Set-Location 'C:\Healthcheck\deploy'
.\healthcheck-api.exe install
.\healthcheck-web.exe install
```

在 Windows“服务”管理器中配置专用服务账号并核对目录权限，再启动：

```powershell
.\healthcheck-api.exe start
.\healthcheck-web.exe start
```

服务安装方式、账号设置和 XML 字段以所选 WinSW 版本为准。[WinSW 使用文档](https://github.com/winsw/winsw)、[XML 配置文档](https://github.com/winsw/winsw/blob/v3/docs/xml-config-file.md)

重启服务器验证 PostgreSQL、API、网页入口自动启动，客户端登录并录入虚构结果。检查后端存活接口 `/api/health`、数据库就绪接口 `/api/ready` 和服务日志。完成这项现场验证后，才能确认开机后工作人员无需命令行或手动启动前端。

## 5. 手动备份与验证

在项目根执行：

```powershell
.\scripts\run-backup.ps1 -PgBin 'C:\Program Files\PostgreSQL\18\bin'
```

`PgBin` 路径按已安装版本修改；SQLite 无需这个参数。PostgreSQL 的 `pg_dump`、`pg_restore` 应与数据库版本兼容。备份从根 `.env` 和环境变量读取配置，环境变量优先。不把连接密码放进命令参数、清单或日志。

每次备份包含：

- SQLite 使用在线 `sqlite3.backup`，包含已提交的 WAL 数据，然后执行 `integrity_check`。
- PostgreSQL 使用 `pg_dump --format=custom`，再用 `pg_restore --list` 检查归档可读取。
- 复制 `REPORTS_DIR` 下的私有文件，检查复制期间文件没有变化。
- 每个文件的 SHA-256 校验清单、备份时间、数据库类型和恢复说明。

失败时返回非零退出码，未完成目录保留 `.incomplete`，不能用于恢复。完整性检查可以发现丢失或损坏，但不能证明业务数据全部正确，必须再进行恢复演练。[PostgreSQL 归档恢复文档](https://www.postgresql.org/docs/current/app-pgrestore.html)

手动重新校验一个备份目录：

```powershell
.\.venv\Scripts\python.exe .\scripts\backup.py --verify '.\backups\实际备份目录' --pg-bin 'C:\Program Files\PostgreSQL\18\bin'
```

本版报告快照和修改历史在数据库中。将来增加附件上传时，需要采用不可变文件和明确的维护备份时段，保证数据库快照与附件清单一致；当前脚本的逐文件稳定性检查不能代替跨文件事务。

外置硬盘副本：

```powershell
.\scripts\run-backup.ps1 -ExternalDirectory 'E:\HealthcheckBackups' -PgBin 'C:\Program Files\PostgreSQL\18\bin'
```

外置副本也重新计算校验和。外置盘断开、写入失败或校验失败时整个命令返回失败；本机已经完成的备份仍然保留。备份目录不删除任何旧数据，由管理员按保存规定、磁盘容量和恢复验证结果手动清理。备份和外置盘同样包含敏感医疗数据，需要访问控制和磁盘保护。

## 6. 每日计划任务

先人工备份、校验并恢复演练，再在服务器管理员 PowerShell 中显式执行：

```powershell
.\scripts\install-backup-task.ps1 -At '02:00' -PgBin 'C:\Program Files\PostgreSQL\18\bin' -ExternalDirectory 'E:\HealthcheckBackups'
```

SQLite 省略 `PgBin`；尚未配置外置盘时省略 `ExternalDirectory`。此操作会创建 `HealthcheckDailyBackup` 每日任务，不覆盖同名已有任务。服务器 Windows 时区必须设为北京时间，脚本使用服务器本地时钟。

模板用 SYSTEM 运行，可以在未登录时备份，限制任务不重叠，错过计划时间时补跑。SYSTEM 需要读取根 `.env` 和数据库、写入目标目录；不能使用只在交互登录后映射的网络盘。生产管理员可以将任务改为有相应权限的专用备份账号。

在任务计划程序手动运行一次，确认“上次运行结果”为 `0x0`，检查 `logs/backup-日期.log`、备份清单和外置目录。非零结果表示失败，应当天处理。当前任务记录日志和退出码，尚未实现短信或邮件故障通知，需由管理员每日查看结果。

PostgreSQL 认证若使用 `PGPASSFILE`，其路径和文件权限应配置给实际运行任务的账号；个人用户的 pgpass 不会自动属于 SYSTEM。不要将密码放在计划任务参数中。

## 7. 恢复演练

恢复脚本不自动覆盖正式数据库，管理员按以下顺序演练：

1. 选取一个完整备份，用 `--verify` 确认清单和文件。
2. 在隔离测试目录建立新的 `.env` 和私有文件目录，使用新的测试数据库。
3. SQLite 将 `database.sqlite3` 复制到测试数据库路径。PostgreSQL 建立空测试库，并执行 `pg_restore --exit-on-error --no-owner --no-acl --dbname 测试库 database.dump`，连接设置通过环境变量或 pgpass 提供。
4. 将备份 `reports` 复制到测试私有目录，配置 `REPORTS_DIR`；配置文件、服务账号和数据库角色凭据由管理员单独安全保存。
5. 用对应代码版本启动测试后端，核对人员/体检记录数量、科室结果、异常标记、修改记录、报告版本、登录权限和私有文件。
6. 保存演练日期、备份编号和验证结果，定期再次演练。

真正恢复正式数据时，先停止网页入口和后端，保存现有数据库及文件，确认连接目标，再执行恢复。切勿在运行的 SQLite 库上直接覆盖文件，也不要遗漏旧库的 `-wal`、`-shm` 状态；将恢复文件放入新的目录并修改配置是更容易核对的做法。

## 8. 版本升级

先备份并确认可恢复；停止服务；更新代码和已验证依赖；从 `backend` 执行 `python -m app.cli migrate` 应用 Alembic 迁移；重新构建前端；启动服务并验证健康、登录、查询和结果保存。结构升级命令不要求重新输入账号密码。

数据库迁移不可只依靠代码回滚处理。涉及表结构或数据转换的升级，必须在生产副本上先验证，并提前确定数据库恢复方案。

## 正式启用前的验收

服务器自动启动、HTTPS 证书信任、固定 IP、每日备份/外置副本和恢复演练全部验证；按角色核对可见数据；验证断网重试和多人修改冲突；在现场 Windows 7/10 浏览器和接近真实的数据量上，验证 10～50 台电脑同时操作。当前实现和模板不能替代现场并发、可靠性及医疗业务验收。
