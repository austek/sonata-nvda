"""
Stubs for NVDA's internal modules, so add-on code can be imported and driven
outside a real NVDA process.

Shared by two test trees with different needs:

  tests/      install(stub_wx=True)  -- everything stubbed, runs anywhere
  tests_gui/  install(stub_wx=False) -- real wxPython, NVDA still stubbed

The split exists so tests_gui/ does not fork a copy of this apparatus that
then drifts. Strategy, unchanged from before:

1. Stub the bundled Windows-only grpc Cython extensions, SCons, markdown.
2. Stub all NVDA-internal packages (config, languageHandler, ...).
3. Register `dengjen_neural_voices` in sys.modules WITHOUT running its
   __init__.py (which imports grpc_client at module level).
4. Stub the intra-package submodules with platform deps (aio). The real
   adapters.dengjen_grpc needs no stub of its own: grpc and aio, its risky
   dependencies, are already stubbed here, so it imports for real.
5. Load the real submodules under test (const, helpers, tts_system).
6. With stub_wx=True, register `dengjen_tts_global_plugin` as a hollow
   package too. With stub_wx=False, leave it alone -- tests_gui/ imports it
   for real, which is what supplies both the GlobalPlugin class under test
   and the package-level re-exports voice_manager.py reaches via
   `from . import ...`.
"""

import builtins
import importlib.util
import inspect
import os
import sys
import types
from concurrent.futures import Future as _Future
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _stub_module(name: str, **attrs) -> types.ModuleType:
    """Create a plain module stub and register it in sys.modules."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _make_ready_future(value=None) -> _Future:
    f: _Future = _Future()
    f.set_result(value)
    return f


_TESTS_DIR = os.path.dirname(__file__)
_SYNTH_DIR = os.path.join(_TESTS_DIR, "..", "addon", "synthDrivers")
_SYNTH_PKG_DIR = os.path.join(_SYNTH_DIR, "dengjen_neural_voices")
_GLOBAL_PLUGIN_DIR = os.path.join(_TESTS_DIR, "..", "addon", "globalPlugins")
_GLOBAL_PLUGIN_PKG_DIR = os.path.join(_GLOBAL_PLUGIN_DIR, "dengjen_tts_global_plugin")

REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
SYNTH_PKG_DIR = os.path.abspath(_SYNTH_PKG_DIR)
GLOBAL_PLUGIN_PKG_DIR = os.path.abspath(_GLOBAL_PLUGIN_PKG_DIR)


def load_module_from_path(
    module_name: str, path: str, package: str | None = None
) -> types.ModuleType:
    """Execute a real .py file as a registered module.

    Tests use this to reach modules the NVDA runtime would normally import, so
    they register under a private name and leave any same-named stub installed
    below untouched. Coverage attributes by file path, not module name.
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    if package:
        mod.__package__ = package
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_real_module(
    module_name: str, filename: str, *, package: str = "dengjen_neural_voices"
) -> types.ModuleType:
    """Execute a real package submodule (with relative imports)."""
    return load_module_from_path(
        module_name, os.path.join(_SYNTH_PKG_DIR, filename), package
    )


_INSTALLED_STUB_WX = None


def install(*, stub_wx: bool = True) -> None:
    """Install the stubs. Idempotent for a repeat call with the same
    stub_wx mode; a conflicting mode means two test trees collided in one
    process, so raise instead of silently keeping whichever wx installed first.
    """
    global _INSTALLED_STUB_WX
    if _INSTALLED_STUB_WX is not None:
        if _INSTALLED_STUB_WX != stub_wx:
            raise RuntimeError(
                f"nvda_stubs.install(stub_wx={stub_wx}) conflicts with an "
                f"earlier install(stub_wx={_INSTALLED_STUB_WX}) in this process"
            )
        return
    _INSTALLED_STUB_WX = stub_wx

    # -----------------------------------------------------------------------
    # 1. Stub grpc, SCons, and all submodules
    # -----------------------------------------------------------------------

    for _grpc_name in [
        "grpc",
        "grpc._cython",
        "grpc._cython.cygrpc",
        "grpc._compression",
        "grpc.experimental",
        "grpc.aio",
    ]:
        sys.modules.setdefault(_grpc_name, MagicMock())

    _stub_module("SCons")
    _stub_module("SCons.Script", Environment=MagicMock(), Builder=MagicMock())
    _stub_module("markdown")

    # -----------------------------------------------------------------------
    # 2. NVDA internal stubs
    # -----------------------------------------------------------------------

    _stub_module(
        "globalVars", appArgs=types.SimpleNamespace(configPath="/tmp/nvda_test_config")
    )

    def _normalize_language(lang: str) -> str:
        """Port of NVDA's languageHandler.normalizeLanguage: dash -> underscore,
        lowercase language, uppercase dialect. Kept in sync with NVDA's real
        implementation so tests catch separator/casing bugs the real driver
        would hit (see issue #63)."""
        lang = lang.replace("-", "_")
        ld = lang.split("_")
        ld[0] = ld[0].lower()
        if ld[0] == "x":
            return None
        if len(ld) >= 2:
            ld[1] = ld[1].upper()
        return "_".join(ld)

    _stub_module("languageHandler", normalizeLanguage=_normalize_language)

    class _FakeConfSection(dict):
        def __missing__(self, key):
            val = _FakeConfSection()
            self[key] = val
            return val

        def isSet(self, key):
            return key in self

        @property
        def spec(self):
            return _FakeConfSection()

        def update(self, other):
            pass

    _fake_conf = _FakeConfSection()
    _fake_conf["audio"]["outputDevice"] = "default"
    _fake_conf["speech"]["dengjen_neural_voices"] = _FakeConfSection()
    _stub_module("config", conf=_fake_conf)

    _stub_module("configobj", ConfigObj=MagicMock())

    _stub_module("logHandler", log=MagicMock())

    class _AutoPropertyMeta(type):
        """Stand-in for NVDA's baseObject.AutoPropertyObject: turns `_get_x`/
        `_set_x` method pairs into a real `x` property. The real SynthDriver's
        rate/volume/voice/etc. settings are plain attribute access at the call
        site (`self.rate = value`) and only work because NVDA wires them up this
        way; without it they'd silently shadow the getter/setter methods instead
        of calling them."""

        def __new__(mcs, name, bases, namespace):
            cls = super().__new__(mcs, name, bases, namespace)
            prop_names = set()
            for klass in cls.__mro__:
                for attr_name in vars(klass):
                    if attr_name.startswith("_get_"):
                        prop_names.add(attr_name[len("_get_") :])
                    elif attr_name.startswith("_set_"):
                        prop_names.add(attr_name[len("_set_") :])
            for prop_name in prop_names:
                if prop_name in cls.__dict__:
                    continue
                getter = getattr(cls, f"_get_{prop_name}", None)
                setter = getattr(cls, f"_set_{prop_name}", None)
                setattr(cls, prop_name, property(getter, setter))
            return cls

    class _FakeSynthDriver(metaclass=_AutoPropertyMeta):
        cachePropertiesByDefault = False
        VoiceSetting = MagicMock(return_value=MagicMock())
        VariantSetting = MagicMock(return_value=MagicMock())
        RateSetting = MagicMock(return_value=MagicMock())
        RateBoostSetting = MagicMock(return_value=MagicMock())
        VolumeSetting = MagicMock(return_value=MagicMock())
        PitchSetting = MagicMock(return_value=MagicMock())

        def __init__(self):
            pass

        def _percentToParam(self, percent, min_val, max_val):
            return min_val + (max_val - min_val) * percent / 100

    _default_synth = types.SimpleNamespace(name="espeak", voice="default")

    _stub_module(
        "synthDriverHandler",
        SynthDriver=_FakeSynthDriver,
        VoiceInfo=MagicMock(side_effect=lambda id, name, lang: (id, name, lang)),
        synthDoneSpeaking=MagicMock(),
        synthIndexReached=MagicMock(),
        getSynth=lambda: _default_synth,
    )

    _stub_module("autoSettingsUtils")
    _stub_module(
        "autoSettingsUtils.driverSetting",
        DriverSetting=MagicMock(return_value=MagicMock()),
        NumericDriverSetting=MagicMock(return_value=MagicMock()),
    )

    class _FakeWavePlayer:
        def __init__(self, *args, **kwargs):
            pass

        def feed(self, data):
            pass

        def sync(self):
            pass

        def stop(self):
            pass

        def pause(self, switch):
            pass

        def close(self):
            pass

        def idle(self):
            pass

        def setVolume(self, **kwargs):
            pass

    _stub_module("nvwave", WavePlayer=_FakeWavePlayer)

    _say_all = MagicMock()
    _say_all.isRunning.return_value = False
    _speech_mod = _stub_module("speech")
    _speech_mod.sayAll = MagicMock()
    _speech_mod.sayAll.SayAllHandler = _say_all
    _stub_module(
        "speech.commands",
        IndexCommand=type("IndexCommand", (), {"index": 0}),
        BreakCommand=type("BreakCommand", (), {"time": 0}),
        LangChangeCommand=type(
            "LangChangeCommand", (), {"lang": "en", "isDefault": False}
        ),
        RateCommand=type("RateCommand", (), {"newValue": 50}),
        VolumeCommand=type("VolumeCommand", (), {"newValue": 100}),
        PitchCommand=type("PitchCommand", (), {"newValue": 50}),
    )

    builtins._ = lambda message: message

    def _init_translation():
        module = inspect.getmodule(inspect.currentframe().f_back)
        module._ = lambda message: message

    _stub_module(
        "addonHandler",
        initTranslation=_init_translation,
        getAvailableAddons=list,
    )

    if stub_wx:
        _stub_module(
            "wx",
            ID_ANY=0,
            YES=1,
            NO=2,
            YES_NO=3,
            OK=4,
            ICON_WARNING=8,
            ICON_ERROR=16,
            ICON_INFORMATION=32,
            EVT_MENU=MagicMock(),
            CallAfter=MagicMock(side_effect=lambda func, *a, **kw: func(*a, **kw)),
            ProgressDialog=MagicMock(return_value=MagicMock()),
        )
    _stub_module(
        "gui",
        messageBox=MagicMock(),
        mainFrame=MagicMock(),
        runScriptModalDialog=MagicMock(),
    )
    _stub_module(
        "gui.settingsDialogs",
        NVDASettingsDialog=MagicMock(),
        SpeechSettingsPanel=MagicMock(),
    )
    _stub_module("core", postNvdaStartup=MagicMock(), restart=MagicMock())

    class _FakeGlobalPlugin:
        """NVDA's GlobalPlugin base. A MagicMock() cannot stand in: subclassing
        an instance silently produces a MagicMock and drops the class body, so
        every method under test would become a no-op that still passes."""

        def __init__(self, *args, **kwargs):
            pass

        def terminate(self):
            pass

    _stub_module("globalPluginHandler", GlobalPlugin=_FakeGlobalPlugin)
    _stub_module("ui", message=MagicMock())

    # -----------------------------------------------------------------------
    # 3. Register `dengjen_neural_voices` as a package WITHOUT running __init__.py
    # -----------------------------------------------------------------------

    if _SYNTH_DIR not in sys.path:
        sys.path.insert(0, _SYNTH_DIR)

    _pkg = types.ModuleType("dengjen_neural_voices")
    _pkg.__path__ = [_SYNTH_PKG_DIR]
    _pkg.__package__ = "dengjen_neural_voices"
    _pkg.__spec__ = importlib.util.spec_from_file_location(
        "dengjen_neural_voices",
        os.path.join(_SYNTH_PKG_DIR, "__init__.py"),
        submodule_search_locations=[_SYNTH_PKG_DIR],
    )
    sys.modules["dengjen_neural_voices"] = _pkg

    # -----------------------------------------------------------------------
    # 4. Stub intra-package submodules that have platform/runtime dependencies
    # -----------------------------------------------------------------------

    _aio = _stub_module("dengjen_neural_voices.aio")
    _aio.initialize = MagicMock()
    _aio.ensure_running = MagicMock()
    _aio.terminate = MagicMock()
    _aio.CancelledError = Exception
    _aio.asyncio = MagicMock()
    _aio.asyncio_cancel_task = MagicMock()
    _aio.asyncio_coroutine_to_concurrent_future = lambda f: f
    _aio.run_in_executor = MagicMock()

    _aio.ENGINE = types.SimpleNamespace(executor=None, event_loop=MagicMock())

    def _call_threaded(func):

        def wrapper(*args, **kwargs):
            _aio.ensure_running()
            try:
                return _aio.ENGINE.executor.submit(func, *args, **kwargs)
            except RuntimeError:
                return None

        return wrapper

    _aio.call_threaded = _call_threaded

    # -----------------------------------------------------------------------
    # 5. Load real submodules we actually want to test
    # -----------------------------------------------------------------------

    _load_real_module("dengjen_neural_voices.const", "const.py")
    _load_real_module("dengjen_neural_voices.helpers", "helpers.py")
    _load_real_module("dengjen_neural_voices.voice_migration", "voice_migration.py")
    _load_real_module(
        "dengjen_neural_voices.domain.tts_system",
        os.path.join("domain", "tts_system.py"),
        package="dengjen_neural_voices.domain",
    )

    _pkg.DengjenTextToSpeechSystem = sys.modules[
        "dengjen_neural_voices.domain.tts_system"
    ].DengjenTextToSpeechSystem
    _pkg.DENGJEN_VOICES_DIR = sys.modules[
        "dengjen_neural_voices.domain.tts_system"
    ].DENGJEN_VOICES_DIR

    if _GLOBAL_PLUGIN_DIR not in sys.path:
        sys.path.insert(0, _GLOBAL_PLUGIN_DIR)

    if stub_wx:
        import dengjen_neural_voices.adapters.dengjen_grpc as _dengjen_grpc

        _gui_plugin_pkg = types.ModuleType("dengjen_tts_global_plugin")
        _gui_plugin_pkg.__path__ = [_GLOBAL_PLUGIN_PKG_DIR]
        _gui_plugin_pkg.__package__ = "dengjen_tts_global_plugin"
        _gui_plugin_pkg.DengjenTextToSpeechSystem = sys.modules[
            "dengjen_neural_voices.domain.tts_system"
        ].DengjenTextToSpeechSystem
        _gui_plugin_pkg.DENGJEN_VOICES_DIR = sys.modules[
            "dengjen_neural_voices.domain.tts_system"
        ].DENGJEN_VOICES_DIR
        _gui_plugin_pkg.helpers = sys.modules["dengjen_neural_voices.helpers"]
        _gui_plugin_pkg.voice_migration = sys.modules[
            "dengjen_neural_voices.voice_migration"
        ]
        _gui_plugin_pkg.aio = _aio
        _gui_plugin_pkg.DengjenGrpcBackend = _dengjen_grpc.DengjenGrpcBackend
        sys.modules["dengjen_tts_global_plugin"] = _gui_plugin_pkg
