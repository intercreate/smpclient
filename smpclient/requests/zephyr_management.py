import smp.zephyr_management as smpz


class _GroupBase:
    _ErrorV1 = smpz.ZephyrManagementErrorV1
    _ErrorV2 = smpz.ZephyrManagementErrorV2


class EraseStorage(smpz.EraseStorageRequest, _GroupBase):
    _Response = smpz.EraseStorageResponse
