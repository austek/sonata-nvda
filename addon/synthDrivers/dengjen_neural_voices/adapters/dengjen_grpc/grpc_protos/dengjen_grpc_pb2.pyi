from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SynthesisMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODE_UNSPECIFIED: _ClassVar[SynthesisMode]
    MODE_LAZY: _ClassVar[SynthesisMode]
    MODE_PARALLEL: _ClassVar[SynthesisMode]
    MODE_BATCHED: _ClassVar[SynthesisMode]

class VoiceQuality(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    VOICE_QUALITY_UNSPECIFIED: _ClassVar[VoiceQuality]
    VOICE_QUALITY_X_LOW: _ClassVar[VoiceQuality]
    VOICE_QUALITY_LOW: _ClassVar[VoiceQuality]
    VOICE_QUALITY_MEDIUM: _ClassVar[VoiceQuality]
    VOICE_QUALITY_HIGH: _ClassVar[VoiceQuality]
MODE_UNSPECIFIED: SynthesisMode
MODE_LAZY: SynthesisMode
MODE_PARALLEL: SynthesisMode
MODE_BATCHED: SynthesisMode
VOICE_QUALITY_UNSPECIFIED: VoiceQuality
VOICE_QUALITY_X_LOW: VoiceQuality
VOICE_QUALITY_LOW: VoiceQuality
VOICE_QUALITY_MEDIUM: VoiceQuality
VOICE_QUALITY_HIGH: VoiceQuality

class Empty(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Version(_message.Message):
    __slots__ = ("version",)
    VERSION_FIELD_NUMBER: _ClassVar[int]
    version: str
    def __init__(self, version: _Optional[str] = ...) -> None: ...

class VoiceRef(_message.Message):
    __slots__ = ("voice_key",)
    VOICE_KEY_FIELD_NUMBER: _ClassVar[int]
    voice_key: str
    def __init__(self, voice_key: _Optional[str] = ...) -> None: ...

class VoiceDescriptor(_message.Message):
    __slots__ = ("voice_key", "synthesis_options", "speakers", "audio", "language", "quality", "supports_streaming_output")
    class SpeakersEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: int
        value: str
        def __init__(self, key: _Optional[int] = ..., value: _Optional[str] = ...) -> None: ...
    VOICE_KEY_FIELD_NUMBER: _ClassVar[int]
    SYNTHESIS_OPTIONS_FIELD_NUMBER: _ClassVar[int]
    SPEAKERS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_STREAMING_OUTPUT_FIELD_NUMBER: _ClassVar[int]
    voice_key: str
    synthesis_options: SynthesisSettings
    speakers: _containers.ScalarMap[int, str]
    audio: AudioFormat
    language: str
    quality: VoiceQuality
    supports_streaming_output: bool
    def __init__(self, voice_key: _Optional[str] = ..., synthesis_options: _Optional[_Union[SynthesisSettings, _Mapping]] = ..., speakers: _Optional[_Mapping[int, str]] = ..., audio: _Optional[_Union[AudioFormat, _Mapping]] = ..., language: _Optional[str] = ..., quality: _Optional[_Union[VoiceQuality, str]] = ..., supports_streaming_output: bool = ...) -> None: ...

class VoiceConfigLocation(_message.Message):
    __slots__ = ("path",)
    PATH_FIELD_NUMBER: _ClassVar[int]
    path: str
    def __init__(self, path: _Optional[str] = ...) -> None: ...

class SynthesisRequest(_message.Message):
    __slots__ = ("voice_key", "text", "prosody", "synthesis_mode")
    VOICE_KEY_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    PROSODY_FIELD_NUMBER: _ClassVar[int]
    SYNTHESIS_MODE_FIELD_NUMBER: _ClassVar[int]
    voice_key: str
    text: str
    prosody: ProsodyControls
    synthesis_mode: SynthesisMode
    def __init__(self, voice_key: _Optional[str] = ..., text: _Optional[str] = ..., prosody: _Optional[_Union[ProsodyControls, _Mapping]] = ..., synthesis_mode: _Optional[_Union[SynthesisMode, str]] = ...) -> None: ...

class SynthesisSettings(_message.Message):
    __slots__ = ("speaker", "length_scale", "noise_scale", "noise_w", "parameters")
    class ParametersEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    SPEAKER_FIELD_NUMBER: _ClassVar[int]
    LENGTH_SCALE_FIELD_NUMBER: _ClassVar[int]
    NOISE_SCALE_FIELD_NUMBER: _ClassVar[int]
    NOISE_W_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    speaker: str
    length_scale: float
    noise_scale: float
    noise_w: float
    parameters: _containers.ScalarMap[str, float]
    def __init__(self, speaker: _Optional[str] = ..., length_scale: _Optional[float] = ..., noise_scale: _Optional[float] = ..., noise_w: _Optional[float] = ..., parameters: _Optional[_Mapping[str, float]] = ...) -> None: ...

class VoiceSynthesisSettings(_message.Message):
    __slots__ = ("voice_key", "synthesis_options")
    VOICE_KEY_FIELD_NUMBER: _ClassVar[int]
    SYNTHESIS_OPTIONS_FIELD_NUMBER: _ClassVar[int]
    voice_key: str
    synthesis_options: SynthesisSettings
    def __init__(self, voice_key: _Optional[str] = ..., synthesis_options: _Optional[_Union[SynthesisSettings, _Mapping]] = ...) -> None: ...

class AudioFormat(_message.Message):
    __slots__ = ("sample_rate", "num_channels", "sample_width")
    SAMPLE_RATE_FIELD_NUMBER: _ClassVar[int]
    NUM_CHANNELS_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_WIDTH_FIELD_NUMBER: _ClassVar[int]
    sample_rate: int
    num_channels: int
    sample_width: int
    def __init__(self, sample_rate: _Optional[int] = ..., num_channels: _Optional[int] = ..., sample_width: _Optional[int] = ...) -> None: ...

class SynthesisChunk(_message.Message):
    __slots__ = ("audio_bytes", "real_time_factor")
    AUDIO_BYTES_FIELD_NUMBER: _ClassVar[int]
    REAL_TIME_FACTOR_FIELD_NUMBER: _ClassVar[int]
    audio_bytes: bytes
    real_time_factor: float
    def __init__(self, audio_bytes: _Optional[bytes] = ..., real_time_factor: _Optional[float] = ...) -> None: ...

class RealtimeAudioChunk(_message.Message):
    __slots__ = ("audio_bytes",)
    AUDIO_BYTES_FIELD_NUMBER: _ClassVar[int]
    audio_bytes: bytes
    def __init__(self, audio_bytes: _Optional[bytes] = ...) -> None: ...

class ProsodyControls(_message.Message):
    __slots__ = ("rate", "volume", "pitch", "appended_silence_ms")
    RATE_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    PITCH_FIELD_NUMBER: _ClassVar[int]
    APPENDED_SILENCE_MS_FIELD_NUMBER: _ClassVar[int]
    rate: int
    volume: int
    pitch: int
    appended_silence_ms: int
    def __init__(self, rate: _Optional[int] = ..., volume: _Optional[int] = ..., pitch: _Optional[int] = ..., appended_silence_ms: _Optional[int] = ...) -> None: ...
