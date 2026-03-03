from smp import os_management as smpos


class _OSGroupBase:
    _ErrorV1 = smpos.OSManagementErrorV1
    _ErrorV2 = smpos.OSManagementErrorV2


class EchoWrite(smpos.EchoWriteRequest, _OSGroupBase):
    _Response = smpos.EchoWriteResponse


class ResetWrite(smpos.ResetWriteRequest, _OSGroupBase):
    _Response = smpos.ResetWriteResponse


class TaskStatisticsRead(smpos.TaskStatisticsReadRequest, _OSGroupBase):
    _Response = smpos.TaskStatisticsReadResponse


class MemoryPoolStatisticsRead(smpos.MemoryPoolStatisticsReadRequest, _OSGroupBase):
    _Response = smpos.MemoryPoolStatisticsReadResponse


class DateTimeRead(smpos.DateTimeReadRequest, _OSGroupBase):
    _Response = smpos.DateTimeReadResponse


class DateTimeWrite(smpos.DateTimeWriteRequest, _OSGroupBase):
    _Response = smpos.DateTimeWriteResponse


class MCUMgrParametersRead(smpos.MCUMgrParametersReadRequest, _OSGroupBase):
    _Response = smpos.MCUMgrParametersReadResponse


class OSApplicationInfoRead(smpos.OSApplicationInfoReadRequest, _OSGroupBase):
    _Response = smpos.OSApplicationInfoReadResponse


class BootloaderInformationRead(smpos.BootloaderInformationReadRequest, _OSGroupBase):
    _Response = smpos.BootloaderInformationReadResponse
