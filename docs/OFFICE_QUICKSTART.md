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

预期：**202 全绿**（Windows 上 skip 的 6 个 symlink 测试在 Linux 上会跑起来）。

如果这 150 不全绿，**停下来**先修再往下走 —— 后面出问题排查不清楚是 real-env 的锅还是 Phase 3 代码的锅。

---

## 4. 写你的项目配置

### 4.1 project.yaml

`config/project.yaml` 已经在 repo 里，**默认状态基本不用改**：

```yaml
templates:
  si: Auto_ext_pro/templates/si/default.env.j2
  calibre: Auto_ext_pro/templates/calibre/wiodio_noConnectByNetName.qci.j2
  quantus: Auto_ext_pro/templates/quantus/ext.cmd.j2
  jivaro: Auto_ext_pro/templates/jivaro/default.xml.j2
```

`work_root` / `verify_root` / `setup_root` / `employee_id` 全部是 Optional —— shell env 里有就用 shell 的，`employee_id` 不设就自动取 `$USER`。`layer_map` 默认是 `${PDK_LAYER_MAP_FILE}`，也从 shell 取。

### 4.2 tasks.yaml（= 新版 Run_ext.txt）

编辑 `config/tasks.yaml`，把 `TODO_LIBRARY_NAME` / `TODO_CELL_NAME` 换成你真实 design 的一个 library + 一个 cell。**先一个 task**，不要贪多：

```yaml
- library: WB_PLL_DCO              # 你 design 的 library
  cell: LO_5GRX_LO_back_v3         # 你要跑的 cell
  lvs_layout_view: layout
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext
  jivaro:
    enabled: false                 # 先 false！先验证 si/calibre/qrc 通
    frequency_limit: 14
    error_max: 2
```

### 4.3 Run_ext.txt → tasks.yaml 字段映射

手工翻，对照表：

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
| `LVSTemp/QrcTemp/JivaroTemp/sienv` | 在 `project.yaml` 的 `templates:` 块里（全局，不是 per-task）|
| `layerMap` | `project.yaml` 的 `layer_map`（全局，默认走 `${PDK_LAYER_MAP_FILE}`）|

Phase 4 的 `migrate` 子命令会把这一步自动化，但还没做。

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
cat runs/task_*/rendered/wiodio_noConnectByNetName.qci
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

---

## 6. 模板里可能需要改的硬编码

本仓库 `templates/` 下的 `.j2` 是从你 **之前粘贴的** anonymized 模板衍生来的，**保留了 5 处项目特定的字面量**。如果 dry-run 出来的路径对，这些字面量就是对的不用动；否则 `grep -rn` 改一下：

| 字面量 | 文件 | 含义 |
|--------|------|------|
| `CFXXX` | `templates/calibre/*.qci.j2`, `templates/quantus/*.cmd.j2` | PDK 工艺子目录 |
| `Ver_Plus_1.0l_0.9` / `Ver_Plus_1.0a` | 同上 + `templates/si/default.env.j2` | Runset 版本号 |
| `<HNxxxx>` / `HNXXXX` | `templates/quantus/ext.cmd.j2`, `templates/quantus/dspf.cmd.j2` | `tech_name` |
| `Hi1A22V100_C1Xplus` | `templates/quantus/dspf.cmd.j2` | 项目名（在 `-file_name` 路径里）|
| `/data/RFIC3/XXX/pptunicad/...empty.cdl` | `templates/si/default.env.j2` | Calibre source 附加 cdl 路径 |

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
        ├── wiodio_noConnectByNetName.qci
        ├── ext.cmd
        └── default.xml
```

Calibre 失败时，`./run.sh run` 的 summary 里会显示 banner + discrepancies count（Phase 3 的 strict LVS 解析）。

---

## 10. 已知限制（下一阶段修）

- **No parallel**：Phase 3 是 serial。跑 10 个 task × 每个 30 min = 5 小时。Phase 3.5 会 wire parallel（`core/workdir.py` 的 `prepare_parallel_workdir` 已就绪，runner 还没调）。
- **No GUI**：Phase 5。而且要先解掉 PyQt5 ABI blocker（见下）。
- **No template editor**：Phase 6，最复杂。
- **No migrate**：Phase 4。Run_ext.txt 现在手翻。

### PyQt5 ABI blocker 诊断

服务器上预装的 PyQt5 5.15.9 的 `.abi3.so` 需要 Qt5 符号 `_ZdaPvm`（sized array delete，Qt 5.9+），但 Cadence / PDK setup 可能把老 Qt5 塞进 `LD_LIBRARY_PATH` 前面覆盖掉系统新 Qt5。Phase 5 GUI 跑不起来时：

```bash
# 看 PyQt5 绑的是哪个 Qt5
ldd $(python3.11 -c 'import PyQt5, os; print(os.path.dirname(PyQt5.__file__))')/QtCore.abi3.so | grep -i qt

# 看那个 libQt5Core.so.5 里有哪些 Qt 版本符号
strings <上面输出里的 libQt5Core.so.5 路径> | grep -E '^Qt_5(\.[0-9]+)?$' | sort -u
```

90% 情况是 LD_LIBRARY_PATH 里混了老 Qt5 —— `echo $LD_LIBRARY_PATH | tr : '\n' | xargs -I{} ls {}/libQt5Core* 2>/dev/null` 看哪几条路径有。

**现在 CLI 不受影响**，PyQt5 blocker 等做 Phase 5 再处理。
