from smp.user import intercreate as smpic


class _GroupBase:
    _ErrorV0 = smpic.ErrorV0
    _ErrorV1 = smpic.ErrorV1


class ImageUploadWrite(smpic.ImageUploadWriteRequest, _GroupBase):
    _Response = smpic.ImageUploadWriteResponse
