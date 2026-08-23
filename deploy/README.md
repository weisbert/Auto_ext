# deploy/ — how code crosses the air gap

Two zones, and only one direction of travel:

```
家里 Windows ──push──▶ public GitHub ──pull──▶ 黄区 Windows
                                                    │
                                          deploy\pack.ps1
                                                    ▼
                                   Auto_ext_pro_<short>.tar.gz + .sha256
                                                    │
                                              上传这两个文件
                                                    ▼
                                     红区 Linux：bash deploy.sh
```

黄区（Windows）能 `git pull`；**红区（Linux）不能** —— 那里没有 git、没有网、
没有 pip、没有 venv，只能往上传文件。所以第二跳只能是一个 tarball。

完整的分步操作（每步带「你应该看到什么」和「不是这样怎么办」）在
[`../docs/refactor/REDZONE_DEPLOY.md`](../docs/refactor/REDZONE_DEPLOY.md)。
这里只解释这几个文件各自负责什么。

| 文件 | 跑在哪 | 干什么 |
|---|---|---|
| `pack.ps1` | 黄区 Windows | 从 **committed HEAD** 打包（`git archive`）。包名带 commit 短哈希，出 `.sha256`，打包前双重预检行尾 |
| `pack.bat` | 黄区 Windows | `pack.ps1` 的双击/免 ExecutionPolicy 包装 |
| `../deploy.sh` | 红区 Linux | 校验 → staging → 备份 → 原子换装 → 失败自动回滚 → 保护你的数据 |
| `doctor.sh` | 红区 Linux | 这台机器能跑到哪一档（tier 1/2/3），`--test` 再跑一遍随包发过去的整套单测 |
| `_env_check.py` | 红区 Linux | `doctor.sh` 的探针，每个候选解释器跑一次，输出 `KEY=VALUE` |

## 三条不能改的性质

**1. 包的内容是黑名单，不是白名单。** 仓库里的东西默认全部过关，除非
`.gitattributes` 里 `export-ignore`。以后新加的模块、测试、recipe 自动进包，
`pack.ps1` 永远不用改 —— 上一版打包脚本手写 include 列表，`recipes/`
加进仓库几个月后它还在按老列表打包，于是**静默地**没过气隙。

**2. 行尾在打包时就判死。** CRLF 会让红区 bash 死在 `$'\r': command not found`，
而那是最没法调试的地方。`pack.ps1` 有两道独立的预检（index 里的 blob 是不是 LF，
以及 `git archive` **实际吐出来的字节**里有没有 `\r`），任一不过就拒绝打包。
`tests/test_deploy.py` 在本机再验一遍。

**3. 红区那边的这四样东西，换装永远不碰**：

| | 为什么 |
|---|---|
| `wheels/` | 离线依赖包，过一次气隙很贵，而且它是 gitignore 的 —— `git archive` 结构上就装不进代码包 |
| `runs/` `logs/` | 你的运行结果 |
| `config/` `recipes/` | 站点配置。这两个**包里有**（给新机器起步用），但已有的永远不覆盖，包里那份挪去 `.deploy/seed/` 供你 diff |
| 任何含 `run.json` 的目录 | 你把 runs 根指到别处的情况 |

其余顶层条目会被挪进 `.deploy/backups/<时间戳>/`，并在换装结束时**用整屏最后一段**
告诉你挪了什么（备份轮转 3 次之后会删，所以那段必须看见）。

## wheels 是独立的一条通道

```powershell
# 黄区（只在依赖集变了的时候做）
powershell -ExecutionPolicy Bypass -File deploy\pack.ps1 -WithWheels
```

产出 `Auto_ext_pro_wheels_<n>.tar.gz` + `.sha256`。它**不走 `deploy.sh`** ——
`deploy.sh` 按文件名认出它并拒绝，同时告诉你正确的两行：

```bash
tar -xzf Auto_ext_pro_wheels_<n>.tar.gz -C <install>
bash scripts/install_offline.sh
```

## 回滚

每次部署把上一份安装完整备份到 `.deploy/backups/<时间戳>/`（保留最近 3 份），
上传过的包保留最近 2 个。回退有两条路，都不需要再过一次气隙：

```bash
# 路 1：重新部署上一个包
bash deploy.sh Auto_ext_pro_<老的短哈希>.tar.gz

# 路 2：直接把备份搬回来（删掉除 .deploy 以外的内容，然后）
mv .deploy/backups/<时间戳>/* .
```
