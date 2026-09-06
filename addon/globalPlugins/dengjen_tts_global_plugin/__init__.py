# Copyright (c) 2023 Musharraf Omer
# This file is covered by the GNU General Public License.

import os
import sys

import addonHandler
import core
import globalPluginHandler
import gui
import wx
from logHandler import log

addonHandler.initTranslation()


_DIR = os.path.abspath(os.path.dirname(__file__))
_ADDON_ROOT = os.path.abspath(os.path.join(_DIR, os.pardir, os.pardir))
_TTS_MODULE_DIR = os.path.join(_ADDON_ROOT, "synthDrivers")
sys.path.insert(0, _TTS_MODULE_DIR)
try:
    from dengjen_neural_voices import (
        DENGJEN_VOICES_DIR,
        DengjenTextToSpeechSystem,
        aio,
        helpers,
        voice_migration,
    )
    from dengjen_neural_voices.adapters.dengjen_grpc import DengjenGrpcBackend
finally:
    sys.path.remove(_TTS_MODULE_DIR)
del _DIR, _ADDON_ROOT, _TTS_MODULE_DIR


__all__ = [
    "DENGJEN_VOICES_DIR",
    "DengjenTextToSpeechSystem",
    "DengjenGrpcBackend",
    "aio",
    "helpers",
    "voice_migration",
]

from .voice_manager import DengjenVoiceManagerDialog


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__voice_manager_shown = False
        self._voice_checker = lambda: wx.CallLater(3000, self._perform_voice_check)
        core.postNvdaStartup.register(self._voice_checker)
        self.itemHandle = gui.mainFrame.sysTrayIcon.menu.Append(
            wx.ID_ANY,
            _("Dengjen &voice manager..."),
            _("Open the voice manager to preview, install or download dengjen voices"),
        )
        gui.mainFrame.sysTrayIcon.menu.Bind(
            wx.EVT_MENU, self.on_manager, self.itemHandle
        )

    def on_manager(self, event):
        manager_dialog = DengjenVoiceManagerDialog()
        gui.runScriptModalDialog(manager_dialog)
        self.__voice_manager_shown = True

    def _perform_voice_check(self):
        if self.__voice_manager_shown:
            return
        if not any(
            DengjenTextToSpeechSystem.load_piper_voices_from_nvda_config_dir(
                DengjenGrpcBackend()
            )
        ):
            retval = gui.messageBox(
                _(
                    "No Dengjen voice was found.\n"
                    "You can preview and download voices from the voice manager.\n"
                    "Do you want to open the voice manager now?"
                ),
                _("Dengjen Neural Voices"),
                wx.YES_NO | wx.ICON_WARNING,
            )
            if retval == wx.YES:
                self.on_manager(None)

    def terminate(self):
        try:
            gui.mainFrame.sysTrayIcon.menu.DestroyItem(self.itemHandle)
        except Exception:
            log.debug("Failed to remove the Dengjen menu item", exc_info=True)
