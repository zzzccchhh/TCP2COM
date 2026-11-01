"""IPv4 interface discovery with Windows' permanent AdapterName identity."""

import ctypes
from dataclasses import dataclass
import ipaddress
import os
import socket


@dataclass(frozen=True)
class InterfaceAddress:
    id: str
    name: str
    ip: str

    def url(self, port):
        return f"http://{self.ip}:{port}/"

    def label(self, port):
        return f"{self.name} · {self.url(port)}"

    def preference(self):
        return {"id": self.id, "name": self.name, "ip": self.ip}


def windows_addresses():
    # Prefixes of the documented IP_ADAPTER_*_LH structures, through fields we read.
    # https://learn.microsoft.com/windows/win32/api/iptypes/ns-iptypes-ip_adapter_addresses_lh
    class SocketAddress(ctypes.Structure):
        _fields_ = [("pointer", ctypes.c_void_p), ("length", ctypes.c_int)]

    class Unicast(ctypes.Structure):
        pass
    Unicast._fields_ = [("alignment", ctypes.c_uint64), ("next", ctypes.POINTER(Unicast)),
                       ("address", SocketAddress), ("prefix_origin", ctypes.c_int),
                       ("suffix_origin", ctypes.c_int), ("dad_state", ctypes.c_int)]

    class Adapter(ctypes.Structure):
        pass
    Adapter._fields_ = [("alignment", ctypes.c_uint64), ("next", ctypes.POINTER(Adapter)),
                       ("adapter_name", ctypes.c_char_p), ("unicast", ctypes.POINTER(Unicast)),
                       ("anycast", ctypes.c_void_p), ("multicast", ctypes.c_void_p), ("dns", ctypes.c_void_p),
                       ("suffix", ctypes.c_wchar_p), ("description", ctypes.c_wchar_p), ("friendly_name", ctypes.c_wchar_p),
                       ("mac", ctypes.c_ubyte * 8), ("mac_length", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                       ("mtu", ctypes.c_uint32), ("if_type", ctypes.c_uint32), ("oper_status", ctypes.c_int)]
    function = ctypes.WinDLL("iphlpapi").GetAdaptersAddresses
    function.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    function.restype = ctypes.c_uint32
    size = ctypes.c_uint32(15000)
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = function(socket.AF_INET, 14, None, buffer, ctypes.byref(size))
        if result != 111:  # ERROR_BUFFER_OVERFLOW: topology may change between calls.
            break
    if result == 232:  # ERROR_NO_DATA
        return []
    if result:
        raise OSError(result, "无法读取 Windows 网卡信息")
    pointer = ctypes.cast(buffer, ctypes.POINTER(Adapter))
    found = []
    while pointer:
        adapter = pointer.contents
        if adapter.oper_status == 1 and adapter.adapter_name:
            identity = "windows:" + adapter.adapter_name.decode("ascii").lower()
            name = adapter.friendly_name or adapter.description or identity
            unicast = adapter.unicast
            while unicast:
                address = unicast.contents.address
                if address.pointer and address.length >= 8 and unicast.contents.dad_state == 4:
                    family = ctypes.c_ushort.from_address(address.pointer).value
                    if family == socket.AF_INET:
                        ip = socket.inet_ntop(socket.AF_INET, ctypes.string_at(address.pointer + 4, 4))
                        found.append(InterfaceAddress(identity, name, ip))
                unicast = unicast.contents.next
        pointer = adapter.next
    return found


def discover_addresses(host):
    warning = ""
    try:
        if os.name == "nt":
            found = windows_addresses()
        else:
            found = [InterfaceAddress("ip:" + row[4][0], "网络接口", row[4][0])
                     for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except (OSError, ValueError):
        # Do not replace a stored permanent identity with an IP-based guess after a failed scan.
        found = []
        warning = "无法读取网卡信息，请重新扫描；已保留首选网卡记录。"
    found = [row for row in found if not ipaddress.IPv4Address(row.ip).is_unspecified and not ipaddress.IPv4Address(row.ip).is_multicast]
    found = [InterfaceAddress("loopback", "本机专用", row.ip) if row.ip.startswith("127.") else row for row in found]
    if host != "0.0.0.0":
        found = [row for row in found if row.ip == host]
        if not found:
            found = [InterfaceAddress("loopback" if host.startswith("127.") else "ip:" + host,
                                      "本机专用" if host.startswith("127.") else "固定监听地址", host)]
    else:
        found = [row for row in found if not row.ip.startswith("127.")]
        found.append(InterfaceAddress("loopback", "本机专用", "127.0.0.1"))
    return sorted(set(found), key=lambda row: (row.ip.startswith("127."), row.name, int(ipaddress.IPv4Address(row.ip)))), warning


def choose_address(addresses, preferred):
    if preferred:
        matches = [row for row in addresses if row.id == preferred["id"]]
        if matches:
            selected = next((row for row in matches if row.ip == preferred["ip"]), matches[0])
            return selected, "首选网卡 IP 已变化，链接和二维码已更新。" if selected.ip != preferred["ip"] else ""
        # Prefer local-only fallback while a known interface is absent; never overwrite its identity.
        selected = next((row for row in addresses if row.ip.startswith("127.")), addresses[0])
        return selected, f"首选网卡 {preferred['name']} 暂不可用，已临时使用 {selected.name}；记录已保留。"
    return addresses[0], ""
