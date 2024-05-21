from smp import header as smpheader
from smp import os_management as smpos

from smpclient.generics import SMPError


class OSManagementError(SMPError[smpos.OS_MGMT_RET_RC]):
    _GROUP_ID = smpheader.GroupId.OS_MANAGEMENT


class _OSGroupBase:
    _ErrorV0 = smpos.OSManagementErrorV0
    _ErrorV1 = smpos.OSManagementErrorV1
    _Error = OSManagementError


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
