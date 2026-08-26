# Next round — one pass, no stages

Written 2026-08-25, at the end of the round that shipped `59850b6` + `e2b722c`
(Recipes form stages 1+2). The user has asked for the whole remainder in a
single round rather than staged.

Read first: `RECIPES_FORM.md` (design of record for the form), the Claude Design
project *Recipes 页面重做设计* (`Recipes Spec.dc.html` page `M` is normative),
and `private/pdf_answers/FINDINGS.md` for the Quantus manual findings — **with
the warning in §2 below**.

Ordering matters in one place only: **§3 (the `extract` model change) must land
before §4's sub-form**, because the UI has nothing to draw until the model is a
list. Everything else is independent.

---

## 1. The three the user raised on 2026-08-25

### 1.1 Corner belongs in Quantus — ruled

`extraction.corner` renders under **Flow**, last on the page, because the
grouping rule is "no landing site → Flow" and this row genuinely has none: the
literal that reaches the tool is the profile-owned `technology_corner`.

The user's ruling: **it is a Quantus setting, put it in Quantus.**

Do it with a new catalog column rather than a hand exception:

```yaml
  - key: extraction_corner
    groups_with: technology_corner   # "no landing site of my own; draw me where that row lands"
```

`technology_corner` lands in `quantus.{ext,dspf}.cmd` section
`process_technology`, so corner lands in **Quantus → process & technology**,
beside `temperature_c`, which is where a person looks for it.

- `OptionSpec.groups_with: str | None`
- `_tool_of()` in `recipes_screen.py` resolves it before falling back to Flow
- **exactly one row uses it today** — say so in the column's docstring, and add
  a catalog self-check that the target key exists and itself has a landing site
- Flow keeps the other four (stages, reduction on/off, the two policy flags),
  which really are decisions about the run rather than lines in a file

**Also fix, same area:** the corner combo is `choices_from: profile.corners`,
so under the demo profile it offers exactly one corner and looks broken. It is
not broken — the real PDK has nine — but nothing on screen says the list comes
from the loaded profile. Add that to the row's hint, e.g.
`from the PDK profile — 9 corners available` (artboard `A` draws exactly this).

### 1.2 `out_file` / `av_extracted` — ownership accepted, pointer row missing

The user accepts that the extracted-view name is per-cell and lives on the Cells
screen. What is missing is the **disabled pointer row** artboard `A` draws on
the Recipes screen:

```
output view name    av_extracted   set per cell, not per recipe — open the Cells column
```

Not built, and not previously listed as unbuilt. Build it, and see §5 for the
DSPF half of the same problem.

Mechanics: `screen: cells` rows are `owner: cells`, so they have no
`recipe_field_path` and cannot bind. They need a non-binding read-only row type
that shows the catalog default and carries a navigate action.

Spec `M` §3 says Common draws **two** such rows — "the two per-cell settings a
recipe author asks about". Today all six `owner: cells` rows carry
`tier: elsewhere`. Split them:

| row | treatment |
|---|---|
| `out_file`, `ground_net` | `tier: elsewhere` → pointer row on the form |
| `library`, `cell`, `lvs_layout_view`, `lvs_source_view` | identity, not settings → searchable only |

### 1.3 `exclude_floating_nets_limit` stays 5000 — ruled

Cadence's documented default is **2000** (extUser p.455); ours is **5000**. The
user has ruled: **keep 5000.**

No value change. What to do is record it, so the next person reading the manual
does not "fix" it back:

- add a `notes` line saying 5000 is a deliberate site choice, not an oversight,
  and that the vendor default is 2000
- **remove this item from the correction list in §2** — it is closed, not open

---

## 2. Catalog corrections from the extUser.pdf probe

Source: `private/pdf_answers/FINDINGS.md` (gitignored — the manual text must
never enter the public repo).

> ⚠ **`FINDINGS.md` §1's summary table misreads the current catalog in four
> rows** (`extract_type`, `input_db_format`, `dspf_subtype`, `output_xy`, and it
> calls `metal_fill_type`'s bad member `real` when it is `actual`). The
> *direction* of every conclusion is right; the "catalog currently says X" column
> is not. **Diff against the real catalog, do not transcribe that table.**

### 2.1 Wrong or incomplete `choices`

| key | today | should be |
|---|---|---|
| `extract_type` | 4, incl. non-existent `c_only` | the 15 legal values (p.388-390); all are usable in our LVS/QCI flow |
| `extract_selection` | `all / specified_nets / selected_nets` | `all / net / nets_file / selected_path_file` — and the last three **take an argument** (§3) |
| `max_fracture_length_unit` | 4 values | `microns / squares` only |
| `metal_fill_type` | `none / virtual / actual` | `floating / grounded / virtual / none` — `actual` does not exist |
| `dspf_subtype` | `standard / extended` | `standard / extended / compatible / compact` |
| `netlist_coupling_values` | `double / single` | + `separate` |
| `input_db_format` | `["DFII"]` | Assura-only option; see §6 |

### 2.2 Three booleans that are three-state

`include_cap_model`, `include_res_model`, `include_parasitic_cap_model` are
`type: bool` and marked `certain`. The tool accepts `true | false | comment`.

**This is a real bug, not a data nit:** the templates render them as
`[[ 'true' if x else 'false' ]]`, so `comment` is unreachable — the same shape
as `include_parasitic_res_model`, which is already an enum and already works.
Fix the catalog rows **and both `.j2` templates**, and add a render test.

### 2.3 `quantus_cpu_count`'s notes are false

The row says *"No multi_cpu section exists in either template, so extraction is
single-threaded today."* extUser p.383: with no `distributed_processing` section
**Quantus defaults to two CPUs**. `OFFICE_TODO.md:110` is about to ask the
office a question built on the false premise — fix both.

License ceiling is a hard constraint worth encoding: `ceil(N/2)` Quantus
licenses for N CPUs (p.19, p.383).

### 2.4 Confidence upgrades

Nine values are confirmed as Cadence defaults (FINDINGS §2) — `device/instance/
net_property_value` 7/6/5, both `hierarchy_delimiter`s, `device_finger_delimiter`,
`min_res`, `decoupling_factor`, `cap/res_component` + property names. Move
`guess`/`likely` → `certain`.

This has a visible effect: the unverified `?` count drops, and Q4's note says an
open-list enum whose confidence reaches `certain` can become a closed dropdown
automatically. Check which rows that frees.

### 2.5 New options worth adding

`distributed_processing -multi_cpu` (Common tier — it is a real lever),
`global_nets` (decides what `-selection all` actually covers, which for RF is
not a detail), `-use_field_solver`, `-extract_via_cap`,
`-parasitic_blocking_device_cells_type`, `unique_qrctemp_name`, `-file_max_size`.

Each needs `tier`, and any that are format-specific need `requires_emit`.
Adding them is what takes the catalog past 100 rows — the grouping absorbs it
without a decision, which was the point of the section map.

---

## 3. `extract` becomes an ordered list

The largest single item, and the only one with ordering constraints.

extUser p.389: `extract` **may appear multiple times**, specifications
accumulate first-to-last, and **the last one wins for any net it covers**. The
vendor's own worked example is the standard RF cost-saving strategy:

```
extract -selection all             -type c_only_coupled    # whole chip, C only
extract -selection nets_file "clk" -type rc_coupled        # these nets get R too
```

Today `extraction.selection` and `extraction.extract_type` are two scalars and
**cannot express this at all**.

Touches, in dependency order:

1. **`Recipe`** — `extraction.extract` becomes `list[ExtractRule]`, each
   `{selection, selection_arg, type}`. Keep a validator that rejects an empty
   list.
2. **`options.yaml`** — `choice_args` on `extract_selection`:
   `all → nothing`, `net → pattern`, `nets_file` / `selected_path_file` → file.
3. **Both templates** — one `extract` block per rule, in order.
4. **`render`** — emit the list; `check_representable` must understand it.
5. **`migrate`** — a v1 recipe's two scalars become a one-rule list.
6. **The three recipes already on the red-zone disk** — they are v1-shaped.
   Either the loader upgrades in place, or the deploy carries a migration step.
   **Decide which before writing code**; a silent in-place upgrade of a file the
   user hand-edited is the kind of thing that loses work.
7. **Tests** — round-trip, ordering, the override semantics.

---

## 4. The Recipes form's remaining design

### 4.1 Row-state visuals — half built (artboard `H`)

The *logic* landed; the *marks* did not. Currently a promoted row and a normal
row look identical.

| state | logic | visual |
|---|---|---|
| promoted into Common | ✅ | ❌ 3px accent bar + `#e8f1fb` tint + bordered `promoted` tag naming the default it left |
| changed from default | ✅ (dirty tracking) | ❌ accent bar, bold value, `was X` in mono |
| `requires_emit` miss | ✅ disabled + tooltip | ❌ the row should say *why* on-row, not only on hover |
| unverified | ✅ | ✅ amber `?` |
| out of advisory range | ✅ | ✅ amber border |
| frozen by template | ✅ | ✅ grey `=` |

`H`'s rule is two channels that never share a pixel: **accent = a person set
this** (bar at x=0), **amber = we are not certain this is right** (flag at the
far right). Implement as QSS attribute selectors (`[state="changed"]`) so no
new colour is introduced.

### 4.2 The focused-row detail pane (`Q3-d`, spec `M` §4)

A 42px strip under the form describing **only the focused row**: model path,
`why`, default, advisory range, open question, and the exact generated line it
writes (`→ ext.cmd line 11`), plus `Reset to default`.

This is what lets All view carry no per-row prose at all, and it repaints two
widgets on focus change rather than the page — which matters on forwarded X11.
At 1600 it becomes the full side pane of artboard `D`.

### 4.3 Search result bands (`J`)

Search works and covers the whole catalog; the *presentation* is a status line.
Build the three labelled bands — `IN COMMON` / `IN ALL ONLY` / `NOT ON THIS
SCREEN` — with results editable in place, `Esc` returning to the previous mode
and scroll position, and an **Open in Cells** button that navigates and selects
the column.

### 4.4 Multi-select popup polish (`I1`)

`N of M` + popup shipped. Missing: the `all` / `none` shortcuts and the
"type to add a value the list does not have" field.

---

## 5. Naming and where output lands — the DSPF half of §1.2

Answering the user's question, because it exposed a bug.

**The two output formats are named by two different mechanisms, and that is
correct:**

| | `extracted_view` | DSPF |
|---|---|---|
| named by | `out_file` | `dspf_out_pattern` |
| owner | `CellEntry` (per cell) | `WorkspaceConfig` (per project) |
| edited on | Cells page, `out view` column | **Project page**, "DSPF output" |
| default | `av_ext` | `${WORK_ROOT2}/{cell}.dspf` |
| what it is | a **view name** inside the OA library | a **full filesystem path** |
| reaches the tool as | `output_db -type extracted_view -view_name` | `output_setup -file_name` |

An extracted view is a cellview *inside* the library — library and cell come
from the cell itself, so only the view name is left, and it is naturally
per-cell. A DSPF is a file on disk, so it needs a whole path, and that path is a
workspace convention rather than a per-cell decision. It is a **pattern**:
`{cell}` / `{library}` / `{task_id}` are substituted per task, plus env vars and
`${output_dir}`-style path tokens, resolved in `runner._resolve_dspf_out_path`.

So: **one pattern, expanded per task.** Set it once on Project; each cell gets
its own file.

### 5.1 🔴 Real bug: nobody creates the DSPF parent directory

extUser p.550: *"If you specify a directory as part of the filename option, the
directory must already exist."* Quantus creates `output_setup -directory_name`
but **not** the directory inside `-file_name`.

`grep -n "mkdir" auto_ext/core/runner.py` — the only calls are for
`paths.results` and the si.env archive dir. **Nothing creates the DSPF file's
parent.**

It works today by luck: the default `${WORK_ROOT2}/{cell}.dspf` writes straight
into `WORK_ROOT2`, which exists. Any pattern with a sub-directory —
`${WORK_ROOT2}/dspf/{cell}.dspf`, which is an obvious thing for a user to type
into the Project page — fails inside Quantus, hours into a run.

Fix: `mkdir(parents=True, exist_ok=True)` on the resolved path's parent before
the Quantus stage, plus a preflight check so `--dry-run` catches it.

### 5.2 Discoverability, same failure as `out_file`

"Where do I set the DSPF file name" has no answer on the Recipes page either.
Give `dspf_out_path` the same pointer row as §1.2, pointing at the **Project**
screen. It is already a catalog row (`owner: run`), so it needs `screen: project`
— i.e. `Screen` grows a third member.

### 5.3 Two more from the manual, both cheap

- **A per-task `dspf_out_path` override exists in the model** (`config.py:23`)
  and has **no UI anywhere**. Either surface it as a Cells column or decide it
  is not wanted — an unreachable capability is the thing this project exists to
  end.
- **LVS-input runs auto-split output files at 2 GB**, appending `.1`, `.2`
  (FINDINGS §7.8). RF top-levels hit this. Anything downstream that reads a DSPF
  must handle shards; check what does, and say so in the docs if nothing can.

---

## 6. Verify on the real tool — near-zero cost, currently unknown

Two options are in our templates but absent from the manual's option tables for
our input type. The templates came from a Quantus GUI **18.21-s340** export
while the tool actually run is **18.12**, so the table's silence is not proof.

- `input_db -type calibre -format "DFII"` (ext.cmd only)
- `output_db -type extracted_view -device_finger_delimiter`

**One real run answers both**: Quantus either rejects the unknown option or does
not. Do this before deciding to remove either.

Also from the probe, both worth doing while there:

- **`corner.defs`** in the technology directory is the authoritative list of
  corner names, and Quantus validates against it (p.84). Discovery rule R6 can
  be promoted from best-effort, and `check-env` can gain a real check.
- **`output_net_name_space = SCHEMATIC` requires `query_output/*.ixf`** to
  exist, or the run fails. A preflight check is cheap and catches it before the
  hours are spent.

---

## Open questions — answer these, do not guess

1. **The red-zone recipes and the `extract` migration** (§3.6). In-place upgrade
   on load, or an explicit migration step in the deploy? Those three files may
   have been hand-edited.
2. **Per-task DSPF override** (§5.3) — surface it, or declare it unwanted?
3. **`Screen` gains `project`** (§5.2) — confirm, or keep pointer rows to Cells
   only.
4. **`-use_field_solver`** — every official example in the manual sets
   `default_accuracy` and our templates omit it entirely, so we run the tool's
   default. What does the office set in the GUI? This is an accuracy knob on the
   critical path.
5. **`global_nets`** — for RF, whether power/ground parasitics are extracted is
   not a detail, and `-selection all` excludes whatever `global_nets` defines.
   We model neither. What is wanted?

## Definition of done

- `pytest` green on Windows; note the count in the commit
- `sh scripts/redzone_scan.sh --staged` clean over everything committed
- `docs/refactor/RECIPES_FORM.md` §6 ("Not built yet") is **empty or deleted**
- the screen renders correctly at 940×560, 1280×800 and 1600×900 — check it,
  do not assume it
- `docs/refactor/OFFICE_TODO.md`'s multi-core question is rewritten off the
  false single-core premise (§2.3)
