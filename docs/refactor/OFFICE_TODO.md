# 办公室待办 —— 只有在真 Cadence 环境里才能做的事

这份清单里的每一条，我在 Windows 开发机上都无法回答。凡是被它卡住的功能，
我都做成了**可改的数据表**，你带回答案之后改数据、不改代码。

> **代码怎么弄到红区去？** 看 [`REDZONE_DEPLOY.md`](REDZONE_DEPLOY.md) ——
> 黄区打包、上传、`bash deploy.sh`、`bash deploy/doctor.sh --test`，每步带预期输出。
> 红区**没有** git，任何让你在服务器上 `git pull` 的说明都是过时的。

---

## 第一优先：三样硬阻断 —— 已解决 2 样，**剩 1 样**

2026-08-24 办公室一轮把第 2、3 条答死了，顺带在扫描器里挖出一个真 bug（见第 2 条）。
现在只剩日志样本，而它必须等一次真跑。

### 1. 真实日志样本（阻断：失败分类）**← 唯一还开着的硬阻断**

仓库里一份真实 EDA 日志都没有（`find . -name "*.log"` 零命中），所以我**没有编造**
任何日志特征串。现在的分类器只用能确定的判据（退出码 127、LVS banner、有无报告），
其余一律返回 `unknown`。

请抓这几份，越原始越好（整个 `.log` 文件，别截断）：

- [ ] 一次 **LVS 不通过** 的 calibre stdout 日志
- [ ] 一次 **license 抢不到** 的输出（任何工具都行，这是最需要特征串的一类）
- [ ] 一次 **Quantus 正常跑完** 的输出（做正常基线）
- [ ] 一次 **工具崩溃 / 段错误 / 磁盘满** 之类的异常输出

拿到之后填进 `auto_ext/core/failure_signatures.yaml`，文件头有格式说明。

### 2. ~~两条 ls~~ ✅ 已解决 2026-08-24

- [x] `ls $VERIFY_ROOT/runset/Calibre_QRC/LVS/*/*/`
- [x] `ls <你的 qrc_deck_dir>`

**结果一：`widio` 是真的。** 真实 deck 目录里 `.wodio.qcilvs` 和 `.widio.qcilvs`
两个文件都在，没有第三种后缀。`choices_confidence` 从 `likely` 提到 `certain`。
顺带发现每个变体旁边还有一个 `.lvs` 兄弟文件（外加一个无后缀的 `<basename>.lvs`
和一个 `empty.cdl`）—— 那是另一种 deck 口味，`filename_pattern` 继续钉死 `.qcilvs`，
不去 glob `.lvs*`。

**结果二（更重要）：R5 规则被证伪，是个真 bug。**
扫描器原来假设 QRC deck 和 LVS deck 共用同一个 `<pdk_subdir>`，
所以把 glob 钉成 `QRC/*/<LVS的basename>/QCI_deck`。真实树上这两个子目录名
**是两个互相独立的 deck 发布号**，除了工艺前缀之外没有任何公共子串 ——
于是那个 glob 在你的服务器上会匹配到**零个**结果，扫描器会对着一个明明就在那儿的
目录说"找不到"。已修：两段都放成通配，窄匹配保留为"先试一次"的快速消歧路径
（名字恰好对得上的 PDK 仍然免问自动定）。三条回归测试钉住了你这棵树的形状。

**结果三：并排两个 QRC deck 发布是常态，不是错误。**
真实树上就有一个正式版和一个 `_offline` 版。所以"多个候选 → 交还给人选"
这条行为是对的，而且是**常见路径**而非异常路径。

版本号不同步这条也当场坐实了（LVS 和 QRC 各自带自己的版本段），
R4 从 `unverified` 升为 `observed`。

### 3. ~~corner 的真实取值~~ ✅ 已解决 2026-08-24

- [x] 这颗工艺认的 corner（来源：**Quantus GUI 的 RuleSet 列表**）

九个，全在这儿了：

```
TYPICAL
CBEST   CBEST_T    CWORST   CWORST_T
RCBEST  RCBEST_T   RCWORST  RCWORST_T
```

**旧一代命名赢了**：`CBEST_CCBEST` / `RCWORST_CCWORST` 那一套在这颗工艺上
**整套不存在**。当初把两代混在一个候选表里发出去，会给你一个一半是无效项的下拉框
—— 这就是它被列为硬阻断的原因。现在 profile 的 corner 表和 catalog 的
`technology_corner` 都是这九个，`certain`。

**另一个发现：找错地方了。** `grep -i corner $SETUP_ROOT/assura_tech.lib`
在真 PDK 上**一条都不返回** —— corner 列表根本不在 tech lib 里，在 Quantus 的
RuleSet 里，而那是个 GUI 列表，扫描器读不到。R6 规则降级为"尽力而为"，
`check-env` 和扫描器的修复提示都已改成指向 RuleSet，不再让你去跑一条注定空手而归的 grep。
（那条 grep 的字面串此前还被一个测试断言着 —— 测试已反转成断言它*不再出现*。）

---

## 第二优先：25 个勾选项

这些我都给了默认值、代码能跑，但默认值是**推断**不是事实。
一行一条，勾掉或改掉即可。

> **2026-08-25：查了红区本地的 `extUser.pdf`，这一节里有一批不用再问你了。**
> 手册是 `<cadence-ext-install>/doc/extUser/extUser.pdf`（761 页）—— 真实路径别写进这个
> 公开仓库，导出成 `$EXTDOC_PDF` 给 `scripts/extdoc_probe.py` 用即可。
> 凡是"这个选项的合法取值是什么 / Cadence 的默认值是多少"这类问题，**手册能答，不必占用你的时间**。
> 已答完的条目就地标了 ✅ 并附页码；剩下开着的，都是**只有你或只有真跑一次才能答**的。
> 全部结论进了 `catalog-quantus.json`（新增 `doc_ref` / `cadence_default` / `applies_to` /
> `schema_findings` / `template_defects` 五处），完整推导过程在
> `private/pdf_answers/FINDINGS.md`（gitignored —— 里面有手册正文，本仓库是 public）。
>
> 顺带的产物：`extdoc_probe.py`（纯标准库，py2.7/py3 都能跑）能在红区把任意 20MB+ PDF
> 拆成 ≤48KB 的可外带小块。以后再撞上"手册里到底怎么写的"，直接用它，不用再靠猜。

### 手册核实之后**新开**的问题（2026-08-25）

这五条是查手册时才浮现出来的，之前的清单里一条都没有：

- [ ] **`global_nets` 整段缺失 —— 电源地网到底提不提？**（p.469-470）
      `extract -selection all` 的语义是"**除电源地网之外**的全部网"，而电源地网正是由
      `global_nets` 命令定义的 —— **我们的模板里没有这一段**。
      官方写法：`global_nets -nets "vdd" "vss" -import_from_lvs true -force_global_nets true`。
      对射频来说"电源地上的寄生算不算"不是小事。你们原来的手工流程里有这一段吗？

- [ ] **`-use_field_solver` 我们一个字都没写**（p.388, p.95/99/108）
      手册里几乎**每个**官方 `extract` 示例都带着 `-use_field_solver default_accuracy`，
      三档是 `none | default_accuracy | high_accuracy`。我们两份模板都没有，走的是未知默认值。
      配套的还有 `-field_solver_type [ deterministic | probabilistic ]`。
      **这两条直接关系到射频精度。** 你在 Quantus GUI 里这两项是怎么设的？

- [ ] **`ext.cmd` 里的 `-device_finger_delimiter` 可能是多余的**（缺陷 D4，p.507-508）
      p.507-508 的 `output_db -type extracted_view` 专表里**没有**这个选项，而我们写了。
      也可能是手册表遗漏。**跑一次就知道** —— 看 Quantus 报不报未知选项。
      （`dspf.cmd` 那一处是合法的，dspf 表里明确列着。）

- [ ] **被屏蔽 cell 的 `gray` / `white` 是一道选择题**（p.402, p.419）
      `-parasitic_blocking_device_cells_type` 的**新默认是 `white`**
      （顶层网与被屏蔽 cell 内部网之间的耦合电容照常提取）；
      `gray` = 把被屏蔽 cell 接地、其 R/C 完全不提取，**那是老版本的默认行为** ——
      早期只要写了 `-parasitic_blocking_device_cells_file` 就等于 `gray`。
      我们只写了 `_file` 没写 `_type`，所以拿到的是 `white`。
      **这是一次静默的默认值变更**：老流程搬过来，行为已经变了。你要哪一种？

- [ ] **`extract` 应该支持写多段**（schema 发现 S1，p.389 / p.95）
      手册明写 `extract` 可以出现多次、结果累积、**后面的覆盖前面的**，
      并给了一个有名字的用法模型 **Full Chip, Selected Nets**：
      全片只提电容，电阻只在关心的那组网上提 —— 正是射频最常用的降档策略。
      我们把 `extract_selection` / `extract_type` 建模成两个标量，**表达不了这个**。
      改成有序列表是明确的，但**要不要现在就改，取决于你实际用不用这种分组提取**。

### 已经发现的疑似 bug（请优先确认）

- [ ] **Jivaro 缩减目标视图名对不上**：`templates/jivaro/default.xml.j2:6` 写死
      `viewsToReduce="av_extracted"`，但同一份模板第 2 行的 `inputView` 用的是
      `[[out_file]]`（配置里是 `av_ext`），Quantus 建的视图也是 `-view_name "[[out_file]]"`。
      三处里两处跟 `out_file` 走，唯独这一处是另一个字面量。
      **Jivaro 很可能在找一个不存在的视图。** 这两个是不是应该永远一致？（默认：应该一致）

- [ ] **耦合电容绝对阈值的单位/量级不对**：manifest 写 `default: 0.01, unit: F`，
      也就是 10 毫法的耦合电容阈值 —— 物理上不成立。你平时填的数值和单位是什么？（默认 0.01 F）
      > **2026-08-25 查手册后降级：不再怀疑数值，只怀疑那个 `unit: F` 标注。**
      > 手册通篇**没有写这个选项的单位**；RSF 映射是 `?minC`（minimum C）。
      > 而 **p.584 的 Cadence 官方示例本身就写 `-coupling_cap_threshold_absolute 0.01`** ——
      > 所以 0.01 是照抄官方示例，不是谁随手填的。
      > 顺带：同段的 `-cap_filtering_mode [ absolute_and_relative | absolute_or_relative ]`
      > 限 DEF/OA，我们这条 LVS 支路用不了 —— 也就是说两个阈值怎么组合，在我们的流程里没有开关。

### 提取条件

- [x] ~~提取类型~~ ✅ **2026-08-25 查手册答完**（extUser.pdf p.388）。
      `extract -type` 共 15 个值，我们这条 LVS(QCI) 支路**全都能用**：
      `none` `substrate_only` `r_only` `c_only_decoupled` `c_only_coupled`
      `c_only_decoupled_to_substrate` `rc_decoupled` `rc_coupled` `rc_decoupled_to_substrate`
      `rlc_{decoupled,coupled,decoupled_to_substrate}` `rlck_{decoupled,coupled,decoupled_to_substrate}`。
      **只提电阻 = `r_only`；只提电容 = `c_only_coupled`（带耦合）/ `c_only_decoupled`（不带）。**
      catalog 原来写的 `c_only` / `rcc` / `rlck` 三个拼写**在手册里不存在**，已改。
      → 还剩一问留给你：**你们平时实际用哪几档？**（不影响代码，只影响 Recipe 预设）
- [ ] 输出形式：一次跑是否需要**同时**出 extracted view 和 DSPF？
      （今天的代码结构里只能二选一，因为 quantus 只有一个模板槽。）另外要不要 SPEF？
      （默认 只出 extracted view）
- [ ] metal fill：DSPF 流程里写着按虚拟填充处理，extracted view 流程里整段没有。
      这两条流程对 metal fill 的处理本来就该不同吗？（默认 DSPF=virtual，extracted view=不写）
- [x] ~~长走线切段与过孔阵列：能不能填数值~~ ✅ **2026-08-25 查手册答完**（p.402）。
      **三项都能填数值。** `-max_fracture_length [ <value> | infinite ]`，
      `-array_vias_spacing [ <value> | "auto" ]`，`-max_via_array_size [ <value> | "auto" ]`。
      Cadence 对 `-max_fracture_length` 的文档默认值是：cell-level 25µm（先进节点 ≤20nm）/
      100µm（成熟节点 >20nm）；**transistor-level 就是 `infinite`** —— 我们的写法与默认一致。
      切段单位 `-max_fracture_length_unit` 只有 **`microns | squares`** 两个值
      （catalog 原来写的 MILS / MILLIMETERS / DBU 都不存在，已改）。
      → 还剩一问：**射频长传输线你们会调到多少？**（是数值选择，不是能不能填的问题）

- [x] ~~Quantus 多核~~ ✅ **2026-08-25 查手册答完**（p.382-385, p.19），
      而且**推翻了这条问题的前提**。
      段名和选项：`distributed_processing -multi_cpu <number>`
      （同段还有 `-lsf_number`（默认 64）/ `-lsf_command` / `-multi_machine <file>`，**互相排斥**）。
      **🔴 "现在提取是单核"是错的。** 原文：*the default for Quantus is to run on two CPUs in
      machines equipped with multiple CPUs*。我们模板里**根本没有 `distributed_processing` 段**，
      所以现在实际跑在 **2 核**上；catalog 里的 `cpu_count: 1` 是一个**从未生效过的值**。
      要真单核必须显式写 `-multi_cpu 1`。
      **License 是硬天花板**：需要的 license 数 = `ceil(N/2)`（4 CPU → 2 个，5 台机 → 3 个），
      与 p.19 的"XL license 数 × 2 = 允许 CPU 数"是同一约束的两种说法。
      EXT15.1 起 transistor-level 流程支持 `-multi_cpu` / `-lsf_number` / `-multi_machine`；
      `-sge_*` 和 `-drm_*` 在 transistor-level **不支持**。
      → 还剩一问：**你们站点有几个 XL license 可用？**（这个数决定 `-multi_cpu` 的上限）

### LVS

- [x] ~~LVS deck 后缀~~ ✅ 2026-08-24：没有别的，就这两种。见第一优先第 2 条。
      （目录里那些 `.lvs` 文件是另一种 deck 口味，不是第三个 variant。）
- [ ] 按名连接：现在只有"全部按名连"和"完全不连"两档。有没有过只对电源地按名连的需求？
      （默认 不按名连接）
- [ ] LVS 器件过滤：过滤开关是关的，但过滤项写着 `AG RC RE RG`。这四个字母各代表什么？
      要不要打开？（默认 关闭）
- [ ] LVS 报告内容 `lvsReportOptions`：现在是 `S`。还需要多打印什么段落吗？（默认 S）
- [ ] 电源网出错就中止：现在不中止。打开之后配合那两张几十项的电源/地网名单，
      会不会一堆块直接过不去？（默认 不中止）
- [ ] Calibre 并行：Turbo 核数 2、多线程开、Hyperscaling 开。你们站点有 Hyperscaling
      license 吗？license 等待时间写的 10，单位是分钟吗？（默认 2 / 都开 / 10）
- [ ] ERC：runset 里声明了 ERC 结果文件（`*lvsERCDatabase` / `*lvsERCSummaryFile`），
      但**从来没人读**。要不要把 ERC 结果也收进运行记录并参与判定？（默认 不判定）

### 网表 / si

- [ ] 小电阻短路门限 `shortRES`：现在 2000.0。是低于这个值被短路还是高于？单位欧姆吗？
      要不要和 Calibre deck 那边保持一致？（默认 2000.0）
- [ ] 器件属性刻度 `checkScale`：现在 `meter`。你们的 Calibre deck 期望 meter 还是 micron？
      （默认 meter）
- [ ] 全局电源/地网名：si.env 里这两项现在是空的，靠原理图自带的全局网兜底。
      你们的原理图总会声明吗？要不要在单元表里加一列"电源网"和已有的"地网"配对？（默认 空）
- [ ] 器件 CDL 前置文件：si.env 里填的是一个环境变量名的字面串。跑一次 si 后打开
      生成的 `.src.net` 确认前面确实带上了器件 subckt 定义。有没有需要前置**多份** CDL 的情况？
      （默认 单份）

### 版图导出 / 其它

- [x] ~~版图导出文件名 / 三种落点~~ ✅ **2026-08-24 已恢复，做成了两个文件。**
      裁决：跑 LVS 用的那个文件**不动**，照旧 `<cell>.calibre.db` 落在运行目录；
      想给别的软件一份 GDS，就**再导一个**。
      入口：`auto-ext export-gds --to '<路径>'`，或 GUI 里 Cells 右键 `Export GDS…`。
      落点、文件名、后缀全随你（内容是 GDSII，Calibre 按 magic bytes 认格式）。
      两条护栏：带导出的 dispatch 的 stage 集合必须恰好是 `{strmout}`；多单元导出
      路径里必须有 `{cell}`。细节见 `OLD_VS_NEW_FLOW.md` 第三节。
- [ ] 版图导出的其它选项：层次深度、大小写处理、点号转换现在一个都没传，用的是
      strmout 默认。你原来的手工流程有没有指定过？（默认 全默认）
- [x] ~~`Design` 这个基名~~ ✅ **已解决 2026-08-24，而且答案比"默认值对"更强。**
      真 `query_cmd` 把它全部十三个输出都写成 `query_output/Design.<ext>`，
      并且文件头**白纸黑字写着契约**：QRC 的 `input_db -run_name` 必须等于前缀
      (`Design`)，`-directory_name` 必须等于目录 (`query_output`)。
      所以它不只是"PDK 定的"，而是**必须和另外两个字段保持锁步**，否则 Quantus
      什么都读不到 —— 这正是它 owner 是 `fixed` 而不是可调项的理由。
      两个 QRC deck 发布里的 `query_cmd` 逐字节相同，不存在版本间分歧。
- [x] ~~寄生器件映射：这四个名字对不对~~ ✅ **2026-08-25 查手册答完**（p.170, p.507）。
      原文写死：*The default parasitic capacitor component and property name are **pcapacitor**
      and **c***；电阻那句同理是 **presistor** 和 **r**。**我们填的就是 Cadence 默认值。**
      Quantus 侧也确实有 `-ind_component` / `-mutual_ind_component` 两个选项，
      与 Jivaro 的 `pinductor` / `pmind` 名字体系一致 —— 之前记的"Jivaro 多出两项"这个不对称，
      真正原因只是**我们没开 RLCK 提取**，不是两边模型不一致。
      语法是 `<string +>`：器件名后面还能跟可选的 view 名和 library 名（默认 view 是 `symbol`）。
      → 还剩一问：**你们 PDK 的 analogLib 里这四个 cell 确实都在吗？**（是查库，不是查手册）

- [ ] 寄生电阻模型语句：现在填的是 `comment`（既不是开也不是关）。这在你们的后仿真里
      意味着什么？三档分别什么时候用？（默认 comment）
      > **2026-08-25 部分答**（p.501）：三档 `[ true | false | comment ]` 确认存在，
      > 而且 **`include_cap_model` / `include_parasitic_cap_model` / `include_res_model`
      > 这三个也同样是三档，不是 bool** —— 模板现在把它们渲染成
      > `[[ "true" if x else "false" ]]`，**`comment` 这一档永远取不到**（已记为缺陷 D2）。
      > `comment` 的确切语义手册正文没取到（在 output_db 的 Options 段，p.509+），
      > 这一问仍然开着。
- [ ] Jivaro 缩减判据 `criterion`：现在写死 `standard`。Jivaro 界面上还有别的选项吗？
      （默认 standard）
- [ ] `strmout` 的 argv 形状**从未在真二进制上验证过**（backlog 老条目）。
      跑一次真的确认一下。

---

## 验完之后

1. 日志样本 → 填 `auto_ext/core/failure_signatures.yaml`
2. corner / variant → 填 PDK Profile 的对应表
3. 勾选清单 → 对照 `docs/refactor/catalog-*.json` 改默认值

**都是改数据，不需要改代码。** 这是这次重构刻意做的隔离。

---

# 现在怎么跑（老的 OFFICE_QUICKSTART 已作废）

`docs/OFFICE_QUICKSTART.md` 等四份文档描述的是重构前的架构，已加作废横幅。
下面是当前的命令。

## 0. 装上去

代码从黄区打包上传，不是 `git pull` —— 完整步骤（每步带预期输出）在
[`REDZONE_DEPLOY.md`](REDZONE_DEPLOY.md)。

**2026-08-24 起不用切分支了** —— `refactor/recipe-and-run` 已 fast-forward 并入
`main`（纯快进，无 merge commit），两者都在 `e66916f`。黄区直接：

```powershell
git pull
powershell -ExecutionPolicy Bypass -File deploy\pack.ps1
```

分支 ref 留着当历史标记，但**不要再从它打包** —— 少一个"必须记得切分支"的步骤，
正是并进 main 的理由：`pack.ps1` 默认打 `HEAD`，切错分支会打出一个看起来完全正常
（有 sha256、有 commit info、装得上）却少了半年功能的包。

包名里的短哈希会告诉你打的到底是哪一个 commit，它必须和红区 `deploy.sh` 打印的
`installed version` 一致。

红区三十秒版：

```tcsh
cd <install>            # 把 .tar.gz + .sha256 上传到这里之后
bash deploy.sh
bash deploy/doctor.sh --test
```

判据是 `OK  deployed.` 和 `OK  self-test passed`。**self-test 不绿就别往下走** ——
后面出的任何问题都分不清是环境的锅还是代码的锅。

## 1. 先体检 —— 它会告诉你能不能跑

> 下面每条都在**安装目录里**敲（上面那个 `cd <install>` 之后）。`config` 这类相对路径按
> 你站的位置解析 —— `run.sh` 会在 chdir 到 workarea 之前替你绝对化，理由见 README §Launch。
> 路径没落到你想的地方时，`AUTO_EXT_ARGV_DEBUG=1` 加在命令前面能看到它到底传了什么。

```bash
./run.sh check-env --config-dir config
```

每条不通过的会写清楚**怎么修**。这一步替代了以前肉眼看那张环境变量表。

## 2. 一次 dry-run

```bash
./run.sh run --config-dir config --recipe rc-typical-55c --profile default --dry-run
```

体检有硬失败会拒绝启动。确实想先跑起来看渲染结果，加 `--no-health-check`。

## 3. 看结果

```bash
./run.sh runs list
./run.sh runs show <run_id>
```

每次运行都在 `runs/<UTC时间戳>_<slug>/` 下，**永不覆盖**：
`run.json`（含配方快照）、`rendered/`（这次真正喂给工具的文件）、`logs/`、`results/`。

## 4. 抓失败日志样本（本文件第一优先第 1 条要的就是它）

失败后日志就在 `runs/<run_id>/logs/<stage>.log`，开头带 argv 和 cwd，自描述。
**带出服务器前记得擦掉工号和 cell 名**，填进
`auto_ext/core/failure_signatures.yaml`（文件头有格式说明）。

## 5. GUI

```bash
./run.sh gui --config-dir config
```

左侧**四**个界面：Cells（主画面）/ Recipes（配方）/ Runs（历史）/ Project（这个项目的配置）。
Setup 不是 tab —— 是标题栏那个 ✓/✗ 徽章，点开是抽屉；抽屉里一条检查指向某个字段时，
"Edit the field" 会直接跳到 Project 屏的那一行。

## 5.5 `check-env` 说某个环境变量没有 —— 而这台机器就是给不出来

这正是 `check-env` 结构上答不了的那一半：它只会问 shell，shell 没有的它没有第二个地方可查。
但**这个项目自己产出过的文件里，那个值是以解析好的形式写着的**。拿一份来喂它：

```bash
./run.sh profile read-env <runset 或 .cmd 或 si.env> [更多文件...]
```

它会逐条列出：变量名、从文件里读出的值、**现在生效的是什么**（已 pin 的 > shell 的 > 没有）、
以及是从哪个文件的哪个表达式反推出来的。看着没问题再加 `--write` 写进
profile 的 `env_overrides`。

- 只报告不写盘；`--write` 才写。
- 退出码：有东西会变 = 1，什么都不会变 = 0。
- **两份文件对同一个变量给出不同答案时，两个都列出来、`--write` 跳过它。**
  这是要你自己裁决的事，错的那个答案和对的那个一样像真的。

GUI 里同一件事在 Project 屏的 "Read environment from a file..."。

## 6. 想把自己手上的 .cmd / .qci 变成配方

```bash
./run.sh recipe import <你的文件> --as my-recipe        # 先看报告，不写盘
./run.sh recipe import <你的文件> --as my-recipe --write
```

catalog 认识的值进配方字段，不认识的差异存成手工修改（patch）。
报告第三段会告诉你哪些值没进配方、为什么。

## 7. 老配置怎么办

`examples/legacy/` 里留着重构前的 `project.yaml` + `tasks.yaml`。
你在办公室那份如果还是老格式，加载会失败并提示跑：

```bash
./run.sh migrate --config-dir <老config目录> --out-root <目标>          # 先看报告
./run.sh migrate --config-dir <老config目录> --out-root <目标> --write
```

老文件原样保留，迁移是新写一套，随时能退回去。
