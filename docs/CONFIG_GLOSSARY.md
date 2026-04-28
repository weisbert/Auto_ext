# Config Glossary

`project.yaml` 每个字段是什么、怎么填、从哪里找。

设计哲学：**大多数字段你不用填**。Auto_ext 会从你 source 过的 EDA setup 脚本设的 env var 里自动反解。手填只在**跨项目复用**或**env var 不全**时有用。

> **Phase 5.6.5 重写**：旧的 `pdk_subdir` / `project_subdir` / `runset_versions.{lvs,qrc}` 四个分段字段已删除，改为一个 `paths` 段。每个 path 直接是模板 `[[name]]` 里引用的整条目录路径，不再做"按段拆解"。详见下面 [`paths`](#paths) 一节。

---

## 字段速查表

| 字段 | 必填？ | 自动反解来源 | 例子 |
|---|---|---|---|
| `work_root` | 否 | `$WORK_ROOT`（display only） | `/data/RFIC3/projB/Hi1A22V100/<id>/workarea` |
| `verify_root` | 否 | `$VERIFY_ROOT`（display only） | `/software/PDK/.../verify` |
| `setup_root` | 否 | `$SETUP_ROOT`（display only） | `/software/PDK/.../setup` |
| `employee_id` | 否 | `$USER` | `w84368867` |
| `tech_name` | 否 | parent of `$PDK_TECH_FILE` 等 | `HN001` |
| **[`paths`](#paths)** | **看模板** | env var 引用直接写在 path 表达式里 | 见下文 |
| `layer_map` | 否 | `$PDK_LAYER_MAP_FILE` | `/software/PDK/.../layers.map` |
| `extraction_output_dir` | 否 | 默认 `${WORK_ROOT}/cds/verify/QCI_PATH_{cell}` | — |
| `intermediate_dir` | 否 | 默认 `${WORK_ROOT2}` | — |
| [`dspf_out_path`](#dspf_out_path) | 否 | 默认 `${WORK_ROOT2}/{cell}.dspf` | — |

---

## 详细字段说明

### work_root / verify_root / setup_root

显示用字段。Auto_ext 实际跑 EDA 时**不读这三个 yaml 字段**——它直接用 shell 里 `$WORK_ROOT` / `$VERIFY_ROOT` / `$SETUP_ROOT` 的值。yaml 设这几个只是给 GUI 的"我现在在哪个项目"显示用。

**何时需要手填**：基本用不着，留空就行。

---

### employee_id

替换模板里 `[[employee_id]]` 占位符。常见用途：raw 路径里有你 home 目录路径段（`/tmpdata/RFIC/rfic_share/<id>/...`）。

**自动反解**：取 `$USER` 环境变量。

**何时需要手填**：你想用别人的 id（很少见），或服务器没设 `$USER`（更少见）。

---

### tech_name

Cadence 技术库名，写进 quantus 的 `-technology_name` 选项。

**自动反解**：依次试 `tech_name_env_vars` 列表里的 env var（默认 `[PDK_TECH_FILE, PDK_LAYER_MAP_FILE, PDK_DISPLAY_FILE]`），第一个非空的，取**父目录名**作为 tech_name。

**例子**：`$PDK_TECH_FILE = /pdk/HN001/techfile.tf` → `tech_name = HN001`。

**何时需要手填**：父目录名不是你想要的（罕见，PDK 命名约定异常的项目）。

---

### paths

模板里 `[[<key>]]` 引用的整条目录路径。每个值是一个**字符串表达式**，可以混合 env var 引用、字面段、可选的 `|parent` 过滤器；render 阶段先经 `substitute_env`、再按 `|parent` 走 `Path.parent`，结果作为 `[[<key>]]` 的值注入到模板。

**为什么不像旧版那样按段拆？** 因为路径结构其实**跨项目是不稳定的**。旧 schema 把每段（`pdk_subdir`、`runset_versions.*`、`project_subdir`）独立成字段，看似规整，但只要有一个 PDK 用了不一样的目录层级，整个 schema 就崩。改成"整条路径一个字段"——你写的就是 `$VERIFY_ROOT/runset/Calibre_QRC/QRC/...`，写多深都行。

**默认你需要的两条**（自带模板引用的）：

```yaml
paths:
  # Calibre LVS rules-file 所在目录，模板里 *lvsRulesFile 引用为 [[calibre_lvs_dir]]。
  # $calibre_source_added_place 是 PDK setup 脚本设到该目录里某个文件的 env var
  # （惯例：empty.cdl），所以 |parent 拿到 dir 本身。
  calibre_lvs_dir: $calibre_source_added_place|parent

  # QRC 部署目录（query_cmd / preserveCellList.txt 所在），模板里被 calibre +
  # quantus 共三处引用为 [[qrc_deck_dir]]。没有公认的 env var 约定，建议显式拼。
  qrc_deck_dir: $VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CF710_Plus_..._V1d0a/QCI_deck
```

**`|parent` 过滤器**：`<expr>|parent` 表示 env-substitute 后取 `pathlib.PurePosixPath(s).parent`。可以串：`$X|parent|parent` 是祖父目录。**注意**：仅当你在路径末尾留了文件名（典型：`empty.cdl`）时才用 `|parent`；若 env var 已经直接指向目录，不要加。

**`calibre_lvs_basename` 自动派生**：模板 `*lvsRulesFile: [[calibre_lvs_dir]]/[[calibre_lvs_basename]].[[lvs_variant]].qcilvs` 里的 basename，运行时自动取 `Path(calibre_lvs_dir).name`（PDK 惯例：rules 文件名前缀 == LVS 子目录名）。如果你的 PDK 不遵守这个惯例，显式覆盖：

```yaml
paths:
  calibre_lvs_dir: $calibre_source_added_place|parent
  calibre_lvs_basename: my_special_basename
```

**自定义 path key**：在模板里加一个 `[[my_other_dir]]`，并在 yaml 里加：

```yaml
paths:
  my_other_dir: $SOME_ENV/foo/bar
```

GUI Project tab 的 Paths 组会自动列出每个 key + 它在哪些模板里被引用。

**手动找路径值的方法**：

```bash
# calibre_lvs_dir：从 *lvsRulesFile 那行取 dirname
grep -m1 '^\*lvsRulesFile:' your_calibre.qci | awk '{print $2}' | xargs dirname

# qrc_deck_dir：从 calibre 的 *lvsPostTriggers query_input 那段或 quantus
# 的 -parasitic_blocking_device_cells_file 那行取 dirname
grep -oP 'query_input \S+' your_calibre.qci | head -1 | sed 's,/query_cmd,,'
```

`init-project` 会从 raw 里这两个锚点行自动反解出 `calibre_lvs_dir` / `qrc_deck_dir`，并把整条路径写进 yaml；你可以再手动改成 `$X|parent` 形式追求更动态。

---

### layer_map

GDS export 用的 layer 映射文件。strmout 阶段必需。

**默认值**：`${PDK_LAYER_MAP_FILE}`（运行时解析 env var）。

**何时需要手填**：你需要用一个**和 env var 不同**的 layer map（比如做 ESD 验证版的 GDS）。

---

### extraction_output_dir

每个 task 的 EDA 输出落盘位置模板。

**默认**：`${WORK_ROOT}/cds/verify/QCI_PATH_{cell}` —— 每个 cell 一个独立目录。

**支持的 format key**：
- `{cell}` `{library}` `{task_id}` `{lvs_layout_view}` `{lvs_source_view}`

**何时改**：
- 想让同 cell 但不同 knob 配置的两个 task 各占一个目录 → 加 `{lvs_layout_view}` 或 `{task_id}` 区分
- 老流程把所有 cell 都堆一个目录 → `${WORK_ROOT}/cds/verify/QCI_PATH`（不推荐，容易互相覆盖）

---

### intermediate_dir

串行模式下 EDA 工具的 cwd（si 从这里读 si.env），并行模式下临时 si.env 的暂存位置。

**默认**：`${WORK_ROOT2}` —— 你的 workarea 根目录。

**何时改**：基本不用改。除非有特殊的 si.env 共享需求。

参见 [`dspf_out_path`](#dspf_out_path)：以前 dspf 输出文件硬编码在 `intermediate_dir/<cell>.dspf`，现在用专门字段控制。

---

### dspf_out_path

`templates/quantus/dspf.cmd.j2` 里 `-file_name` 落盘的位置——也就是 Quantus 写出的 DSPF 寄生参数文件的完整路径。每个 task 独立一份。

**默认**：`${WORK_ROOT2}/{cell}.dspf` —— 复用旧版行为（落在 workarea 根，文件名 = cell 名）。

**支持的 token**：
- env vars: `${X}` / `$X` / `$env(X)` —— 普通 shell 环境变量
- 路径 token: `${output_dir}` / `${intermediate_dir}` / `${calibre_lvs_dir}` / `${qrc_deck_dir}` / 任意 `project.paths.<key>` —— 这些是 runner 解析过的真实路径
- format key: `{cell}` / `{library}` / `{task_id}` —— Python `str.format` 替换

**解析顺序**：先 env 替换（含路径 token），再 `.format(...)`。所以 `${output_dir}/{cell}.dspf` 会先把 `${output_dir}` 换成实际的 `extraction_output_dir`，再把 `{cell}` 换成本 task 的 cell 名。

**例子**：

```yaml
# (1) 默认行为：落在 workarea 根
dspf_out_path: "${WORK_ROOT2}/{cell}.dspf"

# (2) 落在每个 task 自己的 extraction 输出目录里（推荐）
dspf_out_path: "${output_dir}/{cell}.dspf"

# (3) 自定义子目录
dspf_out_path: "${WORK_ROOT}/dspf_export/{library}/{cell}.dspf"
```

**何时改**：想让 dspf 文件和其它 EDA 输出物（map/、ihnl/ 等）放一起→改成 `${output_dir}/{cell}.dspf`；想集中收集到独立目录→自定义路径。

**per-task override**：在 `tasks.yaml` 里某个 spec 写 `dspf_out_path: ...` 就只覆盖那个 spec；留空（不写）则继承 project 层的默认值。

---

## 优先级链（怎么覆盖）

| 想达到的效果 | 怎么做 |
|---|---|
| 默认让 Auto_ext 自动反解（推荐） | yaml 用 `paths.calibre_lvs_dir: $calibre_source_added_place\|parent` |
| 完全硬编码一个绝对路径 | yaml 写 `paths.calibre_lvs_dir: /abs/path/to/dir` |
| 引用别的 env var | yaml 写 `paths.calibre_lvs_dir: $MY_VAR/...` 或 `$MY_VAR|parent` |
| 加新模板引用的 path | 模板里写 `[[my_dir]]`，yaml 加 `paths.my_dir: ...` |

---

## 看不到自动反解的值？

GUI 的 Project tab：
- **Identity / PDK / Output** 组每个字段下方显示 `(auto-derived: <X>)` 或 `(no candidate resolved from [...])`。
- **Paths** 组每个 path 下面有 `↳ <template>:<line>  <line excerpt>` 的"Used by"列表，告诉你这个 path 在哪个模板第几行被用到；hover 看 tooltip 会显示 resolves to 的当前解析结果。

如果 path 显示 `(error: unknown path filter ...)` 之类，说明你 yaml 表达式语法写错了；如果显示成 `$X` 字面量没替换，说明 `$X` 这个 env var 在当前环境里没值——先 `echo "$X"` 验一下，再 source EDA setup 脚本。

---

## init-project 自动填的是什么？

`init-project` 从你给的 raw 文件（calibre `.qci` / si `.env` / quantus `.cmd`）里反解出：

| 字段 | 反解规则 |
|---|---|
| `tech_name` | quantus 文件里第一个 `HN[A-Za-z0-9_.]+`；非 quantus 来源会进 unclassified |
| `paths.calibre_lvs_dir` | calibre `*lvsRulesFile` 行的 dirname（整条路径） |
| `paths.qrc_deck_dir` | calibre `*lvsPostTriggers` 里 `-query_input <X>/query_cmd` 的 `<X>`，与 quantus `-parasitic_blocking_device_cells_file "<X>/preserveCellList.txt"` 交叉校验；不一致就两个都进 unclassified |

写完之后，你可以把硬编码路径改成 `$env|parent` 形式——更动态，跨项目复用更方便。

---

## 什么不再支持？

Phase 5.6.5 删除了以下字段（旧 yaml 加载会报错，因为 pydantic `extra="forbid"`）：

- `pdk_subdir` / `pdk_subdir_env_vars`
- `project_subdir`
- `runset_versions:` (`lvs` / `qrc`)
- `lvs_runset_version_env_vars` / `qrc_runset_version_env_vars`

**手动迁移**（pre-1.0，没有自动迁移工具）：

```yaml
# 旧 schema
pdk_subdir: CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9
runset_versions:
  lvs: Ver_Plus_1.0l_0.9
  qrc: Ver_Plus_1.0a
```

```yaml
# 新 schema（等价）
paths:
  calibre_lvs_dir: $VERIFY_ROOT/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9
  qrc_deck_dir: $VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9/QCI_deck
```

或者更动态、利用 env var：

```yaml
paths:
  calibre_lvs_dir: $calibre_source_added_place|parent
  qrc_deck_dir: $VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9/QCI_deck
```
