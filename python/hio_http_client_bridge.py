# -*- encoding: utf-8 -*-
"""
Minimal hio HTTP requester/respondent extracted for Pyodide JS-bridge use.

This avoids hio's TCP client and uses only the HTTP message build/parse logic.
"""

import json
import copy
import random

from collections import deque
from urllib.parse import urlsplit, quote, quote_plus, unquote, unquote_plus

from hio.help import Hict
from hio.core.http import httping


CRLF = b"\r\n"
LF = b"\n"
CR = b"\r"


class Requester(object):
    """
    Nonblocking HTTP Client Requester class (HTTP message builder only).
    """
    HttpVersionString = httping.HTTP_11_VERSION_STRING  # http version string
    Port = httping.HTTP_PORT  # default port

    def __init__(self,
                 hostname='127.0.0.1',
                 port=None,
                 scheme=u'http',
                 method=u'GET',  # unicode
                 path=u'/',  # unicode
                 qargs=None,
                 fragment=u'',  # unicode
                 headers=None,
                 body=b'',
                 data=None,
                 fargs=None,
                 portOptional=False):
        self.hostname, self.port = httping.normalizeHostPort(hostname, port, 80)
        self.scheme = scheme
        self.method = method.upper() if method else u'GET'
        self.path = path or u'/'
        self.qargs = qargs if qargs is not None else dict()
        self.fragment = fragment
        self.headers = Hict(headers) if headers else Hict()
        if body and isinstance(body, str):
            body = body.encode('iso-8859-1')
        self.body = body or b''
        self.data = data
        self.fargs = fargs
        self.portOptional = True if portOptional else False

        self.lines = []  # keep around for testing
        self.head = b""  # keep around for testing
        self.msg = b""

    def reinit(self,
               hostname=None,
               port=None,
               scheme=None,
               method=None,
               path=None,
               qargs=None,
               fragment=None,
               headers=None,
               body=None,
               data=None,
               fargs=None,
               portOptional=None):
        if hostname is not None:
            self.hostname = hostname
        if port is not None:
            self.port = port
        if scheme is not None:
            self.scheme = scheme
        if method is not None:
            self.method = method.upper()
        if path is not None:
            self.path = path
        if qargs is not None:
            self.qargs = qargs
        if fragment is not None:
            self.fragment = fragment
        if headers is not None:
            self.headers = Hict(headers)
        if body is not None:
            if body and isinstance(body, str):
                body = body.encode('iso-8859-1')
            self.body = body
        if data is not None:
            self.data = data
        if fargs is not None:
            self.fargs = fargs
        if portOptional is not None:
            self.portOptional = True if portOptional else False

    def rebuild(self,
                hostname=None,
                port=None,
                scheme=None,
                method=None,
                path=None,
                qargs=None,
                fragment=None,
                headers=None,
                body=None,
                data=None,
                fargs=None,
                portOptional=None):
        if (hostname is not None or port is not None or scheme is not None or
                method is not None or path is not None or qargs is not None or
                fragment is not None or headers is not None or body is not None or
                data is not None or fargs is not None or portOptional is not None):
            self.reinit(hostname=hostname, port=port, scheme=scheme, method=method,
                        path=path, qargs=qargs, fragment=fragment, headers=headers,
                        body=body, data=data, fargs=fargs, portOptional=portOptional)

        return self.build()

    def build(self):
        """
        Build and return request message from attributes.
        """
        self.lines = []

        pathSplits = urlsplit(self.path)
        path = pathSplits.path
        self.path = path
        path = quote(path)

        scheme = pathSplits.scheme
        if scheme and scheme != self.scheme:
            raise ValueError("Already open connection attempt to change scheme  "
                             " to '{0}'".format(scheme))

        port = pathSplits.port
        if port and port != self.port:
            raise ValueError("Already open connection attempt to change port  "
                             " to '{0}'".format(port))

        hostname = pathSplits.hostname
        if hostname and hostname != self.hostname:
            raise ValueError("Already open connection attempt to change hostname  "
                             " to '{0}'".format(hostname))

        query = pathSplits.query
        self.qargs, query = httping.updateQargsQuery(self.qargs, query)

        fragment = pathSplits.fragment
        if fragment:
            self.fragment = fragment

        combine = u"{0}?{1}#".format(path, query, fragment)
        combine = urlsplit(combine).geturl()

        startLine = "{0} {1} {2}".format(self.method, combine, self.HttpVersionString)
        try:
            startLine = startLine.encode('ascii')
        except UnicodeEncodeError:
            startLine = startLine.encode('idna')
        self.lines.append(startLine)

        if u'host' not in self.headers:
            host = self.hostname
            port = self.port
            if not self.portOptional:
                if ((self.scheme == u'http' and port != 80) or
                        (self.scheme == u'https' and port != 443)):
                    host = "{0}:{1}".format(host, port)
                self.headers[u'host'] = host
            else:
                if ((self.scheme == u'http' and port != 80) or
                        (self.scheme == u'https' and port != 443)):
                    host = "{0}:{1}".format(host, port)
                self.headers[u'host'] = host

        if u'accept-encoding' not in self.headers:
            self.headers[u'accept-encoding'] = u'identity'

        body = b""
        if self.data is not None:
            body = json.dumps(self.data).encode('utf-8')
            self.headers[u'content-type'] = u'application/json; charset=utf-8'
        elif self.fargs is not None:
            if any(isinstance(val, (tuple, list)) for val in self.fargs.values()):
                boundary = "---%s" % random.randrange(1e9)
                formParts = []
                for key, val in self.fargs.items():
                    if isinstance(val, (tuple, list)):
                        for v in val:
                            formParts.append('--{0}\r\n'
                                             'Content-Disposition: form-data; name="{1}"\r\n'
                                             'Content-Type: text/plain; charset=utf-8\r\n'
                                             '\r\n{2}'.format(boundary, key, v))
                    else:
                        formParts.append('--{0}\r\n'
                                         'Content-Disposition: form-data; name="{1}"\r\n'
                                         'Content-Type: text/plain; charset=utf-8\r\n'
                                         '\r\n{2}'.format(boundary, key, val))
                formParts.append('\r\n--{0}--'.format(boundary))
                form = "".join(formParts)
                body = form.encode(encoding='utf-8')
                self.headers[u'content-type'] = u'multipart/form-data; boundary={0}'.format(boundary)
            else:
                formParts = [u"{0}={1}".format(key, val) for key, val in self.fargs.items()]
                form = u'&'.join(formParts)
                form = quote_plus(form, '&=')
                body = form.encode(encoding='utf-8')
                self.headers[u'content-type'] = u'application/x-www-form-urlencoded; charset=utf-8'
        else:
            body = self.body

        if body and (u'content-length' not in self.headers):
            self.lines.append(httping.packHeader(u'Content-Length', str(len(body))))

        for name, value in self.headers.items():
            self.lines.append(httping.packHeader(name, value))

        self.lines.extend((b"", b""))
        self.head = CRLF.join(self.lines)
        self.msg = self.head + body
        return self.msg


class Respondent(httping.Parsent):
    """
    Nonblocking HTTP Client Respondent class (HTTP message parser only).
    """
    Retry = 100

    def __init__(self,
                 redirects=None,
                 redirectable=True,
                 events=None,
                 retry=None,
                 leid=None,
                 **kwa):
        super(Respondent, self).__init__(**kwa)

        self.status = None
        self.code = None
        self.reason = None

        self.redirectant = None
        self.redirected = None
        self.redirects = redirects if redirects is not None else []
        self.redirectable = True if redirectable else False

        self.evented = None
        self.events = events if events is not None else deque()
        self.retry = retry if retry is not None else self.Retry
        self.leid = None
        self.eventSource = None

    def reinit(self,
               redirectable=None,
               **kwa):
        super(Respondent, self).reinit(**kwa)
        if redirectable is not None:
            self.redirectable = True if redirectable else False
        self.status = None
        self.code = None
        self.reason = None
        self.evented = None

    def close(self):
        super(Respondent, self).close()
        if self.eventSource:
            self.eventSource.close()

    def checkPersisted(self):
        connection = self.headers.get("connection")
        if self.version == (1, 1):
            self.persisted = True
            connection = self.headers.get("connection")
            if connection and "close" in connection.lower():
                self.persisted = False
            elif (not self.chunked and self.length is None):
                self.persisted = False
        elif self.version == (1, 0):
            self.persisted = False
            if self.evented:
                self.persisted = True
            elif connection and "keep-alive" in connection.lower():
                self.persisted = True

    def parseHead(self):
        if self.headed:
            return
        self.headers = Hict()
        if self.closed and not self.msg:
            raise httping.PrematureClosure("Connection closed unexpectedly while parsing response head")
        lineParser = httping.parseLine(raw=self.msg, eols=(CRLF, LF), kind="status line")
        while True:
            line = next(lineParser)
            if line is not None:
                lineParser.close()
                break
            (yield None)

        if not line:
            raise httping.BadStatusLine(line)

        version, status, reason = httping.parseStatusLine(line)
        self.code = self.status = status
        self.reason = reason.strip()
        if version in ("HTTP/1.0", "HTTP/0.9"):
            self.version = (1, 0)
        elif version.startswith("HTTP/1."):
            self.version = (1, 1)
        else:
            raise httping.UnknownProtocol(version)

        leaderParser = httping.parseLeader(raw=self.msg, eols=(CRLF, LF), kind="leader header line")
        while True:
            if self.closed and not self.msg:
                raise httping.PrematureClosure("Connection closed unexpectedly while parsing response header")
            headers = next(leaderParser)
            if headers is not None:
                leaderParser.close()
                break
            (yield None)
        if self.headers is None:
            self.headers = Hict()
        self.headers.update(headers)

        transferEncoding = self.headers.get("transfer-encoding")
        if transferEncoding and transferEncoding.lower() == "chunked":
            self.chunked = True
        else:
            self.chunked = False

        contentLength = self.headers.get("content-length")
        if contentLength and not self.chunked:
            try:
                self.length = int(contentLength)
            except ValueError:
                self.length = None
            else:
                if self.length < 0:
                    self.length = None
        else:
            self.length = None

        if ((self.status == httping.NO_CONTENT or self.status == httping.NOT_MODIFIED) or
                (100 <= self.status < 200) or (self.method == "HEAD")):
            self.length = 0

        contentType = self.headers.get("content-type")
        if contentType:
            if u';' in contentType:
                contentType, sep, encoding = contentType.rpartition(u';')
                if encoding:
                    self.encoding = encoding

            if 'text/event-stream' in contentType.lower():
                self.evented = True
                self.eventSource = httping.EventSource(raw=self.body,
                                                       events=self.events,
                                                       dictable=self.dictable)
            else:
                self.evented = False

            if 'application/json' in contentType.lower():
                self.jsoned = True
            else:
                self.jsoned = False

        self.checkPersisted()

        if self.status in (httping.MULTIPLE_CHOICES,
                           httping.MOVED_PERMANENTLY,
                           httping.FOUND,
                           httping.SEE_OTHER,
                           httping.TEMPORARY_REDIRECT):
            self.redirectant = True

        self.headed = True
        yield True
        return

    def parseBody(self):
        if self.bodied:
            return

        if self.length and self.length < 0:
            raise ValueError("Invalid content length of {0}".format(self.length))

        del self.body[:]

        if self.chunked:
            self.parms = dict()
            while True:
                chunkParser = httping.parseChunk(raw=self.msg)
                while True:
                    if self.closed and not self.msg:
                        raise httping.PrematureClosure("Connection closed unexpectedly while parsing response body chunk")
                    result = next(chunkParser)
                    if result is not None:
                        chunkParser.close()
                        break
                    (yield None)

                size, parms, trails, chunk = result

                if parms:
                    self.parms.update(parms)

                if size:
                    self.body.extend(chunk)
                    if self.evented:
                        self.eventSource.parse()
                        if (self.eventSource.retry is not None and
                                self.retry != self.eventSource.retry):
                            self.retry = self.eventSource.retry
                        if (self.eventSource.leid is not None and
                                self.leid != self.eventSource.leid):
                            self.leid = self.eventSource.leid

                    if self.closed and not self.msg:
                        chunkParser.close()
                        break

                else:
                    if trails:
                        self.trails = trails
                    chunkParser.close()
                    break

        elif self.length is not None:
            while len(self.msg) < self.length:
                if self.closed and not self.msg:
                    raise httping.PrematureClosure("Connection closed unexpectedly while parsing response body")
                (yield None)

            self.body = self.msg[:self.length]
            del self.msg[:self.length]

        else:
            while True:
                if self.msg:
                    self.body.extend(self.msg[:])
                    del self.msg[:]

                if self.evented:
                    self.eventSource.parse()
                    if (self.eventSource.retry is not None and
                            self.retry != self.eventSource.retry):
                        self.retry = self.eventSource.retry
                    if (self.eventSource.leid is not None and
                            self.leid != self.eventSource.leid):
                        self.leid = self.eventSource.leid

                if self.closed and not self.msg:
                    break

                (yield None)

        self.length = len(self.body)
        self.bodied = True
        (yield True)
        return
