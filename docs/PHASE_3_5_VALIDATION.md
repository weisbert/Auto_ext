# Phase 3.5 Parallel Runner — Office Validation

**目的**：在办公室真 Cadence 环境下验证 `--jobs N` 并行路径是否能真的跑起来。Windows 上的 mock 测试只能保证代码路径通了；si 能不能在 symlinked `cds.lib` 下正确读 library、Calibre/QRC license 并发上限在哪，这些**只能真跑**才知道。

**前提**：
- 你在办公室，已 `source` 过 PDK / Cadence setup 脚本（`$WORK_ROOT` / `$WORK_ROOT2` / `$VERIFY_ROOT` / `$SETUP_ROOT` / `$PDK_LAYER_MAP_FILE` 都有值）。
- 已 `git pull` 到 Phase 3.5 的 commit。
- `tasks.yaml` 里至少有 2 个不同 cell（下面每步会用到）。没有的话先加一个，改成 `cell: [cellA, cellB]` 让 Cartesian 展开也行。

---

## Step 0 — 拉代码 + 跑测试

```bash
cd /data/RFIC3/<Project>/<Employee ID>/workarea/Auto_ext_pro
git pull
python3.11 -m pytest tests/ -q
```

**预期**：`303 passed`（Linux 上 symlink 测试能跑，9 个 skip 全部 unskip）。

**不对劲信号**：低于 303 → 先查是哪条失败再往下走。测试是 Phase 3.5 的防线，测试过不了就不要碰真 EDA。

---

## Step 1 — Dry-run 看 preflight 工作

```bash
./run.sh run --config-dir Auto_ext_pro/config --jobs 2 --dry-run
```

**预期**：

- 不起任何 EDA subprocess（只渲染模板）。
- Summary 里每个 task 的所有 stage 状态是 `dry_run` / `skipped`。
- `runs/task_<id>/rendered/` 下应有 4 份渲染产物（`*.env` / `*.qci` / `*.cmd` / `*.xml`）。

**不对劲信号**：

- 报 `duplicate (library, cell) pair(s)` → 你的 tasks.yaml 里两个 task 同 library + cell，把它们 cell 改不一样再跑。这不是 bug，是 preflight 工作了。
- 报 `missing env var` → PDK setup 没 source，回去 source 一下。

---

## Step 2 — si 单 stage，`--jobs 2`（**最关键的一步**）

```bash
./run.sh run --config-dir Auto_ext_pro/config --jobs 2 --stage si
```

**这一步在验什么**：si 读 `cds.lib` 是在 cwd 下找的。并行模式下每个 task 的 cwd 不是 workarea 而是 `runs/task_<id>/`，里面的 `cds.lib` 是指回 `workarea/cds.lib` 的 symlink。si 到底认不认这个 symlink 是原设计文档里的 **open risk #1**。

**预期**：

- Summary 两个 task 都 `passed`，`si` stage 都 `passed`。
- `runs/task_<id>/` 里每个都有：
  - `cds.lib` 是 symlink（`ls -la` 看到 `->` 指向 workarea 的 cds.lib）
  - `.cdsinit` 是 symlink
  - `si.env` 是普通文件（不是 symlink，是当前 task 的渲染产物）
  - `rendered/default.env`（刚渲染的 si.env 来源）
- `workarea/si.env` **不存在**（并行模式不动 workarea 共享文件，这是跟串行的关键区别）。

**不对劲信号**：

- si 报 `cannot open cds.lib` / `library ... not found` → symlink 没被 si 认可。把 `logs/task_<id>/si.log` 贴出来。临时 workaround：用 `cp` 替代 symlink（需要改 `core/workdir.py:prepare_parallel_workdir`），但这会让 cds.lib 分叉，不是长期方案。
- 某个 task 的 si 挂了另一个好的 → 记下哪个挂的，看 log 是 license 问题还是真 fail。

---

## Step 3 — 全链 2 job

Step 2 过了再跑：

```bash
./run.sh run --config-dir Auto_ext_pro/config --jobs 2
```

**预期**：5 stage × 2 task 全绿。

**看什么**：

- 两个 task 的 `runs/task_<id>/rendered/` 内容应该**不同**（cell 不同 → `output_dir` 不同 → 渲染出的 `*lvsRunDir` / `-outputFile` 路径不同）。`diff` 两个 task 的 rendered qci 应能看到 cell / output_dir 的差异。
- `$WORK_ROOT/cds/verify/QCI_PATH_<cellA>/` 和 `QCI_PATH_<cellB>/` 各自有输出，没有互相覆盖。
- `logs/task_<id>/<stage>.log` 里 argv 行能看到 `cwd: .../runs/task_<id>`（不是 workarea）。

**不对劲信号**：

- 两 task 的 qrc 输出文件名撞了 → 是不是 task 的 `out_file` 一样？同 `(library, cell)` 已经被 preflight 挡了，但同 cell 不同 view + 同 out_file 可能撞，看日志确认。
- calibre 卡住不返回 → 可能 license 在等，等 10 分钟再看；长时间不动就 `Ctrl-C`，记录 license 数配置。

---

## Step 4 — License 天花板探测

```bash
./run.sh run --config-dir Auto_ext_pro/config --jobs 4
```

**预期**（两种都可能，取决于你的 license 池大小）：

- 如果 license 够：4 个 task 都 passed，只是峰值并发 = 4。
- 如果 license 不够：撞天花板的 task 在 `logs/task_<id>/<stage>.log` 里会看到 "no license available" / "license queue" / "waiting for feature" 之类信息。有的 EDA 会 block 等（慢，最终成功），有的直接 fail（stage 红）。

**要记录的事**：

- 2 / 4 / 8 jobs 时，同时在跑的 Calibre / QRC session 数（`lmstat -a` 或 `/tmp/.../license.log` 看）。
- 撞天花板时 log 里的具体错误字符串（以后可以做成一个友好的检测）。
- 最后决定你日常用 `--jobs 几`。

**不对劲信号**：runner 自己 crash（Python traceback 在 stdout） → 是 bug。贴 traceback。

---

## Step 5 — Preflight 挡 duplicate

故意让 preflight 工作一次：

```bash
# 备份
cp Auto_ext_pro/config/tasks.yaml Auto_ext_pro/config/tasks.yaml.backup
# 把第一个 task 整段复制一份粘到文件末尾（同 library 同 cell）
# 编辑方式看你习惯，vim / nano 都行
vim Auto_ext_pro/config/tasks.yaml

./run.sh run --config-dir Auto_ext_pro/config --jobs 2 --dry-run
```

**预期**：

- Exit code 2（不是 0 不是 1）
- stderr 有 `run aborted: duplicate (library, cell) pair(s) would share extraction_output_dir:`，后面列出具体 task_ids。
- `runs/` 目录**不被创建**（preflight 在任何 render / subprocess 前就挂）。

**恢复**：`mv Auto_ext_pro/config/tasks.yaml.backup Auto_ext_pro/config/tasks.yaml`。

---

## Step 6 — 混合 pass/fail

让一个 task 的 calibre 挂，另一个照常跑。制造办法任选其一：

**办法 A**（快）：编辑某个 cell 的渲染后 qci，把 `*lvsLayoutPaths` 改成一个不存在的文件：

```bash
# 先跑一次 --dry-run 让 rendered/ 有文件
./run.sh run --config-dir Auto_ext_pro/config --jobs 2 --dry-run
# 破坏其中一个 task 的 qci
vim runs/task_<某个 cellA 的 task_id>/rendered/*.qci
# 找 *lvsLayoutPaths 行，路径改成 /nope/nope.calibre.db
./run.sh run --config-dir Auto_ext_pro/config --jobs 2
```

**办法 B**（干净）：临时把一个 cell 改成一个真不存在的 cell 名，让 si 就挂：

```bash
# 编辑 tasks.yaml，把 cellA 改成 nonexistent_cell
# 跑
./run.sh run --config-dir Auto_ext_pro/config --jobs 2
```

**预期**：

- Summary 里：一个 task `failed`，另一个 `passed`。
- 失败的 task 在挂的 stage 后面全是 `skipped`。
- 成功的 task 所有 stage `passed`，**不受另一个 task 影响**（这是并行跟串行都保证的，但要真跑过才放心）。
- Runner 整体 exit code = 1。

**不对劲信号**：另一个 task 也挂了，说明并行 task 之间有串扰（最可能的串扰点：共享 workarea 里的某个文件 —— 但并行模式不该用共享文件）。把两个 task 的日志都贴出来。

---

## 跑完回来告诉我

最少回报这些（按 Step 编号对齐）：

1. `pytest` 是 303 还是别的数。
2. si 单 stage 有没有报 cds.lib 问题。**这是最关键的信号**。
3. 全链 2 job 是不是全绿；`diff runs/task_<A>/rendered/*.qci runs/task_<B>/rendered/*.qci` 是不是看起来合理。
4. `--jobs 4` 撞没撞 license 天花板，撞的话日志里是什么字符串，你记录的 license 峰值。
5. preflight 有没有挡住 duplicate。
6. 混合 pass/fail 有没有互相影响。

全部 OK → Phase 3.5 真正 ship，push 一个新 commit 把这份文档里"uncommitted/pending office"的状态改掉。

有一项出问题 → 贴日志，改代码，再跑一遍。

---

## 产物参考（验证时看这些路径）

```
Auto_ext_pro/
└── runs/
    ├── task_<lib>__<cellA>__<layout>__<schematic>/
    │   ├── cds.lib -> $WORK_ROOT2/cds.lib          # symlink
    │   ├── .cdsinit -> $WORK_ROOT2/.cdsinit        # symlink
    │   ├── si.env                                  # 并行模式特有
    │   └── rendered/
    │       ├── default.env
    │       ├── calibre_lvs.qci
    │       ├── ext.cmd
    │       └── default.xml
    └── task_<lib>__<cellB>__...
        └── (同上结构，内容因 cell 而异)

Auto_ext_pro/logs/
├── task_<lib>__<cellA>__.../
│   ├── si.log              # 每个 log 顶部有 argv + cwd 行可验证
│   ├── strmout.log
│   ├── calibre.log
│   ├── quantus.log
│   └── jivaro.log
└── task_<lib>__<cellB>__.../
    └── ...

$WORK_ROOT/cds/verify/
├── QCI_PATH_<cellA>/       # Calibre LVS / QRC 输出
└── QCI_PATH_<cellB>/
```

并行模式下 `$WORK_ROOT2/si.env`（workarea 根目录下的 si.env）**不应该出现**。出现就是代码漏了哪个分支。
