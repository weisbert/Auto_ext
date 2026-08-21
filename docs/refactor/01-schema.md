# Auto_ext 重构：PdkProfile / Recipe / RunRecord schema + 迁移路径

---

## 0. 落盘布局（后面所有小节都引用它）

```
<auto_ext_root>/
  config/
    workspace.yaml            # project.yaml 的残余，5 个键（见 §1.4）
    cells.yaml                # tasks.yaml 的残余，纯表格
    profiles/
      HN001.yaml              # PdkProfile，扫描发现/生成
      HN001.health.json       # 体检结果缓存，gitignore
  recipes/                    # Recipe 搜索路径之一（见 §1.2 末尾）
    rc-coupled-typical.yaml
  templates/                  # catalog，我们维护，用户不再日常编辑
  runs/
    20260821T143205Z_amp2-rc-coupled-typical/
      run.json                # RunRecord，finalize 后不可变
      events.jsonl            # 运行中追加，finalize 后只作审计
      annotations.json        # 用户可改：display_name / note / tags（唯一可变文件）
      rendered/               # si.env / lvs.qci / ext.cmd / dspf.cmd / jivaro.xml
      logs/                   # si.log strmout.log calibre.log quantus.ext.log ...
      results/                # lvs.report 副本 + lvs_summary.json
      work/                   # 并行隔离 cwd（cds.lib / .cdsinit 软链 + si.env）
    batches/
      20260821T143200Z_nightly.json
    latest -> 20260821T143205Z_amp2-rc-coupled-typical   # POSIX 软链，best-effort
```

顶层 `logs/task_<id>/` 与 `runs/task_<id>/` **整体消失**。

---

## 1. 三个 pydantic v2 模型

### 1.0 共享类型

```python
# auto_ext/model/common.py
"""新对象模型的共享基类与枚举。取代 core/manifest.py 的 knob 类型系统。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

#: 标识符风格的短名：recipe_id / profile_id / corner name / check_id。
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=64)]

#: 路径表达式。语法沿用 core/env.py 现有的两层文法，不做改动：
#:   env 引用 ``$X`` / ``${X}`` / ``$env(X)``（substitute_env）
#:   可选后缀过滤器 ``|parent``（resolve_path_expr）
#: 即 ProjectConfig.paths 的值语法原封不动搬过来。
PathExpr = Annotated[str, StringConstraints(min_length=1)]


class Base(BaseModel):
    """所有可编辑配置对象的基类：未知键直接报错，不静默吞掉。"""
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Frozen(BaseModel):
    """所有不可变记录对象（RunRecord 及其子对象）的基类。"""
    model_config = ConfigDict(extra="forbid", frozen=True)


class Stage(StrEnum):
    """搬自 runner.STAGE_ORDER，语义不变。"""
    SI = "si"
    STRMOUT = "strmout"
    CALIBRE = "calibre"
    QUANTUS = "quantus"
    JIVARO = "jivaro"


STAGE_ORDER: tuple[Stage, ...] = (
    Stage.SI, Stage.STRMOUT, Stage.CALIBRE, Stage.QUANTUS, Stage.JIVARO,
)


class RenderTarget(StrEnum):
    """一次运行可能生成的渲染产物。取代 ProjectConfig.templates 的四个绑定 slot：
    模板不再由用户指定路径，而是由 catalog 按 target 提供；
    patch 也按 target 挂载（一个 target 一份 diff）。"""
    SI_ENV = "si.env"
    LVS_QCI = "lvs.qci"
    QUANTUS_EXT = "quantus.ext.cmd"
    QUANTUS_DSPF = "quantus.dspf.cmd"
    JIVARO_XML = "jivaro.xml"


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 24) -> str:
    s = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    return s[:max_len].rstrip("-") or "x"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
```

---

### 1.1 PdkProfile

```python
# auto_ext/model/pdk.py
"""PdkProfile —— 每个工艺一份，扫描发现，平时隐身。

聚合了原先散落在三处的工艺相关信息：
  (a) project.yaml 的 tech_name / tech_name_env_vars / layer_map / paths / env_overrides
  (b) 模板里写死的工艺字面量（assura_tech.lib、power/ground 名单、preserveCellList.txt）
  (c) manifest 里被当成 knob 的工艺枚举（lvs_variant.choices = [wodio, widio]）
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from auto_ext.model.common import Base, Frozen, PathExpr, Slug, utcnow

PDK_PROFILE_SCHEMA_VERSION = 1


class CornerSpec(Base):
    """一个工艺角。Recipe 只写语义名（name），工具字面量在这里绑定，
    这是 Recipe 能跨工艺移植的关键接缝。

    来源：templates/quantus/*.cmd.j2 里写死的
    ``-technology_corner "TYPICAL"``。原先是字面量，改不了。"""

    name: Slug                          #: 语义名，Recipe 里引用它：typical / rcworst / cworst
    technology_corner: str              #: 传给 Quantus -technology_corner 的字面量
    default_temperature_c: float | None = None  #: 该角的建议温度；Recipe 未指定温度时用它
    aliases: list[str] = Field(default_factory=list)  #: 迁移兼容用的旧名
    description: str | None = None


class LvsDeckVariant(Base):
    """一个 LVS deck 变体。

    来源：calibre_lvs.qci.j2.manifest.yaml 的 knob ``lvs_variant``
    （type=str, choices=[wodio, widio]）。knob 机制删除后，
    "有哪些变体" 是工艺事实，落在这里；"这次用哪个" 是 Recipe 的选择。"""

    name: Slug                          #: 语义名，Recipe.lvs.deck_variant 引用它
    rules_suffix: str                   #: 文件名中间段，如 "wodio"
    description: str | None = None
    #: 该变体是否天然要求 *cmnVConnectNamesState ALL；None = 由 Recipe 决定
    connect_by_name_default: bool | None = None


class LvsDeckSet(Base):
    """Calibre LVS 规则文件的定位规则。

    来源：calibre_lvs.qci.j2 第 1 行
    ``*lvsRulesFile: [[calibre_lvs_dir]]/[[calibre_lvs_basename]].[[lvs_variant]].qcilvs``
    —— 目录来自 project.paths.calibre_lvs_dir，basename 由 runner._build_context
    用 ``PurePosixPath(calibre_lvs_dir).name`` 自动推导，后缀来自 knob。
    三段现在都是 profile 的显式字段。"""

    dir_expr: PathExpr                  #: 搬自 project.yaml paths.calibre_lvs_dir
    #: 搬自 runner._build_context 的自动推导 calibre_lvs_basename；
    #: None 表示继续沿用 "取 dir_expr 解析后的末段目录名" 这条 PDK 约定。
    basename: str | None = None
    #: 文件名拼装式，替换原来硬编码在模板里的 ".{suffix}.qcilvs"。
    filename_pattern: str = "{basename}.{suffix}.qcilvs"
    variants: list[LvsDeckVariant] = Field(default_factory=list)
    default_variant: Slug | None = None

    @model_validator(mode="after")
    def _check(self) -> "LvsDeckSet":
        names = [v.name for v in self.variants]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate lvs deck variant names: {names}")
        if self.default_variant and self.default_variant not in names:
            raise ValueError(f"default_variant {self.default_variant!r} not in {names}")
        return self


class QrcDeck(Base):
    """QRC deck 目录及其内部约定文件名。

    来源：project.yaml paths.qrc_deck_dir，加上模板里写死的两个文件名
    （calibre 模板 lvsPostTriggers 的 ``query_cmd``；
      quantus 模板 -parasitic_blocking_device_cells_file 的 ``preserveCellList.txt``）。"""

    dir_expr: PathExpr                  #: 搬自 project.yaml paths.qrc_deck_dir
    query_cmd_name: str = "query_cmd"
    preserve_cell_list_name: str = "preserveCellList.txt"


class PdkCheckKind(StrEnum):
    ENV_VAR = "env_var"                 #: target 是环境变量名，检查已设置且非空
    FILE_EXISTS = "file_exists"         #: target 是 PathExpr，检查是普通文件
    DIR_EXISTS = "dir_exists"           #: target 是 PathExpr，检查是目录
    GLOB_NONEMPTY = "glob_nonempty"     #: target 是 glob，检查至少命中一个
    COMMAND_OK = "command_ok"           #: target 是 argv 字符串，检查 exit 0


class PdkCheck(Base):
    """一条体检项声明（不含结果）。

    新对象。现状里对应的是 runner._discover_env_vars + resolve_env().require()
    在跑起来的那一刻才炸的隐式检查，没有 UI、没有修法提示。"""

    check_id: Slug
    title: str                          #: UI 上那一行的中文标题
    kind: PdkCheckKind
    target: str                         #: 语义由 kind 决定
    required: bool = True               #: False = 缺失只报黄，不拦运行
    fix_hint: str                       #: ✗ 时展开显示的修法，写清楚 source 哪个脚本 / 设哪个变量


class PdkCheckResult(Frozen):
    """一条体检项的运行时结果。写在 profiles/<id>.health.json，不进 profile 文件。"""

    check_id: Slug
    ok: bool
    observed: str | None = None         #: 实际看到的值 / 路径 / 退出码
    message: str | None = None
    checked_at: datetime


class PdkHealthReport(Frozen):
    profile_id: Slug
    checked_at: datetime
    results: list[PdkCheckResult] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)


class PdkProfile(Base):
    """一份工艺的全部绑定信息。文件：config/profiles/<profile_id>.yaml"""

    schema_version: int = PDK_PROFILE_SCHEMA_VERSION
    profile_id: Slug                            #: 文件名主干，Workspace 与 RunRecord 引用它
    display_name: str                           #: UI 显示，如 "HN001 22nm (runset 2024.3)"
    description: str | None = None

    # ---- 身份 ----
    #: 搬自 ProjectConfig.tech_name。Quantus -technology_name 的值。
    tech_name: str | None = None
    #: 搬自 ProjectConfig.tech_name_env_vars。tech_name 为 None 时的自动推导候选，
    #: 沿用 env.derive_parent_dir_from_env_candidates 的 "取 parent 目录名" 语义。
    tech_name_env_vars: list[str] = Field(
        default_factory=lambda: ["PDK_TECH_FILE", "PDK_LAYER_MAP_FILE", "PDK_DISPLAY_FILE"]
    )
    #: 搬自 quantus 模板里写死的 ``-technology_library_file "$env(SETUP_ROOT)/assura_tech.lib"``。
    tech_library_file: PathExpr = "$env(SETUP_ROOT)/assura_tech.lib"
    #: 搬自 ProjectConfig.layer_map（默认 ${PDK_LAYER_MAP_FILE}），strmout -layerMap 用。
    layer_map: PathExpr = "${PDK_LAYER_MAP_FILE}"
    #: 搬自 si 模板里写死的 ``incFILE = "$calibre_source_added_place"``。
    cdl_include_file: PathExpr = "$calibre_source_added_place"

    # ---- env 解析 ----
    #: 搬自 ProjectConfig.env_overrides，语义不变（override > shell > missing）。
    env_overrides: dict[str, str] = Field(default_factory=dict)
    #: 新增。原先靠 runner._discover_env_vars 扫模板文本反推，模板一改就漂移；
    #: 现在显式声明这套工艺必须存在的变量，check-env 直接读它。
    required_env: list[str] = Field(default_factory=list)

    # ---- deck 目录 ----
    lvs_decks: LvsDeckSet
    qrc: QrcDeck
    #: 逃生用：任意额外路径键，语义 = 旧 ProjectConfig.paths 里除 calibre_lvs_dir /
    #: qrc_deck_dir 之外用户自定义的条目。渲染时暴露为 ``pdk.paths.<key>``。
    extra_paths: dict[str, PathExpr] = Field(default_factory=dict)

    # ---- 取值表 ----
    corners: list[CornerSpec] = Field(default_factory=list)
    default_corner: Slug | None = None
    #: 搬自 calibre_lvs.qci.j2 里写死的 ``*lvsPowerNames`` 那一整行（27 个名字）。
    #: 这是纯 PDK 事实，绝不该待在模板里。
    power_names: list[str] = Field(default_factory=list)
    #: 同上，搬自 ``*lvsGroundNames``。
    ground_names: list[str] = Field(default_factory=list)

    # ---- 体检 ----
    checks: list[PdkCheck] = Field(default_factory=list)

    # ---- 溯源 ----
    #: 这份 profile 是从哪扫出来的（setup 脚本路径 / 原始导出目录）。
    discovered_from: list[str] = Field(default_factory=list)
    scanned_at: datetime = Field(default_factory=utcnow)
    #: 用户手改过 profile 后置 True，重扫时不静默覆盖，改为三方合并报冲突。
    hand_edited: bool = False

    @field_validator("corners")
    @classmethod
    def _unique_corners(cls, v: list[CornerSpec]) -> list[CornerSpec]:
        names = [c.name for c in v]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate corner names: {names}")
        return v

    @model_validator(mode="after")
    def _default_corner_exists(self) -> "PdkProfile":
        if self.default_corner and self.default_corner not in {c.name for c in self.corners}:
            raise ValueError(
                f"default_corner {self.default_corner!r} not in "
                f"{[c.name for c in self.corners]}"
            )
        return self

    def corner(self, name: str) -> CornerSpec | None:
        for c in self.corners:
            if c.name == name or name in c.aliases:
                return c
        return None
```

---

### 1.2 Recipe

```python
# auto_ext/model/recipe.py
"""Recipe —— 全局共享、语义化、跨项目可移植的配方。全新对象。

它吃掉的东西：
  * manifest 四层 knob 的全部内容（7 个 knob）
  * 模板里写死但明显是"提取条件"的字面量（corner / extract type / metal_fill /
    coupling 阈值上游那些 -exclude_* 开关 / min_res 邻近开关 ...）
  * TaskSpec.jivaro + JivaroOverride
  * TaskSpec.continue_on_lvs_fail
  * ProjectConfig.templates 的 quantus slot（选 ext.cmd 还是 dspf.cmd）
  * core/clone_template.py 的"整文件 fork" —— 降级成 patches[]

它坚决不吃的东西：library / cell / view / ground_net / out_file（那是 Cells），
任何绝对路径或工艺字面量（那是 PdkProfile），任何 ${WORK_ROOT} 派生路径（那是 Workspace）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from auto_ext.model.common import Base, RenderTarget, Slug, Stage, STAGE_ORDER, utcnow

RECIPE_SCHEMA_VERSION = 1


# ---- 逃生舱：patch ---------------------------------------------------------

class PatchHunk(Base):
    """一处手工修改。UI 上的"此配方有 N 处手工修改"就是 enabled hunk 的计数，
    "可单条还原" = 把某个 hunk 的 enabled 置 False。"""

    hunk_id: Slug
    header: str                         #: ``@@ -a,b +c,d @@``
    body: str                           #: 该 hunk 的全部行（含 ' ' / '-' / '+' 前缀）
    enabled: bool = True
    note: str | None = None             #: 用户写的原因，比如 "本项目 pcell 需要 hyper 关掉"


class TemplatePatch(Base):
    """相对**生成结果**的 diff，不是相对模板。

    取代 core/clone_template.py：clone 会把整个模板永久 fork 出去，
    catalog 一升级就再也跟不上。patch 存的是三方合并的一条边：

        base_text  = 打补丁时 catalog 渲染出来的文本（合并基）
        hunks      = base_text -> 用户编辑后文本 的 diff
        新一次渲染  = catalog 新版渲染出的文本（合并另一边）

    catalog 升级 / 换工艺 → 生成部分自动跟进；用户改动作为 hunks 继续叠加；
    冲突时按 on_conflict 处理并写进 RunRecord.patches。"""

    patch_id: Slug
    target: RenderTarget                #: 打在哪个产物上
    description: str
    base_text: str                      #: 三方合并基，完整文本（几 KB，值得存）
    base_sha256: str                    #: base_text 的指纹，快速判断"生成部分没变"
    #: 生成 base_text 的 catalog 版本，冲突报告里要指名道姓
    base_catalog_version: str
    hunks: list[PatchHunk] = Field(default_factory=list)
    enabled: bool = True
    on_conflict: str = Field(default="fail", pattern=r"^(fail|warn_skip)$")
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str | None = None

    @property
    def active_hunk_count(self) -> int:
        return sum(1 for h in self.hunks if h.enabled) if self.enabled else 0


# ---- 语义分组 --------------------------------------------------------------

class ExtractType(StrEnum):
    RC_COUPLED = "rc_coupled"
    RC_DECOUPLED = "rc_decoupled"
    R_ONLY = "r_only"
    C_ONLY = "c_only"


class MetalFill(StrEnum):
    NONE = "none"
    VIRTUAL = "virtual"
    ACTUAL = "actual"


class OutputKind(StrEnum):
    EXTRACTED_VIEW = "extracted_view"
    DSPF = "dspf"


class NetlistSettings(Base):
    """si.env 里真正算"提取条件"的那几项。

    来源：templates/si/default.env.j2 全部写死，manifest knobs 为 {}，
    也就是说现在一个都调不了。"""

    simulator: str = "auCdl"                    #: simSimulator
    view_list: list[str] = Field(default_factory=lambda: ["auCdl", "schematic"])  #: simViewList
    stop_list: list[str] = Field(default_factory=lambda: ["auCdl"])               #: simStopList
    incremental: bool = False                   #: simNotIncremental / simReNetlistAll 的合并表达
    short_res_ohm: float = 2000.0               #: shortRES
    preserve_res: bool = True                   #: preserveRES
    preserve_cap: bool = True                   #: preserveCAP
    preserve_dio: bool = True                   #: preserveDIO
    preserve_all: bool = True                   #: preserveALL
    check_res_val: bool = True                  #: checkRESVAL
    check_cap_val: bool = True                  #: checkCAPVAL
    check_dio_area: bool = True                 #: checkDIOAREA
    check_dio_peri: bool = True                 #: checkDIOPERI
    check_scale: str = "meter"                  #: checkScale
    shrink_factor: float = 0.0                  #: shrinkFACTOR
    display_pin_info: bool = True               #: displayPININFO


class LvsSettings(Base):
    """来源：calibre_lvs.qci.j2 + 它的 manifest 两个 knob。"""

    #: 搬自 knob ``lvs_variant``（choices 移到 PdkProfile.lvs_decks.variants）。
    #: 这里只写语义名；哪个后缀、在哪个目录，由 profile 解析。
    deck_variant: Slug = "wodio"
    #: 搬自 knob ``connect_by_name``，控制 ``*cmnVConnectNamesState: ALL``。
    connect_by_name: bool = False
    report_options: str = "S"                   #: *lvsReportOptions，原写死
    recognize_gates: str = "NONE"               #: *lvsRecognizeGates，原写死
    abort_on_supply_error: bool = False         #: *lvsAbortOnSupplyError，原写死 0
    svdb_cci: bool = True                       #: *lvsSVDBcci，原写死 1
    device_filter_options_enabled: bool = False #: *lvsDeviceFilterOptionsEnabled，原写死 0
    layout_device_filter_options: str = "AG RC RE RG"   #: 原写死
    source_device_filter_options: str = "AG RC RE RG"   #: 原写死
    num_turbo: int = Field(default=2, ge=1)     #: *cmnNumTurbo，原写死
    run_mt: bool = True                         #: *cmnRunMT，原写死 1
    run_hyper: bool = True                      #: *cmnRunHyper，原写死 1
    license_wait_seconds: int = 10              #: *cmnLicenseWaitTime，原写死
    #: 是否在 lvsPostTriggers 里跑 ``calibre -query_input <qrc_deck>/query_cmd``。
    #: 原来写死一定跑；只做 LVS 不做提取时应该能关。
    run_qrc_query: bool = True


class ExtractionSettings(Base):
    """来源：quantus ext.cmd.j2 / dspf.cmd.j2 + 它们 manifest 的 5 个 knob。

    注意 corner：原先是 ``-technology_corner "TYPICAL"`` 的字面量，
    同一条 process_technology 语句里的 -temperature 因为写进了 manifest 反而能调 ——
    正是用户点名的那个"参数化走歪"的例子。现在两个都是语义字段。"""

    #: 语义工艺角名，在 PdkProfile.corners 里查表得到工具字面量。
    #: None = 用 profile.default_corner。
    corner: Slug | None = None
    #: 搬自 knob ``temperature``（manifest default 55.0）。
    #: None = 用 CornerSpec.default_temperature_c。
    temperature_c: float | None = None

    extract_type: ExtractType = ExtractType.RC_COUPLED   #: extract -type，原写死
    selection: str = "all"                               #: extract -selection，原写死
    decoupling_factor: float = 1.0                       #: capacitance -decoupling_factor，原写死
    net_name_space: str = "SCHEMATIC"                    #: extraction_setup，原写死

    exclude_self_cap: bool = True                        #: filter_cap，原写死 true
    exclude_floating_nets: bool = True                   #: filter_cap，原写死 true
    #: 搬自 knob ``exclude_floating_nets_limit``
    #: （manifest 5000 / project.yaml 100 / tasks.yaml 200 —— 四层的活样本）
    exclude_floating_nets_limit: int = Field(default=5000, ge=100, le=100_000)
    #: 搬自 knob ``coupling_cap_threshold_absolute``（F）
    coupling_cap_threshold_absolute: float = 0.01
    #: 搬自 knob ``coupling_cap_threshold_relative``
    coupling_cap_threshold_relative: float = 0.001
    #: 搬自 knob ``min_res``（ohm）
    min_res_ohm: float = 0.001
    merge_parallel_res: bool = True                      #: filter_res，原写死 true
    remove_dangling_res: bool = True                     #: filter_res，原写死 true

    #: 搬自 dspf.cmd.j2 写死的 ``metal_fill -type virtual``；ext.cmd.j2 里根本没这段，
    #: 两份模板的这个差异原先是隐性的，现在是一个显式字段。
    metal_fill: MetalFill = MetalFill.VIRTUAL
    array_vias_spacing: str = "auto"                     #: extraction_setup，原写死
    max_fracture_length: str = "infinite"                #: extraction_setup，原写死
    max_via_array_size: str = "auto"                     #: extraction_setup，原写死


class ExtractedViewOutput(Base):
    """output_db -type extracted_view 的形态。全部搬自 ext.cmd.j2 的写死值。
    视图名本身不在这里 —— 它来自 CellEntry.out_file（身份，不是配方）。"""

    cap_component: str = "pcapacitor"
    cap_property_name: str = "c"
    res_component: str = "presistor"
    res_property_name: str = "r"
    device_finger_delimiter: str = "@"
    enable_cellview_check: bool = False
    include_cap_model: bool = False
    include_parasitic_cap_model: bool = False
    include_res_model: bool = False
    include_parasitic_res_model: str = "comment"


class DspfOutput(Base):
    """output_db -type dspf 的形态。全部搬自 dspf.cmd.j2 的写死值。
    文件路径不在这里 —— 那是 WorkspaceConfig.dspf_out_pattern。"""

    subtype: str = "extended"
    netlist_coupling_values: str = "double"
    busbit_delimiter: str = "[]"
    hierarchy_delimiter: str = "/"
    sub_node_char: str = "#"
    device_finger_delimiter: str = "@"
    add_bulk_terminal: bool = False
    disable_instances: bool = False
    output_xy: list[str] = Field(
        default_factory=lambda: [
            "CANONICAL_RES", "PARASITIC_RES", "CANONICAL_CAP", "PARASITIC_CAP",
            "DIODE", "MOS", "BIPOLAR", "GENERIC",
        ]
    )


class OutputSettings(Base):
    """输出形式。

    取代 ProjectConfig.templates.quantus / TaskSpec.templates.quantus 那个 slot：
    原先指向 ext.cmd.j2 还是 dspf.cmd.j2 决定了输出形态，而且只能二选一。
    现在 emit 是列表，quantus stage 会按需渲染并运行一次或两次。"""

    emit: list[OutputKind] = Field(default_factory=lambda: [OutputKind.EXTRACTED_VIEW])
    extracted_view: ExtractedViewOutput = Field(default_factory=ExtractedViewOutput)
    dspf: DspfOutput = Field(default_factory=DspfOutput)

    @model_validator(mode="after")
    def _nonempty_unique(self) -> "OutputSettings":
        if not self.emit:
            raise ValueError("output.emit must list at least one of extracted_view / dspf")
        if len(set(self.emit)) != len(self.emit):
            raise ValueError(f"output.emit has duplicates: {self.emit}")
        return self


class ReductionSettings(Base):
    """搬自 JivaroConfig + JivaroOverride + jivaro/default.xml.j2 的写死值。
    JivaroOverride（per-cell 覆盖）本身删除：per-cell 差异 = 另一份 Recipe。"""

    enabled: bool = False                       #: JivaroConfig.enabled
    frequency_limit_ghz: float | None = None    #: JivaroConfig.frequency_limit（模板 default(14)）
    error_max_pct: float | None = None          #: JivaroConfig.error_max（模板 default(2)）
    criterion: str = "standard"                 #: <criterion>，原写死
    reduce_floating_nets: bool = False          #: <reduceFloatingNets>，原写死
    decoupling_auto_threshold: bool = False     #: <decouplingAutoThreshold>，原写死
    cpu: int = Field(default=1, ge=1)           #: <cpu>，原写死
    log_verbose_level: str = "trace"            #: <logVerboseLevel>，原写死
    views_to_reduce: str = "av_extracted"       #: <viewsToReduce>，原写死
    #: <outputView> = out_file + 这个后缀。原写死 "_red"。
    output_view_suffix: str = "_red"
    r_model: str = "analogLib/presistor/symbol" #: <rModel>，原写死
    c_model: str = "analogLib/pcapacitor/symbol"
    l_model: str = "analogLib/pinductor/symbol"
    k_model: str = "analogLib/pmind/symbol"


class RunPolicy(Base):
    """跑法策略。"""

    #: 搬自 TaskSpec.continue_on_lvs_fail，语义不变。
    continue_on_lvs_fail: bool = False
    #: 新增：LVS 报告解析失败（checks.CheckError）算不算硬失败。
    #: 现状是抛 AutoExtError 直接失败；保留但可配。
    fail_on_unparsable_lvs_report: bool = True


# ---- 顶层 ------------------------------------------------------------------

class Recipe(Base):
    """一份配方。文件：<recipes_dir>/<recipe_id>.yaml"""

    schema_version: int = RECIPE_SCHEMA_VERSION
    recipe_id: Slug
    name: str                                   #: UI 显示名，中文可
    description: str | None = None
    #: 语义版本；改动配方内容时手动或由 UI 递增，RunRecord 里记它做溯源。
    version: str = "1"
    tags: list[str] = Field(default_factory=list)
    #: 从哪份配方"另存为"来的，做血缘。取代 preset.py 的 preset 概念。
    derived_from: Slug | None = None

    #: 这份配方打算跑哪些 stage。取代 CLI ``--stage`` 作为持久化的默认；
    #: CLI 仍可临时收窄，收窄结果记进 RunRecord.stages。
    stages: list[Stage] = Field(default_factory=lambda: list(STAGE_ORDER))

    netlist: NetlistSettings = Field(default_factory=NetlistSettings)
    lvs: LvsSettings = Field(default_factory=LvsSettings)
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    reduction: ReductionSettings = Field(default_factory=ReductionSettings)
    policy: RunPolicy = Field(default_factory=RunPolicy)

    #: 逃生舱。UI 上的 "此配方有 N 处手工修改" = sum(p.active_hunk_count)。
    patches: list[TemplatePatch] = Field(default_factory=list)

    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _check(self) -> "Recipe":
        if not self.stages:
            raise ValueError("recipe.stages must not be empty")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError(f"recipe.stages has duplicates: {self.stages}")
        ids = [p.patch_id for p in self.patches]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate patch_id: {ids}")
        return self

    @property
    def manual_edit_count(self) -> int:
        return sum(p.active_hunk_count for p in self.patches)


class RecipeRef(Frozen):
    """指向一份 Recipe 的引用 + 内容指纹。RunRecord 里与快照并存：
    快照保证可复现，引用保证能回答"这次用的是不是 catalog 里那份"。"""

    recipe_id: Slug
    version: str
    source_path: str | None = None
    content_sha256: str
```

**Recipe 搜索路径**（"全局共享、跨项目可移植"的落地）：`$AUTO_EXT_RECIPES` → `~/.auto_ext/recipes` → `<auto_ext_root>/recipes` → `<config_dir>/recipes`。同 `recipe_id` 时靠后者遮蔽前者，`RecipeRef.source_path` 记录实际命中的那份。

---

### 1.3 RunRecord

```python
# auto_ext/model/run.py
"""RunRecord —— 不可变运行记录。全新对象。

取代的现状：
  * task_id = f"{library}__{cell}__{layout}__{src}" 作为身份（复制 spec 必撞）
  * logs/task_<id>/<stage>.log 以 "w" 打开（重跑覆盖，没有历史）
  * RunSummary / TaskResult / StageResult 三个 dataclass（内存态，跑完就没）

写入协议（保证"永不覆盖"）：
  1. 开跑：allocate_run_dir() 用 mkdir(exist_ok=False) 抢目录 —— 目录创建即是锁
  2. 运行中：事件追加到 events.jsonl（唯一的追加写），run.json 写一份 status=running 的骨架
  3. finalize：完整 RunRecord 写临时文件 + os.replace 原子替换 run.json，此后不再写
  4. 用户改名/加备注：只动 annotations.json，run.json 保持原样
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from auto_ext.model.cells import CellEntry
from auto_ext.model.common import Base, Frozen, RenderTarget, Slug, Stage, utcnow
from auto_ext.model.pdk import PdkCheckResult, PdkProfile
from auto_ext.model.recipe import Recipe, RecipeRef

RUN_SCHEMA_VERSION = 2   # 1 = 不存在（旧世界没有 run.json）


class StageStatus(StrEnum):
    """与 core/progress.StageStatus 逐值对齐，保留 StrEnum 以便 == "passed" 继续成立。"""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    DRY_RUN = "dry_run"


class RunStatus(StrEnum):
    """搬自 core/progress.TaskStatus，收敛规则不变
    （任一 CANCELLED → CANCELLED；否则任一 FAILED → FAILED；否则 PASSED）。"""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageRecord(Frozen):
    """一个 stage 的完整记录。搬自 runner.StageResult 并补齐时间/argv/路径。"""

    #: 本次运行内唯一的键。quantus 可能跑两次（extracted_view + dspf），
    #: 此时 key 为 "quantus.ext" / "quantus.dspf"，stage 都是 QUANTUS。
    key: str
    stage: Stage
    status: StageStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: 耗时（秒）。现状完全没有。
    duration_s: float | None = None
    argv: list[str] = Field(default_factory=list)      #: 来自 Tool.build_argv
    cwd: str | None = None                             #: 串行=workarea，并行=<run>/work
    exit_code: int | None = None
    #: 相对 run 目录的 POSIX 路径，如 "logs/calibre.log"。
    #: 取代 <auto_ext_root>/logs/task_<id>/<stage>.log。
    log_path: str | None = None
    #: 相对 run 目录，如 "rendered/lvs.qci"。取代 runs/task_<id>/rendered/<stem>。
    rendered_path: str | None = None
    render_target: RenderTarget | None = None
    #: EDA 工作区里产出的绝对路径（svdb / query_output / *.calibre.db / dspf ...）。
    #: 搬自 ToolResult.artifact_paths。绝对路径，因为它们不在 run 目录里。
    artifacts: list[str] = Field(default_factory=list)
    #: 搬自 ToolResult.diagnostics，JSON-safe 化。
    diagnostics: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    error: str | None = None
    #: SKIPPED 时的原因，逐字沿用 runner._emit_synthetic_stage 的措辞
    #: （"jivaro disabled for task" / "aborted after earlier stage failure" ...）。
    skip_reason: str | None = None


class LvsResult(Frozen):
    """搬自 core/checks.LvsReport，并按用户要求"提升使用"：
    从只喂给 CalibreTool.parse_result 的一个 bool，变成 run.json 里的一等结果。"""

    passed: bool
    banner: str | None                  #: "CORRECT" / "INCORRECT" / None
    discrepancies: int | None
    #: EDA 工作区里的原件路径（会被下一次运行覆盖）
    source_path: str
    #: run 目录里的副本，相对路径 "results/lvs.report"（永不被覆盖）
    archived_path: str | None = None
    #: CELL SUMMARY 回退路径命中时的行摘要，便于 UI 展开
    cell_summary: list[str] = Field(default_factory=list)


class ExtractionResult(Frozen):
    extracted_view_name: str | None = None      #: = CellEntry.out_file
    extracted_view_path: str | None = None
    dspf_path: str | None = None                #: WorkspaceConfig.dspf_out_pattern 解析后的值
    dspf_bytes: int | None = None
    net_count: int | None = None                #: 能便宜解析就填，否则 None
    res_count: int | None = None
    cap_count: int | None = None


class ReductionResult(Frozen):
    input_view: str | None = None
    output_view: str | None = None              #: out_file + reduction.output_view_suffix
    elements_before: int | None = None
    elements_after: int | None = None
    reduction_ratio: float | None = None


class RunResults(Frozen):
    lvs: LvsResult | None = None
    extraction: ExtractionResult | None = None
    reduction: ReductionResult | None = None


class PatchApplication(Frozen):
    """一条 patch hunk 在这次运行里的落地结果。冲突必须"明确报出来"，就报在这。"""

    patch_id: Slug
    hunk_id: Slug
    target: RenderTarget
    status: str = Field(pattern=r"^(applied|applied_fuzzy|conflict|disabled|obsolete)$")
    #: applied：base_sha256 与本次生成文本一致，直接套用
    #: applied_fuzzy：生成部分变了，但三方合并成功
    #: conflict：三方合并失败，按 on_conflict 决定 fail / warn_skip
    #: disabled：用户关掉了这个 hunk（"单条还原"）
    #: obsolete：hunk 的上下文在新生成结果里已完全消失
    detail: str | None = None
    conflict_diff: str | None = None    #: conflict 时的三方冲突块，UI 直接显示


class EnvBinding(Frozen):
    """一个环境变量的取值与来源。搬自 env.EnvResolution.resolved + .sources。"""

    name: str
    value: str
    source: str = Field(pattern=r"^(override|shell|missing)$")


class WorkspaceSnapshot(Frozen):
    """这次运行落在 EDA 工作区的位置。全是解析后的绝对路径，纯记录，不承担身份。"""

    #: 搬自 ProjectConfig.extraction_output_dir 解析结果（runner._resolve_output_dir）。
    #: 现在只是"这次跑在哪个 Cadence 工作区"，不再是唯一性来源。
    output_dir: str
    intermediate_dir: str               #: 搬自 ProjectConfig.intermediate_dir 解析结果
    dspf_out_path: str | None = None    #: 搬自 ProjectConfig/TaskSpec.dspf_out_path 解析结果
    workarea: str                       #: CLI --workarea
    run_dir: str                        #: runs/<run_id> 绝对路径
    work_dir: str | None = None         #: 并行隔离 cwd = run_dir/work，串行为 None


class RunRecord(Frozen):
    """runs/<run_id>/run.json 的全部内容。JSON 可序列化：
    没有 Path、没有集合、没有元组键；时间用 datetime（pydantic 输出 ISO 8601）。
    用 record.model_dump_json(indent=2) 直接落盘。"""

    schema_version: int = RUN_SCHEMA_VERSION

    # ---- 身份 ----
    run_id: str                         #: == 目录名，如 20260821T143205Z_amp2-rc-coupled-typical
    slug: str                           #: run_id 的后半段
    created_at: datetime
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.PENDING
    #: 批次归属。批次索引文件 runs/batches/<batch_id>.json 列出成员。
    batch_id: str | None = None
    #: 重跑血缘：从哪个 run 重跑出来的。
    parent_run_id: str | None = None

    # ---- 被跑的东西（全部快照，不是引用）----
    dut: CellEntry                      #: 从 cells.yaml 摘下来的那一行，深拷贝
    recipe: Recipe                      #: **快照**：整份配方内联，含 patches
    recipe_ref: RecipeRef               #: 引用+指纹，用于回答"是否与当前 catalog 一致"
    pdk: PdkProfile                     #: profile 也整份快照（几 KB），换工艺后仍可复现
    #: 快照时刻的体检结果；✗ 项在运行前就该拦住，这里留证据。
    pdk_health: list[PdkCheckResult] = Field(default_factory=list)

    # ---- 解析结果 ----
    #: 实际参与运行的 stage 列表（recipe.stages ∩ CLI --stage）
    requested_stages: list[Stage] = Field(default_factory=list)
    #: 只含本次真正用到的变量。搬自 env.resolve_env 的 resolved + sources。
    env: list[EnvBinding] = Field(default_factory=list)
    #: 最终喂给 Jinja 的完整上下文，扁平化成 "recipe.extraction.min_res_ohm" 这种点号键。
    #: 现状里这个东西活在 runner._build_context 的返回值里，跑完即焚。
    context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    workspace: WorkspaceSnapshot

    # ---- 过程与结果 ----
    stages: list[StageRecord] = Field(default_factory=list)
    results: RunResults = Field(default_factory=RunResults)
    patches: list[PatchApplication] = Field(default_factory=list)

    # ---- 环境溯源 ----
    tools: dict[str, str] = Field(default_factory=dict)   #: {"calibre": "/opt/.../calibre", ...}
    tool_versions: dict[str, str] = Field(default_factory=dict)
    host: str | None = None
    user: str | None = None
    auto_ext_version: str | None = None
    python_version: str | None = None
    cancelled_by: str | None = None
    dry_run: bool = False

    @model_validator(mode="after")
    def _unique_stage_keys(self) -> "RunRecord":
        keys = [s.key for s in self.stages]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate stage keys: {keys}")
        return self

    # ---- 显示名（task_id 的降级去处）----
    @property
    def dut_label(self) -> str:
        """旧 task_id 的语义，只用于显示，不进任何路径。"""
        d = self.dut
        return f"{d.library}__{d.cell}__{d.layout_view}__{d.source_view}"

    @property
    def default_display_name(self) -> str:
        return f"{self.dut.cell} · {self.recipe.name}"


class RunAnnotations(Base):
    """runs/<run_id>/annotations.json —— run 目录里唯一可变的文件。
    用户改名、加备注、打标全落在这，run.json 一个字节都不动。"""

    display_name: str | None = None     #: 合并了 TaskSpec.label 的显示用途
    note: str | None = None
    tags: list[str] = Field(default_factory=list)
    starred: bool = False
    updated_at: datetime = Field(default_factory=utcnow)


class RunBatch(Base):
    """runs/batches/<batch_id>.json —— 一次"批量跑"的索引。
    取代 runner.RunSummary 的持久化职责。"""

    batch_id: str                       #: 同样是 <ISO时间戳>_<slug> 格式
    created_at: datetime
    finished_at: datetime | None = None
    label: str | None = None
    recipe_id: Slug
    run_ids: list[str] = Field(default_factory=list)
    max_workers: int = 1
```

**关于 `extra="forbid"` + 内联快照的前向兼容**：`RunRecord.recipe` 是强类型 `Recipe`，若将来 Recipe 加字段，旧 run.json 仍能读（旧字段是新 Recipe 的子集）；反之新 run.json 被旧代码读会因 `extra="forbid"` 报错——这是期望行为。读取入口统一走：

```python
_RUN_UPGRADERS: dict[int, Callable[[dict], dict]] = {}   # {from_version: fn}

def load_run_record(path: Path) -> RunRecord:
    data = json.loads(path.read_text(encoding="utf-8"))
    v = int(data.get("schema_version", 0))
    while v < RUN_SCHEMA_VERSION:
        if v not in _RUN_UPGRADERS:
            raise ConfigError(f"{path}: run schema v{v} 无升级器，需更新 Auto_ext")
        data = _RUN_UPGRADERS[v](data)
        v = int(data["schema_version"])
    if v > RUN_SCHEMA_VERSION:
        raise ConfigError(f"{path}: run schema v{v} 比本程序（v{RUN_SCHEMA_VERSION}）新")
    return RunRecord.model_validate(data)
```

---

### 1.4 支撑对象：Cells 与 Workspace

`Cells` 是第三个名词，`RunRecord.dut` 依赖它；`WorkspaceConfig` 不是四个名词之一，但 `${WORK_ROOT}` 派生的路径模式必须有家——它是 project.yaml 瘦身后的残余（14 键 → 5 键）。

```python
# auto_ext/model/cells.py
from __future__ import annotations
from pydantic import Field, model_validator
from auto_ext.model.common import Base, Slug

class CellEntry(Base):
    """一行 DUT。笛卡尔展开在"批量添加"时就完成并落成明确的行，
    加载时不再展开 —— TaskSpec 的 list 值语义、_expand_spec、_is_excluded、
    ExcludeMatch 全部删除。"""

    library: str                        #: 搬自 TaskSpec.library（标量化）
    cell: str                           #: 搬自 TaskSpec.cell（标量化）
    layout_view: str                    #: 搬自 TaskSpec.lvs_layout_view（改名，去掉 lvs_ 前缀）
    source_view: str = "schematic"      #: 搬自 TaskSpec.lvs_source_view
    ground_net: str = "vss"             #: 搬自 TaskSpec.ground_net
    out_file: str | None = None         #: 搬自 TaskSpec.out_file
    display_name: str | None = None     #: 搬自 TaskSpec.label（唯一保留的 UX 糖）
    enabled: bool = True                #: 取代 TaskSpec.exclude 的全部用途
    note: str | None = None

    @property
    def key(self) -> str:
        """表内唯一键，也是旧 task_id 的显示形态。不进任何路径。"""
        return f"{self.library}__{self.cell}__{self.layout_view}__{self.source_view}"


class CellBook(Base):
    """config/cells.yaml"""
    schema_version: int = 1
    cells: list[CellEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "CellBook":
        keys = [c.key for c in self.cells]
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            # 现状 _warn_on_duplicate_task_ids 只 logger.warning；这里直接拒绝，
            # 因为身份已经不靠它了，重复行纯属编辑事故。
            raise ValueError(f"duplicate cell rows: {sorted(dup)}")
        return self
```

```python
# auto_ext/model/workspace.py
from __future__ import annotations
from pydantic import Field
from auto_ext.model.common import Base, PathExpr, Slug

class WorkspaceConfig(Base):
    """config/workspace.yaml —— project.yaml 减掉 knobs / templates / 三个
    display-only 根路径 / 全部工艺字段之后剩下的东西。"""

    schema_version: int = 1
    #: 本项目使用哪份 PdkProfile。取代 project.yaml 里散落的工艺字段。
    pdk_profile: Slug
    #: 搬自 ProjectConfig.extraction_output_dir。语义降级为"EDA 工作区位置"，
    #: 不再承担身份。格式键：{cell} {library} {layout_view} {source_view}
    #: {recipe} {run_id} {run_slug}；{task_id} 删除。
    output_dir_pattern: str = "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    #: 搬自 ProjectConfig.intermediate_dir
    intermediate_dir: str = "${WORK_ROOT2}"
    #: 搬自 ProjectConfig.dspf_out_path（TaskSpec 的 per-task 覆盖删除）
    dspf_out_pattern: str = "${WORK_ROOT2}/{cell}.dspf"
    #: 保留多少个 run 目录；0 = 不限。runs prune 用，不影响 schema。
    keep_runs: int = 0
```

> `ProjectConfig.work_root / verify_root / setup_root` **丢弃**：代码里从未消费，仅供 GUI env 面板显示，而那个面板本就该直接读 shell。
> `ProjectConfig.employee_id` 移入 `WorkspaceConfig`？不——它是 site/user 级，放 `WorkspaceConfig` 会让它跟着项目走。移到 `~/.auto_ext/site.yaml`，渲染时暴露为 `site.employee_id`，缺省仍走 `$USER` / `$USERNAME` / `"unknown"` 的现有回退链。

---

## 2. Run 的身份方案

### 2.1 目录名格式

```
runs/<UTC-ISO-8601-basic>_<slug>/
runs/20260821T143205Z_amp2-rc-coupled-typical/
```

- **`%Y%m%dT%H%M%SZ`（basic format，无分隔符）**：无冒号（Windows 非法、Linux 上引号地狱），字典序 == 时间序，`ls` 天然按时间排，`Z` 明示 UTC（跨时区共享 workarea 时不歧义）。
- **一个 Run = 一个 DUT × 一份 Recipe**。`RunRecord.dut` 是单数，与"Run 是不可变记录"自洽。批量跑 N 个 cell = N 个 Run + 1 个 `RunBatch` 索引。

### 2.2 slug 生成

```python
def make_run_slug(dut: CellEntry, recipe: Recipe) -> str:
    """cell + recipe，各自截断。不含 library / view —— 那些在 run.json 里，
    目录名只需要"人扫一眼能认出来"。"""
    return f"{slugify(dut.cell, max_len=24)}-{slugify(recipe.recipe_id, max_len=28)}"
```

### 2.3 重名

目录创建本身就是锁，无需额外协调：

```python
def allocate_run_dir(runs_root: Path, slug: str, *, now: datetime | None = None) -> Path:
    ts = (now or utcnow()).strftime("%Y%m%dT%H%M%SZ")
    for n in range(1, 1000):
        name = f"{ts}_{slug}" if n == 1 else f"{ts}_{slug}-{n}"
        d = runs_root / name
        try:
            d.mkdir(parents=True, exist_ok=False)   # POSIX/NTFS 上原子
        except FileExistsError:
            continue
        for sub in ("rendered", "logs", "results"):
            (d / sub).mkdir()
        return d
    raise RunIdError(f"无法在 {runs_root} 分配 run 目录：{ts}_{slug} 已有 999 个同秒同名")
```

同一秒内并行派发同 cell 同 recipe 时追加 `-2` / `-3`。**永远不追加到时间戳上**（时间戳必须保持可解析）。

### 2.4 用户改名

**目录名不可变，改名只改 `annotations.json`。**

| 操作 | 落点 |
|---|---|
| 用户在 UI 里重命名 | `annotations.json.display_name` |
| UI 列表显示 | `annotations.display_name` → 缺省 `run.default_display_name`（`"{cell} · {recipe.name}"`） |
| 排序 | `run.created_at` |
| 搜索 | 同时匹配 display_name / slug / dut.cell / recipe_id / tags |

理由：`run.json` 内的 `log_path` / `rendered_path` 是相对路径，`workspace.run_dir` 是绝对路径，`batches/*.json` 与 `parent_run_id` 都按 `run_id` 引用。改目录名会把这些全打断，而且违背"不可变记录"。

### 2.5 与 task_id / label 的合并

| 旧 | 新 | 性质 |
|---|---|---|
| `TaskConfig.task_id`（`lib__cell__layout__src`） | `CellEntry.key` / `RunRecord.dut_label` | **纯显示**，property，不落盘为身份，不进任何路径 |
| `TaskSpec.label` | `CellEntry.display_name` | 表格行的显示名 |
| —— | `RunAnnotations.display_name` | 单次运行的显示名（新层级） |
| `runner._UNSAFE_TASK_ID` 正则消毒 | `slugify()` | task_id 消毒逻辑不再需要 |
| `_warn_on_duplicate_task_ids`（只 warn） | `CellBook` 校验器（直接拒绝） | 重复行已不影响运行，纯属编辑事故 |

CLI `--task <task_id>` 过滤器改为 `--cell <cell>` / `--row <key>`，接受 `CellEntry.key` 作为向后兼容的别名。

### 2.6 logs/ 与 runs/ 新布局

| 现状 | 新 |
|---|---|
| `<root>/logs/task_<safe_id>/<stage>.log`，`open(..., "w")`，重跑覆盖 | `<root>/runs/<run_id>/logs/<stage_key>.log`。仍然 `"w"`，但目录每次全新 → 天然永不覆盖 |
| `<root>/runs/task_<safe_id>/`（并行 cwd，`prepare_parallel_workdir` 每次 `rmtree` 重建） | `<root>/runs/<run_id>/work/`（同样重建，但这次它属于这个 run，跑完可 prune 而不丢日志） |
| `<root>/runs/task_<safe_id>/rendered/<template_stem>` | `<root>/runs/<run_id>/rendered/<target>`（文件名用 `RenderTarget` 值，不再是模板文件名主干 —— 模板名是 catalog 内部事实） |
| 无 | `<root>/runs/<run_id>/results/lvs.report`（从 EDA 工作区拷贝的副本） |
| 无 | `<root>/runs/<run_id>/run.json` / `events.jsonl` / `annotations.json` |

`runner._task_run_dirs()` 这个"路径约定唯一真相"的函数被替换为：

```python
def run_paths(run_dir: Path) -> RunPaths:      # 纯函数，无 task 参数
    return RunPaths(
        record   = run_dir / "run.json",
        events   = run_dir / "events.jsonl",
        annots   = run_dir / "annotations.json",
        rendered = run_dir / "rendered",
        logs     = run_dir / "logs",
        results  = run_dir / "results",
        work     = run_dir / "work",
    )
```

`rendered_path_for()`（GUI 的"打开渲染结果"）改为读 `RunRecord.stages[i].rendered_path`——不再重算路径数学，因此不可能与 runner 漂移。

### 2.7 与 `extraction_output_dir` 的关系

`${WORK_ROOT}/cds/verify/QCI_PATH_{cell}` 是 **Cadence 工作区**，从今以后：

- **它不承担任何身份职责**。身份完全由 `runs/<run_id>/` 承担。
- **它是可变、可复用、可被下一次运行覆盖的**。si 写 netlist + `map/` + `ihnl/` + `si.env`；Calibre 写 `svdb/` + `query_output/` + `*.lvs.report`；strmout 写 `<cell>.calibre.db`；Quantus 从 `query_output/` 读。这些是 GB 级的中间产物，同 cell 复用工作区是正确的，不该给每个 run 复制一份。
- **Run 目录负责"抢救小而关键的证据"**：finalize 时把 `rendered/*`、`<cell>.lvs.report` 拷进 run 目录，并在 `StageRecord.artifacts` 里记下工作区绝对路径。工作区被下一次运行覆盖后，run 记录依然自洽可读。
- **原来的 `_validate_task_outputs` 撞车检查降级**。它的存在是因为身份不足（同 cell 不同 knob 会撞 output_dir 且撞 task_id）。新模型下：
  - 串行：同 cell 不同 recipe 复用同一工作区是**合法且期望**的，不再报 `ConfigError`。
  - 并行：改成工作区**锁文件** `<output_dir>/.auto_ext.lock`（内容为持锁 run_id + pid），拿不到锁的 run 排队或明确报"工作区被 run X 占用"。
  - 想要完全隔离的用户，把 `output_dir_pattern` 写成 `.../QCI_PATH_{cell}_{run_slug}` 即可——新增的 `{run_slug}` / `{run_id}` 格式键正是为此。
- `si.env` 发布到工作区（`_publish_si_env_to_output_dir`，为绕 Quantus LBRCXM-756）**保留不变**，只是同时在 `rendered/si.env` 留一份归档。

---

## 3. 迁移路径

### 3.1 字段处置总表

**project.yaml（14 键）**

| 旧字段 | 处置 | 新家 |
|---|---|---|
| `work_root` / `verify_root` / `setup_root` | **丢弃** | 代码从不消费，只给 GUI 显示；GUI 改为直接读 shell |
| `employee_id` | 搬 | `~/.auto_ext/site.yaml`，回退链不变 |
| `tech_name` | 搬 | `PdkProfile.tech_name` |
| `tech_name_env_vars` | 搬 | `PdkProfile.tech_name_env_vars` |
| `layer_map` | 搬 | `PdkProfile.layer_map` |
| `env_overrides` | 搬 | `PdkProfile.env_overrides` |
| `paths.calibre_lvs_dir` | 搬 | `PdkProfile.lvs_decks.dir_expr` |
| `paths.qrc_deck_dir` | 搬 | `PdkProfile.qrc.dir_expr` |
| `paths.<其它自定义键>` | 搬 | `PdkProfile.extra_paths` |
| `extraction_output_dir` | 搬 + 改写 | `WorkspaceConfig.output_dir_pattern`；`{task_id}` → **需用户决策** |
| `intermediate_dir` | 搬 | `WorkspaceConfig.intermediate_dir` |
| `dspf_out_path` | 搬 + 改写 | `WorkspaceConfig.dspf_out_pattern`；`{task_id}` → **需用户决策** |
| `templates.{si,calibre,jivaro}` | **丢弃** | catalog 按 `RenderTarget` 提供；非 catalog 模板 → 种子 patch |
| `templates.quantus` | 转译 | `ext.cmd.j2` → `output.emit=[extracted_view]`；`dspf.cmd.j2` → `[dspf]` |
| `knobs.<stage>.<name>` | 折叠 | 与 manifest default 合并后进 Recipe 对应字段 |

**tasks.yaml（每条 spec）**

| 旧字段 | 处置 | 新家 |
|---|---|---|
| `library` / `cell` / `lvs_layout_view` / `lvs_source_view` | 展开后搬 | `CellEntry.library/cell/layout_view/source_view` |
| `ground_net` / `out_file` | 搬 | `CellEntry` 同名字段 |
| `label` | 搬 | `CellEntry.display_name`（空串 → None） |
| `exclude` | **消解** | 展开时直接不生成那些行；被排掉的组合写进迁移报告备查 |
| `jivaro` | 搬 | `Recipe.reduction` |
| `jivaro_overrides` | **消解为多 Recipe** | 每个不同的有效 jivaro 组合 → 一份 Recipe |
| `continue_on_lvs_fail` | 搬 | `Recipe.policy.continue_on_lvs_fail` |
| `dspf_out_path`（per-task） | **丢弃** | `WorkspaceConfig.dspf_out_pattern` 的 `{cell}` 已覆盖 99% 用法；有真实差异的 → **需用户决策** |
| `templates`（per-task） | **消解为多 Recipe** | quantus slot 差异 → `output.emit` 差异 |
| `knobs`（per-task） | 折叠 | 进该组的 Recipe |

**manifest（7 个 knob）**

| knob | 新家 |
|---|---|
| `calibre: lvs_variant` | `Recipe.lvs.deck_variant`（choices → `PdkProfile.lvs_decks.variants`） |
| `calibre: connect_by_name` | `Recipe.lvs.connect_by_name` |
| `quantus: exclude_floating_nets_limit` | `Recipe.extraction.exclude_floating_nets_limit`（range 变 pydantic `ge/le`） |
| `quantus: coupling_cap_threshold_absolute` | `Recipe.extraction.coupling_cap_threshold_absolute` |
| `quantus: coupling_cap_threshold_relative` | `Recipe.extraction.coupling_cap_threshold_relative` |
| `quantus: min_res` | `Recipe.extraction.min_res_ohm` |
| `quantus: temperature` | `Recipe.extraction.temperature_c` |

**模板写死字面量 → 从模板文本"回读"（seeded_from_template）**：`-technology_corner "TYPICAL"` → `CornerSpec`；`*lvsPowerNames` / `*lvsGroundNames` 两行 → `PdkProfile.power_names/ground_names`；`assura_tech.lib` → `PdkProfile.tech_library_file`；`metal_fill -type virtual`、`extract -type "rc_coupled"`、`shortRES = 2000.0` 等 → 对应 Recipe 字段。回读而非用 schema 默认值，**保证迁移在字节层面中性**。

**需用户决策清单**（`MigrationDecision`）：

1. 每个 corner 的语义名：迁移只能看到字面量 `"TYPICAL"`，语义名由用户定（`typical`？）；同时要求补齐这套工艺还有哪些角（`rcworst` / `cworst` / …）及其字面量——这是 Recipe 可移植性的前提。
2. Recipe 命名：默认 `migrated-1` … `migrated-N`，让用户改成有意义的名字。
3. `{task_id}` 出现在 `extraction_output_dir` / `dspf_out_path` 里时 → `{run_slug}`（每次运行隔离）还是 `{cell}`（同 cell 复用）。
4. 样例 project.yaml 里 `qrc_deck_dir` 含未填占位符 `<runset>` / `<pdk_subdir>` → 必须填实，否则 profile 校验不过。
5. 同 cell 出现在多个 Recipe 组里 → 确认这是有意的（新模型下不再需要靠 `extraction_output_dir` 加判别键来绕开撞车，直接是两个 Run）。
6. `output.emit` 是否要同时出 `extracted_view` + `dspf`（旧模型二选一，多数人其实两个都想要）。
7. 被 `exclude` 排掉的组合：确认是"永久不跑"（不生成行）还是"暂时不跑"（生成行但 `enabled=False`）。

### 3.2 migrate 函数签名 + 伪代码

```python
# auto_ext/migrate.py  —— 替换现有的 NotImplementedError 桩

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal


@dataclass(frozen=True)
class FieldDisposition:
    """一条字段的处置记录，迁移报告逐条列出，不允许有字段静默消失。"""
    source: str          # "project.yaml:knobs.quantus.temperature"
    action: Literal["moved", "dropped", "folded", "seeded_from_template", "decision"]
    target: str | None   # "recipe:migrated-1.extraction.temperature_c"
    note: str = ""


@dataclass(frozen=True)
class MigrationDecision:
    key: str
    question: str        # 中文
    options: list[str]
    default: Any
    context: str = ""


@dataclass
class MigrationReport:
    profile: "PdkProfile"
    recipes: list["Recipe"]
    cells: "CellBook"
    workspace: "WorkspaceConfig"
    #: recipe_id -> 该配方覆盖的 CellEntry.key 列表，告诉用户怎么重建原来的跑法
    bindings: dict[str, list[str]] = field(default_factory=dict)
    dispositions: list[FieldDisposition] = field(default_factory=list)
    decisions: list[MigrationDecision] = field(default_factory=list)
    #: seed_patches=True 时自动生成的"字节保真" patch
    seeded_patches: list[tuple[str, "TemplatePatch"]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)


def migrate_v1_to_v2(
    project_yaml: Path,
    tasks_yaml: Path,
    *,
    template_root: Path,          # 旧 templates/ 根，用于回读写死字面量
    catalog_root: Path,           # 新 catalog 根，用于渲染对拍
    out_root: Path,               # 新 config/ 与 recipes/ 的落点
    profile_id: str | None = None,        # None -> slugify(tech_name) 或 "default"
    recipe_name_hint: str = "migrated",
    resolve: Callable[[MigrationDecision], Any] | None = None,  # None -> 全取 default
    seed_patches: bool = True,    # 渲染对拍，残差自动落成 patch
    write: bool = True,           # False = 只算不写（GUI 预览 / --dry-run）
) -> MigrationReport:
    ...
```

```python
# ---- 伪代码 ----

def migrate_v1_to_v2(...):
    rep = MigrationReport(...)
    ask = resolve or (lambda d: d.default)

    # 1) 原样加载旧世界（复用现有 loader，不重写解析）
    project = load_project(project_yaml)                  # core/config.load_project
    tasks   = load_tasks(tasks_yaml, project=project)     # 已完成 Cartesian 展开 + exclude
    raw_specs = _tasks_sequence(_load_yaml(tasks_yaml), tasks_yaml)

    # 2) PdkProfile
    #    2a. 直接搬 project.yaml 的工艺字段
    #    2b. 从旧模板文本回读写死的工艺字面量
    calibre_txt = (template_root / "calibre/calibre_lvs.qci.j2").read_text()
    quantus_txt = (template_root / "quantus/ext.cmd.j2").read_text()
    si_txt      = (template_root / "si/default.env.j2").read_text()
    profile = PdkProfile(
        profile_id        = profile_id or slugify(project.tech_name or "default"),
        display_name      = project.tech_name or "migrated PDK",
        tech_name         = project.tech_name,
        tech_name_env_vars= project.tech_name_env_vars,
        tech_library_file = _grep1(quantus_txt, r'-technology_library_file\s+"([^"]+)"'),
        layer_map         = str(project.layer_map),
        cdl_include_file  = _grep1(si_txt, r'incFILE\s*=\s*"([^"]+)"'),
        env_overrides     = project.env_overrides,
        required_env      = sorted(discover_required_vars([...旧模板与路径表达式...])),
        lvs_decks = LvsDeckSet(
            dir_expr = project.paths["calibre_lvs_dir"],
            variants = [LvsDeckVariant(name=c, rules_suffix=c)          # 2c. choices -> variants
                        for c in _manifest_choices(template_root, "calibre", "lvs_variant")],
            default_variant = _manifest_default(template_root, "calibre", "lvs_variant"),
        ),
        qrc = QrcDeck(dir_expr=project.paths["qrc_deck_dir"]),
        extra_paths = {k: v for k, v in project.paths.items()
                       if k not in ("calibre_lvs_dir", "qrc_deck_dir")},
        power_names  = _grep1(calibre_txt, r"\*lvsPowerNames:\s*(.+)").split(),
        ground_names = _grep1(calibre_txt, r"\*lvsGroundNames:\s*(.+)").split(),
        checks = _default_checks(project),      # 每个 required_env 一条 env_var 检查
                                                # + lvs_decks/qrc 各一条 dir_exists
                                                # + tech_library_file 一条 file_exists
        discovered_from = [str(project_yaml), str(template_root)],
    )
    #    2d. corner 表：模板只给得出一个字面量，语义名问用户
    literal = _grep1(quantus_txt, r'-technology_corner\s*\\?\s*\n?\s*"([^"]+)"')   # "TYPICAL"
    cname   = ask(MigrationDecision(
        key="corner.semantic_name",
        question=f'模板里写死的 -technology_corner "{literal}"，语义名叫什么？',
        options=["typical", "rcworst", "cworst", "rcbest", "cbest"],
        default=slugify(literal)))
    profile.corners = [CornerSpec(name=cname, technology_corner=literal)]
    profile.default_corner = cname
    rep.warnings.append(f"corner 表只从模板回读到 1 个角（{literal}）；"
                        f"其余工艺角需手工补进 profiles/{profile.profile_id}.yaml")

    # 3) 分组 -> Recipe
    #    组键 = 会影响渲染的一切非身份维度：
    #      有效 knobs（manifest default < project < task，即 resolve_knob_values 的结果）
    #      + jivaro（含 jivaro_overrides 落到该 cell 后的结果）
    #      + continue_on_lvs_fail
    #      + templates（决定 output.emit）
    groups: dict[frozenset, list[TaskConfig]] = defaultdict(list)
    for t in tasks:
        eff = {stage: resolve_knob_values(load_manifest(tp), project.knobs.get(stage, {}),
                                          t.knobs.get(stage, {}), {})
               for stage, tp in _stage_templates(t)}
        key = _freeze(eff, t.jivaro, t.continue_on_lvs_fail, t.templates)
        groups[key].append(t)

    for i, (key, members) in enumerate(sorted(groups.items()), start=1):
        rid = ask(MigrationDecision(
            key=f"recipe.{i}.id",
            question=f"第 {i} 组（{len(members)} 个 cell）的配方叫什么？",
            options=[], default=f"{recipe_name_hint}-{i}"))
        r = _recipe_from_group(key, profile, quantus_txt, calibre_txt, si_txt, rid)
        #  _recipe_from_group 内部：
        #    knob 值 -> recipe.extraction.* / recipe.lvs.*
        #    模板写死值 -> recipe.extraction/lvs/netlist/output/reduction 的其余字段（回读）
        #    templates.quantus 指向 dspf.cmd.j2 -> output.emit=[dspf]
        #    jivaro -> recipe.reduction
        #    continue_on_lvs_fail -> recipe.policy
        rep.recipes.append(r)
        rep.bindings[rid] = [m.task_id for m in members]

    # 4) Cells：展开后的 tasks 去重成表（exclude 已在 load_tasks 里消解）
    rep.cells = CellBook(cells=[
        CellEntry(library=t.library, cell=t.cell,
                  layout_view=t.lvs_layout_view, source_view=t.lvs_source_view,
                  ground_net=t.ground_net, out_file=t.out_file,
                  display_name=t.label or None)
        for t in _dedup_by_key(tasks)])
    for spec in raw_specs:                          # exclude 备查
        for ex in spec.get("exclude", []):
            rep.dispositions.append(FieldDisposition(
                "tasks.yaml:exclude", "dropped", None,
                f"组合 {ex} 已在展开时排除，未生成行"))

    # 5) Workspace
    out_pat, dspf_pat = project.extraction_output_dir, project.dspf_out_path
    if "{task_id}" in out_pat or "{task_id}" in dspf_pat:
        repl = ask(MigrationDecision(
            key="path.task_id", question="路径模式里的 {task_id} 换成什么？",
            options=["{run_slug}", "{cell}"], default="{run_slug}"))
        out_pat  = out_pat.replace("{task_id}", repl)
        dspf_pat = dspf_pat.replace("{task_id}", repl)
    rep.workspace = WorkspaceConfig(
        pdk_profile=profile.profile_id, output_dir_pattern=out_pat,
        intermediate_dir=project.intermediate_dir, dspf_out_pattern=dspf_pat)

    # 6) 字节保真对拍（关键一步：迁移绝不能悄悄改 EDA 结果）
    if seed_patches:
        probe = rep.cells.cells[0]
        for r in rep.recipes:
            for target in _targets_for(r):
                old = _render_old(template_root, project, probe, r, target)   # 旧模板 + 旧 knob
                new = _render_new(catalog_root, profile, probe, r, target)    # 新 catalog + Recipe
                if _normalize(old) != _normalize(new):
                    p = _patch_from_diff(target, base_text=new, patched_text=old,
                                         catalog_version=_catalog_version(catalog_root),
                                         description="迁移自动生成：保持与旧模板逐字节一致")
                    r.patches.append(p)
                    rep.seeded_patches.append((r.recipe_id, p))
                    rep.warnings.append(
                        f"{r.recipe_id}/{target}: 新 catalog 渲染与旧模板有 "
                        f"{len(p.hunks)} 处差异，已落成 seed patch；请逐条 review 后删除")

    # 7) 落盘 + 归档旧世界
    if write:
        _write_yaml(out_root / "config/profiles" / f"{profile.profile_id}.yaml", profile)
        _write_yaml(out_root / "config/workspace.yaml", rep.workspace)
        _write_yaml(out_root / "config/cells.yaml", rep.cells)
        for r in rep.recipes:
            _write_yaml(out_root / "recipes" / f"{r.recipe_id}.yaml", r)
        _archive(project_yaml, tasks_yaml, template_root.glob("**/*.manifest.yaml"),
                 into=out_root / "config/_migrated_v1")
        _archive_or_delete(out_root / "logs", out_root / "runs")   # task_<id>/ 布局作废
        rep.written = [...]
    return rep
```

CLI：`auto-ext migrate v1 --config-dir <dir> [--dry-run] [--no-seed-patches]`，`--dry-run` 打印 dispositions + decisions + warnings 三张表，不写任何文件。

---

## 4. 新模型下的 Jinja 渲染上下文

分隔符不变（`[[ ]]` / `[% %]` / `trim_blocks`），`StrictUndefined` 不变（因此命名空间化是安全的：写错组名立刻炸，不会渲染出空字符串）。

### 4.1 上下文全表

**顶层扁平 = 只有 DUT 身份**（这六个出现在每份模板里，且确实是"身份"）

| 键 | 来源 | 现状对应 |
|---|---|---|
| `library` | `CellEntry.library` | 同名，不变 |
| `cell` | `CellEntry.cell` | 同名，不变 |
| `lvs_layout_view` | `CellEntry.layout_view` | 同名，不变（模板侧保留旧名，避免无谓改动） |
| `lvs_source_view` | `CellEntry.source_view` | 同名，不变 |
| `ground_net` | `CellEntry.ground_net` | 同名，不变 |
| `out_file` | `CellEntry.out_file` | 同名，不变 |

**`paths.*` = 解析后的工作区路径**

| 键 | 来源 | 现状对应 |
|---|---|---|
| `paths.output_dir` | `WorkspaceConfig.output_dir_pattern` 解析 | 原 `output_dir`（扁平） |
| `paths.intermediate_dir` | `WorkspaceConfig.intermediate_dir` 解析 | 原 `intermediate_dir`（扁平） |
| `paths.dspf_out` | `WorkspaceConfig.dspf_out_pattern` 解析 | 原 `dspf_out_path`（扁平） |
| `paths.run_dir` | `runs/<run_id>` | **新增** |
| `paths.work_dir` | `runs/<run_id>/work`（串行时 = workarea） | 原并行 `runs/task_<id>/`，未入上下文 |

**`pdk.*` = PdkProfile 解析结果**

| 键 | 来源 | 现状对应 |
|---|---|---|
| `pdk.tech_name` | `profile.tech_name` 或 env 推导 | 原 `tech_name`（扁平） |
| `pdk.layer_map` | `profile.layer_map` 解析 | 原 `layer_map`（扁平） |
| `pdk.tech_library_file` | `profile.tech_library_file` 解析 | 原模板里写死 `$env(SETUP_ROOT)/assura_tech.lib` |
| `pdk.cdl_include_file` | `profile.cdl_include_file` 解析 | 原 si 模板写死 `$calibre_source_added_place` |
| `pdk.lvs_dir` | `lvs_decks.dir_expr` 解析 | 原 `calibre_lvs_dir`（来自 `paths.*`，扁平） |
| `pdk.lvs_basename` | `lvs_decks.basename` 或末段推导 | 原 `calibre_lvs_basename`（runner 自动推导，扁平） |
| `pdk.lvs_rules_file` | 三段拼好的完整路径 | **新增**（原来在模板里手拼三段） |
| `pdk.qrc_deck_dir` | `qrc.dir_expr` 解析 | 原 `qrc_deck_dir`（扁平） |
| `pdk.qrc_query_cmd` | `qrc_deck_dir/query_cmd_name` | 原模板写死 `[[qrc_deck_dir]]/query_cmd` |
| `pdk.qrc_preserve_cell_list` | `qrc_deck_dir/preserve_cell_list_name` | 原模板写死 `preserveCellList.txt` |
| `pdk.power_names` | `profile.power_names`（list） | 原 calibre 模板写死整行 |
| `pdk.ground_names` | `profile.ground_names`（list） | 原 calibre 模板写死整行 |
| `pdk.corner` | `CornerSpec.technology_corner`（由 `recipe.extraction.corner` 查表） | 原模板写死 `"TYPICAL"` |
| `pdk.temperature_c` | `recipe.extraction.temperature_c` 或 corner 默认 | 原 knob `temperature` |
| `pdk.paths.<key>` | `profile.extra_paths` 逐项解析 | 原 `project.paths.*` 自定义键（扁平注入） |

**`recipe.*` = Recipe 全部字段**，一一对应 §1.2 的分组：`recipe.netlist.*` / `recipe.lvs.*` / `recipe.extraction.*` / `recipe.output.*` / `recipe.reduction.*` / `recipe.policy.*`，再加 `recipe.id` / `recipe.name` / `recipe.version`。**旧的 7 个 knob 全部落在这里**，迁移规则一句话可 grep：*凡是原来 `[[foo]]` 且 foo 是 knob 的，改成 `[[recipe.<组>.<foo>]]`*。

**`run.*` = 运行元数据（全部新增）**：`run.id` / `run.slug` / `run.started_at` / `run.stage`（当前 stage key）/ `run.dry_run` / `run.batch_id`。用途：在渲染结果头部盖溯源注释（`# auto_ext run [[run.id]] recipe [[recipe.id]]@[[recipe.version]]`），让 EDA 工作区里捡到的任何一个 `.cmd` 都能反查回 run 目录。

**`site.*`**：`site.employee_id`（原扁平 `employee_id`）、`site.user`、`site.host`、`site.workarea`。

**`env.*`**：`env.<VAR>` = `resolve_env()` 的结果。`substitute_env` 仍在渲染后照跑（Cadence 自己要解析 `$env(X)`），`env.*` 只是让模板能在 Jinja 层做判断（`[% if env.LSF_QUEUE %]`）。

### 4.2 与 `_IDENTITY_KEYS` 的差异

`manifest._IDENTITY_KEYS` 现有 19 个保留名：

| 旧保留名 | 新去向 |
|---|---|
| `library` `cell` `lvs_layout_view` `lvs_source_view` `ground_net` `out_file` | **不变**，仍是顶层扁平 |
| `output_dir` | → `paths.output_dir` |
| `intermediate_dir` | → `paths.intermediate_dir` |
| `dspf_out_path` | → `paths.dspf_out` |
| `layer_map` | → `pdk.layer_map` |
| `tech_name` | → `pdk.tech_name` |
| `employee_id` | → `site.employee_id` |
| `jivaro_frequency_limit` | → `recipe.reduction.frequency_limit_ghz`（模板里 `| default(14)` 的兜底删除，改由 pydantic 默认值负责） |
| `jivaro_error_max` | → `recipe.reduction.error_max_pct`（同上，`| default(2)` 删除） |
| `task_id` | **删除**。不在上下文里，也不在任何路径格式键里。要人读的名字走 `run.slug`；要溯源走 `run.id` |
| `pdk_subdir` | **删除（本就是死名）** |
| `project_subdir` | **删除（本就是死名）** |
| `lvs_runset_version` | **删除（本就是死名）** |
| `qrc_runset_version` | **删除（本就是死名）** |

> 后四个是废弃的段抽取模型（`importer.PdkToken` 的 docstring 明写 *"Phase 5.6.5 简化为 tech_name + abs_path / unknown；pdk_subdir / runset_version / project_subdir 段抽取模型已被放弃"*）留下的保留名 —— `runner._build_context` 从未产出它们，`_IDENTITY_KEYS` 却一直在为它们挡 knob 命名。新模型直接清掉。

**非 `_IDENTITY_KEYS`、但现状会动态注入上下文的**（这一层"隐式注入"正是"哪些参数能调取决于当初谁手写了 manifest"的另一半病根，新模型全部显式化）：

| 现状注入点 | 键 | 新去向 |
|---|---|---|
| `for key, expr in project.paths.items(): ctx[key] = resolve_path_expr(...)` | `calibre_lvs_dir` | `pdk.lvs_dir` |
| 同上 | `qrc_deck_dir` | `pdk.qrc_deck_dir` |
| 同上（用户自定义键） | 任意 | `pdk.paths.<key>` |
| `if "calibre_lvs_dir" in ctx and "calibre_lvs_basename" not in ctx: ...` 自动推导 | `calibre_lvs_basename` | `pdk.lvs_basename`（仍可自动推导，但推导规则搬进 `LvsDeckSet`） |
| `resolve_knob_values()` 的返回值被 `Tool.render_template(knobs=...)` **平铺进上下文** | `lvs_variant` `connect_by_name` `exclude_floating_nets_limit` `coupling_cap_threshold_absolute` `coupling_cap_threshold_relative` `min_res` `temperature` | 分别落到 `recipe.lvs.deck_variant` / `recipe.lvs.connect_by_name` / `recipe.extraction.exclude_floating_nets_limit` / `...coupling_cap_threshold_absolute` / `...coupling_cap_threshold_relative` / `recipe.extraction.min_res_ohm` / `pdk.temperature_c` |

**净增**（现状完全没有的上下文变量）：`paths.run_dir`、`paths.work_dir`、`pdk.lvs_rules_file`、`pdk.tech_library_file`、`pdk.cdl_include_file`、`pdk.qrc_query_cmd`、`pdk.qrc_preserve_cell_list`、`pdk.power_names`、`pdk.ground_names`、`pdk.corner`、整个 `recipe.*` 树（约 70 个字段，其中约 60 个是从模板字面量提升上来的）、整个 `run.*`、整个 `site.*`、整个 `env.*`。

**上下文构造函数签名**（取代 `runner._build_context(project, task, resolved_env)`）：

```python
def build_context(
    *,
    dut: CellEntry,
    recipe: Recipe,
    profile: PdkProfile,
    workspace: WorkspaceConfig,
    run_dir: Path,
    run_id: str,
    stage_key: str,
    resolved_env: dict[str, str],
) -> dict[str, Any]:
    """纯函数，无 I/O。返回的 dict 同时被 Jinja 渲染和
    RunRecord.context（扁平化成点号键后）消费 —— 两者共用同一份，
    保证 run.json 里记的就是真正渲染用的那份。"""
```

`RunRecord.context` 存的是这个 dict 的扁平化（`{"recipe.extraction.min_res_ohm": 0.001, ...}`），列表值 join 成字符串，从而满足 JSON 可序列化且便于 UI 做两次 run 之间的 diff。

---

**关键文件（绝对路径）**

- 读：`C:\code\Auto_ext\Auto_ext\auto_ext\core\config.py`、`runner.py`、`env.py`、`manifest.py`、`checks.py`、`template.py`、`workdir.py`、`importer.py`、`progress.py`、`tools\base.py`、`config\project.yaml`、`config\tasks.yaml`、`templates\{calibre,quantus,si,jivaro}\*`
- 新建：`auto_ext\model\{common,pdk,recipe,run,cells,workspace}.py`
- 改写：`auto_ext\migrate.py`（现为 `migrate_run_ext` 的 `NotImplementedError` 桩，第 11 行）
- 删除：`auto_ext\core\manifest.py`、`preset.py`、`clone_template.py`、`ui\widgets\knob_editor.py`、`ui\widgets\preset_picker.py`、`templates\*\*.manifest.yaml`（迁移后归档）