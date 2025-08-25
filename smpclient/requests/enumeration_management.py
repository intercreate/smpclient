from smp import enumeration_management as smpem


class _EnumGroupBase:
    _ErrorV1 = smpem.EnumManagementErrorV1
    _ErrorV2 = smpem.EnumManagementErrorV2


class CountSupportedGroups(smpem.GroupCountRequest, _EnumGroupBase):
    _Response = smpem.GroupCountResponse


class ListSupportedGroups(smpem.ListOfGroupsRequest, _EnumGroupBase):
    _Response = smpem.ListOfGroupsResponse


class GroupId(smpem.GroupIdRequest, _EnumGroupBase):
    _Response = smpem.GroupIdResponse


class GroupDetails(smpem.GroupDetailsRequest, _EnumGroupBase):
    _Response = smpem.GroupDetailsResponse
