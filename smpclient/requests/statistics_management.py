import smp.statistics_management as smpstat


class _GroupBase:
    _ErrorV1 = smpstat.StatisticsManagementErrorV1
    _ErrorV2 = smpstat.StatisticsManagementErrorV2


class GroupData(smpstat.GroupDataRequest, _GroupBase):
    _Response = smpstat.GroupDataResponse


class ListOfGroups(smpstat.ListOfGroupsRequest, _GroupBase):
    _Response = smpstat.ListOfGroupsResponse
