"""Preview and download dengjen voices."""

import functools
import operator
import os
import shutil
import tempfile
import threading
import winsound

import addonHandler
import gui
import synthDriverHandler
import wx
from logHandler import log
from wx.adv import CommandLinkButton

addonHandler.initTranslation()

from . import (
    DENGJEN_VOICES_DIR,
    DengjenTextToSpeechSystem,
    DengjenGrpcBackend,
    aio,
    helpers,
    voice_download,
    voice_migration,
)
from . import voice_manager_logic as logic
from .components import (
    AsyncSnakDialog,
    ColumnDefn,
    ImmutableObjectListView,
    SimpleDialog,
    make_sized_static_box,
)
from .sized_controls import SizedPanel

with helpers.import_bundled_library():
    import miniaudio


class InstalledDengjenVoicesPanel(SizedPanel):
    def __init__(self, parent):
        super().__init__(parent, -1)
        self.__already_populated = threading.Event()

        wx.StaticText(self, -1, _("Installed voices"))
        self.voices_list = ImmutableObjectListView(
            self,
            -1,
            columns=[
                ColumnDefn(_("Name"), "left", 30, self._get_installed_voice_name),
                ColumnDefn(
                    _("Quality"),
                    "center",
                    30,
                    lambda v: v.properties["quality"].title(),
                ),
                ColumnDefn(_("Language"), "right", 20, operator.attrgetter("language")),
            ],
        )
        self.buttons_panel = SizedPanel(self, -1)
        self.buttons_panel.SetSizerType("horizontal")

        self.model_card_button = wx.Button(
            self.buttons_panel, -1, _("&Voice model card...")
        )

        self.remove_voice_button = wx.Button(
            self.buttons_panel, -1, _("&Remove voice...")
        )
        add_voice_button = CommandLinkButton(
            self,
            -1,
            _("&Install from local file"),
            _(
                "Install a voice from a local archive.\n"
                "The archive contains the voice model and configuration.\n"
                "The archive should have a (.tar.gz) file extension."
            ),
        )
        self.import_voices_button = CommandLinkButton(
            self,
            -1,
            _("Import voices from &Sonata"),
            _(
                "Copy voices you downloaded with the Sonata Neural Voices add-on.\n"
                "The originals are left in place, so Sonata keeps working."
            ),
        )
        self.import_voices_button.Hide()
        self.Bind(wx.EVT_BUTTON, self.on_model_card, self.model_card_button)
        self.Bind(wx.EVT_BUTTON, self.on_remove_voice, self.remove_voice_button)
        self.Bind(wx.EVT_BUTTON, self._on_install_voice_from_tar, add_voice_button)
        self.Bind(wx.EVT_BUTTON, self._on_import_voices, self.import_voices_button)

    def update_voices_list(self, set_focus=False, invalidate_synth_voices_cache=False):
        voices = list(
            DengjenTextToSpeechSystem.load_piper_voices_from_nvda_config_dir(
                DengjenGrpcBackend()
            )
        )
        state = logic.installed_list_state(voices, synthDriverHandler.getSynth().name)
        self.import_voices_button.Show(bool(voice_migration.importable_voice_keys()))
        self.Layout()
        self.buttons_panel.Enable(state.buttons_enabled)
        self.voices_list.set_objects(voices, set_focus=set_focus)
        if state.is_dengjen_synth:
            self.remove_voice_button.Enable(state.remove_enabled)
            if invalidate_synth_voices_cache:
                synth = synthDriverHandler.getSynth()
                synth.terminate()
                synth.__init__()

    def populate_list(self):
        if self.__already_populated.is_set():
            return
        self.update_voices_list()
        self.__already_populated.set()

    def invalidate_cache(self):
        self.__already_populated.clear()

    def _get_installed_voice_name(self, voice):
        return logic.installed_voice_display_name(voice)

    def on_model_card(self, event):
        selected = self.voices_list.get_selected()
        if selected is None:
            self.voices_list.set_focused_item(0)
            return
        model_card_file = os.path.join(selected.location, "MODEL_CARD")
        if os.path.exists(model_card_file):
            with open(model_card_file, "r", encoding="utf-8") as file:
                content = file.read()
            content = logic.sanitize_model_card(content)
            gui.messageBox(
                content,
                _("Model card"),
                style=wx.ICON_INFORMATION,
            )
        else:
            gui.messageBox(
                _("Model card information is unavailable for this voice"),
                _("Not found"),
                style=wx.ICON_WARNING,
            )

    def on_remove_voice(self, event):
        selected = self.voices_list.get_selected()
        if selected is None:
            self.voices_list.set_focused_item(0)
            return
        synth = synthDriverHandler.getSynth()
        if logic.is_active_voice(
            synth_name=synth.name, synth_voice=synth.voice, voice_key=selected.key
        ):
            gui.messageBox(
                _("You cannot remove the currently active voice!"),
                _("Error"),
                style=wx.ICON_ERROR,
            )
            return
        retval = gui.messageBox(
            _("Do you want to remove this voice?\nVoice: {name}").format(
                name=selected.key
            ),
            _("Remove voice?"),
            style=wx.YES_NO | wx.ICON_WARNING,
        )
        if retval == wx.YES:
            try:
                shutil.rmtree(selected.location)
            except Exception:
                log.exception("Failed to remove voice directory", exc_info=True)
                gui.messageBox(
                    _("Failed to remove voice.\nSee NVDA's log for more details."),
                    _("Failed"),
                    style=wx.ICON_WARNING,
                )
            else:
                gui.messageBox(
                    _("Voice removed successfully."),
                    _("Done"),
                    style=wx.ICON_INFORMATION,
                )
                self.update_voices_list(
                    set_focus=True, invalidate_synth_voices_cache=True
                )

    def _on_import_voices(self, event):
        voice_keys = voice_migration.importable_voice_keys()
        if not voice_keys:
            self.update_voices_list()
            return
        retval = gui.messageBox(
            _(
                "Copy the following voices from the Sonata Neural Voices add-on?\n"
                "{voices}\n\n"
                "The originals are left in place, so Sonata keeps working. This "
                "needs as much free disk space as the voices take up."
            ).format(voices="\n".join(voice_keys)),
            _("Import voices from Sonata?"),
            style=wx.YES_NO | wx.ICON_QUESTION,
        )
        if retval != wx.YES:
            return
        try:
            copied = voice_migration.copy_voices_from_old_dir()
        except OSError as exc:
            log.exception("Failed to import voices from the Sonata add-on")
            gui.messageBox(
                _(
                    "Failed to import voices.\n\n{detail}\n\n"
                    "See NVDA's log for more details."
                ).format(detail=str(exc) or type(exc).__name__),
                _("Import failed"),
                style=wx.ICON_ERROR,
                parent=gui.mainFrame,
            )
        else:
            gui.messageBox(
                _("The following voices were imported:\n{voices}").format(
                    voices="\n".join(copied)
                ),
                _("Voices imported"),
                style=wx.ICON_INFORMATION,
            )
        self.update_voices_list(set_focus=True, invalidate_synth_voices_cache=True)

    def _on_install_voice_from_tar(self, event):
        open_file_dialog = wx.FileDialog(
            parent=gui.mainFrame,
            message=_("Choose voice archive file "),
            defaultDir=wx.GetUserHome(),
            wildcard=(
                _("Tar archives (*.tar.gz, *.tgz)")
                + "|*.tar.gz;*.tgz|"
                + _("All files")
                + "|*.*"
            ),
            style=wx.FD_OPEN,
        )
        gui.runScriptModalDialog(
            open_file_dialog,
            functools.partial(self._get_process_tar_archive, open_file_dialog),
        )

    def _get_process_tar_archive(self, dialog, res):
        if res != wx.ID_OK:
            return
        filepath = dialog.GetPath().strip()
        if not filepath:
            return
        try:
            voice_key = voice_download.install_voice_from_tar_archive(
                filepath, DENGJEN_VOICES_DIR
            )
        except Exception as exc:
            log.error("Failed to install voice from archive", exc_info=True)
            gui.messageBox(
                _(
                    "Failed to install voice from archive.\n\n{detail}\n\n"
                    "See NVDA's log for more details."
                ).format(detail=str(exc) or type(exc).__name__),
                _("Voice installation failed"),
                style=wx.ICON_ERROR,
                parent=gui.mainFrame,
            )
        else:
            gui.messageBox(
                _("Voice {voice} has been installed successfully.").format(
                    voice=voice_key
                ),
                _("Voice installed successfully"),
                style=wx.ICON_INFORMATION,
            )
            self.update_voices_list(set_focus=True, invalidate_synth_voices_cache=True)


class OnlineDengjenVoicesPanel(SizedPanel):
    def __init__(self, parent):
        super().__init__(parent, -1)
        self.__already_populated = threading.Event()
        self.languages = []
        self.lang_to_voices = {}

        self._preview_label = _("&Preview")

        wx.StaticText(self, -1, _("Language"))
        self.language_choice = wx.Choice(self, -1, choices=[])
        wx.StaticText(self, -1, _("Available voices"))
        voice_list_columns = [
            ColumnDefn(_("Name"), "left", 30, operator.attrgetter("name")),
            ColumnDefn(_("Quality"), "center", 30, operator.attrgetter("quality")),
        ]
        self.voices_list = ImmutableObjectListView(self, -1, columns=voice_list_columns)
        self.voices_list.SetSizerProps(expand=True)
        self.buttons_panel = SizedPanel(self, -1)
        self.buttons_panel.SetSizerType("vertical")

        preview_box = make_sized_static_box(self.buttons_panel, _("Preview"))
        preview_box.SetSizerType("horizontal")
        wx.StaticText(preview_box, -1, _("Speaker"))
        self.speaker_choice = wx.Choice(preview_box, -1, choices=[])
        self.preview_btn = wx.Button(preview_box, -1, self._preview_label)
        self._preview_active = False
        dl_buttons_panel = SizedPanel(self.buttons_panel, -1)
        dl_buttons_panel.SetSizerType("horizontal")

        self.download_std_btn = wx.Button(
            dl_buttons_panel, -1, _("&Download standard variant")
        )

        self.download_rt_btn = wx.Button(
            dl_buttons_panel, -1, _("Download &fast variant")
        )

        refresh_list_btn = wx.Button(self, -1, _("&Refresh voices list"))
        self.Bind(
            wx.EVT_CHOICE, self.on_language_selection_change, self.language_choice
        )
        self.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_voice_selected, self.voices_list)
        self.Bind(wx.EVT_BUTTON, self.on_preview, self.preview_btn)
        self.Bind(wx.EVT_BUTTON, self.on_download, self.download_std_btn)
        self.Bind(wx.EVT_BUTTON, self.on_download_rt, self.download_rt_btn)
        self.Bind(
            wx.EVT_BUTTON,
            lambda e: self.populate_list(force_online=True),
            refresh_list_btn,
        )
        self.speaker_choice.Enable(False)
        self.buttons_panel.Enable(False)

    def populate_list(self, force_online=False):
        if not force_online and self.__already_populated.is_set():
            return
        AsyncSnakDialog(
            executor=voice_download.THREAD_POOL_EXECUTOR,
            func=functools.partial(
                voice_download.get_available_voices, force_online=force_online
            ),
            done_callback=self._voice_list_retrieved_callback,
            parent=self,
            message=_("Retrieving voices list. Please wait..."),
            dismiss_callback=lambda: True,
        )

    def _voice_list_retrieved_callback(self, future):
        try:
            result = future.result()
        except Exception:
            log.exception("Failed to retreive voices list", exc_info=True)
            wx.CallAfter(
                gui.messageBox,
                _(
                    "Could not retrieve voice list.\nPlease check your connection and try again."
                ),
                _("Error"),
                style=wx.ICON_ERROR,
                parent=gui.mainFrame,
            )
            return
        wx.CallAfter(self.set_voices, result)

    def invalidate_cache(self):
        self.__already_populated.clear()

    def on_language_selection_change(self, event):
        self.voices_list.Enable(True)
        selected_lang = self.languages[event.GetSelection()]
        voices = self.lang_to_voices[selected_lang]
        self.voices_list.set_objects(voices, set_focus=False)
        self.voices_list.EnsureVisible(0)
        self.voices_list.Select(0)
        self.voices_list.SetItemState(0, wx.LIST_STATE_FOCUSED, wx.LIST_STATE_FOCUSED)
        self.buttons_panel.Enable(True)
        self.speaker_choice.SetItems([])
        self.speaker_choice.Enable(False)

    def on_voice_selected(self, event):
        self.speaker_choice.SetItems([])
        selected_voice = self.voices_list.get_selected()
        if selected_voice is None:
            return
        state = logic.download_button_state(selected_voice)
        self.download_std_btn.Enable(state.std_enabled)
        self.download_rt_btn.Enable(state.rt_enabled)
        self.speaker_choice.Enable(state.speaker_enabled)
        if state.speaker_enabled:
            self.speaker_choice.SetItems(list(state.speakers))
            self.speaker_choice.SetSelection(0)

    def on_preview(self, event):

        if self._preview_active:
            winsound.PlaySound(None, winsound.SND_PURGE)
            return
        selected_voice = self.voices_list.get_selected()
        if selected_voice is None:
            return
        speaker_idx = 0
        if selected_voice.num_speakers > 1:
            speaker_idx = self.speaker_choice.GetSelection()
        mp3url = selected_voice.get_preview_url(speaker_idx=speaker_idx)
        self._preview_active = True

        self.preview_btn.SetLabel(_("&Stop preview"))
        future = aio.call_threaded(play_remote_mp3)(mp3url)
        if future is None:
            self._preview_active = False
            self.preview_btn.SetLabel(self._preview_label)
            return
        future.add_done_callback(lambda f: wx.CallAfter(self._on_preview_done, f))

    def _on_preview_done(self, future):
        self._preview_active = False
        self.preview_btn.SetLabel(self._preview_label)
        exc = future.exception()
        if exc is not None:
            log.exception(
                "Voice preview failed", exc_info=(type(exc), exc, exc.__traceback__)
            )
            gui.messageBox(
                _(
                    "Could not play voice preview.\nCheck your connection and try again."
                ),
                _("Preview failed"),
                style=wx.ICON_ERROR,
                parent=gui.mainFrame,
            )

    def on_download(self, event):

        def success_callback():
            self.Parent._invalidate_pages_voice_cache()
            wx.CallAfter(self.populate_list)
            wx.CallAfter(self.voices_list.SetFocus)

        selected_voice = self.voices_list.get_selected()
        if selected_voice is not None:
            downloader = voice_download.PiperVoiceDownloader(
                selected_voice, success_callback=success_callback
            )
            downloader.download()

    def on_download_rt(self, event):

        def success_callback():
            self.Parent._invalidate_pages_voice_cache()
            wx.CallAfter(self.populate_list)
            wx.CallAfter(self.voices_list.SetFocus)

        selected_voice = self.voices_list.get_selected()
        if selected_voice is None:
            return
        downloader = voice_download.PiperRTVoiceDownloader(
            selected_voice, success_callback=success_callback
        )
        downloader.download()

    def set_voices(self, voices):
        self.languages, self.lang_to_voices = logic.group_voices_by_language(voices)
        self.language_choice.SetItems([lang.description for lang in self.languages])
        self.__already_populated.set()


class DengjenVoiceManagerDialog(SimpleDialog):
    def __init__(self):
        super().__init__(
            gui.mainFrame,
            title=_("Dengjen voice manager"),
        )
        self.SetSize((500, -1))
        self.CenterOnScreen()

    def addControls(self, parent):
        self.notebookCtrl = wx.Notebook(parent, -1)
        self.notebookCtrl.SetSizerProps(expand=True)
        panel_info = [
            (
                _("Installed"),
                InstalledDengjenVoicesPanel(self.notebookCtrl),
            ),
            (
                _("Download"),
                OnlineDengjenVoicesPanel(self.notebookCtrl),
            ),
        ]
        for label, panel in panel_info:
            panel.SetSizerType("vertical")
            self.notebookCtrl.AddPage(panel, label)
        self.Bind(
            wx.EVT_NOTEBOOK_PAGE_CHANGED, self.onNotebookPageChanged, self.notebookCtrl
        )
        self.notebookCtrl._invalidate_pages_voice_cache = (
            self._invalidate_pages_voice_cache
        )
        self.notebookCtrl.GetCurrentPage().populate_list()

    def getButtons(self, parent):
        btnsizer = wx.StdDialogButtonSizer()

        cancel_btn = wx.Button(self, wx.ID_CANCEL, _("&Close"))
        btnsizer.AddButton(cancel_btn)
        btnsizer.Realize()
        return btnsizer

    def onNotebookPageChanged(self, event):
        selected_page = self.notebookCtrl.GetPage(event.GetSelection())
        selected_page.populate_list()

    def _invalidate_pages_voice_cache(self):
        for i in range(self.notebookCtrl.GetPageCount()):
            panel = self.notebookCtrl.GetPage(i)
            panel.invalidate_cache()


def play_remote_mp3(mp3_url):
    resp = voice_download.request.get(mp3_url)
    resp.raise_for_status()
    decoded_file = miniaudio.decode(resp.body, nchannels=1, sample_rate=22050)
    with tempfile.TemporaryDirectory() as tempdir:
        wav_file = os.path.join(tempdir, "speaker_0.wav")
        miniaudio.wav_write_file(wav_file, decoded_file)
        winsound.PlaySound(wav_file, winsound.SND_FILENAME | winsound.SND_PURGE)
