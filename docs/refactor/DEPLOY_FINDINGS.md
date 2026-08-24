# 第一次真实红区部署暴露的问题（2026-08-24）

`3916896` 装上了真服务器,迁移完成,`check-env` 全绿,`--dry-run` 通过。
**部署链本身是成立的** —— 但过程中撞出四个缺陷,都不是"环境不对",是我们自己的问题。
这份文档记录它们的**成因**;开放状态在记忆的 backlog 里,别在两处重复。

---

## 1. 文档里 12 处 `--config-dir config` 全是错的 ⚠️ 优先级最高

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

**该怎么修**:两件事一起做才干净 ——
① 文档全部改成不依赖 cwd 的写法;
② `run.sh` 在 `cd` 之前把 `--config-dir` / `--out-root` / `--auto-ext-root` / `--workarea`
这类相对路径先转成绝对路径,这样文档怎么写都能跑。
只改文档是治标:下一个人照直觉敲相对路径,还是会撞。

---

## 2. `migrate` 产出的 profile 里 corner 表和 variant 表是空的

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

**该怎么修**:凡是 legacy 配置**结构上不可能提供**的表(corner / variant / 供电网名单),
`migrate` 应该回落到 shipped profile 的值,并在报告里说明"这几项不是从你的模板来的"。
让用户手工合并两个 YAML 是不可接受的。

---

## 3. `deploy.sh` 会替换 `templates/`,原件只靠 3 次轮转保命

**成因**:`SEED_ONLY=("config" "recipes")` —— `templates/` 不在保护名单里,
所以换装时被包里那份覆盖,用户那套**用了几个月、验证过**的模板被移进
`.deploy/backups/<时间戳>/templates/`,而备份只保留最新 3 份。

**为什么代价特别高**:那套模板正是 `OLD_VS_NEW_FLOW.md` 第零节那个最高价值任务的**输入** ——
要靠它和 `examples/legacy/templates/` 逐份 diff,才能找出所有被冻成错误默认值的字段。
再部署三次就永久没了。

**现场处置**:已抢救到 `<install>/templates_ORIGINAL`。

**该怎么修**:要么把 `templates` 加进保护名单(但它确实是代码包的一部分,有点两难),
要么在换装时**大声警告**"你的 templates/ 被替换了,原件在 <备份路径>,3 次部署后消失"。
现在它是静默的。

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
