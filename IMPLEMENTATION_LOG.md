# MODE ROUTER + MESSAGE ARCHIVE — Implementation Log

Date: 2026-08-04
Branch: multi-agent
Status: ALMOST (Telegram token needed for E2E)

---

## Architecture

```
┌─────────────────────────────────────────────┐
│  gateway/run.py  ── _handle_message ────────┤
│    mode intercept → detect_mode_intent()    │
│    switch handler → apply_mode_change()     │
│    _run_agent → filter_tools(mode)          │
│                 filter_individual_tools()    │
│                 AIAgent(mode=...)            │
├─────────────────────────────────────────────┤
│  agent/system_prompt.py                     │
│    MODE_GUIDANCE injected after PROJECT     │
├─────────────────────────────────────────────┤
│  modes/                                     │
│    state.py   — ~/.hermes/state/modes.json  │
│    detect.py  — NL + /mode → ModeIntent     │
│    policy.py  — MODE_TOOLSETS, blocked tools│
│    router.py  — apply_mode_change, status   │
│    prompts.py — DEV/SECRETARY guidance      │
├─────────────────────────────────────────────┤
│  plugins/message_archive/                   │
│    __init__.py  — is_enabled, helpers       │
│    db.py        — MessageArchiveDB (WAL)    │
│    extractors.py — photo/audio/document     │
├─────────────────────────────────────────────┤
│  tools/                                     │
│    archive_admin.py    — 6 actions          │
│    archive_query_tool.py — FTS search       │
├─────────────────────────────────────────────┤
│  hooks/message-archiver/                    │
│    handler.py — agent:start subscriber      │
└─────────────────────────────────────────────┘
```

## Files Created/Modified: 20

### New files (12)
| File | Purpose | Size |
|------|---------|------|
| modes/__init__.py | Public API: get_mode, set_mode, detect_mode_intent, filter_tools... | 1.3 KB |
| modes/state.py | Persistent per-chat mode storage (~/.hermes/state/modes.json) | 4.8 KB |
| modes/detect.py | NL + slash pattern matching → ModeIntent | 6.7 KB |
| modes/policy.py | MODE_TOOLSETS, blocked tools per mode, filter_individual_tools | 4.7 KB |
| modes/router.py | apply_mode_change, format_status_reply, get_effective_mode | 5.1 KB |
| modes/prompts.py | DEV_GUIDANCE, SECRETARY_GUIDANCE, user-facing reply templates | 8.8 KB |
| plugins/message_archive/db.py | SQLite WAL, writer thread, FTS5, ArchiveRecord | 11.2 KB |
| plugins/message_archive/extractors.py | extract_photo, extract_audio, extract_document | 4.7 KB |
| tools/archive_query_tool.py | FTS search archived messages | 7.1 KB |
| tests/modes/test_detect.py | 33 tests: slash, NL, false positives | 4.8 KB |
| tests/modes/test_state.py | 19 tests: set/get, isolation, idempotency | 3.8 KB |
| tests/modes/test_policy.py | 22 tests: filter dev vs secretary | 4.0 KB |
| tests/modes/test_router_integration.py | 15 tests: full mode switch flow | 5.6 KB |
| tests/modes/test_e2e_gateway.py | 10 tests: 7 acceptance criteria | 5.4 KB |

### Modified files (8)
| File | Changes |
|------|---------|
| gateway/run.py | +mode intercept before command dispatch, +tool filtering in _run_agent, +mode param to AIAgent, +full_message/media_urls/media_types/message_id in agent:start hook |
| agent/system_prompt.py | +MODE_GUIDANCE injection after PROJECT_GUIDANCE |
| agent/agent_init.py | +mode parameter |
| toolsets.py | +"archive" toolset with archive_query |
| plugins/message_archive/__init__.py | rewritten: uses db.get_db(), exports helpers |
| hooks/message-archiver/handler.py | rewritten: ArchiveRecord + db.enqueue() + media extraction |
| tools/archive_admin.py | +set_max_file_mb action, +project_id stats in status |
| projects/path_guard.py | +whitelist for ~/.hermes/archive/** and ~/.hermes/state/** |

### Config changes (~/.hermes/config.yaml)
| Key | Value |
|-----|-------|
| model.default | deepseek-v4-pro |
| model.provider | deepseek |
| modes.default | dev |
| modes.enabled | [dev, secretary] |
| message_archive.enabled | true |
| message_archive.db_path | ~/.hermes/archive/messages.db |
| message_archive.files_dir | ~/.hermes/archive/files |
| message_archive.max_file_mb | 40 |
| message_archive.chats | [] (all chats) |
| gateway.platforms | [telegram] |
| telegram.polling | true |

### Documentation
| File | Content |
|------|---------|
| QUICKSTART-MODES.md | Fork root: table dev vs secretary, Telegram phrases, troubleshooting |
| docs/QUICKSTART-MODES.md | Same, docs version |
| docs/modes-vs-archiving.md | Архивация работает ВСЕГДА (фон), mode влияет только на ответы |
| optional-skills/productivity/secretary-reports/SKILL.md | 5 scenarios: daily report, keyword search, document summary, cron setup, status |

## Test Results

```
tests/modes/ — 99 passed in 0.56s

test_detect.py           33 passed  (RU/EN/slash/false-positive)
test_state.py            19 passed  (set/get/isolation/idempotency)
test_policy.py           22 passed  (dev blocks archive_query, sec blocks terminal)
test_router_integration.py 15 passed  (full flow: NL → switch → confirmation)
test_e2e_gateway.py      10 passed  (7 acceptance criteria)
```

## Two Modes

| | dev | secretary |
|---|---|---|
| terminal | yes | no (config gate) |
| file tools | yes | no |
| delegation | yes | no |
| archive_query | no | yes |
| archive_admin | no | yes |
| cronjob | yes | yes |
| Архивация (фон) | always | always |

## Remaining Blockers

1. TELEGRAM_BOT_TOKEN — закомментирован в .env, без значения
2. gateway — запускается, но "No messaging platforms enabled" (из-за отсутствия токена)

## Quick Start

```bash
# Switch modes
режим секретаря
режим разработки
/mode dev
/mode status

# Archive management (secretary only)
archive_admin list_chats
archive_admin add_chat -1001234567890
archive_admin set_enabled true
archive_admin status

# Search
archive_query keyword="bug" since="2026-08-01T00:00:00Z" until="2026-08-05T00:00:00Z"
archive_query msg_type="document_pdf,document_docx"

# Tests
cd ~/.hermes/hermes-agent && ./venv/bin/python -m pytest tests/modes/ -q
python3 -c "from modes import get_default_mode; print(get_default_mode())"
```
