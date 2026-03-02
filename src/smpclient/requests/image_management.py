from smp import image_management as smpimg


class _ImageGroupBase:
    _ErrorV1 = smpimg.ImageManagementErrorV1
    _ErrorV2 = smpimg.ImageManagementErrorV2


class ImageStatesRead(smpimg.ImageStatesReadRequest, _ImageGroupBase):
    _Response = smpimg.ImageStatesReadResponse


class ImageStatesWrite(smpimg.ImageStatesWriteRequest, _ImageGroupBase):
    _Response = smpimg.ImageStatesWriteResponse


class ImageUploadWrite(smpimg.ImageUploadWriteRequest, _ImageGroupBase):
    _Response = smpimg.ImageUploadWriteResponse


class ImageErase(smpimg.ImageEraseRequest, _ImageGroupBase):
    _Response = smpimg.ImageEraseResponse
