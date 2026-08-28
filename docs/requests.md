# Requests and Responses

Every SMP Request and Response is defined by
[smp](https://jphutchins.github.io/smp/latest/), and each Request carries the
`Response`, `ErrorV1`, and `ErrorV2` it may provoke. `SMPClient.request()` therefore
returns a union that narrows exhaustively:

```python
response = await client.request(EchoWriteRequest(d="Hello, World!"))

if success(response):
    print(response.r)
elif error_v1(response):
    print(response.rc)
elif error_v2(response):
    print(response.err.rc)
else:
    assert_never(response)
```

## Groups

Construct requests directly from `smp`:

| Group | Reference |
| --- | --- |
| Enumeration Management | [smp.enumeration_management](https://jphutchins.github.io/smp/latest/enumeration_management/) |
| File Management | [smp.file_management](https://jphutchins.github.io/smp/latest/file_management/) |
| Image Management | [smp.image_management](https://jphutchins.github.io/smp/latest/image_management/) |
| OS Management | [smp.os_management](https://jphutchins.github.io/smp/latest/os_management/) |
| Settings Management | [smp.settings_management](https://jphutchins.github.io/smp/latest/settings_management/) |
| Shell Management | [smp.shell_management](https://jphutchins.github.io/smp/latest/shell_management/) |
| Statistics Management | [smp.statistics_management](https://jphutchins.github.io/smp/latest/statistics_management/) |
| Zephyr Management | [smp.zephyr_management](https://jphutchins.github.io/smp/latest/zephyr_management/) |
| Intercreate (user) | [smp.user.intercreate](https://jphutchins.github.io/smp/latest/user/intercreate/) |

## Narrowing helpers

::: smpclient.success

::: smpclient.error

::: smpclient.error_v1

::: smpclient.error_v2
