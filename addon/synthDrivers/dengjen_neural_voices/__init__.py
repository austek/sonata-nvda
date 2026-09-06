# Copyright (c) 2023 Musharraf Omer
# This file is covered by the GNU General Public License.

"""NVDA discovers this add-on's synth driver by importing this package and
reading `SynthDriver` at the top level (synthDriverHandler.getSynth) -- this
re-export is load-bearing, not decorative.

`DengjenTextToSpeechSystem` and `DENGJEN_VOICES_DIR` are re-exported as this
package's public API for other in-addon consumers (the global plugin's voice
manager), so they don't need to import `domain.tts_system` directly. Adapter
classes (e.g. `DengjenGrpcBackend`) are deliberately NOT re-exported here:
this module is imported for synth driver discovery on every NVDA startup,
and routing them through this top-level import would pull the gRPC adapter's
bundled deps into that hot path instead of staying lazily imported at the
point of use."""

from .adapters.nvda.synth_driver import SynthDriver
from .domain.tts_system import DENGJEN_VOICES_DIR, DengjenTextToSpeechSystem

__all__ = ["DENGJEN_VOICES_DIR", "DengjenTextToSpeechSystem", "SynthDriver"]
