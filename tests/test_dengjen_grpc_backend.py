"""
Tests for DengjenGrpcBackend's TTSBackend surface: that it correctly
translates the underlying module-level gRPC calls' failures into the
TTSBackend port's typed errors. The gRPC calls themselves (against a real
dengjen-tts-grpc.exe) are covered by tests_contract/, not here.
"""

from concurrent.futures import Future

import pytest
from dengjen_neural_voices.adapters import dengjen_grpc
from dengjen_neural_voices.ports.tts_backend import (
    BackendUnavailableError,
    SynthesisError,
    VoiceLoadError,
)

backend = dengjen_grpc.DengjenGrpcBackend()


def _failed_future(exc):
    f = Future()
    f.set_exception(exc)
    return f


def _resolved_future(value):
    f = Future()
    f.set_result(value)
    return f


def test_initialize_wraps_a_failure_as_backend_unavailable(monkeypatch):
    monkeypatch.setattr(
        dengjen_grpc, "initialize", lambda: _failed_future(RuntimeError("no port"))
    )
    with pytest.raises(BackendUnavailableError):
        backend.initialize()


def test_check_version_wraps_a_failure_as_backend_unavailable(monkeypatch):

    monkeypatch.setattr(
        dengjen_grpc, "check_grpc_server", lambda: _failed_future(TimeoutError())
    )
    monkeypatch.setattr(
        dengjen_grpc, "initialize", lambda: _failed_future(RuntimeError("no port"))
    )
    with pytest.raises(BackendUnavailableError):
        backend.check_version()


def test_check_version_retries_once_after_a_failed_handshake_and_succeeds(monkeypatch):
    """The retry this backend needs for a lost find_free_port() race
    (#143): the first handshake fails, a fresh initialize() is attempted,
    and the second handshake succeeds."""
    attempts = []

    def _check_grpc_server():
        attempts.append(None)
        if len(attempts) == 1:
            return _failed_future(TimeoutError())
        return _resolved_future("1.2.3")

    monkeypatch.setattr(dengjen_grpc, "check_grpc_server", _check_grpc_server)
    monkeypatch.setattr(dengjen_grpc, "initialize", lambda: _resolved_future(None))

    assert backend.check_version() == "1.2.3"
    assert len(attempts) == 2


def test_check_version_gives_up_as_backend_unavailable_after_one_failed_retry(
    monkeypatch,
):
    """The retry is bounded: a second consecutive failure still surfaces as
    BackendUnavailableError rather than looping indefinitely."""
    attempts = []

    def _check_grpc_server():
        attempts.append(None)
        return _failed_future(TimeoutError())

    monkeypatch.setattr(dengjen_grpc, "check_grpc_server", _check_grpc_server)
    monkeypatch.setattr(dengjen_grpc, "initialize", lambda: _resolved_future(None))

    with pytest.raises(BackendUnavailableError):
        backend.check_version()

    assert len(attempts) == 2


def test_load_voice_wraps_a_failure_as_voice_load_error(monkeypatch):
    monkeypatch.setattr(
        dengjen_grpc,
        "load_voice",
        lambda path: _failed_future(RuntimeError("bad proto")),
    )
    with pytest.raises(VoiceLoadError):
        backend.load_voice("/tmp/v/config.json")


def test_load_voice_maps_the_response_fields(monkeypatch):
    class _FakeInfo:
        voice_key = "v1"
        supports_streaming_output = True

        class audio:
            sample_rate = 22050

        speakers = {"0": "Alice"}

        class synthesis_options:
            speaker = "Alice"
            length_scale = 1.0
            noise_scale = 0.5
            noise_w = 0.8

    ready = Future()
    ready.set_result(_FakeInfo())
    monkeypatch.setattr(dengjen_grpc, "load_voice", lambda path: ready)

    loaded = backend.load_voice("/tmp/v/config.json")

    assert loaded.backend_voice_id == "v1"
    assert loaded.sample_rate == 22050
    assert loaded.speakers == {"0": "Alice"}
    assert loaded.defaults.noise_scale == 0.5


def test_set_synth_options_wraps_a_failure_as_voice_load_error(monkeypatch):
    monkeypatch.setattr(
        dengjen_grpc,
        "set_synth_options",
        lambda voice_id, **kw: _failed_future(RuntimeError("boom")),
    )
    with pytest.raises(VoiceLoadError):
        backend.set_synth_options("v1", noise_scale=0.5)


def test_synthesize_wraps_a_failure_as_synthesis_error():
    import asyncio

    async def _boom(**kwargs):
        raise RuntimeError("stream broke")
        yield b""

    async def _run():
        with pytest.raises(SynthesisError):
            async for _ in backend.synthesize(
                "v1", "hi", None, None, None, None, False
            ):
                pass

    import dengjen_neural_voices.adapters.dengjen_grpc as mod

    orig = mod.speak
    mod.speak = _boom
    try:
        asyncio.run(_run())
    finally:
        mod.speak = orig


def test_synthesize_yields_audio_bytes_not_the_raw_message():
    """Regression test: synthesize() briefly re-yielded the raw protobuf
    message instead of unwrapping .audio_bytes (caught and fixed during this
    branch's own work). speak() yields message-shaped objects, not bytes, so
    a fixture asserting bytes-in/bytes-out here would pass even if
    synthesize() forgot to extract .audio_bytes."""
    import asyncio
    import types

    async def _fake_speak(**kwargs):
        yield types.SimpleNamespace(audio_bytes=b"abc")
        yield types.SimpleNamespace(audio_bytes=b"def")

    async def _run():
        return [
            chunk
            async for chunk in backend.synthesize(
                "v1", "hi", None, None, None, None, False
            )
        ]

    import dengjen_neural_voices.adapters.dengjen_grpc as mod

    orig = mod.speak
    mod.speak = _fake_speak
    try:
        chunks = asyncio.run(_run())
    finally:
        mod.speak = orig

    assert chunks == [b"abc", b"def"]


class TestClearStaleServerState:
    """_clear_stale_server_state() is the recovery path for a server that
    Popen'd successfully but never became reachable (e.g. lost the
    find_free_port()-to-bind race to another process) -- without it, every
    later start_grpc_server()/initialize() call would keep reusing the same
    dead process, port and channel forever, since their cache checks only
    look at presence, not health.

    It's an async function (it awaits the channel's own close() rather than
    close_channel()'s thread-hop, which would deadlock when called from the
    aio loop it's already running on -- see its docstring), so every test
    here drives it with asyncio.run(), same as the synthesize() tests
    above."""

    def test_kills_the_cached_process_and_clears_module_globals(self, monkeypatch):
        import asyncio
        import types

        killed = types.SimpleNamespace(value=False)
        fake_process = types.SimpleNamespace(
            kill=lambda: setattr(killed, "value", True)
        )
        monkeypatch.setattr(dengjen_grpc, "GRPC_SERVER_PROCESS", fake_process)
        monkeypatch.setattr(dengjen_grpc, "DENGJEN_GRPC_SERVER_PORT", 12345)

        asyncio.run(dengjen_grpc._clear_stale_server_state())

        assert killed.value
        assert dengjen_grpc.GRPC_SERVER_PROCESS is None
        assert dengjen_grpc.DENGJEN_GRPC_SERVER_PORT is None

    def test_closes_and_clears_the_channel_and_service(self, monkeypatch):
        import asyncio
        import types

        closed = types.SimpleNamespace(value=False)

        class _FakeChannel:
            async def close(self):
                closed.value = True

        monkeypatch.setattr(dengjen_grpc, "CHANNEL", _FakeChannel())
        monkeypatch.setattr(dengjen_grpc, "DENGJEN_GRPC_SERVICE", object())

        asyncio.run(dengjen_grpc._clear_stale_server_state())

        assert closed.value
        assert dengjen_grpc.CHANNEL is None
        assert dengjen_grpc.DENGJEN_GRPC_SERVICE is None

    def test_a_channel_that_fails_to_close_does_not_stop_the_rest_of_the_cleanup(
        self, monkeypatch
    ):
        import asyncio
        import types

        class _FakeChannel:
            async def close(self):
                raise Exception("channel already broken")

        killed = types.SimpleNamespace(value=False)
        fake_process = types.SimpleNamespace(
            kill=lambda: setattr(killed, "value", True)
        )
        monkeypatch.setattr(dengjen_grpc, "CHANNEL", _FakeChannel())
        monkeypatch.setattr(dengjen_grpc, "GRPC_SERVER_PROCESS", fake_process)

        asyncio.run(dengjen_grpc._clear_stale_server_state())

        assert dengjen_grpc.CHANNEL is None
        assert killed.value

    def test_clears_the_globalVars_cache(self):

        import asyncio

        import globalVars

        globalVars.DENGJEN_GRPC_SERVER_PORT = 12345
        globalVars.GRPC_SERVER_PROCESS = object()

        asyncio.run(dengjen_grpc._clear_stale_server_state())

        assert not hasattr(globalVars, "DENGJEN_GRPC_SERVER_PORT")
        assert not hasattr(globalVars, "GRPC_SERVER_PROCESS")

    def test_closes_the_server_log_handle(self, monkeypatch):
        import asyncio
        import types

        closed = types.SimpleNamespace(value=False)
        fake_handle = types.SimpleNamespace(
            close=lambda: setattr(closed, "value", True)
        )
        monkeypatch.setattr(dengjen_grpc, "SERVER_LOG_HANDLE", fake_handle)

        asyncio.run(dengjen_grpc._clear_stale_server_state())

        assert closed.value
        assert dengjen_grpc.SERVER_LOG_HANDLE is None

    def test_is_a_noop_when_nothing_is_cached(self, monkeypatch):
        import asyncio

        import globalVars

        monkeypatch.setattr(dengjen_grpc, "GRPC_SERVER_PROCESS", None)
        monkeypatch.setattr(dengjen_grpc, "DENGJEN_GRPC_SERVER_PORT", None)
        monkeypatch.setattr(dengjen_grpc, "SERVER_LOG_HANDLE", None)
        monkeypatch.setattr(dengjen_grpc, "CHANNEL", None)
        monkeypatch.setattr(dengjen_grpc, "DENGJEN_GRPC_SERVICE", None)
        monkeypatch.delattr(globalVars, "DENGJEN_GRPC_SERVER_PORT", raising=False)
        monkeypatch.delattr(globalVars, "GRPC_SERVER_PROCESS", raising=False)

        asyncio.run(dengjen_grpc._clear_stale_server_state())

    def test_a_process_that_refuses_to_die_does_not_stop_the_cleanup(self, monkeypatch):
        import asyncio
        import types

        fake_process = types.SimpleNamespace(
            kill=lambda: (_ for _ in ()).throw(Exception("access denied"))
        )
        monkeypatch.setattr(dengjen_grpc, "GRPC_SERVER_PROCESS", fake_process)

        asyncio.run(dengjen_grpc._clear_stale_server_state())

        assert dengjen_grpc.GRPC_SERVER_PROCESS is None


def test_check_grpc_server_clears_stale_state_when_the_handshake_fails():
    """The wiring half of TestClearStaleServerState: exercises
    check_grpc_server()'s real try/except body -- not a reimplementation of
    it -- and confirms a failed handshake actually triggers the cleanup
    rather than just leaving it available unused.

    Calls the coroutine function directly (asyncio.run), same as the
    synthesize() tests above: @aio.asyncio_coroutine_to_concurrent_future is
    stubbed to the identity function in tests (nvda_stubs.py), so
    check_grpc_server is the plain coroutine function here, not a
    Future-returning wrapper -- unlike in production, where the real aio
    engine backs it.
    """
    import asyncio
    import types

    import dengjen_neural_voices.adapters.dengjen_grpc as mod

    killed = types.SimpleNamespace(value=False)
    fake_process = types.SimpleNamespace(kill=lambda: setattr(killed, "value", True))

    orig_get_version = mod.get_dengjen_version
    orig_process = mod.GRPC_SERVER_PROCESS

    async def _boom():
        raise RuntimeError("connection refused")

    mod.get_dengjen_version = _boom
    mod.GRPC_SERVER_PROCESS = fake_process
    try:
        with pytest.raises(RuntimeError):
            asyncio.run(mod.check_grpc_server())
    finally:
        mod.get_dengjen_version = orig_get_version
        mod.GRPC_SERVER_PROCESS = orig_process

    assert killed.value
    assert mod.GRPC_SERVER_PROCESS is None
