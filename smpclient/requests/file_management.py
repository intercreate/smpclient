from smp import file_management as smpfs


class _FileGroupBase:
    _ErrorV1 = smpfs.FileSystemManagementErrorV1
    _ErrorV2 = smpfs.FileSystemManagementErrorV2


class FileDownload(smpfs.FileDownloadRequest, _FileGroupBase):
    _Response = smpfs.FileDownloadResponse


class FileUpload(smpfs.FileUploadRequest, _FileGroupBase):
    _Response = smpfs.FileUploadResponse


class FileStatus(smpfs.FileStatusRequest, _FileGroupBase):
    _Response = smpfs.FileStatusResponse


class FileHashChecksum(smpfs.FileHashChecksumRequest, _FileGroupBase):
    _Response = smpfs.FileHashChecksumResponse


class SupportedFileHashChecksumTypes(smpfs.SupportedFileHashChecksumTypesRequest, _FileGroupBase):
    _Response = smpfs.SupportedFileHashChecksumTypesResponse


class FileClose(smpfs.FileCloseRequest, _FileGroupBase):
    _Response = smpfs.FileCloseResponse
