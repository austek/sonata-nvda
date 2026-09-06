import asyncio
import atexit
import ctypes
import os
import re
import subprocess
import time
from pathlib import Path

import globalVars
from logHandler import log

VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def _vcruntime_missing():
    """Return True if vcruntime140_1.dll cannot be loaded.

    dengjen-tts-grpc.exe is built with MSVC and needs the Visual C++ 2015-2022
    Redistributable (x64). On fresh Windows installs without it, Popen
    succeeds but the child process exits immediately with a missing-DLL
    dialog the user never sees from inside NVDA; the addon then logs the
    misleading 'Connection refused' from the failing gRPC channel.

    Use ctypes.WinDLL to ask Windows directly — it respects the standard
    DLL search path, so this is more reliable than checking a fixed
    System32 location.
    """
    try:
        ctypes.WinDLL("vcruntime140_1.dll")
        return False
    except (OSError, AttributeError):
        return True


def _show_vcruntime_warning():
    """Defer a user-facing wx messageBox about the missing VC++ redistributable.

    Imports of wx and gui are local so this module stays importable from
    contexts (tests, headless tooling) where the NVDA GUI isn't available.
    """
    try:
        import gui
        import wx

        wx.CallAfter(
            gui.messageBox,
            (
                "Dengjen Neural Voices could not start because the "
                "Microsoft Visual C++ 2015-2022 Redistributable (x64) "
                f"is not installed.\n\nDownload and install it from:\n{VC_REDIST_URL}\n\n"
                "Then restart NVDA."
            ),
            "Dengjen: missing dependency",
            style=wx.ICON_ERROR,
            parent=gui.mainFrame,
        )
    except Exception:
        log.exception(
            "Failed to show VC++ redistributable warning dialog", exc_info=True
        )


from ...const import DENGJEN_VOICES_BASE_DIR
from ...helpers import BIN_DIRECTORY, import_bundled_library
from ...ports.tts_backend import (
    BackendUnavailableError,
    LoadedVoice,
    SynthesisError,
    SynthOptions,
    VoiceLoadError,
)

with import_bundled_library():
    import grpc

    from ... import aio
    from .grpc_protos import dengjen_grpc_pb2 as msgs
    from .grpc_protos.dengjen_grpc_pb2_grpc import DengjenGrpcStub


DENGJEN_GRPC_SERVER_PORT = None
GRPC_SERVER_PROCESS = None
SERVER_LOG_HANDLE = None
CHANNEL = None
DENGJEN_GRPC_SERVICE = None
SERVER_CHECK_TIMEOUT = 15


STARTUP_TIMEOUT = SERVER_CHECK_TIMEOUT + 5
CALL_TIMEOUT = 10
CHANNEL_CLOSE_TIMEOUT = 5
PORT_HANDSHAKE_TIMEOUT = 10


_LISTENING_LINE_RE = re.compile(r"DENGJEN_GRPC_LISTENING port=(\d+)\r?\n")


def _wait_for_listening_port(process, log_path, timeout=None, poll_interval=0.05):
    """Poll the server's log file for its handshake line and return the port.

    Requires the trailing newline in the match so a line still being
    flushed mid-write can't be read as a complete, truncated port number.
    """
    if timeout is None:
        timeout = PORT_HANDSHAKE_TIMEOUT
    deadline = time.monotonic() + timeout
    while True:
        try:
            with open(log_path, "rb") as log_file:
                content = log_file.read().decode(errors="replace")
        except OSError:
            content = ""
        match = _LISTENING_LINE_RE.search(content)
        if match:
            return int(match.group(1))
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(
                "dengjen-tts-grpc.exe exited before reporting its listening "
                f"port (exit code: {exit_code})"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"dengjen-tts-grpc.exe did not report its listening port "
                f"within {timeout}s"
            )
        time.sleep(poll_interval)


def start_grpc_server():
    global GRPC_SERVER_PROCESS, DENGJEN_GRPC_SERVER_PORT, SERVER_LOG_HANDLE
    if hasattr(globalVars, "DENGJEN_GRPC_SERVER_PORT"):
        DENGJEN_GRPC_SERVER_PORT = globalVars.DENGJEN_GRPC_SERVER_PORT
        GRPC_SERVER_PROCESS = globalVars.GRPC_SERVER_PROCESS
        return True
    if _vcruntime_missing():
        log.error(
            "Dengjen GRPC server cannot start: vcruntime140_1.dll not found. "
            "The Microsoft Visual C++ 2015-2022 Redistributable (x64) is required. "
            f"Download and install it from {VC_REDIST_URL} then restart NVDA."
        )
        _show_vcruntime_warning()
        return False
    grpc_server_exe = os.path.join(BIN_DIRECTORY, "dengjen-tts-grpc.exe")
    nvda_espeak_dir = os.path.join(globalVars.appDir, "synthDrivers")
    env = os.environ.copy()
    env.update(
        {
            "DENGJEN_GRPC_SERVER_PORT": "0",
            "DENGJEN_ESPEAKNG_DATA_DIRECTORY": os.fspath(nvda_espeak_dir),
            "DENGJEN_GRPC": "info",
        }
    )
    creationflags = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.REALTIME_PRIORITY_CLASS
    )
    try:
        server_log_file = os.path.join(
            DENGJEN_VOICES_BASE_DIR, "logs", "dengjen-tts-grpc.log"
        )
        Path(server_log_file).parent.mkdir(parents=True, exist_ok=True)
        server_stdout = SERVER_LOG_HANDLE = open(server_log_file, "wb")  # noqa: SIM115
    except OSError:
        log.exception(
            "Failed to open server log file for writing; cannot confirm the "
            "Dengjen GRPC server's listening port without it.",
            exc_info=True,
        )
        return False
    try:
        GRPC_SERVER_PROCESS = subprocess.Popen(
            args=grpc_server_exe,
            cwd=os.fspath(BIN_DIRECTORY),
            env=env,
            creationflags=creationflags,
            stdout=server_stdout,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        log.exception(
            "Failed to start Dengjen GRPC server. The synth will not be available.",
            exc_info=True,
        )
        if SERVER_LOG_HANDLE is not None:
            SERVER_LOG_HANDLE.close()
            SERVER_LOG_HANDLE = None
        return False
    try:
        DENGJEN_GRPC_SERVER_PORT = _wait_for_listening_port(
            GRPC_SERVER_PROCESS, server_log_file
        )
    except Exception:
        log.exception(
            "Dengjen GRPC server did not report a listening port; killing it.",
        )
        GRPC_SERVER_PROCESS.kill()
        GRPC_SERVER_PROCESS = None
        DENGJEN_GRPC_SERVER_PORT = None
        if SERVER_LOG_HANDLE is not None:
            SERVER_LOG_HANDLE.close()
            SERVER_LOG_HANDLE = None
        return False
    globalVars.DENGJEN_GRPC_SERVER_PORT = DENGJEN_GRPC_SERVER_PORT
    globalVars.GRPC_SERVER_PROCESS = GRPC_SERVER_PROCESS
    return True


@aio.asyncio_coroutine_to_concurrent_future
async def initialize():
    global CHANNEL, DENGJEN_GRPC_SERVICE
    if not start_grpc_server():
        raise RuntimeError("Failed to start the Dengjen GRPC server")
    if CHANNEL is not None:
        try:
            channel_loop = getattr(CHANNEL, "_loop", None)
            if (
                channel_loop is aio.ENGINE.event_loop
                and aio.ENGINE.event_loop.is_running()
            ):
                return
        except Exception:
            log.debug("Failed to inspect the existing GRPC channel", exc_info=True)
        try:
            await CHANNEL.close()
        except Exception:
            log.debug("Failed to close the stale GRPC channel", exc_info=True)
        CHANNEL = None
    port = DENGJEN_GRPC_SERVER_PORT
    CHANNEL = grpc.aio.insecure_channel(f"localhost:{port}")
    DENGJEN_GRPC_SERVICE = DengjenGrpcStub(CHANNEL)


def close_channel():
    """Close the aio channel on the loop that owns it.

    Channel.close() is a coroutine whose internals walk the running loop's
    task set, so it cannot be driven from another thread or a stopped loop.
    """
    global CHANNEL
    if CHANNEL is None:
        return
    channel, CHANNEL = CHANNEL, None
    loop = aio.ENGINE.event_loop
    if loop is None or not loop.is_running():
        log.debug("Discarding the GRPC channel: its event loop is gone")
        channel.close().close()
        return
    try:
        asyncio.run_coroutine_threadsafe(channel.close(), loop).result(
            timeout=CHANNEL_CLOSE_TIMEOUT
        )
    except Exception:
        log.debug("Failed to close the GRPC channel cleanly", exc_info=True)


@atexit.register
def terminate():
    global GRPC_SERVER_PROCESS, DENGJEN_GRPC_SERVER_PORT, SERVER_LOG_HANDLE
    DENGJEN_GRPC_SERVER_PORT = None
    try:
        close_channel()
        aio.terminate()
        if GRPC_SERVER_PROCESS is not None:
            GRPC_SERVER_PROCESS.terminate()
    finally:
        GRPC_SERVER_PROCESS = None
        if SERVER_LOG_HANDLE is not None:
            SERVER_LOG_HANDLE.close()
            SERVER_LOG_HANDLE = None


async def _clear_stale_server_state():
    """Drop cached server/port/channel state after a failed readiness check.

    start_grpc_server() caches the spawned process and its port in
    globalVars as soon as Popen succeeds -- before anything confirms the
    server actually bound that port and is answering RPCs. find_free_port()
    closes its probe socket before the child starts, so another process can
    claim the port in that gap; if that happens the child exits (or never
    becomes reachable) and, without this, every later start_grpc_server()
    call would keep reusing the same dead process and port forever, since
    its cache check only looks at presence, not health.

    Also clears CHANNEL/DENGJEN_GRPC_SERVICE: initialize() reuses a cached
    CHANNEL outright when its loop still matches the running one, without
    checking which port it was opened against -- leaving those set would
    have every later RPC call reconnect to the dead port's channel even
    after a fresh subprocess starts on a new one.

    Called from check_grpc_server() -- the one place that actually
    confirms the server is alive -- whenever that confirmation fails, so
    the next initialize() call opens a fresh channel to a freshly spawned
    subprocess instead. Awaits the channel's own close() directly (rather
    than close_channel()'s thread-hop) because this always runs on the aio
    loop already -- close_channel()'s run_coroutine_threadsafe().result()
    would deadlock waiting on the very loop it's called from.
    """
    global \
        GRPC_SERVER_PROCESS, \
        DENGJEN_GRPC_SERVER_PORT, \
        SERVER_LOG_HANDLE, \
        CHANNEL, \
        DENGJEN_GRPC_SERVICE
    process, GRPC_SERVER_PROCESS = GRPC_SERVER_PROCESS, None
    DENGJEN_GRPC_SERVER_PORT = None
    DENGJEN_GRPC_SERVICE = None
    channel, CHANNEL = CHANNEL, None
    if channel is not None:
        try:
            await channel.close()
        except Exception:
            log.debug("Failed to close the stale GRPC channel", exc_info=True)
    if hasattr(globalVars, "DENGJEN_GRPC_SERVER_PORT"):
        del globalVars.DENGJEN_GRPC_SERVER_PORT
    if hasattr(globalVars, "GRPC_SERVER_PROCESS"):
        del globalVars.GRPC_SERVER_PROCESS
    if process is not None:
        try:
            process.kill()
        except Exception:
            log.debug("Failed to kill an unready GRPC server process", exc_info=True)
    if SERVER_LOG_HANDLE is not None:
        SERVER_LOG_HANDLE.close()
        SERVER_LOG_HANDLE = None


@aio.asyncio_coroutine_to_concurrent_future
async def check_grpc_server() -> str:
    try:
        async with asyncio.timeout(SERVER_CHECK_TIMEOUT):
            return await get_dengjen_version()
    except Exception:
        await _clear_stale_server_state()
        raise


async def get_dengjen_version():
    resp = await DENGJEN_GRPC_SERVICE.GetDengjenVersion(msgs.Empty())
    return resp.version


@aio.asyncio_coroutine_to_concurrent_future
async def load_voice(config_path):
    req = msgs.VoiceConfigLocation(path=config_path)
    return await DENGJEN_GRPC_SERVICE.LoadVoice(req)


@aio.asyncio_coroutine_to_concurrent_future
async def get_synth_options(voice_id):
    req = msgs.VoiceRef(voice_key=voice_id)
    return await DENGJEN_GRPC_SERVICE.GetSynthesisOptions(req)


@aio.asyncio_coroutine_to_concurrent_future
async def set_synth_options(
    voice_id, speaker=None, length_scale=None, noise_scale=None, noise_w=None
):
    req = msgs.VoiceSynthesisSettings(
        voice_key=voice_id,
        synthesis_options=msgs.SynthesisSettings(
            speaker=speaker,
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w=noise_w,
        ),
    )
    return await DENGJEN_GRPC_SERVICE.SetSynthesisOptions(req)


async def speak(
    voice_id,
    text,
    rate=None,
    volume=None,
    pitch=None,
    appended_silence_ms=None,
    streaming=False,
):
    speech_args = None
    if any(v is not None for v in (rate, volume, pitch, appended_silence_ms)):
        speech_args = msgs.ProsodyControls(
            rate=rate,
            volume=volume,
            pitch=pitch,
            appended_silence_ms=appended_silence_ms,
        )
    utterance = msgs.SynthesisRequest(
        voice_key=voice_id,
        text=text,
        prosody=speech_args,
    )
    if streaming:
        stream = DENGJEN_GRPC_SERVICE.SynthesizeUtteranceRealtime
    else:
        stream = DENGJEN_GRPC_SERVICE.SynthesizeUtterance
    async for ret in stream(utterance):
        yield ret


async def bench(n=10000):
    initialize()
    t0 = time.perf_counter()
    for _ in range(n):
        await get_dengjen_version()
    return time.perf_counter() - t0


class DengjenGrpcBackend:
    """TTSBackend adapter over this module's process-wide gRPC client state.

    A thin facade: the gRPC channel and dengjen-tts-grpc.exe subprocess are
    genuinely process-wide resources (one subprocess for the whole NVDA
    process, regardless of how many SynthDriver instances come and go), so
    this class delegates to the module-level functions/globals above rather
    than duplicating that state per instance.
    """

    def initialize(self):
        try:
            initialize().result(timeout=STARTUP_TIMEOUT)
        except Exception as exc:
            raise BackendUnavailableError(str(exc)) from exc

    def check_version(self):
        try:
            return check_grpc_server().result(timeout=STARTUP_TIMEOUT)
        except Exception:
            log.warning(
                "Dengjen GRPC server was not ready on the first attempt "
                "(possibly lost a port-bind race); retrying once with a "
                "fresh subprocess.",
                exc_info=True,
            )
            try:
                initialize().result(timeout=STARTUP_TIMEOUT)
                return check_grpc_server().result(timeout=STARTUP_TIMEOUT)
            except Exception as exc:
                raise BackendUnavailableError(str(exc)) from exc

    def shutdown(self):
        try:
            terminate()
        except Exception as exc:
            raise BackendUnavailableError(str(exc)) from exc

    def load_voice(self, config_path):
        try:
            info = load_voice(config_path).result(timeout=CALL_TIMEOUT)
        except Exception as exc:
            raise VoiceLoadError(str(exc)) from exc
        return LoadedVoice(
            backend_voice_id=info.voice_key,
            supports_streaming_output=info.supports_streaming_output,
            sample_rate=info.audio.sample_rate,
            speakers=dict(info.speakers),
            defaults=SynthOptions(
                speaker=info.synthesis_options.speaker,
                length_scale=info.synthesis_options.length_scale,
                noise_scale=info.synthesis_options.noise_scale,
                noise_w=info.synthesis_options.noise_w,
            ),
        )

    def get_synth_options(self, backend_voice_id):
        try:
            opts = get_synth_options(backend_voice_id).result(timeout=CALL_TIMEOUT)
        except Exception as exc:
            raise VoiceLoadError(str(exc)) from exc
        return SynthOptions(
            speaker=opts.speaker,
            length_scale=opts.length_scale,
            noise_scale=opts.noise_scale,
            noise_w=opts.noise_w,
        )

    def set_synth_options(self, backend_voice_id, **kwargs):
        try:
            set_synth_options(backend_voice_id, **kwargs).result(timeout=CALL_TIMEOUT)
        except Exception as exc:
            raise VoiceLoadError(str(exc)) from exc

    async def synthesize(
        self,
        backend_voice_id,
        text,
        rate,
        volume,
        pitch,
        sentence_silence_ms,
        streaming,
    ):
        try:
            async for ret in speak(
                voice_id=backend_voice_id,
                text=text,
                rate=rate,
                volume=volume,
                pitch=pitch,
                appended_silence_ms=sentence_silence_ms,
                streaming=streaming,
            ):
                yield ret.audio_bytes
        except Exception as exc:
            raise SynthesisError(str(exc)) from exc
