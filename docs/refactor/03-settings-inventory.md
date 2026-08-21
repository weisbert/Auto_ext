## 目标去向 = `profile`（PDK Profile：工艺/站点环境层，平时隐身）

| 设置名 | 现在住在哪 | 现在在 GUI 哪里露出 | 目标去向 | 判定理由 |
|---|---|---|---|---|
| `work_root` | `auto_ext/core/config.py:150` | Project tab → Identity 组第 1 行（`project_tab.py:76`，带 `…` 目录选择器 `:343/882`） | profile | 只是 `$WORK_ROOT` 的显式影子；真值来自 PDK/项目 setup 脚本。归入 Profile 的「env 解析 + 体检项（✓/✗ + 修法）」，解析结果同时快照进 `run.json`。用户日常不该看见 |
| `verify_root` | `config.py:152` | Project tab → Identity 组（`project_tab.py:77`） | profile | Calibre/QRC runset 根 = deck 目录的上级，纯工艺资产位置 |
| `setup_root` | `config.py:154` | Project tab → Identity 组（`project_tab.py:78`） | profile | `assura_tech.lib` 的根，目标模型明确列在 Profile 里 |
| `employee_id` | `config.py:158` | Project tab → Identity 组（`project_tab.py:79`） | profile | 从 `$USER`/`$USERNAME` 自动解析，只在模板路径里做替换；属于 env 解析层，和工艺一起随环境走 |
| `tech_name` | `config.py:165` | Project tab → PDK 组（`project_tab.py:80`） | profile | 目标模型逐字列出：`tech_name` 属 Profile |
| `tech_name_env_vars` | `config.py:170-176` | 不露出（只在 `_hint_for_field` 的 tooltip 里间接显示自动反解结果，`project_tab.py:124-132`） | profile | 是 `tech_name` 的自动反解规则，即 Profile 的「扫描发现」逻辑本身 |
| `paths.calibre_lvs_dir` | `config.py:194`（值见 `config/project.yaml`） | Project tab → Paths 组动态行（`project_tab.py:371-380`, `_add_path_row:620`），带 "Used by" 模板引用列表 | profile | 目标模型的「deck 目录」。工艺一换整条路径都变 |
| `paths.qrc_deck_dir` | `config.py:194` | 同上 | profile | 同上，QRC deck 目录 |
| `paths.<自定义 key>` | `config.py:194`；`+ Add path` = `project_tab.py:841-864`，`−` = `:866-880` | Project tab → Paths 组 | profile | 模板的路径词汇表是工艺绑定的；Recipe 要跨工艺就不能装路径 |
| `layer_map` | `config.py:198` | Project tab → Output 组（`project_tab.py:81`，文件选择器 `_FILE_FIELDS:87`） | profile | 目标模型逐字列出：layer map 属 Profile |
| `env_overrides.<VAR>` | `config.py:200` | Project tab → Environment resolution 表的 `Override` / `Clear` 按钮（`project_tab.py:951-984`，表体 `:412-436`） | profile | 就是「env 解析 + 每项 ✓/✗ + 修法」那套体检的修法入口 |
| `extraction_output_dir` | `config.py:208` | Project tab → Output 组（`project_tab.py:82`） | profile | EDA 侧输出目录约定（Cadence 库区 `QCI_PATH_*`），和 deck/layer map 同层的路径约定。注意：文档里推荐的 `{task_id}` 区分符（`docs/CONFIG_GLOSSARY.md:134`）随 Run 目录接管隔离而作废 |
| `intermediate_dir` | `config.py:209` | Project tab → Output 组（`project_tab.py:83`） | profile | 串行 EDA 调用的 cwd + si.env 暂存位，属环境路径约定 |
| `dspf_out_path`（project 层） | `config.py:220`；预设列表 `ui/widgets/dspf_out_path_combo.py:_PROJECT_PRESETS` | Project tab → Output 组的可编辑 combo + 解析预览（`project_tab.py:360-365`） | profile | **路径**归 Profile；「要不要产 DSPF」是 Recipe 的输出形式字段，两者拆开 |
| Templates tab 的 `Inventory` 子页（env_var / literal / user_defined / jinja 四类占位符 + ok/override/missing/info 状态） | `ui/tabs/templates_tab.py:231-241`, `_populate_inventory_table:415-468`, 状态色 `:96-101`, 判定逻辑 `ui/templates_view.py:142-167` | Templates tab 右侧 `Inventory` 页 | profile | 它本质就是「每项 ✓/✗」的体检报告，只是今天挂在模板上。并入 Profile 体检页，去掉 manifest-knob 那一路判定 |
| `init-project --raw-calibre` | `cli.py:295-303` | 新建项目向导第 1 页文件选择（`ui/widgets/init_wizard.py:210-236`） | profile | 从 raw export 反解 `tech_name` / `paths.*` 正是 Profile 的「扫描发现」；改造成 Profile 引导的输入 |
| `init-project --raw-quantus` | `cli.py:304-312` | 同上 | profile | 同上 |
| `init-project --raw-si` | `cli.py:313-321` | 同上 | profile | 同上 |
| `init-project --raw-jivaro` | `cli.py:322-333` | 同上（可选项） | profile | 同上 |

---

## 目标去向 = `recipe`（Recipe 配方：用户真会调的提取条件）

| 设置名 | 现在住在哪 | 现在在 GUI 哪里露出 | 目标去向 | 判定理由 |
|---|---|---|---|---|
| `jivaro.enabled` | `config.py:37`（`JivaroConfig`），spec 字段 `config.py:257` | Tasks tab → `jivaro (spec default)` 组的 `enabled` 勾选（`tasks_tab.py:288-292`） | recipe | 目标模型「缩减（jivaro on-off）」逐字对应 |
| `jivaro.frequency_limit` | `config.py:38` | Tasks tab → 同组 `frequency_limit`（`tasks_tab.py:293-300`） | recipe | 「缩减 … 频率」 |
| `jivaro.error_max` | `config.py:39` | Tasks tab → 同组 `error_max`（`tasks_tab.py:301-306`） | recipe | 「缩减 … 误差」 |
| `lvs_variant`（`wodio`/`widio`） | `templates/calibre/calibre_lvs.qci.j2.manifest.yaml`，模板引用 `calibre_lvs.qci.j2:1` | Templates tab → `Knobs` 子页；Tasks tab → `knobs (advanced)` 组的 calibre 段 | recipe | 目标模型「LVS 设置（deck variant）」。语义值留 Recipe，`wodio→实际 rules 文件` 的落地映射进 Profile 的「LVS deck variant 表」 |
| `connect_by_name` | 同一 manifest；模板 `calibre_lvs.qci.j2:36`（`[% if connect_by_name %]`） | 同上 | recipe | 目标模型「LVS 设置（connect_by_name）」逐字对应 |
| `exclude_floating_nets_limit` | `templates/quantus/ext.cmd.j2.manifest.yaml` + `dspf.cmd.j2.manifest.yaml`；模板 `ext.cmd.j2:23` | Templates tab → `Knobs` 子页；Tasks tab → `knobs` 组 quantus 段；`config/project.yaml` 的 `knobs.quantus` 块；`config/tasks.yaml` 的 per-task `knobs` 块 | recipe | 目标模型「floating nets 上限」 |
| `coupling_cap_threshold_absolute` | 两个 quantus manifest；模板 `ext.cmd.j2:25` | 同上 | recipe | 目标模型「耦合阈值」 |
| `coupling_cap_threshold_relative` | 两个 quantus manifest；模板 `ext.cmd.j2:26` | 同上 | recipe | 目标模型「耦合阈值」 |
| `min_res` | 两个 quantus manifest；模板 `ext.cmd.j2:29` | 同上 | recipe | 目标模型「min_res」 |
| `temperature` | 两个 quantus manifest；模板 `ext.cmd.j2:63-64` | 同上 | recipe | 目标模型「温度」 |

---

## 目标去向 = `cells`（Cells 表：DUT 属性）

| 设置名 | 现在住在哪 | 现在在 GUI 哪里露出 | 目标去向 | 判定理由 |
|---|---|---|---|---|
| `library` | `config.py:240`（`str \| list[str]`） | Tasks tab → `Axes (cartesian expansion)` 组的 TagListEdit（`tasks_tab.py:74`, `:210-220`） | cells | DUT 身份第一列。列表形态降级为「批量添加」的输入便利，添加时立即展开成明确行 |
| `cell` | `config.py:241` | 同上 | cells | DUT 身份 |
| `lvs_layout_view` | `config.py:243` | 同上 | cells | DUT 身份（layout view 列） |
| `lvs_source_view` | `config.py:242`（默认 `schematic`） | 同上 | cells | DUT 身份（source view 列） |
| `ground_net` | `config.py:245`（默认 `vss`） | Tasks tab → `Scalars` 组（`tasks_tab.py:247-251`） | cells | 目标模型 Cells 表逐字列出 `ground net` |
| `out_file` | `config.py:246` | Tasks tab → `Scalars` 组（`tasks_tab.py:252-257`） | cells | 目标模型 Cells 表逐字列出 `out_file`；同时是 jivaro `inputView` 的必填依赖（`runner.py:901-906`） |
| Spec 列表的 `+` / `copy` / `−` / `↑` / `↓` | `tasks_tab.py:166-193`, `_on_add_spec:1159`, `_on_copy_spec:1173`, `_on_remove_spec:1192`, `_move_spec:1208` | Tasks tab 左栏按钮条 | cells | 变成 Cells 表的增删行/复制行。`copy` 的 tooltip 里「同 cell 不同 knob」的用法随 knob 删除而失效，改由「同 Cells 行 × 不同 Recipe」表达 |
| `init-project --cell` | `cli.py:349-353` | 新建项目向导 → Advanced 组 `cell:`（`init_wizard.py:170/176`） | cells | 它写出的是那条一行 `tasks.yaml`，新模型里就是种下第一行 Cells |
| `init-project --library` | `cli.py:354-358` | 同上 `library:`（`init_wizard.py:171/177`） | cells | 同上 |

---

## 目标去向 = `runtime`（运行时选项；含 Run 对话框选项与一次性工具的调用参数）

| 设置名 | 现在住在哪 | 现在在 GUI 哪里露出 | 目标去向 | 判定理由 |
|---|---|---|---|---|
| `jobs` / `--jobs` `-j` | `run_tab.py:223-227`（QSpinBox 1–64）；`cli.py:97-106` | Run tab 顶栏 `Jobs:` | runtime | 并行度是本次跑的资源决策，不该写进任何持久对象 |
| stage 勾选（si/strmout/calibre/quantus/jivaro） | `run_tab.py:245-253`, `_selected_stages:397`；`cli.py --stage:64-69` | Run tab 左栏 `Stages` 组 | runtime | 「这次只跑哪几步」是运行时范围 |
| `Dry run` | `run_tab.py:256`；`cli.py --dry-run:75-79` | Run tab 左栏勾选框 | runtime | 目标模型点名的 runtime 例子 |
| Tasks 勾选列表 | `run_tab.py:238-243`, `_selected_tasks:387`；`cli.py --task:59-63` | Run tab 左栏 `Tasks` 组 | runtime | 「这次跑哪些 Cells 行」是本次 Run 的选择集，写进 `run.json` 的 DUT 快照 |
| `continue_on_lvs_fail` | `config.py:258`（per-task），CLI 全局覆盖 `cli.py:70-74` + `run:152-153` | Tasks tab → `Scalars` 组勾选框（`tasks_tab.py:258-262`） | runtime | 它不改变任何生成文件的内容，只改编排行为；per-task 粒度从来没有真实用途（CLI 只提供全局强制）。降为 Run 对话框一个勾选 |
| `Auto-follow current stage` | `run_tab.py:261-264`, `_on_auto_follow_toggled:585` | Run tab 左栏勾选框 | runtime | 纯查看偏好，跟着 Run 视图走 |
| `Follow tail` | `ui/tabs/log_tab.py:54-59`（LogTab 内嵌于 Run tab，`run_tab.py:310`） | Run tab 右下日志面板表头 | runtime | 同上 |
| `Open config dir...` / `Reload` | `run_tab.py:215-217`, `_browse_config_dir:328`；空状态 banner 的 `Open existing project...` / `New project...`（`run_tab.py:201-204`） | Run tab 顶栏 + 空状态横幅 | runtime | 项目打开/重载是会话动作 |
| `--config-dir`（run / gui / check-env） | `cli.py:50-58` / `:223-231` / `:969-977` | 无（CLI）/ GUI 由 Open 按钮代替 | runtime | 定位参数。`check-env` 命令本身并入 Profile 体检 |
| `--auto-ext-root` | `cli.py:80-84`（run）/ `:232-235`（gui） | 无 | runtime | 输出根（`runs/`、`logs/`）位置，随调用给定；新模型下是 `runs/<ISO>_<slug>/` 的父目录 |
| `--workarea` | `cli.py:85-89`（run）/ `:236-240`（gui） | 无 | runtime | EDA cwd，随调用给定 |
| `--no-progress` | `cli.py:107-112` | 无 | runtime | 输出呈现方式（CI/非 TTY） |
| `-v` / `--verbose` | `cli.py:113` | 无 | runtime | 日志级别 |
| `--remember-config` / `--no-remember-config` | `cli.py:242-250`（QSettings 持久化最近 config_dir） | 无（行为体现在 GUI 启动时自动加载）；向导末页 `auto_load` 勾选 `init_wizard.py:560` | runtime | 会话便利，不是配置内容 |
| `init-project --force` | `cli.py:359-366` | 新建项目向导第 1 页勾选（`init_wizard.py:123-126`） | runtime | 一次性工具的写入安全开关 |
| `import --tool` | `cli.py:496-500` | Templates tab → `Generate Template from Raw...` 对话框内的格式选择（`ui/widgets/template_generator.py`） | runtime | catalog 建设工具的调用参数，不落 Profile/Recipe/Cells 任何一层 |
| `import --input` | `cli.py:501-509` | 同上（拖拽区 `ui/widgets/drop_zone.py`） | runtime | 同上 |
| `import --output` | `cli.py:510-515` | 同上 | runtime | 同上 |
| `import --cell` / `--library` / `--lvs-layout-view` / `--lvs-source-view` | `cli.py:516-519` | 同上（identity 覆盖字段） | runtime | 只用于告诉 importer 该把哪些字面量替换成占位符，不持久化 |
| `import --fresh` | `cli.py:520-524` | 同上 | runtime | 一次性工具的合并策略开关 |
| Run tab 状态树右键 `View log file` / `Open rendered template` / `Open LVS report` | `run_tab.py:613-710`，路径推导 `_stage_log_path:571`、`rendered_path_for`（`core/runner.py:302-339`）、`lvs_report_path_from_runset`（`tools/calibre.py`） | Run tab 右侧状态树右键菜单 | runtime | 保留，但三条路径全部改指 `runs/<ISO>_<slug>/{logs,rendered,results}/`，不再指 `logs/task_<id>/` 与 `runs/task_<id>/` |

---

## 目标去向 = `delete`

| 设置名 | 现在住在哪 | 现在在 GUI 哪里露出 | 目标去向 | 判定理由 |
|---|---|---|---|---|
| `project.knobs.<stage>.<name>` | `config.py:226`；写回 `config.py:641-658` + `_apply_doubly_nested_edit:673-700`；`config/project.yaml` 尾部 `knobs:` 块 | Templates tab → `Knobs` 子页每行（`templates_tab.py:470-518`, 写回 `:596-600`） | delete | knob 四层机制整体删除；语义内容改由 Recipe 显式字段承载 |
| `tasks.yaml[i].knobs.<stage>.<name>` | `config.py:265`（spec）/ `:298`（TaskConfig）/ 深拷贝 `:485` | Tasks tab → `knobs (advanced — per-task overrides)` 折叠组（`tasks_tab.py:359-390`, `_rebuild_knobs_form:880`, `_build_knob_section:923`, `_on_task_knob_changed:1004`） | delete | 同上；per-task 层在新模型里由「另建一条 Recipe + 另跑一个 Run」表达 |
| `--knob <stage>.<name>=<value>` | `cli.py:90-96`，解析 `_parse_cli_knobs:1031-1058`，注入 `run:156/205` → `runner.run_tasks:176/231/247` → `_run_task:353/426` → `_render_stage:494/514-526` | 无（CLI） | delete | knob 四层的最高层，随机制一起删 |
| manifest `default` 层本身（`<name>.j2.manifest.yaml`） | `core/manifest.py`（整模块）+ `templates/*/*.j2.manifest.yaml`（5 个） | Templates tab → `Knobs` 子页的 `(default: X)` 提示、unit/range 提示 | delete | 「哪些参数能调取决于谁手写了 manifest」正是要根除的病根 |
| `project.templates.si` / `.calibre` / `.quantus` / `.jivaro` | `config.py:85-131`（`TemplatePaths` + 两个 validator）、`:221`；合并 `_merge_templates:435-444`；写回白名单 `config.py:580` + 反斜杠归一化 `:616-621` | Project tab → `Templates` 组 4 个 ComboBox + `×`（`project_tab.py:386-410`, `:471-536`）；Templates tab → `project.templates` 4 行 QLineEdit + `…`（`templates_tab.py:190-208`, `:555-594`） | delete | 「哪个 .j2 渲染哪个 stage」不该是用户设置；改由 Recipe 的语义字段（输出形式 / LVS deck variant）在 catalog 内部映射 |
| `tasks.yaml[i].templates.<stage>`（per-task 模板覆盖） | `config.py:244`（spec）/ `:287`（TaskConfig） | Tasks tab → `templates (per-task overrides)` 折叠组（`tasks_tab.py:314-354`, `:777-878`） | delete | 明确在删除清单里；per-DUT 换模板是旧逃生舱，被 Recipe patch 取代 |
| `tasks.yaml[i].exclude[]`（`library`/`cell`/`lvs_layout_view`/`lvs_source_view` 选择器） | `config.py:56-82`（`ExcludeMatch`）、`:269`；判定 `_is_excluded:499-516`；零任务报错 `:491-495` | Tasks tab → `Cartesian expansion preview` 每行的 `include` 勾选框（`tasks_tab.py:450-472`, `_on_preview_toggled:1305`, `_selector_matches:1423`, `_minimal_selector:1443`） | delete | 展开在添加时完成、落成明确行后，「先笛卡尔再挖洞」的选择器无处可用 |
| `tasks.yaml[i].jivaro_overrides.<cell>.{enabled,frequency_limit,error_max}` | `config.py:42-53`（`JivaroOverride`）、`:274`；合并 `_merge_jivaro_override:519-533` + `_expand_spec:468-470` | Tasks tab → `jivaro_overrides (advanced — per-cell tweaks)` 折叠表（`tasks_tab.py:394-436`, `_rebuild_override_table:677-746`, `:1097-1155`） | delete | 明确在删除清单里；per-cell 偏差改由「该 cell 单独用另一条 Recipe 跑一个 Run」表达 |
| `tasks.yaml[i].dspf_out_path`（per-task 覆盖） | `config.py:262`（spec）/ `:297`；解析 `runner._resolve_dspf_out_path:806-840`，env 发现 `:858-861` | Tasks tab → `Scalars` 组的 dspf combo，index 0 为 `(default: X)` 哨兵（`tasks_tab.py:268-281`, `_refresh_dspf_default_hint:537`, `_on_dspf_value_changed:641`） | delete | project 层的 `{cell}` 格式键已覆盖全部真实需求；per-task 覆盖只是旧的 5 套覆盖机制之一 |
| `tasks.yaml[i].label` | `config.py:256`（spec）/ `:294`（TaskConfig） | Tasks tab → `Scalars` 组第一行 `label:`（`tasks_tab.py:231-246`）；Run tab 任务列表/状态树显示（`run_tab.py:72-84`, `:363-368`, `:493-497`）；Log 头（`run_tab.py:153-183`）；spec 列表行前缀（`tasks_tab.py:1372-1373`） | delete | 它存在的唯一理由是「`task_id` 太丑」。Run 有自己的 `<ISO时间戳>_<slug>` 身份，Cells 行本身就是可读的表格行 |
| `task_id`（`f"{library}__{cell}__{layout}__{src}"`） | `config.py:282`，生成 `:473`，撞名只 warn `_warn_on_duplicate_task_ids:536-540` | 所有任务列表/状态树/日志目录名 | delete | 现状问题 #2 的根源：复制 spec 必撞 id。身份改由 Run（时间戳+slug）+ Cells 行 id 承担 |
| `spec_index` / `expansion_index` | `config.py:299-300`，赋值 `:486-487/490` | 无（内部，但泄漏进 Tasks tab 预览的 spec 编号 `_summarize_spec:1361`） | delete | 是笛卡尔展开的副产品，展开消失后无意义 |
| Templates tab 的模板列表（bound 在前 + `[unused]` 灰行） | `templates_tab.py:213-227`, `_format_entry_label:338`, `ui/templates_view.py:collect_template_entries` | Templates tab 左栏列表 | delete | 「bound / unbound」概念随四个绑定 slot 一起消失 |
| Templates tab → `Knobs` 子页（整页） | `templates_tab.py:243-254`, `_populate_knobs_form:470-518`, `_clear_knobs_form:520`, `_stage_for_selected_path:524`, `_effective_project_knobs:532-551` | Templates tab 右侧 `Knobs` 页 | delete | knob 机制的主 UI |
| 右键 `Edit...`（就地改模板并写回文件） | `templates_tab.py:703-715`, `_invoke_edit_template:778-794`；实现 `ui/widgets/diff_editor.py:open_for_edit` | Templates tab 列表右键 | delete | 被 patch 逃生舱取代：用户在**生成结果**上编辑，系统存相对生成结果的 diff，而不是永久改写 catalog 模板 |
| 右键 `Copy...`（clone 成新模板 + sidecar） | `templates_tab.py:717-729`, `_prompt_clone_suffix:827-860`, `_invoke_copy_template:862-903`；实现 `core/clone_template.py` | Templates tab 列表右键 | delete | 明确在删除清单里；「整文件另存为新模板」= 永久 fork，正是 patch 模式要取消的 |
| 右键 `Delete...`（删 .j2 + sidecar） | `templates_tab.py:731-742`, `_delete_blocked_reason:752-776`, `_invoke_delete_template:796-823` | Templates tab 列表右键 | delete | clone 的配套善后动作，随之消失 |
| `+ Save as preset` 按钮 + preset 存储 | `ui/widgets/diff_editor.py:249-251/483/559-592`；`core/preset.py`（整模块）；`templates/presets/` 目录 | 模板编辑对话框底部按钮 | delete | 明确在删除清单里 |
| Preset 选择对话框（`applicable_tool` 过滤、预览、应用） | `ui/widgets/preset_picker.py`（整文件，299 行） | **无任何调用点**——只有 `tests/ui/test_preset_picker.py` 引用 | delete | 已经是死代码，从来没接进任何 tab |
| `Template Diff Viewer...` 按钮 | `templates_tab.py:170-176`, `_on_open_diff_viewer:639-650`；实现 `ui/widgets/template_diff_viewer.py` | Templates tab 顶部工具条 | delete | 从用户日常入口删除；`core/diff_template.py` 本体保留为我们建 catalog 的一次性工具 |
| `Generate Template from Raw...` 按钮 | `templates_tab.py:177-185`, `_on_open_template_generator:652-661`；实现 `ui/widgets/template_generator.py` | Templates tab 顶部工具条 | delete | 同上：`core/importer.py` + `template_generator` 保留但改定位为 catalog 建设工具，不再是用户日常入口 |
| `knob suggest <TEMPLATE>` + `--all` | `cli.py:650-721`（子命令注册 `:32-37`） | 无（CLI）；Templates tab 的 "(no manifest sidecar — `auto_ext knob suggest` …)" 提示文案 `templates_tab.py:475-479` | delete | 明确在删除清单里 |
| `knob promote <TEMPLATE> <KEY...>` + `--type` + `--name` | `cli.py:724-891` | 无（CLI） | delete | 明确在删除清单里 |
| `init-project --output-config-dir` | `cli.py:334-339` | 新建项目向导第 1 页（`init_wizard.py:99-109`） | delete | `project.yaml + tasks.yaml` 的「项目骨架」概念随三层 config 消失；Profile / Recipe / Cells 各有自己的存放位置 |
| `init-project --output-templates-dir` | `cli.py:340-348` | 新建项目向导第 1 页（`init_wizard.py:111-121`） | delete | 模板不再 per-project 落地，改由共享 catalog 提供 |
| `migrate` 子命令 | `cli.py:279-287`（stub，直接 exit 2）；`auto_ext/migrate.py`（`raise NotImplementedError`） | 无 | delete | 从 Phase 1 至今没实现过，`Run_ext.txt` 迁移已被 init-project 的 raw 导入路径替代 |
| `ProjectConfig.source_path` / `ProjectConfig.raw` | `config.py:228-229`（`exclude=True`） | 无（内部，服务 ruamel 注释保留的写回路径 `dump_project_yaml:543`, `apply_project_edits:589`, `apply_tasks_edits:719`） | delete | 非用户可见；随「两文件 YAML + 注释保留写回」这套机制一起重建，列出以免遗漏 |

---

## 附录 A：非设置类控件（不参与去向判定，但删除时会牵动）

| 控件 | 位置 | 处置 |
|---|---|---|
| `💾 Save` / `↶ Revert` / `● unsaved` 标记 | `project_tab.py:316-321/1013-1027`；`tasks_tab.py:128-133/1347`；`templates_tab.py:155-160/627` | 三处重复实现，随三层 config 合并成一处 |
| 自动保存（focus-out 即写盘） | `project_tab.py:_maybe_autosave:1029-1052` | 保留语义，宿主对象改为 Recipe / Cells |
| 「运行中禁止保存」/「外部改动强制覆盖」对话框 | `project_tab.py:988-1011`；`tasks_tab.py:1324-1345`；`templates_tab.py:604-625`；`run_tab.py:407-421` | 保留 |
| `▶ Run` / `✕ Cancel` | `run_tab.py:266-274`, `_start_run:400`, `_cancel_run:479` | 保留；`_start_run` 改为创建 Run 对象并快照 Recipe |
| 状态树 / 空状态 banner / `Live status` | `run_tab.py:187-208/288-303/487-505` | 保留；行标识从 `task_id` 改为 Cells 行 + Run |

## 附录 B：Recipe 要求、但今天根本不是设置项（写死在模板字面量里）

| Recipe 字段 | 今天的字面量位置 | 说明 |
|---|---|---|
| 提取类型（`type`） | `templates/quantus/ext.cmd.j2:12` `-type "rc_coupled"`；`dspf.cmd.j2:12` 同 | 硬编码，无 knob |
| corner（`RCWORST`/`TYPICAL`…） | `ext.cmd.j2:59-60` `-technology_corner "TYPICAL"`；`dspf.cmd.j2:69` 同 | 用户点名的病例：同一条语句里 `-temperature` 能改、`-technology_corner` 不能改 |
| 输出形式（extracted_view / dspf / 两者） | `ext.cmd.j2:41` `output_db -type extracted_view` vs `dspf.cmd.j2:41-42` `output_db -type dspf -subtype extended` | 今天只能靠「换绑哪个 .j2」表达，是四个绑定 slot 的唯一真实用途 |
| metal fill | 无（模板里完全不存在） | 纯新增字段 |
| floating nets 开关（区别于上限） | `ext.cmd.j2:22` `-exclude_floating_nets true`（写死），只有 `-exclude_floating_nets_limit` 是 knob | 半个 knob 半个字面量 |
| jivaro 其余缩减参数 | `templates/jivaro/default.xml.j2`：`criterion=standard`、`reduceFloatingNets=false`、`decouplingAutoThreshold=false`、`viewsToReduce=av_extracted`、`cpu=1` 全部写死，manifest 是 `knobs: {}` | jivaro 模板一个 knob 都没声明，印证「能不能调取决于谁写了 manifest」 |

---

# 删除清单的连带影响

### 1. knob 四层机制 + `core/manifest.py`
**整文件删除**
- `auto_ext/core/manifest.py`（482 行）
- `auto_ext/ui/widgets/knob_editor.py`（194 行）
- `templates/calibre/calibre_lvs.qci.j2.manifest.yaml`、`templates/quantus/ext.cmd.j2.manifest.yaml`、`templates/quantus/dspf.cmd.j2.manifest.yaml`、`templates/si/default.env.j2.manifest.yaml`、`templates/jivaro/default.xml.j2.manifest.yaml`
- `tests/core/test_manifest.py`（整）、`tests/ui/test_knob_editor.py`（整）

**需要改动**
- `auto_ext/core/config.py` — `ProjectConfig.knobs:226`、`TaskSpec.knobs:265`、`TaskConfig.knobs:298`、`_expand_spec` 的 `copy.deepcopy(spec.knobs):485`、`_KNOB_STAGES:586`、`apply_project_edits` 的三段 key 分支 `:641-658`、`_apply_doubly_nested_edit:673-700`（删掉后 `apply_project_edits` 只剩 1/2 段 key）；`import copy:15` 可能变成未使用
- `auto_ext/core/runner.py` — `from ...manifest import load_manifest, resolve_knob_values:66`；`run_tasks(cli_knobs=...)` 签名 `:176`、`:231`、`:247`；`_run_task(cli_knobs)` `:353`、`:426`；`_render_stage(cli_knobs)` `:494`、渲染前的 knob 解析 `:514-526`（`knobs=stage_knobs` 从 context 拿掉）；文档串 `:196-198`
- `auto_ext/tools/base.py:218-224` — dry-run 分支里的 `load_manifest` + `resolve_knob_values(manifest, {}, {}, {})`
- `auto_ext/cli.py` — `knob_app` 注册 `:32-37`；`run --knob:90-96`、`:156`、`:205`；`_parse_cli_knobs:1031-1058`；`knob_suggest:650-721`；`knob_promote:724-891`；`_build_review_report:924-931/952-954`（"Knob candidates" / "Next steps" 两节）；`import_cmd:538-647` 里 manifest 读写与 merge 分支；模块 docstring `:11-12`
- `auto_ext/ui/tabs/templates_tab.py` — import `:62-67`、`:85`；Knobs 子页构建 `:243-254`；`_refresh_inventory_and_knobs:377-385`；`_populate_inventory_table` 的 `manifest` 参数 `:415-450`；`_populate_knobs_form:470-518`；`_clear_knobs_form:520-522`；`_stage_for_selected_path:524-530`；`_effective_project_knobs:532-551`；`_on_knob_changed:596-600`；`_invoke_delete_template:798` 的 sidecar 处理；类 docstring `:1-19`
- `auto_ext/ui/tabs/tasks_tab.py` — import `:54-58/67`；`_KNOB_STAGES:78`（注意它同时被 templates 组复用，删两处要一起）；knobs 折叠组 `:359-390`；`_clear_knobs_form:750-757`；`_rebuild_knobs_form:880-921`；`_build_knob_section:923-973`；`_manifest_for_stage:975-1002`；`_on_task_knob_changed:1004-1028`；`_on_task_template_changed:870-874`（切模板后重建 knobs 的联动）；`_clear_editor:673`
- `auto_ext/ui/templates_view.py` — `from ...manifest import TemplateManifest, _IDENTITY_KEYS:32`；`jinja_variable_status:142-167`（`manifest.knobs` 判定分支）；模块 docstring `:20`
- `auto_ext/core/importer.py` — `from ...manifest import KnobSpec, TemplateManifest:31`；`ImportResult.auto_knobs:107`；`_detect_candidates` / `_CAND_PATTERNS` / `_classify_value` / `_coerce_literal` / `_snake_case` / `_substitute_at_key`；`auto_knobs:338`；`_auto_calibre_connect_by_name:375-423`；`_auto_calibre_lvs_variant:425-445`；`merge_reimport:837-910`。**注意**：`connect_by_name` / `lvs_variant` 两个自动 knob 现在是 Recipe 字段的来源，importer 的这两段要改成「填 Recipe 字段」而不是删
- `auto_ext/core/init_project.py:39/261-275` — 不再写 sidecar manifest
- `auto_ext/ui/widgets/diff_editor.py:47-51/455-457/520/599-609` — 侧车 manifest 的读、写、报错路径
- `auto_ext/ui/widgets/template_generator.py:81-85/647-701` — 同上
- `auto_ext/core/clone_template.py:37/152-186` — sidecar 复制/删除（该模块本身也在删除清单，见 §3）
- `config/project.yaml`（尾部 `knobs:` 块 + 上方 26 行注释）、`config/tasks.yaml`（per-task `knobs:` 块 + 头部映射注释）
- 测试：`tests/test_cli_run.py:165-320`（`_parse_cli_knobs` 5 例 + `test_run_knob_beats_manifest_default` + `assert "knobs:" in manifest_text`）、`tests/ui/test_tasks_tab.py:396-465`、`tests/ui/test_templates_tab.py:246-307`、`tests/core/test_importer.py:501-509`、`tests/ui/test_templates_view.py:111-119`、`tests/core/test_config.py`、`tests/test_cli_init_project.py`、`tests/ui/test_template_generator.py:329/408/446`、`tests/ui/test_diff_editor.py:171`
- 文档：`docs/OFFICE_QUICKSTART.md`（§候选 knob + 提升 `:141-166`、§5.7 `:278-290`、`:340`）、`docs/GUI_GUIDE.md:172-226/288-289`、`docs/OFFICE_VALIDATION.md` Step 4 `:138-190`、`:249/261`、`docs/CONFIG_GLOSSARY.md:134/183-193`（优先级链一节）

### 2. `core/preset.py` + `ui/widgets/preset_picker.py`
- **整文件删除**：`auto_ext/core/preset.py`（404 行）、`auto_ext/ui/widgets/preset_picker.py`（299 行）、`tests/core/test_preset.py`、`tests/ui/test_preset_picker.py`、`templates/presets/`（含 `.gitkeep`）
- `auto_ext/ui/widgets/diff_editor.py` — `from ...preset import save_preset:53`；`_save_preset_btn:249-251/255`；`:483`（enable 联动）；`_on_save_preset:559-585`；`_prompt_for_preset_slug:587-592`；docstring `:661`
- `auto_ext/core/clone_template.py:12/27` — `presets` 作为合法 stage 目录之一的白名单
- `auto_ext/core/diff_template.py:95` — docstring 里的 preset 说明
- `tests/ui/test_diff_editor.py:83`（按钮 disabled 断言）、`:216-235`（save-as-preset 用例）；`tests/core/test_clone_template.py:84/161/240`（presets stage 参数化）；`tests/ui/test_clone_template_dialog.py:394-425`（无 sidecar 的 preset 克隆）
- 注意：`ui/widgets/dspf_out_path_combo.py` 里的 "preset" 是**另一个概念**（dspf 路径下拉的预设项），不受影响

### 3. `core/clone_template.py`
- **整文件删除**：`auto_ext/core/clone_template.py`（201 行）、`tests/core/test_clone_template.py`、`tests/ui/test_clone_template_dialog.py`
- `auto_ext/ui/tabs/templates_tab.py` — import `:53-59`；右键菜单 `Copy...` `:717-729` 与 `Delete...` `:731-742`；`_delete_blocked_reason:752-776`；`_invoke_delete_template:796-823`；`_prompt_clone_suffix:827-860`；`_invoke_copy_template:862-903`；`templates_changed` 信号 `:111-115`
- `auto_ext/ui/widgets/diff_editor.py` — `open_for_save_as_new`（`:654-` 起的 `SaveAsNewDialog` 一路）、docstring `:796`
- `auto_ext/ui/main_window.py` — `templates_changed` → `TasksTab.refresh_template_combos` 的接线（与 §4 同批）
- `auto_ext/ui/tabs/tasks_tab.py:759-775` — `refresh_template_combos` 这个公开 hook 失去唯一调用方

### 4. 四个绑定 slot（`project.templates` + per-task `templates`）
- `auto_ext/core/config.py` — `TemplatePaths:85-131`（含 `_normalize_separators` / `_reject_relative_traversal` 两个 validator）；`ProjectConfig.templates:221`；`TaskSpec.templates:244`；`TaskConfig.templates:287`；`_merge_templates:435-444`（`_expand_spec:458` 的调用）；`_EDIT_NESTED_KEYS["templates"]:580`；`apply_project_edits` 反斜杠归一化 `:616-621`
- `auto_ext/ui/tabs/project_tab.py` — `_TEMPLATE_STAGES:91`；Templates 组构建 `:386-410`；`_rebuild_template_combos:471-521`；`_on_template_combo_changed:523-532`；`_on_template_clear_clicked:534-536`；`_collect_used_by_index:593-618`（Paths 组的 "Used by" 靠遍历 `project.templates` 拿模板集，改为遍历 Recipe 解析出的模板集）；`_on_config_loaded:455`
- `auto_ext/ui/tabs/templates_tab.py` — `_TEMPLATE_TOOLS:91`；path picker `:190-208`；`_refresh_path_edits:286-294`；`_on_path_edited:555-561`；`_on_browse_clicked:563-594`；`_delete_blocked_reason:760-764`（"bound 就不许删"）
- `auto_ext/ui/tabs/tasks_tab.py` — templates 折叠组 `:314-354`；`refresh_template_combos:759-775`；`_rebuild_template_combos:777-849`；`_on_task_template_changed:851-874`；`_on_task_template_clear:876-878`；`_clear_editor:667-672`
- `auto_ext/ui/templates_view.py` — `TemplateEntry.in_project` / `.tool`、`collect_template_entries`（bound/unbound 排序整套）
- `auto_ext/core/runner.py` — 取模板的入口（`_render_stage:494-526`、`rendered_path_for:302-339` 用 `project.templates` 推 stem）、`_discover_env_vars:864`（遍历四个 stage 的模板扫 env）
- `auto_ext/core/template.py` — `resolve_template_path` / `enumerate_stage_templates` 保留，但调用点全部改为 catalog 解析
- `config/project.yaml:16-20`、`examples/demo/project.yaml:28-32`
- 测试：`tests/ui/test_templates_tab.py`、`tests/ui/test_project_tab.py`、`tests/ui/test_tasks_tab.py`、`tests/core/test_config.py`、`tests/core/test_template.py`、`tests/test_path_safety.py`（`..` traversal 用例针对 `TemplatePaths` validator）、`tests/test_runner.py`、`tests/test_integration_e2e.py`

### 5. `exclude`
- `auto_ext/core/config.py` — `ExcludeMatch:56-82`（含 `_must_set_at_least_one`）；`TaskSpec.exclude:269`；`_expand_spec:466`；`_is_excluded:499-516`；「exclude 把组合全删光」的报错 `:491-495`
- `auto_ext/ui/tabs/tasks_tab.py` — import `ExcludeMatch:47`；预览表 `include` 列 `:450-472`；`_refresh_preview:1240-1274`（`excludes` / `included_task_ids` / 删除线着色）；`_on_preview_toggled:1305-1320`；`_selector_matches:1423-1440`；`_minimal_selector:1443-1458`；`_full_cartesian:1276-1303`（整个「先全展开再挖洞」的预览模型）
- `auto_ext/ui/models.py` — `EXCLUDED_ROW_COLOR`
- 测试：`tests/core/test_config.py`（exclude 系列）、`tests/ui/test_tasks_tab.py`（预览勾选系列）

### 6. `jivaro_overrides`
- `auto_ext/core/config.py` — `JivaroOverride:42-53`；`TaskSpec.jivaro_overrides:274`；`_merge_jivaro_override:519-533`；`_expand_spec:468-470`
- `auto_ext/ui/tabs/tasks_tab.py` — import `JivaroOverride:48`；`_JIVARO_TRI_STATES:76`；折叠组 `:394-436`；`_rebuild_override_table:677-746`；`_on_axis_changed` 里 cell 轴变动触发重建 `:1058-1059`；`_on_override_enabled_changed:1097-1118`；`_on_override_num_changed:1120-1141`；`_on_override_cleared:1143-1155`；`_jivaro_summary_for:1416-1420`（预览表 `jivaro.enabled` 列的 `*` 标记）；`_enabled_to_index:1408-1413`；`_clear_editor:666`
- 测试：`tests/core/test_config.py`、`tests/ui/test_tasks_tab.py`

### 7. `label`
- `auto_ext/core/config.py:256/294`，`_expand_spec:482`
- `auto_ext/ui/tabs/run_tab.py` — `_task_display:72-84`；`_task_id_from_item:87-97`；`display_for_log_path:153-176`；`_on_stage_selected_for_log:178-183`；任务列表填充 `:363-368`；`_selected_tasks:392-394`；`_reset_status_tree:493-497`；右键菜单 `_on_tree_context_menu:642`
- `auto_ext/ui/tabs/log_tab.py:98-105`（`display_id` 参数与表头拼接）
- `auto_ext/ui/tabs/tasks_tab.py:231-246`（编辑框）、`:518`、`_on_scalar_edited:1069-1073`（空串落 `None`）、`_summarize_spec:1372-1373`
- `auto_ext/ui/main_window.py`（把 `display_for_log_path` 结果喂给 LogTab 的接线）
- 测试：`tests/ui/test_run_tab.py`、`tests/ui/test_tasks_tab.py`

### 8. per-task `dspf_out_path`
- `auto_ext/core/config.py:262/297`，`_expand_spec:484`
- `auto_ext/core/runner.py` — `_resolve_dspf_out_path:806-840` 里 `task.dspf_out_path or project.dspf_out_path` 的取优先级；`_discover_env_vars:858-861`（per-task 覆盖里的 env 扫描）
- `auto_ext/ui/tabs/tasks_tab.py:268-281`（combo）、`_refresh_dspf_default_hint:537-553`、`_build_extended_env_for_preview:555-614`、`_resolve_dspf_for_preview:616-639`、`_on_dspf_value_changed:641-651`
- `auto_ext/ui/widgets/dspf_out_path_combo.py` — `include_default_sentinel` 参数与 `(default: X)` 哨兵项一路（`:107/248/267-281/316-362`）；project 侧不用哨兵，删掉后该参数可去
- 测试：`tests/ui/test_tasks_tab.py:671-700/865-900`

### 9. `task_id` / `spec_index` / `expansion_index`（身份重建）
- `auto_ext/core/config.py:282/299-300`、生成 `:473`、`_warn_on_duplicate_task_ids:536-540`
- `auto_ext/core/runner.py` — `_task_run_dirs:285-299`（`runs/task_<safe_id>/` + `logs/task_<safe_id>/`）、`rendered_path_for:302-339`、`_run_task:360-457`（全程用 `task.task_id` 报进度）、`_validate_task_outputs:901-906`、`extraction_output_dir` 的 `{task_id}` 格式键 `:939-999`（含 `duplicate extraction_output_dir` 的报错文案 `:996-999`）
- `auto_ext/core/workdir.py:106-122`（`prepare_parallel_workdir(task_id)` + `_TASK_ID_UNSAFE` 清洗）
- `auto_ext/tools/base.py:104` — `log_path.open("w")`，每次重跑覆盖；改 Run 目录后天然不再覆盖，但这一行仍要显式改成 per-run 路径
- `auto_ext/ui/tabs/run_tab.py:69`（`_UNSAFE_TASK_ID`）、`_stage_log_path:571-583`、`display_for_log_path:153-176`、`_stage_items` / `_task_items` 的 key `:130-131`
- `auto_ext/ui/qt_reporter.py` / `auto_ext/core/progress.py` — 所有 `task_id: str` 的信号载荷
- `auto_ext/cli.py:59-63/135-146`（`--task` 按 `task_id` 过滤）、`_print_summary:1077-1080`
- `examples/runs/task_DEMO_LIB__inv__.../` 两个目录（示例产物，路径形态过期）
- 测试：`tests/test_runner.py`、`tests/test_runner_parallel.py`、`tests/core/test_workdir.py`、`tests/test_integration_e2e.py`、`tests/ui/test_run_tab.py`、`tests/ui/test_qt_reporter.py`、`tests/test_cli_run.py:278`

### 10. `migrate`
- `auto_ext/cli.py:279-287`（子命令）、`auto_ext/migrate.py`（整文件）、模块 docstring `:15`
- `config/tasks.yaml:3-20`（`Run_ext.txt` 字段映射注释块）、`docs/archive/Old_project_prompt.txt` 相关段落