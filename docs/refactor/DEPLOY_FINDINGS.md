# 真实红区部署暴露的问题

这份文档记录**成因**;开放状态在记忆的 backlog 里,别在两处重复。
两次部署各占一节。

## 第一次（2026-08-24,`3916896`）

装上了真服务器,迁移完成,`check-env` 全绿,`--dry-run` 通过。
**部署链本身是成立的** —— 但过程中撞出四个缺陷,都不是"环境不对",是我们自己的问题。

---

## 1. 文档里 12 处 `--config-dir config` 全是错的 ✅ 已修

**现象**:在安装目录里敲 `./run.sh migrate --config-dir config`,报
`Invalid value for '--config-dir': Directory 'config' does not exist.` —— 而 `config/` 明明在。

**成因**:`run.sh` 在启动 python 之前先

```bash
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # 安装目录
workarea="$(cd "${here}/.." && pwd)"                    # 上一级
cd "${workarea}"
```

这是**对的**,而且必须保留:Cadence 流程要求 cwd 是 workarea(`si -batch` 从 cwd 读 `si.env`,
`cds.lib` 也在那)。但后果是相对路径 `config` 解析成 `<workarea>/config`,
而 Typer 的 `exists=True` 在解析阶段就拒绝了。

**影响面**:`REDZONE_DEPLOY.md`、`OFFICE_TODO.md`、`OFFICE_VALIDATION.md`、
`OLD_VS_NEW_FLOW.md` 共 12 处。**整条办公室验证路径第一步就走不通。**

**现场绕法**:csh 有内置变量 `$cwd`,展开发生在 csh 里,轮不到 `run.sh`:

```csh
./run.sh check-env --config-dir $cwd/config
```

**怎么修的**:按上面第 ② 条做的 —— `run.sh` 在 `cd` 之前把路径型参数按**调用者的 cwd**
绝对化,相对路径于是恢复了它本来的语义,文档里那 17 处原样就能跑。只改文档是治标:
下一个人照直觉敲相对路径,还是会撞。

`AUTO_EXT_PATH_FLAGS`(在 `run.sh` 里)是那份"哪些选项吃路径"的清单,
`tests/test_run_sh.py` 拿 `cli.py` 的 AST 双向比对它 —— 这种手写清单会在
"有人加了个新选项但没打开 shell 脚本"的那天悄悄失效,而且失效了没人看得出来。
`--to` / `--layout-out` 故意不在清单里:它们是 workarea 相对的输出模板,带 `{cell}` 占位符。

规则本身只写在 README §Launch 一处;操作文档里只放指针,不复述。

---

## 2. `migrate` 产出的 profile 里 corner 表和 variant 表是空的 ✅ 已修

**现象**:迁移成功,`check-env` 却有两个 blocking fail:`lvs.variants (empty)`、
`pdk.corners (empty)`。

**成因**:`migrate` 的设计是**值中性** —— 所有值从用户自己的模板反读出来。
而用户的老模板里**本来就没有** corner 表和 deck variant 表这种东西
(老脚本根本不管这两样,见 `OLD_VS_NEW_FLOW.md` 第零节)。
于是迁移诚实地写了两张空表,并且**没有任何提示**告诉用户"包里那份 profile 已经有答案了"。

**现场绕法**:把 `.deploy/seed/config/profiles/default.yaml`(包里那份,带今天办公室查到的
9 个 corner、2 个 variant、29 个电源网、27 个地网)盖过去,再把迁移算出来的
`qrc.dir_expr` 单独贴回来 —— 因为**两份文件各有对方没有的东西**:
seed 有 PDK 事实表,migrated 有站点真实路径。

**怎么修的**:凡是 legacy 配置**结构上不可能提供**的表(corner / variant / 供电网名单),
`migrate` 回落到 shipped profile(`config/profiles/default.yaml`,经
`profile_discover.builtin_profile()`),并且**四个地方同时说**这几项不是用户的:
报告里的 `shipped_fallbacks` 段、一条 `seeded_from_shipped_profile` disposition
(所以"没有字段会悄悄消失"这条约定仍然成立)、一条 warning(于是 `migrate` 退出码是 1)、
以及写出的 YAML 里那个字段头上的 `NOT FROM YOUR CONFIG` 注释 —— 报告不会一直留在手边,
文件得自己会说话。

**只在表为空时回落。** 模板能给出的值一律不替换:哪怕只读回一个 corner,比 shipped 的九个
"差"在每一个维度上,除了唯一要紧的那个 —— 它是用户自己的。这条边界有专门的回归测试
(`test_a_corner_the_templates_do_have_is_never_replaced`)。

站点自己的路径这一半照旧从迁移里来 —— 当初逼着人手工合并两个 YAML 的正是这个:
seed 有 PDK 事实,migrated 有真实路径,两边互不包含。

---

## 3. `deploy.sh` 会替换 `templates/`,原件只靠 3 次轮转保命 ✅ 已修

**成因**:`SEED_ONLY=("config" "recipes")` —— `templates/` 不在保护名单里,
所以换装时被包里那份覆盖,用户那套**用了几个月、验证过**的模板被移进
`.deploy/backups/<时间戳>/templates/`,而备份只保留最新 3 份。

**为什么代价特别高**:那套模板正是 `OLD_VS_NEW_FLOW.md` 第零节那个最高价值任务的**输入** ——
要靠它和 `examples/legacy/templates/` 逐份 diff,才能找出所有被冻成错误默认值的字段。
再部署三次就永久没了。

**现场处置**:已抢救到 `<install>/templates_ORIGINAL`。

**怎么修的**:那个"两难"是真的,所以两边都不选 —— `templates/` **仍然被替换**
(渲染路径在运行时解析它,留着旧的就是"这版代码跑上版流程"),但换装前把**要被顶掉的那份
另存一份**到 `<install>/.deploy/yours/templates-<时间戳>/`,**这个目录轮转永远不碰**,
并在结尾那段"不能滚过去的警告"里点名具体路径。

只在它和包里那份**有差异**时另存(`diff -rq`),所以没动过模板的机器不会攒垃圾 ——
这条有独立的反向测试。真正证明这个修法的测试是
`test_the_keepsake_outlives_the_backup_that_used_to_be_its_only_home`:
连部署 `KEEP_BACKUPS + 1` 次,原来那份备份已经被轮转掉,另存的那份还在。

机制是通用的,不是给 `templates` 开的特例:`deploy.sh` 里的 `KEEPSAKE_DIRS`
就是"包里发、又归用户改"的目录清单。

---

## 4. `views_to_reduce: av_extracted` 出现在全部 3 个迁移出来的配方里

不是新发现,是**在真实数据上确认**了之前只在样例里看到的问题。
成因和修法见 `OLD_VS_NEW_FLOW.md` §D4:它被放在 recipe 作用域(多单元共享),
而视图名是 cells 作用域(每单元一个),所以**无论字面值对不对,放在配方里就已经错了**。

---

## 顺带被证明可行的

- **`deploy.sh` 的 bootstrap 缺口有解**。指南的 4a 是"全新机器"、4c 是"覆盖老 clone"
  但默认你已有 `deploy.sh` —— "老装在、`deploy.sh` 不在"两边都没覆盖。
  答案是从包里单挖那一个文件,不用单独上传:

  ```tcsh
  tar -xzf Auto_ext_pro_<hash>.tar.gz --strip-components=1 Auto_ext_pro/deploy.sh
  bash deploy.sh
  ```

  这样版本必然和包一致,换行符也有 `pack.ps1` 的预检兜底 —— 手工上传 `.sh` 会绕开那道保护,
  很多 SFTP 客户端默认 ASCII 模式会转成 CRLF,然后红区给你 `bash: $'\r': command not found`。

- **今天办公室查的数据全部生效**:9 个 corner、2 个 deck variant 在真 PDK 上 `check-env` 全绿。

- **老 clone 上覆盖安装(指南 4c)行为正确**:`.git/` 被挪进备份而不是删除。

---

# 第二次（2026-08-25,`216c369`）—— 装好了,self-test 抓到 3 个真问题

`deploy.sh` 一次成功,`doctor.sh` 三档全绿(五个 EDA 工具、五个站点变量、PyQt5、DISPLAY 全部就位)。
**这也是那套单测第一次在真 Linux 上跑** —— 2532 个用例,4 个红。装机本身没问题;
三个红是真缺陷,而且没有一个能在开发机上暴露。

## 5. 测试断言的是**机器的属性**,不是工具的属性 ⚠️ 这一类最危险

两个红同属一类:

| 测试 | 它默认成立的前提 | 红区的真相 |
|---|---|---|
| `test_a_missing_env_var_says_how_to_bind_it` | `VERIFY_ROOT` **没有**被 export | PDK setup 脚本把五个全 export 了 |
| `test_run_summary_reports_a_missing_binary_as_such` | `si` **不在** PATH 上 | `si` 就在 PATH 上 |

两个在每一台开发机上都是绿的 —— 因为开发机上那些东西本来就没有。
第二个还更糟:它**真的启动了一个 Cadence `si`**。`doctor.sh --test` 跑的自检没有资格做这种事。

**成因不是逻辑写错了,是断言的对象错了。** 一套要随包发出去、在目标机上当装机判据跑的测试,
**不能依赖目标机比开发机更贫瘠**。

**怎么修的**:`clean_env`(那个从 `tests/support/v2.ENV` 派生变量名的 fixture)**早就存在**,
只是它是 opt-in,而那个测试忘了要。opt-in 给不了这个保证 —— 只要有一个测试忘记就破功 ——
所以它现在是 **autouse**;要某个变量的测试自己 `setenv`。
PATH 那个单独处理:它不能进 autouse(会让 Windows 上找 `bash` 的 fixture 全部 skip),
所以那一个测试把 PATH 指向一个空目录。

**验证方式**:在开发机上**把红区环境复现出来**再跑全量 —— 六个站点变量全设、
五个假的 EDA 可执行文件放进 PATH。修复前那两条复现失败,修复后 2516 全绿、
skip 数和干净环境一模一样。没有这一步,"绿了"只是又一次在贫瘠环境里绿。

## 6. `deploy.sh` 的备份目录在**同一秒内**会撞名

**现象**(红区自检里的原文):

```
mv: cannot move '.../Auto_ext_pro/auto_ext' to '.../.deploy/backups/20260825-101438/auto_ext': Directory not empty
!! swap failed during 'backup' -- rolling back
```

**成因**:`TS="$(date +%Y%m%d-%H%M%S)"` 是**秒**分辨率。同一秒内的第二次部署算出同一个
`BK`,于是它把自己备份**进上一次的备份里**。目录非空时 `mv` 会报错 —— 那是响的那一半;
**安静的那一半更坏**:普通文件会 `mv` 成功并覆盖,于是一个备份目录里混着两套安装,
而回滚哪一套都不对。

**是谁抓到的**:`test_the_keepsake_outlives_the_backup_that_used_to_be_its_only_home` ——
昨晚为第 3 条写的那个测试,连部署 4 次。**Linux 上快到足以撞在同一秒,Windows 上慢到永远撞不上。**
这个缺陷是原有的,不是昨晚引入的;只是在此之前没有任何东西连着部署过。

**怎么修的**:`BK` 在动任何文件之前先去重(`<ts>`、`<ts>-2`、`<ts>-3`……)。
回归测试 `test_two_deploys_in_the_same_second_get_separate_backups` 用一个**冻住的 `date` shim**
把碰撞变成必然 —— 靠真实速度去撞就是一个只在快机器上才响的竞态,而那正是这个 bug
一路溜到办公室的方式。另有一条反向控制测试证明那个 shim 真的冻住了时钟。

## 7. GUI 测试假设了初始窗口宽度

`test_auto_compact_can_be_taken_over`:`show()` 之后窗口管理器给的第一个尺寸,
在真 X11 上**已经低于折叠阈值**,所以工具栏在测试"接管"之前就折叠了。
offscreen 平台给的初始尺寸更宽,于是本地一直绿。

**怎么修的**:先把宿主 resize 到阈值以上并**断言前置条件**,再接管。
前置条件不写出来,后面那条断言就什么也没说。

