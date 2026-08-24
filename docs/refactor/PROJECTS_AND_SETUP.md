# 项目概念 + Setup 可编辑（2026-08-24 夜起草）

用户 2026-08-24 定的下一轮方向，原话：

> 下个回合我们先做 setup 编辑（同时我也希望建立项目级别的概念，一个项目可以用一个配置，
> 可以通过导入现成的、在该项目环境下生成的模板生成相关的项目环境变量；
> 主要针对于那些没有系统环境变量的变量）

三条要求，按用户的顺序。这份文档记**决定和理由**；开放状态在记忆的 backlog 里，别在两处重复。

---

## 零、出发点：现在长什么样

- Setup 抽屉**完全只读**。`python scripts/ui_inventory.py --screen setup` 一行就列完了：
  `buttons : ['Re-check', '✗']`。它唯一能写的东西是 `env_overrides`，走
  `override_requested` 信号，而且那个信号在没有接收者时按钮是禁用的。
- 写入通道**早就修好了、只是没有 UI**：`ConfigController.stage_workspace` /
  `stage_profile` → `save()`（渲染全部暂存文档 → 写盘 → 重新加载 → `config_saved`），
  和 Recipes 屏用的是同一条路。缺的从来只是前面那块玻璃。
- "项目"今天等于**一个被记住的路径**：`QSettings` 里一条 `last_config_dir`
  （`auto_ext/ui/app.py`）。换项目 = 走文件对话框重选目录。

---

## 一、什么是一个"项目"

**决定：项目就是 config 目录本身，磁盘上不发明任何新东西。**

一个项目 = 一个 `config_dir`，里面是：

| 文件 | 对象 | 谁拥有它 |
|---|---|---|
| `workspace.yaml` | `WorkspaceConfig` | 项目：产物落在哪、用哪个 profile |
| `profiles/<id>.yaml` | `PdkProfile` | 站点 + 工艺：路径、corner 表、deck、环境 |
| `cells.yaml` | `CellBook` | 项目：这个项目要跑哪些单元 |
| `recipes/*.yaml` | `Recipe` | 可移植：跨项目共享的才是好配方 |

**为什么不发明 `project.yaml`。** 上一轮刚把 v1 的 `project.yaml` 拆掉（14 个键变 5 个，
见 `01-schema.md` §1.4）。再引入一个"项目描述文件"就是把它请回来，而且这次连迁移路径都没有。
项目的身份是**它的目录**；名字是 `workspace.yaml` 已有的东西加上目录名，不需要新字段。

**变的是什么。** GUI 从"记住一个路径"变成"认识一组项目"：一个已知项目的**登记表**
（`QSettings`，与 `last_config_dir` 同一处），每项是一个绝对 `config_dir` 加上一次
最后打开时间。切项目 = 从列表里挑一个，而不是每次重走文件对话框。

登记表是**便利设施，不是事实来源**。目录被移走/删掉的条目在读取时被静默丢弃，
和今天 `_read_last_config_dir` 对陈旧条目的处理完全一样。任何一条路径都可以直接用
`--config-dir` 打开而不必先登记；登记只是让它下次出现在列表里。

---

## 二、Setup 怎么变成可编辑的

**决定：抽屉不长大。新开一个 Project 屏，抽屉链过去。**

抽屉现在的职责是一句话：*我现在能不能跑，不能的话我该敲什么*。它 520px 宽，
每一行是**一个检查**，不是一个字段。把 29 个字段塞进去会同时毁掉两件事。

所以：

- **抽屉保持原样**，只多一个动作：一条检查的 fix hint 指向某个 YAML 字段时，
  给一个"去改它"的入口，跳到 Project 屏并滚到那个字段。
  抽屉自己仍然不做任何 I/O —— 它发信号，宿主去办。
- **新的 Project 屏**（导航栏第四项）就是"一个项目一份配置"那个对象的编辑器：
  `WorkspaceConfig` 6 个字段 + `PdkProfile` 23 个字段，加上项目切换和第三条的导入。

这不违反抽屉文档里那句 "Setup is not a tab"。那句话说的是**健康判据**不是 tab
（canvas 1h 的决定），说的不是配置编辑器。判据仍然只住在徽章后面的抽屉里。

**保存语义跟 Recipes 屏一致**：改一下就 stage 一下，Save 走
`ConfigController.save()`，Revert 丢弃暂存。不新造第二套脏状态机制 ——
上一轮的 8 个缺陷里就有一个是"Save 只入队、不写盘"，一套已经够难对了。

### 可达性

落地那天把 `PdkProfile` / `WorkspaceConfig` 加进 `tests/ui/test_reachability.py`。
今天它们**故意**不在里面：两个对象整体不可达，而一个豁免集等于"全部"的审计什么也没说。
加进去之后，每个字段要么绑到控件，要么进豁免集**并写明理由**——理由才是承重的那部分。

---

## 三、从项目自己产出的文件里反推环境值

这是最有价值的一条，也是**已经有引擎**的一条。

**问题。** `check-env` 能查的只有 shell 给得出的东西。一台新机器上，
那些不在 `$ENV` 里的变量它永远解决不了 —— 不是查不到，是**没地方查**。

**已有的引擎。** `auto_ext/core/recipe_import.py` 干的正是这件事，只是瞄准的是一份 Recipe：

- `solve_template_vars` —— 拿**模板自己的文本**当模式，用户文件当字符串，差就是答案。
  `inputView value="[[library]]/[[cell]]/[[out_file]]"` 对上
  `inputView value="INV_LIB/INV1/av_ext"`，一行还原三个值。
- `env_vars_solvable_from_files` —— 明确算出"哪些环境变量能从文件里读回来，
  而不是从 shell"。函数自己的 docstring 就写着这个存在的理由。
- `_infer_env` / `_env_from_profile` —— 把 `$env(SETUP_ROOT)/assura_tech.lib`
  对上用户的绝对路径，反解出 `SETUP_ROOT`。
- 结果对象 `RecipeImportResult` 上已经挂着 `resolved_env: dict[str, str]`
  和 `profile: PdkProfile` + `derived_profile: bool`。

**决定：这是把已有的东西拓宽一格，不是起一个新子系统。** 同一台解算器，
换一个落点：不是落进 Recipe，而是落进**这个项目的 profile 的 `env_overrides`**。

用户体验是一句话：*给我一份这个项目真产出过的文件（runset / `.cmd` / `si.env`），
我把这个项目自己的取值读回来。* 逐条给出：变量名、解出的值、来自哪个文件的哪一行、
以及 shell 现在给的是什么（如果给得出）。用户逐条采纳，采纳的写进 `env_overrides`。

**为什么落在 `env_overrides` 而不是别处。** 它已经是"我故意偏离 shell"的那个字段，
健康检查已经会把来自 override 的值画成 `⇆`（WARN）而不是 OK —— 语义现成，
而且这个"你偏离了"的提示正是这条功能需要的。

**风险，写在这里免得以后当惊喜。** `recipe_import` 自己的 docstring 说了：整套东西
是对着本仓库的模板和由它们渲染出的文件开发的，**从没对过办公室服务器上真 Quantus GUI
写出来的 `.cmd`**。第一次拿真文件喂它，最先动的两个数是 `MIN_SITE_HITS` 和
`MIN_SITE_COVERAGE`。这条功能的第一次真实使用同时也是那套假设的第一次检验。

---

## 落地顺序

1. Project 屏 + 字段规格层 + Save/Revert（第二条）
2. `test_reachability.py` 收编两个对象（第二条的验收）
3. 项目登记表 + 切换（第一条）
4. 从产出文件反推环境值（第三条）

第二条先做，因为它是另外两条的**载体**：切项目切的是这一屏显示的对象，
反推出来的值也要落到这一屏的某个字段上。
