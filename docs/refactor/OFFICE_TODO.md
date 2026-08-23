# 办公室待办 —— 只有在真 Cadence 环境里才能做的事

这份清单里的每一条，我在 Windows 开发机上都无法回答。凡是被它卡住的功能，
我都做成了**可改的数据表**，你带回答案之后改数据、不改代码。

---

## 第一优先：三样硬阻断

### 1. 真实日志样本（阻断：失败分类）

仓库里一份真实 EDA 日志都没有（`find . -name "*.log"` 零命中），所以我**没有编造**
任何日志特征串。现在的分类器只用能确定的判据（退出码 127、LVS banner、有无报告），
其余一律返回 `unknown`。

请抓这几份，越原始越好（整个 `.log` 文件，别截断）：

- [ ] 一次 **LVS 不通过** 的 calibre stdout 日志
- [ ] 一次 **license 抢不到** 的输出（任何工具都行，这是最需要特征串的一类）
- [ ] 一次 **Quantus 正常跑完** 的输出（做正常基线）
- [ ] 一次 **工具崩溃 / 段错误 / 磁盘满** 之类的异常输出

拿到之后填进 `auto_ext/core/failure_signatures.yaml`，文件头有格式说明。

### 2. 两条 ls（阻断：LVS variant 和 deck 自动发现）

现在我手上只有 `docs/calibre_raw.txt` 里的一个样本
（`.../LVS/Ver_LVS_A/CFXXX/CFXXX.wodio.qcilvs`），
`widio` 这个变体**只存在于手写的 manifest 里，真实文件里从没出现过**。

- [ ] `ls $VERIFY_ROOT/runset/Calibre_QRC/LVS/*/*/`
- [ ] `ls <你的 qrc_deck_dir>`（就是 `project.yaml` 里 `qrc_deck_dir` 指的那个目录）

注意一个已发现的事实：LVS deck 和 QRC deck 的版本号**可以不同步**
（样本里是 `Ver_LVS_A` vs `Ver_QRC_B`），而且 QRC deck 比 LVS deck
多一层 `QCI_deck` —— 这正是 `qrc_deck_dir` 没法用 `|parent` 自动推导、
必须手填的根本原因。

### 3. corner 的真实取值（阻断：你点名要的那个功能）

`templates/quantus/ext.cmd.j2` 里 `-technology_corner "TYPICAL"` 是写死的。
我给的候选列表混了两代命名（`RCWORST` 一套 和 `RCWORST_CCWORST` 一套），
真实的 `assura_tech.lib` 通常只提供其中一套 —— 直接做成下拉框会给你一半无效选项。

- [ ] `grep -i corner $SETUP_ROOT/assura_tech.lib`（或者你直接说这颗工艺认哪几个）

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

- [ ] LVS deck 后缀：deck 目录里除了 wodio、widio 还有别的吗？（默认 两种）
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

- [ ] 版图导出文件名：现在导出 `<cell>.calibre.db`（内容是 GDS）。旧脚本导出 `<cell>.gds`
      并且允许你选落在默认目录 / 当前目录 / 指定子目录 —— **这是你曾经有、现在被拿掉的能力**。
      三种落点还要保留吗？（默认 `<cell>.calibre.db`，落在本次运行目录）
- [ ] 版图导出的其它选项：层次深度、大小写处理、点号转换现在一个都没传，用的是
      strmout 默认。你原来的手工流程有没有指定过？（默认 全默认）
- [ ] `Design` 这个基名：`query_output/` 下的 `Design.gds.map` / `Design.props` 到底是
      PDK 的 `query_cmd` 生成的，还是我们可以改？请打开 QRC deck 目录下的 `query_cmd` 看一眼。
      （默认 Design，且当作不可改）
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

## 0. 分支

```bash
cd <deploy>/Auto_ext
git checkout refactor/recipe-and-run     # 全部改动都在这个分支，main 一个字没动
```

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
