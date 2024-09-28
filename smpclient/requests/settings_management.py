import smp.settings_management as smpset


class _GroupBase:
    _ErrorV1 = smpset.SettingsManagementErrorV1
    _ErrorV2 = smpset.SettingsManagementErrorV2


class ReadSetting(smpset.ReadSettingRequest, _GroupBase):
    _Response = smpset.ReadSettingResponse


class WriteSetting(smpset.WriteSettingRequest, _GroupBase):
    _Response = smpset.WriteSettingResponse


class DeleteSetting(smpset.DeleteSettingRequest, _GroupBase):
    _Response = smpset.DeleteSettingResponse


class CommitSettings(smpset.CommitSettingsRequest, _GroupBase):
    _Response = smpset.CommitSettingsResponse


class LoadSettings(smpset.LoadSettingsRequest, _GroupBase):
    _Response = smpset.LoadSettingsResponse


class SaveSettings(smpset.SaveSettingsRequest, _GroupBase):
    _Response = smpset.SaveSettingsResponse
