# 局域网体检系统

一台 Windows 服务器保存所有数据，科室电脑通过浏览器使用。后端采用 FastAPI、SQLAlchemy、Alembic；前端采用 React、TypeScript、Vite。系统按模块化单体开发，开发默认 SQLite，正式多人使用采用 PostgreSQL。

客户端不需要安装 Python、Node.js、数据库、Docker 或桌面客户端。以下安装和命令只在服务器或开发电脑执行。

## 部署架构

```text
科室电脑：浏览器
        ↓ HTTP / REST API
一台 Windows 服务器：网页入口 + FastAPI
        ↓ SQLAlchemy
同一台服务器：数据库、私有报告文件、日志及备份
```

React 网页构建为 `frontend/dist` 后，由 FastAPI 直接提供，或在正式部署中由 Caddy 提供并转发 `/api`。Node.js 和 npm 用于安装前端依赖、构建及开发调试；正式日常运行使用构建文件，不启动 Node.js 或 Vite 开发服务器。

数据库只由后端连接，浏览器通过 API 查询和保存，不直接连接数据库。查询、分页、权限校验及业务处理在服务器执行，科室电脑共用服务器上的同一套数据。

当前验证环境为本机 SQLite 演示库和网页预览。PostgreSQL 连接支持、局域网 `-Lan` 启动参数、Caddy 和 Windows 开机服务配置模板已经预留；正式服务器、固定 IP、HTTPS、服务及每日备份尚未完成现场部署验收。

## 当前开发：第 9 阶段医生工作台

第 1～4 阶段的基础架构、数据库、登录及角色权限已按顺序开发和检查。管理员账号管理页面支持创建四类角色、分配医生科室、修改资料与权限、停用账号、服务端搜索与分页。权限改变后该账号全部旧会话失效；多人同时修改会提示冲突并保留输入，核对最新资料后才能重新保存。

第 5 阶段已完成受检者完整档案、新增与编辑、姓名/手机号/身份证号搜索、服务端分页、自动年龄及历史体检入口。保存失败保留输入，旧版本修改需核对最新资料；编辑当前档案不改写历史体检或报告。关键功能、回归及浏览器验收已通过，用户已确认人工核对完成，记录见 [受检者管理验收记录](docs/PHASE_5.md)。

第 6 阶段原有功能及补充“读取身份证或手工录入 → 精确查找已有人员 → 确认本次体检”的完整前台流程均已通过验收。民族、地址、体检类型、可选套餐和可配置编号已实现；人员档案与每次体检继续分表保存。实际证据见 [完整前台登记验收](docs/PHASE_6_COMPLETE.md)。

第 7 阶段已完成科室及基础检查项目的分页查询、新增、编辑、启停，以及分类、单位、参考范围、结果类型和异常标记能力配置。新体检保存项目配置快照，基础配置修改不改写已有体检、检查结果和报告；多人修改需核对版本，保存结果未确认时保留原请求重试。管理员页面、接口权限、迁移与原数据保护均已验收，实际结果见 [科室和检查项目验收](docs/PHASE_7.md)。

第 8 阶段已完成套餐新增、编辑、启停、多项目关联及两个登记入口的套餐选择。页面采用服务端分页和按需读取；套餐及关联配置变化需重新核对，修改只影响之后新登记的体检，原体检、结果及报告保持快照。272 项完整测试、32 项前端测试、构建、断网重试、迁移及重启验收均通过，实际结果见 [体检套餐验收](docs/PHASE_8.md)。本轮止于第 8 阶段。

用户已授权第 9 阶段医生工作台，正在补齐本科室待检/已检分页队列、姓名及编号搜索、独立科室结论、操作医生和保存时间。结果保存按原请求核对，版本冲突必须明确核对后才能继续；现有历史结果及报告保留。本轮不进入第 10 阶段。

当前上传快照中的第 9 阶段仅包含数据模型、追加迁移 `0008` 和部分结果元数据处理，医生队列、科室结论保存接口、可靠重试及对应前端尚未完成，不能视为第 9 阶段验收通过。现有数据库使用这份新源码前，应按部署文档先备份并迁移到当前版本；本机预览库尚未升级到 `0008`。

身份证设备尚未确定，当前提供独立读卡接口及明确标注的模拟读取，不要求客户端安装开发环境。

### 前台完整登记

“读取身份证”预留真实设备适配器，“模拟读取身份证”仅使用虚构软件测试资料；读卡失败仍可手工录入。流程及接入边界见 [身份证读取模块](docs/IDENTITY_READER.md)。人员资料按身份证精确复用，本次体检另行保存；没有证件时先搜索已有档案，不能仅凭同名自动合并。

工作人员一次确认登记，服务器在同一事务保存人员、本次体检和唯一编号。套餐可暂不选择，此时仍为待检，没有检查项目，也不能完成总检。民族、地址、身份证只在授权档案页面使用。

### 服务器体检编号规则

默认新编号为 `20261002-0001`，按实际登记的中国日期分配每日流水号。修改服务器 `.env` 中 `EXAM_NUMBER_MODE`、`EXAM_NUMBER_PREFIX`、`EXAM_NUMBER_DATE_FORMAT`、`EXAM_NUMBER_WIDTH`、`EXAM_NUMBER_SEPARATOR` 后重启后端，对之后的新登记生效。示例见 `.env.example`，旧编号保持不变。

日期格式支持 `%Y%m%d`、`%Y-%m-%d`、`%y%m%d`；流水号位数 3～8。前缀和分隔符分别最多 10 个 ASCII 字母、数字、下划线或连字符。`random` 模式使用前缀加原有 TJ 日期随机编号，日期格式、位数和分隔符用于 `date_sequence` 模式。编号代表实际登记日期，所选体检日期另外保存。

验收使用独立 SQLite 测试库及 `data/preview` 演示环境；未安装正式 PostgreSQL、Windows 服务或备份计划任务。前阶段记录见 [第 2～4 阶段验收记录](docs/PHASES_2_4.md)。完整体检业务原型仍按后续阶段分别验收。

| 基础架构检查项 | 预期 |
| --- | --- |
| 后端存活 | `GET /api/health` 返回 HTTP 200 和 `{"status":"ok"}`，不依赖数据库 |
| 数据库就绪 | `GET /api/ready` 检查连接及 `users` 表；未初始化或不可用时可以返回 503 |
| 前端状态 | 根据真实 `/api/health` 响应显示检查中、已连接或连接失败 |
| 网页构建 | 构建输出 `frontend/dist`，后端可直接提供网页 |
| 重启 | 手动停止后再启动，网页与存活检查恢复正常 |

“服务器已连接”只表示后端存活检查通过，不代表数据库、账号或医疗业务已经可用。

## 本机启动步骤

以下命令在项目根目录运行。当前电脑已有依赖时，直接构建并启动即可；新服务器先按后面的“服务器依赖准备”建立环境。

```powershell
Set-Location 'C:\Users\Administrator\Documents\ChatGPT\体检'
.\scripts\frontend.ps1 -Action Build
.\scripts\start.ps1
```

浏览器打开 [本机网页](http://127.0.0.1:8000)。保持启动终端运行；按 Ctrl+C 停止。启动脚本优先选择项目 `.venv`，不会创建账号、迁移数据库或安装 Windows 服务。

前端构建和开发统一使用 `frontend.ps1`：脚本优先使用正常安装的 npm，找不到时使用当前项目已准备的 `.deps/npm` 和可用的 Node.js。脚本不会自动下载依赖；新服务器应安装标准 Node.js（包含 npm）。依赖需要安装时，显式执行 `-Action Install`，它按锁定文件运行 `npm ci`。

开发验证使用 `.\scripts\frontend.ps1 -Action Test` 运行前端组件与数据处理测试，`-Action Build` 同时执行 TypeScript 检查。测试复用现有 TypeScript 和 React，不添加大型依赖，不需要在客户端安装任何环境。

### 开发时分别启动前后端

第一个终端：

```powershell
.\scripts\start-backend.ps1 -Reload
```

第二个终端，仍在项目根目录：

```powershell
.\scripts\frontend.ps1 -Action Dev
```

打开 [开发网页](http://127.0.0.1:5173)。开发服务器只监听本机，`/api` 转发到 `127.0.0.1:8000`；5173 被占用时会报错，不会自动换端口。

### 检查连接与重启

网页首次打开会显示“正在检查连接…”，只在 HTTP 200 且响应 `status` 为 `ok` 时显示“服务器已连接”。停止后端后，点击“重新检查”应显示“服务器连接失败”；重启后端后，点击“重试连接”应恢复。检查仅在首次打开和手动点击时执行，不轮询或刷新整个页面。

也可以用另一个 PowerShell 终端检查后端：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/health'
```

`/api/ready` 与存活检查分开：即使它因数据库未就绪返回 503，`/api/health` 仍应返回 200。开发或新服务器应按后面的初始化说明准备数据库；当前演示数据库与 `.env` 中的正式数据目录分开。详细步骤见 [分阶段验收清单](docs/ACCEPTANCE.md)。

## 已有业务原型（后续阶段参考）

- 登录与管理员、登记员、科室医生、总检医生权限。
- 受检者登记、查询、分页；选择套餐创建一次体检。
- 医生查看本科室待检人员、填写结果和异常标记。
- 修改结果保留历史，检测多人修改冲突。
- 总检汇总、完成后生成报告网页快照；重新打开时保留原报告版本。
- 重要操作审计，受检者软删除；有有效体检时禁止删除。
- 网络或服务器错误提示；服务器提交成功后才显示保存成功。
- 数据库与私有文件备份脚本、Windows 自动启动配置模板。

当前是供验证流程的基础版本。科室、检查项目和套餐管理已完成对应阶段验收；收费、PDF、仪器接口等留待后续。正式使用前需要现场验证老电脑、权限、备份恢复、开机启动和 10～50 台客户端并发，暂不进入真实医疗业务。

## 服务器依赖准备

此节用于尚未准备环境的新服务器，已有环境无需重复创建。

准备已安装的 Python 3.12 或 3.13、Node.js 22，以及 PowerShell。Node.js 只在构建前端时使用；网页打包完成后，日常运行不需要启动 Node.js。示例默认在项目根目录执行，路径有空格或中文时保留引号。

### 1. 建立 Python 环境并安装后端依赖

```powershell
Set-Location 'C:\Users\Administrator\Documents\ChatGPT\体检'
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
if (-not (Test-Path -LiteralPath .\.env)) {
    Copy-Item -LiteralPath .\.env.example -Destination .\.env
}
```

如果安装的是 Python 3.12，将第一条 Python 命令改为 `py -3.12`。已经有 `.env` 时不要覆盖，先查看里面的配置。`.env` 保存数据库连接等配置，不要提交到 Git 或发给其他人。默认 SQLite 文件固定在项目根 `data/healthcheck.db`；从 `backend` 目录执行命令也使用同一个数据库。

### 2. 安装前端依赖并构建网页

```powershell
.\scripts\frontend.ps1 -Action Install
.\scripts\frontend.ps1 -Action Build
```

打包文件保存在 `frontend/dist`。后端直接提供这些静态文件，日常运行只需一个后端进程。修改前端后重新执行构建；构建完成后重启后端并核对网页。

如果 Windows 阻止执行本项目脚本，查看当前执行策略并遵循服务器管理规定。允许执行本地脚本的开发电脑可在当前 PowerShell 窗口运行 `Set-ExecutionPolicy -Scope Process RemoteSigned`；窗口关闭后设置失效。

## 数据库初始化与业务原型使用参考

以下初始化步骤用于新的服务器或独立演示环境。已有数据库和账号无需重复初始化；完整体检流程保留为后续业务阶段参考。

### 初始化数据库及演示账号

```powershell
Push-Location .\backend
..\.venv\Scripts\python.exe -m app.cli init --username admin --demo
Pop-Location
```

命令会要求输入并确认至少 12 个字符的密码，再执行数据库结构迁移和初始化。密码由你本人设置，输入时不显示字符，没有默认密码。

`--demo` 建立演示科室、套餐和以下账号；它们在首次初始化时使用刚输入的演示密码：

| 账号 | 用途 |
| --- | --- |
| `admin` | 管理员 |
| `frontdesk` | 前台登记 |
| `doctor_internal` | 内科医生 |
| `doctor_lab` | 检验科医生 |
| `reviewer` | 总检医生 |

这些账号只用于演示。正式环境应按人员建立账号、分配角色和科室，并分别设置密码。重复初始化不会覆盖已有账号密码，不要把再次执行初始化当成重置密码。

账号维护在服务器上执行，密码均交互输入，例如：

```powershell
Push-Location .\backend
..\.venv\Scripts\python.exe -m app.cli create-user --username registrar01 --display-name '登记员张' --role registrar
..\.venv\Scripts\python.exe -m app.cli set-password --username frontdesk
Pop-Location
```

医生账号还需要 `--department-id` 指定实际科室编号。重置密码会撤销该账号已有登录会话。

### 演示完整流程

1. 用 `frontdesk` 登记一名虚构受检者，选择演示套餐创建体检。
2. 用 `doctor_internal` 和 `doctor_lab` 分别录入本科室结果，选取一项异常结果。
3. 用 `reviewer` 查看全部项目，填写总检结论并完成总检。
4. 查看报告网页；重新打开体检后修改结果、再次总检，验证修改历史和报告版本。
5. 在输入结果时短暂断开服务器，验证界面保留输入、明确提示保存失败。恢复连接后重试并核对服务器结果。

用不同浏览器或隐私窗口模拟不同人员；同一个浏览器会话共用登录 Cookie，不要用同一会话的两个标签页测试两个人。

### 局域网演示

假设服务器 IP 是 `192.168.1.100`，先在根 `.env` 的 `ALLOWED_ORIGINS` 数组追加 `http://192.168.1.100:8000`，随后运行：

```powershell
.\scripts\start.ps1 -Lan
```

科室电脑打开 [局域网演示网页](http://192.168.1.100:8000)。Windows 防火墙只允许体检中心局域网访问 8000，路由器不配置互联网端口转发。这种 HTTP 演示使用虚构数据。真实医疗数据采用下方正式 HTTPS 部署方案。

Windows 7 浏览器验证目标为 Chrome 109，前端构建目标也设为 Chrome 109；现场仍需实际测试。IE 不在本版支持范围。服务器和客户端不需要使用相同 Windows 版本，服务器采用仍受维护的 Windows 系统。

## 后续备份和正式部署参考

手动备份命令：

```powershell
.\scripts\run-backup.ps1
```

默认备份至项目 `backups`，输出日志至 `logs/backup-日期.log`。每次创建独立日期目录，含数据库、`reports` 私有文件、`manifest.json` 校验清单和 `RESTORE.md` 恢复说明。脚本不自动清理旧备份。网页报告快照目前在数据库中，`data/reports` 为将来私有文件预留，不能放到公开静态目录。

外置硬盘备份例子：

```powershell
.\scripts\run-backup.ps1 -ExternalDirectory 'E:\HealthcheckBackups'
```

正式部署使用 PostgreSQL、Caddy HTTPS 和 Windows 服务，科室电脑仅访问服务器网页入口。服务模板与每日备份任务脚本已提供，但没有在当前电脑安装或注册。完整配置、恢复演练、升级步骤见 [部署说明](docs/DEPLOYMENT.md)。

## 目录

```text
backend/             API、权限、数据库模型和迁移
frontend/            React 网页源代码
frontend/dist/       构建后的网页（不提交 Git）
scripts/             启动、备份和备份任务模板
deploy/              Caddy 与 WinSW 服务配置模板
docs/API.md          前后端接口约定
docs/DEPLOYMENT.md   Windows 部署与恢复说明
data/                开发数据库和私有文件（不提交 Git）
logs/                运行及备份日志（不提交 Git）
backups/             本机备份（不提交 Git）
.env                 私有配置（不提交 Git）
.deps/npm/           当前开发电脑的 npm 后备入口（不提交 Git）
```
