"""Task orchestration + stage selection.

Drives the Stage x Task matrix from the Run panel / CLI: for each selected
task, render templates, prepare workdir, execute stages in order (si ->
strmout -> calibre -> qrc -> jivaro) honouring per-task ``continue_on_lvs_fail``.
Emits progress events consumable by both the Qt GUI and the Rich CLI logger.

Implementation lands in Phase 2/3.
"""

from __future__ import annotations
