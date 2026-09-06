# dengjen-nvda Architecture & Guardrails
- **Environment**: NVDA screen-reader add-on in Python 3.13 (Windows-only runtime: gRPC server, vendored native wheels)[cite: 3].
- **Core Subsystems & Data Flow**:
  - `addon/synthDrivers/dengjen_neural_voices/`: Synthesizer driver loaded by NVDA, structured as ports-and-adapters (`domain/`, `ports/`, `adapters/`)[cite: 3].
    - `adapters/nvda/synth_driver.py`'s `SynthDriver.speak()` transforms speech sequences into `SpeechTask`/`BreakTask`/`IndexReachedTask`/`DoneSpeakingTask` objects; the package `__init__.py` re-exports `SynthDriver` for NVDA's driver discovery[cite: 3].
    - Tasks execute via `process_speech()` on the shared asyncio loop, feeding raw PCM into sample-rate-keyed `WavePlayer` instances[cite: 3].
    - `domain/tts_system.py`'s `DengjenTextToSpeechSystem` manages voice/pitch/rate state and pulls chunks through `ports/tts_backend.py`'s `TTSBackend` protocol[cite: 3].
  - `addon/globalPlugins/dengjen_tts_global_plugin/`: Tray-menu voice manager GUI (imports directly from the synth driver package)[cite: 3].
  - `aio.py`: `AsyncEngine` singleton managing dedicated background thread, event loop, and thread pool for async gRPC calls[cite: 3].
  - `adapters/dengjen_grpc/`: the `TTSBackend` adapter; spawns detached `dengjen-tts-grpc.exe` subprocess on a free port, explicitly validates `vcruntime140_1.dll` presence prior to spawn[cite: 3].
  - `_config.py` & `voice_migration.py`: `DengjenConfig` persists per-voice configuration; migration handles legacy Sonata settings on first run[cite: 3].
- **Vendored Dependencies**: Do NOT edit `lib/` directly; refresh native wheels via `update_grpc.py`, `update_miniaudio.py`, `update_cffi.py`[cite: 3].
- **Build & Versioning**: SCons-driven (`scons`)[cite: 3]. Prefer configuring via `buildVars.py` before modifying `sconstruct`[cite: 3]. `addon_version` MUST be strict 3-part semver[cite: 3].
- **Release Workflow**: Tag-driven from `main` via `git tag -a vMAJOR.MINOR.PATCH(-beta.N) -m "..."`[cite: 3]. CI builds on Ubuntu, executes Windows tests, and drafts release artifacts[cite: 3].

# Git & Contribution Conventions (Overrides Global Jira Rules)
- **Branches**: `fix/<slug>`, `feat/<slug>`, `chore/<slug>`[cite: 3].
- **Commits**: Short imperative subject line, blank line, body explaining *why*, and relevant issue references[cite: 3].
- **PR Titles**: Conventional Commits style (`fix:`, `feat:`, `chore:`, `docs:`)[cite: 3].
- **Trailers**: Never include `Co-Authored-By` trailers or AI attribution[cite: 1, 3].

# Testing Rules & Mocking Guardrails
- **CRITICAL**: Never stub a base class with `MagicMock()` (subclassing a mock makes class bodies no-ops)[cite: 3]. Stub NVDA base classes as plain empty classes[cite: 3].
- **Test Matrix**:
  - `pytest` (`tests/`): Unit/stub suite. Runs on Linux and Windows[cite: 3].
  - `pytest tests_contract/`: Real gRPC server integration (Windows only)[cite: 3].
  - `pytest tests_gui/`: Real wxPython GUI suite (Windows only)[cite: 3].
  - `pytest tests_e2e/`: Full NVDA automation via `nvda-addon-testkit` (Windows CI only)[cite: 3].
- **GUI Testing Gotchas (`tests_gui/`)**:
  - Never call `ShowModal()` (causes CI deadlock timeouts); construct -> assert -> `Destroy()`[cite: 3].
  - wx swallows event handler exceptions; assert observable side-effects instead of `pytest.raises`[cite: 3].
  - `gui.messageBox` returns `wx.YES`/`wx.NO`/`wx.OK`/`wx.CANCEL` (not `wx.ID_*`)[cite: 3].
  - Key events require explicit `SetId(window.GetId())` or wx drops the event[cite: 3].
  - Walk tab order with `Navigate()`, not `wx.UIActionSimulator`[cite: 3].

# Common Commands
- Build Addon: `scons`[cite: 3]
- Update Translation Template: `scons pot`[cite: 3]
- Run Local Unit Tests: `pytest`[cite: 3]
- Single Test: `pytest tests/test_file.py::TestClass::test_func`[cite: 3]
