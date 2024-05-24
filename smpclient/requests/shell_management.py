from smp import shell_management as smpshell


class _ShellGroupBase:
    _ErrorV0 = smpshell.ShellManagementErrorV0
    _ErrorV1 = smpshell.ShellManagementErrorV1


class Execute(smpshell.ExecuteRequest, _ShellGroupBase):
    _Response = smpshell.ExecuteResponse
