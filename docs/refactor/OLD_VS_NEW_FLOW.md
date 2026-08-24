# 老脚本 vs 新架构：流程逐条对比

**为什么有这份文档。** 老脚本的流程经过几个月真实使用验证，是**资产**，不是待重构的债务。
重构的目标是 GUI 和架构，流程本该原样保留。这份文档逐条核对"到底有没有原样保留"，
把结论分成三堆：**一致的**（不必再花时间）、**有差异需要你拍板的**、
**一类系统性风险**（比任何单条差异都重要）。

对比基准：`docs/archive/Old_project_prompt.txt`（老脚本的原始需求书）
对比对象：本分支 `refactor/recipe-and-run` 的实际代码。

---

## 零、先说最重要的一条：一类系统性风险

老脚本处理模板的方式是**查找替换**，替换清单是写死的十七个字段：

| 模板 | 老脚本会替换的字段 |
|------|---------------------|
| calibre `.qci` | `CellName`、`LibraryName`、`*lvsSourceView:`、`*lvsLayoutView:`、`QCI_PATH` |
| quantus `.cmd` | `CellName`、`LibraryName`、`-ground_net`、`QCI_PATH`、`-view_name` |
| jivaro `.xml`  | `inputView`、`frequencyLimit`、`errorMax`、`outputView` |
| `si.env`       | `CellName`、`LibraryName`、`simViewName`、`QCI_PATH`、`WORK_ROOT` |

**清单以外的一切，老脚本一个字都不碰，原样保留你自己模板里的值。**
你的模板是你手工维护的，所以那些值天然是对的 —— 老脚本靠"不碰"保证正确。

新架构反过来：catalog 托管 **177 个参数**，其中 **168 个的默认值是从仓库里那份
shipped 模板反读出来的**（每行都带 `observed: true` 和 `source_ref`）。

> **于是风险的形状是：只要"仓库里那份 shipped 模板" ≠ "你服务器上真正在用的模板"，
> 差异就会被冻成 catalog 默认值，而且此后再也没人更新它。**

`viewsToReduce` 就是这个风险已经兑现的一例：它不在老脚本的替换清单里（老脚本不碰），
shipped 模板里写的是 `av_extracted`，而你实际用的是 `av_ext`。老脚本正确，新架构错。

**这不是一个 bug，是一类 bug。** 要一次找全，只有一个办法：

```bash
# 在服务器上，把你真正在用的四份模板拿出来
diff <你的 calibre_wiodio.qci>  examples/legacy/templates/calibre/calibre_lvs.qci.j2
diff <你的 qrc_ext.txt>          examples/legacy/templates/quantus/ext.cmd.j2
diff <你的 jivaro_tempt.xml>     examples/legacy/templates/jivaro/default.xml.j2
diff <你的 si.env>               examples/legacy/templates/si/default.env.j2
```

（`.j2` 那边多了 Jinja 的 `[[ ]]` 洞，比对时看**洞以外**的字面量。）

**每一处不同，都是一个和 `viewsToReduce` 同类的候选 bug。** 这一条的性价比高于本文档其余全部内容。

---

## 一、完全一致，不必再验

以下逐条核对过代码，与老规格书一字不差或语义等同。**不要在这些上面再花时间。**

| # | 老规格书 | 新代码 | 位置 |
|---|----------|--------|------|
| 1 | 严格 LVS→QRC→jivaro 顺序 | `STAGE_ORDER = ("si","strmout","calibre","quantus","jivaro")` | `core/runner.py:162` |
| 2 | 跑 si 前先删 workarea 下的 `.running` | 每次 si 前无条件 `unlink` | `tools/si.py` |
| 3 | `si -batch -command netlist -cdslib ./cds.lib` | argv 一字不差 | `tools/si.py` |
| 4 | si 读 cwd 的 si.env，需复制一份到 workarea | 跑前复制、跑完删除 | `core/workdir.py` |
| 5 | `calibre -gui -lvs -runset <file> -batch` | argv 一字不差（含 `-gui` 与 `-batch` 并存） | `tools/calibre.py` |
| 6 | LVS 不过就中止，不进下一步 | 默认中止 | `core/runner.py` 失败策略 |
| 7 | `qrc -cmd <file>` | argv 一字不差 | `tools/quantus.py` |
| 8 | `jivaro -xml <file>` | argv 一字不差 | `tools/jivaro.py` |
| 9 | 每单元一个 `QCI_PATH_<CellName>` 目录 | `${WORK_ROOT}/cds/verify/QCI_PATH_{cell}` | `config/workspace.yaml:10` |
| 10 | 不可改原模板，渲染新文件到别处 | 渲染进 `runs/<id>/rendered/` | `core/render.py` |
| 11 | 串行，task1 跑完才 task2 | 默认串行 | `run_tasks(max_workers=None)` |

**第 2 条值得单独说一句**：新代码的注释比老规格书还准 —— si **正常退出也不删 `.running`**，
所以那个文件是稳态，不是异常残留。这解释了为什么"无条件删"是对的，也解释了为什么
si 被强杀之后可以直接重试。

---

## 二、有差异，需要你拍板

### D1. LVS 判据变严了 ⚠️ **而且顺带修了老脚本一个真 bug**

- **老**：report 里出现 `CORRECT` 字符 → 算通过。
- **新**：`INCORRECT` 优先判负；`CORRECT` + `DISCREPANCIES = 0` 才算过；
  `CORRECT` + 非零 discrepancies 判**失败**；没有 banner 直接报错不猜。
  老版 Calibre（v2019.2）在干净通过时不打印 DISCREPANCIES，回退去查 CELL SUMMARY 表。

**顺带修的 bug**：`INCORRECT` 这个词里**包含** `CORRECT`。老脚本朴素查找 `"CORRECT"`，
在一份 INCORRECT 的报告上会误判为**通过**。新代码用负向断言排除了这种情况
（`core/checks.py:46`）。

**要你确认的**：新判据比老的严。以前被放过的边界情况（有 discrepancy 但 banner 是 CORRECT）
现在会被拦。这是对的还是会挡你的路？

### D2. GDS 文件名和落点变了 ⚠️ **你曾经有、现在被拿掉的能力**

- **老**：文件名 `<CellName>.gds`；菜单里三选一 —— 默认路径 / 当前路径 / 指定子路径。
- **新**：文件名固定 `<cell>.calibre.db`（内容仍是 GDS，Calibre 按 magic bytes 认格式），
  落点固定在 output_dir。

三个落点的选择**没有对应物**。这条已在 `OFFICE_TODO.md` 里挂着，此处只是说明它的来源。

### D3. jivaro 开关的作用域变了 ⚠️ **改变你的日常操作方式**

- **老**：`ifJivaro: Yes/No` —— **每个 task 一个**。
- **新**：`recipe.reduction.enabled` —— **每个配方一个**，而配方是多单元共享的。

`ReductionSettings` 的 docstring 明说：per-cell 的 `JivaroOverride` 是**故意删掉**的，
"需要不同 reduction 设置的单元，就该用第二份配方"。

**后果**：以前一张任务表里"A 单元做 jivaro、B 单元不做"是一行配置；
现在需要两份配方，单元表里分别指过去。这是**设计决定不是 bug**，但它改变了你的操作习惯，
所以要你点头。

### D4. `viewsToReduce`：从"不碰"到"托管但抄错初值"

见第零节。老脚本不替换它，新架构托管了它，初值取自 shipped 模板的 `av_extracted`，
而你的 `out_file` 是 `av_ext`。

**结构性问题**：它被放在 **recipe 作用域**（多单元共享），而视图名是 **cells 作用域**
（每单元一个）。一份共享配方不可能知道某个单元的视图叫什么。所以**无论字面值对不对，
放在配方里就已经错了**，正解是从 `out_file` 派生 + 留覆盖口。

**唯一还需要问工具的**：Jivaro 的 `viewsToReduce` 能不能填多个值？
如果能，说明它另有用途，那覆盖口是必需的而不是可选的。开 Jivaro 界面瞄一眼即可。

### D5. 新增并行（串行路径未变）

- **老**：明确要求串行。
- **新**：默认仍串行；`--jobs N` 才开并行。目录名默认在两种模式下都是
  `QCI_PATH_{cell}`，**没有变**。

并行带来的唯一新约束是预检：两个任务如果解析出**同一个** output_dir，
串行下合法（工作区是复用不是争用，只记一条日志），**并行下直接拒绝启动** ——
两个并发任务写同一个 svdb 会互相写坏。想让同一单元的两次运行并行，
得自己把 `output_dir_pattern` 改成带 `{run_slug}` / `{run_id}` / `{recipe}` 的形式
换取隔离（`core/runner.py:1944-1949`）。

**串行路径完全没变**，所以这条只在你真的用 `--jobs` 时才相关。

### D6. 新增 `runs/` 运行记录（纯增量）

- **老**：结果就在 `QCI_PATH_<cell>` 里，同名文件直接覆盖；**不删目录**。
- **新**：`QCI_PATH_<cell>` 的行为**照旧**（仍被每次运行重写）；额外在
  `runs/<UTC时间戳>_<slug>/` 存一份永不覆盖的记录（渲染产物、日志、结果、配方快照）。

不影响老行为，是加法。

### D7. 老菜单 → 新命令行，一项没有对应物

| 老菜单 | 新命令 |
|--------|--------|
| 检查环境、模板是否齐全 | `./run.sh check-env` |
| 一次性全跑 / 只跑某一个任务 | `./run.sh run`（单元表里 `enabled:` 控制） |
| 仅生成各软件的输入文件 | `./run.sh run --dry-run` |
| 仅 LVS / 仅 qrc / 仅 jivaro | `--stage calibre` / `--stage quantus` / `--stage jivaro` |
| 默认 LVS 已跑完，从 qrc 继续 | `--stage quantus,jivaro` |
| **生成 gds 到 默认/当前/指定 三个落点** | **无对应物** → 见 D2 |

---

## 三、用户裁决（2026-08-24）

| 条目 | 裁决 |
|------|------|
| **D1** LVS 判据变严 | ✅ **接受**。不阻塞流程，保持现状。 |
| **D2** GDS 落点 | ✅ **恢复，但做成两个文件**（见下）。 |
| **D3** jivaro 作用域 | ✅ **接受**。每配方一个开关，需要区分就用第二份配方。 |
| **D4** `viewsToReduce` | 待办：改成从 `out_file` 派生 + 覆盖口。 |

### D2 的落地方式：两个文件，而不是一个可搬家的文件

用户的原话是"给跑 LVS 用的文件就不要去动他，就放在默认的地方；我要一个导出的 GDS
是我为了方便其他软件用的，完全可以做两个"。这个判断比我原先的提案好——

我原本打算把落点做成一个 `layout_out_pattern` 配置项，让 strmout 的 `-strmFile`
和 runset 的 `*lvsLayoutPaths` 一起搬家。**那个方案整个作废了**：只要落点可配，
就存在配错把 LVS 指向一个没人写过的文件的可能，而且失败形态是一个难查的 LVS 报错。

现在的实现里，**LVS 那条路一行代码都没改**：

- `strmout` 照旧写 `<output_dir>/<cell>.calibre.db`，`*lvsLayoutPaths` 照旧读它。
- 导出是**第二次独立的 strmout 调用**，写第二个文件，落在你指定的任何地方。

两条护栏让"搞坏 LVS"从"不建议"变成"不可能"：

1. **带导出的 dispatch，stage 集合必须恰好是 `{strmout}`。** 任何其他组合在起任何
   子进程之前就被 `ConfigError` 拒掉。
2. **多个单元导出时，路径里必须有 `{cell}`。** 否则第二个单元会静默覆盖第一个 ——
   静默是因为 strmout 每次都成功，损失要到后面发现 GDS 内容对不上才暴露。

入口三处，同一条代码路径：

```bash
auto-ext export-gds --config-dir config --to 'reliability/{cell}.gds'
auto-ext run --config-dir config --stage strmout --layout-out '${WORK_ROOT2}/{cell}.gds'
```

GUI：**Cells 表格选中行 → 右键 → `Export GDS…`**。单行给文件对话框（默认名
`<cell>.gds`），多行给目录对话框然后自动补 `{cell}.gds`。stage 集合在这里是**钉死**的，
不是默认值 —— 运行条上勾了什么都不影响导出。

后缀随你：内容本来就是 GDSII，Calibre 按 magic bytes 认格式不看后缀。

**老菜单三个落点的对应关系**：默认路径 = 不用导出，本来就在；当前路径 / 指定路径 =
`--to` 的两种取值。老菜单把"改 LVS 的输入"和"给别的软件一份"并列成三个选项，
新做法把它们彻底分开了。

## 三、结论

- **一致的 11 条不用再验。**
- **要你拍板的 3 条已全部裁决**（见第三节）：D1 接受、D2 做成两个文件（已实现）、D3 接受。
- **要修的 1 条**：D4，且改法已经清楚（从 `out_file` 派生 + 覆盖口）。
- **最高优先级的一件事不在上面任何一条里**：**把你服务器上真正在用的四份模板，
  和 `examples/legacy/templates/` 逐份 diff**。老脚本靠"不碰"保证正确的那 160 个字段，
  现在全部由 shipped 模板的反读值托管。`viewsToReduce` 只是第一个被发现的。
