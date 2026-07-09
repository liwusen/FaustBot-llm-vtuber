# Repository Guidelines

## Project Overview

FaustBot is an AI-driven Vtuber/desktop pet platform with a **Python FastAPI backend** (port 13900) and **Electron frontend**. The backend runs a **LangGraph-based agent** with tool calling, multi-modal memory (vector + knowledge graph + BM25), full-duplex speech (VAD → ASR → Agent → TTS), trigger scheduling, and a plugin system. The frontend renders Live2D/VRM avatars with real-time lip sync and gesture animation.

**Two agents** run concurrently:

- **Faust** (main): conversational agent with 18+ tools — file I/O, code execution, web search, Minecraft, browser automation, GUI control
- **Araya** (background): idle-triggered memory maintenance agent — autonomously mines the knowledge graph when the user is away

## Architecture & Data Flow

```
Frontend (Electron)               Backend (FastAPI :13900)
  ├─ app.js ──WS /faust/chat──→   routes/chat.py
  │                               → lifecycle.stream_chat_agent_events()
  │                               → LangGraph agent.astream_events()
  │                               → yields delta/tool_start/tool_result JSON
  ├─ app.js ──WS /faust/audio/ws/vad──→  VAD + ASR pipeline
  ├─ electron-main.js ──WS /faust/command──→  frontend/bridge.py (commands, TTS, triggers)
  └─ config-window.js ──REST /faust/admin/*──→  admin_runtime.py
```

**Chat flow**: User message → `stream_chat_agent_events()` → acquires `agent_lock` (asyncio.Lock, serialized) → LangGraph agent invokes LLM with tools → `astream_events` yields typed events → WebSocket sends JSON frames → renderer updates UI.

**Tool output flow**: Tool executes → `middleware.wrap_tool_output()` intercepts → `OutputStore.put()` stores full output → LLM receives truncated summary (500 chars) with `[完整输出: artifact://<id>]` footer → LLM can call `read("artifact://<id>")` for full content.

## Key Directories

| Directory                         | Purpose                                                                                                                                               |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/main.py`                 | FastAPI entry point, lifespan, router registration                                                                                                    |
| `backend/faust_backend/runtime/`  | Global state (`state.py`), lifecycle (`lifecycle.py`), tool middleware (`middleware.py`), output artifacts (`output_store.py`), URI parser (`uri.py`) |
| `backend/faust_backend/tools/`    | Agent tools: core 6 (`read/write/edit/execute/search/find`) + specialized (`memory/diary/nimble/trigger/minecraft/skill/hil/animation/media`)         |
| `backend/faust_backend/memory/`   | `GraphStore` — unified memory: NetworkX graph + NanoVectorDB + BM25; `api.py` for REST, `tools.py` for agent-facing wrappers                          |
| `backend/faust_backend/routes/`   | FastAPI routers: `chat.py` (WS+POST), admin modules (config/plugins/agents/triggers/skills/services)                                                  |
| `backend/faust_backend/events/`   | `EventBus` — asyncio.Events + Future/Event pools for HIL, backend2frontend signaling                                                                  |
| `backend/faust_backend/frontend/` | `bridge.py` — async queue-based command dispatch to Electron main process                                                                             |
| `backend/faust_backend/speech/`   | TTS synthesis + ASR transcription, multi-engine (local/Edge/OpenAI/cloud)                                                                             |
| `backend/agents_template/`        | Template files for `faust/`, `araya/`, `ishmael/` agents (AGENT.md, ROLE.md, COREMEMORY.md, TASK.md)                                                  |
| `backend/tests/`                  | pytest suites: `test_harness.py` (core tools + runtime), `test_speech_runtime_edge.py` (TTS adapter)                                                  |
| `frontend/`                       | Electron app: `electron-main.js` (main process), `app.js` (renderer, 3098 lines IIFE), `config-window.js` (config center), `styles.css`, `index.html` |
| `frontend/libs/configer/`         | Config window constants (80+ config key labels, dropdown values)                                                                                      |

## Development Commands

```bash
# Backend (use bundled .runtime Python)
.runtime/python.exe backend/main.py                 # Start server (port 13900)
.runtime/python.exe -m pytest backend/tests/test_harness.py -v  # Run core tests
.runtime/python.exe -m pytest backend/tests/ -v     # Run all tests

# Install dependencies into .runtime/
.runtime/python.exe -m pip install -r requirements.txt
.runtime/python.exe -m pip install -r requirements_local_infer.txt
# Frontend
cd frontend && npm start                              # Dev (electron .)
cd frontend && npm run package                        # Build (electron-builder --win dir)

# Config
cp backend/faust.config.example.json ~/.faustbot/faust.config.json
# Edit ~/.faustbot/faust.config.private.json for API keys
```

**CLI flags** for `main.py`: `--agent <name>` (default `faust`), `--no-run-other-backend-services`, `--save-in-memory`, `--no-startup-chat`.

## Code Conventions & Common Patterns

### Tool Registration

```python
# tools/_registry.py
@register          # 1. appends to toollist[], records in ORIGINAL_TOOL_FUNCS
@tool              # 2. wraps as LangChain BaseTool with schema from docstring
def readTool(path: str) -> str:
    """Read files…"""
    ...

# tools/__init__.py imports all tool modules to trigger @register side effects
```

- **Order matters**: `@register` must come before `@tool`
- **Agent filtering**: `get_tools_for_agent(name)` returns filtered lists (Araya gets memory-only tools, Faust gets all minus exclusions)
- **Plugin composition**: `plugin_manager.compose_tools()` and `compose_middlewares()` inject additional tools at agent creation time

### URI Scheme

All path-accepting tools use a unified multi-scheme URI (parsed by `runtime/uri.py`):

- `src/main.py` or `src/main.py:50-100` → filesystem
- `artifact://shell_3` → OutputStore lookup
- `memory://notes/math` → GraphStore (knowledge base)

### Middleware Pattern

`runtime/middleware.py::wrap_tool_output()` patches both `_arun` and `_run`:

- Short output (≤120 chars, single line) → returns as-is
- Multimodal JSON (`kind: "multimodal_tool_result"`) → stores full content, augments text with artifact reference
- Long text → stores in `OutputStore`, returns truncated summary (500 chars / 5 lines) + artifact:// footer
- Exceptions → captured and stored as error artifacts

### State Management

- **Module-level globals** in `runtime/state.py`: `agent` instance, `agent_lock` (asyncio.Lock), `THREAD_ID`, `abort_event`, SQLite `checkpointer`, `RUNTIME_STATUS` flag
- **Config as module globals** in `config_loader.py`: 80+ values loaded from `faust.config.json` + `faust.config.private.json`
- **Singleton factories**: `get_memory()`, `get_output_store()`, `get_bus()` — lazy-init, per-agent

### Async Patterns

- `asyncio.Lock` serializes agent invocations
- `asyncio.Event` for signaling (abort, backend2frontend queue, HIL feedback)
- `asyncio.Future` for request-response (HIL approval)
- `asyncio.Queue` for frontend command dispatch
- `asyncio.to_thread()` for sync HTTP calls (requests library)
- `ThreadPoolExecutor` for parallel BM25 operations
- Tools can be sync or async; `wrap_tool_output` patches both

### Error Handling

- **429 rate limit**: 3-attempt exponential backoff (0.5s → 1.5s → 3.0s)
- **Tool exceptions**: caught by middleware, returned to LLM as error artifact — LLM decides retry / alternative / explain
- **Startup degradation**: if agent creation fails → `RUNTIME_STATUS = "waiting_for_config"` → WebSocket returns error payload with reason
- **Agent interrupt**: `{type: "interrupt"}` WS message → `abort_event.set()` → `asyncio.CancelledError` → cleanup → `{type: "interrupted"}`

### Memory System

`GraphStore` (`memory/store.py`, ~1600 lines) — monolithic memory engine:

- **File tree**: `networkx.MultiDiGraph` with `path:/...` nodes and `has_child` edges
- **Vector**: `nano-vectordb` (EMBED_DIM=1536, text-embedding-3-small)
- **BM25**: `rank_bm25.BM25Okapi` tokenized with `\w+` regex
- **Knowledge graph**: entity nodes (person/place/event/concept/object/document) connected via `kb_refs` edges
- **Hybrid search**: vector (cosine) + BM25 (alpha=0.5) + graph-aware (entity → kb_refs) + 2-hop expansion + optional reranker
- **Entity extraction**: background LLM call on file write → `extraction_prompt.md` → JSON response → cosine dedup
- Content stored in `~/.faustbot/agents/<name>/memory/`; graph persisted as JSON

### Deprecated Legacy Tools

12 old tools kept as plain `@tool` functions **without** `@register` (LLM never sees them). They delegate to core tools:

- `readTextFileTool` → `read()`, `writeTextFileTool` → `write()`, `sysExecTool`/`pythonExecTool` → `execute()`
- `kbReadTool`/`kbWriteTool`/`kbListTool` → `read("memory://...")` / `write("memory://...")`

### Frontend ↔ Backend Communication

- **Renderer → Backend**: WebSocket + REST (proxied through Electron main process via `contextBridge`)
- **Backend → Frontend commands**: `FrontendBridge` asyncio.Queue → WS `/faust/command` → Electron main → IPC → renderer
- **Window types**: Main (transparent frameless alwaysOnTop), Config (1240×860), Live (900×700)
- **Click-through**: `body.click-through` class sets `pointer-events: none` on canvas, `pointer-events: auto` on HUD controls

## Important Files

| File                                            | Role                                                                      |
| ----------------------------------------------- | ------------------------------------------------------------------------- |
| `backend/main.py`                               | App entry, router registration, CORS                                      |
| `backend/faust_backend/runtime/lifecycle.py`    | Lifespan startup order, `rebuild_runtime()`, `stream_chat_agent_events()` |
| `backend/faust_backend/runtime/state.py`        | Agent singleton, lock, prompt assembly, abort event                       |
| `backend/faust_backend/config_loader.py`        | Config loading, `_ensure_faustbot_init()`, CLI args                       |
| `backend/faust_backend/tools/_registry.py`      | `@register`, `toollist`, `get_tools_for_agent()`                          |
| `backend/faust_backend/runtime/middleware.py`   | `wrap_tool_output()`, `wrap_tools()`                                      |
| `backend/faust_backend/runtime/output_store.py` | `Artifact`, `OutputStore`, `artifact://` protocol                         |
| `backend/faust_backend/runtime/uri.py`          | `ParsedURI`, multi-scheme parse, line selectors                           |
| `backend/faust_backend/memory/store.py`         | `GraphStore` — vector + graph + BM25 unified memory                       |
| `backend/faust_backend/routes/chat.py`          | WS chat handler, `chat_websocket()`, interrupt support                    |
| `backend/faust_backend/events/bus.py`           | `EventBus` with HIL futures, feedback events                              |
| `backend/faust_backend/frontend/bridge.py`      | `FrontendBridge` — command queue to Electron                              |
| `backend/faust_backend/araya_runtime.py`        | `ArayaRuntime` — idle-triggered background agent                          |
| `backend/faust_backend/admin_runtime.py`        | Config CRUD, agent file sync from templates                               |
| `backend/faust_backend/speech/config.py`        | TTS/ASR mode detection, URL builders                                      |
| `backend/faust.config.example.json`             | All ~60 config keys with defaults                                         |
| `frontend/electron-main.js`                     | Main process: windows, tray, shortcuts, deep-link, IPC                    |
| `frontend/app.js`                               | Renderer: Live2D/VRM, chat WS, VAD, nimble windows, HIL                   |
| `frontend/config-window.js`                     | Config center with 12 module sections                                     |
| `frontend/preload.js`                           | `contextBridge` — secure IPC to main process                              |
| `frontend/libs/configer/constants.js`           | 80+ config key labels, field groups, dropdown options                     |
| `backend/tests/test_harness.py`                 | 742-line pytest: 13 test classes, 60+ tests                               |
| `agents_template/faust/AGENT.md`                | Faust agent personality + tool reference                                  |
| `agents_template/araya/AGENT.md`                | Araya background agent personality                                        |

## Runtime/Tooling Preferences

- **Python**: 3.11+ required (numpy>=2 constraint). **Prefer `.runtime/python.exe`** — the project bundles an embedded Python 3.11 runtime at `.runtime/` containing Python interpreter, pip, and all required site-packages (torch, torchaudio, langchain, etc.). Use `.runtime/python.exe` or `.runtime/Scripts/pip.exe` to ensure dependency consistency.
- **Package manager**: pip (`requirements.txt`, `requirements_local_infer.txt`). Install into `.runtime/` with `.runtime/python.exe -m pip install -r requirements.txt`
- **Frontend runtime**: Node.js + Electron (no Bun, not a web app)
- **Config**: flat JSON, two-tier (public `faust.config.json` + private `faust.config.private.json`)
- **Frontend build**: `esbuild` for bundling, `electron-builder` for packaging (ASAR, Windows x64 dir target)
- **No frontend tests** exist (only backend pytest suites)
- **No linter/formatter** configured in project
- **No CI/CD** pipeline configured
- **Backend auto-launch**: Electron main process checks port 13900; if closed, spawns `powershell.exe` running `backend/MAIN.bat`

## Testing & QA

- **Framework**: pytest with async support
- **Test files**: `backend/tests/test_harness.py`, `backend/tests/test_speech_runtime_edge.py`
- **Pattern**: class-per-subsystem (`TestURIParse`, `TestOutputStore`, `TestReadTool`, etc.)
- **Fixtures**: `tmp_path` (pytest) for filesystem isolation, `monkeypatch` for config/module mocking, `mock.AsyncMock` for async dependencies
- **Run**: `python -m pytest backend/tests/test_harness.py -v`
- **60+ tests** covering URI parsing, artifact storage, all 6 core tools, multimodal images, force_plain_text, middleware pass-through
- `PROJECT_ROOT` is monkeypatched before each test and restored after — never pollute real project state

## Reference

- If you are a LLM, you MUST read [instruction](document/ai_prompt.md)
