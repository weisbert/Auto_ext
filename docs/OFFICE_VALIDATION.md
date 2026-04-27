# Office Validation Guide

办公室真 Cadence 环境下要验证什么 + 怎么一步步验。

**目的**：把 Windows 上跑过的所有事情在 Linux 服务器 + 真 Cadence 上重新跑一遍，发现"在我这能跑、在你那不能跑"的 bug。

**前提**：
- 在服务器上已 `git clone` 过 Auto_ext，路径 `/data/RFIC3/<Project>/<Employee ID>/workarea/Auto_ext_pro/`
- 已 `source` 过项目的 Cadence/PDK setup（`$WORK_ROOT` / `$WORK_ROOT2` / `$VERIFY_ROOT` / `$SETUP_ROOT` / `$PDK_LAYER_MAP_FILE` 都有值）
- Python 3.11 + 离线依赖已装好（`OFFICE_QUICKSTART.md` §0 + §2）

---

> 配置字段（`paths.calibre_lvs_dir` / `paths.qrc_deck_dir` / 等）不知道是啥、去哪填的，看 [`CONFIG_GLOSSARY.md`](CONFIG_GLOSSARY.md)。Phase 5.6.5 起每个 path 直接是模板 `[[<key>]]` 引用的整条目录路径，可以用 `$X|parent` 形式动态地基于 env var 派生。

## Step 1 — 拉最新代码 + 跑测试

```bash
cd /data/RFIC3/<Project>/<Employee ID>/workarea/Auto_ext_pro
git pull
./run.sh test                       # 跑 tests/ 全集
./run.sh test tests/core -k progress  # 也接受 pytest 任意参数
```

**预期**：Phase 5.6.5 之后当前 main 是 **Linux ~593 绿**（Windows 上 585 绿 + 10 skip；Linux 上 symlink + UI 测试都能跑，10 个 skip 里有 9 个解锁）。如果没拉到 `c9f009d`（Phase 5.6.5 paths-as-paths refactor）之后的版本，老 schema 跑出来是 579 绿。

`./run.sh test` 自动做了：
- `cd workarea`
- 把 PyQt5 自带的 Qt5/lib 塞到 `LD_LIBRARY_PATH` 最前（绕开 CentOS 7 libstdc++ 太旧导致 `_ZdaPvm` 缺失的问题，否则 pytest-qt plugin 在启动阶段就 INTERNALERROR）
- `exec python -m pytest` + 透传你给的参数

不走 `./run.sh test` 而直接 `python3.11 -m pytest tests/`，会被 pytest-qt 的 PyQt5 import 卡死在 `pytest_configure` 阶段。

如果数字明显偏低 → **停下来**先查哪条失败，不要带着失败的测试去碰真 EDA。

---

## Step 2 — GUI 能不能起来

```bash
./run.sh gui
```

**预期**：弹出 PyQt5 窗口，5 个 tab（Run / Log / Project / Tasks / Templates）。

**如果报错**：
```
ImportError: .../PyQt5/QtCore.abi3.so: symbol _ZdaPvm, version Qt_5 not defined
```
→ 是 CentOS 7 的 libstdc++ 太旧。`run.sh` 应该已经自动 prepend PyQt5 自带的 Qt5 lib。如果还是报错，确认 `<site-packages>/PyQt5/Qt5/lib/` 这个目录在不在；没有就 `bash scripts/install_offline.sh` 重装一次（默认 wheel 是带 bundled Qt5 的）。

**如果 X11 forward 慢**：忍一下，先把功能验完，性能调优后面说。

---

## Step 3 — 验 Phase 5.6.5 paths schema（最关键）

5.6.5 是个 schema breaking change：`pdk_subdir` / `runset_versions.{lvs,qrc}` / `project_subdir` 全删了，换成 `project.paths.<key>` 整条路径。这次 office 验证要 **重点确认两条 path 在真 PDK 路径下解析正确，且每个模板渲染出的路径是对的**。Windows 上跑过手动 sanity render，但**没在真 EDA 服务器、真 `$VERIFY_ROOT` 下跑过**。

### 3.1 旧 yaml 必须迁移（破坏性变更，不报错就出问题）

如果你的 `Auto_ext_pro/config/project.yaml` 还在用 5.6.4 schema：

```yaml
# 旧（5.6.5 不再接受）
pdk_subdir: CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9
runset_versions:
  lvs: Ver_Plus_1.0l_0.9
  qrc: Ver_Plus_1.0a
```

`./run.sh check-env --config-dir config` 应该 **明确报错** 提到 `pdk_subdir` / `runset_versions` 是 unknown field（pydantic `extra="forbid"`）。**这就是 5.6.5 的预期行为**——如果没报错说明拉错代码了。

迁移到新 schema：

```yaml
paths:
  calibre_lvs_dir: $calibre_source_added_place|parent
  qrc_deck_dir: $VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9/QCI_deck
```

`calibre_lvs_dir` 用 `$calibre_source_added_place|parent` 是因为这个 env var 由 PDK setup 设置成 `<dir>/empty.cdl`，`|parent` 取 dir 本身。`qrc_deck_dir` 没有等价的 env var 约定，建议显式拼。详见 `CONFIG_GLOSSARY.md#paths`。

### 3.2 渲染验证：4 个文件 4 条不同路径

**这是 5.6.5 的核心 bug fix**：旧 schema 让 calibre `*lvsPostTriggers` + 两个 quantus 模板都通过 `[[pdk_subdir]]` 渲染，但 LVS subdir 和 QRC subdir 是**不同字符串**——所以旧版渲染出来 calibre lvsPostTriggers / quantus 文件都用了错的 LVS dir。新版每个模板该用哪个 path 是分开声明的。

```bash
./run.sh run --config-dir config --dry-run
RD=runs/task_*/rendered

# (a) calibre LVS rules file → 应该用 calibre_lvs_dir
grep -E '^\*lvsRulesFile' $RD/calibre_lvs.qci

# (b) calibre lvsPostTriggers query_input → 应该用 qrc_deck_dir（不是 LVS dir！）
grep 'query_input' $RD/calibre_lvs.qci

# (c) quantus ext.cmd 的 -parasitic_blocking → 应该用 qrc_deck_dir
grep 'parasitic_blocking' $RD/ext.cmd

# (d) quantus dspf.cmd 的 -parasitic_blocking → 应该用 qrc_deck_dir
grep 'parasitic_blocking' $RD/dspf.cmd
```

**预期**：
- (a) 是 `<calibre_lvs_dir>/<calibre_lvs_basename>.<variant>.qcilvs`，里面是你 PDK 的 **LVS** subdir
- (b)(c)(d) 都是 `<qrc_deck_dir>/...`，里面是你 PDK 的 **QRC** subdir + `Ver_Plus_1.0a`（不是 `Ver_Plus_1.0l_0.9`）
- (a) 和 (b) 解析出来的目录**不应该相同**（LVS dir vs QRC dir）。这一条是 5.6.5 的核心 regression check——如果它们一样，说明 schema 没生效。
- 全部路径都是真值，没有 `$X` / `${X}` / `$env(X)` / `<runset>` / `<pdk_subdir>` 残留。

### 3.3 GUI Project tab → Paths group

5.6.5 给 GUI Project tab 加了新的 **Paths** 组，每个 path 下面会用 grep 出来的 "Used by" 列表告诉你它在哪个模板第几行被用。

1. `./run.sh gui --config-dir config`
2. 切到 **Project** tab → 滚到 **Paths** 组（在 Output 组下面）
3. **预期看到两条**：
   - `calibre_lvs_dir:` + 输入框（你填的表达式）+ 下面 `↳ calibre_lvs.qci.j2:1  *lvsRulesFile: [[calibre_lvs_dir]]/...`
   - `qrc_deck_dir:` + 输入框 + 下面 3 行 `↳`：`calibre_lvs.qci.j2:26` / `ext.cmd.j2:18` / `dspf.cmd.j2:18`
4. **Hover 输入框看 tooltip**：应该显示 `resolves to: <真路径>`，把 env var 解析出来给你预览。如果 tooltip 显示成 `(error: ...)` 或者 `$X` 字面没替换 → env 没 source 到位
5. 点 `+ Add path` 按钮 → 输入个 key（如 `test_dir`）→ 新行出现，输入框为空，Used by 显示 `↳ (no template references this path)` ← 这是预期，因为没模板引用它
6. 行末 `−` 按钮删掉 `test_dir` → Save → 看 `project.yaml` 里这个 key 没了
7. 编辑 `calibre_lvs_dir` 的值（比如改成 `$calibre_source_added_place|parent|parent` 试试 chained `|parent`）→ 顶上 `●` 脏标记 → Save → reload 看持久化
8. 改回去 `$calibre_source_added_place|parent` → Save

**这一步的关键看点**：Used-by 列表是不是从你**实际配置的模板**里 grep 的（不是 hardcode）；改了 path 表达式之后 tooltip 的 resolves-to 是不是马上更新。

### 3.4 init-project 在真 raw 上的路径反解

如果有空的话，把你**真的 office 用的 4 份 raw 文件**喂给 `init-project`（File → New project from raws…），到 Preview 页看 `生成的 yaml` tab：

- `paths.calibre_lvs_dir` 应该是真路径，类似 `$VERIFY_ROOT/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9`（注意：**整条路径，不是单个段**）。`$VERIFY_ROOT` / `$env(VERIFY_ROOT)` 都会被规范化成 `$VERIFY_ROOT`。
- `paths.qrc_deck_dir` 同理，类似 `$VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CF710_Plus_0818_QRC_QCI_1P10M_7X1Z1U_AL28K_V1.0a_offline/QCI_deck`。
- 如果 calibre 的 `*lvsPostTriggers` 行抽出来的 QRC dir 和 quantus 的 `-parasitic_blocking_device_cells_file` 抽出来的不一致 → 不会 promote，两个值都会出现在 Unclassified 区，需要你手 review。这种情况报告一下。

---

## Step 4 — 验 Phase 5.6.3 新加的 calibre knob

5.6.3 的两个 knob，**还没在真环境验过**。

### 4.1 TemplatesTab → 项目层默认值

1. GUI → **Templates** tab → 左侧选中 `templates/calibre/calibre_lvs.qci.j2`
2. 右侧 **Knobs** 面板应该显示两行：
   - `lvs_variant` —— 一个**下拉框**，里面 `wodio` / `widio` 两个选项，默认 `wodio`
   - `connect_by_name` —— 一个**勾选框**，默认未勾
3. 把 `lvs_variant` 切到 `widio`，勾上 `connect_by_name` → 顶上 `●` 脏标记亮起 → 点 **Save**

**回头看 `config/project.yaml`**：
```bash
cat config/project.yaml | grep -A4 'knobs:'
```
应该出现：
```yaml
knobs:
  calibre:
    lvs_variant: widio
    connect_by_name: true
```

### 4.2 渲染验证

```bash
./run.sh run --config-dir config --dry-run
cat runs/task_*/rendered/calibre_lvs.qci | grep -E 'lvsRulesFile|VConnectNamesState'
```

**预期**两行：
```
*lvsRulesFile: <calibre_lvs_dir>/<calibre_lvs_basename>.widio.qcilvs
*cmnVConnectNamesState: ALL
```
其中 `<calibre_lvs_dir>` 是 `project.yaml.paths.calibre_lvs_dir` 解析结果（典型：`$calibre_source_added_place|parent`），`<calibre_lvs_basename>` 自动取 `Path(calibre_lvs_dir).name`。

把 GUI 改回默认（`lvs_variant=wodio` + `connect_by_name=false` → reset 按钮也行），再 dry-run 一次：
```
*lvsRulesFile: ...wodio.qcilvs
```
不应该再有 `*cmnVConnectNamesState` 那行。

### 4.3 TasksTab 单 task override

1. GUI → **Tasks** tab → 选中一个 task
2. 展开右侧的 `▷ knobs (advanced — per-task overrides)`
3. 在 `calibre` 段里把 `lvs_variant` 设成 `widio`（即使 project 默认是 `wodio`）
4. Save → 看 `tasks.yaml` 里出现 per-task `knobs.calibre.lvs_variant: widio`
5. 再 dry-run，看那个 task 的 `rendered/calibre_lvs.qci` 是 widio，其他 task 仍然用 project 默认

**这一步要看的是**：per-task override 在 GUI 里能正常落盘 + 优先级正确。

---

## Step 5 — Init wizard 走一遍真路径

Phase 5.7 的 init wizard 在 Windows 上跑过 mock 数据，但**没用真 Cadence raw 跑过**。

1. 准备 4 份真导出文件（calibre `.qci` / si `.env` / quantus `.cmd` / 可选 jivaro `.xml`）
2. GUI → File 菜单 → **New project from raws…**（或 Ctrl+N）
3. 6 步走完：Intro → Destination → RawFiles（拖文件进去）→ Preview → Commit → Result
4. 默认 destination 是 `$WORK_ROOT2/Auto_ext_pro/`，可以改到一个 throw-away 目录验，别覆盖你正在用的 config

**Preview 页要看的**：
- `概要` tab 应识别出 cell / library / lvs view 等 identity 字段，没有红色冲突 banner
- `生成的 yaml` tab 显示的 `project.yaml` + `tasks.yaml` 路径都是真路径（没有 `$X` / `${X}` 残留）

**Commit 页**：点下一步 → 进度日志一行行刷出来 → ResultPage 列出写入的文件
**ResultPage**：勾选 `自动加载` → 点完成 → 主窗口自动 load 新 config

**如果哪步卡住或者报错**：截图 + 记下用的什么 raw 文件，回头反馈。

---

## Step 6 — 网络挂载下的 QFileSystemWatcher

Auto_ext 里的 file-watcher 用来检测外部 `project.yaml` / `tasks.yaml` 改动（比如你 vim 编辑了）。**inotify 在 NFS 上不一定工作**，没在 `/data/RFIC3/...` 验过。

1. GUI 里 load 一个 config（不要做改动）
2. 在另一个终端：`vim config/project.yaml` → 随便改一个值（如 `tech_name`）→ 保存
3. **预期**：GUI 弹出"配置已被外部修改，是否重新加载？"对话框

**如果没弹**：file-watcher 在你的网络挂载上不工作。不致命（手动 reload 就行），但记下来反馈，可能要换轮询模式。

---

## Step 7 — TemplateDiffViewer 看一眼

Phase 5.6.2 的纯查看工具，验它能起来即可。

1. GUI → Templates tab → toolbar `[模板对比…]` 按钮
2. 弹出对话框 → 拖两份 `.j2` 进去（左右各一）
3. **预期**：左右并排显示，差异行有颜色标记（红=只在左、绿=只在右、黄=变了），滚动同步

不需要保存，关掉就行。验的就是"它能起来 + 对真模板没崩"。

---

## Step 8 — Phase 3.5 并行验证（可选，license 富裕的话再做）

详见 `PHASE_3_5_VALIDATION.md`。简版：

```bash
./run.sh run --config-dir config --jobs 4
```

要看的：
- 4 个 task 真的并行起来（top 看 si/calibre 进程数）
- license pool 不够时报什么错，不会卡死
- 同 cell 不同 knob 的 task 能否共存（Phase 5.5.1 的 `extraction_output_dir` 区分符那块）
- 故意让某个 task LVS 挂掉，看 summary 里 mixed pass/fail 的呈现

---

## 要带回来的东西（Phase 5.8 输入 + 5.6.5 反馈）

跑的过程中**记下这些**，回家路上发我：

1. **5.6.5 paths schema 在你真 PDK 下解析对不对**：Step 3.2 那 4 个 grep 命令的输出贴一下；尤其 calibre lvsRulesFile 解析出来的 LVS dir + lvsPostTriggers / quantus 解析出来的 QRC dir，**两条路径长什么样**。如果用了 `$calibre_source_added_place|parent`，原始 env var 的值也贴一下。
2. **init-project 在真 raw 上反解出来的 paths 对不对**（Step 3.4）：calibre / quantus 是否同意一个 qrc_deck_dir，还是进 Unclassified？
3. **Project tab Paths 组的 Used-by 列表对不对**（Step 3.3）：是不是显示了正确的 template:line + excerpt？
4. **哪些 hardcode 的 literal 你想改成 knob？** 比如 calibre `.qci` 里的 `*cmnNumTurbo: 2`、quantus `.cmd` 里某个数值。Phase 5.8 就是干这个的。
5. **GUI 里哪一步用得别扭？** 截图 + 一句话描述，比 plan 阶段的猜测准多了。
6. **真环境跑出来的报错**：完整 traceback + 当时在干啥。

---

## 出问题怎么办

1. **测试就挂**（Step 1）→ 截 `pytest` 输出，不要往下走
2. **GUI 起不来**（Step 2）→ 跑 `python3.11 -c "import PyQt5.QtCore"` 看具体错
3. **Paths 渲染不对**（Step 3）→
   - calibre lvsRulesFile 用了 QRC dir，或者 lvsPostTriggers / quantus parasitic_blocking 用了 LVS dir → 说明你的 yaml 把 calibre_lvs_dir / qrc_deck_dir 填反了，或者用了同一条 path
   - 路径里有 `$X` / `$env(X)` / `${X}` 残留 → env var 没 resolve（`echo "$VERIFY_ROOT"` 验证 PDK setup 是不是 source 过了）
   - `(error: unknown path filter ...)` → yaml 里 `|parent` 拼错（只支持 `parent`，不支持 `name` / `stem` 等）
   - 加载 yaml 时报 `extra_forbidden` / `pdk_subdir` → 老 yaml 没迁移，照 §3.1 改
4. **Knob 渲染不对**（Step 4）→ `cat runs/task_*/rendered/calibre_lvs.qci` 全文看一下，对比 `templates/calibre/calibre_lvs.qci.j2` 和 manifest sidecar
5. **EDA 报错**：`logs/task_<id>/<stage>.log` + `runs/task_<id>/rendered/<file>` 都贴出来；多数 EDA 错的根因在渲染产物里能直接看到（比如路径里有 `$X` 残留就是 env 没解析）

---

## 附：当前已知 quirks（别浪费时间）

`memory/project_cadence_quirks.md` 里记的几条，遇到了别再重新踩：

- **si 不会把 `si.env` 拷到 simRunDir** —— Auto_ext 已经在 si 后手动 publish，正常
- **`.running` 文件不是 stale lock** —— SiTool 已经 unconditionally unlink，正常
- **Calibre v2019.2 通过 LVS 时不写 `DISCREPANCIES = 0` 行** —— parser 已经接受两种格式
- **si.env 里写错 cell 的字段大概率"也能跑"，但 metadata 错** —— 别信"跑通了"，记得对一下 `simLibName` / `simCellName` 是不是当前 task
