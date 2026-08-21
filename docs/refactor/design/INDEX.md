# Claude Design 画布 —— 画板索引

来源：claude.ai/design 项目 `c574afa1-56ef-4d84-89a5-f02962797989`
文件：`Auto_ext Redesign.dc.html`（185 KB，15 块画板，全部按真实窗口尺寸绘制）

`support.js` 是 Claude Design 画布自己的 React 运行时，**与 PyQt5 实现无关**，没有落库。

## 新设计（1a–1j）

| id | 画面 | 关键点 |
|---|---|---|
| `1a` | Cells，选中 3 行准备跑 | 主画面。1280×800 |
| `1b` | 运行中，第 4 个 cell 卡在 quantus | run 面板从 Cells 底部升起；**无进度条、无 ETA** |
| `1c` | Runs，批次列表 + 一张 PASSED 卡展开 | 不可变记录，最新在上 |
| `1d` | Runs，**LVS INCORRECT 卡** | discrepancy 数 + 与上次的差值 + Calibre Interactive 接力 |
| `1e` | 一批里四种失败，按"谁该动手"排序 | 三字母代码 chip + 每类不同的按钮 |
| `1f` | Recipes 编辑器，手工修改区收起 | 选项全部来自内建固定清单 |
| `1g` | 手工修改区展开 —— 逃生舱 | 逐 hunk 还原；**这是模板唯一可见的地方** |
| `1h` | Setup 抽屉（从标题栏徽章打开） | 一条失败的检查，修法就写在旁边 |
| `1i` | Cells 空状态 | 表头仍在，形状可见 |
| `1j` | 1366×768 下什么让位 | **窗口最小 940×560**，无一处硬最小值超过它 |

## Token 表（1k）

直接照抄进 QSS。要点：

- **强调色（`#1f5fa8`）永远不表示通过/失败/警告。** 状态有独立色阶，任何人不得借用。
- 状态色阶**逐字继承** `auto_ext/ui/models.py` 的 `STATUS_COLOR`；只有 running 从 `#0080ff`
  压暗到 `#0f6fd1` 以保证文字对比度。
- 失败四类复用同两个色相，**靠三字母代码区分而不是颜色**：
  `LIC` / `CFG` 走琥珀（改环境），`LVS` / `CRS` 走红（改设计或修工具）。
  灰度打印和色盲读者都能分辨。
- 字体只有两族，且 CentOS 7 上都在：
  `DejaVu Sans, Liberation Sans` / `DejaVu Sans Mono, Liberation Mono`。最小 11px。
- 圆角 0（按钮 2px）、无渐变、无阴影、无动画、无 emoji。
  字形限于 `✓ ✗ ▶ ■ – · ⇆ ▾ ▴ ▼`，全部在 DejaVu 里。
- 关键尺寸：表格行 24px、toolbar 32px、nav 项 30px（rail 132/44px）、
  焦点 1px 边框无光晕、选中态 3px 左边条。

## 现状对照（1l–1o）

照着 `ui/tabs/*.py` 在同样的 1280×800 下重绘的"之前"，用于对比：

| id | 对照 | 它指出的问题 |
|---|---|---|
| `1l` | Run tab | splitter 400\|800，右侧 400/400 |
| `1m` | Project tab | 5 个 group box 竖排、无滚动区 —— **1001px 最小高度的来源** |
| `1n` | Tasks tab | 竖直 splitter 3:2，预览表最小宽 696px |
| `1o` | Templates tab | 5 行列表 + 5 行表格，各占 700px |

## 与既有 spec 的一处 IA 变化

设计用**左侧 nav rail（132/44px）取代了顶部 tab bar**。
这与 `docs/refactor/` 里"四块区域"的信息架构一致，只是换了导航形态。
D 轮实现时以画布为准。
