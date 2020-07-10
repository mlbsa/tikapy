#!/usr/bin/python3

#
# Copyright (c) 2015, VSHN AG, info@vshn.ch
# Licensed under "BSD 3-Clause". See LICENSE file.
#
# Authors:
#  - Andre Keller <andre.keller@vshn.ch>
#

"""
MikroTik RouterOS Python API Clients
"""

import logging
import socket
import ssl
from .api import ApiError, ApiRos, ApiUnrecoverableError

## Imports for check to determine the OS
import os, sys

LOG = logging.getLogger(__name__)


class ClientError(Exception):
    """
    Exception returned when a API client interaction fails.
    """
    pass


class TikapyBaseClient():
    """
    Base class for functions shared between the SSL and non-SSL API client
    """

    def __init__(self):
        """
        Constructor. Initialize instance variables.
        """
        self._address = None
        self._port = None
        self._base_sock = None
        self._sock = None
        self._api = None

    @property
    def address(self):
        """
        Address of the remote API.
        :return: string - address of the remote API.
        """
        return self._address

    @address.setter
    def address(self, value):
        """
        Address of the remote API.
        """
        self._address = value

    @property
    def port(self):
        """
        Port of the remote API.
        :return:
        """
        return self._port

    @port.setter
    def port(self, value):
        """
        Port of the remote API.
        :raises: ValueError - if invalid port number is specified
        """
        try:
            if not 0 < value < 65536:
                raise ValueError('%d is not a valid port number' % value)
            self._port = value
        except ValueError as exc:
            raise ValueError('invalid port number specified') from exc

    def __del__(self):
        """
        Destructor. Tries to disconnect socket if it is still open.
        """
        self.disconnect()

    def disconnect(self):
        """
        Disconnect/closes open sockets.
        """
        try:
            if self._sock:
                self._sock.close()
        except socket.error:
            pass
        try:
            if self._base_sock:
                self._base_sock.close()
        except socket.error:
            pass

    def _connect_socket(self, timeOut):
        """
        Connect the base socket.
        If self.address is a hostname, this function will loop through
        all available addresses until it can establish a connection.
        :param timeOut: Time set for the timeout for the API connections
        attempt.
        :raises: ClientError - if address/port has not been set
                             - if no connection to remote socket
                               could be established.
        """
        if not self.address:
            raise ClientError('address has not been set')
        if not self.port:
            raise ClientError('address has not been set')

        for family, socktype, proto, _, sockaddr in \
                socket.getaddrinfo(self.address,
                                   self.port,
                                   socket.AF_UNSPEC,
                                   socket.SOCK_STREAM):

            try:
                self._base_sock = socket.socket(family, socktype, proto)
                self._base_sock.settimeout(timeOut)
            except socket.error:
                self._base_sock = None
                continue

            try:
                self._base_sock.connect(sockaddr)
            except socket.error:
                self._base_sock.close()
                self._base_sock = None
                continue
            break

        if self._base_sock is None:
            ## Disable the log attempt as it creates unneeded forced info
            ## to shown on the screen with no option to disable this.
            # LOG.error('could not open socket')
            raise ClientError('could not open socket')

    def _connect(self, timeOut):
        """
        Connects the socket and stores the result in self._sock.
        This is meant to be sub-classed if a socket needs to be wrapped,
        f.e. with an SSL handler.
        :param timeOut: Time set for the timeout for the API connections
        attempt.
        """
        self._connect_socket(timeOut)
        self._sock = self._base_sock

    def login(self, user, password, timeOut=60, allow_insecure_auth_without_tls=False):
        """
        Connects to the API and tries to login the user.
        :param user: Username for API connections
        :param password: Password for API connections
        :param timeOut: Time set for the timeout for the API connections
        attempt. Default is 60 seconds.
        :param allow_insecure_auth_without_tls: Boolean to allow insecure
        authentication. Default is False.
        :raises: ClientError - if login failed
        """
        self._connect(timeOut)
        self._api = ApiRos(self._sock)
        try:
            socket_is_tls = hasattr(self._sock, "getpeercert")
            send_plain_password = (socket_is_tls or allow_insecure_auth_without_tls)
            self._api.login(user, password, send_plain_password)
        except (ApiError, ApiUnrecoverableError) as exc:
            raise ClientError('could not login') from exc

    def talk(self, words):
        """
        Send command sequence to the API.
        :param words: List of command sequences to send to the API
        :returns: dict containing response or ID.
        :raises: ClientError - If client could not talk to remote API.
                 ValueError - On invalid input.
        """
        if isinstance(words, list) and all(isinstance(x, str) for x in words):
            try:
                return self.tik_to_json(self._api.talk(words))
            except (ApiError, ApiUnrecoverableError) as exc:
                raise ClientError('could not talk to api') from exc
        raise ValueError('words needs to be a list of strings')

    @staticmethod
    def tik_to_json(tikoutput):
        """
        Converts MikroTik RouterOS output to python dict / JSON.
        :param tikoutput:
        :return: dict containing response or ID.
        """
        try:
            if tikoutput[0][0] == '!done':
                return tikoutput[0][1]['ret']
        except (IndexError, KeyError):
            pass
        try:
            return {
                d['.id'][1:]: d for d in ([x[1] for x in tikoutput])
                if '.id' in d.keys()}
        except (TypeError, IndexError) as exc:
            raise ClientError('unable to convert api output to json') from exc
       

class TikapyClient(TikapyBaseClient):
    """
    RouterOS API Client.
    """

    def __init__(self, address, port=8728):
        """
        Initialize client.
        :param address: Remote device address (maybe a hostname)
        :param port: Remote device port (defaults to 8728)
        """
        super().__init__()
        self.address = address
        self.port = port


class TikapySslClient(TikapyBaseClient):
    """
    RouterOS SSL API Client.
    """

    def __init__(self, address, port=8729, verify_cert=True,
                 verify_addr=True):
        """
        Initialize client.
        :param address: Remote device address (maybe a hostname)
        :param port: Remote device port (defaults to 8728)
        :param verify_cert: Verify device certificate against system CAs
        :param verify_addr: Verify provided address against certificate
        """
        super().__init__()
        self.address = address
        self.port = port
        self.verify_cert = verify_cert
        self.verify_addr = verify_addr

    def _connect(self, timeOut):
        """
        Connects a ssl socket.
        :param timeOut: Time set for the timeout for the API connections
        attempt.
        """
        self._connect_socket(timeOut)
        try:            
            ## Added due to SSLv3 errors happening. This is even though ssl.create_default_context()
            ## is suppose to set OP_NO_SSLv3, but it still tries to use SSLv3 and subsequently gets an
            ## error. This was found on Windows and Linux environments. To bypass this you require to 
            ## use ADH as the cipher with SECLEVEL set to 0.
            if (os.name == "nt") and ("win" in sys.platform):
                ctx = ssl.create_default_context()                            
                if not self.verify_cert:
                    ctx.verify_mode = ssl.CERT_OPTIONAL
                if not self.verify_addr:
                    ctx.check_hostname = False

                ctx.set_ciphers('ADH:@SECLEVEL=0')
            else:
                ctx = ssl.create_default_context()
                ctx.verify_mode = ssl.CERT_OPTIONAL
                ctx.check_hostname = False
                ctx.set_ciphers('ADH')
            self._sock = ctx.wrap_socket(self._base_sock, server_hostname=self.address)
        except ssl.SSLError:
            ## Disable the log attempt as it creates unneeded forced info
            ## to shown on the screen with no option to disable this.
            # LOG.error('could not establish SSL connection')
            raise ClientError('could not establish SSL connection')