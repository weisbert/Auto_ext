# Office Linux Quickstart

上班后把 Auto_ext 在真 Cadence 环境下跑通的最短路径。从**不熟**开始，一步一步。

---

## 0. 前提

- 已经在服务器上 `git clone` 过 Auto_ext，部署路径 `/data/RFIC3/<Project>/<Employee ID>/workarea/Auto_ext_pro/`
- Python 3.11 已就位（`/software/public/python/3.11.4/bin/python3.11`）
- 第三方依赖已通过 `scripts/install_offline.sh` 装到 `~/.local/lib/python3.11/site-packages/`
- 你的 Cadence / PDK setup 脚本 `source` 之后，shell 里这几个 env var 有值：
  - `$WORK_ROOT`（你的 workarea 的父目录，extraction 输出落盘处）
  - `$WORK_ROOT2`（= workarea，EDA 的 cwd）
  - `$VERIFY_ROOT`（Calibre / QRC runset 路径）
  - `$SETUP_ROOT`（`assura_tech.lib` 所在）
  - `$PDK_LAYER_MAP_FILE`（GDS 导出的 layer map）

---

## 1. 拉最新代码

```bash
cd /data/RFIC3/<Project>/<Employee ID>/workarea/Auto_ext_pro
git pull
```

预期：fast-forward 拉到包含 Phase 3 + 本文档的 commit。

## 2. 验证 Python 依赖

```bash
python3.11 -c "import jinja2, ruamel.yaml, pydantic, typer, rich; print('deps ok')"
```

报 `ModuleNotFoundError` 就重跑一次 `bash scripts/install_offline.sh`。

## 3. 跑已有单元测试做 sanity check

```bash
python3.11 -m pytest tests/ -v
```

预期：当前 main（Phase 5.7 起）在 Windows 上为 **547 绿 + 10 skip**，Linux 上 symlink 测试能跑起来，会再多 9 个绿。

全绿数量随 phase 增长 —— 如果当前数量偏离太多**停下来**先查再往下走，后面出问题排查不清楚是 real-env 的锅还是代码的锅。

---

## 4. 写你的项目配置

### 4.1 一键 bootstrap：`init-project`

新项目 / 新 PDK / 新 tech，推荐**一条命令**从原始 Cadence 导出生成整套 config + templates：

```bash
./run.sh init-project \
  --raw-calibre ~/exports/my.qci \
  --raw-si      ~/exports/si.env \
  --raw-quantus ~/exports/ext.cmd \
  --raw-jivaro  ~/exports/reduction.xml
```

做的事：

1. 4 个原始文件跑 import，抽出 identity（cell / library / views / ground_net / out_file）
2. 跨工具校验 identity 必须一致（cell 在 calibre/si/quantus/jivaro 必须都指同一个，否则报 `identity mismatch` 拒绝写出）
3. 把 4 个文件里的硬编码值聚合成 project 级常量：`tech_name`（`HN001`）/ `pdk_subdir`（`CFXXX`）/ `project_subdir`（`projB`）/ `runset_versions.lvs`（`Ver_Plus_1.0l_0.9`）/ `runset_versions.qrc`（`Ver_Plus_1.0a`）
4. 把每份模板 body 里的这些值都换成 `[[tech_name]]` / `[[pdk_subdir]]` / 等占位符
5. 写出：
   - `Auto_ext_pro/config/project.yaml` — 填好 PDK 常量 + 4 个 template 指针
   - `Auto_ext_pro/config/tasks.yaml` — 一条基于检测 identity 的 skeleton 任务
   - `Auto_ext_pro/templates/{calibre,si,quantus,jivaro}/imported.*.j2` + 空 `knobs: {}` manifest

默认输出路径就是 `./Auto_ext_pro/config` + `./Auto_ext_pro/templates`。要写别处用 `--output-config-dir` / `--output-templates-dir`。

Jivaro 可选：不跑 reduction 的项目省掉 `--raw-jivaro`，init-project 就不生成 jivaro 模板，tasks.yaml 里 `jivaro.enabled: false`。

重跑时需要加 `--force`（已存在文件自动备份到 `.bak` 再覆盖）。

运行完 console 会列一张 `Detected project constants` 表 + 必要时列 `Unclassified hardcoded values`（跨工具冲突或无法归类的硬编码，**手工 review**）。看完后直接跳 §5 做 check-env + dry-run。

### 4.2 调整 tasks.yaml

init-project 写的 `tasks.yaml` 只有**一条 task**，用的是 raw 文件里检测到的 cell / library。真跑 batch 时编辑它：

```yaml
- library: WB_PLL_DCO                          # 你 design 的 library
  cell: [LO_5GRX_LO_back_v3, another_cell]     # 列表 → 自动展开
  lvs_layout_view: [layout, layout_test]
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext
  jivaro:
    enabled: false                             # 先 false 验证 si/calibre/qrc 通
```

展开语法见 §7。

### 4.3 project.yaml 各字段说明

init-project 写出的 project.yaml 长这样（节选）：

```yaml
tech_name: HN001
pdk_subdir: CFXXX
project_subdir: projB
runset_versions:
  lvs: Ver_Plus_1.0l_0.9
  qrc: Ver_Plus_1.0a
templates:
  calibre: Auto_ext_pro/templates/calibre/imported.qci.j2
  si: Auto_ext_pro/templates/si/imported.env.j2
  quantus: Auto_ext_pro/templates/quantus/imported.cmd.j2
  jivaro: Auto_ext_pro/templates/jivaro/imported.xml.j2
```

`work_root` / `verify_root` / `setup_root` / `employee_id` 全部**不写**就行 —— shell env 里有就用 shell 的，`employee_id` 不设就自动取 `$USER`。`layer_map` 默认是 `${PDK_LAYER_MAP_FILE}`，也从 shell 取。

换 PDK / 换 tech？直接编辑上面那几个字段的值，模板完全不用动。这就是 init-project 的意义。

### 4.4 单个模板的 ad-hoc import（不跑整个 init-project 时）

只想补一个模板（比如尝试新的 Calibre 选项集）：用单文件的 `./run.sh import`：

```bash
./run.sh import \
  --tool calibre \
  --input ~/my_raw.qci \
  --output Auto_ext_pro/templates/calibre/my_tpl.qci.j2
```

`--tool` 必填，四选一：`calibre` / `si` / `quantus` / `jivaro`。identity 字段（cell / library / 两个 view / ground_net / out_file）按 per-tool key 表自动推断；推断不准时用 `--cell` / `--library` / `--lvs-layout-view` / `--lvs-source-view` 强制覆盖。

导入产出三个文件（相对 `--output`）：

- `my_tpl.qci.j2` — identity 已替换成 `[[cell]]` / `[[library]]` 等占位符
- `my_tpl.qci.j2.manifest.yaml` — 空 `knobs: {}` 起步
- `my_tpl.qci.j2.review.md` — 人读的 review 报告，列出 identity / 候选 knob 数量 / 残留硬编码

**注意**：单文件 import **不会**跑跨文件的 PDK 聚合（那是 init-project 的活）；硬编码 `CFXXX` / `HN001` / `Ver_Plus_*` 会原样留在 body 里。如果目标 `project.yaml` 已经有 `tech_name` / `pdk_subdir` 等字段，手动在 body 里替换成 `[[tech_name]]` 等即可。

#### 候选 knob + 提升

```bash
./run.sh knob suggest Auto_ext_pro/templates/calibre/my_tpl.qci.j2
./run.sh knob promote Auto_ext_pro/templates/calibre/my_tpl.qci.j2 \
  cmnNumTurbo cmnLicenseWaitTime
```

suggest 输出一张 Rich 表：原始 key、字面值、推断类型、建议 snake_case 名、行号。`type` 列带 `*` 的是 bool 启发式命中（key 含 `Enable`/`Disable`/`Run`/`Use`/`Abort`/`Connect`/`Show`/`Warn`/`Release`/`Specify`/`Hyper` 且值是 0/1）—— 不确定就 `--all` 看全部。

promote 会把 `.j2` 里对应行的字面量换成 `[[cmn_num_turbo]]` / `[[cmn_license_wait_time]]`，manifest 里加上对应 knob 条目（带 `source: {tool: calibre, key: cmnNumTurbo}` 便于二次导入）。

覆盖 heuristic：
- `--type int/float/str/bool` 强制类型（比如把 bool 启发式回退成 int）
- `--name custom` 重命名 knob（只允许同时提升一个 key 时用）

#### 二次导入（smart merge）

再跑一次 `./run.sh import --tool … --input … --output …`（同样的 `--output`）会**保留**你已经提升过的 knob：

- identity 位从新 raw 重新替换
- 每个带 `source:` 的 manifest knob 会在新 raw 里找到对应 key，替换新 body，并用新 raw 的值刷新 `default`（变了会在 console 打印 `default updated old → new`）
- manifest 里你手动改过的 `description` / `range` / `unit` 原样保留
- 没有 `source:` 的 knob（你手写的，不是 importer 造的）完全不动，只提示一行

每次写入前都会把原 `.j2` / `.manifest.yaml` / `.review.md` 备份到 `.bak` 文件。想完全重置（丢掉已提升的 knob）加 `--fresh`。

### 4.5 Run_ext.txt → tasks.yaml 字段映射（老项目迁移）

有存量 `Run_ext.txt` 配置要迁过来时参考：

| Run_ext.txt | tasks.yaml |
|-------------|-----------|
| `LibraryName` | `library` |
| `CellName` | `cell` |
| `lvsSourceView` | `lvs_source_view` |
| `lvsLayoutView` | `lvs_layout_view`（多个值写成 `[layout, layout_test]`，自动展开）|
| `GndName` | `ground_net` |
| `OutFileName` | `out_file` |
| `ifJivaro Yes/No` | `jivaro.enabled: true/false` |
| `frequencyLimit` | `jivaro.frequency_limit` |
| `errorMax` | `jivaro.error_max` |
| `LVSTemp/QrcTemp/JivaroTemp/sienv` | 在 `project.yaml` 的 `templates:` 块里（全局）|
| `layerMap` | `project.yaml` 的 `layer_map`（全局，默认走 `${PDK_LAYER_MAP_FILE}`）|

Phase 4c 的 `migrate` 子命令会把这步自动化，还没做。

---

## 5. 验证流程（**务必按顺序**）

### 5.1 Source Cadence setup

按你平时的方式：

```bash
source <你平时 source 的项目 setup 脚本>
```

`echo $WORK_ROOT` 应该有值。

### 5.2 check-env

```bash
./run.sh check-env --config-dir Auto_ext_pro/config
```

预期：一张 rich 表格列出每个 env var 的来源（override / shell / missing），每一项都应该是 **green shell** 或 **yellow override**。如果有 **red missing**：

- 要么是 source 脚本漏掉了某个 var —— 补 source / export
- 要么在 `config/project.yaml` 的 `env_overrides:` 块里手动补

### 5.3 Dry-run（**不起真 EDA，只渲染模板**）

```bash
./run.sh run --config-dir Auto_ext_pro/config --dry-run
```

预期：summary 显示 1 个 task，所有 stage 状态 `d`（dry_run），全部 passed。

**打开渲染结果看一眼**：

```bash
ls runs/task_*/rendered/
cat runs/task_*/rendered/calibre_lvs.qci
cat runs/task_*/rendered/default.env
cat runs/task_*/rendered/ext.cmd
```

检查点：
- 没有 `$env(...)` / `${X}` 残留
- 所有路径是真实的（`/data/RFIC3/你的项目/...`），不是 `/tmp/demo` 也不是 `<Employee ID>`
- cell / library 名是你刚填的那个

如果看起来不对 —— **不要往下走**，回去检查 config + templates。

### 5.4 只跑 si 一个 stage

```bash
./run.sh run --config-dir Auto_ext_pro/config --stage si
```

预期：1 个 task，`si` stage passed，生成 netlist `$WORK_ROOT/cds/verify/QCI_PATH_<cell>/<cell>.src.net`。日志在 `logs/task_<id>/si.log`。

Si 失败最常见的几个原因：
- `cds.lib` 不在 cwd = workarea —— 确认 workarea 根目录有 `cds.lib`
- 你的 library 在 `cds.lib` 里没 define
- schematic 视图不存在

### 5.5 加上 strmout + calibre

```bash
./run.sh run --config-dir Auto_ext_pro/config --stage si,strmout,calibre
```

预期：
- `strmout` 生成 `$WORK_ROOT/cds/verify/QCI_PATH_<cell>/<cell>.calibre.db`（GDSII 内容，文件名对齐 Calibre 模板里的 `*lvsLayoutPaths`）
- `calibre` 产出 LVS report，`$WORK_ROOT/cds/verify/QCI_PATH_<cell>/<cell>.lvs.report`
- runner 解析 report，banner=CORRECT 且 discrepancies=0 → stage passed

Calibre 失败的两种可能：
1. **exit code 非 0** —— Calibre 报错，看 `logs/task_<id>/calibre.log`。通常是 runset 路径不对（`$VERIFY_ROOT/runset/.../CFXXX/CFXXX.wodio.qcilvs` 这个路径里 `CFXXX` 要对应到你项目的真工艺名）
2. **exit code 0 但 LVS 不过** —— 真 LVS 错了，看 report 里的 error section。这种情况用 `--continue-on-lvs-fail` 继续跑 QRC：
   ```bash
   ./run.sh run --config-dir Auto_ext_pro/config --stage si,strmout,calibre,quantus --continue-on-lvs-fail
   ```

### 5.6 全链

LVS 过了之后：

```bash
./run.sh run --config-dir Auto_ext_pro/config
```

默认所有 5 个 stage（si → strmout → calibre → quantus → jivaro）。Jivaro 因为 task 里 `jivaro.enabled: false` 会被 silent skip。等整链通了再把 jivaro 开开。

### 5.7 单次跑调 knob（可选）

模板里的部分数字常量（温度、耦合电容阈值、浮动网上限等）在 sidecar `<template>.j2.manifest.yaml` 里声明为 knob。默认值不动，想临时试一个参数就用 `--knob`：

```bash
./run.sh run --config-dir Auto_ext_pro/config \
  --knob quantus.temperature=60 \
  --knob quantus.exclude_floating_nets_limit=10000
```

格式：`<stage>.<knob_name>=<value>`，可重复。精度 / 范围错误（`--knob quantus.temperature=abc` 或超出 manifest 声明的 `range`）会在 render 之前以 exit code 2 拒绝，不会污染 EDA 运行。

想长期固定某个 knob 值：写到 `project.yaml`（全项目） 或 `tasks.yaml`（单 task） 的 `knobs:` 块里。优先级从低到高：`manifest default` < `project.yaml.knobs` < `tasks.yaml[...].knobs` < `--knob CLI`。

### 5.8 并行跑多个 task（Phase 3.5）

多个 cell 要同时跑时加 `--jobs N`：

```bash
./run.sh run --config-dir Auto_ext_pro/config --jobs 2
```

- `--jobs 1`（默认）= 串行，走跟之前 Phase 3 完全一样的代码路径，`si.env` 临时放到 `workarea/si.env` 再清理。
- `--jobs >=2` = 并行，每个 task 独立建一个 `runs/task_<id>/`，用 symlink 指回 `workarea/cds.lib` + `.cdsinit`，`si.env` 直接写进 task 目录，所有 stage（不仅是 si）的 cwd 都切到 task 目录。workarea 不再共享 `si.env`，多个 task 写文件不会互踩。
- 失败策略跟串行一致：某 task 某 stage 挂 → 那个 task 后续 stage 跳过，**其他 task 继续跑**；不做全局 fail-fast。

**License 预算自己管**：Calibre / QRC / si 的 license 有数量上限，`--jobs 4` 撞到天花板时会在某 task 的 `logs/task_<id>/<stage>.log` 里看到 "no license available"（具体错误看 EDA 版本）。先 `--jobs 2` 稳住，确认 license 够再往上加。

**重复 `(library, cell)` 会被提前拒绝**：两个 task 同 library 同 cell 会共用 `extraction_output_dir`（`$WORK_ROOT/cds/verify/QCI_PATH_{cell}`），多线程写同一目录必撞。runner 启动前扫一遍，撞了就 `ConfigError` + exit 2，连 render 都不会开始。

### 5.9 Phase 3.5 办公室 smoke 清单（第一次 `--jobs 2` 要盯的）

跑之前在 `tasks.yaml` 里准备至少 2 个不同 cell。按下面顺序踩：

1. **si 单跑**：`./run.sh run --jobs 2 --stage si`。看 `logs/task_<id>/si.log`，`cds.lib` 在 symlink 下能被 si 正常解析吗？—— 这是 parallel 路径最大的未知数（见 `project_auto_ext_design.md` 的 open risk 列表）。
2. **全链 2 job**：`./run.sh run --jobs 2`。两个 task 都绿 + 两份 `runs/task_<id>/rendered/` 无交叉污染就算过。
3. **License 天花板**：`./run.sh run --jobs 4`。撞墙时错误是不是清清爽爽地出现在 stage log 里（而不是挂死 / silent fail）？
4. **Preflight 挡 duplicate**：临时把 `tasks.yaml` 里一个 task 整段复制一份（同 library + cell），确认 `./run.sh run` 立刻 exit 2 并在 stderr 打出两个 task_id。改完恢复。
5. **混合 pass/fail**：故意把某 cell 的 `.qci` 改坏，让它的 calibre 挂，另一 task 必须照常跑完。summary 应显示 `1 passed / 1 failed` + exit code 1。

跑完把 license 天花板数记下来，后续决定默认用 `--jobs` 几。

---

## 6. 模板里可能需要改的硬编码

### 6.1 init-project 生成的模板

通过 `init-project` 写出的 `Auto_ext_pro/templates/<tool>/imported.*.j2` 已经把下面这几类值全部抽象成了 `[[...]]` 占位符，**不用再手改**：

| 原始字面量（示例） | 抽象成 | 存储在 |
|-------------------|-------|-------|
| `HN001` | `[[tech_name]]` | `project.yaml.tech_name` |
| `CFXXX` | `[[pdk_subdir]]` | `project.yaml.pdk_subdir` |
| `Ver_Plus_1.0l_0.9` / `Ver_Plus_1.0a` | `[[lvs_runset_version]]` / `[[qrc_runset_version]]` | `project.yaml.runset_versions.{lvs,qrc}` |
| `projB`（`/data/RFIC3/projB/...` 里的） | `[[project_subdir]]` | `project.yaml.project_subdir` |
| `/tmpdata/RFIC/rfic_share/alice/` + `/data/RFIC3/.../alice/` | `[[employee_id]]` | shell `$USER` 或 `project.yaml.employee_id` |

换 PDK / tech / 项目 / 用户 = 编辑 `project.yaml` 的对应字段，模板完全不碰。

### 6.2 仓库自带的老模板

仓库 `templates/` 下自带的 5 个模板（`calibre_lvs.qci.j2` / `ext.cmd.j2` / `dspf.cmd.j2` / `default.env.j2` 等）已经把 PDK 硬编码全部抽象成了 `[[tech_name]]` / `[[pdk_subdir]]` / `[[lvs_runset_version]]` / `[[qrc_runset_version]]` 占位符，所以直接用这些模板、让 `project.yaml` 填那几个字段就能跑。Calibre 模板还接受 `lvs_variant`（`wodio` / `widio`，默认 `wodio`）和 `connect_by_name`（默认 `false`） 两个 knob，可在 `project.yaml.knobs.calibre.*` 或 `tasks.yaml[i].knobs.calibre.*` 里覆盖。

`tech_name` 还支持**自动推导**：`project.yaml` 里不设 `tech_name` 时，runner 会依次从 `$PDK_TECH_FILE` / `$PDK_LAYER_MAP_FILE` / `$PDK_DISPLAY_FILE` 的父目录名推出来（第一个有值的 env var 生效）。默认候选列表也可以通过 `project.yaml.tech_name_env_vars` 覆盖。推不出来时 `check-env` 会给黄色警告。

如果想换一套完全没见过的 raw 做起点，还是推荐 `init-project` 从头生成。

---

## 7. 多 cell / 多 view 怎么写

### 批量展开（Cartesian product）

```yaml
- library: WB_PLL_DCO
  cell: [LO_5GRX_LO_back_v3, another_cell]
  lvs_layout_view: [layout, layout_test]
  lvs_source_view: schematic
  ...
```

→ 自动展成 `2 × 2 = 4` 个 task。

### 多 entry（不同参数）

```yaml
- library: LIB_A
  cell: cell_1
  lvs_layout_view: layout
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext
  jivaro: { enabled: false }

- library: LIB_B
  cell: cell_2
  lvs_layout_view: layout_test
  lvs_source_view: schematic
  ground_net: gnd
  out_file: av_ext_v2
  jivaro: { enabled: true, frequency_limit: 20, error_max: 1 }
```

---

## 8. 只跑特定的 task

task_id 格式是 `<library>__<cell>__<layout_view>__<source_view>`：

```bash
./run.sh run --config-dir Auto_ext_pro/config \
  --task WB_PLL_DCO__LO_5GRX_LO_back_v3__layout__schematic
```

`--task` 可重复使用跑多个：

```bash
./run.sh run --config-dir Auto_ext_pro/config \
  --task WB_PLL_DCO__cell_a__layout__schematic \
  --task WB_PLL_DCO__cell_b__layout__schematic
```

---

## 9. 失败定位

每个 task 的每个 stage 都有独立 log：

```
logs/
└── task_<library>__<cell>__<layout>__<source>/
    ├── si.log
    ├── strmout.log
    ├── calibre.log
    ├── quantus.log
    └── jivaro.log
```

每个 log 文件顶部有 `# argv:` / `# cwd:` header，底部有 `# exit:`，中间是 tool 的 stdout+stderr 合流。

渲染产物（可审计）：

```
runs/
└── task_<id>/
    └── rendered/
        ├── default.env          (si 用的 si.env)
        ├── calibre_lvs.qci
        ├── ext.cmd
        └── default.xml
```

Calibre 失败时，`./run.sh run` 的 summary 里会显示 banner + discrepancies count（Phase 3 的 strict LVS 解析）。

---

## 10. 已知限制（下一阶段修）

- **No GUI**：Phase 5。PyQt5 ABI blocker **已解决**（见下），Phase 5 可以直接写代码。
- **No template editor**：Phase 6，最复杂。
- **No migrate**：Phase 4c。Run_ext.txt 现在手翻（§4.5 对照表）。
- **Parallel Steps 4-6 未验**：Phase 3.5 代码 + 3 个办公室实跑发现的 bug fix 已全部 ship（HEAD=63c0af8）；`--jobs 2` 全链办公室实跑通过（2026-04-24）。`--jobs 4` license 天花板 / duplicate preflight / 混合 fail 三项不 block 用户正常工作，下次有时间再补。

### PyQt5 ABI blocker（已解决）

2026-04-24 办公室诊断：

- 服务器 `/usr/lib64/libstdc++.so.6` 的最高版本符号是 `GLIBCXX_3.4.19`（GCC 4.8），**不提供** `_ZdaPvm`（C++14 sized-delete operator，GLIBCXX_3.4.21+ 才有）。
- PyQt5 5.15.9 的 `QtCore.abi3.so` 链接时引用了 `_ZdaPvm@Qt_5`。任何依赖系统 libstdc++ 的 Qt5（如 `/software/public/qt/5.15.3_xcb/lib/libQt5Core.so.5`，`nm -D` 显示 `U _ZdaPvm`）都无法解析这个符号。
- **PyQt5 的 manylinux2014 wheel 自带一份 Qt5**（`<PyQt5_path>/Qt5/lib/`），用较新工具链编译，`libQt5Core.so.5` 里 `T _ZdaPvm`（已定义，非引用）。只要把这个路径前置到 `LD_LIBRARY_PATH` 就能 bypass 系统 libstdc++ 老旧的问题。

`run.sh` 已加上这个逻辑：当检测到 subcommand 为 `gui` 或 `gui-*` 时自动前置 `<PyQt5>/Qt5/lib` 到 `LD_LIBRARY_PATH`。非 GUI 命令（如 `run` / `check-env` / `init-project`）不触发，保持 EDA 子进程环境干净。

诊断确认（bash 里跑）：

```bash
export LD_LIBRARY_PATH=/software/public/python/3.11.4/lib/python3.11/site-packages/PyQt5/Qt5/lib:$LD_LIBRARY_PATH
python3 -c 'from PyQt5 import QtCore, QtGui, QtWidgets; print("modules ok")'
python3 -c 'from PyQt5 import QtWidgets; a = QtWidgets.QApplication([]); print("QApplication ok")'
```

两行都打 `ok` → OK。出现 `qt.qpa.xcb: ...` 之类 warning 但最终 `QApplication ok` 打出来也 OK —— 那是 X11 display 问题，真跑 GUI 用 `ssh -Y` 或 VNC 解决，不是 ABI 问题。
