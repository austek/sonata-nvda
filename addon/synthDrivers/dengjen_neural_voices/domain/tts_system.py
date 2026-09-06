# Copyright (c) 2023 Musharraf Omer
# This file is covered by the GNU General Public License.

"""Core TTS domain logic: voices, speech options, providers.

Imports no gRPC and no I/O-heavy NVDA module -- the one accepted exception is
languageHandler.normalizeLanguage, a pure string-normalization function with
no runtime/subprocess dependency of its own.
"""

import copy
import operator
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from languageHandler import normalizeLanguage

from ..const import (
    DEFAULT_PITCH,
    DEFAULT_RATE,
    DEFAULT_VOLUME,
    DENGJEN_VOICES_DIR,
    FALLBACK_SPEAKER_NAME,
    IGNORED_PUNCS,
)
from ..ports.tts_backend import TTSBackend
from ..voice_migration import migrate_voices_directory


class VoiceNotFoundError(LookupError):
    pass


class SpeakerNotFoundError(LookupError):
    pass


@dataclass
class Scales:
    length_scale: float
    noise_scale: float
    noise_w: float


class AudioProvider(ABC):
    @abstractmethod
    def generate_audio(self) -> bytes:
        """Generate audio."""


class SilenceProvider(AudioProvider):
    __slots__ = ["sample_rate", "time_ms"]

    def __init__(self, time_ms, sample_rate):
        self.time_ms = time_ms
        self.sample_rate = sample_rate

    def generate_audio(self):
        num_samples = int((self.time_ms / 1000.0) * self.sample_rate)
        return bytes(num_samples * 2)


class SpeechProvider(AudioProvider):
    """A pending request to speak some text."""

    __slots__ = ["speech_options", "text"]

    def __init__(self, text, speech_options):
        self.text = text
        self.speech_options = speech_options

    def generate_audio(self):
        return self.speech_options.speak_text(self.text)


@dataclass
class DengjenVoice:
    key: str
    name: str
    language: str
    description: str
    location: str
    backend: TTSBackend
    properties: Mapping[str, int] | None = field(default_factory=dict)
    remote_id: str | None = None
    supports_streaming_output: bool = False

    @classmethod
    def from_path(cls, path, backend):
        path = Path(path)
        key = path.name
        try:
            lang, name, quality = key.split("-")
        except ValueError:
            raise ValueError(f"Invalid voice path: {path}")
        return cls(
            key=key,
            name=name.replace("+RT", ""),
            language=normalizeLanguage(lang),
            description="",
            location=path,
            backend=backend,
            properties={"quality": quality.lower()},
        )

    def load(self):
        if self.remote_id:
            return
        try:
            self.config_path = next(self.location.glob("*.json"))
        except StopIteration:
            raise RuntimeError(
                f"Could not load voice from `{os.fspath(self.location)}`"
            )
        loaded = self.backend.load_voice(str(self.config_path))
        self.remote_id = loaded.backend_voice_id
        self.supports_streaming_output = loaded.supports_streaming_output
        self.default_scales = Scales(
            length_scale=loaded.defaults.length_scale,
            noise_scale=loaded.defaults.noise_scale,
            noise_w=loaded.defaults.noise_w,
        )
        self.sample_rate = loaded.sample_rate
        self.speakers = loaded.speakers
        self.speaker_names = list(self.speakers.values())
        self.is_multi_speaker = bool(self.speakers)
        self.default_speaker = (
            loaded.defaults.speaker if self.is_multi_speaker else None
        )

    def _get_synth_option(self, name):
        options = self.backend.get_synth_options(self.remote_id)
        return getattr(options, name)

    def _set_synth_option(self, **kwargs):
        self.backend.set_synth_options(self.remote_id, **kwargs)

    @property
    def speaker(self):
        if self.is_multi_speaker:
            return self._get_synth_option("speaker")
        return FALLBACK_SPEAKER_NAME

    @speaker.setter
    def speaker(self, value):
        if self.is_multi_speaker:
            self._set_synth_option(speaker=value)

    @property
    def noise_scale(self):
        return self._get_synth_option("noise_scale")

    @noise_scale.setter
    def noise_scale(self, value):
        self._set_synth_option(noise_scale=value)

    @property
    def length_scale(self):
        return self._get_synth_option("length_scale")

    @length_scale.setter
    def length_scale(self, value):
        self._set_synth_option(length_scale=value)

    @property
    def noise_w(self):
        return self._get_synth_option("noise_w")

    @noise_w.setter
    def noise_w(self, value):
        self._set_synth_option(noise_w=value)

    @property
    def is_fast(self):
        return "+RT" in self.key

    @property
    def variant(self):
        return "fast" if self.is_fast else "standard"

    @property
    def standard_variant_key(self):
        return DengjenTextToSpeechSystem.get_voice_variants(self.key)[0]

    @property
    def fast_variant_key(self):
        return DengjenTextToSpeechSystem.get_voice_variants(self.key)[1]

    async def synthesize(self, text, rate, volume, pitch, sentence_silence_ms):
        if (len(text) < 10) and (set(text.strip()).issubset(IGNORED_PUNCS)):
            return
        stream = self.backend.synthesize(
            self.remote_id,
            text,
            rate,
            volume,
            pitch,
            sentence_silence_ms,
            streaming=self.supports_streaming_output,
        )
        async for chunk in stream:
            yield chunk


class SpeechOptions:
    __slots__ = ["pitch", "rate", "sentence_silence_ms", "voice", "volume"]

    def __init__(
        self,
        voice,
        speaker=None,
        rate=None,
        volume=None,
        pitch=None,
        sentence_silence_ms=None,
    ):
        self.voice = None
        self.set_voice(voice)
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self.sentence_silence_ms = sentence_silence_ms

    def set_voice(self, voice: DengjenVoice):
        voice.load()
        self.voice = voice

    @property
    def speaker(self):
        return self.voice.speaker

    @speaker.setter
    def speaker(self, value):
        self.voice.speaker = value

    def copy(self):
        return copy.copy(self)

    def speak_text(self, text):
        return self.voice.synthesize(
            text, self.rate, self.volume, self.pitch, self.sentence_silence_ms
        )


class DengjenTextToSpeechSystem:
    def __init__(
        self, voices: Sequence[DengjenVoice], speech_options: SpeechOptions = None
    ):
        self.voices = voices
        if speech_options is not None:
            self.speech_options = speech_options
        else:
            try:
                voice = self.voices[0]
            except IndexError:
                raise VoiceNotFoundError("No Piper voices found")
            self.speech_options = SpeechOptions(voice=voice)

    @contextmanager
    def create_synthesis_context(self):
        old_speech_options = self.speech_options.copy()
        try:
            yield
        finally:
            self.speech_options = old_speech_options

    def shutdown(self):
        # No-op by design: the gRPC engine process outlives any single TTS
        # system, so it is torn down by adapters/dengjen_grpc's atexit
        # handler instead.
        pass

    @property
    def voice(self) -> str:
        return self.speech_options.voice.key

    @voice.setter
    def voice(self, new_voice: str):
        for voice in self.voices:
            if voice.key == new_voice:
                self.speech_options.set_voice(voice)
                return
        raise VoiceNotFoundError(
            f"A voice with the given key `{new_voice}` was not found"
        )

    @property
    def speaker(self) -> str:
        return self.speech_options.speaker or FALLBACK_SPEAKER_NAME

    @speaker.setter
    def speaker(self, new_speaker: str):
        if not self.speech_options.voice.is_multi_speaker:
            return
        if new_speaker == FALLBACK_SPEAKER_NAME:
            self.speech_options.speaker = self.speech_options.voice.speakers[0]
        elif new_speaker in self.speech_options.voice.speaker_names:
            self.speech_options.speaker = new_speaker
        else:
            raise SpeakerNotFoundError(f"Speaker `{new_speaker}` was not found")

    @property
    def language(self) -> str:
        return self.speech_options.voice.language

    @language.setter
    def language(self, new_language: str):
        lang = normalizeLanguage(new_language)
        if self.speech_options.voice.language == lang:
            return
        lang_code = lang.split("_")[0] + "_"
        possible_voices = []
        for voice in self.voices:
            if voice.language == lang:
                self.speech_options.set_voice(voice)
                return
            elif voice.language.startswith(lang_code):
                possible_voices.append(voice)
        if possible_voices:
            self.speech_options.set_voice(possible_voices[0])
            return
        raise VoiceNotFoundError(
            f"A voice with the given language `{new_language}` was not found"
        )

    @property
    def volume(self) -> float:
        if self.speech_options.volume is None:
            return DEFAULT_VOLUME
        return self.speech_options.volume

    @volume.setter
    def volume(self, new_volume: float):
        self.speech_options.volume = new_volume

    @property
    def rate(self) -> float:
        if self.speech_options.rate is None:
            return DEFAULT_RATE
        return self.speech_options.rate

    @rate.setter
    def rate(self, new_rate: float):
        self.speech_options.rate = new_rate

    @property
    def pitch(self) -> float:
        if self.speech_options.pitch is None:
            return DEFAULT_PITCH
        return self.speech_options.pitch

    @pitch.setter
    def pitch(self, new_pitch: float):
        self.speech_options.pitch = new_pitch

    def get_voices(self):
        return self.voices

    def get_speakers(self):
        if self.speech_options.voice.is_multi_speaker:
            return self.speech_options.voice.speaker_names
        else:
            return [FALLBACK_SPEAKER_NAME]

    @staticmethod
    def get_voice_variants(voice_key):
        std_key = voice_key.replace("+RT", "")
        lang, name, quality = std_key.split("-")
        rt_key = f"{lang}-{name}+RT-{quality}"
        return std_key, rt_key

    def create_speech_provider(self, text):
        return SpeechProvider(text, self.speech_options.copy())

    def create_break_provider(self, time_ms):
        return SilenceProvider(time_ms, self.speech_options.voice.sample_rate)

    @classmethod
    def load_piper_voices_from_nvda_config_dir(cls, backend):
        migrate_voices_directory()
        Path(DENGJEN_VOICES_DIR).mkdir(parents=True, exist_ok=True)
        return sorted(
            cls.load_voices_from_directory(DENGJEN_VOICES_DIR, backend),
            key=operator.attrgetter("key"),
        )

    @classmethod
    def load_voices_from_directory(
        cls, voices_directory, backend, *, directory_name_prefix="voice-"
    ):
        rv = []
        for directory in (d for d in Path(voices_directory).iterdir() if d.is_dir()):
            try:
                voice = DengjenVoice.from_path(directory, backend)
            except ValueError:
                continue
            rv.append(voice)
        return rv
