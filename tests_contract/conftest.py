"""
conftest.py for the gRPC contract tests — these talk to the real, vendored
dengjen-tts-grpc.exe over a real gRPC channel.

Deliberately not part of tests/: that suite's conftest.py stubs `grpc`
itself (sys.modules["grpc"] = MagicMock()) for every test in it, and
pytest.ini's testpaths=tests keeps this directory out of a bare `pytest`
run, so the two never collide in the same process. Run this tree with
`pytest tests_contract/` explicitly.

No NVDA stubbing here. The generated protobuf/grpc client
(synthDrivers/dengjen_neural_voices/grpc_client/grpc_protos/) has no NVDA
dependencies, so tests talk to it directly instead of going through
grpc_client/__init__.py — that module pulls in globalVars, logHandler, and
Windows subprocess flags meant for NVDA's own background process lifecycle,
not a short-lived test process.

Windows-only: dengjen-tts-grpc.exe is a Windows PE binary and the vendored
`grpc` package under lib/ is compiled for cp313-win_amd64. Test modules
must check `sys.platform` and call `pytest.skip(..., allow_module_level=True)`
before importing `grpc` — a plain skipif marker does not prevent pytest
from importing the module (and therefore `grpc`) during collection.
"""

import os
import sys

_TESTS_CONTRACT_DIR = os.path.dirname(__file__)
REPO_ROOT = os.path.abspath(os.path.join(_TESTS_CONTRACT_DIR, ".."))
_SYNTH_PKG_DIR = os.path.join(
    REPO_ROOT, "addon", "synthDrivers", "dengjen_neural_voices"
)

LIB_DIRECTORY = os.path.join(_SYNTH_PKG_DIR, "lib")
BIN_DIRECTORY = os.path.join(_SYNTH_PKG_DIR, "bin")
GRPC_CLIENT_DIR = os.path.join(_SYNTH_PKG_DIR, "adapters", "dengjen_grpc")
GRPC_SERVER_EXE = os.path.join(BIN_DIRECTORY, "dengjen-tts-grpc.exe")


for _p in (LIB_DIRECTORY, GRPC_CLIENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
