# API 约定

第 1～8 阶段及补充完整前台登记已通过检查，实际结果见 [受检者管理验收记录](PHASE_5.md)、[原体检登记验收](PHASE_6.md)、[完整前台登记验收](PHASE_6_COMPLETE.md)、[科室和检查项目验收](PHASE_7.md) 和 [体检套餐验收](PHASE_8.md)。第 9 阶段医生工作台已获授权，正在开发和验收，本轮不进入第 10 阶段。

所有路径以 `/api` 开头。日期使用 ISO 格式。登录采用 HttpOnly 会话 Cookie；登录和 `/auth/me` 返回 `csrf_token`，除登录之外的写请求必须带 `X-CSRF-Token`。错误响应为 `{ "detail": "中文说明" }`，列表为 `{ "items": [], "total": 0, "page": 1, "page_size": 20 }`。

角色：`admin` 管理员、`registrar` 登记员、`doctor` 科室医生、`reviewer` 总检医生。User：`id, username, display_name, role, department_id`。

- `GET /health`：无登录、无数据库依赖的后端存活检查；HTTP 200，响应 `{"status":"ok"}`。此结果不证明数据库或业务就绪。
- `GET /ready`：数据库就绪检查；执行连接查询及 `users` 表查询，成功时 HTTP 200、响应 `{"status":"ok"}`；连接失败或表尚未初始化时 HTTP 503、响应 `{"detail":"数据库尚未就绪，请检查数据库服务和初始化状态"}`。不创建表、不迁移、不执行业务写入；不用于全面验证业务表或迁移版本。SQLite 首次连接仍可能由驱动生成空库或 WAL 文件。
- `POST /auth/login`：`{username,password}` → `{user,csrf_token}`。
- `GET /auth/me` → `{user,csrf_token}`；`POST /auth/logout` → `{message}`。
- `GET /users?page=1&page_size=20&q=` → 管理员专用的账号分页列表，按账号或工作人员姓名搜索。UserSummary：`id,username,display_name,role,department_id,is_active`，不返回密码哈希及会话信息。
- `GET /users/{id}` → UserSummary；管理员可读取停用账号，不存在返回 404。非管理员统一返回 403。
- `POST /users`：`{username,display_name,password,role,department_id}` → HTTP 201、UserSummary。管理员创建工作人员账号；用户名 3～64 位 ASCII 字母、数字、点、下划线或连字符，区分大小写；密码至少 12 字符，保留首尾空白，哈希保存。医生必须指定有效科室，其他角色科室必须为 null。
- `PUT /users/{id}`：`{display_name,role,department_id,is_active,expected:{display_name,role,department_id,is_active}}` → UserSummary。管理员修改显示名、角色、科室或启用状态；账号名不可修改。expected 必须与服务器最新资料一致，否则 409，前端保留输入供核对。不能停用本人或撤销本人的管理员权限，必须保留启用的管理员。角色、科室或状态变化会在同一事务撤销该账号全部会话并记录审计；仅修改显示名不撤销会话。并发管理使用共同数据库写锁，取得锁后重新验证管理员会话。
- `GET /departments` → `[{id,name,is_active}]`，保留停用科室供已有账号和历史名称显示；新增医生分配只能使用启用科室。
- `GET /packages` → `[{id,name,description,items:[{id,name,department_id,department_name,category,unit,reference_range,result_type,abnormal_allowed}]}]`。只提供有项目且全部项目、科室启用的有效套餐。包含停用成员时隐藏整个套餐，不静默删减成员；创建接口同样严格校验。
- `GET /patients?page=1&page_size=20&q=` → 分页 PatientRecord。默认每页 20，上限 100；仅查询未归档档案，`q` 按姓名、手机号或身份证号在服务器搜索。管理员、登记员可访问。
- `POST /patients`：`{name,sex,birth_date,phone,identity_number,employer,ethnicity,address,note,request_id}` → PatientRecord。姓名、性别和请求编号必填，其余资料选填，可为 null；相同请求编号、账号、内容的重试返回同一档案，内容不同或账号不同返回 409。身份证号去除首尾空白并转大写，非空时为 15 位数字，或前 17 位数字加末位数字/X；未归档档案中唯一，不强制证件号与生日、性别对应。民族最多 50 字符、地址最多 500 字符。管理员、登记员可访问。
- `GET /patients/{id}` → PatientRecord。读取受检者完整档案；仅管理员、登记员可访问。
- `PUT /patients/{id}`：`{name,sex,birth_date,phone,identity_number,employer,ethnicity,address,note,version}` → PatientRecord。`version` 必须与服务器一致；保存后版本递增，旧版本返回 409，前端保留输入供核对。民族、地址省略时保留现值以兼容旧页面，明确提交 null 时清空；其他资料沿用完整提交规则。身份证格式和未归档唯一规则同新增。仅管理员、登记员可访问。
- `GET /patients/{id}/exams?page=1&page_size=20` → 分页 ExamSummary。仅管理员、登记员可访问；只返回体检编号、套餐、日期和进度等摘要，不返回检查结果、总检结论或报告内容。
- `POST /patients/{id}/archive`：`{reason,version}` → `{message}`。管理员执行软删除，界面带当前版本；`version` 可省略以兼容旧原型，提供时必须匹配。已有有效体检时拒绝归档；归档后可用同一证件号建立新档案。归档、编辑、创建体检使用共同数据库写锁，避免检查条件后被并发操作改变。
- `POST /exams`：`{patient_id,package_id,package_version,exam_date,exam_type,note,request_id}` → ExamDetail。管理员、登记员为未归档受检者创建体检。patient_id、request_id 必填；package_id 可为空，不选套餐时 package_name 为“未选择套餐”、items 为空，状态仍为待检且不能总检。选择套餐时必须有效且包含项目。exam_type 为个人体检、单位体检、入职体检、其他，默认个人体检。exam_date、note 可省略或为 null。体检日期为 YYYY-MM-DD，不早于 1900 年；为空时，首次创建使用服务器中国日期（UTC+8）。登记备注去除首尾空白，空值归一为 null，最多 2000 字符。
- `GET /exams?page=1&page_size=20&q=&status=&department_id=` → 分页 ExamSummary。医生始终限本科室；默认仅返回本科室尚未完成项目的体检。其他角色默认返回全部。
- `GET /exams/{id}` → ExamDetail。医生只能访问含本科室项目的体检，返回的 items 也只能包含本科室；登记员只能看到项目状态，结果内容隐藏。
- `PUT /exam-items/{id}`：`{value,is_abnormal,note,version}` → ExamItem。管理员或对应科室医生执行，value 必填，version 必须匹配；已总检体检禁止直接修改。
- `POST /exams/{id}/finalize`：`{conclusion,version}` → ExamDetail。管理员、总检医生执行；全部项目完成才允许，生成不可变报告快照。
- `POST /exams/{id}/reopen`：`{reason,version}` → ExamDetail。管理员、总检医生执行；保留原报告并解锁结果修改，之后再次总检生成新版本报告。
- `GET /exams/{id}/report` → Report。管理员、总检医生可访问；已重新打开的体检暂不能作为当前报告查看。
- `GET /exam-items/{id}/history` → `[{id,version,value,is_abnormal,note,changed_by,changed_at}]`。管理员、总检医生或本科室医生可访问。

PatientRecord：`id,name,sex,birth_date,age,phone,identity_number,employer,ethnicity,address,note,created_at,updated_at,version`。sex 为 `male/female/other`；日期为 YYYY-MM-DD 或 null；年龄由出生日期按中国当前日期在服务器计算，生日未知时为 null。创建时 version 为 1；时间与年龄不可由浏览器提交，编辑仅附当前版本。

受检者档案变更不覆盖已有体检中的受检者快照，也不改写已生成报告。受检者档案的身份证、单位、民族、地址、备注不进入体检或报告响应。档案新增及编辑审计记录字段名、版本，不复制证件号、手机、地址或备注内容；归档原因保存在受保护的审计表中，不进入普通运行日志。

ExamSummary：`id,exam_number,patient:{id,name,sex,birth_date,phone},package_name,exam_date,exam_type,status,created_at,completed_count,total_count,version`。exam_date 是体检日期；created_at 是实际登记时间，两者分别显示。旧记录日期迁移按原 created_at 加 8 小时回填中国日期，新增类型为“个人体检”；保留原编号、检查结果和报告快照。

ExamDetail：在 ExamSummary 上增加 `items,conclusion,completed_at,latest_report_revision`。仅管理员、登记员详情响应另包含登记备注 `note`（可为 null）；医生、总检医生响应及列表、报告响应不包含该字段。登记备注与每个检查项目的 note 是不同字段。

ExamItem：`id,item_name,department_id,department_name,category,unit,reference_range,result_type,abnormal_allowed,value,is_abnormal,note,status,version`；status 为 `pending/completed`。名称、科室、分类、单位、参考范围、结果类型和异常标记能力为本次体检创建时的快照；基础项修改不覆盖它。旧体检新增元数据默认分类为空、类型 text、允许异常标记；旧报告 JSON 保持原样，页面兼容缺少新增字段。

Exam status：`registered/in_progress/ready_for_review/completed`，界面依次显示“待检 / 体检中 / 待总检 / 已完成”。创建体检时由服务器设置 registered，之后沿用已有检查、总检流程更新；登记请求不能指定或直接修改状态。

Report：`exam_id,exam_number,revision,issued_at,patient,package_name,items,conclusion,reviewer_name`。

分页 page_size 上限 100。姓名/体检编号搜索由服务器执行。禁止公共静态目录提供报告。成功响应必须在结果、修改历史、审计及状态更新的事务提交之后发送；版本冲突返回 409，权限不足 403，登录失效 401。

## 第 6 阶段：创建体检与稳定重试

request_id 由浏览器为一次登记生成并在重试时保持不变。已有编号属于同一账号、受检者、套餐，且登记备注一致时，重试返回同一体检，不再生成项目或重复写创建审计；明确提交体检日期时，还必须与已保存日期一致。日期为 null 的重试复用第一次保存的日期，跨午夜也不会生成第二条。请求编号被用于其他账号或内容时返回 409，不能借此读取其他账号的登记结果。

体检编号由服务器统一生成，默认如 `20261002-0001`，编号日期来自实际创建时的中国日期。规则通过服务器配置控制：`EXAM_NUMBER_MODE=date_sequence|random`、`EXAM_NUMBER_PREFIX`、`EXAM_NUMBER_DATE_FORMAT`、`EXAM_NUMBER_WIDTH`、`EXAM_NUMBER_SEPARATOR`；不会改写旧编号。每日计数与登记在同一事务，数据库唯一约束保证编号和请求编号不重复。编号冲突在事务保存点中最多尝试 3 次，耗尽后返回 503，不保留部分体检、项目或创建审计；其他数据库写入错误不能被当成成功重试。体检、套餐项目快照和审计在同一事务提交。

前端收到网络错误、服务器错误、正文解析失败，或成功响应缺少有效体检 id/编号时，保留并锁定原登记资料与 request_id，按原请求重试，直到服务器明确确认结果。它不能仅凭 HTTP 成功状态显示登记成功，也不能在结果尚未确认时以新请求编号再次登记。本阶段不提供新增套餐、修改检查项目或手工切换体检状态的接口。

## 第 6 阶段补充：一次确认完成前台登记

- `GET /registration/options` → `{today,exam_types}`，仅管理员、登记员；today 为服务器中国日期，exam_types 为上述四类。
- `POST /patients/lookup-identity`：`{identity_number}` → `{patient:PatientRecord|null}`，管理员、登记员按规范化身份证号精确匹配。证件放在请求正文，禁止使用 URL 查询参数传递。仅有归档档案时返回 409，提示管理员核对；审计只记录是否找到和档案 ID。
- `POST /registrations`：`{patient:{name,sex,birth_date,identity_number,ethnicity,address,phone,employer,note},patient_id,patient_version,exam_date,exam_type,note,package_id,package_version,request_id}` → `{patient:PatientRecord,exam:ExamDetail,reused_patient:boolean}`。patient_id、patient_version 可为空，指定时必须成对且匹配有效档案和版本。外层 note 为本次体检备注，patient.note 为人员档案备注。

服务器在一个事务中查找或新建人员、创建本次体检和项目快照、保存请求指纹与操作审计。相同身份证精确复用有效档案；姓名、性别、生日不一致时返回 409。不会因为刷卡自动修改已保存档案的民族、地址等资料。没有身份证且没有选定已有人员时，新建人员；不能仅凭姓名自动合并。

同一 request_id、账号、规范化请求内容重试返回原人员 ID 和本次体检，不重复登记；其他内容或账号返回 409。请求表只保存输入指纹与关联 ID，不复制明文身份证或地址。新人员、体检、计数、请求记录、项目及审计一起提交或回滚，成功响应在提交之后发送。既有 `/patients`、`/exams` 接口继续保留供档案管理和已有人员使用。

## 第 7 阶段：科室和基础检查项目

以下管理路径仅管理员可用，普通医生、总检医生和登记员直接调用也返回 403，匿名返回 401。数据库保留原行，使用启停状态，不提供硬删除接口。

- `GET /admin/departments?page=1&page_size=20&q=&is_active=` → 分页 DepartmentRecord；q 按科室名称搜索，is_active 省略为全部、true 为启用、false 为停用。
- `GET /admin/departments/{id}` → DepartmentRecord；不存在 404。
- `POST /admin/departments`：`{name,request_id}` → DepartmentRecord，初始启用，名称 1～100 字符，重复名称 409。
- `PUT /admin/departments/{id}`：`{name,is_active,version}` → DepartmentRecord。版本必须匹配；有变更版本加 1，无变更保持原版本；旧版本 409。
- `GET /admin/catalog-items?page=1&page_size=20&q=&department_id=&is_active=` → 分页 CatalogRecord；q 按项目名、分类或科室名搜索，department_id 按科室 ID 筛选。
- `GET /admin/catalog-items/{id}` → CatalogRecord；不存在 404。
- `POST /admin/catalog-items`：`{name,department_id,category,unit,reference_range,result_type,abnormal_allowed,is_active,request_id}` → CatalogRecord。
- `PUT /admin/catalog-items/{id}`：相同基础字段加 `version`、不传 request_id → CatalogRecord。匹配版本后修改，有变更加 1，否则保持原版本。

DepartmentRecord：`id,name,is_active,version`。CatalogRecord：`id,name,department_id,department_name,department_is_active,category,unit,reference_range,result_type,abnormal_allowed,is_active,version`。分类为最多 100 字符的说明文字；项目名称最多 100、单位最多 50、参考范围最多 500 字符。可选分类、单位、范围使用空字符串；null 不符合该接口约定。result_type 为 text（文本）、number（数字）、boolean（是/否），默认 text；异常能力和启用状态为严格布尔值，默认 true。

创建请求编号按账号、种类和规范化内容校验。原创建请求重试只返回同一条配置的当前内容、版本，不重复创建；同编号用于其他账号或内容返回 409。配置、请求关联和审计一起提交。更新错误不覆盖已保存资料；响应不确定时，前端保持原提交重试或读取服务器结果核对。

基础配置修改、停用和两类体检登记入口使用相同数据库实际写锁，取得锁后重新验证会话及角色。停用科室不删除原医生所属，已有医生可处理历史检查；新增或变更分配到停用科室拒绝。停用科室不能新增关联项目；既有项目仍可维护，已停用项目不能在停用科室下重新启用。

科室或项目停用后禁止用于新体检，既有 ExamItem 和报告快照保持原值。录入结果按 ExamItem 的快照类型校验：数字接受有限 ASCII 数值，拒绝 NaN/Infinity；boolean 只接受“是”或“否”；禁止异常能力的项目不能提交 is_abnormal=true。参考范围不用于自动判断异常。

## 第 8 阶段：体检套餐

PackageSummary：`id,name,description,is_active,version,item_count,is_available,unavailable_reason`。不可用于登记的原因包括套餐停用、没有项目或包含停用项目/科室；可用时 unavailable_reason 为空字符串。PackageDetail 在摘要上增加排序的 `item_ids` 及当前 `items:CatalogRecord[]`。列表不携带整套项目，详情按需读取。

- `GET /admin/packages?page=1&page_size=20&q=&is_active=` → Page<PackageSummary>。仅管理员，默认 20、最多 100；q 最多 100 字符，按名称和说明服务端查询，is_active 为 true/false 或省略。
- `GET /admin/packages/{id}` → PackageDetail。仅管理员，包含停用套餐及失效成员供核对、维护。
- `POST /admin/packages`：`{name,description,item_ids,is_active,request_id}` → PackageDetail。name 最多 100 字符且必填、唯一；description 为字符串，默认空字符串，最多 2000 字符，不能为 null；item_ids 为 1～200 个严格正整数，拒绝重复、布尔值及无效项目，规范化为按 ID 排序。is_active 严格布尔值，默认 true。新关联要求项目及科室均启用。
- `PUT /admin/packages/{id}`：`{name,description,item_ids,is_active,version}` → PackageDetail。仅管理员，版本不符返回 409；字段或关联变化时版本加一，无变化保持原版本。原失效成员可保留用于维护元资料、停用或移除；停用套餐重新启用时所有成员必须有效。旧空套餐可维护、停用；原非空套餐仅在同时停用时允许移除全部成员，不能新造启用空套餐或重新启用空套餐。
- `GET /registration/packages?page=1&page_size=20&q=` → Page<PackageSummary>。仅管理员、登记员；服务器先过滤有效套餐，再查询、计数、分页，规则同管理分页。当前登记页面使用此接口，保留旧 `/packages` 数组合同作兼容。
- `GET /registration/packages/{id}` → PackageDetail。仅管理员、登记员；套餐不存在返回 404，已停用或成员失效返回 422，前端保留人员及本次体检输入供重新选择。

套餐创建按 request_id、账号及规范化内容幂等，重试返回同一套餐的当前资料和版本，后续改名、改项目或停用不导致重复新增；账号或内容不同返回 409。套餐、项目关联、请求记录及审计同事务提交。关联联合主键继续防止重复，更新不会改写任何已有 Exam、ExamItem、ResultRevision 或 ReportSnapshot。

两个创建入口 `POST /exams` 和 `POST /registrations` 均增加可选 `package_version`。提供版本时必须同时选择 package_id；新请求在共同锁内读取套餐，先核对版本，再检查可用性，套餐已变化返回 409 提示重新读取。当前登记页面提交已核对版本。旧客户端可省略版本；已提交登记先按原请求重放，不会因之后套餐修改或停用而重新创建。完整登记在版本为空时保留升级前的请求指纹规则。

关联检查项目或其科室的实际配置变更，也会在同一事务递增相关套餐版本；同一科室包含多个成员时，一个套餐只递增一次，无变化操作不递增。已保存体检中的旧版本及项目快照不随之改变。

登记响应不确定后，原请求重试若收到服务器明确的套餐版本变化 409，表示原旧版本今后也不能创建新体检。前端保留人员、日期、类型、备注和套餐选择，解除原提交锁并要求重新读取核对，再使用新请求登记。其他请求编号冲突、网络错误或服务器错误不按此处理，仍保留原请求核对，防止重复创建。

Exam 摘要和详情增加 `package_id,package_version`：新建有套餐体检保存当时的套餐版本；无套餐及升级前旧体检的版本为 null。项目在创建时复制为独立快照，关联本次 exam_id；后续修改套餐关联只改变未来新登记的项目集合。

## 当前角色范围

| 操作 | 管理员 | 前台（registrar） | 科室医生 | 总检医生 |
| --- | --- | --- | --- | --- |
| 账号列表、新增、修改、停用 | 允许 | 拒绝 | 拒绝 | 拒绝 |
| 科室及基础检查项目管理 | 允许 | 拒绝 | 拒绝 | 拒绝 |
| 套餐管理 | 允许 | 拒绝 | 拒绝 | 拒绝 |
| 登记套餐分页选择及详情 | 允许 | 允许 | 拒绝 | 拒绝 |
| 受检者新增、编辑、详情、搜索与历史摘要 | 允许 | 允许 | 拒绝 | 拒绝 |
| 受检者归档 | 允许，无有效体检 | 拒绝 | 拒绝 | 拒绝 |
| 创建体检 | 允许 | 允许 | 拒绝 | 拒绝 |
| 查看体检登记备注 | 仅详情 | 仅详情 | 拒绝 | 拒绝 |
| 查看体检进度 | 全部 | 全部，检查结果内容隐藏 | 仅本科室 | 全部 |
| 录入检查结果 | 允许 | 拒绝 | 仅本科室 | 拒绝 |
| 查看结果修改历史 | 全部 | 拒绝 | 仅本科室 | 全部 |
| 总检、重新打开及报告 | 允许 | 拒绝 | 拒绝 | 允许 |

权限由后端逐请求验证，前端同时隐藏无权限入口。手工输入接口地址也不能绕过权限。数据库中未知角色拒绝登录和业务访问。
