以下所有行号均指向 `C:\code\Auto_ext\Auto_ext\` 下的文件。

**前置声明**：给我的 Jivaro/si/Calibre 草案在 `reduction_log_level` 条目中途被截断（`choices: ["error","warning","i`），因此 D 组里若干条可能已被截断部分覆盖；我按"可见部分未出现"来判定，并逐条标注了这一点。

---

# 1. 漏掉的

## A. templates/quantus/ext.cmd.j2 + dspf.cmd.j2

**A1. 文件头注释块与工具版本水印** — 两文件 L1-5
```
#OPTION COMMAND FILE created by Cadence Extraction Quantus UI Version 18.21-s340
```
应进 catalog，归 `fixed / provenance`。理由：`18.21-s340` 决定了这份 cmd 的选项语法属于哪一代 Quantus；升级 QRC 后合法选项集会变（草案里一半 `guess` 条目的答案都取决于版本）。Run 归档必须能一眼看出当次跑的是哪个语法版本。同时它是纯注释，patch diff 必须能整块识别为非语义行。两份草案零覆盖。

**A2. 引号风格 / 断行风格 / 缩进宽度** — ext L14 `auto`（裸）、L15 `infinite`（裸）、L16 `"MICRONS"`（带引号）、L17 `"auto"`（带引号）；L46-47 `-cdl_out_map_directory` 与它的值分两行；L59-60 `-technology_corner` 与 `"TYPICAL"` 分两行；L63-64 `-temperature` 与值分两行；所有续行用 ` \`，段末不带续行；参数行统一缩进 14 空格。
应进 catalog，归 `渲染规则`（不是用户可调项）。理由：patch 逃生舱以"生成结果"为基线做 diff，catalog 如果不把逐选项的引号/断行/缩进记下来，换 catalog 重新生成时整文件飘红，用户的真实 patch 会被噪声淹没。两份草案零覆盖，而这正是"逃生舱能不能用"的先决条件。

**A3. 段结构与"哪些行属于哪种输出形态"的分解表** — ext 独有：`-design_cell_name`(L32)、`-format`(L36)、`-device_properties_file`(L40)、整段 `output_db -type extracted_view`(L41-54)；dspf 独有：`-hierarchy_delimiter`(L35)、整段 `metal_fill`(L39-40)、`output_db -type dspf`(L41-62)、`-file_name`(L65)、`output_setup -net_name_space`(L66)。其余约 90% 逐字相同。
应进 catalog，归 `结构层`。理由：草案的 `output_form` 只说"由它决定生成哪一份"，但没给共有行/形态专属行的分解表；没有这张表就无法从一份 Recipe 渲染出两份 cmd。

**A4. 十个命令名本身** — `capacitance` / `extract` / `extraction_setup` / `filter_cap` / `filter_coupling_cap` / `filter_res` / `input_db` / `output_db` / `output_setup` / `process_technology`（+ dspf 的 `metal_fill`）。
应进 catalog，归 `fixed / section`。理由：新 catalog 要按段渲染，段名是一级结构；今天它们只作为字面量存在于模板里，没有任何数据结构记录"哪个参数属于哪个段"。

**A5. extracted_view 形态的产物没有落盘路径** — ext.cmd 的 `output_setup`(L55-57) 没有 `-file_name`，产物是 DFII 库里的 cellview（`-view_name "[[out_file]]"`, L54）。
应进 catalog，归 `run / artifact`。理由：草案只有 `dspf_out_path` 一条产物路径，Run 的 `run.json` 要记 artifact 路径时，extracted_view 这一支是"库/单元/视图三元组"而不是文件路径，属于另一类产物，必须单列。

**A6. ext.cmd 完全没有 metal_fill 段这件事的语义** — 草案 `metal_fill_type` 只说"ext.cmd 整段缺失，等价于不做 metal fill 处理"，这是推断不是事实。
应进 catalog 并降级为待确认：不写这段时 Quantus 的默认行为由 deck 决定，可能是 `none` 也可能是 `real`。

## B. templates/calibre/calibre_lvs.qci.j2 + docs/calibre_raw.txt

**B1. ERC 产物声明了但没人开、也没人读** — qci L20-21 `*lvsERCDatabase` / `*lvsERCSummaryFile`，但整份 runset 没有任何 ERC 开关，`core/checks.py` 也只解析 `.lvs.report`（checks.py:1-17）。
应进 catalog，归 `fixed + run/results`。理由：草案把这两行塞进 `dut_identity_bindings` 就算完，漏掉了"ERC 结果文件生成了但从没被判定"这条事实；Run 的 `results/` 该不该收 `.erc.summary` 需要明确写进 spec。

**B2. `*lvsSpiceFile: [[cell]].sp`** — qci L13。这是流程里的第三个文件名约定（另两个：si 产 `<cell>.src.net`、strmout 产 `<cell>.calibre.db`）。
应进 catalog，归 `fixed`，并注明产出方与消费方。草案把它混进 identity bundle，没记录它由谁产生、有没有人读。

**B3. `*cmnFDIDEFLayoutPath: [[cell]].def`** — qci L44。整条流水线没有任何一步产生或读取 `.def`。
应进 catalog，归 `fixed / dead field`，明确标注"Calibre Interactive 面板残留，保留字面量以免 runset 不完整"。否则重构时一定有人纠结它。

**B4. 两个 deck 各自的版本段与 PDK 子目录** — raw L2 `.../LVS/Ver_Plus_1.0l_0.9/CFXXX/CFXXX.wodio.qcilvs`；raw L27 `.../QRC/Ver_Plus_1.0a/CFXXX/QCI_deck/query_cmd`。
应进 catalog，归 `profile`，拆成 `lvs_runset_version` / `qrc_runset_version` / `pdk_subdir` 三项 + 体检。理由：草案的 `lvs_deck_dir` / `qrc_deck_dir` 把这些揉成一整串路径，因此丢掉了两条硬事实：(1) LVS deck 与 QRC deck 版本号可以不同步（`1.0l_0.9` vs `1.0a`）；(2) LVS deck 目录就是 `<pdk_subdir>`，而 QRC deck 目录多一层 `QCI_deck` —— 这正是 `qrc_deck_dir` 无法沿用 `|parent` 自动推导、必须手填的根本原因（config/project.yaml 里那句 "No standard env-var convention" 就是它的后果）。

**B5. 可选行的 trim_blocks 缠绕写法** — qci L31-32：
```
[% if connect_by_name %]*cmnVConnectNamesState: ALL
[% endif %]*cmnSpecifyLicenseWaitTime: 1
```
应进 catalog，归 `渲染规则`。理由：这是本项目里"一行可选内容"的唯一正确写法（`[% endif %]` 必须贴着下一条指令），`core/importer.py:355-400` 也依赖 `*cmnShowOptions:` 作为注入锚点。新 catalog 只要产生任何可选行，就必须复用这个模式，否则渲染出空行、与真实导出 diff 不上。

**B6. `*cmnVConnectNamesState` 只有 ALL 分支，没有 NONE 分支** — 关闭时整行不输出，靠 Calibre 默认兜底。"不写"与"写 NONE"是否等价没有验证。→ 归入待确认。

**B7. runset 名字本身编码了配方信息** — raw L1 段头 `==Calibre_wiodio_noConnectByNetName==`，且渲染产物文件名也叫 `wiodio_noConnectByNetName.qci`（`examples/runs/task_DEMO_LIB__inv__layout__schematic/rendered/`）。
应进 catalog。草案的 `runset_section_header` 只讲了段头，漏了**文件名同样在编码 variant + connect_by_name**，也就是说今天"配方"是靠人肉文件名管理的 —— 这正是 Recipe 对象要接管的东西，新模型里段头和文件名都应由 Run slug 生成。

## C. templates/si/default.env.j2

字段级别两份草案是全覆盖的（35 行逐行核对无遗漏），缺的是以下四条**非字段类**事实：

**C1. `simRunDir` 缺行必须自动补，这是不变量不是普通字段** — `core/importer.py:476-485` 有专门逻辑：真实导出的 si.env 常常没有 `simRunDir`，缺了 si 会把网表写进 cwd，Quantus 随后找不到（注释里引了 Cadence bug 号 `LBRCXM-756`）。草案 `netlist_run_dir` 只说了 workdir 归属，没把"缺行自动补"和这个 bug 号带进来。

**C2. si.env 的布尔字面量有三种写法** — L5 `'t`、L15 `'nil`、L6 裸 `nil`。
应进 catalog，归 `渲染规则`：统一输出 `'t` / `'nil`，并记录 L6 是历史噪声。草案只在 `netlist_renetlist_all` 里顺带一提，没升成全局规则。

**C3. 字段顺序是导出顺序不是逻辑顺序** — `checkCAPPERI`(L22) 脱离了电容组(L16-18)，夹在二极管组(L19-21)后面。
应进 catalog：明确"si.env 是 SKILL 赋值，顺序无语义，但 catalog 必须固定沿用现有顺序"，否则按逻辑分组重排会导致与用户手上的真实 si.env 全文 diff。

**C4. `incFILE` 只有一个槽** — L34 `incFILE = "$calibre_source_added_place"`。草案已提"展开时机要确认"，但漏了另一半：若某 PDK 需要前置多份 CDL，现在无从表达。Profile 侧该字段应支持列表并渲染成 SKILL 列表。

## D. templates/jivaro/default.xml.j2（以下均为草案可见部分未出现；截断段落可能已覆盖其中若干条）

**D1. `<reductionParameters version="2024.1">`** — L1。schema 版本，必须与现场 Jivaro 可执行版本匹配。应进 catalog，归 `fixed / provenance`，与 A1 同理。

**D2. `<outputView value="[[out_file]]_red"/>`** — L3，`_red` 后缀硬编码。应进 catalog，归 `cells / run 输出命名`。理由：缩减后的视图名才是后仿真真正要挂的那个名字，用户必须知道、也可能要改；旧流程同样是 `<OutFileName>_red`（`docs/archive/Old_project_prompt.txt:47`），说明这是沿袭下来的约定而非任意值。

**D3. `<viewsToReduce value="av_extracted"/>` 与 `out_file` 不一致 —— 这是模板里最实质的一处潜在 bug** — L6 硬编码 `av_extracted`，而 `examples/demo/tasks.yaml` 与 `config/tasks.yaml` 里 `out_file` 都是 `av_ext`，Quantus 写出的视图名是 `-view_name "[[out_file]]"`（ext.cmd:54），Jivaro 的 `inputView` 也是 `[[out_file]]`（jivaro L2）。三处里两处跟 `out_file` 走，唯独 `viewsToReduce` 是另一个字面量。
必须进 catalog，并且应由 `out_file` 派生而不是独立字面量。两份草案零覆盖。

**D4. 寄生器件模型映射四行** — L9-12 `rModel=analogLib/presistor/symbol`、`cModel=analogLib/pcapacitor/symbol`、`lModel=analogLib/pinductor/symbol`、`kModel=analogLib/pmind/symbol`。
必须进 catalog，与 Quantus 的 `-res_component "presistor"`(ext.cmd:52) / `-cap_component "pcapacitor"`(ext.cmd:42) 合成一个"寄生器件契约"组，落 PDK Profile。理由：改一边不改另一边，Jivaro 读不出器件；而且 Jivaro 侧多出电感/互感两项，Quantus 侧没有对应项（因为没开 RLCK 提取），这个不对称本身要记下来。

**D5. `<!-- Selectivity settings -->` 空段** — L21。Jivaro 的选择性缩减（按网络指定）入口就在这里，现在是空的。应进 catalog，标记为 patch 逃生舱的官方入口点。

**D6. 整份 XML 挤在 3 个物理行里** — L1 一行含 4 个标签、L21 一行含 3 个闭合标签。应进 catalog，归 `渲染规则`（否则重新生成时格式化成缩进 XML，与用户手上的文件全文 diff）。

**D7. DUT 三元组有两种序列化格式** — jivaro L2 `"[[library]]/[[cell]]/[[out_file]]"`（斜杠分隔、库在前）vs quantus ext.cmd L32 `"[[cell]] [[lvs_layout_view]] [[library]]"`（空格分隔、**倒序**）。
应进 catalog。草案的 `dut_identity_bindings` 只列了字段名，没记这两种序列化差异，会让人以为可以共用一个渲染函数。

## E. 跨文件 / 整块缺席的对象

**E1. strmout 整个 stage 零覆盖** — `auto_ext/tools/strmout.py:25-39`，argv 为 `-library / -topCell / -view / -strmFile / -layerMap`。四点缺失：
- 输出名 `<cell>.calibre.db` 硬编码在 Python（strmout.py:31），与 calibre `*lvsLayoutPaths`(qci:3) 强耦合 —— 与草案 `netlist_out_name` 完全同类的跨文件硬耦合，但它住在 Python 里而不是模板里，所以两份"从模板反推"的草案必然漏掉；
- 旧流程输出的是 `<CellName>.gds`，且菜单允许三种落点（默认 / 当前目录 / 指定子目录，`docs/archive/Old_project_prompt.txt:64, 92-95`）—— 这是用户曾经有、现在被拿掉的能力；
- strmout **没有** 传 `-cdslib`，而 si 传了 `-cdslib ./cds.lib`（si.py:31-38）—— 两个工具对 cds.lib 的发现方式不一致；
- 常用但缺席的选项（`-hierDepth` / `-convertDot` / `-case` / `-runDir` / `-logFile` / `-templateFile` / `-strmVersion`）一条都没有。

**E2. layer_map 本身没有任何 catalog 条目** — `project.yaml: layer_map: ${PDK_LAYER_MAP_FILE}`（config.py:196-197，runner.py:622），喂给 strmout。目标模型明说 PDK Profile 要装 layer map，两份草案却一条都没有。

**E3. cds.lib** — si.py:31-38 `-cdslib ./cds.lib`，由 `core/workdir.py` 负责摆放。运行目录契约，无条目。

**E4. employee_id 与站点 scratch 根** — `core/importer.py` 有硬编码的两种员工路径预处理形状 `/tmpdata/RFIC/rfic_share/<id>/` 与 `/data/RFIC3/<project>/<employee>/`，`ext.cmd` 的 `-cdl_out_map_directory` 在真实模板里就带员工路径（importer.py:508-511 的负向前瞻注释可证）；runner 里 `employee_id` 是一等上下文变量（runner.py:624-629, 646）。两份草案零覆盖。这属于 Site/Profile，且与"绝对路径会泄漏项目+工号"这条既有约束直接相关。

**E5. output_dir / extraction_output_dir / intermediate_dir 的路径模板** — config.py:208-209（`${WORK_ROOT}/cds/verify/QCI_PATH_{cell}`、`${WORK_ROOT2}`）及 `{cell}/{library}/{task_id}/{lvs_layout_view}/{lvs_source_view}` 格式键。草案只有 `query_output_dir` / `dspf_out_path` 两条派生项，派生的根没有条目。新模型里这就是 Run 的目录布局来源。

**E6. `continue_on_lvs_fail`** — config.py:258 / cli.py:72。"LVS 挂了要不要继续往下跑"是真正的运行策略开关，两份草案都没有。应归 Recipe 或 Run policy（我倾向 Run，因为它是本次跑法而不是提取条件）。

**E7. stage 子集执行与并行度** — CLI `--stage`(cli.py:66) / `--jobs`(cli.py:99)，对应旧菜单的"只跑 LVS / 只跑 QRC / 从 QRC 继续 / 仅生成文件"（Old_project_prompt.txt:97-105）。属于 Run 不属于 Recipe，但必须在 catalog 的对象归属表里出现，否则重构时会被误塞进 Recipe。

**E8. `Design` 这个基名的真正来源** — 四处同一个字面量：`-run_name "Design"`(ext:34)、`Design.gds.map`(ext:38)、`Design.props`(ext:40)、`-temporary_directory_name "Design"`(ext:57)；而这些文件实际由 calibre 的 `*lvsPostTriggers` 调 `<qrc_deck_dir>/query_cmd`(qci:26) 产生。也就是说基名由 **PDK deck 的 query_cmd 决定**，不是我们能自由改的；`core/importer.py:522,529` 也把它写死进正则。草案把 `calibre_run_name` 标成"likely 可暴露"，却把两条派生路径标成"certain 不可动"，自相矛盾。应四条并成一组 `fixed`，并加一项体检：读 `<qrc_deck_dir>/query_cmd` 核对基名。

**E9. 除 LVS report 外的结果文件无归档条目** — `checks.py` 只读 `<cell>.lvs.report`；Run 的 `results/` 还应收 `.erc.summary`(qci:21)、Quantus 日志、Jivaro 缩减报告。草案的 `lvs_report_file_name` 只覆盖第一项。

**E10. 事实性补充：现有 knob 总数是 7 不是 5** — quantus 两份 manifest 各 5 个（同名同值），calibre manifest 另有 `lvs_variant` + `connect_by_name`（`templates/calibre/calibre_lvs.qci.j2.manifest.yaml:4-15`），si / jivaro manifest 的 `knobs` 为空。草案多处写"现有 5 个 knob 之一"。

---

# 2. 判断可疑的

**2.1 `ground_net` 默认值写错，却标了 certain** — 草案写 `default: "gnd!"`；实际 `TaskSpec.ground_net = "vss"`（config.py:245），`examples/demo/tasks.yaml` 与 `config/tasks.yaml` 也都是 `vss`。模板本身不含默认值（ext.cmd:9 是纯变量）。同一批草案的 si 段落里又正确地写了"默认 vss"，两份草案内部打架。

**2.2 四条 `currently: existing_knob` 是错的** — `dspf_out_path`（实为 ProjectConfig/TaskSpec 字段，config.py:220 / 269）、`reduction_enabled` / `reduction_frequency_limit` / `reduction_error_max`（实为 `TaskSpec.jivaro` 的 JivaroConfig 字段，config.py:34-39；jivaro manifest 的 `knobs` 是空的）。这不是措辞问题：删除 knob 四层机制**不会**顺带删掉这四项，它们走的是完全另一条配置路径，迁移工作量和风险都不同。

**2.3 `lvs_deck_basename` 默认值写成了某个 PDK 的实例值** — 草案 `default: "CFXXX"`，标 certain。实际默认是派生的 `PurePosixPath(calibre_lvs_dir).name`（runner.py:660-661），`CFXXX` 只是 `docs/calibre_raw.txt` 这一份导出里的取值。catalog 里这样写会让下一个人以为 CFXXX 是通用默认。

**2.4 草案 key 名与模板变量名不一致，且没有记录映射** — `lvs_deck_variant` vs 模板 `[[lvs_variant]]`(qci:1)、`lvs_connect_by_name` vs `connect_by_name`(qci:31)、`preserve_cell_list_file` 其实并不是一个变量而是 `[[qrc_deck_dir]]` 拼出来的字面路径(ext.cmd:18)、`reduction_frequency_limit` vs `[[jivaro_frequency_limit]]`(jivaro:16)。catalog 必须显式带一列"catalog key → 模板变量名"，否则 importer 与 patch 基线全部对不上。

**2.5 两份草案的 `group` 是两套互不兼容的分类法** — Quantus 草案的 group 是"模板段落"（extraction / lvs / output / netlist），Calibre 草案的 group 是"对象归属"（profile / fixed / netlist / reduction / lvs）。同名 group 含义冲突：`netlist` 在 Quantus 侧指输出网表格式选项、在 Calibre 侧指 si.env 网表生成；`lvs` 在 Quantus 侧指"从 Calibre 读数据"、在 Calibre 侧指 LVS 本身。合并前必须二选一。建议：`group` 只表示对象归属（recipe / profile / cells / run / site / fixed），另加独立的 `section` 字段记模板段落。

**2.6 按对象归属看，Quantus 草案里归错的条目**（这些在"模板段落"语义下不算错，但目标对象模型要的是归属）：
| key | 草案 group | 应归 |
|---|---|---|
| `preserve_cell_list_file` | extraction | profile |
| `technology_library_file` | extraction | profile |
| `technology_name` | extraction | profile |
| `ground_net` | extraction | cells |
| `design_cell_name` | lvs | cells |
| `extracted_view_name` | output | cells |
| `query_output_dir` / `layer_map_file` / `device_properties_file` / `cdl_out_map_directory` | lvs / output | run（派生） |
| `dspf_out_path` | output | run |
| `input_db_type` | lvs | fixed |
| `calibre_run_name` / `output_temporary_directory_name` | lvs / output | fixed（见 E8） |
| `lvs_license_wait_min` | lvs | site（草案理由里自己承认了，group 却没改） |
| `cpu_count` / `lvs_cpus` / `reduction_cpus` / `lvs_run_mt` / `lvs_run_hyper` | extraction / lvs / reduction | 单开一组 resources |

最后一行尤其要紧：资源类旋钮若留在 Recipe 里，一份 Recipe 从 8 核机器带到 64 核机器就得改，直接违背"Recipe 跨项目可移植"这条硬要求。

**2.7 `input_db_type` 标 "likely 应暴露" 不成立** — `tools/calibre.py:70` 把 LVS 引擎写死成 Calibre，`*lvsPostTriggers`(qci:26) 又写死了 `calibre -query_input ... -query svdb`。把 `input_db -type` 暴露成 Recipe 字段，用户改成 `pvs` 只会得到一个自相矛盾的状态。应归 `fixed`，由"这条流水线的 LVS 工具是谁"派生。

**2.8 confidence 一栏被当成两件事在用** — 同一个字段里既表达"这行是不是写死的"（可从模板 100% 确定）又表达"我给的合法取值集对不对"（只能问用户）。典型受害者：`extract_type`（写死=certain，choices=guess）、`include_parasitic_res_model`（三态这件事=certain，取值=guess）、`lvs_device_filter_options`（值 `AG RC RE RG`=certain，含义=guess）、`technology_corner`（写死=certain，9 个 corner 名=guess）、`extract_selection`、`metal_fill_type`。建议拆成两列 `observed`（来自模板，全部 certain）+ `choices_confidence`。

**2.9 `coupling_cap_threshold_absolute` 的 unit + default 组合物理上不成立** — 草案与 manifest 都写 `unit: F, default: 0.01`，即 10 mF 的耦合电容阈值。要么单位不是法拉，要么这个默认值本身就是错的。而它被标成 `certain`。这条恰好是用户那句"取决于当初谁手写了 manifest"的活样本：manifest 是人写的，catalog 不能把它当事实继承。

**2.10 `technology_corner` 的 choices 混了两代命名** — 草案同时列了 `CBEST/CWORST/RCBEST/RCWORST` 与 `CBEST_CCBEST/CWORST_CCWORST/RCBEST_CCBEST/RCWORST_CCWORST`。真实的一份 `assura_tech.lib` 通常只提供其中一套。当成 choices 直接进 UI 会给用户一半是无效项的下拉框。

**2.11 `lvs_abort_on_supply_error` 的理由不成立（结论成立）** — 草案断言"正因为它是 0，那两张 power/ground 名单目前基本是哑的"。从仓库无法证实：power/ground 名单同时喂 supply 识别与 ERC，而 runset 确实声明了 ERC 产物(qci:20-21)。理由该改写，"该暴露"这个结论保留。

**2.12 `netlist_force_full` / `netlist_renetlist_all` 的"取值互相矛盾"是误读** — `simNotIncremental='t`（不做增量网表）与 `simReNetlistAll=nil`（不强制重新 netlist 所有 cellview）不冲突，是常见组合。草案据此建议合并成三态枚举 `incremental / full / renetlist_all`，会把当前真实存在的 `t + nil` 组合表达不出来。应保留两个独立布尔。

**2.13 `check_scale` 的 `micron` 是编的** — 模板只有 `meter`(si:24)，标 likely 偏高，应 guess。

**2.14 所有 range 都是发明的，却混在标 certain 的条目里** — `temperature [-55,175]`、`netlist_short_res [0,1000000]`、`reduction_frequency_limit [0.1,1000]`、`reduction_error_max [0,100]`、`lvs_cpus / reduction_cpus / cpu_count [1,64]`、`lvs_license_wait_min [0,1440]`、`decoupling_factor [0,1]`。catalog 里 range 必须单独标 `unverified`，否则读者会以为边界也被核实过（其中 `temperature` 那条整体标了 certain）。

**2.15 `lvs_deck_variants` 里 `widio` 本身也是传闻** — "importer 只认 wodio|widio" 属实（importer.py:415-416, 442），但真实文件里只出现过 `wodio`（raw:2）；`widio` 只存在于手写 manifest 的 choices 里。草案标 certain 偏高，`widio` 是否真实存在应进用户确认清单。

**2.16 `output_form` 应该是 certain 而不是 likely，且它的严重性被低估了** — `TemplatePaths` 只有**一个** quantus 槽（config.py:85-93，project.yaml/tasks.yaml 各一份），所以今天在一次 run 里**不可能**同时产 extracted_view 和 DSPF。这是可从代码确定的结构性限制，也是删掉模板绑定槽后 Recipe 必须承担的头号功能。

**2.17 `design_cell_name` 的观察正确但止步太早** — "dspf.cmd 无此行"属实（dspf L31-38 同时缺 `-design_cell_name` / `-format` / `-device_properties_file`）。但没说后果：DSPF 流程靠什么确定 top cell？多半靠 `query_output` 里 svdb 的内容。这是个真实空洞，应升级为用户确认项而不是脚注。

**2.18 `input_db_hierarchy_delimiter` 的理由缺了一半** — 它与 `output_hierarchy_delimiter` 都只在 dspf 出现（dspf:35 / dspf:46），ext.cmd 侧完全没有分隔符设置（因为 extracted_view 走 DFII 层次不需要）。这句必须写进理由，否则重构时有人会"好心"给 ext.cmd 补一行。

**2.19 `dut_identity_bindings` 是打包条目而非参数，且 lands_in 漏项** — 它混在按参数排的表里，无法被渲染器直接消费；`lands_in` 里漏了 strmout 的 `-library / -topCell / -view` 与它产出的 `<cell>.calibre.db`（strmout.py:26-38），而 `*lvsLayoutPaths`(qci:3) 的值正是这个产物。

**2.20 `lvs_report_options` 的风险描述过宽** — checks.py 只在"banner 为 CORRECT 且没有 DISCREPANCIES 行"时才回落到 CELL SUMMARY（checks.py:5-12）。所以"减字母会误报 fail"仅在 Calibre 不打印 DISCREPANCIES 行的版本上才致命（注释点名 v2019.2）。理由应收窄。

---

# 3. 必须问用户的

```
[ ] 缩减目标视图名：Jivaro 模板里写死要缩减 av_extracted，但任务表里提取出来的视图名是 av_ext，这两个是不是应该永远一致？（默认 av_extracted）
[ ] 提取类型 extract -type：现在写死 rc_coupled 改不了，你们平时还会用哪几档（只提电容 / 只提电阻 / 带耦合）？请给出你们 QRC 认的确切写法。（默认 rc_coupled）
[ ] 工艺 corner：现在写死 TYPICAL。请在办公室打开本工艺的 assura_tech.lib，把里面实际提供的 corner 名字抄一份给我。（默认 TYPICAL）
[ ] 输出形式：一次跑是否需要同时出 extracted view 和 DSPF？（今天只能二选一）另外除这两种，还需要 SPEF 吗？（默认 只出 extracted view）
[ ] 耦合电容绝对阈值：现在默认写的是 0.01 且标注单位为法拉，这个量级不合理。你们平时填的数值和单位是什么？（默认 0.01 F）
[ ] Design 这个名字：query_output 下的 Design.gds.map / Design.props 是 PDK 的 query_cmd 生成的，还是可以由我们改？请打开 QRC deck 目录下的 query_cmd 看一眼。（默认 Design）
[ ] metal fill：DSPF 流程里写着按虚拟填充处理，extracted view 流程里整段没有。这两条流程对 metal fill 的处理是不是本来就该不同？（默认 DSPF=virtual，extracted view=不写）
[ ] LVS deck 版本后缀：deck 目录里除了 wodio、widio，还有别的后缀吗？请 ls 一下 LVS deck 目录把文件名给我。（默认 wodio / widio 两种）
[ ] 按名连接：现在只有"全部按名连接"和"完全不连"两档。你们有没有过只对电源地按名连、信号网不连的需求？（默认 不按名连接）
[ ] 小电阻短路门限 shortRES：现在 2000.0。是低于这个值被短路还是高于？单位是欧姆吗？这个值和 Calibre deck 那边是不是必须一致？（默认 2000.0）
[ ] 器件属性刻度 checkScale：现在 meter。你们的 Calibre deck 期望的是 meter 还是 micron？（默认 meter）
[ ] 全局电源/地网名：si.env 里这两项现在是空的，完全靠原理图自带的全局网兜底。你们的原理图是否总会声明？要不要在单元表里加一列"电源网"，和已有的"地网"配对？（默认 两项都为空）
[ ] 器件 CDL 前置文件：si.env 里填的是一个环境变量名的字面串。请跑一次 si 后打开生成的 .src.net，确认前面确实带上了器件 subckt 定义。另外你们有没有需要前置多份 CDL 的情况？（默认 单份，$calibre_source_added_place）
[ ] 版图导出文件：现在导出的文件叫 <cell>.calibre.db（内容是 GDS）。旧脚本导出的是 <cell>.gds 并且允许你选落在默认目录 / 当前目录 / 指定子目录。这三种落点还要保留吗？（默认 <cell>.calibre.db，落在本次运行目录）
[ ] Quantus 多核：现在提取是单核跑的。你们的 QRC 支持多核吗？如果支持，命令文件里要写哪一段（有没有 turbo / 每机核数之类的写法）？（默认 单核）
[ ] Jivaro 缩减判据 criterion：现在写死 standard。Jivaro 界面上这一项还有别的选项吗？（默认 standard）
[ ] 寄生器件映射：Quantus 把寄生电阻/电容映射成 presistor / pcapacitor，Jivaro 那边另外还认电感和互感（pinductor / pmind）。这四个名字在你们 PDK 里是不是就这样？两边必须一致吗？（默认 analogLib 的 presistor / pcapacitor / pinductor / pmind）
[ ] LVS 器件过滤：现在过滤开关是关的，但过滤项写着 AG RC RE RG。这四个字母各代表什么？你们要不要打开过滤？（默认 关闭）
[ ] LVS 报告内容 lvsReportOptions：现在是 S。你们平时还需要报告里多打印什么段落？（默认 S）
[ ] 电源网出错就中止：现在是不中止。打开之后配合那两张几十项的电源/地网名单，会不会一堆块直接跑不过去？（默认 不中止）
[ ] Calibre 并行：现在 Turbo 核数 2、多线程开、Hyperscaling 开。你们站点有 Hyperscaling license 吗？另外 license 等待时间写的 10，单位是分钟吗？（默认 2 核 / 都开 / 10）
[ ] 寄生电阻模型语句：这一项现在填的是 comment（既不是开也不是关）。comment 在你们的后仿真里意味着什么？三档分别什么时候用？（默认 comment）
[ ] 长走线切段与过孔阵列：切段长度写的 infinite、过孔阵列间距和上限都写的 auto。这三项除了 auto/infinite 还能填数值吗？射频长传输线你们会调到多少？（默认 infinite / auto / auto）
[ ] ERC：LVS runset 里声明了 ERC 结果文件，但从来没人看。要不要把 ERC 结果也收进每次运行的记录里并参与判定？（默认 不判定）
[ ] 版图导出的其它选项：层次深度、大小写处理、点号转换这几项现在一个都没传，用的是 strmout 默认。你们原来的手工流程有没有指定过？（默认 全部用 strmout 默认值）
```