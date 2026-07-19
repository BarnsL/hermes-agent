# 50 Whys: Hermes Session Create/Resume Slowness
**Date:** 2026-07-11 | **Target:** Snappy like Claude Code

---

## Surface Symptom
Session create and resume feel slow. Not responsive.

## Why #1
**Why does session create feel slow?**
Because there's a long delay between clicking "New Session" and seeing a response.
→ Dual RPC pattern: `session.create` (~100-500ms) + `prompt.submit` (~500ms-2s). Two WebSocket round-trips before anything happens.

## Why #2
**Why are there two RPCs instead of one?**
Because the architecture separates session creation from prompt submission. The desktop creates an empty session, navigates to it, then sends the prompt separately.
→ Claude Code collapses this into one call because the agent IS the process.

## Why #3
**Why can't Hermes collapse create+submit into one RPC?**
Because the desktop needs a `session_id` to route streaming events to the correct tab before any text can appear. The `session.create` RPC returns this ID.
→ The frontend needs the ID before it can subscribe to the response stream.

## Why #4 (GIL)
**Why is the gateway slow to respond even to simple RPCs?**
Because the Python asyncio event loop is stalled by the GIL (Global Interpreter Lock).
→ Log evidence: "event loop stalled 35.2s (GIL pressure suspected)", "ws write slow (loop stalled >10.0s)"

## Why #5
**Why is the GIL stalling the event loop?**
Because the agent turn runs CPU-heavy Python code (prompt building, message serialization, MCP/server calls) in the SAME process as the gateway's asyncio loop.
→ Single Python process can only run one thread at a time due to GIL.

## Why #6
**Why does the agent turn run in the same process as the gateway?**
Because Hermes uses a monolithic gateway process that handles both I/O serving (WebSocket, HTTP) AND agent execution. No process isolation.
→ The server architecture runs everything in one Python process.

## Why #7
**Why is it a single process?**
Because the gateway was originally a simple CLI tool that grew into a server. Process-per-agent isolation was never added.
→ Architectural debt from CLI origins.

## Why #8 (Backend spawn)
**Why is session.create slow on the first session?**
Because a cold profile requires spawning a new Python subprocess, loading MCP servers, building system prompts, and importing skills.
→ This can take 2-5 seconds on first session.

## Why #9
**Why does a cold profile need to spawn a new process?**
Because Hermes supports multi-profile isolation — each profile has its own agent process pool with separate config, MCPs, and tools.
→ Multi-profile support adds startup overhead.

## Why #10
**Why aren't pools pre-warmed?**
Because the desktop starts the gateway lazily — it only spawns agent processes when a session is actually created in that profile.
→ No eager pool pre-warming on boot.

## Why #11 (RAF gating)
**Why does the first response chunk feel delayed even after streaming starts?**
Because `syncSessionStateToView()` batches `$messages` updates via `requestAnimationFrame`, adding up to 16ms of artificial latency before the first visible token.
→ RAF coalescing is a performance optimization for during-streaming, but adds latency to the first frame.

## Why #12
**Why is RAF coalescing needed?**
Because during active streaming, `message.delta` events arrive at ~30Hz and without batching, each would trigger a full React re-render — tanking performance.
→ Without RAF batching, streaming would be janky.

## Why #13 (Route change)
**Why does the route change on session create add latency?**
Because `createBackendSessionForSend` calls `navigate(sessionRoute(stored))` which triggers a React Router transition, component unmount/remount, and re-render cycle while streaming content is arriving.
→ Route change is unnecessary — the session is already active and streaming.

## Why #14
**Why does the route need to change?**
Because the sidebar and URL bar need to reflect the current session. The `/` route is the "new session" draft; sessions need a `/:sessionId` route.
→ URL routing is tied to session identity.

## Why #15 (localStorage writes)
**Why do atom writes feel slower than they should?**
Because `$currentModel`, `$currentProvider`, `$currentReasoningEffort`, `$currentFastMode` persist to localStorage synchronously on every change — blocking the main thread for 1-5ms per write.
→ Synchronous localStorage writes on the hot path.

## Why #16
**Why do we persist to localStorage synchronously?**
Because nanostores' `persistentAtom` uses synchronous `localStorage.setItem()` to ensure state survives page reloads.
→ The persistence layer uses blocking writes.

## Why #17 (WebSocket vs HTTP)
**Why are RPC calls slower than direct HTTP?**
Because WebSocket JSON-RPC adds framing overhead: serialize JSON → send over WS → backend deserializes → dispatch → serialize response → send → desktop deserializes.
→ Each hop involves serialization/deserialization overhead.

## Why #18
**Why use WebSocket instead of HTTP for RPC?**
Because WebSocket supports bidirectional streaming — the desktop needs to receive unsolicited `message.delta` events. HTTP would require polling or SSE.
→ Streaming requires persistent connections.

## Why #19 (prompt building)
**Why does session.create take so long on the backend?**
Because `hermes_cli/web_server.py::session_create()` calls `_make_agent()` which builds the full system prompt (~9000 lines), loads MCP servers, imports skills and plugins, and resolves project context.
→ Full agent initialization on every session create.

## Why #20
**Why rebuild the full agent on every session create?**
Because each session needs its own agent instance with the correct profile, model, and tools. Agent state is not shared between sessions.
→ Sessions are isolated, requiring fresh agent construction.

## Why #21 (Message serialization)
**Why does the RESUME path feel slow?**
Because `session.resume` RPC needs to read all messages from SQLite, deserialize them, and send them back over WebSocket as JSON.
→ For long sessions (100+ messages), this can be 50-200ms just for serialization.

## Why #22
**Why are messages stored in SQLite?**
Because SQLite is the only embedded database that works cross-platform without dependencies. It's the default persistence layer.
→ SQLite is the universal embedded DB choice.

## Why #23
**Why not use an in-memory cache for hot sessions?**
Because the agent process may be restarted (gateway crash, profile switch, config change), and the only durable state is SQLite.
→ No RAM cache across process lifetimes.

## Why #24 (Cold resume path)
**Why is cold resume (no warm cache) slower than warm resume?**
Because cold resume must: `resolveStoredSession` → `ensureGatewayProfile` → `requestGateway('session.resume')` → wait for response → `updateSessionState` → `syncSessionStateToView` → RAF → paint.
→ Cold resume has 3+ async hops before paint.

## Why #25
**Why does cold resume need `resolveStoredSession`?**
Because the sidebar stores session metadata (title, timestamp, message count) but not the actual messages. The gateway session.resume RPC must fetch them.
→ Session metadata is cached; messages are not.

## Why #26
**Why aren't messages cached on the desktop?**
Because the warm cache (`sessionStateByRuntimeIdRef`) stores messages per runtime session, and runtime sessions are ephemeral (created/destroyed by the gateway). When the gateway restarts, all warm caches are invalidated.
→ Runtime IDs are gateway-lifetime-scoped, not persistent.

## Why #27
**Why are runtime IDs scoped to gateway lifetime?**
Because the gateway generates runtime IDs on `session.resume` that are only valid for the current gateway process. This is a safety mechanism to prevent stale references after restarts.
→ Runtime ID scoping prevents accessing dead sessions.

## Why #28 (GIL + SQLite)
**Why is the GIL causing SQLite to be slow?**
Because Python's `sqlite3` module holds the GIL during database operations. When the agent turn is running, database queries queue behind the agent's CPU work.
→ SQLite operations are blocked by the agent's GIL hold.

## Why #29
**Why doesn't SQLite run in a separate thread?**
Because Python's GIL means even a separate thread would be blocked by the agent turn's CPU work. Threads don't help with CPU-bound GIL contention.
→ Threading doesn't bypass the GIL.

## Why #30
**Why not use multiprocessing to isolate from the GIL?**
Because the gateway wasn't designed for multiprocessing. `session.create` already spawns subprocesses for agent turns, but the gateway itself (WebSocket server + SQLite) is single-process.
→ Architecture predates multiprocessing needs.

## Why #31 (React rendering)
**Why does React re-rendering add perceived latency?**
Because `$messages` atom updates trigger `ChatRuntimeBoundary` re-render → `toRuntimeMessage()` conversion for ALL messages (not just the new one) → `assistant-ui` ExportedMessageRepository recomputes → Thread virtualizer remeasures.
→ Re-rendering O(n) messages on every update.

## Why #32
**Why convert ALL messages on every update?**
Because `assistant-ui`'s `useExternalMessageConverter` hook receives the full messages array and converts each one. There's no incremental update path.
→ The message converter API is designed for full-array input.

## Why #33 (Virtualizer)
**Why does the virtualizer add latency?**
Because after `$messages` updates, the virtual list must remeasure item heights, recalculate total scroll height, and scroll to bottom. This is a layout pass that blocks paint.
→ Virtualizer recalculations are synchronous layout work.

## Why #34
**Why use a virtual list for the thread?**
Because sessions can have hundreds of messages. Without virtualization, the DOM would have thousands of nodes, making the page unresponsive.
→ Virtualization is required for large sessions.

## Why #35 (Cross-window broadcast)
**Why does `broadcastSessionsChanged()` add latency?**
Because after session create, the desktop sends a `postMessage` to all other Hermes windows to sync the session list. This adds a macrotask to the event queue.
→ Cross-window sync is a postMessage macrotask.

## Why #36
**Why sync across windows?**
Because Hermes supports multiple windows (New Window action). When one window creates a session, all windows need to see it in their sidebar.
→ Multi-window support requires cross-window state sync.

## Why #37 (YOLO apply)
**Why does the YOLO apply step add latency on session create?**
Because if YOLO was armed in the draft, `setSessionYolo()` makes a second `session.set_yolo` WebSocket RPC after `session.create` returns. Serial RPCs.
→ YOLO mode requires a separate state mutation RPC.

## Why #38
**Why does YOLO need a separate RPC?**
Because YOLO mode is a session-level flag that affects tool approval behavior. It must be set before `prompt.submit` runs.
→ Session flags are set via dedicated RPCs.

## Why #39 (Profile swap)
**Why does profile swapping add latency on session create?**
Because `ensureGatewayProfile()` must swap the active gateway connection to the new profile. If the profile has no running backend, it must spawn a pooled backend process via websocket to the gateway.
→ Profile isolation requires separate backend processes.

## Why #40
**Why does each profile need a separate backend?**
Because profiles have different configs, API keys, models, and tools. Mixing them would leak credentials and state between profiles.
→ Security/isolation requirement.

## Why #41 (MCP discovery)
**Why does MCP discovery add latency?**
Because on agent init, the gateway discovers available MCP servers by scanning config and testing connections. Each MCP server may take 100-500ms to connect.
→ MCP server connections are established eagerly.

## Why #42
**Why are MCP servers connected eagerly?**
Because the agent needs to know which tools are available BEFORE building the system prompt. The tool list is embedded in the prompt.
→ Tool availability must be known at prompt-build time.

## Why #43 (Skills loading)
**Why does skill loading add latency?**
Because on agent init, all enabled skills are loaded from disk (YAML + markdown parsing), validated, and their prompts are injected into the system prompt.
→ Skills are filesystem-loaded at agent init.

## Why #44
**Why not cache parsed skills in memory?**
Because skills can change between sessions (user edits a SKILL.md, plugin updates). The agent must always load the latest version.
→ Stale skill cache would cause incorrect behavior.

## Why #45 (Project context)
**Why does project context loading add latency?**
Because when opening a session in a project, Hermes scans AGENTS.md, CLAUDE.md, .cursorrules, and other context files from the project root and injects them into the system prompt.
→ Project context files are read at session open time.

## Why #46
**Why not cache project context?**
Because project files can change between sessions. The user might edit AGENTS.md, git pull new rules, etc.
→ Stale context would give wrong instructions.

## Why #47 (Gateway restart loop)
**Why does the gateway sometimes restart during a session?**
Because the desktop log shows "Hermes backend exited (1)" in a loop. The gateway crashes, Electron restarts it, it crashes again.
→ Gateway instability causes session loss and re-resume overhead.

## Why #48
**Why does the gateway crash and restart?**
Because unhandled exceptions in the agent turn or gateway module cause the process to exit. The `hermes serve` process isn't resilient to agent failures.
→ No process-level error isolation between gateway serving and agent execution.

## Why #49
**Why isn't the gateway resilient to agent failures?**
Because the agent runs in the same process as the gateway. An unhandled exception in the agent's conversation loop can crash the entire gateway.
→ Single-process architecture has no fault isolation.

## Why #50 (The root)
**Why is Hermes slow compared to Claude Code?**
Because Claude Code runs as a single Node.js process where session creation is implicit, streaming starts immediately, and there's no GIL, no cross-process WebSocket overhead, no separate agent spawn, no SQLite persistence on the hot path, and no multi-profile architecture. Hermes optimizes for features (multi-profile, multi-window, skills, MCPs, plugin system, cross-platform SQLite persistence) at the cost of latency. Every architectural decision that adds capability also adds an async hop.

---

## Ranked Fixes (impact × feasibility)

| # | Fix | Impact | Feasibility |
|---|-----|--------|-------------|
| 1 | **Lazy session create while user types** | 🔴 HIGH | 🟢 Easy |
| 2 | **Combine session.create + prompt.submit into one RPC** | 🔴 HIGH | 🟡 Medium |
| 3 | **Eager-flush first message.delta (skip RAF for first update)** | 🟡 HIGH | 🟢 Easy |
| 4 | **Skip route change on create (defer to after streaming starts)** | 🟡 HIGH | 🟡 Medium |
| 5 | **Pre-warm agent pools on desktop boot** | 🟡 MEDIUM | 🟡 Medium |
| 6 | **Debounce localStorage writes (batch every 500ms)** | 🟢 LOW | 🟢 Easy |
| 7 | **Cache system prompt / skip rebuild on same-profile same-session-type** | 🟢 LOW | 🟡 Medium |
| 8 | **Process-per-agent isolation (prevent GIL stalls)** | 🔴 HIGH | 🔴 Hard |

## Quick Wins (can implement today)

1. **Lazy session create**: Start `session.create` in background when "New Session" is clicked, not when Enter is pressed. Hides 100-500ms behind typing time.

2. **Skip RAF for first message.delta**: In `syncSessionStateToView`, if `$messages` is empty (first content), flush synchronously instead of waiting for next RAF. Saves up to 16ms.

3. **Debounce localStorage atom persists**: Batch `setCurrentModel`, `setCurrentProvider`, etc. to write every 500ms instead of every change. Saves ~5ms per atom write.
