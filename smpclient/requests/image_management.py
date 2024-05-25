from smp import image_management as smpimg


class _ImageGroupBase:
    _ErrorV0 = smpimg.ImageManagementErrorV0
    _ErrorV1 = smpimg.ImageManagementErrorV1


class ImageStatesRead(smpimg.ImageStatesReadRequest, _ImageGroupBase):
    _Response = smpimg.ImageStatesReadResponse


class ImageStatesWrite(smpimg.ImageStatesWriteRequest, _ImageGroupBase):
    _Response = smpimg.ImageStatesWriteResponse


class ImageUploadWrite(smpimg.ImageUploadWriteRequest, _ImageGroupBase):
    _Response = smpimg.ImageUploadProgressWriteResponse


class ImageErase(smpimg.ImageEraseRequest, _ImageGroupBase):
    _Response = smpimg.ImageEraseResponse
