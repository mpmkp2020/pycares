
from _pycares_cffi import ffi as _ffi, lib as _lib
import _cffi_backend  # hint for bundler tools

if _lib.ARES_SUCCESS != _lib.ares_library_init(_lib.ARES_LIB_INIT_ALL):
    raise RuntimeError('Could not initialize c-ares')

from . import errno
from .utils import ensure_bytes, maybe_str
from ._version import __version__

import collections.abc
import socket
import math
import functools
import sys


exported_pycares_symbols = [
    # Flag values
    'ARES_FLAG_USEVC',
    'ARES_FLAG_PRIMARY',
    'ARES_FLAG_IGNTC',
    'ARES_FLAG_NORECURSE',
    'ARES_FLAG_STAYOPEN',
    'ARES_FLAG_NOSEARCH',
    'ARES_FLAG_NOALIASES',
    'ARES_FLAG_NOCHECKRESP',

    # Nameinfo flag values
    'ARES_NI_NOFQDN',
    'ARES_NI_NUMERICHOST',
    'ARES_NI_NAMEREQD',
    'ARES_NI_NUMERICSERV',
    'ARES_NI_DGRAM',
    'ARES_NI_TCP',
    'ARES_NI_UDP',
    'ARES_NI_SCTP',
    'ARES_NI_DCCP',
    'ARES_NI_NUMERICSCOPE',
    'ARES_NI_LOOKUPHOST',
    'ARES_NI_LOOKUPSERVICE',
    'ARES_NI_IDN',
    'ARES_NI_IDN_ALLOW_UNASSIGNED',
    'ARES_NI_IDN_USE_STD3_ASCII_RULES',

    # Bad socket
    'ARES_SOCKET_BAD',
]

for symbol in exported_pycares_symbols:
    globals()[symbol] = getattr(_lib, symbol)


exported_pycares_symbols_map = {
    # Query types
    "QUERY_TYPE_A" : "T_A",
    "QUERY_TYPE_AAAA" : "T_AAAA",
    "QUERY_TYPE_ANY" : "T_ANY",
    "QUERY_TYPE_CNAME" : "T_CNAME",
    "QUERY_TYPE_MX" : "T_MX",
    "QUERY_TYPE_NAPTR" : "T_NAPTR",
    "QUERY_TYPE_NS" : "T_NS",
    "QUERY_TYPE_PTR" : "T_PTR",
    "QUERY_TYPE_SOA" : "T_SOA",
    "QUERY_TYPE_SRV" : "T_SRV",
    "QUERY_TYPE_TXT" : "T_TXT",
}

for k, v in exported_pycares_symbols_map.items():
    globals()[k] = getattr(_lib, v)


globals()['ARES_VERSION'] = maybe_str(_ffi.string(_lib.ares_version(_ffi.NULL)))


PYCARES_ADDRTTL_SIZE = 256


class AresError(Exception):
    pass


def check_channel(f):
    @functools.wraps(f)
    def wrapper(self, *args, **kwds):
        if not self.channel:
            raise AresError("Channel has already been destroyed")
        return f(self, *args, **kwds)
    return wrapper

# callback helpers

_global_set = set()

@_ffi.callback("void (void *data, ares_socket_t socket_fd, int readable, int writable )")
def _sock_state_cb(data, socket_fd, readable, writable):
    sock_state_cb = _ffi.from_handle(data)
    sock_state_cb(socket_fd, readable, writable)

@_ffi.callback("void (void *arg, int status, int timeouts, struct hostent *hostent)")
def _host_cb(arg, status, timeouts, hostent):
    callback = _ffi.from_handle(arg)
    _global_set.discard(arg)

    if status != _lib.ARES_SUCCESS:
        result = None
    else:
        result = ares_host_result(hostent)
        status = None

    callback(result, status)

@_ffi.callback("void (void *arg, int status, int timeouts, char *node, char *service)")
def _nameinfo_cb(arg, status, timeouts, node, service):
    callback = _ffi.from_handle(arg)
    _global_set.discard(arg)

    if status != _lib.ARES_SUCCESS:
        result = None
    else:
        result = ares_nameinfo_result(node, service)
        status = None

    callback(result, status)

@_ffi.callback("void (void *arg, int status, int timeouts, unsigned char *abuf, int alen)")
def _query_cb(arg, status, timeouts, abuf, alen):
    callback, query_type = _ffi.from_handle(arg)
    _global_set.discard(arg)

    if status == _lib.ARES_SUCCESS:
        if query_type == _lib.T_ANY:
            result = []
            for qtype in (_lib.T_A, _lib.T_AAAA, _lib.T_CNAME, _lib.T_MX, _lib.T_NAPTR, _lib.T_NS, _lib.T_PTR, _lib.T_SOA, _lib.T_SRV, _lib.T_TXT):
                r, status = parse_result(qtype, abuf, alen)
                if status not in (None, _lib.ARES_ENODATA, _lib.ARES_EBADRESP):
                    result = None
                    break
                if r is not None:
                    if isinstance(r, collections.abc.Iterable):
                        result.extend(r)
                    else:
                        result.append(r)
            else:
                status = None
        else:
            result, status = parse_result(query_type, abuf, alen)
    else:
        result = None

    callback(result, status)

def parse_result(query_type, abuf, alen):
    if query_type == _lib.T_A:
        addrttls = _ffi.new("struct ares_addrttl[]", PYCARES_ADDRTTL_SIZE)
        naddrttls = _ffi.new("int*", PYCARES_ADDRTTL_SIZE)
        parse_status = _lib.ares_parse_a_reply(abuf, alen, _ffi.NULL, addrttls, naddrttls)
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            result = [ares_query_simple_result(addrttls[i]) for i in range(naddrttls[0])]
            status = None
    elif query_type == _lib.T_AAAA:
        addrttls = _ffi.new("struct ares_addr6ttl[]", PYCARES_ADDRTTL_SIZE)
        naddrttls = _ffi.new("int*", PYCARES_ADDRTTL_SIZE)
        parse_status = _lib.ares_parse_aaaa_reply(abuf, alen, _ffi.NULL, addrttls, naddrttls)
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            result = [ares_query_simple_result(addrttls[i]) for i in range(naddrttls[0])]
            status = None
    elif query_type == _lib.T_CNAME:
        host = _ffi.new("struct hostent **")
        parse_status = _lib.ares_parse_a_reply(abuf, alen, host, _ffi.NULL, _ffi.NULL)
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            result = ares_query_cname_result(host[0])
            _lib.ares_free_hostent(host[0])
            status = None
    elif query_type == _lib.T_MX:
        mx_reply = _ffi.new("struct ares_mx_reply **")
        parse_status = _lib.ares_parse_mx_reply(abuf, alen, mx_reply);
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            result = []
            mx_reply_ptr = mx_reply[0]
            while mx_reply_ptr != _ffi.NULL:
                result.append(ares_query_mx_result(mx_reply_ptr))
                mx_reply_ptr = mx_reply_ptr.next
            _lib.ares_free_data(mx_reply)
            status = None
    elif query_type == _lib.T_NAPTR:
        naptr_reply = _ffi.new("struct ares_naptr_reply **")
        parse_status = _lib.ares_parse_naptr_reply(abuf, alen, naptr_reply);
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            result = []
            naptr_reply_ptr = naptr_reply[0]
            while naptr_reply_ptr != _ffi.NULL:
                result.append(ares_query_naptr_result(naptr_reply_ptr))
                naptr_reply_ptr = naptr_reply_ptr.next
            _lib.ares_free_data(naptr_reply)
            status = None
    elif query_type == _lib.T_NS:
        hostent = _ffi.new("struct hostent **")
        parse_status = _lib.ares_parse_ns_reply(abuf, alen, hostent);
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            result = []
            host = hostent[0]
            i = 0
            while host.h_aliases[i] != _ffi.NULL:
                result.append(ares_query_ns_result(host.h_aliases[i]))
                i += 1
            _lib.ares_free_hostent(host)
            status = None
    elif query_type == _lib.T_PTR:
        hostent = _ffi.new("struct hostent **")
        hostttl = _ffi.new("int*", PYCARES_ADDRTTL_SIZE)
        parse_status = _lib.ares_parse_ptr_reply(abuf, alen, _ffi.NULL, 0, socket.AF_UNSPEC, hostent, hostttl);
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            aliases = []
            host = hostent[0]
            i = 0
            while host.h_aliases[i] != _ffi.NULL:
                aliases.append(maybe_str(_ffi.string(host.h_aliases[i])))
                i += 1
            result = ares_query_ptr_result(host, hostttl[0], aliases)
            _lib.ares_free_hostent(host)
            status = None
    elif query_type == _lib.T_SOA:
        soa_reply = _ffi.new("struct ares_soa_reply **")
        parse_status = _lib.ares_parse_soa_reply(abuf, alen, soa_reply);
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            result = ares_query_soa_result(soa_reply[0])
            _lib.ares_free_data(soa_reply[0])
            status = None
    elif query_type == _lib.T_SRV:
        srv_reply = _ffi.new("struct ares_srv_reply **")
        parse_status = _lib.ares_parse_srv_reply(abuf, alen, srv_reply);
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            result = []
            srv_reply_ptr = _ffi.new("struct ares_srv_reply **")
            srv_reply_ptr[0] = srv_reply[0]
            while True:
                if srv_reply_ptr[0] == _ffi.NULL:
                    break
                result.append(ares_query_srv_result(srv_reply_ptr[0]))
                srv_reply_ptr[0] = srv_reply_ptr[0].next
            _lib.ares_free_data(srv_reply[0])
            status = None
    elif query_type == _lib.T_TXT:
        txt_reply = _ffi.new("struct ares_txt_ext **")
        parse_status = _lib.ares_parse_txt_reply_ext(abuf, alen, txt_reply);
        if parse_status != _lib.ARES_SUCCESS:
            result = None
            status = parse_status
        else:
            result = []
            txt_reply_ptr = _ffi.new("struct ares_txt_ext **")
            txt_reply_ptr[0] = txt_reply[0]
            tmp_obj = None
            while True:
                if txt_reply_ptr[0] == _ffi.NULL:
                    if tmp_obj is not None:
                        result.append(tmp_obj)
                    break
                if txt_reply_ptr[0].record_start == 1:
                    if tmp_obj is not None:
                        result.append(tmp_obj)
                    tmp_obj = ares_query_txt_result(txt_reply_ptr[0])
                else:
                    new_chunk = ares_query_txt_result(txt_reply_ptr[0])
                    tmp_obj.text += new_chunk.text
                txt_reply_ptr[0] = txt_reply_ptr[0].next
            _lib.ares_free_data(txt_reply[0])
            status = None
    else:
        raise ValueError("invalid query type specified")

    return result, status


class Channel:
    def __init__(self,
                 flags = None,
                 timeout = None,
                 tries = None,
                 ndots = None,
                 tcp_port = None,
                 udp_port = None,
                 servers = None,
                 domains = None,
                 lookups = None,
                 sock_state_cb = None,
                 socket_send_buffer_size = None,
                 socket_receive_buffer_size = None,
                 rotate = False,
                 local_ip = None,
                 local_dev = None,
                 resolvconf_path = None):

        channel = _ffi.new("ares_channel *")
        options = _ffi.new("struct ares_options *")
        optmask = 0

        if flags is not None:
            options.flags = flags
            optmask = optmask | _lib.ARES_OPT_FLAGS

        if timeout is not None:
            options.timeout = int(timeout * 1000)
            optmask = optmask | _lib.ARES_OPT_TIMEOUTMS

        if tries is not None:
            options.tries = tries
            optmask = optmask |  _lib.ARES_OPT_TRIES

        if ndots is not None:
            options.ndots = ndots
            optmask = optmask |  _lib.ARES_OPT_NDOTS

        if tcp_port is not None:
            options.tcp_port = tcp_port
            optmask = optmask |  _lib.ARES_OPT_TCP_PORT

        if udp_port is not None:
            options.udp_port = udp_port
            optmask = optmask |  _lib.ARES_OPT_UDP_PORT

        if socket_send_buffer_size is not None:
            options.socket_send_buffer_size = socket_send_buffer_size
            optmask = optmask |  _lib.ARES_OPT_SOCK_SNDBUF

        if socket_receive_buffer_size is not None:
            options.socket_receive_buffer_size = socket_receive_buffer_size
            optmask = optmask |  _lib.ARES_OPT_SOCK_RCVBUF

        if sock_state_cb:
            if not callable(sock_state_cb):
                raise TypeError("sock_state_cb is not callable")

            userdata = _ffi.new_handle(sock_state_cb)

            # This must be kept alive while the channel is alive.
            self._sock_state_cb_handle = userdata

            options.sock_state_cb = _sock_state_cb
            options.sock_state_cb_data = userdata
            optmask = optmask |  _lib.ARES_OPT_SOCK_STATE_CB

        if lookups:
            options.lookups = lookups
            optmask = optmask |  _lib.ARES_OPT_LOOKUPS

        if domains:
            strs = [_ffi.new("char[]", ensure_bytes(i)) for i in domains]
            c = _ffi.new("char *[%d]" % (len(domains) + 1))
            for i in range(len(domains)):
               c[i] = strs[i]

            options.domains = c
            options.ndomains = len(domains)
            optmask = optmask |  _lib.ARES_OPT_DOMAINS

        if rotate:
            optmask = optmask |  _lib.ARES_OPT_ROTATE

        if resolvconf_path is not None:
            optmask = optmask |  _lib.ARES_OPT_RESOLVCONF
            options.resolvconf_path = _ffi.new('char[]', ensure_bytes(resolvconf_path))

        r = _lib.ares_init_options(channel, options, optmask)
        if r != _lib.ARES_SUCCESS:
            raise AresError('Failed to initialize c-ares channel')

        self.channel = channel[0]

        if servers:
            self._set_servers(servers)

        if local_ip:
            self.set_local_ip(local_ip)

        if local_dev:
            self.set_local_dev(local_dev)

    def __del__(self):
        self.destroy()

    def destroy(self):
        if self.channel:
            _lib.ares_destroy(self.channel)
            self.channel = None
        self._sock_state_cb_handle = None

    @check_channel
    def cancel(self):
        _lib.ares_cancel(self.channel)

    @check_channel
    def _set_servers(self, servers):
        c = _ffi.new("struct ares_addr_node[%d]" % len(servers))
        for i, server in enumerate(servers):
            if 1 == _lib.ares_inet_pton(socket.AF_INET, ensure_bytes(server), _ffi.addressof(c[i].addr.addr4)):
                c[i].family = socket.AF_INET
            elif 1 == _lib.ares_inet_pton(socket.AF_INET6, ensure_bytes(server), _ffi.addressof(c[i].addr.addr6)):
                c[i].family = socket.AF_INET6
            else:
                raise ValueError("invalid IP address")

            if i > 0:
                c[i - 1].next = _ffi.addressof(c[i])

        r = _lib.ares_set_servers(self.channel, c)
        if r != _lib.ARES_SUCCESS:
            raise AresError()

    @check_channel
    def _get_servers(self):
        servers = _ffi.new("struct ares_addr_node **")

        r = _lib.ares_get_servers(self.channel, servers)
        if r != _lib.ARES_SUCCESS:
            raise AresError(errno.strerror(r))

        server_list = []
        server = _ffi.new("struct ares_addr_node **", servers[0])
        while True:
            if server == _ffi.NULL:
                break

            ip = _ffi.new("char []", _lib.INET6_ADDRSTRLEN)
            s = server[0]
            if _ffi.NULL != _lib.ares_inet_ntop(s.family, _ffi.addressof(s.addr), ip, _lib.INET6_ADDRSTRLEN):
                server_list.append(maybe_str(_ffi.string(ip, _lib.INET6_ADDRSTRLEN)))

            server = s.next

        return server_list

    servers = property(_get_servers, _set_servers)

    @check_channel
    def getsock(self):
        rfds = []
        wfds = []
        socks = _ffi.new("ares_socket_t [%d]" % _lib.ARES_GETSOCK_MAXNUM)
        bitmask = _lib.ares_getsock(self.channel, socks, _lib.ARES_GETSOCK_MAXNUM)
        for i in range(_lib.ARES_GETSOCK_MAXNUM):
            if _lib.ARES_GETSOCK_READABLE(bitmask, i):
                rfds.append(socks[i])
            if _lib.ARES_GETSOCK_WRITABLE(bitmask, i):
                wfds.append(socks[i])

        return rfds, wfds


    @check_channel
    def process_fd(self, read_fd, write_fd):
        _lib.ares_process_fd(self.channel, _ffi.cast("ares_socket_t", read_fd), _ffi.cast("ares_socket_t", write_fd))

    @check_channel
    def timeout(self, t = None):
        maxtv = _ffi.NULL
        tv = _ffi.new("struct timeval*")

        if t is not None:
            if t >= 0.0:
                maxtv = _ffi.new("struct timeval*")
                maxtv.tv_sec = int(math.floor(t))
                maxtv.tv_usec = int(math.fmod(t, 1.0) * 1000000)
            else:
                raise ValueError("timeout needs to be a positive number or None")

        _lib.ares_timeout(self.channel, maxtv, tv)

        if tv == _ffi.NULL:
            return 0.0

        return (tv.tv_sec + tv.tv_usec / 1000000.0)

    @check_channel
    def gethostbyaddr(self, name, callback):
        if not callable(callback):
            raise TypeError("a callable is required")

        addr4 = _ffi.new("struct in_addr*")
        addr6 = _ffi.new("struct ares_in6_addr*")
        if 1 == _lib.ares_inet_pton(socket.AF_INET,ensure_bytes(name), (addr4)):
            address = addr4
            family = socket.AF_INET
        elif 1 == _lib.ares_inet_pton(socket.AF_INET6, ensure_bytes(name), (addr6)):
            address = addr6
            family = socket.AF_INET6
        else:
            raise ValueError("invalid IP address")

        userdata = _ffi.new_handle(callback)
        _global_set.add(userdata)
        _lib.ares_gethostbyaddr(self.channel, (address), _ffi.sizeof(address[0]), family, _host_cb, userdata)


    @check_channel
    def gethostbyname(self, name, family, callback):
        if not callable(callback):
            raise TypeError("a callable is required")

        userdata = _ffi.new_handle(callback)
        _global_set.add(userdata)
        _lib.ares_gethostbyname(self.channel, ensure_bytes(name), family, _host_cb, userdata)

    def query(self, name, query_type, callback):
        if not callable(callback):
            raise TypeError("a callable is required")

        if query_type not in (_lib.T_A, _lib.T_AAAA, _lib.T_ANY, _lib.T_CNAME, _lib.T_MX, _lib.T_NAPTR, _lib.T_NS, _lib.T_PTR, _lib.T_SOA, _lib.T_SRV, _lib.T_TXT):
            raise ValueError("invalid query type specified")

        userdata = _ffi.new_handle((callback, query_type))
        _global_set.add(userdata)
        _lib.ares_query(self.channel, ensure_bytes(name), _lib.C_IN, query_type, _query_cb, userdata)

    @check_channel
    def set_local_ip(self, ip):
        addr4 = _ffi.new("struct in_addr*")
        addr6 = _ffi.new("struct ares_in6_addr*")
        if 1 == _lib.ares_inet_pton(socket.AF_INET, ensure_bytes(ip), addr4):
            _lib.ares_set_local_ip4(self.channel, socket.ntohl(addr4.s_addr))
        elif 1 == _lib.ares_inet_pton(socket.AF_INET6, ensure_bytes(ip), addr6):
            _lib.ares_set_local_ip6(self.channel, addr6)
        else:
            raise ValueError("invalid IP address")

    @check_channel
    def getnameinfo(self, ip_port, flags, callback):
        if not callable(callback):
            raise TypeError("a callable is required")

        ip, port = ip_port

        if port < 0 or port > 65535:
            raise ValueError("port must be between 0 and 65535")

        sa4 = _ffi.new("struct sockaddr_in*")
        sa6 = _ffi.new("struct sockaddr_in6*")

        if 1 == _lib.ares_inet_pton(socket.AF_INET, ensure_bytes(ip), _ffi.addressof(sa4.sin_addr)):
            sa4.sin_family = socket.AF_INET
            sa4.sin_port = socket.htons(port)
            sa = sa4
        elif 1 == _lib.ares_inet_pton(socket.AF_INET6, ensure_bytes(ip), _ffi.addressof(sa6.sin6_addr)):
            sa6.sin6_family = socket.AF_INET6
            sa6.sin6_port = socket.htons(port)
            sa = sa6
        else:
            raise ValueError("invalid IP address")

        userdata = _ffi.new_handle(callback)
        _global_set.add(userdata)
        _lib.ares_getnameinfo(self.channel, _ffi.cast("struct sockaddr*", sa), _ffi.sizeof(sa[0]), flags, _nameinfo_cb, userdata)

    @check_channel
    def set_local_dev(self, dev):
        _lib.ares_set_local_dev(self.channel, dev)


class AresResult:
    __slots__ = ()

    def __repr__(self):
        attrs = ['%s=%s' % (a, getattr(self, a)) for a in self.__slots__]
        return '<%s> %s' % (self.__class__.__name__, ', '.join(attrs))


class ares_host_result(AresResult):
    __slots__ = ('name', 'aliases', 'addresses')

    def __init__(self, hostent):
        self.name = maybe_str(_ffi.string(hostent.h_name))
        self.aliases = []
        self.addresses = []
        i = 0
        while hostent.h_aliases[i] != _ffi.NULL:
            self.aliases.append(maybe_str(_ffi.string(hostent.h_aliases[i])))
            i += 1

        i = 0
        while hostent.h_addr_list[i] != _ffi.NULL:
            buf = _ffi.new("char[]", _lib.INET6_ADDRSTRLEN)
            if _ffi.NULL != _lib.ares_inet_ntop(hostent.h_addrtype, hostent.h_addr_list[i], buf, _lib.INET6_ADDRSTRLEN):
                self.addresses.append(maybe_str(_ffi.string(buf, _lib.INET6_ADDRSTRLEN)))
            i += 1


class ares_query_simple_result(AresResult):
    __slots__ = ('host', 'ttl')

    def __init__(self, ares_addrttl):
        buf = _ffi.new("char[]", _lib.INET6_ADDRSTRLEN)
        if _ffi.typeof(ares_addrttl) is _ffi.typeof("struct ares_addrttl"):
            _lib.ares_inet_ntop(socket.AF_INET, _ffi.addressof(ares_addrttl.ipaddr), buf, _lib.INET6_ADDRSTRLEN)
        elif _ffi.typeof(ares_addrttl) is _ffi.typeof("struct ares_addr6ttl"):
            _lib.ares_inet_ntop(socket.AF_INET6, _ffi.addressof(ares_addrttl.ip6addr), buf, _lib.INET6_ADDRSTRLEN)
        else:
            raise TypeError()

        self.host = maybe_str(_ffi.string(buf, _lib.INET6_ADDRSTRLEN))
        self.ttl = ares_addrttl.ttl


class ares_query_cname_result(AresResult):
    __slots__ = ('cname', 'ttl')

    def __init__(self, host):
        self.cname = maybe_str(_ffi.string(host.h_name))
        self.ttl = -1


class ares_query_mx_result(AresResult):
    __slots__ = ('host', 'priority', 'ttl')

    def __init__(self, mx):
        self.host = maybe_str(_ffi.string(mx.host))
        self.priority = mx.priority
        self.ttl = mx.ttl


class ares_query_naptr_result(AresResult):
    __slots__ = ('order', 'preference', 'flags', 'service', 'regex', 'replacement', 'ttl')

    def __init__(self, naptr):
        self.order = naptr.order
        self.preference = naptr.preference
        self.flags = maybe_str(_ffi.string(naptr.flags))
        self.service = maybe_str(_ffi.string(naptr.service))
        self.regex = maybe_str(_ffi.string(naptr.regexp))
        self.replacement = maybe_str(_ffi.string(naptr.replacement))
        self.ttl = naptr.ttl


class ares_query_ns_result(AresResult):
    __slots__ = ('host', 'ttl')

    def __init__(self, ns):
        self.host = maybe_str(_ffi.string(ns))
        self.ttl = -1


class ares_query_ptr_result(AresResult):
    __slots__ = ('name', 'ttl', 'aliases')

    def __init__(self, hostent, ttl, aliases):
        self.name = maybe_str(_ffi.string(hostent.h_name))
        self.ttl = ttl
        self.aliases = aliases


class ares_query_soa_result(AresResult):
    __slots__ = ('nsname', 'hostmaster', 'serial', 'refresh', 'retry', 'expires', 'minttl', 'ttl')

    def __init__(self, soa):
        self.nsname = maybe_str(_ffi.string(soa.nsname))
        self.hostmaster = maybe_str(_ffi.string(soa.hostmaster))
        self.serial = soa.serial
        self.refresh = soa.refresh
        self.retry = soa.retry
        self.expires = soa.expire
        self.minttl = soa.minttl
        self.ttl = soa.ttl


class  ares_query_srv_result(AresResult):
    __slots__ = ('host', 'port', 'priority', 'weight', 'ttl')

    def __init__(self, srv):
        self.host = maybe_str(_ffi.string(srv.host))
        self.port = srv.port
        self.priority = srv.priority
        self.weight = srv.weight
        self.ttl = srv.ttl


class ares_query_txt_result(AresResult):
    __slots__ = ('text', 'ttl')

    def __init__(self, txt):
        self.text = maybe_str(_ffi.string(txt.txt))
        self.ttl = txt.ttl


class ares_nameinfo_result(AresResult):
    __slots__ = ('node', 'service')

    def __init__(self, node, service):
        self.node = maybe_str(_ffi.string(node))
        self.service = maybe_str(_ffi.string(service)) if service != _ffi.NULL else None


__all__ = exported_pycares_symbols + list(exported_pycares_symbols_map.keys()) + ['AresError', 'Channel', 'errno', '__version__']

del exported_pycares_symbols, exported_pycares_symbols_map

