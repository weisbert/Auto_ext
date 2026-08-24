# 红区部署 —— 照着敲，每步都写了「你应该看到什么」

**这是这个项目唯一正确的同步说明。** `docs/OFFICE_QUICKSTART.md` 和
`docs/OFFICE_VALIDATION.md` 里让你在服务器上 `git pull` 的那两步是错的 ——
红区没有到 GitHub 的路，而且包里根本不带 `.git`，那条命令的实际结果是
`fatal: not a git repository`。

三件事先说清楚：

1. **每一步都有预期输出。** 对不上就往下翻那一步的「不是这样怎么办」，别往下走。
2. **红区登录 shell 是 csh/tcsh。** 所有 `.sh` 一律 `bash xxx.sh` 地敲（不是 `./xxx.sh`），
   设环境变量用 `setenv FOO bar`，看退出码用 `echo $status`。
3. **路径全是占位符。** `<install>` = 你的安装目录
   （现在是 `/data/RFIC3/<project>/<sub-project>/<employee-id>/workarea/Auto_ext_pro/`）。

---

## 三十秒版

已经装过一次、只想更新的话，只有下面这几行：

```powershell
# 黄区 Windows（PowerShell）
git pull
powershell -ExecutionPolicy Bypass -File deploy\pack.ps1
```

```tcsh
# —— 把 deploy\dist\ 里那两个文件上传到红区的 <install>/ ——
cd <install>
bash deploy.sh
bash deploy/doctor.sh --test
```

**第一次**装的人别跳，从步骤 1 开始。

---

## 链路：三跳

```
家里 Windows ──push──▶ GitHub(public) ──clone/pull──▶ 黄区 Windows
                                                            │
                                                  deploy\pack.ps1
                                                            ▼
                                          Auto_ext_pro_<short>.tar.gz
                                                  + .sha256
                                                            │
                                                       上传这两个
                                                            ▼
                                            红区 Linux：bash deploy.sh
```

黄区能 `git pull`，红区只能上传文件 —— 所以第一跳靠 git，第二跳只能是一个 tarball。
**依赖（wheels）走另一条通道**，见文末。

---

## 步骤 1（黄区 Windows）—— 把代码拉下来

```powershell
cd <你放代码的地方>\Auto_ext
git pull
git status
```

**你应该看到**：`Fast-forward` 或 `Already up to date.`，以及
`nothing to commit, working tree clean`。

**确认你在要发的那个分支上。** 打包打的是当前 `HEAD`，不是 `main`：

```powershell
git branch --show-current
```

要发别的分支就先 `git checkout <branch>`，或者打包时给 `-Ref <branch>`。
包名里的短哈希就是最终的判据 —— 它和红区 `deploy.sh` 打印的
`installed version` 必须一致。

| 看到 | 说明 | 去做 |
|---|---|---|
| `git status` 列出 `modified:` | 有未提交改动 | **打包只打已提交的内容**，未提交的东西过不了气隙。要么提交，要么 `git checkout -- .` 丢弃 |
| `fatal: unable to access ...` | 黄区连不上 GitHub | 换台能连的机器 clone 再整个目录拷过来。**别在红区解决这个问题** |

---

## 步骤 2（黄区 Windows）—— 打包

```powershell
powershell -ExecutionPolicy Bypass -File deploy\pack.ps1
```

只要 git + PowerShell，不需要 Python、不需要 tar、不下载任何东西。
（ExecutionPolicy 挡住的话双击 `deploy\pack.bat`，效果一样。）

**你应该看到**（末尾这几行是判据）：

```
>> packaging HEAD (2cc02b8) -> ...\deploy\dist\Auto_ext_pro_2cc02b8.tar.gz

OK  package : ...\deploy\dist\Auto_ext_pro_2cc02b8.tar.gz  (1,028.8 KB)
    sha256  : 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08

commit info:
    2cc02b8  2026-08-23T10:36:11+08:00  <这次的 commit 标题>

NEXT -- upload BOTH files into  .../Auto_ext_pro/ :
       Auto_ext_pro_2cc02b8.tar.gz
       Auto_ext_pro_2cc02b8.tar.gz.sha256
```

判据是 **`OK  package :`** 那一行，以及 `deploy\dist\` 下确实出现了**两个**文件。
包名里的 `2cc02b8` 就是 GitHub 上那个 commit 的短哈希 —— 以后「红区装的是哪一版」
一眼就能对上。

| 看到 | 说明 | 去做 |
|---|---|---|
| `WARNING: Working tree has uncommitted changes` | 有改动没提交，包里不会有它们 | 只是警告。不在乎就继续，否则回步骤 1 |
| `... has CRLF in the git index.` | 行尾被 Windows 污染了 | 照它说的做：`git add --renormalize <那个文件>` 再提交。**这条不许绕过** —— CRLF 会让红区 bash 死在 `$'\r': command not found`，那是最没法调试的地方 |
| `git archive emits CRLF for the shell scripts ...` | `.gitattributes` 改了但没提交 | 提交 `.gitattributes` 再打包。`git archive` 读的是**提交进去的**那份 |
| `... is not tracked by git` | 新文件还没进 git | 在开发机上提交并 push，黄区重新 `git pull`。**黄区不提交东西** |

---

## 步骤 3 —— 上传

把**两个文件**都传到红区的安装目录：

```
<install>/Auto_ext_pro_<short>.tar.gz
<install>/Auto_ext_pro_<short>.tar.gz.sha256
```

第一次装的话这个目录还不存在 —— 传到你打算安装的**父目录**（也就是 workarea）里就行。

| 看到 | 说明 | 去做 |
|---|---|---|
| 只有 `.tar.gz`，没有 `.sha256` | 少传了一个 | 补传。没有它下一步会跳过校验并打 `WARN`，等于放弃了「传输没坏」这个保证 |
| `.tar.gz` 大小是 0 或明显偏小 | 传输被截断 | 重传。**不要**试图解压一个残包 |

---

## 步骤 4（红区）—— 装上去

### 4a. 第一次（这台机器上还没有安装目录）

```tcsh
cd <workarea>
tar -xzf Auto_ext_pro_<short>.tar.gz
cd Auto_ext_pro
```

解压出来的 `Auto_ext_pro/` 就是安装目录，以后所有事都在它里面做。

### 4b. 以后每次更新

把新的 `.tar.gz` + `.sha256` 传进**安装目录本身**，然后：

```tcsh
cd <install>
bash deploy.sh
```

不用给参数 —— 它自己挑目录里最新的那个包，并告诉你挑了哪个。

**你应该看到**（本机实测的真实形状）：

```
>> found package: Auto_ext_pro_2cc02b8.tar.gz
>> verifying sha256...
>> extracting to staging...
>> incoming version:
     Auto_ext
     commit   2cc02b8b6592f859db9e48221b7a9933423752e4
     date     2026-08-23T10:36:11+08:00
     subject  <这次的 commit 标题>
>> backing up current install -> <install>/.deploy/backups/20260823-104012
>> installing new version...
>> rotating backups (keeping newest 3)...

OK  deployed.
    installed version:
     ...（和 incoming 一致）
    previous install backed up at: <install>/.deploy/backups/20260823-104012
    kept as-is: .deploy wheels runs logs
    your config recipes kept; the package's copy is in .deploy/seed/ if you want to diff it

    NEXT -- check this box can actually run it (no network / no venv needed):
       cd <install> && bash deploy/doctor.sh --test
```

判据是 **`OK  deployed.`**，以及 `installed version` 里的 hash 和步骤 2 的
`commit info` 一致。

### 4c. 第一次从老的 `git clone` 安装上覆盖

老安装目录里有个 `.git/`。它会被挪进备份（不是删除），并且换装结束时会单独打一段：

```
    NOTE: this install had a .git/ directory -- it came from the old
          'git clone on the server' flow, which cannot work here ...
```

这是对的：红区没有到 GitHub 的路，代码从此只从你上传的 tarball 来。

| 看到 | 说明 | 去做 |
|---|---|---|
| `bash: $'\r': command not found` | 包里的 `.sh` 是 CRLF | 步骤 2 的预检被绕过了。回黄区重新打包，**别在红区 `dos2unix`** —— 那只治了这一个文件 |
| `checksum FAILED` | 传输坏了 | 回步骤 3 重传两个文件。**安装目录一个字节都没动**，可以放心重来 |
| `... is the WHEELS bundle, not a code package` | 传错文件了 | 照它打印的两行做（见文末 wheels 一节） |
| `!! swap failed ... !! rollback complete -- install restored.` | 换装中途失败，**已自动回滚**，原来那份还在 | 把整段输出留着。多半是磁盘满或权限：`df -h .` 看一眼 |
| `!! YOUR OWN FILES WERE MOVED OUT OF ...` | 你在安装目录里放了别的东西，被挪进备份了 | 照它说的把名字写进 `.deploy/preserve.list`（一行一个）。**备份轮转 3 次之后会删，别拖** |

---

## 步骤 5（红区）—— 选 python

```tcsh
ma python/3.11.4
python -V
```

**你应该看到** `Python 3.11.4`。

| 看到 | 说明 | 去做 |
|---|---|---|
| `Python 2.7.6` | `ma` 没生效，用的是 PATH 上那个 OpenOffice 自带的 | 重新 `ma python/3.11.4`，并确认在**同一个 shell** 里往下敲 |
| `ma: Command not found` | 这台机器没有 module 工具 | 直接找一个 3.11 的解释器，后面每条命令加 `--python /abs/path` |

---

## 步骤 6（红区）—— 体检 + 跑一遍装好的单测 ★

```tcsh
bash deploy/doctor.sh --test
```

它做两件事：① 探测这台机器上每个候选解释器能跑到哪一档；② 跑一遍**随包发过来的整套单测**。
在一台没网的机器上，一套全绿的测试是能拿到的最强证据 —— 它同时证明了包完整落地、
依赖装好了、解释器可用、逻辑正确。

**你应该看到**（末尾这几行是判据）：

```
>> /software/.../bin/python3.11  (3.11.4)
     OK  dep Jinja2         3.1.6
     OK  dep ruamel.yaml    0.19.1
     OK  dep pydantic       2.13.3
     OK  dep typer          0.24.1
     OK  dep rich
     OK  import auto_ext.core.runner
     OK  import auto_ext.core.render
     OK  import auto_ext.cli
     OK  si       /software/.../si
     ...
     OK  env      all 5 site variables are set
     OK  PyQt5    OK only with the wheel bundled Qt5 -- use ./run.sh gui,
              which sets LD_LIBRARY_PATH for you (bare python will fail)
     --  DISPLAY  unset (headless -- no GUI; normal in a plain ssh session)
     ------------------------------------------------
     tier 1  render / dry-run / tests         AVAILABLE
     tier 2  drive the EDA flow               AVAILABLE
     tier 3  GUI                              code OK, needs X11 ($DISPLAY)

RECOMMENDED: /software/.../bin/python3.11   (tier 2)
...
=== self-test with ... ===
... 2327 passed, 27 skipped, 1 xfailed ...

OK  self-test passed -- the package landed intact and this interpreter runs it.
```

**判据只有两条**：`tier 1 ... AVAILABLE`，以及最后那句
`OK  self-test passed`（`echo $status` 是 `0`）。

> **测试条数会随版本变**，别拿数字当判据。`skipped` 里有一部分是「这里不是 git 检出」
> —— 那是对的：包里没有 `.gitattributes` / `pack.ps1`，那几条开发侧的检查会优雅跳过。

**关于 tier**

| tier | 能干什么 | 缺了要紧吗 |
|---|---|---|
| 1 | 渲染、dry-run、跑单测 | **要紧**。缺了说明依赖没装或包没落全 |
| 2 | 真跑 EDA 流程 | 真跑之前得有。缺的是 `si`/`strmout`/`calibre`/`qrc` 不在 PATH —— `source` 你的 Cadence setup 即可 |
| 3 | GUI | **不要紧**。纯 ssh 会话里本来就该只有 CLI |

| 看到 | 说明 | 去做 |
|---|---|---|
| `VERDICT: no interpreter on this box can run Auto_ext.` | 没有解释器能用 | ① `ma python/3.11.4` 再跑；② `bash deploy/doctor.sh --python /abs/path`；③ 提示里如果说 `offline dependencies not installed` → `bash scripts/install_offline.sh` |
| `dep ... MISSING` | wheels 没装 | `bash scripts/install_offline.sh` |
| `CANNOT SELF-TEST: pytest is not installed` | wheels 包里没带 dev 依赖 | 装本身是好的（上面的结论仍然成立）。想要单测就在黄区 `python scripts\download_wheels.py --include-dev` 重打 wheels 包 |
| `FAIL  self-test failed.` | 单测有红 | **别继续**，这个安装的任何结果都不可信。先重跑 `bash deploy.sh` 重装；仍然红就把整段输出贴回来 |
| 满屏 `?` 或乱码 | 这台机器 `LANG` 是 `C` | 不影响判据（判据行全是 ASCII）。想看清就 `setenv LANG en_US.UTF-8` |

---

## 步骤 7（红区）—— 开始干活

到这里同步就结束了，后面是工具本身的事，接
[`OFFICE_TODO.md`](OFFICE_TODO.md) 的「到了办公室先跑这三条」：

```tcsh
./run.sh check-env --config-dir config
./run.sh run --config-dir config --recipe rc-typical-55c --profile default --dry-run
./run.sh runs list
```

三条都从安装目录里敲；`config` 这种相对路径按你站的位置解析（README §Launch）。

---

## wheels 是独立的一条通道

依赖**不在代码包里**，也不可能在：`wheels/` 是 gitignore 的，`git archive`
结构上就看不见它。它变化频率极低（一年两次），所以单独走。

```powershell
# 黄区，只在依赖集变了的时候
powershell -ExecutionPolicy Bypass -File deploy\pack.ps1 -WithWheels
```

```tcsh
# 红区：注意是 tar 直接解，不走 deploy.sh
tar -xzf Auto_ext_pro_wheels_<n>.tar.gz -C <install>
cd <install>
bash scripts/install_offline.sh
```

`deploy.sh` 会**按文件名认出** wheels 包并拒绝，同时告诉你上面这两行 —— 免得它
三步之后才以 `staged package missing auto_ext/core/runner.py` 失败，那个报错看起来
像「代码包坏了」。

装好之后 `wheels/` 就留在安装目录里，以后每次 `deploy.sh` 都**不碰它**。

---

## 回滚

每次部署把上一份完整备份到 `.deploy/backups/<时间戳>/`（保留最近 3 份），
上传过的包保留最近 2 个。两条路都不需要再过一次气隙：

```tcsh
# 路 1：重新部署上一个包（推荐）
cd <install>
bash deploy.sh Auto_ext_pro_<老的短哈希>.tar.gz

# 路 2：直接把备份搬回来
#   先删掉除 .deploy 以外的内容，然后
mv .deploy/backups/<时间戳>/* .
```

---

## 这一趟到底写了哪些地方

**写了**（全部在安装目录里面，父目录一个字节都不碰）：

* `<install>/` 下的代码 —— 步骤 4 的换装；
* `<install>/.deploy/` —— 备份 / staging / scratch / seed。

**没写、也永远不会写**：

* `<install>/wheels/`、`runs/`、`logs/` —— 换装永不触碰；
* `<install>/config/`、`recipes/` —— 已存在就永不覆盖，包里那份挪去 `.deploy/seed/`；
* 任何含 `run.json` 的目录（你把 runs 根指到别处的情况）；
* `/tmp`、`/opt`、`/var` —— 一个都不碰，连临时文件都在 `.deploy/tmp/` 里。

---

## csh/tcsh 备忘

| 想干的事 | bash 写法（**别在红区用**） | csh/tcsh 写法 |
|---|---|---|
| 设环境变量 | `export FOO=bar` | `setenv FOO bar` |
| 看退出码 | `echo $?` | `echo $status` |
| stdout + stderr 一起进文件 | `cmd > f 2>&1` | `cmd >& f` |
| stdout + stderr 一起进管道 | `cmd 2>&1 \| less` | `cmd \|& less` |
| 跑一个 `.sh` | `./x.sh` | **一律 `bash x.sh`**（上传通道可能吃掉 exec 位） |

---

## 失败对照总表

| 现象 | 在哪一步 | 一句话原因 | 去做 |
|---|---|---|---|
| `fatal: not a git repository` | 任何 | 你在红区敲了 `git pull` | 红区没有 git，代码只从 tarball 来。看本文步骤 2–4 |
| `bash: $'\r': command not found` | 4 | 包里 `.sh` 是 CRLF | 回黄区重新打包（`pack.ps1` 有预检，正常打不出 CRLF 包） |
| `checksum FAILED` | 4 | 传输坏了 | 重传两个文件；安装目录未被触碰 |
| `is the WHEELS bundle` | 4 | 传错文件 | 见 wheels 一节 |
| `VERDICT: no interpreter ...` | 6 | 没有能用的 python，或依赖没装 | `ma python/3.11.4` / `--python` / `install_offline.sh` |
| `dep ... MISSING` | 6 | wheels 没装 | `bash scripts/install_offline.sh` |
| `not on PATH: si, strmout, calibre, qrc` | 6 | 只到 tier 1 | **不挡 dry-run**。真跑前 `source` 你的 Cadence setup |
| `FAIL  self-test failed` | 6 | 单测有红 | 重装；再红就是 bug，贴输出 |
| `sh scripts/redzone_scan.sh` 报 `not a git repo` | 任何 | **那条不该在红区跑** | 它是开发机的提交闸门（要 git），已被 `export-ignore` 挡在包外。红区的等价物是 `bash deploy/doctor.sh --test` |
