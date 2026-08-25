# The Recipes form — grouping, density, and the four catalog columns behind them

Design of record for the Recipes screen's presentation rules. The visual design
lives in the Claude Design project *Recipes 页面重做设计* (`Recipes Redesign.dc.html`
for the artboards, `Recipes Spec.dc.html` for the normative spec page `M` and the
section map `L`). **Where an artboard annotation and spec page `M` disagree, `M`
wins.** Where `M` and this file disagree, this file wins — it records what was
actually built, including the two places the build deviated and why.

Everything here is data. There is no field list, no section list and no width
table in any widget: `option_editor.py` and `recipes_screen.py` read
`catalog/options.yaml` and nothing else. That constraint is not stylistic — the
system this replaced exposed 7 settings because 7 had been typed into a manifest
by hand, and the other hundred-odd were reachable only by editing a `.j2`.

## 1. Four new columns on `OptionSpec`

| column | authored? | rows today | what it decides |
|---|---|---|---|
| `tier` | authored | 19 `common`, 6 `elsewhere`, rest `full` | which density draws the row |
| `screen` | authored | 6 `cells` | which screen owns the value |
| `requires_emit` | authored | 14 | which output formats the row applies to |
| `sections` (table) | authored | 23 | the level-2 heading each template section gets |

`tier` **defaults to `full`**, where spec `M` calls it required on every row.
Deviation, and deliberate: `full` is the answer that hides nothing. An
unclassified row appears in All view, is found by search, and is promoted into
Common the moment its value leaves the default. Requiring the column would be
the safer choice only if the *unsafe* value were the silent one, and it is the
other way round. The cost of the alternative was 85 mechanical YAML edits whose
only effect would be to spell out the default.

## 2. Grouping — two levels, identical in both densities

**Level 1 is the tool**, in pipeline order: si → Calibre LVS → Quantus → Jivaro,
then the synthetic `Flow` bucket. Read off `lands_in[].target`'s `template_id`
directory, which spells the *tool* (`calibre`) where the target id spells the
*file* (`lvs.qci`).

The grouping this replaced was the first component of `recipe_field_path` —
`extraction` / `output` / `netlist`. That is the shape of our data model and of
nothing the user has ever seen. They think in tools, and when a run fails the
manual in their hand is that tool's.

**Level 2 is `SectionDisplay`** — the generated file's own section names, run
through a 23-row table (`options.yaml`'s `sections:`, artboard `L`). The table
does three things and all three are needed:

- **rename** — `device_check` reads "device checks"
- **merge** — a shared `group`; `filter_cap` + `filter_coupling_cap` +
  `capacitance` render as one "capacitance" heading
- **split** — `split_by: requires_emit`; `output_db` becomes one heading per
  emitted format, because the vendor documents four *different* option sets
  under that one name

Authored **per section, never per option**: adding an option touches it never,
adding a template section touches it once. A section missing from the table is
not an error — it renders under its raw name at order 999, so a new tool section
appears ugly, visible, and fixable in one line.

Two properties the code holds and the tests assert:

- **A row is drawn once.** 23 Quantus rows write both command files; drawing
  them twice would ask the user which copy is the real one.
- **A row never changes parent between densities.** That is what the mode
  toggle's "keep the focused row" behaviour depends on.

Safe because **no option carries a different `section` in different target
files** — verified across all 80 rows that have a landing site. The other 5 have
none and are the `Flow` bucket: they are decisions about the run rather than
lines in a file (corner, stages, reduction on/off, two policy flags).

## 3. Density — what Common shows

Common draws a row when **any** of:

- `tier == common` (19 today), **or**
- its value differs from `default` — *promoted*, whatever the tier says

Hiding rows is only allowable because nothing becomes unreachable, and three
rules enforce that:

1. The toggle is always on screen, never in a menu, never disabled.
2. Search always covers the whole catalog — including the 66 rows Common is
   hiding and the 6 rows another screen owns.
3. A non-default value is never hidden. **A Common view that omits one is a bug,
   not a preference** — the form would be lying about what the run will do.

A row that returns to its default **stays visible** until the next mode switch,
save or recipe change. It must not disappear under the cursor that just reset it.

A tool with zero Common rows still gets its heading line, stating the count and
that every row sits at the catalog default. `si` is that tool today. One that
vanished would read as *a stage that is not being run*, which is a different and
much more alarming claim.

## 4. Row geometry

Five columns: marker · label · control · annotation · flag.

- **Label**: `minmax(120px, 292px)`. The floor is the load-bearing half. The
  inner `ElidedLabel` pins its own minimum to zero so the screen can reach the
  940px window floor, and without a floor here the grid took that literally and
  *removed whole label columns* — at 1280px the entire Output section rendered
  as anonymous check boxes reading only "default off".
- **Column count**: `clamp(floor(available / 370), 1, 2)`. This is what makes
  the label floor affordable: the grid gives up a column before it gives up a
  label.
- **Control**: `value_width()` — max characters over `choices ∪ {default}`,
  clamped 4–24, then raised to a per-type floor. Computed, never authored; an
  override in the catalog would be the hand-curated exception this module exists
  to avoid.

  **Deviation from `M`:** spec `M` states the per-type floor unconditionally,
  which gives `device_finger_delimiter` a 12-character box to hold `@` and
  contradicts artboard `I3`, which draws that row at 30px. The floor is applied
  **only when there is nothing to measure** — no choices and a default that is
  absent or empty. Both pages are then true: `@` measures 1 and clamps up to 4,
  while `netlist.global_power_sig` measures 0 and gets the 12 it needs to hold a
  supply-net name.

- **Multi-value rows** are always the `N of M` summary control with a popup,
  never inline members. Eight members spelled out is 81 characters of row width
  for one value, and it still overflowed into a `…` button; the popup overlays,
  so opening it reflows nothing.

## 5. Disabled, hidden, absent

| case | treatment | why |
|---|---|---|
| `requires_emit` miss | **disabled**, visible | The option exists and the tool accepts it; this recipe just does not reach it. Hiding it would say "this tool has no such setting", which is false. Rendering it anyway writes a command file the tool rejects, hours into a run. |
| template freezes the value | **disabled**, visible, with the reason | Same rule. `check_representable` refuses the render; that is a good last line of defence and a bad first one. |
| `tier: full`, at default, in Common | **hidden** | Only because one always-visible toggle and search both reach it. Never hidden when non-default. |
| tool with 0 Common rows | **one-line strip** | A tool must never disappear — see §3. |

## 6. Not built yet

Everything in this file is implemented and tested. From the design, two pieces
are not:

- **The focused-row detail pane** (spec `M` §4, artboard `Q3-d`) — the 42px
  strip that describes the focused row and lets All view carry no per-row prose
  at all. Until it exists, All view keeps the on-row hint.
- **The repeating `extract` sub-form** (artboards `F1`–`F3`) — blocked on the
  Recipe model change that turns `extract` from two scalars into an ordered
  list. That change ripples into `render`, `migrate`, the tests and the three
  recipes already on the red-zone disk, so it is its own round.

Search currently reports off-screen matches by name in the status line; the
three labelled result bands of artboard `J`, and the button that navigates to
the Cells screen, are part of the detail-pane round.

## 7. The library list — overruling artboard `G`

`G` drew the list as `recipe_id` (mono, first line) over `description`, with
`name` living only in the form header. The reasoning was sound and is worth
keeping on the record: the migrator writes names like `rc_coupled, corner
typical, 55C, extracted_view, with reduction` — sixty-three characters, of
which twelve fitted the 214px column — so a list drawn from `name` was a list
of truncated sentences.

**The user overruled it on 2026-08-25.** The report was "我在recipe页把recipe
更名之后，左边的菜单栏上的内容并没有跟着改名". Whatever the column is
technically showing, a rename that leaves the list unmoved reads as a rename
that did not take, and that reading costs more than the truncation did.

What is drawn now, top to bottom:

| line | field | treatment |
|---|---|---|
| 1 | `name` | body size, DemiBold, **wrapped over up to two lines**, elided past that |
| 2 | `recipe_id` | mono, meta size, secondary, middle-elided |
| tooltip | `description` | falls back to `recipe_id` when absent |

Wrapping is what makes this survivable where `G`'s objection was not: the
sixty-three-character name takes two lines and the row grows, rather than
being cut at twelve characters. `recipe_id` keeps a line of its own because it
is the file name and the string every error message quotes. `sizeHint` and
`_paint_two_lines` measure the same way, so the row is never clipped.

**One writer.** `_fill_list_item` is the only thing that writes a list row,
called by both `set_recipes` and `_on_name_edited`. That is not tidiness: the
bug underneath the user's report was those two writing *different* meanings
into column 0 — `set_recipes` the id, `_on_name_edited` the name — after
`e2b722c` changed what column 0 meant and left the rename handler behind. Two
call sites agreeing is a rule someone has to remember; one call site is a rule
that holds itself.
