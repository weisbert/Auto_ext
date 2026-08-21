## 一、逐文件处置清单

路径均相对 `C:\code\Auto_ext\Auto_ext\`。测试数 = pytest 实际 collect 数（含 parametrize 展开），合计 897。

### tests/core/

| 文件 | 测试数 | 处置 | 理由 / 被什么取代 |
|---|---|---|---|
| `tests/core/test_checks.py` | 23 | **keep** | LVS 报告解析在保留清单且要提升使用。banner/DISCREPANCIES/CELL SUMMARY 三路判定与 Calibre v2019.2 兼容分支与对象模型无关。新增的只是 `LvsReport` 会被写进 `run.json` 的 results 段，不影响现有断言。 |
| `tests/core/test_clone_template.py` | 33 | **delete** | `core/clone_template.py` 在删除清单。`validate_suffix` / `derive_clone_destination` / 整文件 fork 语义整个下线。取代者：patch 模式（在生成结果上编辑，存相对生成结果的 diff），不再产生新模板文件，因此没有"目标文件名怎么派生"这个问题。 |
| `tests/core/test_config.py` | 84 | **rewrite** | 测的是 `ProjectConfig`(14 键) / `TaskSpec`(15 键) / `TaskConfig` 三个模型 + ruamel 编辑器。新模型下裂成 PDK Profile、Recipe、Cells 三套 schema，`task_id` 拼接身份、`knobs`、`exclude`、`jivaro_overrides`、`templates` 绑定全消失。内部拆分：29 delete（knobs 10、exclude 6、jivaro_overrides 3、templates 绑定 8、`duplicate_task_id` 警告 1、`load_tasks_jivaro_defaults` 1）、9 adapt（`label` 8 + `TaskConfig` frozen 1）、46 rewrite。 |
| `tests/core/test_diff_template.py` | 29 | **adapt** | 模块保留但改定位。diff/hunk 计算、相邻 hunk 合并、whitespace-only 拒绝、`LargeDiffWarning` 阈值（9 条）原样有效，是 patch 引擎的底座。要改的 20 条：`_validate_toggle_name` 的禁用名单来自 identity keys + Jinja 关键字（patch id 校验规则不同）、`_wrap_hunk` 生成 `[% if <knob> %]` 包裹（knob 下线 → 条件块由 Recipe 字段驱动）、`render_byte_equivalence_check` 的 on/off 双渲染、`apply_toggle_to_template` 的锚定/重叠语义要改成"patch 相对生成结果锚定"。 |
| `tests/core/test_eda_quirks.py` | 12 | **adapt** | 12 条全是真实 Cadence 坑的回归钉，必须一条不少地活下来。10 条原样有效（simRunDir 注入 4、Calibre v2019.2 报告 4、Quantus `-cdl_out_map_directory` 续行 2）。2 条要改夹具：`test_eda_serial_workdir_si_env_is_per_task_not_workarea` / `test_eda_parallel_workdir_si_env_is_per_task` 用 `task_id` 造并行目录，要换成 run id + `runs/<ISO>_<slug>/`。 |
| `tests/core/test_env.py` | 42 | **keep** | `core/env.py` 在保留清单。`discover_required_vars` / `substitute_env` / `resolve_env` / `resolve_path_expr`（含 `$X\|parent` 过滤器链）的契约不随对象模型变。唯一变化是调用方从 `project.yaml.paths` 变成 PDK Profile 的 env 解析段，函数签名不动。 |
| `tests/core/test_errors.py` | 8 | **keep** | 异常层级 `AutoExtError` + 5 个子类。新模型会加 `RecipeError` / `PatchConflictError` / `ProfileError`，是追加不是改写。 |
| `tests/core/test_importer.py` | 60 | **adapt** | importer 保留但改定位为"我们建 catalog 的一次性工具"。47 条原样有效：四工具身份提取、身份在每个出现位置的替换、pathological cell 不越界、cross-validation 冲突、`simRunDir` 注入、`aggregate_pdk_tokens`（tech_name / calibre_lvs_dir / qrc_deck_dir 跨文件交叉校验）、`apply_project_constants` 体改写 —— 这些正是 PDK Profile 扫描发现的原料。13 条 delete：`# ---- auto-knobs` 整节 8 条（calibre `connect_by_name` / `lvs_variant` 播种成 knob）+ smart re-import merge 里保 user knob 的 5 条。取代者：`connect_by_name` 与 LVS deck variant 变成 Recipe 的 LVS 设置字段 + PDK Profile 的 deck variant 表。 |
| `tests/core/test_init_project_module.py` | 10 | **rewrite** | `dry_run` / `commit` / `cross_validate_identities` 的编排骨架还要（首次建 Profile 仍需预览-确认-落盘），但产物清单从"project.yaml + tasks.yaml + 4 模板 + 4 manifest = 9~10 文件"变成 PDK Profile 一份 + Cells 一张表 + 选一个现成 Recipe，`test_dry_run_jivaro_omitted_yields_8_files` 这类文件计数断言全部作废。 |
| `tests/core/test_manifest.py` | 50 | **delete** | `core/manifest.py` 在删除清单。`KnobSpec`（type/range/choices 校验）、`SourceRef`、`resolve_knob_values` 四层优先级、`current_knob_value`、`append_knob_to_manifest_yaml` 整体下线。取代者：Recipe 是一个固定 Pydantic 模型，字段就是全集，取值域来自 PDK Profile 的 corner 取值表 / LVS deck variant 表，不存在"谁写了 manifest 谁就能调"。 |
| `tests/core/test_preset.py` | 14 | **delete** | `core/preset.py` 在删除清单。preset = 一组锚定在模板文本上的 hunk，其定位问题（anchor lost / ambiguous）由 Recipe 从根上消解：Recipe 存语义字段而非文本 hunk，跨工艺可移植。 |
| `tests/core/test_progress.py` | 9 | **adapt** | `ProgressReporter` 事件序列 + `CancelToken`（stage 间取消、subprocess drain 循环中途 kill、reporter 抛异常不中断）在保留清单，逻辑完全有效。2 条纯协议/取消测试可原样留；7 条断言里的 `on_task_start(task_id, ...)` 第一参要换成 run/dut 标识，且要补"事件落进 run.json 的 stage 状态/耗时"。 |
| `tests/core/test_template.py` | 39 | **adapt** | 25 条 keep：`render_template` 的 env-先-Jinja-后顺序、strict_env、`$$` 转义、Jinja undefined 抛错、`scan_placeholders`、`collect_var_references`。4 条 delete：`test_render_with_knobs_substitutes` / `_knobs_default_none` / `_knob_name_collision_with_context_raises` / `_missing_knob_raises_undefined`（knobs 参数下线，渲染上下文由 Profile+Recipe+Cell 三方合成）。10 条 adapt：`resolve_template_path` 的 cwd→auto_ext_root→workarea 三级回退要换成 catalog 根 + patch 层；`enumerate_stage_templates` 的扫描根换成 catalog。 |
| `tests/core/test_workdir.py` | 20 | **adapt** | workdir 隔离在保留清单，16 条原样有效（serial 复制/回滚/幂等、symlink cds.lib/.cdsinit、symlink 被拒时抛 WorkdirError、`place_si_env_in_parallel_dir`）。4 条 adapt：`prepare_parallel_workdir(root, workarea, task_id)` 第三参变 run id，`test_prepare_parallel_sanitizes_task_id` / `_accepts_int_task_id` 的 sanitize 规则要按 `<ISO时间戳>_<slug>` 重写，`_creates_task_dir` / `_reuses_stale_dir` 的目录名从 `runs/task_<id>` 变 `runs/<ISO>_<slug>/`（且"复用 stale 目录"这条语义在不可变 Run 下要反转成"永不复用"）。 |
| `tests/core/test_yaml_roundtrip.py` | 11 | **rewrite** | ruamel 注释保真 / 键序 / 尾换行 / anchors+merge keys 的保证必须留（这是明确的项目规则），但 11 条的夹具全部是 project.yaml + tasks.yaml 形状，其中 4 条直接调 `apply_project_edits` / `apply_tasks_edits`，1 条读生产 fixture `project_minimal.yaml`。新宿主是 recipe.yaml / cells.yaml / profile 缓存，逐条重写。 |

### tests/ 根目录

| 文件 | 测试数 | 处置 | 理由 / 被什么取代 |
|---|---|---|---|
| `tests/test_cli_init_project.py` | 11 | **rewrite** | 同 `test_init_project_module.py`。其中 `test_init_project_cross_project_abstraction`（A/B 两套 raw 产出的 project.yaml 只应在 PDK 常量字段上不同）的**意图**是新模型的核心命题，必须以新形态保留：同一份 Recipe 挂到两个 PDK Profile 上，渲染结果只在工艺绑定处不同。 |
| `tests/test_cli_run.py` | 28 | **rewrite** | 13 条 delete：`--knob` 解析 5 条、`test_run_malformed_knob_exits_2`、`test_run_knob_beats_manifest_default`、`test_run_knob_layering_project_task_cli`、`knob suggest` 1 条、`knob promote` 4 条 —— CLI 的 knob 子命令在删除清单。15 条 rewrite：`run` / `check-env` 命令还在但参数面全换（`--task-id` 过滤 → `--cell` / `--recipe`，新增 `--profile`、`--run-note`），`import` 子命令 4 条要移到 catalog 工具命名空间，`test_reimport_preserves_user_knob_*` 2 条随 knob 一起 delete。 |
| `tests/test_integration_e2e.py` | 6 | **rewrite** | 六条缝合测试（init-project → check-env → run --dry-run → dspf 路径跨路径一致性 → 缺环境变量的失败模式）的**方法论**是整个套件里杠杆最高的部分，注释里已写明 dspf bug 类正是死在缝上。新模型的缝变成 Profile 发现 → Recipe 解析 → Cells 展开 → Run 落盘 → rendered 归档，六条全部按新缝重写。 |
| `tests/test_path_safety.py` | 44 | **adapt** | 21 条 keep：无 `shell=True` 全仓审计、`run_subprocess` argv list 形式、cell 名含 `` `id` `` / `$(whoami)` / `foo;bar` 时仍是单个 argv 元素、`substitute_env` 是字面替换不递归。23 条 adapt：`TemplatePaths` 的 `..` 拒绝（16 条 parametrize + 5 条 accept + 1 条经 `load_project`）宿主消失，要改挂到 Recipe 引用 catalog 模板的校验和 patch 文件路径校验上；`test_path_safety_dspf_out_path_rejects_traversal` 的宿主变成 Recipe 输出形式字段。**新模型新增攻击面**：run slug 由用户输入拼进目录名，必须补 slug 的路径穿越测试。 |
| `tests/test_runner.py` | 36 | **adapt** | 33 条 adapt：stage 编排、si.env 只在成功后发布到 output_dir、LVS 失败中止 vs `continue_on_lvs_fail`、dry-run 只渲染不起进程 —— 全在保留清单，改的是入参（`TaskConfig` → DUT+Recipe 快照）与 rendered/log 落点（`runs/<ISO>_<slug>/rendered/`）。11 条 dspf_out_path 解析测试要迁到 Recipe 的输出形式（extracted_view / dspf / 两者）。3 条 delete：`test_calibre_lvs_default_knobs_render`、`test_calibre_lvs_knob_overrides_flip_render`、`test_phase59_bc_rendered_path_for_per_task_override_beats_project_default`（per-task 模板覆盖在删除清单）。 |
| `tests/test_runner_parallel.py` | 7 | **adapt** | 5 条 adapt：并行两 job 都过、summary 保序、一个失败另一个继续、jobs=1 走 serial 路径。2 条 **rewrite 且语义反转**：`test_preflight_rejects_duplicate_library_cell` 与 `test_preflight_accepts_same_cell_when_pattern_discriminates` —— 现状把"同 library/cell"当冲突拒绝，而新模型里"复制一个 spec，同 cell 不同参数"正是必须支持的头号场景。新断言应为：同 cell 多 Run 并行合法，隔离靠 `runs/<ISO>_<slug>/` 目录唯一性而非 preflight 拒绝。 |
| `tests/test_sanity.py` | 4 | **keep** | 包版本、`Tool` ABC 不可实例化、`ToolResult` 默认值、mocks 目录存在。与模型无关。 |

### tests/tools/

| 文件 | 测试数 | 处置 | 理由 / 被什么取代 |
|---|---|---|---|
| `tests/tools/test_tools.py` | 24 | **keep** | `tools/*` 在保留清单。5 个 Tool 的 `build_argv` 形状、`SiTool` 无条件 unlink `.running`、`CalibreTool.parse_result` 与 checks.py 的集成、`lvs_report_path_from_runset` 解析 qci 指令、`run_subprocess` 逐行 flush —— 全部原样有效。**唯一夹具债**：`test_production_templates_render` 的 5 条 parametrize 硬编码模板子路径与一个含 `"task_id": "LIB__inv__layout__schematic"` 的 context dict，需随 catalog 与新 context 合成方式刷新（5/24）。 |

### tests/ui/

| 文件 | 测试数 | 处置 | 理由 / 被什么取代 |
|---|---|---|---|
| `tests/ui/test_app.py` | 7 | **adapt** | QSettings 记住上次 config_dir 的逻辑保留；判定"这是不是一个合法项目目录"的依据是 `project.yaml` 是否存在（`test_read_ignores_dir_without_project_yaml`），要换成新的项目标识（cells 表 / 项目清单文件）。 |
| `tests/ui/test_clone_template_dialog.py` | 20 | **delete** | Templates tab 右键 Copy/Delete + `CloneTemplateDialog` + `EditTemplateDialog` 全部依附 `clone_template.py`。取代者：patch 编辑器 —— 在生成结果上编辑、存 diff、UI 显示"此配方有 N 处手工修改"、可展开、可单条还原。"删除模板时若被绑定则禁用"这类保护随四个绑定 slot 一起消失。 |
| `tests/ui/test_config_controller.py` | 19 | **adapt** | load/dirty/revert/save/`has_external_change`(mtime) 的控制器契约与对象模型正交，全部值得留。改的是桶：现在是 `stage_edits`(project 键) + `stage_tasks_edits`(tasks 整表替换) 两个独立桶，新模型至少三个（profile 只读、recipe、cells），`test_save_writes_both_project_and_tasks` 变成"写 recipe + cells"。 |
| `tests/ui/test_diff_editor.py` | 14 | **rewrite** | `DiffEditorDialog` 是 toggle 编辑器：拖两个 raw 文件 → 算 toggle → 写回 .j2 + append manifest knob。这条链的每一环都换了。取代者是 patch 编辑器：左边生成结果、右边用户编辑、存 diff、冲突时明确报出。1 条 `test_save_as_preset_creates_preset_dir` 直接 delete。语法高亮（`JinjaHighlighter`）那条可整条搬到新对话框。 |
| `tests/ui/test_gui_concurrency.py` | 8 | **adapt** | 4 条并发不变式（mtime 外部变更检测、emit storm 不丢信号含跨线程、关窗中止 worker、跨 tab pending edits 不互相覆盖）在新模型下只会更重要 —— Run 不可变意味着落盘期间的并发写更敏感。只需换桶名与夹具。 |
| `tests/ui/test_init_wizard.py` | 14 | **rewrite** | 向导仍需要（首次建 PDK Profile），页面骨架（destination → raw files 拖放 → preview dry_run → commit → result 自动加载）可复用，但每页内容换：raw files 页变成"指向工艺目录，扫描发现"，preview 页变成体检清单（每项 ✓/✗ + 修法），commit 产物从 9 文件变 Profile 一份。`test_commit_page_rejects_reentrant_validation` 这条防重入的钉子原样搬。 |
| `tests/ui/test_knob_editor.py` | 6 | **delete** | `ui/widgets/knob_editor.py` 在删除清单。choices→QComboBox / bool→QCheckBox 的数据绑定语义在 Recipe 表单里会重新出现，但那是新控件的新测试，不是这 6 条的迁移。 |
| `tests/ui/test_main_window.py` | 11 | **adapt** | 10 条 adapt：File 菜单 / RunTab 空态横幅两个向导入口、脏控制器 Save/Discard/Cancel 三分支、LogTab 嵌进 RunTab、stage 选中驱动 LogTab —— 交互骨架不变。3 条 label/task_id 表头渲染换成 run slug。1 条 rewrite：`test_feature4_main_window_has_four_tabs` 断言具体四个 tab 名，而 Templates tab 下线、Recipes / Runs 上线。 |
| `tests/ui/test_os_open.py` | 5 | **keep** | 纯 stdlib 平台派发（`os.startfile` / `open` / `xdg-open`），新模型下"打开 `runs/<ts>/rendered/ext.cmd`"走同一个函数。 |
| `tests/ui/test_preset_picker.py` | 5 | **delete** | 随 `core/preset.py` 一起下线。 |
| `tests/ui/test_project_tab.py` | 44 | **rewrite** | 这一屏就是"设置面积过大"的具象：14 键 project.yaml 表单 + paths group（6 条）+ Templates combo（4 条）+ dspf combo（13 条）+ `_hint_for_field` 推断规则（7 条）。新模型下拆成 PDK Profile（平时隐身、只读展示 + 体检）与 Recipe 表单。**必须以新形态保留的不变式**：run 进行中禁用 Save 并在 run 结束后恢复（`test_save_button_recovers_after_run_finishes`）、外部冲突时不 autosave、dspf 预览永不残留模板字面量（`test_dspf_combo_preview_never_contains_template_literals`）。 |
| `tests/ui/test_qt_reporter.py` | 5 | **keep** | `QtProgressReporter` 把 core 事件转 Qt 信号、跨线程投递到主线程 —— 与身份模型无关。 |
| `tests/ui/test_run_tab.py` | 20 | **adapt** | 任务勾选跨 reload 保持、状态树填充、stage 行右键菜单（文件不存在时禁用、只在 stage 行出现）、单击不触发/双击触发、auto-follow 实时日志 —— 全部保留。改：6 条 label/task_id 显示回退换成 run slug；rendered/log 路径换 `runs/<ISO>_<slug>/`；`test_new_task_id_defaults_unchecked_on_reload` 的身份键换成 Cell 行 id。**要新增而非改造的**：历史 Run 列表这一屏。 |
| `tests/ui/test_run_worker.py` | 2 | **adapt** | dry-run 走通 + 启动前取消返回 cancelled summary。要加一条：worker 结束时 run.json 已完整落盘。 |
| `tests/ui/test_tag_list_edit.py` | 8 | **keep** | 通用 tag 输入控件（去重保序、空串忽略、增删发信号）。笛卡尔展开退化成"批量添加"后，这个控件正是批量添加的输入部件。 |
| `tests/ui/test_tasks_tab.py` | 45 | **rewrite** | 现状是"多轴 tag 列表 + 笛卡尔预览 + 勾选写 exclude + 三套 per-spec 覆盖"。新模型是一张 Cells 表（library / cell / layout / source / ground_net / out_file），展开在添加时完成、落成明确的行。内部：19 delete（exclude 3、jivaro_overrides fold 4 + 写盘 1、per-task knobs 6、per-task templates 5）、13 条 dspf combo 换宿主到 Recipe、5 条 label adapt、7 条表格 CRUD（add/copy/remove/populate/preview）rewrite 成表格行操作。`test_copy_spec_inserts_deep_clone_after_selection` 的意图（复制一份改参数）在新模型下是头等场景，且现状正是它撞 task_id —— 新测试要断言复制后两行都能各自起 Run。 |
| `tests/ui/test_template_diff_viewer.py` | 8 | **keep** | 只读并排 diff 查看器：拖两文件、增删行着色、swap、同步滚动、hunk 计数横幅、非 UTF-8 优雅降级。这正是 patch 模式展示"N 处手工修改、可展开、可单条还原"所需的展示部件，一行不用改就能复用。 |
| `tests/ui/test_template_generator.py` | 26 | **adapt** | 保留但改定位为 catalog 建设工具。23 条 adapt：拖 raw → 参数化 body、按扩展名/内容自动识别工具、身份覆盖面板 + 防抖重导入 + 内联错误、真-diff 高亮对齐（3 条）、保存前置条件。3 条动刀：`test_save_writes_j2_and_manifest`（rewrite 成只写 .j2 进 catalog）、`test_save_merges_existing_manifest_knobs` 与 `test_save_seeds_calibre_auto_knobs`（delete）。 |
| `tests/ui/test_templates_tab.py` | 15 | **delete** | Templates tab 的四个绑定 slot 在删除清单，knobs form 4 条随 knob 下线，路径 picker 5 条随绑定下线。唯一有转生价值的概念是 inventory 表（模板占位符逐项 ✓/✗ + 未声明变量标红）—— 它变成 PDK Profile 的体检项（每项 ✓/✗ + 修法），但那是全新数据源与全新断言，不是迁移。 |
| `tests/ui/test_templates_view.py` | 11 | **rewrite** | `collect_template_entries` 依赖 `project.templates` 四绑定 + `auto_ext_root/templates` 走查（4 条）；占位符分类器 `env_var_status` / `literal_placeholder_status` / `user_defined_status` / `jinja_variable_status`（6 条，其中 `_declared_knob_is_ok` 直接依赖 `KnobSpec`）。分类器的**判定表**要转生到 Profile 体检 + Recipe 变量覆盖检查，但输入类型全换。 |

### 非测试文件

| 文件 | 测试数 | 处置 | 理由 |
|---|---|---|---|
| `tests/conftest.py` | 0 | **rewrite** | 见第三节。`project_tools_config` / `project_config` / `templates_root` / `clean_env` 四个夹具把旧 schema 焊死在整个套件上。 |

---

## 二、分类统计

### 按文件（权威口径，43 个测试文件）

| 处置 | 文件数 | 测试数 | 占比 |
|---|---|---|---|
| keep | 9 | 127 | 14.2% |
| adapt | 16 | 349 | 38.9% |
| rewrite | 11 | 278 | 31.0% |
| delete | 7 | 143 | 15.9% |
| **合计** | **43** | **897** | **100%** |

keep 文件：`core/test_checks.py`、`core/test_env.py`、`core/test_errors.py`、`test_sanity.py`、`tools/test_tools.py`、`ui/test_os_open.py`、`ui/test_qt_reporter.py`、`ui/test_tag_list_edit.py`、`ui/test_template_diff_viewer.py`

delete 文件：`core/test_clone_template.py`、`core/test_manifest.py`、`core/test_preset.py`、`ui/test_clone_template_dialog.py`、`ui/test_knob_editor.py`、`ui/test_preset_picker.py`、`ui/test_templates_tab.py`

### 按单个测试（细粒度口径，按上表内部拆分累加）

| 处置 | 测试数 | 占比 |
|---|---|---|
| keep | 252 | 28.1% |
| adapt | 212 | 23.6% |
| rewrite | 206 | 23.0% |
| delete | 227 | 25.3% |
| **合计** | **897** | **100%** |

两个口径的差额来自 11 个混合文件：

| 文件 | keep | adapt | rewrite | delete |
|---|---|---|---|---|
| `core/test_config.py` | 0 | 9 | 46 | 29 |
| `core/test_diff_template.py` | 9 | 20 | 0 | 0 |
| `core/test_eda_quirks.py` | 10 | 2 | 0 | 0 |
| `core/test_importer.py` | 47 | 0 | 0 | 13 |
| `core/test_progress.py` | 2 | 7 | 0 | 0 |
| `core/test_template.py` | 25 | 10 | 0 | 4 |
| `core/test_workdir.py` | 16 | 4 | 0 | 0 |
| `test_cli_run.py` | 0 | 0 | 15 | 13 |
| `test_path_safety.py` | 21 | 23 | 0 | 0 |
| `test_runner.py` | 0 | 33 | 0 | 3 |
| `test_runner_parallel.py` | 0 | 5 | 2 | 0 |
| `tools/test_tools.py` | 19 | 5 | 0 | 0 |
| `ui/test_main_window.py` | 0 | 10 | 1 | 0 |
| `ui/test_diff_editor.py` | 0 | 0 | 13 | 1 |
| `ui/test_tasks_tab.py` | 0 | 5 | 21 | 19 |
| `ui/test_template_generator.py` | 0 | 23 | 1 | 2 |

### delete 的 227 条按被删机制归类

| 机制 | 测试数 | 分布 |
|---|---|---|
| knob 四层优先级 + manifest | 105 | `core/test_manifest.py` 50、`test_cli_run.py` 13、`core/test_config.py` 20、`core/test_importer.py` 13、`ui/test_tasks_tab.py` 6、`ui/test_knob_editor.py` 6、`core/test_template.py` 4、`test_runner.py` 2 (`ui/test_templates_tab.py` 的 4 条计入下行) |
| clone_template 整文件 fork | 53 | `core/test_clone_template.py` 33、`ui/test_clone_template_dialog.py` 20 |
| preset 文本锚定 | 20 | `core/test_preset.py` 14、`ui/test_preset_picker.py` 5、`ui/test_diff_editor.py` 1 |
| Templates tab 四绑定 slot + per-task 模板覆盖 | 30 | `ui/test_templates_tab.py` 15、`core/test_config.py` 6、`ui/test_tasks_tab.py` 5、`test_runner.py` 1、`ui/test_template_generator.py` 2、`core/test_config.py` templates 编辑 1 |
| exclude 选择器 | 9 | `core/test_config.py` 6、`ui/test_tasks_tab.py` 3 |
| jivaro_overrides + per-task jivaro | 10 | `core/test_config.py` 4、`ui/test_tasks_tab.py` 5、`core/test_config.py` jivaro defaults 1 |

---

## 三、新模型需要补的测试

### A. Run 记录（`runs/<ISO时间戳>_<slug>/`）

1. **身份唯一性 —— 同一 DUT 同一 Recipe 连跑两次生成两个目录**：冻结时钟推进 1 秒，断言两次 `run_dir` 不同、两份 `run.json` 都在、第一次的内容一字未改。这条直接对冲现状"日志以 `w` 打开每次重跑覆盖"的缺陷。
2. **同秒内两个 Run 不撞**：并行起两个 Run，时钟返回同一 ISO 秒。断言目录名有确定性去重后缀（`_2` 或纳秒段），且两者的 `logs/` 互不写入对方。
3. **Recipe 是快照不是引用**：起一个 Run，落盘后修改磁盘上的 Recipe 文件（改 corner、加一条 patch），断言 `run.json` 里的 recipe 段仍是起跑那一刻的值，且能仅凭 `run.json` 重放出同一份 rendered 文件（字节相等）。
4. **rendered/ 归档的是真正用过的文件**：run 结束后逐一比对 `rendered/ext.cmd` 与 subprocess 实际收到的 `-cmd` 参数所指文件的字节内容；再断言把 workdir 整个删掉后 `rendered/` 仍完好。
5. **stage 级结构化结果落盘**：五个 stage 各自的 `status` / `duration_s` / `log_path` / `artifact_paths` 都在 `run.json` 里，且 `log_path` 指向的文件真实存在；`results/lvs.report` 是副本而非软链（office 侧原始 report 会被下次 LVS 覆盖）。
6. **失败与取消的 Run 同样完整落盘**：Calibre 失败中止后，`run.json` 里 calibre 是 FAILED、quantus/jivaro 是 SKIPPED 且带原因；中途 CancelToken 触发后被 kill 的 stage 记为 CANCELLED，`run.json` 不是半截 JSON（原子写：先写 `.tmp` 再 rename）。
7. **不可变性防御**：Run 目录落盘后再起一个 Run，断言不复用 stale 目录（与现状 `test_prepare_parallel_reuses_stale_dir` 语义相反）；且对已存在的 `run.json` 写入抛错而非静默覆盖。
8. **slug 路径安全**：slug 取自用户输入（cell 名或备注），断言 `../`、`/`、`\`、`:`、shell 元字符被 sanitize；**Windows 专项**：ISO 时间戳里的 `:` 必须不出现在目录名里，否则整套 UI 测试在开发机上无法建目录。
9. **历史可枚举**：写 5 个 Run 后 `list_runs()` 按时间倒序返回，能按 cell / recipe / 状态过滤；目录里混入一个手工建的垃圾目录和一个 `run.json` 损坏的 Run 时跳过并 warn，不抛。

### B. Recipe 解析

1. **语义字段完整加载**：一份写全的 recipe.yaml（type/corner/temperature/耦合阈值/min_res/floating nets 上限/metal fill/输出形式/LVS deck variant/connect_by_name/jivaro on-off+频率+误差）→ 断言每个字段值与类型；未写的字段落到模型默认值而不是 `None`。
2. **未知字段拒绝**：`extra="forbid"`，写错的键（`temprature`、`corner_`）报错信息里含键名和文件行号 —— 这是替代 knob 的唯一防呆手段，必须比现状 manifest 更严。
3. **corner 是语义值而非工艺值**：Recipe 写 `corner: RCWORST`，断言解析后仍是字符串 `RCWORST`，**不做**任何工艺查表；查表只发生在与 Profile 绑定时。
4. **跨工艺可移植**：同一份 Recipe 分别绑 Profile-A（`RCWORST` → `cmax`）与 Profile-B（`RCWORST` → `rcworst_125`），断言渲染出的两份 `.cmd` 只在工艺绑定处不同，其余字节相等。这是 `test_init_project_cross_project_abstraction` 的转生形态。
5. **语义值不在 Profile 表里 → 明确报错**：Recipe 写 `corner: RCBEST`，Profile 的 corner 取值表只有 3 个值，断言报错列出该工艺**支持的全部取值**（而不是 pydantic 的裸 ValidationError）。
6. **输出形式的三态**：`extracted_view` / `dspf` / 两者 —— 断言各自决定了哪些 stage 会跑、`out_file` 必填性如何变化（现状 `_validate_task_outputs` 里"jivaro 开但 out_file 未设"那条规则的新家）。
7. **jivaro on-off 门控 stage 编排**：`reduction.enabled: false` 时 jivaro stage 发出 synthetic SKIPPED 而不是消失（保留现状 `test_jivaro_disabled_emits_synthetic_skipped_pair` 的事件序列契约）。
8. **Recipe 全局共享、跨项目**：从两个不同项目目录加载同一份全局 Recipe，断言解析结果完全相等，且 Recipe 里不含任何项目级路径（写了绝对路径要报错 —— 项目路径归 Cells / Profile）。
9. **ruamel 往返保真**：加载 → 改一个字段 → dump，注释、键序、尾换行全保留（`test_yaml_roundtrip.py` 的保证换宿主）。

### C. patch 应用（逃生舱）

1. **patch 存的是相对生成结果的 diff**：给定 (Profile, Recipe, Cell) 渲染出 baseline，用户在 baseline 上改一行，断言存下来的是含上下文的 hunk（能定位），**不是**整个文件副本，且 patch 里记录了 baseline 的指纹。
2. **干净叠加**：baseline + patch = 用户当初看到的那份，字节相等。
3. **catalog 升级后生成部分自动跟进**：改 catalog 模板使 baseline 变化（在 patch 触及的行之外），重新应用同一个 patch，断言新 baseline 的变化生效 **且** 用户改动继续叠加。这是与 `clone_template` 永久 fork 的根本区别，必须有钉子。
4. **换工艺后自动跟进**：同一 patch 挂到另一个 PDK Profile 生成的 baseline 上，锚点仍在 → 成功叠加；锚点因工艺差异不存在 → 归入冲突分支（下条）。
5. **冲突明确报出**：catalog 升级恰好改到 patch 所在的行，断言抛出结构化冲突（含 patch id、期望上下文、实际上下文、baseline 前后各 N 行），而不是静默丢弃或静默错位。
6. **多 patch 顺序与重叠**：同一 Recipe 上 3 个 patch，断言应用顺序确定（存储序），两个 patch 触及相邻/重叠行时报重叠错（`OverlapError` 的转生），非重叠时全部生效。
7. **单条还原**：3 个 patch 中删掉第 2 个，断言第 1、3 仍生效且结果与"只加 1、3"的结果字节相等（即 patch 之间无隐式耦合）。
8. **UI 计数正确**：`patch_count(recipe)` 返回 3；一个 patch 被还原后返回 2；一个 patch 处于冲突态时仍计入但标冲突。
9. **patch 不改变 Run 的可重放性**：带 patch 的 Recipe 起 Run，`run.json` 的 recipe 快照里含 patch 全文，断言仅凭快照重放能得到相同 rendered 字节（即 patch 是快照的一部分，不是外部引用）。
10. **空 patch / 纯空白 patch 拒绝**：用户点了编辑但没改，或只改了行尾空白 —— 不生成 patch（`_all_hunks_whitespace_only` 的转生）。

### D. Profile 发现

1. **扫描发现的幂等性**：对同一个工艺目录扫两次，断言产出的 Profile 完全相等（含字段顺序），且不写任何东西进工艺目录（office 侧 site-packages / deck 目录是只读的）。
2. **各成分定位**：从工艺目录里发现 env 解析结果、deck 目录、layer map、`assura_tech.lib`、`tech_name` —— 每项各一条，断言发现值与预置 fixture 一致；`tech_name` 的多源交叉校验（Quantus cmd 里的值 vs `assura_tech.lib` vs layer map 路径）一致时采纳、冲突时不猜（沿用 `aggregate_pdk_tokens` 的 unclassify 语义）。
3. **corner 取值表来源可溯**：断言表里每个 corner 值都带来源（哪个文件哪一行），空表时体检项报 ✗ 并给修法。
4. **LVS deck variant 表**：扫出 wiodio / wodio 之类的 variant 列表，断言 Recipe 里写的 variant 名必须在表内；表为空时该 Recipe 字段降级为"必须手填绝对路径"并在体检项标注。
5. **体检项逐项 ✓/✗ + 修法**：构造 6 种缺失场景（deck 目录不存在、layer map 不可读、`assura_tech.lib` 缺失、env 变量未设、corner 表空、二进制不在 PATH），断言每种各产出一条 ✗ 项、每条 ✗ 都带一句可执行的修法文本、✓ 项不带修法。
6. **平时隐身**：断言 Profile 不出现在 Recipe 与 Cells 的任何字段里，且 Recipe 加载不触发扫描（懒加载 / 缓存）；只有起 Run 或点开体检时才解析。
7. **多 Profile 共存与选择**：两个工艺目录 → 两份 Profile，按 `tech_name` 选择；同名冲突时报错列出两个来源路径。
8. **缓存失效**：Profile 缓存后工艺目录里的某个文件 mtime 变化，断言下次访问重扫（复用 `has_external_change` 的 mtime 思路）。
9. **只读文件系统下不炸**：整个工艺目录 chmod 只读（或 monkeypatch open 写模式抛 PermissionError），断言扫描全程只读、缓存写到用户侧目录 —— 这是办公 Linux 服务器的硬约束。
10. **缺失 Profile 时的降级**：没有任何 Profile 可发现时，起 Run 应在 preflight 阶段失败并给出"如何指定工艺目录"的提示，而不是渲染出带未替换 `${}` 的文件（沿用 `strict_env` 姿态）。

---

## 四、测试基础设施的坑

读了 `tests/conftest.py`、`tests/ui/test_tasks_tab.py`、`tests/ui/test_templates_tab.py`（另参照 `tests/ui/test_project_tab.py`）。

### 1. `project_tools_config` 是把旧 schema 焊死在整个套件上的那根钉子

`tests/conftest.py` 里这个夹具用一段 f-string 硬写出一份完整 project.yaml：14 个顶层键里写满了 `work_root` / `verify_root` / `setup_root` / `employee_id` / `tech_name` / `paths` / `layer_map` / `env_overrides` / `extraction_output_dir` / `intermediate_dir` / `dspf_out_path` / **`templates:` 四个绑定 slot**，外加一份含 `jivaro:` 子块的 tasks.yaml。它被 `test_runner.py`、`test_cli_run.py`、`test_runner_parallel.py`、`ui/test_app.py`、`ui/test_config_controller.py`、`ui/test_project_tab.py`、`ui/test_gui_concurrency.py`、`ui/test_run_tab.py`、`ui/test_run_worker.py` 全体消费。

三个具体阻碍：

- `templates:` 段直接写进了四个 `.j2` 的**绝对路径**，而四个绑定 slot 在删除清单。catalog 化后模板由 catalog 按 stage 语义解析，这一段没有对应物 —— 不是改值，是整段删掉后要重新回答"测试怎么拿到模板"。
- `paths:` 里写着 `$calibre_source_added_place|parent` 这种 path-expr，它是 PDK Profile 的东西，但现在从项目配置注入。Profile 改成扫描发现后，测试要么造一个假工艺目录树，要么给 Profile 开一个"直接构造"的测试后门 —— 后者更现实，但必须现在就定，否则每个消费者各造各的。
- 它同时承担了三个角色（提供 config_dir 路径、提供 WORK_ROOT 指向 pytest 沙箱、绑定生产 templates_root）。新模型要拆成 `pdk_profile` / `recipe` / `cells` 三个夹具，拆的时候这三个角色必须重新分配清楚，否则会出现"哪个夹具负责把输出限制在 tmp_path 内"的空档，测试会往开发者真实磁盘写文件。

### 2. `project_config` 返回单一 `ProjectConfig`，而新 `_build_context` 是三方合成

`conftest.py` 末尾的 `project_config` = `load_project(fixtures/project_minimal.yaml)`。`test_runner.py` 里 15 个 `_build_context` / `resolve_dspf_path` 测试直接把它当唯一入参。新模型下上下文由 (Profile, Recipe, Cell) 三方合成，这个夹具无法一对一映射 —— 这 15 条不是改断言，是改调用形状。`fixtures/project_minimal.yaml` 本身只有 5 行（work_root / verify_root / setup_root / employee_id / layer_map），拆成三份新 fixture 时要注意：这 5 个值现在分属 Profile（layer_map）、项目（work_root 系）、无人认领（employee_id 属于谁需要定）。

### 3. `clean_env` 硬编码 12 个变量名，且是项目级全局清单

```
WORK_ROOT, WORK_ROOT2, VERIFY_ROOT, SETUP_ROOT, PDK_LAYER_MAP_FILE, EMP, LIB, FOO, BAR, BAZ, UNDEFINED_X, AUTO_EXT_TEST_VAR
```

其中 `PDK_LAYER_MAP_FILE` 明显是 PDK Profile 的东西，`WORK_ROOT*` 是项目的。新模型里"这个工艺需要哪些环境变量"是 Profile 发现出来的、每个工艺不同，一张写死的全局清单从概念上就不对。这个夹具要改成"按 profile 清场"，且 Profile 发现测试自己需要一个反向夹具（构造一个 env 变量缺失的场景来触发体检 ✗）。

### 4. 没有任何夹具能表达 Run，也没有时钟控制

`workarea` 只造 `cds.lib` + `.cdsinit`。整个 conftest 里没有 `runs/` 骨架、没有 `run.json`、没有冻结时钟。Run 用 ISO 时间戳做身份意味着：**不冻结时钟就无法断言目录名**，而现状 897 条测试里没有一条碰过时间。必须先加 `frozen_clock`（monkeypatch 一个 `now()` 注入点，不要 monkeypatch `datetime` 本体）与 `run_dir` 夹具，否则 A 组的 9 条测试全部写不出来。

**Windows 硬坑**：ISO 8601 的 `2026-08-21T14:32:05` 含 `:`，NTFS 不允许出现在文件名里。开发机是 Windows 11，办公机是 Linux。slug 格式必须在写第一个 Run 测试之前定死（建议 `20260821T143205_<slug>`），否则整套 Run 测试在开发机上一条都跑不起来 —— 而这个问题在 Linux CI 上不会暴露。

### 5. 没有 `tests/ui/conftest.py`，7 处 UI 脚手架各写各的

`find tests -name conftest.py` 只有一个结果。后果是每个 UI 测试文件手写自己的项目脚手架，形状彼此不兼容：

- `test_tasks_tab.py::_multi_spec_config` 写的 project.yaml 只有一行 `employee_id: alice`，tasks.yaml 是三轴笛卡尔 + jivaro 块；
- `test_templates_tab.py::_scaffold_project` 造完整 `auto_ext_root/templates/{calibre,quantus,si}/` 树，写两个 `.j2` + **一个 `ext.cmd.j2.manifest.yaml` sidecar**（含 temperature / exclude_floating_nets_limit 两个 knob），project.yaml 里写 `templates:` 两个绑定；
- `test_project_tab.py` 用 conftest 的 `project_tools_config`；
- `test_diff_editor.py` / `test_init_wizard.py` / `test_template_generator.py` / `test_clone_template_dialog.py` 各有自己的 fixture 块。

改 schema 要逐文件手改 7 处，而且其中 `_scaffold_project` 写的 manifest sidecar 在新模型下无处安放。**建议在动 schema 之前先建 `tests/ui/conftest.py` 收敛脚手架**，否则这 7 处会在重构期间各自漂移。

### 6. UI 测试的固定构造三连把 RunTab 变成了隐式全局依赖

`test_tasks_tab.py::_make_tab` 和 `test_templates_tab.py::_make_tab` 形状一致：

```python
controller = ConfigController(auto_ext_root=...)
run_tab = RunTab(controller)          # 只为了让 is_worker_active() 返回 False
tab = XTab(controller, run_tab)
```

每个 tab 都要先造一个 RunTab，因为"当前有没有 run 在跑"这个状态寄生在 RunTab 上（`test_project_tab.py` 的注释直接写着 "a real (but inert) RunTab so `is_worker_active()` returns False"）。Run 变成独立的不可变记录对象后，这个状态应该归 Run/RunManager，但**现在所有 tab 的构造签名都吃 `run_tab`** —— 不先把这个依赖挪走，Runs 做不成独立一屏，而挪它会同时动 4 个 UI 测试文件的每一个 `_make_tab`。

### 7. `task_id` 字符串是 UI 层的实际主键，且有一处反向解析

不只是断言里出现字符串。`auto_ext/ui/tabs/run_tab.py` 里：

- `lw_item.setData(Qt.UserRole, t.task_id)` —— 勾选状态、状态树行、点击回路全部以 `task_id` 字符串为键（`test_run_tab.py::test_new_task_id_defaults_unchecked_on_reload` 直接测这个）；
- 更麻烦的一处是 **反向解析**：`if _UNSAFE_TASK_ID.sub("_", t.task_id) == safe_id:` —— 它拿日志目录名 `task_<safe_id>` 去反查是哪个 task，办法是把每个 task 的 `task_id` 重新 sanitize 一遍做字符串比对。这是"日志路径 ⇄ 任务身份"的隐式双向映射。Run 记录把 `log_path` 显式写进 `run.json` 之后这段代码应该整段删掉，但在删之前，任何改 `task_id` 格式的动作都会让这段静默失配（找不到就什么都不做，不报错），测试不一定红。

### 8. 少数与模型无关、直接留的好夹具

`can_symlink`（session 级 symlink 能力探针）、`mocks_on_path`（Linux 直接 prepend PATH，Windows 生成 `.bat` shim 调 git-bash）、`mocks_dir` / `fixtures_dir`、`isolated_qsettings`（`test_ui/test_app.py` 里把 QSettings 重定向到 tmp_path，避免污染开发者真实配置）—— 这四个原样保留。其中 `mocks_on_path` 的 Windows 分支在没有 git-bash 时 `pytest.skip`，意味着 Windows 开发机上 `test_runner.py` / `test_runner_parallel.py` / `test_cli_run.py` 的相当一部分可能整体跳过 —— 重构期间要确认这些是真跑过了还是被 skip 了，否则会误以为 adapt 完成。