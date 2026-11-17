"""Provider switching in the Windows settings dialog, without cloud or tray."""
import tempfile
from pathlib import Path
from tkinter import ttk

from gui_preview import ARTIFACTS, FakeMonitor
from token_plan_monitor.models import Credentials, Settings
from token_plan_monitor.providers import get_provider
from token_plan_monitor.storage import Storage
from token_plan_monitor.ui import MonitorUI


class MemoryKeyring:
    def __init__(self):
        self.values = {}
        self.writes = 0

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[service, username] = value
        self.writes += 1


class Monitor(FakeMonitor):
    def __init__(self):
        super().__init__()
        self.updates = []

    def update_configuration(self, settings, credentials):
        self.updates.append((settings, credentials))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def pump(app, ms=80):
    app.root.after(ms, app.root.quit)
    app.root.mainloop()


def button(window, text):
    return next(w for w in descendants(window)
                if isinstance(w, ttk.Button) and w.cget("text") == text)


def main():
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
        monitor = Monitor()
        storage = Storage(Path(temporary), MemoryKeyring())
        storage.save_credentials(Credentials("volc-ak", "volc-sk"), "volcengine")
        storage.save_credentials(Credentials("bailian-ak", "bailian-sk"), "bailian")
        storage.save_settings(Settings(provider="volcengine", region="cn-beijing"))
        app = MonitorUI(monitor, storage)
        try:
            app._create_popup()
            app._create_desktop_widget()

            # The initial form shows the active provider's own vault pair;
            # the secret field is never prefilled.
            app.open_settings()
            pump(app)
            dialog = app._settings_dialog
            assert dialog.provider == "volcengine"
            assert app._settings_entries["access_key"].get() == "volc-ak"
            assert app._settings_entries["secret_key"].get() == ""

            # Switching providers carries interval/alert values, falls back an
            # unsupported region, and swaps in the new provider's credentials.
            app._settings_entries["region"].delete(0, "end")
            app._settings_entries["region"].insert(0, "us-east-1")
            app._settings_entries["refresh"].delete(0, "end")
            app._settings_entries["refresh"].insert(0, "30")
            app._settings_alert_reset.set(True)
            dialog.provider_var.set(get_provider("bailian").label)
            dialog._schedule_rebuild()
            pump(app)
            assert dialog.provider == "bailian"
            assert app._settings_entries["access_key"].get() == "bailian-ak"
            assert app._settings_entries["region"].get() == "cn-beijing"
            assert app._settings_entries["refresh"].get() == "30"
            assert app._settings_alert_reset.get() is True
            assert dialog.existing is not None
            assert dialog.existing.access_key_id == "bailian-ak"

            # Cancelling after a switch writes nothing at all.
            button(app.settings_window, "取消").invoke()
            pump(app)
            assert app.settings_window is None
            assert monitor.updates == []
            assert storage.load_settings().provider == "volcengine"
            assert storage.load_credentials("volcengine").secret_access_key == "volc-sk"
            assert storage.load_credentials("bailian").secret_access_key == "bailian-sk"

            # Reopen still shows the stored provider; a saved switch reuses the
            # new provider's existing secret when the field stays blank.
            app.open_settings()
            pump(app)
            dialog = app._settings_dialog
            assert dialog.provider == "volcengine"
            dialog.provider_var.set(get_provider("bailian").label)
            dialog._schedule_rebuild()
            pump(app)
            assert dialog.provider == "bailian"
            app._settings_entries["access_key"].delete(0, "end")
            app._settings_entries["access_key"].insert(0, "bailian-ak-new")
            keyring_writes = storage._keyring.writes
            button(app.settings_window, "保存设置").invoke()
            pump(app)
            assert app.settings_window is None
            assert len(monitor.updates) == 1
            new_settings, new_credentials = monitor.updates[0]
            assert new_settings.provider == "bailian"
            assert new_settings.region == "cn-beijing"
            assert new_settings.refresh_minutes == 15
            assert new_settings.alert.notify_on_reset is False
            assert new_credentials.access_key_id == "bailian-ak-new"
            assert new_credentials.secret_access_key == "bailian-sk"
            assert storage.load_settings().provider == "bailian"
            # Only the bailian pair changed (one vault write per entry, SK
            # first); the volcengine pair is never touched by the switch.
            assert storage._keyring.writes == keyring_writes + 2
            assert storage.load_credentials("bailian").secret_access_key == "bailian-sk"
            volc = storage.load_credentials("volcengine")
            assert volc.access_key_id == "volc-ak" and volc.secret_access_key == "volc-sk"

            # Switching back surfaces the previous provider's pair again.
            app.open_settings()
            pump(app)
            dialog = app._settings_dialog
            dialog.provider_var.set(get_provider("volcengine").label)
            dialog._schedule_rebuild()
            pump(app)
            assert dialog.provider == "volcengine"
            assert app._settings_entries["access_key"].get() == "volc-ak"
            assert app._settings_entries["region"].get() == "cn-beijing"
            print("PASS: provider switch rebuild, region fallback, alert carry, "
                  "credential isolation, cancel, save reuse, vault separation")
        finally:
            app.shutdown()


if __name__ == "__main__":
    main()
