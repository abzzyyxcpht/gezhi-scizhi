# SciZhi UI Alignment Implementation Plan

> Execute with subagent-driven-development in this session; user approved the shared Flet + TUI design.

**Goal:** Apply shared preferences and ZCode-inspired display behavior to both working interfaces.
**Architecture:** JSON defines preference validation and design metrics. Python owns persistence and migration; Flet and OpenTUI consume the same contract with renderer-specific geometry.
**Tech Stack:** Python 3.13, Flet 0.86, Node 24, OpenTUI/React.

## Constraints
- Preserve research, permissions, session and approval semantics.
- Do not change real data or regenerate presentations.
- UI font size changes text only; code and native terminal sizes are independent.
- TUI font family and physical size belong to the host terminal; label shared desktop font preferences honestly.
- Apply and verify every exposed control; keep legacy settings readable.

## Tasks
- [x] Shared contract: assets/ui-design.json, src/ui_preferences.py, src/settings.py; test isolated save/load, invalid values, legacy migration and unrelated settings preservation.
- [x] Desktop tokens and type: src/ui/theme.py, src/ui/brand.py; test reversible scaling, live colors, dynamic Markdown, code and terminal independence.
- [x] Settings center: src/ui/settings_center.py; implement grouped editable appearance controls with preview and save feedback, test real bridge persistence.
- [x] Desktop shell and output: src/ui/desktop.py, src/ui/project_studio.py, src/ui/terminal_panel.py, src/ui/code_preview.py; consume system theme, sidebar/inspector, syntax/line numbers/wrap, runtime preference reload.
- [x] TUI: shared JSON adapter, config and runtime, tokens, overlay sidebars and keyboard close. Test headless frames and persisted toggles.
- [x] Integrate: run focused tests, full CI in isolated data directory, capture native light/dark and narrow frames, independent review and fixes.

## Verification commands
`python -m pytest tests -q --basetemp <unique-temporary-directory>`
`npm --prefix tui test`
`python -m ruff check src tests scripts`
`python scripts/capture_desktop.py --sizes 1440x900,1100x760 --screens home,agent,settings-appearance,project-files --out <temporary-preview-directory>`

## Tracking
Recover local Git history from the existing remote without overwriting working files. Keep all previous local edits; review tracked deletions from the earlier cleanup separately. Do not publish until integration checks pass.

## Final evidence
Python: 1732 passed, 2 symlink-permission skips. TUI: 79 commands passed; late-read ordering fix verified with the appearance-sync and appearance-ui renderer tests afterward. Ruff and UI lint pass. Native captures cover both themes, two window sizes and the 20px text setting. Independent review findings fixed and re-reviewed. Existing executable packages were not rebuilt.
