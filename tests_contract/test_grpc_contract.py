"""
Contract test against the real, vendored dengjen-tts-grpc.exe: starts the actual
engine binary and confirms it answers the GetDengjenVersion handshake over a
real gRPC channel — the same call grpc_client.check_grpc_server() makes
during NVDA startup.

Also exercises LoadVoice + SynthesizeUtterance against a real trained
voice model, downloaded from HuggingFace at test time (see
TestVoiceSynthesis below) — the first test to touch actual speech
synthesis rather than the mocked grpc_client used everywhere in tests/.
"""

import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

if sys.platform != "win32":
    pytest.skip("dengjen-tts-grpc.exe is a Windows binary", allow_module_level=True)

import espeakng_loader
import grpc
import grpc_protos.dengjen_grpc_pb2 as msgs
import grpc_protos.dengjen_grpc_pb2_grpc as pb2_grpc

from tests_contract.conftest import BIN_DIRECTORY, GRPC_SERVER_EXE

STARTUP_TIMEOUT = 15
STARTUP_POLL_INTERVAL = 0.5

_LISTENING_LINE_RE = re.compile(r"DENGJEN_GRPC_LISTENING port=(\d+)\r?\n")


def _wait_for_listening_port(process, log_path, timeout, poll_interval=0.05):
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
        if process.poll() is not None:
            return None
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_interval)


@pytest.fixture(scope="session")
def grpc_server():
    assert os.path.exists(GRPC_SERVER_EXE), (
        f"dengjen-tts-grpc.exe not found at {GRPC_SERVER_EXE}"
    )

    log_path = os.path.join(tempfile.mkdtemp(), "dengjen-tts-grpc.log")
    env = os.environ.copy()

    espeakng_data_dir = os.path.dirname(espeakng_loader.get_data_path())
    env.update(
        {
            "DENGJEN_GRPC_SERVER_PORT": "0",
            "DENGJEN_ESPEAKNG_DATA_DIRECTORY": espeakng_data_dir,
            "DENGJEN_GRPC": "info",
        }
    )

    with open(log_path, "wb") as log_file:
        process = subprocess.Popen(
            args=GRPC_SERVER_EXE,
            cwd=BIN_DIRECTORY,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    port = _wait_for_listening_port(process, log_path, timeout=STARTUP_TIMEOUT)
    if port is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        with open(log_path, "rb") as log_file:
            server_log = log_file.read().decode(errors="replace")
        pytest.fail(
            f"dengjen-tts-grpc.exe did not report a listening port within "
            f"{STARTUP_TIMEOUT}s (exit code: {process.poll()}).\n"
            f"Server log:\n{server_log}"
        )

    channel = grpc.insecure_channel(f"localhost:{port}")
    stub = pb2_grpc.DengjenGrpcStub(channel)

    deadline = time.monotonic() + STARTUP_TIMEOUT
    last_error = None
    ready = False
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            stub.GetDengjenVersion(msgs.Empty(), timeout=STARTUP_POLL_INTERVAL)
            ready = True
            break
        except grpc.RpcError as exc:
            last_error = exc
            time.sleep(STARTUP_POLL_INTERVAL)

    if not ready:
        channel.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        with open(log_path, "rb") as log_file:
            server_log = log_file.read().decode(errors="replace")
        pytest.fail(
            f"dengjen-tts-grpc.exe did not become ready within {STARTUP_TIMEOUT}s "
            f"(exit code: {process.poll()}, last gRPC error: {last_error}).\n"
            f"Server log:\n{server_log}"
        )

    yield stub

    channel.close()
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


class TestVersionHandshake:
    def test_get_dengjen_version_returns_a_non_empty_version_string(self, grpc_server):
        response = grpc_server.GetDengjenVersion(msgs.Empty())
        assert isinstance(response.version, str)
        assert response.version.strip() != ""


VOICE_KEY = "vi_VN-vivos-x_low"
VOICE_FILES_BASE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/vi/vi_VN/vivos/x_low"
)
DOWNLOAD_TIMEOUT = 60
CALL_TIMEOUT = 30


def _download(url, target_path):
    with (
        urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response,
        open(target_path, "wb") as f,
    ):
        f.write(response.read())


@pytest.fixture(scope="session")
def downloaded_voice(tmp_path_factory):
    """Download the voice once per session; returns the config (.onnx.json) path.

    LoadVoice takes the config path and expects the matching .onnx file
    alongside it (same naming convention DengjenVoice.load() relies on in
    production).
    """
    voice_dir = tmp_path_factory.mktemp("voice")
    onnx_path = voice_dir / f"{VOICE_KEY}.onnx"
    config_path = voice_dir / f"{VOICE_KEY}.onnx.json"
    _download(f"{VOICE_FILES_BASE_URL}/{VOICE_KEY}.onnx", onnx_path)
    _download(f"{VOICE_FILES_BASE_URL}/{VOICE_KEY}.onnx.json", config_path)
    return str(config_path)


class TestVoiceSynthesis:
    def test_load_voice_and_synthesize_returns_non_empty_audio(
        self, grpc_server, downloaded_voice
    ):
        voice_info = grpc_server.LoadVoice(
            msgs.VoiceConfigLocation(path=downloaded_voice), timeout=CALL_TIMEOUT
        )
        assert voice_info.voice_key
        assert voice_info.audio.sample_rate > 0

        utterance = msgs.SynthesisRequest(
            voice_key=voice_info.voice_key, text="xin chào"
        )
        frames = list(grpc_server.SynthesizeUtterance(utterance, timeout=CALL_TIMEOUT))

        assert frames, "expected at least one audio frame from SynthesizeUtterance"
        assert sum(len(frame.audio_bytes) for frame in frames) > 0
