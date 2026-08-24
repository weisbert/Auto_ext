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

### 已经发现的疑似 bug（请优先确认）

- [ ] **Jivaro 缩减目标视图名对不上**：`templates/jivaro/default.xml.j2:6` 写死
      `viewsToReduce="av_extracted"`，但同一份模板第 2 行的 `inputView` 用的是
      `[[out_file]]`（配置里是 `av_ext`），Quantus 建的视图也是 `-view_name "[[out_file]]"`。
      三处里两处跟 `out_file` 走，唯独这一处是另一个字面量。
      **Jivaro 很可能在找一个不存在的视图。** 这两个是不是应该永远一致？（默认：应该一致）

- [ ] **耦合电容绝对阈值的单位/量级不对**：manifest 写 `default: 0.01, unit: F`，
      也就是 10 毫法的耦合电容阈值 —— 物理上不成立。你平时填的数值和单位是什么？（默认 0.01 F）

### 提取条件

- [ ] 提取类型：现在写死"带耦合的 RC"。你们平时还会用哪几档（只提电容 / 只提电阻）？
      请给你们 QRC 认的确切写法。（默认 rc_coupled）
- [ ] 输出形式：一次跑是否需要**同时**出 extracted view 和 DSPF？
      （今天的代码结构里只能二选一，因为 quantus 只有一个模板槽。）另外要不要 SPEF？
      （默认 只出 extracted view）
- [ ] metal fill：DSPF 流程里写着按虚拟填充处理，extracted view 流程里整段没有。
      这两条流程对 metal fill 的处理本来就该不同吗？（默认 DSPF=virtual，extracted view=不写）
- [ ] 长走线切段与过孔阵列：切段长度写 infinite、过孔阵列间距和上限都写 auto。
      这三项除了 auto/infinite 还能填数值吗？射频长传输线你们会调到多少？（默认 全 auto/infinite）
- [ ] Quantus 多核：现在提取是单核。你们的 QRC 支持多核吗？命令文件里要写哪一段？（默认 单核）

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
- [ ] 寄生器件映射：Quantus 把寄生电阻/电容映射成 `presistor` / `pcapacitor`，
      Jivaro 那边另外还认电感和互感（`pinductor` / `pmind`）。这四个名字在你们 PDK 里是这样吗？
      两边必须一致吗？（默认 analogLib 的这四个）
- [ ] 寄生电阻模型语句：现在填的是 `comment`（既不是开也不是关）。这在你们的后仿真里
      意味着什么？三档分别什么时候用？（默认 comment）
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

**先在黄区切到对的分支再打包** —— 全部改动都在 `refactor/recipe-and-run` 上，
`main` 一个字没动：

```powershell
git checkout refactor/recipe-and-run
powershell -ExecutionPolicy Bypass -File deploy\pack.ps1
```

（不想切分支就 `pack.ps1 -Ref refactor/recipe-and-run`，效果一样。包名里的短哈希
会告诉你打的到底是哪一个 commit。）

红区三十秒版：

```tcsh
cd <install>            # 把 .tar.gz + .sha256 上传到这里之后
bash deploy.sh
bash deploy/doctor.sh --test
```

判据是 `OK  deployed.` 和 `OK  self-test passed`。**self-test 不绿就别往下走** ——
后面出的任何问题都分不清是环境的锅还是代码的锅。

## 1. 先体检 —— 它会告诉你能不能跑

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

左侧三个界面：Cells（主画面）/ Recipes（配方）/ Runs（历史）。
Setup 不是 tab —— 是标题栏那个 ✓/✗ 徽章，点开是抽屉。

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
