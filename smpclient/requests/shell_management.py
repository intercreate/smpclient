from smp import shell_management as smpshell


class _ShellGroupBase:
    _ErrorV1 = smpshell.ShellManagementErrorV1
    _ErrorV2 = smpshell.ShellManagementErrorV2


class Execute(smpshell.ExecuteRequest, _ShellGroupBase):
    _Response = smpshell.ExecuteResponse
