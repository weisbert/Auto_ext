# UX 验证：为什么 676 个 GUI 测试没拦住这八个缺陷

> 立档于 2026-08-24，红区第一次真人 GUI 试用之后。
> 机械部分已经落成两个测试文件；这份文档是**它们拦不住的那一类**的处置办法。

## 一、那天暴露的八条

| # | 现象（用户原话） | 类别 |
|---|---|---|
| 1 | 改了温度点保存，没写进去，下次打开还是旧的 | **假动作** |
| 2 | recipes 的名字也没有改 | **够不着** |
| 3 | 改 quantus 的 TYPICAL / RCWORST 的东西在哪里，找不到 | **够不着** |
| 4 | 没有更改 ext 输出的文件名称的地方 | **够不着** |
| 5 | edit rendered file 只能打开 si.env，选不了要 edit 哪一个 | **够不着** |
| 6 | 很多参数是 blank 填写，用户不知道正确写法 | **能用但没法用** |
| 7 | General 里的 stage 也是 blank text，应该是 checkbox | **能用但没法用** |
| 8 | 换项目时 setup 没有地方可以改 | **够不着（已知未排期）** |

三类，泾渭分明，而且**没有一条是逻辑错误**。模型是对的，渲染器是对的，控制器是对的，
测试也证明了这三样都是对的。

## 二、为什么测试全绿

### 假动作（#1）

Recipes 屏的主 Save 按钮只把配方塞进 controller 的待写队列，真正落盘要走 `File → Save`。
围着它的测试是这么写的：

```python
screen.save_button().click()
assert controller.is_dirty            # 通过
assert "recipe:rc-coupled" in controller.pending_keys()   # 通过
```

**这两条断言恰好就是 bug 本身的形状。** 唯一能区分「保存了」和「排队了」的断言，是保存之后
去读磁盘上的字节——而全套 676 个 GUI 测试里没有一条这么做。

根因不是漏测，是**测试的客户端不是用户**。`screen.set_value(...)` 和「一个人坐在窗口前面
找控件、按按钮、然后关掉程序再打开」是两个完全不同的客户端，前者绿了不能推出后者能用。

### 够不着（#2 #3 #4 #5 #8）

同一个形状：模型字段存在、渲染器会读它、CLI 能设它，**但 GUI 里没有任何控件指向它**。

最刺眼的是 #3。`recipes_screen.py` 的模块 docstring 里白纸黑字写着：

> `extraction.corner` has no row here. […] following `owner` rather than the
> artboard is deliberate, and the field re-appears the day the catalog says the
> recipe owns it.

一条**注释声明了这个字段够不着，并说这是有意的**，然后通过了评审。它能通过，是因为当时
没有任何东西在度量「可达性」——只要没人量，「有意为之」和「忘了做」在代码里长得一模一样。

catalog↔model 的双向交叉检查（`test_every_recipe_field_has_a_catalog_row`）其实就是这个
思路，而且已经存在——但它**只走到 catalog 为止**，从不追问「这一行有没有对应的控件」。

### 能用但没法用（#6 #7）

这两条**一直是可达的**。`extract_type` 有输入框、`stages` 有输入框，能编辑，能保存，能渲染。
它们失败是因为控件类型选错了：一个有限集合被做成了空白文本框，一个五元素的封闭集合被做成
了逗号分隔字符串。

这不是逻辑判断，是**对「用户能被期待知道多少」的判断**。而当年那个判断（DECISIONS #19：
猜测枚举一律给文本框）不但被写下来了，还**被测试锁死了**：

```python
assert all(editor_kind(opt) is EditorKind.TEXT for opt in guessed)
```

于是测试忠实地把错误答案钉在了原地。**测试能保护一个决定，但不能验证这个决定是对的。**

## 三、补上的三层

### 第 1 层 — 可达性审计（机械，拦 #2 #3 #4）

`tests/ui/test_reachability.py`

对每个用户拥有的对象（`Recipe` / `CellEntry`），枚举全部字段，每一个字段要么绑定到一个控件，
要么出现在豁免集合里**并且带一句理由**。

理由是承重结构。豁免集合是一个断言：「有人做过判断」。它可评审，而「没有测试覆盖」永远不可评审。

新增字段忘了做控件 → 审计红。故意不做控件 → 写进豁免集并留下理由 → 下一个人能看见并质疑它。

### 第 2 层 — 旅程测试（端到端，拦 #1）

`tests/ui/test_journeys.py`

**一条规矩：旅程测试只能碰用户能碰的东西，只能断言用户能看见的结果。**
不碰私有属性，不调 controller，不断言信号发出。点按钮、往框里打字，然后读磁盘上的文件。

这条规矩不是风格洁癖，它是唯一能跨过「控件尽到了本分」和「用户拿到了想要的东西」之间那道缝的
断言形式。文件里五条旅程，每条都对应上面表里的一行原话。

### 第 3 层 — 首次使用者走查（人/agent，拦 #6 #7）

前两层拦不住第三类，因为那是判断题不是是非题。能拦住它的只有一件事：
**让一个不预先知道答案的人去用，然后记下他卡在哪。**

`scripts/ui_inventory.py` 是这一层的输入端：离屏跑起 GUI，把每个屏幕上**用户能看到的全部**
导成纯文本——控件名、控件类型、当前值、下拉里有什么、灰字提示是什么——不附带任何源码。

```
python scripts/ui_inventory.py --config-dir <dir>
```

拿这份 dump 交给一个被设定为「第一次用这个工具的 RFIC 验证工程师、没有代码库访问权」的
agent，给它一个具体任务，例如：

> 把这条配方改成只抽电容、125°C、rcworst 角，只跑 quantus 和 jivaro 两个 stage，
> 输出视图叫 `av_ext_125c`。逐步说明你会点哪里、填什么；任何你不确定该填什么的地方，
> 明确说出来。

**它说不确定的每一处，都是一条真缺陷**——因为那份 dump 就是用户全部能拿到的信息。
用今天的 dump 跑一遍，#6 和 #7 会立刻现形：`extract_type` 是空白框而正确拼写只有工具知道，
`stages` 要求你知道分隔符是逗号、拼写是小写。

这一层不进 CI（它需要判断，不是断言）。它属于**每一轮 GUI 改动之后、送去红区之前**跑一次。

## 三点五、第二轮：用自己造的工具再审一遍

第一轮修完后，拿 `scripts/ui_inventory.py` 的 dump 和"可达性 / 假动作"两把尺子回头再扫一遍，
又找出六条。**没有一条是用户报的**，全是这两把尺子量出来的——这就是这三层存在的意义。

| # | 缺陷 | 类别 | 是哪把尺子量出来的 |
|---|---|---|---|
| 9 | **关窗口不问，未保存的改动直接丢**；关窗时若有 run 在跑，还会把活着的 QThread 连同进程一起带走 | 假动作 | 写 `closeEvent` 的旅程测试时发现根本没有 `closeEvent` |
| 10 | **Recipes 屏的普通编辑根本没进 controller**：屏幕显示 "unsaved"，标题栏没有星号，`File → Save` 是灰的，关掉就没了 | 假动作 | 写 #9 的测试时断言 `controller.is_dirty` 才发现是 False |
| 11 | **Cells 屏没有 Save**，唯一写盘途径是菜单 | 假动作/不一致 | 控件清单：Cells 的按钮列表里没有 Save，而 Recipes 有 |
| 12 | **escape hatch 存完补丁后屏幕谎报"已保存"**，Save 按钮变灰，用户无从落盘 | 假动作 | 顺着 #10 查 dirty 状态一致性时撞见 |
| 13 | `netlist_global_gnd_sig` 等两行的灰字提示是 `default `（后面什么都没有）——空框配空提示 | 能用但没法用 | 控件清单肉眼可见 |
| 14 | `temperature_c` 显示 55.0，而**清空 = 用该 corner 建议的温度**这件事全程没人说；这个 fallback 在模型里存在、在 GUI 里不可达 | 能用但没法用 | 控件清单 + 模型对照 |

另外按 #6 的同一条逻辑外推：`output_xy` 这种**有成员表的 list** 也从逗号分隔文本框改成了
复选框 + 一个 "other" 输入框（猜测集合仍要留出打字的余地）；没有成员表的（`netlist_view_list`
等）保持文本框——没有东西可画成复选框时，文本框才是诚实的控件。

**#10 修完后又连带弄坏了 Revert**：既然每次敲键都进队列，"撤销我打的东西"就必须同时丢掉队列里
那一条，否则 Revert 会从它正在撤销的那份暂存里重画屏幕，看起来像没反应。这条是新增的旅程测试
`test_revert_actually_puts_the_old_value_back` 当场抓到的——**旅程测试上线的第一天就还了本**。

**一个副产品的教训**：加上 `closeEvent` 之后整个测试套件挂死了。原因是 pytest-qt 在
`pytest_runtest_teardown` 里程序化 `close()` 每个注册过的控件，撞上确认框，而没有任何 fixture
能跑在那个 hook 前面。正确的解法不是给测试开后门，而是 `closeEvent` 只拦 **spontaneous**
（窗口管理器发来的，也就是用户点了 X）的关闭；`File → Quit` 走 `request_close()` 显式确认后再关。
程序化 `close()` 一律照办——这本来就是 `spontaneous` 这个标志的用途。

## 四、留下的账

- **#8 setup 不可编辑**是已知的、被明确划出本轮范围的（`setup_drawer.py` docstring：
  "Nothing in this round writes configuration from the GUI"）。它不是测试漏掉的，是排期漏掉的。
  可达性审计目前只覆盖 `Recipe` 和 `CellEntry`；`PdkProfile` / `WorkspaceConfig` 没有进来，
  因为今天它们**整体**不可达，写进去只会得到一个全是豁免的集合。
  **Setup 编辑器落地那天，把这两个对象加进第 1 层。**
- DECISIONS #19 已按用户裁定改写（猜测枚举 → 可编辑下拉框）。**其余 22 条决定同样是在
  一台没有 Cadence 的机器上替用户做的**，同样没有经过第 3 层。它们排在 backlog 里。

## 五、第二轮（2026-09-04）：三层没量到的维度，和补上的六把尺子

> 立档于 2026-09-04。起因是用户在真实使用里又撞到四条：LVS 失败后两个「看报告」按钮一个没用；
> recipe 改了忘保存、回来就没了，而且「连 RC / R only 这种常见 extraction type 都没有」；cells 列
> 「高亮即选中」；点过一次 Run 之后再新增、再点 Run 就做不到。全部是用户侧才暴露的，全部不是逻辑错误。

### 5.1 为什么三层全绿

第三节的三层各量一件事：层 1 量「字段 → 是否绑了控件」，层 2 量「用户能碰的 → 用户能看见的」，
层 3 量「控件文本够不够一个不知道答案的人用」。这四条各自落在**三层都没量的维度**上：

| 样本 | 三层为什么没拦住 | 缺的尺子 |
|---|---|---|
| 「Show N discrepancies」点了没反应 | 层 1 只量「字段→控件」，没人量反方向「控件→效果」；层 2 的 16 条旅程没有一条走到失败态；`test_result_card.py` 直接调 `show_lvs_detail()` 只断言「不抛异常」 | **A 控件→可见效果**（含退化态：滚动范围 0、路径不存在、N=0/1/多）；**E 错误路径旅程** |
| 切到另一个 recipe，编辑没了 | 层 2 的旅程是线性的「编→存→读回」；没人枚举「带着未保存编辑时发生的每一种迁移」 | **T 状态迁移 × 未保存编辑矩阵**：行 = 每个可编辑处，列 = 每种会重建/替换/写盘的迁移；每格只能是 保留 / 询问 / 静默丢弃 / 失步 / 静默使用 |
| 「连 R only 都没有」 | 15 个 extract type 早就在模型和 catalog 里；层 1 到「字段绑了控件」为止，不问**控件在默认密度下有没有画出来**；层 3 的 dump 只出一种密度 | **V 渲染可见性**（isVisible 而非 isBound，按密度各 dump 一次） |
| 「想复刻 Quantus 界面」 | 没人拿**厂商工具**当 field list 做可达性审计；catalog 只和自己的模型互查 | **D 领域对齐**：Quantus GUI / 手册 → catalog 行 → 控件 → 模板行 → readback，每一跳都要闭合 |
| 高亮即选中 | 判断题 | **W 走查 + 交互约定核对表**（所有「选择/悬停/焦点驱动的状态变化」和「同义控件成对出现」列出来让人判） |
| 第二次 Run 做不到 | 全套 GUI 测试每个动作只做**一次**；`v2.py` 的「二有其二」只用在数据上 | **N 动作二有其二**：run→改→run、import→import、revert→edit、切项目→切回，第二次必须和第一次不同 |

### 5.2 两条元规则

1. **两个客户端。** 每个屏幕有两个客户端：测试和用户。一条测试若能在用户失败时仍然通过，它量的就是
   错的客户端。本轮每条修复都必须带一条**穿过控件**（`click()` / `trigger()` / `keyClicks`）、
   **断言用户可见结果或磁盘内容**、**对旧代码失败**的测试。`test_project_screen.py` 里有一条
   点了真按钮然后 `assertNotEmitted(save_requested)` 的测试——它证明按钮什么都不做，并把这叫通过。
   这是这条规则要禁止的形状。
2. **仪器本身也要被量。** 走查发现层 3 依赖的 `scripts/ui_inventory.py` 自己有 12 处盲区：它跳过
   `editor is None` 的行（整个 extract 子表单不可见）、把 Cells 的 recipe 下拉列报成只读、根本没有
   Runs 屏的 dumper、把指针行的那句提示丢掉、没有密度开关。**上一轮的走查根本量不到这一轮的东西。**
   规则：每把尺子的机械半必须有自己的自检（dump 必须点到每个屏幕的名字；行数 ≥ 树上 isVisible 的控件数）。

### 5.3 六把尺子各量出了什么

六份只读账本共 147 条，去重后 **135 条**（高 52 / 中 65 / 低 18），全文在 backlog 的
`docs/gui-review-2026-09-04-master-ledger.md`。用户报的四条只是其中 4 条。最重的几类：

- **假动作**：`Import…` 成功提示「34 options set」但 `recipe_imported` 在主窗口没有任何接收者；
  run bar 的「continue on LVS fail」读进 RunRequest 后被丢掉，runner 只看 recipe，卡片还反过来说
  「这个选项是关的」；Cells 的「Import from tasks.yaml」被接到了新建项目向导上。
- **失步**：切 recipe 时表单从过期的 `self._recipes` 重画，controller 里还留着旧编辑，下一次 Save 写的是
  屏幕上已经看不到的值；Project 屏所有字段只在失焦提交，Ctrl+S 写旧值、关窗不问；按过一次
  `File → Revert` 后 Recipes 屏再也不把编辑送进 controller。
- **绿色的假 PASSED**：请求 stage 与 recipe stage 交集为空照样建目录写 `overall=passed`；工具退出 0
  但没写 DSPF 记为 passed，界面标成「Not on this host」；runner 从不写失败判决，签名表是空的。
- **崩溃**：`MainWindow._open_path` 没有 try/except，路径不存在或服务器上没有打开器时整个程序 abort；
  同一次点击还会打开两次（RunsScreen 开一次、MainWindow 再开一次）。
- **领域缺口**：`rlck_*` 静默出不含电感的网表（模板不发 `-ind_component`）；`*_to_substrate` 因
  `-substrate_nets_file` 不可达而什么都不提；`global_nets` 整条命令没建模；手写的单行
  `extract -selection all -type ...` 导入时被静默丢弃。
- **能用但没法用**：整张 87 行表单的下拉框在 `currentTextChanged` 上提交、仓库里没有任何 `wheelEvent`
  拦截，鼠标滚过表单会静默改掉光标下的下拉框，包括 extract type；`default X` 和 `unset — X` 是两种
  契约共用一个词。

### 5.4 进仓库的东西（机械半）

| 尺子 | 文件 | 形状 |
|---|---|---|
| A 控件→效果 | `tests/ui/test_affordances.py` | 每个 QAbstractButton / QAction / 可点标签 要么有 ≥1 个接收者，要么在带理由的豁免集里；ResultCard / RunsScreen 的动作按钮在每种退化态下穿过控件点击必须产生可观察变化，且先断言 `scrollbar.maximum() > 0`，0 滚动范围永远不能算证据 |
| T 迁移矩阵 | `tests/ui/test_transitions.py` | 矩阵参数化；对 HEAD 失败的格用 `xfail(strict=True, reason="M-nn")` 钉住，修复必须翻掉自己的 xfail |
| E 错误路径 | `tests/ui/test_failure_journeys.py` | 15 条失败旅程：磁盘上造一个失败态的 run 目录，MainWindow + RunsScreen 接在一起，点每个按钮，断言打开了哪个文件 |
| N 二有其二 | `tests/support/v2.py` + `tests/ui/test_second_run.py` | 「动作做两遍、第二遍不同」的 helper 和它的第一批用例 |
| V/W 仪器 | `scripts/ui_inventory.py` + `tests/test_ui_inventory.py` | 修 12 处盲区，加密度开关、enabled/visible/receivers/scroll-range 列、Runs 屏 dumper，以及仪器自检 |
| D 领域对齐 | `tests/ui/test_reachability.py` 扩展 | `currently: absent` 的行必须画成禁用的「未接线」行或进带理由的豁免集 |

判断半（W 走查、D 对手册的映射）不进 CI，每轮 GUI 改动之后、送红区之前跑一次。
`extUser.pdf` 第 3 章是 Quantus GUI 的逐字段参考（每节末尾有 `Quantus: <cmd> -<opt>` / `RSF: ?<var>` 对），
不用截图也能做映射——探针命令在主账本 M-128。

### 5.5 分波

按「能不能立刻动」而不是按严重度：波 0 仪器（15）→ 波 1 干净文件（63）并行修，各簇独立 worktree，
合并到 `review/rulers-2026-09-04`；波 2（43）撞另一个会话正在改的 `main_window.py` /
`cells_screen.py` / `run_bar.py`，等它提交；波 3（14）要 owner 拍板（电感器件名、衬底网表、
电源地寄生算不算、拒绝还是警告）或红区探针。用户报的四条原始 bug 里，三条在波 2。

**波 3 的拍板 2026-09-04 回来了，而且不是逐条回来的。** owner 的原话是「你列给我让我
决定的这些旋钮，说实话我一个都不懂，不懂的东西我大概率永远不会用」——这条比任何一条
单独的答案都上位：**不理解 = 不提供**。它把尺子 D 的参照物从厂商手册换成了 owner 在
Quantus GUI 里真正会动的那些，八条波 3 的账因此不是「实现」而是「记录一个决定」结掉的。
展开在 5.6。

### 5.6 尺子 D 换了参照物：「不理解 = 不提供」（2026-09-04 裁定）

> 波 3 的 14 条里有 6 条是「等 owner 拍板」。把这 6 条列成一张选择题交上去，回来的
> 不是答案，是一条更上位的规则：
>
> **「你列给我让我决定的这些旋钮 —— 说实话我一个都不懂，不懂的东西我大概率永远不会用。」**

这句话推翻了本项目一直默认的那把尺子。**领域对齐（尺子 D）原来的参照物是厂商手册**：
手册里有的选项，catalog 里就该有一行，表单上就该有一个控件，缺了就是缺口。裁定之后
参照物变成了**owner 在 Quantus GUI 里真正会去动的那些**。规则一条：

> **不理解 = 不提供。** owner 用不到的厂商选项不画进表单，走工具默认值，
> **catalog 行上写清楚是哪个默认值、为什么不画** —— 豁免是一条被记录的决定，
> 而「控件不存在」不是。
> 反过来同样成立：**表单提供的东西，绝不允许悄悄产出一份不完整或非法的 deck。**

两条推论，本轮各落地一次：

- `extract -type` 十五个成员里**只画六个**（`none` / `r_only` / `c_only_decoupled` /
  `c_only_coupled` / `rc_decoupled` / `rc_coupled`）。另外九个模板补不齐它们的契约 ——
  六个 `rlc*`/`rlck*` 缺 `-ind_component`、`substrate_only` 和三个 `*_to_substrate`
  缺 `substrate_nets_file` —— 选中就是**跑得通、报成功、网表里没有电感 / 没有衬底网络**。
  新增 catalog 列 `choices_not_offered` + `not_offered_reason` 把「工具没有这个值」和
  「工具有，我们不提供」分成两件事：`choices` 保持完整，否则 readback 和导入就再也
  叫不出同事那份 deck 里写的是什么。model 拒绝这九个并在报错里点名缺的是哪个契约，
  导入则降级成 catalog 默认值**并留下一行报告**说明降的是哪一条、为什么、降成了什么。
- **假动作要么实现、要么撤掉。** `fail_on_unparsable_lvs_report` 是唯一一个
  `currently: absent` 却还带着 `context_path`、因而被画成活控件的行 —— 勾与不勾毫无区别，
  因为判决发生在 `CalibreTool.parse_result`，那里根本拿不到 recipe。实现它要把 policy
  穿过整个 Tool 协议或挪到 runner 里判，都不小；所以撤掉 `context_path`，控件消失，
  行和字段留下，把「实现它要付什么代价」写在原地。

两条配套的规矩，都落在 `tests/ui/test_reachability.py` 的 `CATALOG_UNREACHABLE` 上：

- **豁免理由必须自报是哪一种决定**：`owner ruled 2026-09-04` / `BLOCKED ON A PROBE` /
  `NO LANDING SITE` / `RETIRED`。改之前十五条理由是「catalog 还不知道」「手册那一轮
  还没回来」这种**等待**穿着决定的衣服——而 C5 早已把手册答案写到那些行上了。等待和
  遗漏一年之后长得一模一样，所以理由必须说清自己在等谁。
- **被裁掉的行必须交代工具默认是什么**（field solver 默认关、via cap 默认 true、
  fringing cap 自 11.1 起默认 true、`min_res_centering` 默认 false、subnode 默认写出、
  没有 `global_nets` 则 `-selection all` 真的包含 VDD/VSS）。手册没写默认值的行，
  理由里必须**明说手册没写**，不许编——这和 `range_verified` 那一列是同一条规矩。

还有一条不是「不提供」而是「提供但说清楚」：`metal_fill -type virtual` 在 Calibre LVS
输入下是空转（手册原话如此），但它在 owner 跑了多年的每一份老 deck 里都有。删掉只省
一行、代价是把那些 deck 全 diff 一遍，所以**留着**，事实写在 catalog 行和模板的一行
Jinja 注释里，免得下一个人当成整洁化再删一次。至于「这颗 PDK 的 dummy fill 到底该怎么
建模」——那是另一个问题，`question:` 保持打开。
