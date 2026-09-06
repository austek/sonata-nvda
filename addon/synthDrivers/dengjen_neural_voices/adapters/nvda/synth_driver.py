import typing
from asyncio.exceptions import CancelledError as AsyncioCancelledError
from collections import OrderedDict
from contextlib import suppress

import addonHandler
import config
import languageHandler
import ui
from autoSettingsUtils.driverSetting import DriverSetting, NumericDriverSetting
from logHandler import log
from nvwave import WavePlayer
from speech import sayAll
from speech.commands import (
    BreakCommand,
    IndexCommand,
    LangChangeCommand,
    PitchCommand,
    RateCommand,
    VolumeCommand,
)
from synthDriverHandler import (
    SynthDriver as NvdaSynthDriver,
)
from synthDriverHandler import (
    VoiceInfo,
    synthDoneSpeaking,
    synthIndexReached,
)

from ... import aio
from ..._config import DengjenConfig
from ...aio import (
    CancelledError,
    asyncio,
    asyncio_cancel_task,
    asyncio_coroutine_to_concurrent_future,
    run_in_executor,
)
from ...domain.tts_system import (
    DengjenTextToSpeechSystem,
    SpeakerNotFoundError,
    SpeechOptions,
)
from ...helpers import update_displaied_params_on_voice_change
from ...ports.tts_backend import BackendUnavailableError

addonHandler.initTranslation()


def _bootstrap_backend():
    """Construct and start the production TTS backend.

    A module-level function (not inline in SynthDriver.__init__) so tests can
    replace it wholesale via monkeypatch -- NVDA constructs SynthDriver()
    with zero arguments, so the backend cannot be a constructor parameter.

    Imports DengjenGrpcBackend lazily: this keeps the vendored, Windows-only
    grpc dependency out of every code path that doesn't actually need to talk
    to the engine (in particular, out of every test that monkeypatches this
    function before SynthDriver() is ever constructed).
    """
    from ..dengjen_grpc import DengjenGrpcBackend

    aio.ensure_running()
    backend = DengjenGrpcBackend()
    backend.initialize()
    version = backend.check_version()
    log.info(f"Dengjen GRPC server version: {version}")
    return backend


class DoneSpeakingTask:
    __slots__ = ["on_index_reached", "player"]

    def __init__(self, player, on_index_reached):
        self.player = player
        self.on_index_reached = on_index_reached

    async def __call__(self):
        await run_in_executor(self.player.idle)
        await run_in_executor(self.on_index_reached, None)


class IndexReachedTask:
    __slots__ = ["callback", "index_list"]

    def __init__(self, callback, index_list):
        self.callback = callback
        self.index_list = index_list

    async def __call__(self):
        for index in self.index_list:
            await run_in_executor(self.callback, index)


class SpeechTask:
    __slots__ = ["player", "task"]

    def __init__(self, task, player):
        self.task = task
        self.player = player

    async def __call__(self):
        if sayAll.SayAllHandler.isRunning():
            self.task.text = self.task.text.replace("\n", " ")
            self.task.speech_options.sentence_silence_ms = 50
        speech_stream = self.task.generate_audio()
        feed_func = self.player.feed
        async for wave_samples in speech_stream:
            await run_in_executor(feed_func, wave_samples)
        self.player.sync()


class BreakTask:
    __slots__ = ["player", "task"]

    def __init__(self, task, player):
        self.task = task
        self.player = player

    async def __call__(self):
        await run_in_executor(self.player.feed, self.task.generate_audio())
        await run_in_executor(self.player.sync)


def speaker_setting():
    """Factory function for creating speaker setting."""
    return DriverSetting(
        "speaker",
        _("&Speaker"),
        availableInSettingsRing=True,
        displayName=_("Speaker"),
    )


def create_wave_player(sample_rate):
    return WavePlayer(channels=1, samplesPerSec=sample_rate, bitsPerSample=16)


async def _process_speech_sequence(speech_seq):
    for callable in speech_seq:
        try:
            await callable()
        except (AsyncioCancelledError, CancelledError):
            log.debug(f"Canceled speech task {callable}", exc_info=True)
            break
        except Exception:
            log.exception(f"Failed to execute speech task {callable}", exc_info=True)
            break


@asyncio_coroutine_to_concurrent_future
async def process_speech(speech_seq):
    speech_task = _process_speech_sequence(speech_seq)
    return asyncio.get_running_loop().create_task(speech_task)


class SynthDriver(NvdaSynthDriver):
    supportedSettings = (
        NvdaSynthDriver.VoiceSetting(),
        NvdaSynthDriver.VariantSetting(),
        speaker_setting(),
        NvdaSynthDriver.RateSetting(),
        NvdaSynthDriver.RateBoostSetting(),
        NvdaSynthDriver.VolumeSetting(),
        NvdaSynthDriver.PitchSetting(),
        NumericDriverSetting("noise_scale", _("&Noise scale"), False),
        NumericDriverSetting("length_scale", _("&Length scale"), True),
        NumericDriverSetting("noise_w", _("Noise &w"), False),
    )
    supportedCommands: typing.ClassVar = {
        IndexCommand,
        LangChangeCommand,
        BreakCommand,
        RateCommand,
        VolumeCommand,
        PitchCommand,
    }
    supportedNotifications: typing.ClassVar = {synthIndexReached, synthDoneSpeaking}

    description = "Dengjen Neural Voices"
    name = "dengjen_neural_voices"
    cachePropertiesByDefault = False

    @classmethod
    def check(cls):
        return True

    def __init__(self):
        super().__init__()

        self._current_task = None
        self._rateBoost = False
        self.tts = None
        self._player = None
        self._players = {}
        self._noise_scale_factor = None
        self._length_scale_factor = None
        self._noise_w_factor = None
        try:
            backend = _bootstrap_backend()
        except BackendUnavailableError:
            log.exception(
                "Failed to initialize Dengjen services. Synthesizer will not be available.",
                exc_info=True,
            )
            return
        except Exception:
            log.exception(
                "Unexpected error initializing Dengjen services. Synthesizer will not be available.",
                exc_info=True,
            )
            return
        voices = DengjenTextToSpeechSystem.load_piper_voices_from_nvda_config_dir(
            backend
        )
        if not any(voices):
            log.error(
                "No installed voices were found for Dengjen. Synthesizer will not be available."
            )
            return
        self.voices = voices
        try:
            voice_key = config.conf["speech"]["dengjen_neural_voices"]["voice"]
            configured_voice = next(
                filter(lambda v: v.key.startswith(voice_key), self.voices)
            )
        except (KeyError, StopIteration):
            configured_voice = self.voices[0]
        init_speech_options = SpeechOptions(voice=configured_voice)
        self.tts = DengjenTextToSpeechSystem(
            self.voices, speech_options=init_speech_options
        )
        self._player = self._get_or_create_player(
            self.tts.speech_options.voice.sample_rate
        )
        self.availableLanguages = {v.language for v in self.voices}
        self._voice_map = {v.key: v for v in self.voices}
        self._standard_voice_map = {v.standard_variant_key: v for v in self.voices}
        self.availableVoices = self._get_valid_voices()
        self.__voice = None

    def terminate(self):
        self.cancel()
        if self.tts is not None:
            self.tts.shutdown()
        for player in self._players.values():
            player.close()
        self._players.clear()
        self._player = None

    def speak(self, speechSequence):
        with self.tts.create_synthesis_context():
            self._fast_prepare_and_run_speech_task(speechSequence)

    def _fast_prepare_and_run_speech_task(self, speech_sequence):
        self.cancel()
        self._current_task = process_speech(
            self._build_speech_tasks(speech_sequence)
        ).result()

    def _build_speech_tasks(self, speech_sequence):
        speech_seq = []
        text_list = []
        index_command_list = []
        default_lang = self.tts.language
        for item in speech_sequence:
            item_type = type(item)
            if item_type is IndexCommand:
                index_command_list.append(item.index)
                continue
            if item_type is str:
                text_list.append(item)
                continue

            if any(text_list):
                speech_seq.append(self._create_speech_task(text_list))
                text_list.clear()
            break_task = self._apply_speech_command(item, default_lang)
            if break_task is not None:
                speech_seq.append(break_task)
        if any(text_list):
            speech_seq.append(self._create_speech_task(text_list))
        if any(index_command_list):
            speech_seq.append(
                IndexReachedTask(self._on_index_reached, index_command_list)
            )
        speech_seq.append(DoneSpeakingTask(self._player, self._on_index_reached))
        return speech_seq

    def _create_speech_task(self, text_list):
        return SpeechTask(
            self.tts.create_speech_provider("\n".join(text_list)),
            self._player,
        )

    def _apply_speech_command(self, item, default_lang):
        item_type = type(item)
        if item_type is BreakCommand:
            return BreakTask(
                self.tts.create_break_provider(item.time),
                self._player,
            )
        if item_type is LangChangeCommand:
            self.tts.language = default_lang if item.isDefault else item.lang
        elif item_type is RateCommand:
            self.tts.rate = item.newValue
        elif item_type is VolumeCommand:
            self.tts.volume = item.newValue
        elif item_type is PitchCommand:
            self.tts.pitch = item.newValue
        return None

    def cancel(self):
        if self._current_task is not None:
            asyncio_cancel_task(self._current_task)
        if self._player is not None:
            self._player.stop()

    def pause(self, switch):
        if self._player is not None:
            self._player.pause(switch)

    def _on_index_reached(self, index):
        if index is not None:
            synthIndexReached.notify(synth=self, index=index)
        else:
            synthDoneSpeaking.notify(synth=self)

    def _get_or_create_player(self, sample_rate):
        if sample_rate not in self._players:
            self._players[sample_rate] = create_wave_player(sample_rate)
        return self._players[sample_rate]

    def _get_rateBoost(self):
        return self._rateBoost

    def _set_rateBoost(self, enable):
        if enable != self._rateBoost:
            rate = self.rate
            self._rateBoost = enable
            self.rate = rate

    def _get_rate(self):
        if self._rateBoost:
            return self.tts.rate
        else:
            self.tts.rate = min(40, self.tts.rate)
            return int(self.tts.rate * 2.5)

    def _set_rate(self, value):
        if self._rateBoost:
            self.tts.rate = value
        else:
            self.tts.rate = int(self._percentToParam(value, 0, 40))

    def _get_volume(self):
        return self.tts.volume

    def _set_volume(self, value):
        self.tts.volume = value
        self._player.setVolume(all=value / 100)

    def _get_pitch(self):
        return self.tts.pitch

    def _set_pitch(self, value):
        self.tts.pitch = value

    def _get_voice(self):
        return self._get_variant_independent_voice_id(self.tts.voice)

    _SCALE_SETTINGS: typing.ClassVar = {
        "noise_scale": {
            "factor_attr": "_noise_scale_factor",
            "multiplier": 3,
            "skip_if_unchanged": False,
        },
        "length_scale": {
            "factor_attr": "_length_scale_factor",
            "multiplier": 2,
            "skip_if_unchanged": False,
        },
        "noise_w": {
            "factor_attr": "_noise_w_factor",
            "multiplier": 3,
            "skip_if_unchanged": True,
        },
    }

    def _get_scale_factor(self, name):
        factor_attr = self._SCALE_SETTINGS[name]["factor_attr"]
        factor = getattr(self, factor_attr, None)
        if factor is not None:
            return factor
        if self.voice in DengjenConfig:
            factor = DengjenConfig[self.voice].get(name, 50)
            setattr(self, factor_attr, factor)
            return factor
        return 50

    def _set_scale_factor(self, name, value, force=False):
        spec = self._SCALE_SETTINGS[name]
        factor_attr = spec["factor_attr"]
        if (
            not force
            and spec["skip_if_unchanged"]
            and getattr(self, factor_attr, None) == value
        ):
            return
        voice = self.tts.speech_options.voice
        default = getattr(voice.default_scales, name)
        if value == 50:
            setattr(voice, name, default)
        else:
            setattr(
                voice,
                name,
                max(
                    0.1,
                    round(
                        self._percentToParam(value, 0.0, default * spec["multiplier"]),
                        2,
                    ),
                ),
            )
        setattr(self, factor_attr, value)

    def _reapply_scale_settings(self):

        for name in self._SCALE_SETTINGS:
            self._set_scale_factor(name, self._get_scale_factor(name), force=True)

    def _get_noise_scale(self):
        return self._get_scale_factor("noise_scale")

    def _set_noise_scale(self, value):
        self._set_scale_factor("noise_scale", value)

    def _get_length_scale(self):
        return self._get_scale_factor("length_scale")

    def _set_length_scale(self, value):
        self._set_scale_factor("length_scale", value)

    def _get_noise_w(self):
        return self._get_scale_factor("noise_w")

    def _set_noise_w(self, value):
        self._set_scale_factor("noise_w", value)

    def _set_voice(self, value):
        if value not in self.availableVoices:
            value = next(iter(self.availableVoices))
        try:
            self.tts.voice = self._standard_voice_map[value].key
        except Exception:
            log.exception(f"Failed to load voice `{value}`")
            ui.message(
                _("Failed to load voice {voice}. Keeping the previous voice.").format(
                    voice=self.availableVoices[value].displayName
                )
            )
            return
        self.__voice = value
        with suppress(AttributeError):
            del self._availableVariants
        with suppress(AttributeError):
            del self._availableSpeakers
        if value in DengjenConfig:
            variant = DengjenConfig[value].get("variant", self.variant)
            speaker = DengjenConfig[value].get("speaker")
        else:
            variant = self._standard_voice_map[value].variant
            speaker = None
        self._set_variant(variant)

        if speaker is not None:
            self._set_speaker(speaker)
        try:
            update_displaied_params_on_voice_change(self)
        except Exception:
            log.exception("Failed to update Speech GUI", exc_info=True)

    def _get_language(self):
        return self.tts.language

    def _set_language(self, value):
        self.tts.language = value

    def _get_variant(self):
        return self.tts.speech_options.voice.variant

    def _set_variant(self, value):
        variant = value.lower()
        if variant == "standard":
            voice_key = self.tts.speech_options.voice.standard_variant_key
        elif variant == "fast":
            voice_key = self.tts.speech_options.voice.fast_variant_key
        else:
            log.info(f"Unknown voice variant: {variant}")
            return
        if voice_key not in self._voice_map:
            return
        prev_speaker = self.tts.speech_options.voice.speaker
        self.tts.voice = voice_key
        self.tts.speech_options.voice.speaker = prev_speaker
        DengjenConfig.setdefault(self.voice, {})["variant"] = value
        voice = self.tts.speech_options.voice
        self._player = self._get_or_create_player(voice.sample_rate)

        self._reapply_scale_settings()

    def _getAvailableVariants(self):
        std_key, rt_key = DengjenTextToSpeechSystem.get_voice_variants(self.__voice)
        rv = OrderedDict()
        if std_key in self._voice_map:
            rv["standard"] = VoiceInfo("standard", "Standard", self.language)
        if rt_key in self._voice_map:
            rv["fast"] = VoiceInfo("fast", "Fast", self.language)
        return rv

    def _get_variant_independent_voice_id(self, voice_key):
        return DengjenTextToSpeechSystem.get_voice_variants(voice_key)[0]

    def _get_valid_voices(self):
        all_voices = OrderedDict()
        for voice in self.voices:
            voice_id = self._get_variant_independent_voice_id(voice.key)
            quality = voice.properties["quality"]
            lang = languageHandler.normalizeLanguage(voice.language).replace("_", "-")
            display_name = f"{voice.name} ({lang}) - {quality}"
            all_voices[voice_id] = VoiceInfo(voice_id, display_name, voice.language)
        return all_voices

    def _get_speaker(self):
        return self.tts.speaker

    def _set_speaker(self, value):
        try:
            self.tts.speaker = value
            DengjenConfig.setdefault(self.voice, {})["speaker"] = value
        except SpeakerNotFoundError:
            DengjenConfig.setdefault(self.voice, {})["speaker"] = self.tts.speaker

    def _get_availableSpeakers(self):
        return {spk: VoiceInfo(spk, spk, None) for spk in self.tts.get_speakers()}
