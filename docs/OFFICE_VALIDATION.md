# Office Validation Guide

办公室真 Cadence 环境下要验证什么 + 怎么一步步验。

**目的**：把 Windows 上跑过的所有事情在 Linux 服务器 + 真 Cadence 上重新跑一遍，发现"在我这能跑、在你那不能跑"的 bug。

**前提**：
- 在服务器上已 `git clone` 过 Auto_ext，路径 `/data/RFIC3/<Project>/<Employee ID>/workarea/Auto_ext_pro/`
- 已 `source` 过项目的 Cadence/PDK setup（`$WORK_ROOT` / `$WORK_ROOT2` / `$VERIFY_ROOT` / `$SETUP_ROOT` / `$PDK_LAYER_MAP_FILE` 都有值）
- Python 3.11 + 离线依赖已装好（`OFFICE_QUICKSTART.md` §0 + §2）

---

> 配置字段（pdk_subdir / runset_versions / 等）不知道是啥、去哪填的，看 [`CONFIG_GLOSSARY.md`](CONFIG_GLOSSARY.md)。Phase 5.6.4 起大多数字段会从 `$calibre_source_added_place` 等 env var **自动反解**，不用手填。

## Step 1 — 拉最新代码 + 跑测试

```bash
cd /data/RFIC3/<Project>/<Employee ID>/workarea/Auto_ext_pro
git pull
./run.sh test                       # 跑 tests/ 全集
./run.sh test tests/core -k progress  # 也接受 pytest 任意参数
```

**预期**：当前 main 是 **574 绿**（Linux 上 symlink 测试能跑、UI 测试也能跑，没有 skip）。

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

## Step 3 — 验 Phase 5.6.3 新加的 calibre knob（最关键）

这是今天刚上的，**还没在真环境验过**。两个 knob 都通过 GUI 改一下，验证渲染产物正确。

### 3.1 TemplatesTab → 项目层默认值

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

### 3.2 渲染验证

```bash
./run.sh run --config-dir config --dry-run
cat runs/task_*/rendered/calibre_lvs.qci | grep -E 'lvsRulesFile|VConnectNamesState'
```

**预期**两行：
```
*lvsRulesFile: $VERIFY_ROOT/runset/Calibre_QRC/LVS/<lvs_runset_version>/<pdk_subdir>/<pdk_subdir>.widio.qcilvs
*cmnVConnectNamesState: ALL
```

把 GUI 改回默认（`lvs_variant=wodio` + `connect_by_name=false` → reset 按钮也行），再 dry-run 一次：
```
*lvsRulesFile: ...wodio.qcilvs
```
不应该再有 `*cmnVConnectNamesState` 那行。

### 3.3 TasksTab 单 task override

1. GUI → **Tasks** tab → 选中一个 task
2. 展开右侧的 `▷ knobs (advanced — per-task overrides)`
3. 在 `calibre` 段里把 `lvs_variant` 设成 `widio`（即使 project 默认是 `wodio`）
4. Save → 看 `tasks.yaml` 里出现 per-task `knobs.calibre.lvs_variant: widio`
5. 再 dry-run，看那个 task 的 `rendered/calibre_lvs.qci` 是 widio，其他 task 仍然用 project 默认

**这一步要看的是**：per-task override 在 GUI 里能正常落盘 + 优先级正确。

---

## Step 4 — Init wizard 走一遍真路径

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

## Step 5 — 网络挂载下的 QFileSystemWatcher

Auto_ext 里的 file-watcher 用来检测外部 `project.yaml` / `tasks.yaml` 改动（比如你 vim 编辑了）。**inotify 在 NFS 上不一定工作**，没在 `/data/RFIC3/...` 验过。

1. GUI 里 load 一个 config（不要做改动）
2. 在另一个终端：`vim config/project.yaml` → 随便改一个值（如 `tech_name`）→ 保存
3. **预期**：GUI 弹出"配置已被外部修改，是否重新加载？"对话框

**如果没弹**：file-watcher 在你的网络挂载上不工作。不致命（手动 reload 就行），但记下来反馈，可能要换轮询模式。

---

## Step 6 — TemplateDiffViewer 看一眼

Phase 5.6.2 的纯查看工具，验它能起来即可。

1. GUI → Templates tab → toolbar `[模板对比…]` 按钮
2. 弹出对话框 → 拖两份 `.j2` 进去（左右各一）
3. **预期**：左右并排显示，差异行有颜色标记（红=只在左、绿=只在右、黄=变了），滚动同步

不需要保存，关掉就行。验的就是"它能起来 + 对真模板没崩"。

---

## Step 7 — Phase 3.5 并行验证（可选，license 富裕的话再做）

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

## 要带回来的东西（Phase 5.8 输入）

跑的过程中**记下这些**，回家路上发我，能直接喂给 Phase 5.8 promote-to-knob：

1. **哪些 hardcode 的 literal 你想改成 knob？** 比如 calibre `.qci` 里的 `*cmnNumTurbo: 2`、quantus `.cmd` 里某个数值。Phase 5.8 就是干这个的。
2. **GUI 里哪一步用得别扭？** 截图 + 一句话描述，比 plan 阶段的猜测准多了。
3. **真环境跑出来的报错**：完整 traceback + 当时在干啥。

---

## 出问题怎么办

1. **测试就挂**（Step 1）→ 截 `pytest` 输出，不要往下走
2. **GUI 起不来**（Step 2）→ 跑 `python3.11 -c "import PyQt5.QtCore"` 看具体错
3. **Knob 渲染不对**（Step 3）→ `cat runs/task_*/rendered/calibre_lvs.qci` 全文看一下，对比 `templates/calibre/calibre_lvs.qci.j2` 和 manifest sidecar
4. **EDA 报错**：`logs/task_<id>/<stage>.log` + `runs/task_<id>/rendered/<file>` 都贴出来；多数 EDA 错的根因在渲染产物里能直接看到（比如路径里有 `$X` 残留就是 env 没解析）

---

## 附：当前已知 quirks（别浪费时间）

`memory/project_cadence_quirks.md` 里记的几条，遇到了别再重新踩：

- **si 不会把 `si.env` 拷到 simRunDir** —— Auto_ext 已经在 si 后手动 publish，正常
- **`.running` 文件不是 stale lock** —— SiTool 已经 unconditionally unlink，正常
- **Calibre v2019.2 通过 LVS 时不写 `DISCREPANCIES = 0` 行** —— parser 已经接受两种格式
- **si.env 里写错 cell 的字段大概率"也能跑"，但 metadata 错** —— 别信"跑通了"，记得对一下 `simLibName` / `simCellName` 是不是当前 task
