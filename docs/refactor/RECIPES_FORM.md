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
| `groups_with` | authored | 17 | where to draw a row that lands in no file |

`tier` **defaults to `full`**, where spec `M` calls it required on every row.
Deviation, and deliberate: `full` is the answer that hides nothing. An
unclassified row appears in All view, is found by search, and is promoted into
Common the moment its value leaves the default. Requiring the column would be
the safer choice only if the *unsafe* value were the silent one, and it is the
other way round. The cost of the alternative was 85 mechanical YAML edits whose
only effect would be to spell out the default.

### 1.1 `groups_with` — added 2026-08-25

Level 1 is the tool, read off the row's landing site, and a row with no
landing site falls into the synthetic `Flow` bucket. That was right for the
five rows Flow was built for — which stages, reduction on or off, the two
policy flags, all decisions *about* the run rather than lines in a file — and
wrong the moment a real setting had no site of its own.

`extraction_corner` is the case that forced the column. What reaches Quantus
is the profile-owned `technology_corner` literal, so the corner row lands
nowhere and collected under Flow, while the person looking for it looks under
Quantus beside the temperature. The user ruled on it directly: *it is a
Quantus setting, put it in Quantus.*

```yaml
  - key: extraction_corner
    groups_with: technology_corner   # no landing site of my own — draw me where that row lands
```

A column rather than an exception in `recipes_screen.py`, because the next
such row then costs one line and no code. It resolves **one level only** and
the catalog self-check enforces both halves: the named key must exist, and it
must itself land somewhere. A row carrying both `groups_with` and its own
`lands_in` is refused — one row, one place.

The sixteen rows added by the catalog-correction round all use it. They are
`currently: absent` (the tool has the option, we emit nothing, we take the
tool default), so none of them has a landing site, and without this column
every one of them would have piled into Flow. That is what "the grouping
absorbs the new rows without a decision" turned out to require.

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
| member in `choices_not_offered` | **absent from the control**, present in `choices` | The one case where hiding is right, and only because the alternative is worse: the tool accepts the value, we cannot render a complete deck for it, and the model refuses it — so an offered entry would be a click that can only end in an error dialog. Search still finds the row, its `why` still names the member, and readback still shows it. See §8.1. |
| `tier: full`, at default, in Common | **hidden** | Only because one always-visible toggle and search both reach it. Never hidden when non-default. |
| tool with 0 Common rows | **one-line strip** | A tool must never disappear — see §3. |

## 6. Built, 2026-08-26

This section used to list what was missing. It is empty on purpose, and the
list is kept here as a record of what closed rather than deleted, because two
of the three turned out to cost more than the plan said.

- **The focused-row detail pane** (spec `M` §4, artboard `Q3-d`) — a 42px
  strip under the form describing only the focused row: model path, `why`,
  default, advisory range, open question, the exact generated line it writes,
  and Reset. One `focusChanged` connection for the whole screen rather than a
  `focusInEvent` on ninety editors. It keeps the last description when focus
  leaves the form — otherwise the sentence vanishes at the moment the user
  acts on it.
- **The repeating `extract` sub-form** (artboards `F1`–`F3`) — see §8.
- **Search result bands** (artboard `J`) — `IN COMMON` / `IN ALL ONLY` /
  `NOT ON THIS SCREEN`, with the third carrying an Open button. The bands are
  labels on the existing group headers, not three new containers: re-parenting
  results would make a row change parent depending on how the user reached it,
  and the mode toggle's "keep the focused row" behaviour depends on parents
  being stable. `Esc` restores the density **and** the scroll offset, both
  captured on the way *in*.
- **Row-state marks** (artboard `H`) — the logic had shipped and the marks had
  not, so a promoted row and an ordinary one looked identical. Two channels
  that never share a pixel: accent at x=0 means *a person set this*, amber at
  the far right means *nobody has checked this*. QSS attribute selectors on a
  dynamic property, so no new colour entered the palette.
- **Multi-select `all` / `none`** (artboard `I1`) — one emission per click,
  not one per member. The "type a value the list does not have" field already
  existed for guessed member lists; no shipped row is one today, which is why
  its test builds the spec instead of finding it.

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

## 8. `extract` is a list, and the sub-form that edits it

`extract` may appear more than once in a Quantus command file. Specifications
accumulate first-to-last and **the last one wins for any net it covers**. The
vendor's own worked example is the standard RF cost-saving strategy:

```
extract -selection all             -type c_only_coupled
extract -selection nets_file "clk" -type rc_coupled
```

Whole chip at capacitance only; the nets that matter get R as well. Two
scalars could not express it, so the one thing an RF engineer most wants from
this tool was unreachable — from the GUI, the YAML and the CLI alike.

### Two new catalog columns this needed

| column | what it says | rows |
|---|---|---|
| `describes_member` | this row describes one field of one member of the collection at `context_path`, not a scalar | 2 |
| `choice_args` | which enum members take an operand, and what kind | 1 |
| `choices_not_offered` + `not_offered_reason` | members the tool accepts and this form does not draw, and why — added 2026-09-04, see §8.1 | 1 |

`describes_member` is why `extract_selection` and `extract_type` have a real
`context_path` and a `recipe_field_path` of `None`. The value genuinely lives
at `recipe.extraction.extract`; what does not exist is a *single* value a
control could bind to. Pointing them at `null` instead would have made them
look like the structural rows that bind to nothing at all, which is a
different and less true statement.

### 8.1 `choices_not_offered` — added 2026-09-04

`extract -type` has fifteen members and the combo now draws six. The other
nine are listed on the row as `choices_not_offered` with a
`not_offered_reason`, and `ExtractRule` refuses them.

The reason they could not simply be deleted from `choices` is that the two
edits make different claims. Removing a member says **the tool has no such
value**; listing it here says **the tool has it and we decline to offer it**.
`choices` is what readback matches a colleague's deck against, what the
importer names in its report, and what the model quotes when it refuses — so a
deleted member stops being nameable and the deck that carries it imports as
"not one of the catalog's choices", which is this tool calling the vendor
wrong.

What decided it was not taste. Each of the nine renders a deck Quantus accepts
and runs to a clean exit while silently omitting the thing the member was
chosen for: the six `rlc*`/`rlck*` members need `-ind_component` /
`-mutual_ind_component`, which no template emits, so the netlist comes back
with no inductor in it; `substrate_only` and the three `*_to_substrate`
members need `substrate_nets_file`, and the manual says that without it *no
nets are extracted as connected to the substrate*. The owner ruled on
2026-09-04 that a knob they do not understand is a knob they will never use —
**not understood is not offered** — and the second half of that rule is that
nothing the form *does* offer may quietly produce an incomplete deck.

Mechanics:

- `OptionSpec.offered_choices` is `choices` minus the exempted members, in the
  vendor's own order. Every control reads that; `choices_for()` too.
- The catalog self-check refuses a member with no reason, a reason with no
  member, a member that is not in `choices`, a set that hides every member,
  and a set that hides the row's own `default` — the last because a new recipe
  would otherwise start on a value its own form will not draw.
- The model keeps its own `NOT_OFFERED_EXTRACT_TYPES` table so it can validate
  without reading YAML, and a test keeps the two equal — the same arrangement
  `SELECTION_ARG_KIND` already has with `choice_args`.
- A rule read off disk carrying one of the nine is still **shown** in the
  combo. Not offering a value is not the same as pretending it is not there;
  snapping to the first entry would rewrite the user's file on open.
- An imported deck degrades to the catalog default with a report line naming
  the statement, the member, the reason and what it became — never silently.

`choice_args` is what makes three of `-selection`'s four members usable.
Before it, the template emitted `-selection "[[extract_selection]]"` as a bare
quoted token, so choosing `net`, `nets_file` or `selected_path_file` produced
a command line the tool rejects — three quarters of the option unusable, with
nothing on screen to say why.

### What the change cost outside the model

Three things the plan did not predict, all found by tests:

- **`selection_line` is one property, not two placeholders.** The first
  version wrote the operand with an inline `[% if %]`. The import solver keys
  a site by `(statement, option)` and a statement name glued to a block tag is
  a statement it cannot see, so `extraction_setup` vanished from the template
  side and every option in it came back as a manual edit.
  `readback.strip_block_tags` fixes the general case; the one-placeholder line
  keeps this one simple.
- **Stripping block tags exposed two wrong line hints.** `add_bulk_terminal`
  and `sub_node_char` recorded the *rendered* line, seven higher than the
  template line, because the `output_xy` loop expands one line into eight.
  Nothing had caught it: that loop's `[% endfor %]` was glued to the next
  statement, so the parser could not see it and the check skipped everything
  below.
- **The importer needs to read rules back.** A hand-written file carrying the
  downgrade pattern would otherwise import as an unmappable patch — the values
  surviving in the diff and invisible in the form, which is the exact shape
  the catalog exists to end. `extract_rules_from_text` reads every `extract`
  statement **in file order**, because order is the semantics.

### The sub-form

One row per rule: index, selection, the operand field *when that member takes
one*, type, move up, move down, remove. Order is editable and visible — a
rule list whose order the user cannot see or change is a list whose meaning
they cannot predict. The last row cannot be removed: a recipe with no extract
statement runs Quantus and extracts nothing, which reports as a successful
extraction of a cell that happens to have no parasitics.

It goes through `OptionGrid.add_span` rather than beside the grid, so density,
section membership and the focus machinery treat it like any other row. A
widget bolted on outside the grid would be the one thing on the form the mode
toggle could not hide.

**One sub-form per collection, not per row.** Two catalog rows describe
`recipe.extraction.extract`; drawing two editors would ask the user which is
the real one — the same "a row is drawn once" rule the rest of the form keeps,
one level up.

### Migration

A v1 recipe carrying the two scalars is upgraded to a one-rule list **on
load**, in place, silently — the user ruled that on 2026-08-25 over an
explicit step in the deploy. Silent means "does not stop to ask", not "leaves
no trace": `load_recipe_with_raw` writes one line to the log naming the file,
and the comment-carrying tree is updated too, or a later save would write the
v1 keys straight back and the upgrade would un-happen on every load. A file
carrying *both* spellings is left alone and the list wins; guessing which one
the author meant is the silent-wrong-answer this project exists to remove.
