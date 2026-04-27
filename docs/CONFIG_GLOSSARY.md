# Config Glossary

`project.yaml` 每个字段是什么、怎么填、从哪里找。

设计哲学：**大多数字段你不用填**。Auto_ext 会从你 source 过的 EDA setup 脚本设的 env var 里自动反解。手填只在**跨项目复用**或**env var 不全**时有用。

---

## 字段速查表

| 字段 | 必填？ | 自动反解来源 | 例子 |
|---|---|---|---|
| `work_root` | 否 | `$WORK_ROOT`（display only） | `/data/RFIC3/projB/Hi1A22V100/<id>/workarea` |
| `verify_root` | 否 | `$VERIFY_ROOT`（display only） | `/software/PDK/.../verify` |
| `setup_root` | 否 | `$SETUP_ROOT`（display only） | `/software/PDK/.../setup` |
| `employee_id` | 否 | `$USER` | `w84368867` |
| `tech_name` | 否 | parent of `$PDK_TECH_FILE` 等 | `HN001` |
| **`pdk_subdir`** | **看模板** | parent of `$calibre_source_added_place` | `CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9` |
| `project_subdir` | 否 | 暂无（手填或 init-project 提取） | `projB` |
| **`runset_versions.lvs`** | **看模板** | grandparent of `$calibre_source_added_place` | `Ver_Plus_1.0l_0.9` |
| **`runset_versions.qrc`** | **看模板** | 暂无（手填或 init-project 提取） | `Ver_Plus_1.0a` |
| `layer_map` | 否 | `$PDK_LAYER_MAP_FILE` | `/software/PDK/.../layers.map` |
| `extraction_output_dir` | 否 | 默认 `${WORK_ROOT}/cds/verify/QCI_PATH_{cell}` | — |
| `intermediate_dir` | 否 | 默认 `${WORK_ROOT2}` | — |

"看模板"含义：如果你的模板里没出现 `[[X]]` 这个变量，就**不需要**填。否则要填或要让 fallback chain 解出来。

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

### pdk_subdir

PDK 子目录名，出现在多个 calibre / quantus 模板的路径里（比如 `$VERIFY_ROOT/runset/Calibre_QRC/LVS/<runset>/<HERE>/<HERE>.wodio.qcilvs`）。

**自动反解**：取 `pdk_subdir_env_vars` 列表里第一个解出值的 env var，**取该值的父目录名**。

```yaml
# project.yaml 默认配置（不用改也行）
pdk_subdir_env_vars:
  - calibre_source_added_place
```

**例子**：
```
$calibre_source_added_place = $VERIFY_ROOT/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9/empty.cdl
                                                                                    └─── pdk_subdir 这一段 ───┘
```
解出 `pdk_subdir = CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9`。

**手动找的方法**：
```bash
ls $VERIFY_ROOT/runset/Calibre_QRC/LVS/*/
```
列出来的子目录名就是。

**何时需要手填**：
- 跨多个项目用同一个 Auto_ext 部署（每个项目 pdk_subdir 不同 → 在 tasks.yaml 加 per-task 覆盖，或者 yaml 里硬编码）
- 你公司没有 `$calibre_source_added_place` 这个约定 → 要么改 `pdk_subdir_env_vars` 指向别的 env var，要么直接填 `pdk_subdir`

---

### project_subdir

`/data/RFIC3/<projectName>/...` 路径里的 `<projectName>` 段。si 的 raw 路径常出现，模板里 `[[project_subdir]]` 引用。

**当前没有自动 env var 反解**——init-project 从 raw 文件里 grep `/data/RFIC3/<X>/` 提取，但运行时没 fallback。

**手动找的方法**：
```bash
pwd | grep -oP '/data/RFIC3/\K[^/]+'
```

**何时需要填**：模板里有 `[[project_subdir]]`。

---

### runset_versions.lvs

Calibre LVS rules-file 路径里的版本段：

```
$VERIFY_ROOT/runset/Calibre_QRC/LVS/<HERE>/<pdk_subdir>/<pdk_subdir>.<variant>.qcilvs
                                    └── runset_versions.lvs ──┘
```

**自动反解**：`lvs_runset_version_env_vars`（默认 `[calibre_source_added_place]`），**取该值的祖父目录名**（grandparent）。

**例子**：上面那个 `$calibre_source_added_place` 解出 `runset_versions.lvs = Ver_Plus_1.0l_0.9`。

**手动找的方法**：
```bash
ls $VERIFY_ROOT/runset/Calibre_QRC/LVS/
```

---

### runset_versions.qrc

QRC 路径里的版本段：

```
$VERIFY_ROOT/runset/Calibre_QRC/QRC/<HERE>/<pdk_subdir>/QCI_deck/...
                                    └── runset_versions.qrc ──┘
```

**当前 `qrc_runset_version_env_vars` 默认空**——没有公认的 env var 命名约定。

**怎么填**：
- 如果你公司有类似 `$quantus_source_added_place` 的 env var，加到 `project.yaml`：
  ```yaml
  qrc_runset_version_env_vars:
    - quantus_source_added_place
  ```
  Auto_ext 会按"祖父目录名"反解。
- 否则手填：
  ```yaml
  runset_versions:
    qrc: Ver_Plus_1.0a
  ```
  从 calibre 的 `*lvsPostTriggers` 那行 query_input 路径里 grep 一下能看到。

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

---

## 优先级链（怎么覆盖）

每个 derive-able 字段都遵循同一套优先级（高到低）：

```
project.yaml 显式硬编码值   >   env var fallback chain   >   None（render 时报错）
```

举例 —— 切换 pdk_subdir：

| 想达到的效果 | 怎么做 |
|---|---|
| 默认让 Auto_ext 自动反解 | `project.yaml` 完全不写 `pdk_subdir`，靠 `pdk_subdir_env_vars` 的默认值 |
| 整个项目硬编码一个值 | `project.yaml` 写 `pdk_subdir: CF710_...` |
| 某些 task 用不同的值 | `tasks.yaml[i]` 加 `knobs.calibre.lvs_variant`（注意：这只能切 variant，pdk_subdir 整体替换需要在 `project.yaml` 改） |
| 完全换 env var 来源 | 改 `pdk_subdir_env_vars: [my_company_var]` |

---

## 看不到自动反解的值？

GUI 的 Project tab 在每个字段下方显示当前**有效来源**：
- `(auto-derived: <X>)` —— fallback chain 解出来了
- `(no candidate resolved from [...])` —— 列出来的 env var 全没值，需要手填或者 source 你的 setup 脚本

如果你看到 `no candidate resolved`，第一步先：

```bash
echo "$calibre_source_added_place"
```

空？说明你 EDA setup 脚本没跑。先 `source` 它再启动 GUI。

---

## init-project 自动填的是什么？

`init-project` 从你给的 raw 文件（calibre `.qci` / si `.env` / quantus `.cmd`）里 grep 提取这些值，写进 `project.yaml`。提取规则在 `auto_ext/core/importer.py:_PDK_PATTERNS`：

| 字段 | 规则 |
|---|---|
| `tech_name` | quantus 文件里第一个 `HN[A-Za-z0-9_.]+` |
| `pdk_subdir` | ≥2 个 raw 文件都出现的 `CF[A-Za-z0-9_.]+` |
| `project_subdir` | si 文件里 `/data/RFIC3/<X>/` 中的 `<X>` |
| `runset_versions.lvs` | calibre + si 一致的 `Ver_...` |
| `runset_versions.qrc` | quantus 里的 `Ver_...` |

init-project 写完之后，你可以删掉硬编码值，让 fallback chain 接管（更动态、跨项目复用更方便）。
