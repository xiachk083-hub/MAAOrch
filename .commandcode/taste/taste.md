# Taste

## Communication
- User communicates in Simplified Chinese and expects responses in Chinese (their project's docs, naming, and discussion are all in Chinese). Confidence: 0.9

## Environment & Tooling
- User's machine is Windows and `shell_command` executes with Windows cmd semantics: use native commands (`dir`, `findstr`, `type`, `powershell -NoProfile -Command`) with backslash paths (e.g. `C:\Users\xiach\...`). Unix-style paths (`/c/Users/...`) and `$USERPROFILE` in bash syntax fail. Confidence: 0.85
- Command Code stores per-project session data under `C:\Users\xiach\.commandcode\projects\<project-slug>\<session-uuid>.jsonl` plus a sibling `.meta.json`; searching there with `dir /s /b ... | findstr /i "<id>"` resolves session IDs the user pastes. Confidence: 0.8
- Prefers to bypass AUTO-MAS scheduling and run MAA directly/independently, using MAS only as an account-data source. Confidence: 0.9

## Workflow & Autonomy
- Prefers autonomous end-to-end execution after goal is authorized, rather than stopping for confirmation at each small sub-step; finds incremental one-step-at-a-time progress too fragmented. Confidence: 0.85
