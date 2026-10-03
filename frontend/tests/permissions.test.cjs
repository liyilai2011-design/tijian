const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test, after } = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');

// Render the production menu component with the existing TypeScript runtime.
// No DOM emulator, browser installation or additional front-end dependency.
const sourcePath = path.resolve(__dirname, '../src/components/RoleNavigation.tsx');
const cacheDirectory = path.resolve(__dirname, '../node_modules/.cache/permission-tests');
fs.mkdirSync(cacheDirectory, { recursive: true });
const compiledPaths = [];
const compiledBySource = new Map();
function loadComponent(source) {
  source = path.resolve(source);
  if (compiledBySource.has(source)) return require(compiledBySource.get(source));
  const compiledPath = path.join(cacheDirectory, `component-${process.pid}-${compiledPaths.length}.cjs`);
  compiledPaths.push(compiledPath); compiledBySource.set(source, compiledPath);
  const output = ts.transpileModule(fs.readFileSync(source, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText.replace(/require\("(\.[^"]+)"\)/g, (match, relativePath) => {
    if (relativePath.endsWith('.css')) return 'void 0'; // Static HTML checks do not execute stylesheet imports.
    const base = path.resolve(path.dirname(source), relativePath);
    const dependency = [`${base}.ts`, `${base}.tsx`].find(candidate => fs.existsSync(candidate));
    if (!dependency) return match;
    loadComponent(dependency);
    return `require(${JSON.stringify(compiledBySource.get(dependency))})`;
  });
  fs.writeFileSync(compiledPath, output);
  return require(compiledPath);
}
const { RoleNavigation } = loadComponent(sourcePath);
const { previewAge, patientPayload, blankPatient, PatientFields } = loadComponent(path.resolve(__dirname, '../src/components/PatientFields.tsx'));
const { blankExamRegistration, canRegisterWithPackage, examRegistrationPayload, confirmedExamIdentity, ExamRegistrationFields, packageSelectionReady, recoverPackageVersionConflict } = loadComponent(path.resolve(__dirname, '../src/components/ExamRegistrationFields.tsx'));
const { api, ApiError } = loadComponent(path.resolve(__dirname, '../src/api.ts'));
const { identityReader, simulatedIdentityReader } = loadComponent(path.resolve(__dirname, '../src/identity-reader.ts'));
const { registrationPayload, confirmedRegistration, identityDifferences, RegistrationForm } = loadComponent(path.resolve(__dirname, '../src/components/RegistrationForm.tsx'));
const { blankDefinition, canSubmitDefinition, catalogPayload, departmentPayload, departmentCreatePayload, confirmedDefinition, confirmedDefinitionSave, definitionMatches, DefinitionFields, availableDepartments } = loadComponent(path.resolve(__dirname, '../src/components/DefinitionFields.tsx'));
const { resultTypeFor, validResultValue, ResultValueControl, AbnormalControl } = loadComponent(path.resolve(__dirname, '../src/components/ResultValueControl.tsx'));
const { blankPackage, packagePayload, packageDraft, confirmedManagedPackage, confirmedPackageSave, packageMatches, canSubmitPackage, PackageFields } = loadComponent(path.resolve(__dirname, '../src/components/PackageFields.tsx'));
const { PackagePicker, confirmedPackageSelection } = loadComponent(path.resolve(__dirname, '../src/components/PackagePicker.tsx'));
const { togglePackageItem, SelectedPackageItems } = loadComponent(path.resolve(__dirname, '../src/components/PackageItemSelector.tsx'));
const { Pagination } = loadComponent(path.resolve(__dirname, '../src/ui.tsx'));
after(() => compiledPaths.forEach(file => fs.unlinkSync(file)));

const allowedButtons = {
  admin: ['受检者登记', '体检管理', '科室和项目', '套餐管理', '账号管理'],
  registrar: ['受检者登记', '体检管理'],
  doctor: ['本科室待检'],
  reviewer: ['总检工作台'],
};
for (const [role, expected] of Object.entries(allowedButtons)) {
  test(`${role}: only permitted menu entries are rendered`, () => {
    const markup = renderToStaticMarkup(React.createElement(RoleNavigation, { role, page: 'exams', onNavigate() {} }));
    const rendered = [...markup.matchAll(/<button[^>]*>([^<]+)<\/button>/g)].map(match => match[1]);
    assert.deepEqual(rendered, expected);
  });
}
test('age preview respects birthdays, leap days and invalid dates', () => {
  assert.equal(previewAge('2000-10-03', '2026-10-02'), 25);
  assert.equal(previewAge('2000-10-02', '2026-10-02'), 26);
  assert.equal(previewAge('2026-10-02', '2026-10-02'), 0);
  assert.equal(previewAge('2000-02-29', '2024-02-28'), 23);
  assert.equal(previewAge('2000-02-29', '2024-02-29'), 24);
  assert.equal(previewAge('2000-02-29', '2025-02-28'), 24);
  assert.equal(previewAge('2000-02-29', '2025-03-01'), 25);
  for (const invalid of ['', 'invalid', '1899-12-31', '2026-10-03', '2025-02-29', '2026-13-01']) {
    assert.equal(previewAge(invalid, '2026-10-02'), null);
  }
});
test('patient requests normalize optional text and do not send a stored age', () => {
  const normalized = patientPayload({ ...blankPatient(), name: ' 测试档案 ', identity_number: '00000020000101001x', employer: ' 测试单位 ', note: ' 备注 ', phone: '   ' });
  assert.deepEqual(normalized, { name: '测试档案', sex: 'male', birth_date: null, phone: null, identity_number: '00000020000101001X', ethnicity: null, address: null, employer: '测试单位', note: '备注' });
  assert.equal(Object.hasOwn(normalized, 'age'), false);
});
test('all patient input controls are disabled during saving', () => {
  const markup = renderToStaticMarkup(React.createElement(PatientFields, { draft: blankPatient(), onChange() {}, disabled: true }));
  const controls = [...markup.matchAll(/<(input|select|textarea)\b[^>]*>/g)].map(match => match[0]);
  assert.equal(controls.length, 9);
  assert.ok(controls.every(control => /\bdisabled=""/.test(control)));
});

test('registration sends optional date and note without a writable status', () => {
  assert.deepEqual(examRegistrationPayload({ ...blankExamRegistration(), packageId: '1', packageVersion: 3 }), { package_id: 1, package_version: 3, exam_date: null, exam_type: '个人体检', note: null });
  assert.deepEqual(examRegistrationPayload({ ...blankExamRegistration(), packageId: '2', packageVersion: 4, examDate: '2026-10-03', note: ' 本次登记备注 ' }), { package_id: 2, package_version: 4, exam_date: '2026-10-03', exam_type: '个人体检', note: '本次登记备注' });
  assert.equal(examRegistrationPayload(blankExamRegistration('2026-10-02')).package_id, null);
});
test('unconfirmed registration cannot be mistaken for a saved exam', () => {
  assert.equal(confirmedExamIdentity({ id: 1, exam_number: 'TJ20261002TEST' }), true);
  for (const result of [null, {}, { id: 0, exam_number: 'TJTEST' }, { id: 1, exam_number: '' }, { id: '1', exam_number: 'TJTEST' }]) {
    assert.equal(confirmedExamIdentity(result), false);
  }
  const markup = renderToStaticMarkup(React.createElement(ExamRegistrationFields, { draft: blankExamRegistration(), onChange() {}, disabled: true }));
  const controls = [...markup.matchAll(/<(input|select|textarea)\b[^>]*>/g)].map(match => match[0]);
  assert.equal(controls.length, 3);
  assert.ok(controls.every(control => /\bdisabled=""/.test(control)));
});
test('real-reader fallback cannot silently return simulated identities', async () => {
  await assert.rejects(identityReader.read(), /尚未接入.*手工/);
  const first = await simulatedIdentityReader.read(), second = await simulatedIdentityReader.read();
  assert.deepEqual(first, second);
  assert.ok(first.name.includes('（虚构）'));
  assert.equal(first.identity_number.slice(0, 6), '000000');
  assert.equal(previewAge(first.birth_date, '2026-10-02'), 36);
  assert.equal(first.ethnicity, '汉');
  assert.ok(first.address.includes('虚构'));
});
test('combined registration normalizes personnel and includes an independent exam', () => {
  const draft = { ...blankPatient(), name: ' 软件测试 ', ethnicity: ' 汉 ', address: ' 虚构地址 ' };
  const fields = registrationPayload(draft, { ...blankExamRegistration('2026-10-02'), examType: '入职体检', note: ' 本次备注 ' }, null);
  assert.equal(fields.patient.name, '软件测试');
  assert.equal(fields.patient.ethnicity, '汉');
  assert.equal(fields.patient.address, '虚构地址');
  assert.equal(fields.patient_id, null);
  assert.equal(fields.patient_version, null);
  assert.equal(fields.exam_date, '2026-10-02');
  assert.equal(fields.exam_type, '入职体检');
  assert.equal(fields.note, '本次备注');
  assert.equal(fields.patient.note, null);
  assert.equal(fields.package_id, null);
  assert.equal(Object.hasOwn(fields.patient, 'age'), false);
  const matched = registrationPayload(draft, blankExamRegistration(), { id: 42, version: 3 });
  assert.equal(matched.patient_id, 42);
  assert.equal(matched.patient_version, 3);
});
test('combined registration succeeds only after both related records are confirmed', () => {
  const saved = { patient: { id: 2, version: 1 }, exam: { id: 8, exam_number: '20261002-0001', patient: { id: 2 } }, reused_patient: false };
  assert.equal(confirmedRegistration(saved), true);
  for (const invalid of [null, {}, { ...saved, exam: null }, { ...saved, patient: { id: 0, version: 1 } },
    { ...saved, patient: { id: 2, version: 0 } }, { ...saved, exam: { ...saved.exam, patient: { id: 4 } } },
    { ...saved, exam: { ...saved.exam, exam_number: '' } }, { ...saved, reused_patient: undefined }]) {
    assert.equal(confirmedRegistration(invalid), false);
  }
});
test('identity differences name exactly the fields that require operator verification', () => {
  const card = { ...blankPatient(), name: '读卡名字', sex: 'female', birth_date: '1990-01-01' };
  assert.deepEqual(identityDifferences(card, { name: '旧档案名字', sex: 'male', birth_date: '1991-01-01' }), ['姓名', '性别', '出生日期']);
  assert.deepEqual(identityDifferences(card, card), []);
  assert.deepEqual(identityDifferences(blankPatient(), { name: '服务器资料', sex: 'female', birth_date: '1990-01-01' }), []);
});
test('registration keeps manual input, reader guidance, optional fields and exam controls together', () => {
  const markup = renderToStaticMarkup(React.createElement(RegistrationForm, { onRegistered() {}, onExam() {}, onPatient() {}, onBusyChange() {} }));
  for (const label of ['人员资料', '本次体检', '读取身份证', '模拟读取身份证', '民族', '地址', '确认登记', '暂不选择套餐']) assert.ok(markup.includes(label));
  assert.ok(markup.includes('不能读取实体身份证'));
  assert.ok(markup.includes('<details'));
  assert.ok(markup.includes('autoComplete="off"') || markup.includes('autoComplete="off"'.toLowerCase()));
  assert.ok(!markup.includes('value="1990-01-01"'));
});
test('foundation requests respect create/update contracts and normalize optional catalog text', () => {
  const draft = { ...blankDefinition(), name: ' 验收项目 ', departmentId: '2', category: ' 检验 ', unit: ' mmol/L ', referenceRange: ' 3.9～6.1 ', resultType: 'number', abnormalAllowed: false, is_active: false };
  assert.deepEqual(departmentCreatePayload(draft), { name: '验收项目' });
  assert.deepEqual(departmentPayload(draft), { name: '验收项目', is_active: false });
  assert.deepEqual(catalogPayload(draft), { name: '验收项目', is_active: false, department_id: 2, category: '检验', unit: 'mmol/L', reference_range: '3.9～6.1', result_type: 'number', abnormal_allowed: false });
  const empty = catalogPayload({ ...blankDefinition(), departmentId: '1' });
  assert.equal(empty.unit, ''); assert.equal(empty.reference_range, '');
});
test('foundation confirmation rejects partial data but accepts an already edited create replay', () => {
  const department = { id: 3, name: '服务器当前名称', is_active: false, version: 4 };
  assert.equal(confirmedDefinition(department, false), true);
  assert.equal(confirmedDefinitionSave(department, false), true);
  assert.equal(definitionMatches(department, { name: '原创建名称', is_active: true }, false), false);
  for (const invalid of [null, {}, { ...department, id: 0 }, { ...department, version: 0 }, { ...department, name: '' }, { ...department, is_active: undefined }]) assert.equal(confirmedDefinition(invalid, false), false);
  const fields = { name: '服务器当前名称', is_active: false };
  assert.equal(confirmedDefinitionSave(department, false, { id: 3, version: 3, fields }), true);
  assert.equal(confirmedDefinitionSave(department, false, { id: 3, version: 4, fields }), false);
  assert.equal(confirmedDefinitionSave(department, false, { id: 2, version: 3, fields }), false);
  assert.equal(confirmedDefinitionSave(department, false, { id: 3, version: 3, fields: { ...fields, name: '另一输入' } }), false);
  assert.equal(confirmedDefinition(department, true), false);
  const catalog = { ...department, department_id: 1, category: '', unit: null, reference_range: null, result_type: 'boolean', abnormal_allowed: true };
  assert.equal(confirmedDefinition(catalog, true), true);
  assert.equal(confirmedDefinition({ ...catalog, result_type: 'unknown' }, true), false);
  assert.equal(definitionMatches(catalog, { unit: '', reference_range: '', result_type: 'boolean' }, true), true);
});
test('an uncertain original save remains retryable when a later department lookup fails', () => {
  const draft = { ...blankDefinition(), name: '待核对的项目', departmentId: '2' };
  const failedLookup = { confirming: true, hasSubmission: true, dirty: true, conflict: false, departmentError: '无法连接服务器' };
  assert.equal(canSubmitDefinition(draft, true, failedLookup), true);
  assert.equal(canSubmitDefinition(draft, true, { ...failedLookup, confirming: false }), false);
  assert.equal(canSubmitDefinition(draft, true, { ...failedLookup, hasSubmission: false }), false);
  assert.equal(canSubmitDefinition(draft, true, { ...failedLookup, confirming: false, departmentError: '', conflict: true }), false);
  assert.equal(canSubmitDefinition(draft, true, { ...failedLookup, confirming: false, departmentError: '' }), true);
});
test('new department assignments exclude inactive entries while existing doctors retain theirs', () => {
  const departments = [{ id: 1, name: '启用科室', is_active: true }, { id: 2, name: '旧停用科室', is_active: false }, { id: 3, name: '兼容旧接口' }];
  assert.deepEqual(availableDepartments(departments).map(value => value.id), [1, 3]);
  assert.deepEqual(availableDepartments(departments, 2).map(value => value.id), [1, 2, 3]);
});
test('locked catalog editor disables every editable control including activation', () => {
  const markup = renderToStaticMarkup(React.createElement(DefinitionFields, { draft: blankDefinition(), onChange() {}, disabled: true, catalog: true, departments: [], editing: true }));
  const controls = [...markup.matchAll(/<(input|select|textarea)\b[^>]*>/g)].map(match => match[0]);
  assert.equal(controls.length, 8);
  assert.ok(controls.every(control => /\bdisabled=""/.test(control)));
  assert.ok(markup.includes('参考范围'));
  assert.ok(markup.includes('maxLength="500"'));
  assert.ok(markup.includes('异常由医生核对后标记'));
});
test('result validation uses exam snapshots and preserves legacy text defaults', () => {
  assert.equal(resultTypeFor({}), 'text');
  assert.equal(resultTypeFor({ result_type: 'number' }), 'number');
  assert.equal(resultTypeFor({ result_type: 'boolean' }), 'boolean');
  for (const value of ['0', '-1.5', '.25', '1e3', '+2.5']) assert.equal(validResultValue(value, 'number'), true);
  for (const value of ['', 'NaN', 'Infinity', '-Infinity', '1e1000', '0x12', '文字']) assert.equal(validResultValue(value, 'number'), false);
  for (const value of ['是', '否']) assert.equal(validResultValue(value, 'boolean'), true);
  for (const value of ['', 'true', 'false', '1', '正常']) assert.equal(validResultValue(value, 'boolean'), false);
  assert.equal(validResultValue('原有文本结果', 'text'), true);
});
test('snapshot controls offer a finite numeric input, explicit yes/no and optional anomaly marking', () => {
  const number = renderToStaticMarkup(React.createElement(ResultValueControl, { type: 'number', value: '4.5', disabled: true, onChange() {} }));
  assert.ok(number.includes('type="number"')); assert.ok(number.includes('step="any"')); assert.ok(number.includes('disabled=""'));
  const boolean = renderToStaticMarkup(React.createElement(ResultValueControl, { type: 'boolean', value: '否', disabled: true, onChange() {} }));
  assert.ok(boolean.includes('<select')); assert.ok(boolean.includes('value="是"')); assert.ok(boolean.includes('value="否"'));
  const text = renderToStaticMarkup(React.createElement(ResultValueControl, { type: 'text', value: '原结果', disabled: false, onChange() {} }));
  assert.ok(text.includes('<textarea')); assert.ok(text.includes('原结果'));
  const forbidden = renderToStaticMarkup(React.createElement(AbnormalControl, { allowed: false, value: true, disabled: false, onChange() {} }));
  assert.ok(!forbidden.includes('checkbox')); assert.ok(forbidden.includes('不标记异常'));
});
const packageItem = (id, overrides = {}) => ({ id, name: `虚构项目${id}`, department_id: 1, department_name: '验收科室', department_is_active: true, category: '', unit: '', reference_range: '', result_type: 'text', abnormal_allowed: true, is_active: true, version: 1, ...overrides });
const packageRecord = (overrides = {}) => ({ id: 5, name: '虚构套餐', description: '', is_active: true, version: 1, item_count: 2, is_available: true, unavailable_reason: '', items: [packageItem(1), packageItem(2)], item_ids: [1, 2], ...overrides });
test('package payload compares membership as a set and preserves selected item objects', () => {
  const draft = { ...blankPackage(), name: ' 虚构套餐 ', description: ' 说明 ', items: [packageItem(2), packageItem(1)] };
  assert.deepEqual(packagePayload(draft), { name: '虚构套餐', description: '说明', is_active: true, item_ids: [1, 2] });
  assert.deepEqual(draft.items.map(item => item.id), [2, 1]);
  assert.equal(packageMatches(packageRecord({ description: '说明' }), packagePayload(draft)), true);
});
test('package confirmation validates exact item sets and allows unavailable records only in management', () => {
  const valid = packageRecord(); assert.equal(confirmedManagedPackage(valid), true); assert.equal(confirmedPackageSelection(valid, 5), true);
  const unavailable = packageRecord({ is_available: false, unavailable_reason: '检查项目已停用', items: [packageItem(1, { is_active: false }), packageItem(2)] });
  assert.equal(confirmedManagedPackage(unavailable), true); assert.equal(confirmedPackageSelection(unavailable, 5), false);
  for (const broken of [packageRecord({ items: [packageItem(1), packageItem(1)] }), packageRecord({ item_ids: [1, 1] }), packageRecord({ item_ids: [1, 3] }), packageRecord({ item_count: 99 })]) {
    assert.equal(confirmedManagedPackage(broken), false); assert.equal(confirmedPackageSelection(broken, 5), false);
  }
});
test('package save distinguishes create replay from a versioned update confirmation', () => {
  const editedReplay = packageRecord({ name: '其他管理员后来修改', version: 3 });
  assert.equal(confirmedPackageSave(editedReplay), true);
  const fields = packagePayload(packageDraft(packageRecord()));
  assert.equal(confirmedPackageSave(editedReplay, { id: 5, version: 1, fields }), false);
  assert.equal(confirmedPackageSave(packageRecord({ version: 2 }), { id: 5, version: 1, fields }), true);
  assert.equal(confirmedPackageSave(packageRecord(), { id: 5, version: 1, fields }), false);
});
test('new package validation enforces membership bounds and preserves uncertain original retries', () => {
  const first = { confirming: false, hasSubmission: false, dirty: true, conflict: false };
  const draft = { ...blankPackage(), name: '虚构新套餐' };
  assert.equal(canSubmitPackage(draft, null, first), false);
  assert.equal(canSubmitPackage({ ...draft, items: [packageItem(1)] }, null, first), true);
  assert.equal(canSubmitPackage({ ...draft, items: [packageItem(1), packageItem(1)] }, null, first), false);
  assert.equal(canSubmitPackage({ ...draft, items: Array.from({ length: 201 }, (_, index) => packageItem(index + 1)) }, null, first), false);
  assert.equal(canSubmitPackage(draft, null, { ...first, confirming: true, hasSubmission: true }), true);
  assert.equal(canSubmitPackage(draft, null, { ...first, confirming: true, hasSubmission: false }), false);
});
test('empty legacy packages can be maintained or disabled without creating enabled empty packages', () => {
  const state = { confirming: false, hasSubmission: false, dirty: true, conflict: false };
  const emptyDraft = { ...blankPackage(), name: '虚构旧模板', items: [] };
  const emptyLegacy = packageRecord({ item_count: 0, items: [], item_ids: [], is_available: false });
  assert.equal(canSubmitPackage(emptyDraft, emptyLegacy, state), true);
  assert.equal(canSubmitPackage(emptyDraft, { ...emptyLegacy, is_active: false }, state), false);
  assert.equal(canSubmitPackage({ ...emptyDraft, is_active: false }, packageRecord(), state), true);
  assert.equal(canSubmitPackage(emptyDraft, packageRecord(), state), false);
  assert.equal(canSubmitPackage({ ...emptyDraft, is_active: false }, null, state), false);
});
test('item selection keeps inactive old members removable and bounds newly added active members', () => {
  const old = packageItem(1, { is_active: false }); const selection = [old];
  assert.deepEqual(togglePackageItem(selection, old), []);
  assert.equal(togglePackageItem([], old).length, 0);
  assert.equal(togglePackageItem([], packageItem(2, { department_is_active: false })).length, 0);
  assert.equal(togglePackageItem(selection, packageItem(2)).length, 2);
  const full = Array.from({ length: 200 }, (_, index) => packageItem(index + 1));
  assert.equal(togglePackageItem(full, packageItem(201)), full);
  const markup = renderToStaticMarkup(React.createElement(SelectedPackageItems, { items: selection, disabled: true, onRemove() {} }));
  assert.ok(markup.includes('虚构项目1')); assert.ok(markup.includes('已停用')); assert.ok(markup.includes('disabled=""'));
});
test('registration requires a freshly confirmed package version while original uncertain retries remain available', () => {
  const selected = { ...blankExamRegistration(), packageId: '5', packageVersion: 3 };
  assert.equal(packageSelectionReady(selected), true);
  assert.equal(packageSelectionReady({ ...selected, packageVersion: null }), false);
  assert.equal(packageSelectionReady(blankExamRegistration()), true);
  const readFailed = { ...selected, packageVersion: null };
  assert.equal(canRegisterWithPackage(readFailed, { confirming: false, hasSubmission: false, packageConflict: false }), false);
  assert.equal(canRegisterWithPackage(readFailed, { confirming: true, hasSubmission: true, packageConflict: true }), true);
  assert.equal(canRegisterWithPackage(readFailed, { confirming: true, hasSubmission: false, packageConflict: false }), false);
  assert.equal(examRegistrationPayload(selected).package_version, 3);
  assert.equal(examRegistrationPayload(blankExamRegistration()).package_version, null);
});
test('unknown registration can recover only from the definite old-package-version conflict', () => {
  const original = { ...blankExamRegistration('2026-10-03'), packageId: '5', packageVersion: 2, examType: '入职体检', note: '保留本次备注' };
  assert.equal(canRegisterWithPackage(original, { confirming: true, hasSubmission: true, packageConflict: false }), true);
  const recovered = recoverPackageVersionConflict(original, new ApiError('套餐已被修改，请重新读取套餐后核对 请刷新服务器数据并核对后再提交。', 409));
  assert.deepEqual(recovered, { ...original, packageVersion: null });
  assert.equal(original.packageVersion, 2);
  assert.equal(canRegisterWithPackage(recovered, { confirming: false, hasSubmission: false, packageConflict: true }), false);
  const reread = { ...recovered, packageVersion: 3 };
  assert.equal(canRegisterWithPackage(reread, { confirming: false, hasSubmission: false, packageConflict: false }), true);
  assert.equal(examRegistrationPayload(reread).package_version, 3);
  assert.equal(examRegistrationPayload(reread).note, original.note);
  for (const unrelated of [new ApiError('同一请求编号已用于其他体检资料', 409), new ApiError('套餐资料与原请求不符', 409), new ApiError('数据尚未保存', 0, true), new ApiError('套餐已被修改，请重新读取套餐后核对', 503, true), new Error('套餐已被修改，请重新读取套餐后核对')]) {
    assert.equal(recoverPackageVersionConflict(original, unrelated), null);
  }
});
test('package fields and pagination remain safe within the registration and editor forms', () => {
  const fields = renderToStaticMarkup(React.createElement(PackageFields, { draft: blankPackage(), disabled: true, onChange() {} }));
  const controls = [...fields.matchAll(/<(input|select|textarea)\b[^>]*>/g)].map(match => match[0]);
  assert.equal(controls.length, 3); assert.ok(controls.every(control => /\bdisabled=""/.test(control)));
  const picker = renderToStaticMarkup(React.createElement(PackagePicker, { value: '5', version: 3, disabled: true, onChange() {} }));
  const buttons = [...picker.matchAll(/<button\b[^>]*>/g)].map(match => match[0]);
  assert.ok(buttons.every(button => /type="button"/.test(button) && /disabled=""/.test(button)));
  const pager = renderToStaticMarkup(React.createElement(Pagination, { page: 1, total: 50, busy: false, onPage() {} }));
  assert.ok([...pager.matchAll(/<button\b[^>]*>/g)].every(match => /type="button"/.test(match[0])));
});
test('write timeout still aborts while the response body is stalled', async () => {
  const previousFetch = global.fetch, previousWindow = global.window;
  let abortTimer, cleared = false, bodyStarted = false;
  global.window = { setTimeout(callback) { abortTimer = callback; return 1; }, clearTimeout() { cleared = true; }, dispatchEvent() { return true; } };
  global.fetch = async (url, options) => ({ ok: true, status: 200, json() {
    bodyStarted = true;
    return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }));
  } });
  try {
    const request = api('/exams', { method: 'POST', body: {} });
    await Promise.resolve(); await Promise.resolve();
    assert.equal(bodyStarted, true);
    assert.equal(cleared, false);
    abortTimer();
    await assert.rejects(request, error => error instanceof ApiError && error.uncertain && error.status === 0);
    assert.equal(cleared, true);
  } finally { global.fetch = previousFetch; global.window = previousWindow; }
});
test('server errors and malformed success keep a write uncertain', async () => {
  const previousFetch = global.fetch, previousWindow = global.window;
  global.window = { setTimeout() { return 1; }, clearTimeout() {}, dispatchEvent() { return true; } };
  try {
    global.fetch = async () => new Response('{"detail":"数据尚未保存"}', { status: 503 });
    await assert.rejects(api('/exams', { method: 'POST', body: {} }), error => error instanceof ApiError && error.status === 503 && error.uncertain);
    global.fetch = async () => new Response('invalid json', { status: 200 });
    await assert.rejects(api('/exams', { method: 'POST', body: {} }), error => error instanceof ApiError && error.status === 200 && error.uncertain);
  } finally { global.fetch = previousFetch; global.window = previousWindow; }
});
