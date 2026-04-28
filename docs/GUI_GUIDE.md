# Auto_ext GUI 使用说明

本文档对应当前 main（Phase 5.6.5 + 5.7 + 5.8 + dspf_out_path）的 GUI 状态：5 个 tab（Project / Templates / Tasks / Run / Log）全部可用，外加 5.6 的 Diff-mode 模板编辑器、5.6.2 的 TemplateDiffViewer 只读对比、5.6/5.7 的 PresetPicker / KnobEditor / InitWizard、5.8 的单文件 TemplateGenerator dialog，以及 Project/Tasks 两个 tab 上新增的 DspfOutPathCombo。

适用场景：Windows 开发机本地启动看 UI；不依赖 Cadence。

---

## 0. 前置条件（Windows 开发机）

仓库布局假设：

```
C:\code\Auto_ext\                 # 外层目录（= Linux deployment 上的 workarea）
├── .venv\                        # Python 3.11 venv，PyQt5 5.15.x 已装
└── Auto_ext\                     # 仓库根（auto_ext 包就在这下面）
    ├── auto_ext\
    ├── config\                   # project.yaml + tasks.yaml
    ├── templates\
    └── tests\
```

如果你不在这个布局，下文路径需要自行替换。

依赖确认（一次性）：

```powershell
C:\code\Auto_ext\.venv\Scripts\python.exe -c "import PyQt5, jinja2, ruamel.yaml, pydantic; print('deps ok')"
```

应输出 `deps ok`。报 ImportError 就先把 PyQt5 / pytest-qt 等装到 venv（参考项目记忆里的依赖列表）。

---

## 1. 启动 GUI

**PowerShell**：

```powershell
cd C:\code\Auto_ext
$env:PYTHONPATH = "C:\code\Auto_ext\Auto_ext"
.\.venv\Scripts\python.exe -m auto_ext gui --config-dir Auto_ext\config
```

**Git Bash**：

```bash
cd /c/code/Auto_ext
PYTHONPATH=/c/code/Auto_ext/Auto_ext /c/code/Auto_ext/.venv/Scripts/python -m auto_ext gui --config-dir Auto_ext/config
```

参数说明：

| flag | 说明 | 默认 |
|---|---|---|
| `--config-dir` | 包含 `project.yaml` + `tasks.yaml` 的目录 | 不传则启动后是空状态 |
| `--auto-ext-root` | `templates/` 所在的仓库根 | 默认从 `--config-dir` 上一级推断 |
| `--workarea` | EDA 命令的 cwd | 默认 `--auto-ext-root` 的上一级 |

**Windows 已知兼容性细节**：

- `run.sh` 是 Linux-only（处理 `LD_LIBRARY_PATH` + bundled Qt5），Windows 直接调 venv 里的 `python.exe` 即可。
- Windows 不需要 `LD_LIBRARY_PATH` 这一套；PyQt5 的 wheel 自带 Qt5 二进制。

**关于 templates 路径**：`config/project.yaml` 现在用 auto_ext-root-relative 写法（如 `templates/calibre/foo.qci.j2`），不带部署目录前缀。`resolve_template_path` 会按 `cwd → workarea → auto_ext_root` 顺序回退，所以：

- 老的 workarea-relative 写法（`Auto_ext_pro/templates/...`）依然能工作 —— 向后兼容
- 新写法在 Windows 本地（仓库名 `Auto_ext`）和 Linux 部署（仓库名可能叫 `Auto_ext_pro`）下都能解析，部署目录改名不影响

---

## 2. 五个 Tab 的当前能力

启动后窗口标题 `Auto_ext`；有未保存编辑时变 `Auto_ext *`。

### 2.1 Run（Phase 5.2 落地）

按选中的 task × stage 组合发起 dry-run 或真跑。

- 顶部 **Config** 栏：显示当前 config 目录；`[Open…]` 切换；`[Reload]` 重新加载磁盘
- **Jobs** 数字框：并行 task 数（1..64），= CLI 的 `--jobs N`
- 左面板：
  - tasks 列表（按 `tasks.yaml` 展开后的 task_id 一行一个，带 checkbox）
  - stages 勾选（si / calibre / quantus / jivaro，少勾就跳过）
  - **Dry-run** 勾选（不实际跑 EDA，只打印计划）
  - `[▶ Run]` / `[■ Cancel]` 按钮
- 右面板：QTreeWidget 状态树（task 父行 → stage 子行），按状态着色
- 点 stage 子行 → 自动跳到 Log tab 显示对应日志文件

不需要 Cadence 也能玩：勾 dry-run 就行；状态树会显示 `… dry run` 占位。

### 2.2 Log（Phase 5.2）

只读日志查看器：

- QPlainTextEdit + 暗色等宽主题
- `QFileSystemWatcher` + 1s fallback 定时器（网络挂载偶尔丢事件）
- 增量读取（按字节 offset），文件被截断会自动重置
- **Follow tail** 勾选（默认开），有新内容自动滚到底

通常你不会主动来这里 —— 从 Run tab 点 stage 行会自动跳过来。

### 2.3 Project（Phase 5.3）

`project.yaml` 的表单编辑器 + 环境变量解析面板。

- 顶部：config 路径标签 / `●` 脏标记 / `[Save]` / `[Revert]`
- 三个 GroupBox：
  - **Identity**：`work_root` / `verify_root` / `setup_root`（带 `[…]` 目录选择）/ `employee_id`
  - **PDK**：`tech_name`
  - **Output**：`layer_map`（文件选择）/ `extraction_output_dir` / `intermediate_dir`
- **Paths**（Phase 5.6.5）：每条 `project.paths.<key>` 一行；下面 `↳ <template>:<line>  <line excerpt>` 显示这个 path 在哪些模板里被引用（自动 grep），hover 看 tooltip 给 resolves to 预览 + 用法说明。`+ Add path` 加自定义 key、行末 `−` 删除。
- **Templates**：只读 summary（5.5 之前是这里编辑入口的占位；现在去 Templates tab 编辑）
- **Environment resolution** 表（核心面板）：
  - 列：`var / source / value / shell value / [Override] / [Clear]`
  - source 颜色：✓ shell（绿）/ ⇄ override（琥珀）/ ✗ missing（红）
  - `[Override]` 弹 QInputDialog 让你设值；`[Clear]` 清掉 override 回 shell 值
  - 这些 override 编辑同样进 dirty 队列，跟 form 编辑共享一个 Save

注释保留：编辑 → Save 后 `git diff` 应只看到值变化，文件顶部和字段间的注释保留。

### 2.4 Tasks（Phase 5.4）

`tasks.yaml` 的编辑器 + cartesian 展开预览。

- 顶部跟 Project 一样：path label / dirty / Save / Revert
- 上下分栏（QSplitter vertical）：
- 上半：
  - 左：spec 列表（一个 TaskSpec 一行）+ `[+]` `[−]` `[↑]` `[↓]` 按钮
  - 右：选中 spec 的编辑器，含：
    - **Axes**（4 个 TagListEdit chip 控件）：`library / cell / lvs_layout_view / lvs_source_view`
    - **Scalars**：`ground_net` / `out_file` / `continue_on_lvs_fail`
    - **jivaro (spec default)**：`enabled` / `frequency_limit` / `error_max`
    - **jivaro_overrides (per-cell)**：表格，行 = cell axis ∪ 已有 override key；过期 key（不在 axis 里）红色 + tooltip
- 下半：**Cartesian expansion preview** 表
  - 每行一个 `(library, cell, lvs_layout_view, lvs_source_view)` 组合
  - 第一列 checkbox：取消勾选 = 加进 `spec.exclude`，被排除行灰色 + 删除线（仍显示，便于反勾回来）
  - 末列显示 `jivaro.enabled`，cell 有 override 时带星号

最少留一个 spec：删到只剩一个时点 `[−]` 会弹警告。

### 2.5 Templates（Phase 5.5，本次新落地）

**这是这次重点要看的 tab**。三栏布局：

#### 顶部：`project.templates` 路径选择器

4 个 QLineEdit + `[…]` 文件选择按钮，对应 `si / calibre / quantus / jivaro` 4 个 tool slot。

- 文件选择默认从 `<auto_ext_root>/templates/<tool>/` 起，filter `*.j2`
- 选完后会自动尝试存为相对 workarea 的路径（`templates/calibre/foo.qci.j2` 而不是绝对路径）
- 清空文本框 → `(unset)` → Save 时该 tool 字段从 yaml 删掉，`templates: {}` 也会级联 prune 掉

#### 中部左：模板列表

按顺序：

1. 4 个绑定 slot（`[si]` / `[calibre]` / `[quantus]` / `[jivaro]` 标签 + 路径）
2. 在 `<auto_ext_root>/templates/**/*.j2` 走到的、但**没**绑到任何 slot 的文件，标 `[unused]` 灰色显示

点击切换右侧的 Inventory + Knobs 视图。150ms 防抖刷新。

#### 中部右：QTabWidget 子 Tab

**Inventory** 子 tab（只读）：

| kind | name | status |
|---|---|---|
| env_var | `WORK_ROOT` | `ok` 绿 / `override` 琥珀 / `missing` 红 |
| literal | `CELL_NAME` | `info` 灰 |
| user_defined | `user_defined_freq` | `info` 灰 |
| jinja | `cell` | `ok` 绿（identity）/ `ok` 绿（manifest knob）/ `missing` 红（无绑定） |

`status` 列说明：
- env_var 走 `_discover_env_vars` → `resolve_env`，跟 Project tab 的 env 面板同步（含本 tab 的 staged override）
- jinja 变量绿 = 在 `_IDENTITY_KEYS`（`cell` / `library` / `output_dir` 等 runner 自动注入）或 manifest 声明了同名 knob
- jinja 变量红 = 模板里 `[[foo]]` 但没人绑定 —— 跑起来 StrictUndefined 会抛
- literal / user_defined 一律灰 —— 5.5 不做绑定判定（这些是 5.6 diff editor 的事）

**Knobs** 子 tab：

每行一个 `KnobEditor`，按 `KnobSpec.type` 选 widget：

- `bool` → QCheckBox
- `int` / `float` → QLineEdit + Q*Validator（带 `range` 时验证器自动设范围）
- `str` → QLineEdit

每行右侧：
- `[reset]` 按钮：清掉 project 层 override，退回 manifest default。**只有当前有 override 时才 enabled**。
- 末尾灰字 hint：默认状态下显示 `unit · range: [low, high]`；有 override 时显示 `(default: <X>)` 让你能比对

数值字段清空 → 等同 `[reset]`。

#### Save 行为

5.5 加了的 dotted-key 形式：

- `templates.calibre`、`templates.si` 等 4 个 tool slot
- `knobs.<stage>.<name>` 三段（例如 `knobs.quantus.temperature`）

跟 Project tab、Tasks tab 共用一个 ConfigController，**任意 tab 的 Save 会同时写 project.yaml + tasks.yaml**（如果两边都有 pending edits）。注释保留，Save 后 `git diff` 只看到值变化。

---

## 3. 推荐 Windows 试玩路径

> 假设按上面命令启动了 GUI，`config/project.yaml` 加载成功。

1. **Project tab**：env 面板里看 `WORK_ROOT` / `WORK_ROOT2` / `PDK_LAYER_MAP_FILE` 等大概率显示 `✗ missing`（Windows 上没 source PDK setup） —— 正常。点一个 `[Override]` 给个假值（比如 `D:\fake`），看下 `●` 标记 + `[Save]` 是否 enable。
2. **Tasks tab**：选中那一行 spec，往 `cell` chip 里加个值，下面 cartesian 表行数应当变化；对某行取消勾选看下灰色删除线效果。
3. **Templates tab**（重点）：
   - 顶部 4 行路径选择器应该有 `si / calibre / quantus / jivaro`，路径形如 `templates/quantus/ext.cmd.j2`
   - 列表里前 4 行是绑定模板（`[si]` / `[calibre]` / `[quantus]` / `[jivaro]` 标签），下面应该还能看到 1 行 `[unused]` 灰色 —— `templates/quantus/dspf.cmd.j2`，仓库里有但没绑到任何 slot
   - 选中 `[quantus] templates/quantus/ext.cmd.j2`，右侧：
     - **Inventory** 应填出 `[[temperature]]` `[[exclude_floating_nets_limit]]` `[[tech_name]]` `[[output_dir]]` 等行（jinja kind），manifest 声明的 knob + identity 都标 `ok` 绿；env vars (`WORK_ROOT` 等) 应该是红 `missing`（没 source PDK setup）
     - **Knobs** 子 tab 应有 4 行（manifest 里声明的 4 个 knob），每行末尾灰字 hint 显示 unit / range
   - 改一个 knob 值（比如 `temperature` 改成 `25`），回车后 `(default: 55)` hint 出来，`●` 标记 + Save 按钮 enable
   - 点 `[reset]`，hint 应该消失，按钮再次变灰
   - 试试 `[…]` picker：选中 `[unused]` 那行的 `dspf.cmd.j2` 看下 picker 默认起始目录是不是 `templates\quantus\`
4. **Save** 后切到外面用 git 看 diff：

   ```bash
   cd /c/code/Auto_ext/Auto_ext && git diff config/project.yaml
   ```

   应该能看到 `templates.quantus` 的值变化 + 新增的 `knobs.quantus.temperature: 25`，注释保留。

5. **Revert** 回滚未保存的变更，`●` 应清掉。

6. 想跑一次 dry-run 确认 GUI 没炸：去 **Run** tab → 勾 Dry-run → `[▶ Run]`，状态树应当所有 stage 都是 `… dry run`。

---

## 4. 5.6 之后新增的 GUI 入口

下面这些是 5.5 之后陆续落地的；上面 §2 主要描述 5.5 的 5 个 tab，新东西基本都是从 Templates tab 或菜单触发的对话框。

### 4.1 Diff-mode 模板编辑器（Phase 5.6, `widgets/diff_editor.py`）

Templates tab → 选中一个绑定模板 → `[Edit diff…]`（或对应入口按钮）打开 modal `DiffEditor`：

- 拖两份原始 EDA 导出（diff 的 A / B）进 DropZone，或 `[Browse…]`
- 用 `[% if toggle %] … [% else %] … [% endif %]` 把两边差异 wrap 进同一个 `.j2`
- 输出回 `<auto_ext_root>/templates/<tool>/...`，保留注释 + 缩进
- Toggle 名 / 默认值用对话框里的 QLineEdit + checkbox 直接配，写回 manifest

Calibre 的 "noConnectByNetName on/off" 之类两份模板现在用这个 wrap，不再手工切。

### 4.2 只读 TemplateDiffViewer（Phase 5.6.2, `widgets/template_diff_viewer.py`）

仅查看差异、不写文件。两个 DropZone + 同步滚动 + 行级 diff 着色。常用于"我手里两份 raw 想看下到底差哪几行"的快查；不依赖 controller，可独立起。

### 4.3 PresetPicker（Phase 5.6, `widgets/preset_picker.py`）

Templates tab 新增的 `[Apply preset…]`。列出 `<auto_ext_root>/templates/presets/` 下所有合法 preset，右侧给 meta + snippet 预览，选中 → apply 到当前模板。锚点对不上直接拒绝（v1 没有 fuzzy 回退）。

### 4.4 InitWizard（Phase 5.7, `widgets/init_wizard.py`）

主菜单 / 启动页 `[Init project…]` 调起的 `QWizard`。6 页：Intro / Destination / RawFiles / Preview / Commit / Result，里面包了 `auto_ext.core.init_project`。第一次建 `project.yaml` 不再必须走 CLI；同步执行（无后台线程 / 取消 / rollback），Preview 页两个子 tab：概要 + 生成的 yaml。

### 4.5 TemplateGenerator dialog（Phase 5.8, `widgets/template_generator.py`）

非模态 `[Generate from raw…]`。把单个 raw EDA 文件（Calibre `.qci` / `si.env` / Quantus `.cmd` / Jivaro `.xml`）直接转成参数化 `.j2`：

- 拖一份 raw 进 DropZone → 自动 detect tool（也可顶部下拉手切）
- 调 `core.importer.import_template` 算参数化 body
- 左 raw / 右 parameterized 双栏；真正 differ 的行黄底高亮
- 右侧第三栏 6 个 Identity 字段（library / cell / view 等），改完 300ms 防抖重新 import
- 状态条：`自动抽取 / 用户覆盖 / 导入失败：<reason>`
- 满意之后 `[Save…]` 写到 `templates/<tool>/`

### 4.6 Paths group（Phase 5.6.5, Project tab）

§2.3 已经描述。重写要点：旧 `pdk_subdir` / `project_subdir` / `runset_versions.{lvs,qrc}` 四段 schema 已删，统一进 `paths.<key>`，每个 path 直接是模板 `[[<key>]]` 引用的整条目录路径，可 `${X}|parent` 形式从 env var 派生。每行带"被哪些模板引用"的反查列表 + tooltip 给 resolves-to 预览。

### 4.7 DspfOutPathCombo（最近一次，Project + Tasks tab）

`widgets/dspf_out_path_combo.py`。Project tab 的 Output group 里加了 `dspf_out_path`，Tasks tab 的每行 spec 里也有同名字段（per-task 覆盖）。

- Editable QComboBox，下拉项展示 **resolved real paths**（`${X}` / `[[X]]` / `{cell}` 都已替换）
- 每条 preset 在 `Qt.UserRole` 里挂着 **template form**（如 `${WORK_ROOT2}/{cell}.dspf`），写回 yaml 仍是 templated
- 末尾 `Custom...` 哨兵让你直接键入自定义表达式
- Tasks tab 变体在 index 0 多一个 `(default: <X>)` 哨兵：选中即删除 per-task 覆盖、回退 project default
- Combo 下方斜体 label 实时显示 fully-resolved 路径

### 4.8 仍未做的 / 已知限制

- **Manifest 编辑**：GUI 不改 `*.manifest.yaml`（manifest 是 template-author 领域，跟着 git 走）。加新 knob 仍走 `auto_ext knob suggest` / `knob promote`。
- **Per-task knob override**：`tasks.yaml[...].knobs.<stage>.<name>` 现在仍只在 CLI / yaml 手编里改；GUI 要在 TasksTab 后续扩展加。
- **办公室验证 Steps 4–6**：`--jobs 4` 的 license ceiling 探测、duplicate-cell preflight、混合 pass/fail 真跑，仍待真 Cadence 环境（见 `OFFICE_VALIDATION.md`）。

---

## 5. 出问题时

**GUI 一启动就 ImportError**：venv 里漏了 PyQt5。`pip install "PyQt5>=5.15"` 装到 venv（**不要**装到 system Python，参考记忆里的"不 pip install 到 site-packages"原则）。

**`python -m auto_ext` 找不到模块**：`PYTHONPATH` 没设对。它要指向**内层** `Auto_ext\`（auto_ext 包的父目录），不是外层。

**点哪个 tab 都报 `Config error: ...`**：`project.yaml` 或 `tasks.yaml` schema 不过。先在终端跑 `python -m auto_ext check-env --config-dir Auto_ext\config`，错误会更清晰。

**改了一个东西但 `[Save]` 一直灰着**：editingFinished 没触发 —— Qt 的 QLineEdit 要按 Tab 或 Enter 离开焦点才算编辑结束。

**Save 报 "config changed on disk"**：你 GUI 之外手动改过 yaml。点 `[Reload]` 拉最新，再编。
