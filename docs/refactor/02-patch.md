# Patch 逃生舱机制设计

替换 `core/preset.py` + `core/clone_template.py`。新模块 `auto_ext/core/patch.py`（纯 Python，无 Qt 依赖）；UI 侧 `ui/widgets/patch_panel.py` 替代 `preset_picker.py`，`diff_editor.EditTemplateDialog` 改造成"编辑生成结果"面板并把结果喂给 `capture_patch()`。

---

## 0. 前置事实（从代码里挖出来的、影响设计的约束）

| 事实 | 出处 | 对设计的影响 |
|---|---|---|
| Jinja 分隔符 `[[ ]]` / `[% %]` / `[# #]`，**`trim_blocks` 没开** | `core/template.py::_make_jinja_env`，memory `project_auto_ext_rules.md` | 生成结果对空白字节敏感；patch 必须按字节存 `after`，不能"美化"缩进 |
| 渲染前 `$X`/`${X}`/`$env(X)` 全部被 `substitute_env` 替换掉 | `core/template.py::render_template` | **生成结果里几乎不含 `$`** → 可以安全地用 `${var}` 做掩码 token，只需保留 `$$` 转义 |
| `ext.cmd.j2` 大量用 `\` 续行，参数缩进 14 空格 | `templates/quantus/ext.cmd.j2` | 必须有"空白归一化"匹配层：Quantus UI 重新导出会改缩进 |
| `calibre_lvs.qci.j2` 含 Tcl 花括号字面量 `{{rm -rf %d/svdb} process 1}` | 同上 | patch 内容不能走任何 shell/正则未转义路径 |
| 同一个模板会为**每个 cell** 渲染一次（`[[cell]]` `[[library]]` `[[out_file]]` 遍布全文） | `runner._build_context` | **决定性约束**：一条 patch 必须能作用于所有 DUT 的渲染结果，所以 patch 不能含 cell 相关的字面量 |
| `PresetHunk` 只有单行 `anchor_before`/`anchor_after`，逐行 `==` 精确比对，0 或 >1 匹配直接 `raise ValueError` | `core/preset.py::_locate_preset_hunk` | 已知缺陷：**一个字节都不能变**。上表最后一行说明它在本项目里几乎必然失败 |
| `DiffHunk` 已经把 `difflib.get_opcodes()` 封装好，`_merge_adjacent` 可复用 | `core/diff_template.py` | 采集侧直接复用，不重写 |

最后一行是本设计的核心洞察：**preset v1 之所以"锚点对不上直接拒绝"是致命的，不是因为缺 fuzzy，而是因为它在"含变量的渲染结果"上做精确字节匹配 —— 换一个 cell 就必炸。** 加 fuzzy 只是给错误的比对空间打补丁。正确的修法是换比对空间。

---

## 1. 存储格式

### 选择：**掩码化的锚定 hunk 列表（masked anchored hunks）**

不是 unified diff，不是 preset 那种单行锚点，也不是语义化指令。存的是：

```yaml
patches:
  - stage: quantus
    template_id: quantus/ext.cmd
    base:
      template_sha256: 3f2a...           # 采集时 .j2 源码的哈希
      catalog_version: "2026.08"
      profile_id: tsmc22ull
      masked_sha256: 9c1b...             # 掩码化生成结果的哈希 —— 快路径钥匙
      captured_at: 2026-08-21T09:14:02Z
    on_fuzzy: block
    hunks:
      - id: 7a3e91c4
        enabled: true
        intent: "这个 block 的 corner 必须是 CBEST，TYPICAL 会低估 RC"
        anchored_at_head: false
        anchored_at_tail: true
        context_before: |
          process_technology \
                        -technology_corner \
        before: |
                        "TYPICAL" \
        after: |
                        "CBEST" \
        context_after: |
                        -technology_library_file "${qrc_tech_lib}" \
                        -technology_name "${tech_name}" \
        occurrence_index: 0
        occurrence_count: 1
        slots: [qrc_tech_lib, tech_name]
        captured_values:
          qrc_tech_lib: /pdk/tsmc22ull/assura_tech.lib
          tech_name: tsmc22ull_1p10m
```

三个要素：

**(a) 掩码空间（masked space）—— 这是关键。** `before` / `after` / `context_*` 存的不是渲染后的真实文本，而是**把每个 Jinja 变量的落点替换成 `${var}` token 后的文本**。`$$` 转义字面 `$`。

生成掩码文本的办法不是字符串搜索（那会误伤：`ground_net="VSS"` 会把无关行里的 "VSS" 也掩掉），而是**用同一个模板渲染第二遍**：

```
base_real   = render(tpl, ctx)                                   # 真实生成结果
base_masked = render(tpl, {k: "${k}" if maskable(k) else v ...}) # 掩码生成结果
```

Jinja 自己完成 provenance 追踪，零启发式，位置绝对准确。`maskable(k)` 的规则：值是 str/int/float、不含 `\n`、且该变量**没有出现在任何 `[% if %]` 条件里**（条件变量必须绑真值，否则分支走错、行数就对不齐了）。条件变量集合从 `env.parse(src)` 的 AST 里 walk `nodes.If` 收集。

**(b) 上下文窗口而非单行锚点。** 默认前后各 3 行（`PresetHunk` 只有 1 行，在 `.qci` 这种每行都是 `*key: value` 的文件里，单行锚点的区分度太低）。窗口长度隐含在列表长度里，可按 hunk 缩短。文件头/尾用 `anchored_at_head/tail` 标记，匹配时强制 `start == 0` / `end == len`。

**(c) 出现序号 `occurrence_index` / `occurrence_count`。** 采集时记录"这是全文第 k 个匹配、共 n 个"。这是一个极其廉价的消歧器：当新 base 里仍然是 n 个匹配时，直接取第 k 个而不是报 AMBIGUOUS。preset v1 完全没有这个，所以任何重复结构（两个 `output_db` 段）都直接判死。

### 为什么不是 unified diff

在"生成侧内容会变"这个前提下逐条论证：

1. **`@@ -12,7 +12,6 @@` 的行号在重新生成后就是垃圾。** catalog 在上面插一条 directive，所有行号全错。真要用就必然退化成"忽略行号、靠 context 搜索" —— 那 diff 格式提供的唯一结构信息就没了，剩下的只是一个更难解析的 hunk 列表。行号还会给人虚假的确定感。

2. **unified diff 的 context 是死的字面文本，而本项目的 context 里全是变量。** `git apply` 在下面这个场景必然失败：patch 采集自 `cell=pll_top`，运行在 `cell=vco_core`。context 行 `*lvsLayoutPrimary: pll_top` 变成 `*lvsLayoutPrimary: vco_core` → context 不匹配 → reject。而这正是**最常见的场景**，不是边缘情况：一条 recipe 天然要跑几十个 cell。掩码版 `*lvsLayoutPrimary: ${cell}` 直接穿过去。同理适用于换 Profile（`${qrc_deck_dir}` 路径变了）和改 corner（`${temperature}` 从 25 变 85）。

3. **`after` 侧也要跟着变。** 如果用户的手工修改里引用了 cell 名（比如加一行 `-extra_netlist "pll_top_extra.sp"`），unified diff 会把 `pll_top` 冻死在补丁里，换 cell 就产出错文件且不报错 —— **静默错误，最坏的一类**。掩码格式把它存成 `${cell}_extra.sp`，重新物化时自动跟上。

4. **要求"UI 显示 N 处手工修改、可展开、可单条还原"。** 一个 diff blob 做不到稳定的单条身份：重新生成后 hunk 会合并/分裂，`id` 无处安放。列表结构里每个 hunk 有 uuid、`enabled`、`intent`、`last_status`，直接映射到 UI 行。

5. **需要挂元数据。** `intent`（为什么改）、`captured_values`（当时的值，做诊断用）、`on_fuzzy` 策略 —— diff 格式没有地方放，只能塞注释，注释又不参与校验。

**但是**：unified diff 是最好的**展示**格式和**导入**格式。`render_hunk_as_udiff()` 用于 UI 和 `run.json`；`import_udiff()` 接受同事粘过来的补丁并转换成掩码 hunk。存储 ≠ 展示。

### 为什么不是语义化补丁

"把 `-technology_corner` 的值设为 CBEST" 这类结构化指令 —— **这就是 knob 换了个名字**。它要求我们预先理解每种文件的语法，等于把"哪些参数能调"重新变成"当初谁写了 parser"。逃生舱的全部意义就是能碰任何东西，包括我们从没建模过的东西。明确拒绝。

---

## 2. 应用算法

### 2.1 快路径

如果 `sha256(base_masked_now) == patch.base.masked_sha256`，说明模板源码、profile、掩码结果三者完全一致，直接按采集时记录的行偏移拼接，`fuzz=0`，不跑任何搜索。这覆盖"没升级 catalog、没换工艺"的绝大多数运行 —— 注意即使 cell 换了，掩码结果也是相同的，所以快路径对整个 batch 都成立。这是掩码格式的第二个红利。

### 2.2 匹配阶梯

慢路径逐 hunk 跑一个有序阶梯，命中即停。三个正交轴：

| 轴 | 取值（从严到松） |
|---|---|
| 上下文长度 `ctx_n` | `full` → `full-1` → … → `0` |
| 变量绑定 `binding` | `EXACT`（全部绑当前值）→ `CHANGED_WILD`（只把 `captured_values` 里已变的槽放成通配）→ `ALL_WILD`（全部槽通配 `[^\r\n]*?`）|
| 空白 `normalize` | `off` → `on`（每行 `strip()` + 内部空白 run 折叠成单空格）|

遍历顺序：`ctx_n` 降序为外层，`(binding, normalize)` 按 `(EXACT,off) → (CHANGED_WILD,off) → (ALL_WILD,off) → (EXACT,on) → (CHANGED_WILD,on) → (ALL_WILD,on)` 为内层。

状态判定：

- `ctx_n == full and normalize == off` → **`CLEAN`**（`binding` 任意。掩码通配不算 fuzzy —— provenance 保证那些槽本来就是变量）
- 其余命中 → **`SHIFTED`**，记录 `fuzz=(dropped_ctx, binding, normalize)`

每层都要求**唯一命中**。多命中时：若 `len(matches) == occurrence_count` 则取 `matches[occurrence_index]` 并降级为 `SHIFTED`；否则继续下一层；全部层都多命中 → `AMBIGUOUS`。

低区分度守卫：当 `ctx_n == 0 and binding == ALL_WILD` 时，要求 needle 至少 2 行，或单行的非通配字面字符数 ≥ 12。否则跳过该层。防止 `-min_res ${min_res}` 这种一行掩码需要匹配到任何一行。

### 2.3 no-op / 转正 检测

**顺序很重要**：

1. **NOOP**：物化后 `before == after`（变量值收敛导致的）。不改动文本，状态 `NOOP`。
2. 跑 `before` 阶梯。命中 → 应用。**只要 `before` 还在，就说明 catalog 没有转正，不做 3。**
3. `before` 在第 1–4 层全灭 → 跑 **ABSORBED 探针**：拿 `after` 当 needle、用同样的 context 走同一套阶梯。若在 `CLEAN` 层唯一命中 → 状态 `ABSORBED`，**不改动文本**（catalog 已经产出了用户想要的东西），报告里标"此补丁已被 catalog 采纳，可删除"，UI 给一键删除。
4. 都不行 → 相似度层（§2.4）。
5. 还不行 → `LOST`。

（部分转正 —— catalog 产出的东西介于 before 和 after 之间 —— 会落到第 4 步的 `REVIEW`，这是正确行为。）

### 2.4 要不要 fuzzy、fuzzy 到什么程度

**要，但分级，且永不静默。**

- 第 1–4 层（缩上下文 + 掩码通配 + 空白归一）我称为**结构性 fuzz**：每一步放松的都是我们**有理由认为无意义**的差异（行号漂移、变量取值、EDA GUI 重导出的缩进）。这些自动应用，记 `SHIFTED`，UI 打徽章，`run.json` 记录。

- 第 5 层是**相似度 fuzz**，明确划线在这里：
  - 只对 `before` 块本身（不带 context）做窗口扫描，`difflib.SequenceMatcher(None, norm(before), norm(window)).ratio()`（先用 `quick_ratio()` 预筛）。
  - 阈值 `ratio >= 0.80` **且** 最优与次优（不重叠的）之差 `>= 0.10`。
  - 结果状态 `REVIEW`：**文本照打**（这样用户能在编辑器里预览合并结果），但 `report.blocking = True`。
  - **默认策略下 REVIEW 直接让 run 拒绝启动。** 只有 recipe 显式写 `on_fuzzy: accept` 才放行，且 `run.json` 强制记录。

为什么这么严：这是 RC 提取流程。一处 patch 打歪，在 `.qci` 里改的是 LVS 连接语义、在 `.cmd` 里改的是提取 corner —— **失败形式不是崩溃，是一份看起来完全正常、跑完了、LVS clean、但寄生参数是错的 netlist**。GNU `patch --fuzz` 那套"打上了、STDERR 提一句"的默认值，在这个领域是不可接受的。preset v1 "对不上就拒绝"的直觉是对的（它认识到了危险），错的是把拒绝当成了唯一手段；正确的修法不是"加 fuzzy"，而是"加 fuzzy + 让它不可能被无意识地消费掉"。

阈值 0.80 / margin 0.10 的取法：低于 0.80 时在 `.qci` 这种"每行都是 `*key: value`、行间高度同构"的文件里，随机行对的 ratio 就能到 0.6–0.7，噪声地板太高。margin 是为了防止在续行块里挑到相邻的错误行。

### 2.5 应用与冲突

- 全部 hunk 解析完毕后，按 `start` 排序，检查区间重叠 → `OVERLAP`（阻塞）。
- **自底向上**拼接（沿用 `preset.apply_preset` / `diff_template.apply_toggle_to_template` 已有做法，索引不失效）。
- `normalize` 命中时做**缩进再基准化**：`delta = 首个命中行的前导空白 - 物化 before 首行的前导空白`，把 `delta` 施加到每条 `after` 行。其它情况 `after` 逐字节原样写入。
- 报告永远返回 best-effort 的 `patched_text`（已解析的 hunk 都打上），阻塞与否由 `report.blocking` 决定。编辑器预览用前者，runner 看后者。
- 输入文本读入时统一 `\r\n → \n`，存储只用 LF。

### 2.6 代码

```python
# auto_ext/core/patch.py
"""Patch escape hatch: user edits the *generated* file; we store the diff
against the *masked* generated file so it survives catalog upgrades,
PDK-profile swaps and per-cell re-renders.

Replaces core/preset.py (single-line anchors, no fallback) and
core/clone_template.py (whole-file fork). Pure Python — no Qt.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from jinja2 import nodes

from auto_ext.core.template import _make_jinja_env

# --- masking -----------------------------------------------------------------

#: ``${name}`` is a variable slot; ``$$`` is a literal ``$``.
#: Safe because render_template() substitutes every $env(X)/${X}/$X away
#: BEFORE Jinja runs, so generated output contains essentially no ``$``.
_MASK_RE = re.compile(r"\$(?:\$|\{([A-Za-z_][A-Za-z0-9_]*)\})")
_WS_RUN_RE = re.compile(r"[ \t]+")

#: A value shorter than this is never masked — too likely to collide with
#: unrelated text and turn a discriminating anchor into a wildcard.
MIN_MASK_LEN = 3
DEFAULT_CTX_LINES = 3
SIMILARITY_MIN_RATIO = 0.80
SIMILARITY_MIN_MARGIN = 0.10
LOW_DISCRIMINATION_MIN_LITERAL_CHARS = 12


def escape_literal(text: str) -> str:
    """Escape a raw string so it round-trips through the mask grammar."""
    return text.replace("$", "$$")


def unmask(masked: str, values: Mapping[str, str], *, missing: str = "keep") -> str:
    """Materialise ``${var}`` slots with current values. ``missing='keep'``
    leaves unknown slots as-is (they can only be matched as wildcards)."""
    def repl(m: re.Match[str]) -> str:
        if m.group(1) is None:
            return "$"
        name = m.group(1)
        if name in values:
            return values[name]
        if missing == "keep":
            return m.group(0)
        raise KeyError(name)
    return _MASK_RE.sub(repl, masked)


def slots_in(masked: str) -> set[str]:
    return {m.group(1) for m in _MASK_RE.finditer(masked) if m.group(1)}


def condition_vars(template_source: str) -> set[str]:
    """Names referenced inside ``[% if %]`` conditions. These MUST be bound
    to their real values during the masked render, otherwise the two renders
    take different branches and stop being line-aligned."""
    env = _make_jinja_env()
    ast = env.parse(template_source)
    out: set[str] = set()
    for if_node in ast.find_all(nodes.If):
        for name in if_node.test.find_all(nodes.Name):
            out.add(name.name)
    return out


def masked_context(
    template_source: str, context: Mapping[str, Any]
) -> dict[str, Any]:
    """Derive the render context that produces the *masked* base.

    A var is masked iff: not used in a ``[% if %]`` condition, stringifies
    to >= MIN_MASK_LEN chars, and contains no newline (a multi-line value
    would break line alignment between the real and masked renders).
    """
    cond = condition_vars(template_source)
    out: dict[str, Any] = {}
    for key, val in context.items():
        if key in cond or val is None or isinstance(val, bool):
            out[key] = val
            continue
        text = str(val)
        if len(text) < MIN_MASK_LEN or "\n" in text:
            out[key] = val
            continue
        out[key] = "${%s}" % key
    return out


def mask_values(
    template_source: str, context: Mapping[str, Any]
) -> dict[str, str]:
    """The inverse map used at apply time: slot name -> current string value."""
    return {
        k: str(v)
        for k, v in context.items()
        if masked_context(template_source, context).get(k) == "${%s}" % k
    }


# --- models (see §4 for the pydantic layer; these are the runtime views) -----


class PatchStatus(str, Enum):
    CLEAN = "clean"            # exact / masked-exact, full context
    SHIFTED = "shifted"        # applied with reduced ctx / wildcards / ws-norm
    REVIEW = "review"          # applied by similarity — needs human sign-off
    ABSORBED = "absorbed"      # catalog now produces `after`; delete the patch
    NOOP = "noop"              # before == after after materialisation
    AMBIGUOUS = "ambiguous"    # >1 candidate site, cannot disambiguate
    LOST = "lost"              # anchor gone entirely
    OVERLAP = "overlap"        # collides with another hunk's resolved range
    DISABLED = "disabled"      # enabled=false


BLOCKING_STATUSES = frozenset(
    {PatchStatus.REVIEW, PatchStatus.AMBIGUOUS,
     PatchStatus.LOST, PatchStatus.OVERLAP}
)
_OK_STATUSES = frozenset(
    {PatchStatus.CLEAN, PatchStatus.SHIFTED, PatchStatus.ABSORBED,
     PatchStatus.NOOP, PatchStatus.DISABLED}
)


class Binding(str, Enum):
    EXACT = "exact"                # every slot -> current value
    CHANGED_WILD = "changed_wild"  # slots whose value drifted -> wildcard
    ALL_WILD = "all_wild"          # every slot -> wildcard


@dataclass(frozen=True)
class Fuzz:
    dropped_context: int = 0
    binding: Binding = Binding.EXACT
    normalized: bool = False
    similarity: float | None = None

    @property
    def is_clean(self) -> bool:
        return (self.dropped_context == 0 and not self.normalized
                and self.similarity is None)

    def describe(self) -> str:
        bits = []
        if self.dropped_context:
            bits.append(f"上下文缩短 {self.dropped_context} 行")
        if self.binding is Binding.CHANGED_WILD:
            bits.append("已变化的变量槽放宽")
        elif self.binding is Binding.ALL_WILD:
            bits.append("全部变量槽放宽")
        if self.normalized:
            bits.append("忽略空白差异")
        if self.similarity is not None:
            bits.append(f"相似度匹配 {self.similarity:.0%}")
        return "、".join(bits) or "精确匹配"


@dataclass
class HunkResolution:
    hunk_id: str
    status: PatchStatus
    fuzz: Fuzz = field(default_factory=Fuzz)
    start: int | None = None          # 0-indexed line, in the NEW base
    end: int | None = None            # half-open
    candidates: list[tuple[int, int]] = field(default_factory=list)
    nearest: tuple[int, int, float] | None = None   # (start, end, ratio)
    expected_text: str = ""           # materialised `before`
    found_text: str = ""              # what is actually there
    tried: list[str] = field(default_factory=list)  # audit trail
    message: str = ""

    @property
    def blocking(self) -> bool:
        return self.status in BLOCKING_STATUSES


@dataclass
class PatchApplyReport:
    patched_text: str
    resolutions: list[HunkResolution]
    fast_path: bool = False

    @property
    def blocking(self) -> bool:
        return any(r.blocking for r in self.resolutions)

    @property
    def counts(self) -> dict[PatchStatus, int]:
        out: dict[PatchStatus, int] = {}
        for r in self.resolutions:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def summary(self) -> str:
        parts = [f"{s.value}={n}" for s, n in sorted(
            self.counts.items(), key=lambda kv: kv[0].value)]
        return f"{len(self.resolutions)} 处手工修改: " + ", ".join(parts)


# --- matching primitives -----------------------------------------------------


def _norm(line: str) -> str:
    return _WS_RUN_RE.sub(" ", line.strip())


def _line_pattern(
    masked_line: str,
    values: Mapping[str, str],
    wildcards: frozenset[str],
    *,
    normalized: bool,
) -> re.Pattern[str]:
    src = _norm(masked_line) if normalized else masked_line
    out: list[str] = []
    pos = 0
    for m in _MASK_RE.finditer(src):
        out.append(re.escape(src[pos:m.start()]))
        name = m.group(1)
        if name is None:
            out.append(re.escape("$"))
        elif name in wildcards or name not in values:
            out.append(r"[^\r\n]*?")
        else:
            out.append(re.escape(values[name]))
        pos = m.end()
    out.append(re.escape(src[pos:]))
    return re.compile("".join(out))


def _literal_char_count(masked_line: str) -> int:
    return len(_MASK_RE.sub("", masked_line).strip())


def _match_seq(
    base_lines: Sequence[str],
    at: int,
    patterns: Sequence[re.Pattern[str]],
    *,
    normalized: bool,
) -> bool:
    if at < 0 or at + len(patterns) > len(base_lines):
        return False
    for off, pat in enumerate(patterns):
        line = base_lines[at + off]
        if not pat.fullmatch(_norm(line) if normalized else line):
            return False
    return True


def _find_sites(
    base_lines: Sequence[str],
    needle: Sequence[str],
    lead: Sequence[str],
    tail: Sequence[str],
    values: Mapping[str, str],
    wildcards: frozenset[str],
    *,
    normalized: bool,
    at_head: bool,
    at_tail: bool,
) -> list[tuple[int, int]]:
    """Return every ``(start, end)`` (needle region only) that matches."""
    mk = lambda ls: [
        _line_pattern(l, values, wildcards, normalized=normalized) for l in ls
    ]
    p_lead, p_need, p_tail = mk(lead), mk(needle), mk(tail)
    n, nl, nt = len(needle), len(lead), len(tail)
    out: list[tuple[int, int]] = []
    lo, hi = nl, len(base_lines) - n - nt
    for s in range(lo, hi + 1):
        if at_head and s - nl != 0:
            continue
        if at_tail and s + n + nt != len(base_lines):
            continue
        if not _match_seq(base_lines, s - nl, p_lead, normalized=normalized):
            continue
        if n and not _match_seq(base_lines, s, p_need, normalized=normalized):
            continue
        if not _match_seq(base_lines, s + n, p_tail, normalized=normalized):
            continue
        out.append((s, s + n))
    return out


def _similarity_scan(
    base_lines: Sequence[str], needle_materialised: Sequence[str]
) -> list[tuple[float, int]]:
    n = max(1, len(needle_materialised))
    target = "".join(_norm(l) + "\n" for l in needle_materialised)
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(target)
    scored: list[tuple[float, int]] = []
    for s in range(0, max(0, len(base_lines) - n) + 1):
        window = "".join(_norm(l) + "\n" for l in base_lines[s:s + n])
        sm.set_seq1(window)
        if sm.real_quick_ratio() < SIMILARITY_MIN_RATIO:
            continue
        if sm.quick_ratio() < SIMILARITY_MIN_RATIO:
            continue
        scored.append((sm.ratio(), s))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored


# --- the ladder --------------------------------------------------------------


def _ladder(
    ctx_full: int,
) -> Iterable[tuple[int, Binding, bool]]:
    """Yield (ctx_lines, binding, normalize) from strictest to loosest."""
    for ctx_n in range(ctx_full, -1, -1):
        for normalized in (False, True):
            for binding in (Binding.EXACT, Binding.CHANGED_WILD,
                            Binding.ALL_WILD):
                yield ctx_n, binding, normalized


def _wildcards_for(
    binding: Binding,
    all_slots: frozenset[str],
    captured: Mapping[str, str],
    current: Mapping[str, str],
) -> frozenset[str]:
    if binding is Binding.EXACT:
        return frozenset()
    if binding is Binding.ALL_WILD:
        return all_slots
    return frozenset(
        s for s in all_slots
        if s not in current or captured.get(s) != current.get(s)
    )


def _discriminating(needle: Sequence[str], ctx_n: int,
                    binding: Binding) -> bool:
    if ctx_n > 0 or binding is not Binding.ALL_WILD:
        return True
    if len(needle) >= 2:
        return True
    return bool(needle) and (
        _literal_char_count(needle[0]) >= LOW_DISCRIMINATION_MIN_LITERAL_CHARS
    )


def _locate(
    base_lines: Sequence[str],
    needle: Sequence[str],
    ctx_before: Sequence[str],
    ctx_after: Sequence[str],
    *,
    values: Mapping[str, str],
    captured: Mapping[str, str],
    occurrence_index: int | None,
    occurrence_count: int | None,
    at_head: bool,
    at_tail: bool,
    tried: list[str],
) -> tuple[tuple[int, int] | None, Fuzz, list[tuple[int, int]]]:
    """Run the ladder. Returns (site|None, fuzz, last_candidate_set)."""
    all_slots = frozenset().union(
        *(slots_in(l) for l in (*needle, *ctx_before, *ctx_after))
    ) if (needle or ctx_before or ctx_after) else frozenset()
    ctx_full = min(len(ctx_before), len(ctx_after)) if (
        needle) else max(len(ctx_before), len(ctx_after))
    ctx_full = max(len(ctx_before), len(ctx_after))
    last: list[tuple[int, int]] = []

    for ctx_n, binding, normalized in _ladder(ctx_full):
        if not needle and ctx_n == 0:
            continue           # pure insertion needs both anchors
        if not _discriminating(needle, ctx_n, binding):
            continue
        lead = list(ctx_before)[len(ctx_before) - min(ctx_n, len(ctx_before)):]
        tail = list(ctx_after)[:min(ctx_n, len(ctx_after))]
        wild = _wildcards_for(binding, all_slots, captured, values)
        sites = _find_sites(
            base_lines, needle, lead, tail, values, wild,
            normalized=normalized, at_head=at_head and ctx_n >= len(ctx_before),
            at_tail=at_tail and ctx_n >= len(ctx_after),
        )
        tried.append(
            f"ctx={ctx_n} binding={binding.value} "
            f"norm={int(normalized)} -> {len(sites)}"
        )
        last = sites or last
        if not sites:
            continue
        fuzz = Fuzz(
            dropped_context=ctx_full - ctx_n,
            binding=binding,
            normalized=normalized,
        )
        if len(sites) == 1:
            return sites[0], fuzz, sites
        if (occurrence_count is not None
                and occurrence_index is not None
                and len(sites) == occurrence_count):
            # Same shape as capture time — trust the recorded ordinal.
            return sites[occurrence_index], Fuzz(
                dropped_context=max(1, fuzz.dropped_context),
                binding=fuzz.binding, normalized=fuzz.normalized,
            ), sites
    return None, Fuzz(), last


# --- public: apply -----------------------------------------------------------


def apply_patch(
    base_text: str,
    patch: "TemplatePatch",
    values: Mapping[str, str],
    *,
    base_masked_text: str | None = None,
) -> PatchApplyReport:
    """Apply ``patch`` to a freshly generated ``base_text``.

    ``values`` maps slot name -> current rendered string value (from
    :func:`mask_values`). ``base_masked_text`` enables the fast path.
    """
    base_text = base_text.replace("\r\n", "\n")
    base_lines = base_text.splitlines(keepends=True)

    if (base_masked_text is not None
            and _sha256(base_masked_text) == patch.base.masked_sha256):
        return _apply_fast_path(base_lines, patch, values)

    resolutions: list[HunkResolution] = []
    edits: list[tuple[int, int, list[str], HunkResolution]] = []

    for hunk in patch.hunks:
        res, edit = _resolve_hunk(base_lines, hunk, values)
        resolutions.append(res)
        if edit is not None:
            edits.append((*edit, res))

    # Overlap detection across resolved ranges.
    edits.sort(key=lambda e: (e[0], e[1]))
    for (s1, e1, _, r1), (s2, e2, _, r2) in zip(edits, edits[1:]):
        if s2 < e1:
            for r in (r1, r2):
                r.status = PatchStatus.OVERLAP
                r.message = (
                    f"与另一处手工修改的落点重叠："
                    f"行 {s1 + 1}-{e1} 与 行 {s2 + 1}-{e2}"
                )
    edits = [e for e in edits if e[3].status not in
             (PatchStatus.OVERLAP, PatchStatus.ABSORBED, PatchStatus.NOOP)]

    out = list(base_lines)
    for start, end, replacement, _res in sorted(edits, key=lambda e: -e[0]):
        out[start:end] = replacement
    return PatchApplyReport("".join(out), resolutions)


def _resolve_hunk(
    base_lines: Sequence[str],
    hunk: "PatchHunk",
    values: Mapping[str, str],
) -> tuple[HunkResolution, tuple[int, int, list[str]] | None]:
    res = HunkResolution(hunk_id=hunk.id, status=PatchStatus.LOST)
    if not hunk.enabled:
        res.status = PatchStatus.DISABLED
        return res, None

    before = hunk.before_lines
    after = hunk.after_lines
    mat_before = [unmask(l, values) for l in before]
    mat_after = [unmask(l, values) for l in after]
    res.expected_text = "".join(mat_before)

    if mat_before == mat_after:
        res.status = PatchStatus.NOOP
        res.message = "变量取值已收敛，这条修改现在不产生任何差异，建议删除。"
        return res, None

    site, fuzz, cands = _locate(
        base_lines, before, hunk.context_before_lines, hunk.context_after_lines,
        values=values, captured=hunk.captured_values,
        occurrence_index=hunk.occurrence_index,
        occurrence_count=hunk.occurrence_count,
        at_head=hunk.anchored_at_head, at_tail=hunk.anchored_at_tail,
        tried=res.tried,
    )

    if site is not None:
        start, end = site
        res.start, res.end, res.fuzz = start, end, fuzz
        res.status = PatchStatus.CLEAN if fuzz.is_clean else PatchStatus.SHIFTED
        res.found_text = "".join(base_lines[start:end])
        repl = _rebase_indent(mat_after, base_lines[start:end], mat_before) \
            if fuzz.normalized else mat_after
        return res, (start, end, repl)

    # `before` gone — has the catalog absorbed it?
    absorbed_site, absorbed_fuzz, _ = _locate(
        base_lines, after, hunk.context_before_lines, hunk.context_after_lines,
        values=values, captured=hunk.captured_values,
        occurrence_index=hunk.occurrence_index,
        occurrence_count=hunk.occurrence_count,
        at_head=hunk.anchored_at_head, at_tail=hunk.anchored_at_tail,
        tried=res.tried,
    )
    if absorbed_site is not None and absorbed_fuzz.is_clean:
        res.status = PatchStatus.ABSORBED
        res.start, res.end = absorbed_site
        res.message = (
            "catalog 现在已经直接产出你要的内容，这条手工修改已转正，可以删除。"
        )
        return res, None

    if len(cands) > 1:
        res.status = PatchStatus.AMBIGUOUS
        res.candidates = cands
        res.message = f"锚点有歧义：{len(cands)} 处都能匹配，无法自动选择。"
        return res, None

    # Similarity fallback.
    scored = _similarity_scan(base_lines, mat_before)
    if scored:
        best_ratio, best_start = scored[0]
        n = max(1, len(mat_before))
        second = next(
            (r for r, s in scored[1:] if abs(s - best_start) >= n), 0.0
        )
        res.nearest = (best_start, best_start + n, best_ratio)
        if (best_ratio >= SIMILARITY_MIN_RATIO
                and best_ratio - second >= SIMILARITY_MIN_MARGIN):
            res.status = PatchStatus.REVIEW
            res.start, res.end = best_start, best_start + n
            res.fuzz = Fuzz(dropped_context=len(hunk.context_before_lines),
                            binding=Binding.ALL_WILD, normalized=True,
                            similarity=best_ratio)
            res.found_text = "".join(base_lines[best_start:best_start + n])
            res.message = (
                f"生成内容已改变，只能按 {best_ratio:.0%} 相似度定位。"
                f"请人工确认落点后再运行。"
            )
            return res, (best_start, best_start + n, mat_after)

    res.status = PatchStatus.LOST
    res.message = (
        "锚点彻底消失：新生成的内容里找不到这条修改对应的位置。"
        + (f" 最接近的一段在行 {res.nearest[0] + 1}"
           f"（相似度 {res.nearest[2]:.0%}）。" if res.nearest else "")
    )
    return res, None


def _rebase_indent(
    after: Sequence[str], found: Sequence[str], expected: Sequence[str]
) -> list[str]:
    """Whitespace-normalised match: re-apply the found region's indentation."""
    if not after or not found or not expected:
        return list(after)
    lead = lambda s: len(s) - len(s.lstrip(" \t"))
    delta = lead(found[0]) - lead(expected[0])
    if delta == 0:
        return list(after)
    if delta > 0:
        return [" " * delta + l for l in after]
    return [l[min(-delta, lead(l)):] for l in after]


def _apply_fast_path(
    base_lines: Sequence[str], patch: "TemplatePatch", values: Mapping[str, str]
) -> PatchApplyReport:
    out = list(base_lines)
    resolutions: list[HunkResolution] = []
    for hunk in sorted(patch.hunks, key=lambda h: -(h.recorded_start or 0)):
        if not hunk.enabled:
            resolutions.append(
                HunkResolution(hunk.id, PatchStatus.DISABLED))
            continue
        s = hunk.recorded_start
        e = s + len(hunk.before_lines)
        out[s:e] = [unmask(l, values) for l in hunk.after_lines]
        resolutions.append(
            HunkResolution(hunk.id, PatchStatus.CLEAN, start=s, end=e))
    resolutions.reverse()
    return PatchApplyReport("".join(out), resolutions, fast_path=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- public: capture ---------------------------------------------------------


def capture_patch(
    *,
    template_source: str,
    template_sha256: str,
    stage: str,
    template_id: str,
    profile_id: str | None,
    catalog_version: str | None,
    base_real: str,
    base_masked: str,
    edited_real: str,
    values: Mapping[str, str],
    intents: Mapping[int, str] | None = None,
    ctx_lines: int = DEFAULT_CTX_LINES,
    existing: "TemplatePatch | None" = None,
) -> "TemplatePatch":
    """Turn a user edit of the generated file into a masked patch.

    ``base_real`` and ``base_masked`` MUST be line-aligned — guaranteed by
    :func:`masked_context` refusing to mask multi-line values and binding
    every ``[% if %]`` condition variable to its real value.
    """
    base_real = base_real.replace("\r\n", "\n")
    edited_real = edited_real.replace("\r\n", "\n")
    real_lines = base_real.splitlines(keepends=True)
    masked_lines = base_masked.splitlines(keepends=True)
    if len(real_lines) != len(masked_lines):
        raise ValueError(
            "real/masked renders are not line-aligned; a context value most "
            "likely contains a newline or drives an [% if %] branch"
        )
    edit_lines = edited_real.splitlines(keepends=True)

    sm = difflib.SequenceMatcher(a=real_lines, b=edit_lines, autojunk=False)
    hunks: list[PatchHunk] = []
    prev_by_index = {h.id: h for h in (existing.hunks if existing else [])}

    for k, (tag, i1, i2, j1, j2) in enumerate(
        [op for op in sm.get_opcodes() if op[0] != "equal"]
    ):
        before_masked = masked_lines[i1:i2]
        after_raw = edit_lines[j1:j2]
        after_masked = _remask_edit(after_raw, before_masked, values)
        lead = masked_lines[max(0, i1 - ctx_lines):i1]
        tail = masked_lines[i2:i2 + ctx_lines]
        n_sites = len(_find_sites(
            masked_lines, before_masked, lead, tail, {}, frozenset(),
            normalized=False, at_head=False, at_tail=False,
        ))
        idx = _find_sites(
            masked_lines, before_masked, lead, tail, {}, frozenset(),
            normalized=False, at_head=False, at_tail=False,
        ).index((i1, i2)) if n_sites else 0
        hunks.append(PatchHunk(
            id=uuid.uuid4().hex[:8],
            intent=(intents or {}).get(k, ""),
            before="".join(before_masked),
            after="".join(after_masked),
            context_before="".join(lead),
            context_after="".join(tail),
            anchored_at_head=(i1 - len(lead) == 0),
            anchored_at_tail=(i2 + len(tail) == len(masked_lines)),
            occurrence_index=idx,
            occurrence_count=n_sites,
            captured_values={
                s: str(values[s]) for s in
                slots_in("".join((*before_masked, *lead, *tail, *after_masked)))
                if s in values
            },
            recorded_start=i1,
        ))

    return TemplatePatch(
        stage=stage,
        template_id=template_id,
        base=BaseFingerprint(
            template_sha256=template_sha256,
            catalog_version=catalog_version,
            profile_id=profile_id,
            masked_sha256=_sha256(base_masked),
            captured_at=datetime.now(timezone.utc),
        ),
        hunks=hunks,
        on_fuzzy=(existing.on_fuzzy if existing else FuzzyPolicy.BLOCK),
    )


def _remask_edit(
    after_raw: Sequence[str],
    before_masked: Sequence[str],
    values: Mapping[str, str],
) -> list[str]:
    """Re-mask the user's typed lines.

    Deliberately narrow scope: only substitute values for slots that already
    appear in this hunk's ``before``, longest value first. Anything else the
    user typed stays literal — that is what they meant.
    """
    candidates = sorted(
        (s for s in slots_in("".join(before_masked)) if s in values),
        key=lambda s: -len(values[s]),
    )
    out: list[str] = []
    for line in after_raw:
        text = escape_literal(line)
        for name in candidates:
            val = escape_literal(values[name])
            if len(val) >= MIN_MASK_LEN:
                text = text.replace(val, "${%s}" % name)
        out.append(text)
    return out


# --- display -----------------------------------------------------------------


def render_hunk_as_udiff(
    hunk: "PatchHunk", values: Mapping[str, str], *, label: str = ""
) -> str:
    """Unified diff for the UI / run.json. Storage format stays masked."""
    a = [unmask(l, values) for l in hunk.before_lines]
    b = [unmask(l, values) for l in hunk.after_lines]
    return "".join(difflib.unified_diff(
        a, b, fromfile=f"generated{label}", tofile=f"patched{label}", n=0
    ))
```

---

## 3. 冲突时的 UX 契约

### 3.1 Recipe 编辑器：常驻的"手工修改"区

Recipe 面板底部一个可折叠区，标题 **`手工修改 (3)`**。徽章颜色取最坏状态。每条一行：

```
● 7a3e91c4  quantus/ext.cmd   corner 强制 CBEST                    ✓ 已应用
● 2c04f8ab  calibre/lvs.qci   加 *lvsIgnorePorts                   ↕ 已应用（上下文缩短 2 行）
● 9d17b0e2  quantus/ext.cmd   加 -extra_netlist                    ⚠ 需确认（相似度 84%）
● 55ae3c10  si/default.env    simLibName 覆盖                      ✓✓ 已转正 · 可删除
● f0b2d9a7  jivaro/default.xml 关掉 auto_freq                      ✗ 锚点丢失
```

展开一条给四块内容：

1. **意图**（`intent`，用户自己写的话，可编辑）。
2. **unified diff**：`generated → patched`，用**当前**变量值物化。旁边一个 `显示掩码原文` 开关，切到 `${cell}` 形式，让用户看懂"为什么换 cell 还能用"。
3. **落点**：新 base 里的行号区间 + 前后 3 行实际内容。冲突态下这里显示三栏：`期望的（物化 before）` / `实际找到的` / `算法认为最接近的一段`。
4. **诊断**：`fuzz.describe()` 的中文串 + `tried` 审计链（`ctx=3 binding=exact norm=0 -> 0` 这类，折叠在"详情"里）。冲突态额外显示 `captured_values` 与当前值的对照表，直接指出 `qrc_deck_dir: /pdk/tsmc22ull/... → /pdk/tsmc16ffc/...`。

### 3.2 每条可做的操作

| 操作 | 语义 |
|---|---|
| `还原这一条` | 删掉这个 hunk。 |
| `全部还原` | 清空 `patches`，回到纯 catalog 产出。 |
| `暂时停用` | `enabled=false`。保留记录不应用 —— 用于二分定位"是不是我的补丁把这次跑挂了"。 |
| `重新锚定` | 打开新 base，用户点选正确区间，用它重写 `before`/`context_*`，`after`/`intent` 原样保留。**冲突态的主要出路。** |
| `以当前结果为准（重采集）` | 把当前打完补丁的文本当作新的"已编辑结果"，对新 base 重跑 `capture_patch`。会把 `REVIEW` 降级回 `CLEAN`。 |
| `删除（已转正）` | 仅 `ABSORBED`/`NOOP` 态出现，一键删。 |
| `申请并入 catalog` | 导出这条 hunk 的 udiff + intent + 命中的模板 id，作为"这个改动应该做成 catalog 默认值"的素材。这是通往 `ABSORBED` 的正规路径，也是逃生舱不会无限膨胀的机制。 |

### 3.3 运行时契约（硬规则）

| 最坏状态 | 行为 |
|---|---|
| `CLEAN` / `NOOP` / `DISABLED` | 正常跑。 |
| `ABSORBED` | 正常跑；run 结束后在 run card 上提示"有 N 条修改已转正，可清理"。 |
| `SHIFTED` | 正常跑，但 run card 打黄色 caution 徽章，`run.json.patch_report[]` 记录每条的 `fuzz`。 |
| `REVIEW` / `AMBIGUOUS` / `LOST` / `OVERLAP` | **该 stage 拒绝启动**，整个 batch 在开跑前就失败并给出冲突报告。 |

关于阻塞态：**运行对话框里不提供"仍然运行"按钮。** 两个理由。其一，batch 通常是晚上挂着跑的，弹窗等人点 = 整批卡死到第二天。其二，"跑一半用了错 deck"的代价（几小时机时 + 一份看不出问题的错 netlist 流到后续仿真）远高于"没跑"。要放行必须回 Recipe 编辑器显式解决，或者在 recipe 里写死 `on_fuzzy: accept` —— 那是一个留痕的、需要打字的、review 时看得见的决定，不是一个半夜的误点击。

### 3.4 归档契约

每次 run 无条件写入：

- `runs/<id>/rendered/<template>.generated` —— **打补丁前**的 catalog 产出
- `runs/<id>/rendered/<template>` —— 实际交给 EDA 二进制的文件
- `runs/<id>/run.json` 的 `patch_report`：每条 hunk 的 `id` / `intent` / `status` / `fuzz.describe()` / 落点行号 / udiff

事后"这次到底用的什么配置"必须能在不依赖任何外部状态的前提下答出来。这也是 `Run` 存 recipe **快照而非引用**的同一条原则的延伸。

### 3.5 CLI 对等面

```
auto_ext recipe patch list <recipe>
auto_ext recipe patch show <recipe> <hunk-id> [--masked]
auto_ext recipe patch check <recipe> --profile <id> [--cell <lib/cell>]   # 干跑整个阶梯，退出码 = 是否阻塞
auto_ext recipe patch disable|enable|drop <recipe> <hunk-id>
auto_ext recipe patch recapture <recipe> <template-id>
```

`check` 进 CI / pre-run hook：换 PDK 或升 catalog 之后先跑一遍，冲突在开工前暴露而不是在 batch 里。

---

## 4. 数据结构（pydantic v2）

```python
# auto_ext/core/patch_models.py  (or the top of core/patch.py)
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Stage(str, Enum):
    SI = "si"
    STRMOUT = "strmout"
    CALIBRE = "calibre"
    QUANTUS = "quantus"
    JIVARO = "jivaro"


class FuzzyPolicy(str, Enum):
    BLOCK = "block"     # default: a REVIEW hunk refuses the run
    ACCEPT = "accept"   # explicit opt-in; still recorded in run.json


class BaseFingerprint(BaseModel):
    """What the patch was captured against. Enables the fast path and
    turns 'why did this stop matching' into a diffable answer."""
    model_config = ConfigDict(extra="forbid")

    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    masked_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_version: str | None = None
    profile_id: str | None = None
    captured_at: datetime


class PatchHunk(BaseModel):
    """One contiguous manual edit, expressed in MASKED space.

    ``before`` / ``after`` / ``context_*`` are stored as whole strings so
    ruamel emits them as YAML block scalars (readable + hand-editable);
    the ``*_lines`` properties give the keepends line lists the matcher
    wants. ``${var}`` marks a Jinja-variable slot, ``$$`` a literal ``$``.
    """
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f]{8}$")
    enabled: bool = True
    intent: str = ""

    before: str = ""
    after: str = ""
    context_before: str = ""
    context_after: str = ""

    anchored_at_head: bool = False
    anchored_at_tail: bool = False

    #: "this was match k of n at capture time" — a cheap, very effective
    #: disambiguator for repeated structures (two output_db blocks, ...).
    occurrence_index: int | None = Field(default=None, ge=0)
    occurrence_count: int | None = Field(default=None, ge=1)

    #: slot -> value at capture time. Powers the CHANGED_WILD tier and the
    #: "qrc_deck_dir: /pdk/A → /pdk/B" diagnostic table in the UI.
    captured_values: dict[str, str] = Field(default_factory=dict)

    #: 0-indexed line in the capture-time base. ONLY used by the fast path
    #: (guarded by masked_sha256); never trusted by the ladder.
    recorded_start: int = Field(default=0, ge=0)

    # ---- derived ----
    @property
    def before_lines(self) -> list[str]:
        return self.before.splitlines(keepends=True)

    @property
    def after_lines(self) -> list[str]:
        return self.after.splitlines(keepends=True)

    @property
    def context_before_lines(self) -> list[str]:
        return self.context_before.splitlines(keepends=True)

    @property
    def context_after_lines(self) -> list[str]:
        return self.context_after.splitlines(keepends=True)

    @field_validator("before", "after", "context_before", "context_after")
    @classmethod
    def _no_crlf(cls, v: str) -> str:
        if "\r" in v:
            raise ValueError("patch text must use LF line endings only")
        return v

    @model_validator(mode="after")
    def _check(self) -> "PatchHunk":
        if not self.before and not self.after:
            raise ValueError("hunk is empty on both sides")
        if not self.before and not (self.context_before and self.context_after):
            raise ValueError(
                "a pure-insertion hunk needs BOTH context anchors to be placed"
            )
        if (self.occurrence_index is not None
                and self.occurrence_count is not None
                and self.occurrence_index >= self.occurrence_count):
            raise ValueError("occurrence_index out of range")
        return self


class TemplatePatch(BaseModel):
    """All manual edits a recipe makes to ONE generated file.

    A patch is per-(recipe, template), NOT per-cell: it is applied to every
    DUT's render. That is only sound because the stored text is masked —
    per-cell values live in ${slots}, not in the patch.
    """
    model_config = ConfigDict(extra="forbid")

    stage: Stage
    template_id: str = Field(pattern=r"^[a-z0-9_]+/[A-Za-z0-9_.-]+$")
    base: BaseFingerprint
    hunks: list[PatchHunk] = Field(default_factory=list)
    on_fuzzy: FuzzyPolicy = FuzzyPolicy.BLOCK

    @model_validator(mode="after")
    def _unique_ids(self) -> "TemplatePatch":
        ids = [h.id for h in self.hunks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate hunk id")
        return self

    @property
    def enabled_count(self) -> int:
        return sum(1 for h in self.hunks if h.enabled)
```

### 嵌进 Recipe

```python
class Recipe(BaseModel):
    """Globally shared, semantic, cross-project portable. Binds NO PDK paths
    and NO cell identity — that is what makes it portable, and what makes
    patches[] safe to apply across every DUT."""
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str
    description: str = ""

    extraction: ExtractionSettings   # type / corner / temperature / thresholds
    output: OutputSettings           # extracted_view | dspf | both
    lvs: LvsSettings                 # deck variant / connect_by_name
    reduction: ReductionSettings     # jivaro on/off / frequency / error

    #: The escape hatch. Everything the semantic fields above cannot express.
    #: UI surfaces this as "此配方有 N 处手工修改".
    patches: list[TemplatePatch] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_patch_per_template(self) -> "Recipe":
        keys = [(p.stage, p.template_id) for p in self.patches]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "at most one TemplatePatch per (stage, template_id); "
                "merge the hunks instead"
            )
        return self

    @property
    def manual_edit_count(self) -> int:
        return sum(p.enabled_count for p in self.patches)
```

### Run 侧的记录

```python
class HunkOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hunk_id: str
    intent: str
    status: PatchStatus
    fuzz: str                       # Fuzz.describe(), already localised
    start_line: int | None = None
    end_line: int | None = None
    udiff: str = ""


class StagePatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Stage
    template_id: str
    fast_path: bool
    outcomes: list[HunkOutcome] = Field(default_factory=list)
    blocked: bool = False
```

`Run.run.json` 里 `patch_reports: list[StagePatchReport]`，与 recipe 快照并列。

### 与 runner 的接线点

`Tool.render_template()` 目前是 `render → write`。改成三段，其余 stage 编排/取消/并行 workdir 逻辑不动：

```python
base_real   = render_template(tpl_path, context, env)
base_masked = render_template(tpl_path, masked_context(src, context), env)
report      = apply_patch(base_real, patch, mask_values(src, context),
                          base_masked_text=base_masked)
if report.blocking and patch.on_fuzzy is FuzzyPolicy.BLOCK:
    raise PatchConflictError(report)          # stage 开跑前失败
out_path.write_text(report.patched_text, encoding="utf-8")
(out_path.parent / (out_path.name + ".generated")).write_text(base_real, "utf-8")
```

多一次 Jinja 渲染，对几十 KB 的文本可以忽略。

---

## 5. 必须覆盖的测试场景

统一 fixture：`ext.cmd.j2` 的 `process_technology` 段（真实模板尾部），context 至少含 `cell`、`library`、`out_file`、`tech_name`、`qrc_deck_dir`、`temperature`、`ground_net`。

---

**T1 — 换 cell（同 recipe 跑 batch 里第二个 DUT）**
- 输入：patch 采集自 `cell=pll_top, out_file=pll_top_ext`；应用到 `cell=vco_core, out_file=vco_core_ext` 的生成结果。hunk 的 `context_before` 含 `-design_cell_name "${cell} ${lvs_layout_view} ${library}"`。
- 期望：`masked_sha256` 命中 → `fast_path=True`，状态 `CLEAN`，`fuzz.is_clean`，`patched_text` 里 `after` 用**新** cell 值物化。
- 反例断言：把 `capture_patch` 里的掩码关掉（存字面 `pll_top`），同一用例必须变成 `LOST` —— 这条断言就是"为什么不能用 unified diff"的可执行证明。

**T2 — catalog 在补丁上方插入新 directive（纯行号漂移）**
- 输入：新 base 在 `capacitance` 段前多了 4 行 `#comment` + 一条新 `-decoupling_factor`。补丁区域字节不变。
- 期望：`fast_path=False`（masked_sha256 变了），ladder 在 `ctx=3, EXACT, norm=off` 首层唯一命中，状态 `CLEAN`，`start` = 旧 start + 4。

**T3 — 换 PDK Profile，上下文行里的路径变了**
- 输入：`qrc_deck_dir` 从 `/pdk/tsmc22ull/qrc` 变成 `/pdk/tsmc16ffc/qrc`；`tech_name` 从 `tsmc22ull_1p10m` 变成 `tsmc16ffc_1p13m`。补丁的 `context_after` 含 `-technology_name "${tech_name}"`。
- 期望：`EXACT` 层失败（`values` 里已是新值 → 其实 EXACT 就用新值，应该直接命中）。**明确断言 `EXACT` 层即命中**、状态 `CLEAN` —— 因为 `unmask` 用的是当前 values，掩码槽根本不参与"变没变"的判断。这正是掩码格式的核心收益，必须有一条测试把它钉死。
- 补充断言：`captured_values` 仍是旧值，UI 诊断表能给出 `tech_name: tsmc22ull_1p10m → tsmc16ffc_1p13m`。

**T4 — catalog 把写死的字面量参数化了（最棘手的一类）**
- 输入：补丁把 `"TYPICAL"` 改成 `"CBEST"`。新 catalog 模板改成 `"[[corner]]"`，profile 给 `corner=RCWORST`，新 base 里那行是 `"RCWORST" \`。
- 期望：`before`（掩码文本是字面 `"TYPICAL" \`，因为采集时它就是字面量）在全部 4 层失败 → ABSORBED 探针（needle=`"CBEST" \`）也失败 → 相似度层：`ratio("\"TYPICAL\" \\", "\"RCWORST\" \\") ≈ 0.7`，**低于 0.80** → 状态 `LOST`，`nearest` 指向那一行并给出 ratio。
- UX 断言：报告 message 含"锚点彻底消失"+ nearest 行号；`report.blocking is True`。
- 附加断言：把 hunk 的 context 保留 3 行（`process_technology \` / `-technology_corner \`）时，如果改用 `ctx=2 + ALL_WILD`，`before` 仍是字面 TYPICAL 所以还是不匹配 —— 确认算法**不会**把它错配成 CLEAN。这条是"宁可 LOST 不可乱打"的守卫测试。

**T5 — catalog 转正**
- 输入：补丁 `before="TYPICAL"` / `after="CBEST"`。新 catalog 模板本身就产出 `"CBEST" \`。
- 期望：`before` ladder 全灭 → ABSORBED 探针在 `ctx=full, EXACT, norm=off` 唯一命中 → 状态 `ABSORBED`，`patched_text == base_text`（一字节不改），`blocking is False`，`message` 含"已转正"。

**T6 — Quantus GUI 重导出改了续行缩进**
- 输入：新 base 把 `ext.cmd` 里 14 空格缩进改成 8 空格（对所有续行）。补丁区域语义不变。
- 期望：`norm=off` 全层失败 → `ctx=3, EXACT, norm=on` 命中 → 状态 `SHIFTED`，`fuzz.normalized is True`，`fuzz.dropped_context == 0`。
- 关键断言：`_rebase_indent` 生效 —— `patched_text` 里 `after` 行的缩进是 **8** 空格（跟随新 base），不是补丁里存的 14。且 patched 文件整体缩进一致（不能出现 14/8 混排）。

**T7 — 重复结构的歧义，靠 occurrence 消歧**
- 输入：`.cmd` 里有两个结构相同的 `output_db` 段（`extracted_view` + `dspf`，recipe 选了 "两者"）。补丁改的是第 2 个（`occurrence_index=1, occurrence_count=2`）。新 base 里两个段都在、都还匹配。
- 期望 A：ladder 在首层拿到 2 个候选，`len(sites)==occurrence_count` → 取 `sites[1]`，状态 `SHIFTED`（不是 CLEAN —— 用了序号消歧就必须留痕），`start` 落在第二个段。
- 期望 B（同一用例的变体）：把 `occurrence_count` 改成 `None`（模拟旧数据），或新 base 里变成 3 个段 → 状态 `AMBIGUOUS`，`candidates` 长度 == 实际候选数，`blocking is True`，不写任何文本。

**T8 — 两条 hunk 落点重叠**
- 输入：hunk A 改 `filter_cap` 段第 2–3 行，hunk B 改第 3–4 行（在新 base 上因为上方行被删而各自漂移到重叠）。
- 期望：两条各自都能定位，但重叠检测把**两条都**置为 `OVERLAP`，`message` 含双方行号区间，`patched_text` 里**两条都不打**（不能只打一条），`blocking is True`。

**T9 — 纯插入 hunk，锚点仍在 / 锚点断开**
- 输入 A：`before=""`，`after="              -extra_netlist \"${cell}_extra.sp\" \\\n"`，两侧锚点各 3 行，新 base 里锚点仍相邻。
- 期望 A：命中，`CLEAN`，插入位置在 `context_before` 之后，`after` 里的 `${cell}` 用**当前** cell 物化。
- 输入 B：catalog 在两个锚点**之间**插了一行新 directive。
- 期望 B：`ctx=3/2/1` 全灭（needle 空 → `ctx==0` 被 `_ladder` 跳过）→ 相似度层因 `before` 为空跳过 → 状态 `LOST`。构造模型时 `before="" and not (context_before and context_after)` 必须触发 `ValidationError`。

**T10 — `after` 侧含变量，必须跟着新值走**
- 输入：`after` 掩码文本为 `-cdl_out_map_directory "${output_dir}/patched/"`。采集时 `output_dir=/work/pll_top`，应用时 `output_dir=/work/vco_core`。
- 期望：`patched_text` 含 `/work/vco_core/patched/`，**不含** `/work/pll_top`。
- 配套：`_remask_edit` 单测 —— 用户手打 `"/work/pll_top/patched/"`，`before` 块含 `${output_dir}` 槽 → 被重新掩码成 `${output_dir}/patched/`；而用户手打的 `"CBEST"`（不是任何 slot 的当前值）保持字面。

**T11 — NOOP：变量取值收敛**
- 输入：补丁把 `-temperature 25` 改成 `-temperature 85`；后来用户在 recipe 里把温度语义值也设成 85，新 base 直接产出 85。掩码后 `before` 是 `-temperature ${temperature}`，`after` 也被 `_remask_edit` 掩成 `-temperature ${temperature}`（因为 85 == 当前 temperature 值）。
- 期望：物化后 `before == after` → 状态 `NOOP`，`patched_text == base_text`，`message` 提示"建议删除"，`blocking is False`。
- （这条同时是对 `_remask_edit` 掩码过度的一个刻意暴露：它把用户的字面 85 掩成了变量。断言 UI 有"保持字面"开关时，`_remask_edit` 的 `keep_literal={"temperature"}` 参数能让状态回到 `CLEAN`。）

**T12 — 相似度层的接受与拒绝边界**
- 输入 A：新 base 把补丁那行的一个参数名从 `-exclude_floating_nets_limit` 改成 `-exclude_floating_net_limit`（去了个 s），其余不变，且上下文也小改到 EXACT/WILD 层全灭。相似度 ≈ 0.97，次优 < 0.6。
- 期望 A：状态 `REVIEW`，`fuzz.similarity ≈ 0.97`，文本**已打上**（编辑器可预览），但 `report.blocking is True`；`on_fuzzy=ACCEPT` 时 `patch.on_fuzzy` 让 runner 放行且 `run.json` 记录 `status=review`。
- 输入 B：`.qci` 里存在两段几乎一样的 `*cmnLSFSlaveTbl` / `*cmnGridSlaveTbl`（真实模板里就有），best 0.93 / second 0.91，margin 0.02 < 0.10。
- 期望 B：**拒绝**相似度匹配 → 状态 `LOST`（不是 REVIEW），`nearest` 给出 best 的位置和 ratio 供人参考。这条钉死 margin 守卫。

**T13 — `$` 转义往返**
- 输入：生成结果里含字面 `$`（构造一个 net 名叫 `A$B` 或注释里带 `$`）。
- 期望：`escape_literal` → `unmask` 恒等；`_line_pattern` 把 `$$` 编译成匹配单个 `$` 的字面；hunk 模型 round-trip 过 ruamel YAML 后字节不变。property test：随机 ASCII 文本 `unmask(escape_literal(t), {}) == t`。

**T14 — 真实/掩码渲染的行对齐守卫**
- 输入 A：某个 context 变量的值含 `\n`（例如从 project.yaml 误配的多行字符串）。
- 期望 A：`masked_context` 不掩它（绑真值），`capture_patch` 正常工作，该变量在 patch 里以字面形式出现。
- 输入 B：人为构造一个模板，`[% if connect_by_name %]` 的 `connect_by_name` 被错误地掩成 `"${connect_by_name}"`（真值字符串 → Jinja 判 truthy），导致掩码渲染走了不同分支、行数不同。
- 期望 B：`capture_patch` 抛 `ValueError("real/masked renders are not line-aligned")`，且 `condition_vars()` 单测证明 `connect_by_name` 在正常路径下**必定**被 `masked_context` 排除。

**T15 — 快路径与慢路径产出必须一致**
- 输入：同一 patch、同一 base，一次传 `base_masked_text`（走快路径），一次传 `None`（走 ladder）。
- 期望：两次 `patched_text` 逐字节相同；快路径 `fast_path=True` 且所有 `fuzz.is_clean`。这是防止快路径的 `recorded_start` 悄悄腐烂的回归闸。