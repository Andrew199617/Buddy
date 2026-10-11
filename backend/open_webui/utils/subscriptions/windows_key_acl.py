"""Owner-only access for the Buddy Runner key on Windows.

POSIX keeps the key private with modes 0700 and 0600. On Windows the owner and
the DACL decide access instead, so the Runner reads both. It refuses an
existing state folder, key folder, or key that other accounts can use, and
never repairs one; the error names the commands that fix it. New folders and
keys start with a protected DACL, so they never inherit a shared parent's.

Unlike POSIX, higher folders are not checked, because ordinary Windows roots
such as C:\\ let every user add folders. The Runner's pins keep them from being
renamed while the key is used, and a folder or key planted in place of the
real one belongs to another owner, which is refused.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

SYSTEM = 'S-1-5-18'
ADMINISTRATORS = 'S-1-5-32-544'
OWNER_RIGHTS = 'S-1-3-4'
CREATOR_OWNER = 'S-1-3-0'
EVERYONE = 'S-1-1-0'
# The DACL Python gives os.mkdir(mode=0o700) on Windows: full control for
# SYSTEM, Administrators, and the object's owner, and nothing inherited.
PRIVATE_DIRECTORY_SDDL = 'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)'
PRIVATE_FILE_SDDL = 'D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;OW)'

ACCESS_ALLOWED_ACE_TYPE = 0
ACCESS_DENIED_ACE_TYPE = 1
ACCESS_ALLOWED_CALLBACK_ACE_TYPE = 9
ACCESS_DENIED_CALLBACK_ACE_TYPE = 10

# Rights that let an account add, replace, or remove what a folder holds, or
# change who may. A key folder may be readable by others, as POSIX allows 0755.
FILE_ADD_FILE = 0x2
FILE_ADD_SUBDIRECTORY = 0x4
FILE_WRITE_EA = 0x10
FILE_DELETE_CHILD = 0x40
FILE_WRITE_ATTRIBUTES = 0x100
DELETE = 0x10000
WRITE_DAC = 0x40000
WRITE_OWNER = 0x80000
GENERIC_ALL = 0x10000000
GENERIC_WRITE = 0x40000000
CHANGE_RIGHTS = (
    FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY | FILE_WRITE_EA | FILE_DELETE_CHILD
    | FILE_WRITE_ATTRIBUTES | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_ALL | GENERIC_WRITE
)


@dataclass
class AccessEntry:
    """One DACL entry. An empty sid marks an entry type this check does not read."""

    sid: str
    allowed: bool
    mask: int


@dataclass
class Security:
    owner: str
    # None when the object has no DACL, which gives everyone full access.
    entries: list[AccessEntry] | None


def trusted_accounts(user_sid: str) -> set[str]:
    """Accounts that may hold the key: the Runner's own, and the host's administrators."""
    return {user_sid, SYSTEM, ADMINISTRATORS, OWNER_RIGHTS, CREATOR_OWNER}


def other_accounts(security: Security, user_sid: str, *, private: bool) -> list[str]:
    """Accounts other than the trusted ones that can use (private) or change the object."""
    if security.entries is None:
        return [EVERYONE]
    trusted = trusted_accounts(user_sid)
    others = []
    for entry in security.entries:
        if not entry.allowed or entry.sid in trusted:
            continue
        if not private and not entry.mask & CHANGE_RIGHTS:
            continue
        if entry.sid not in others:
            others.append(entry.sid)
    return others


def owner_is_trusted(security: Security, user_sid: str) -> bool:
    return security.owner in (user_sid, SYSTEM, ADMINISTRATORS)


def _account_list(sids: list[str]) -> str:
    names = []
    for sid in sids:
        if sid:
            names.append(account_name(sid))
        else:
            names.append('an access entry of an unrecognized type')
    return ', '.join(names)


def access_fix_commands(path: Path, user_sid: str, *, directory: bool) -> list[str]:
    """The icacls commands that leave only the user's own access, in the order they run.

    icacls refuses to name an entry for a deleted or unmapped account, and
    then changes nothing, so the commands never list the other accounts.
    /reset drops every entry set on the object itself, and /inheritance:r
    then drops the inherited ones while granting the user full control.
    """
    if directory:
        grant = f'"*{user_sid}:(OI)(CI)F"'
    else:
        grant = f'"*{user_sid}:F"'
    reset_explicit_entries = f'icacls "{path}" /reset'
    keep_only_user = f'icacls "{path}" /inheritance:r /grant:r {grant}'
    return [reset_explicit_entries, keep_only_user]


def access_message(path: Path, what: str, others: list[str], user_sid: str, *, directory: bool, private: bool) -> str:
    reset_explicit_entries, keep_only_user = access_fix_commands(path, user_sid, directory=directory)
    if private:
        problem = f'The Runner {what} {path} can be used by other Windows accounts: {_account_list(others)}.'
    else:
        problem = f'The Runner {what} {path} can be changed by other Windows accounts: {_account_list(others)}.'
    return (
        f'{problem} Make it private to your account by running {reset_explicit_entries} '
        f'and then {keep_only_user} '
        f'Then check that icacls "{path}" lists only your account, SYSTEM or Administrators, '
        'and start the Runner again.'
    )


def owner_message(path: Path, what: str, owner: str, user_sid: str) -> str:
    return (
        f'The Runner {what} {path} is owned by {account_name(owner)}, not by your account. '
        'If it is yours, make your account its owner from an administrator Command Prompt: '
        f'icacls "{path}" /setowner *{user_sid} Then start the Runner again.'
    )


def check_private(path: Path, what: str, *, directory: bool, descriptor: int | None = None) -> None:
    """Refuse a state folder or key that any other account can use, like POSIX 0700 and 0600."""
    _check(path, what, directory=directory, private=True, descriptor=descriptor)


def check_key_folder(path: Path) -> None:
    """Refuse a separate key folder that other accounts can change, like POSIX group or other write."""
    _check(path, 'key folder', directory=True, private=False, descriptor=None)


def _check(path: Path, what: str, *, directory: bool, private: bool, descriptor: int | None) -> None:
    user_sid = current_user_sid()
    security = read_security(path, descriptor)
    if not owner_is_trusted(security, user_sid):
        raise ValueError(owner_message(path, what, security.owner, user_sid))
    others = other_accounts(security, user_sid, private=private)
    if others:
        raise ValueError(access_message(path, what, others, user_sid, directory=directory, private=private))


if sys.platform == 'win32':
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ('length', wintypes.DWORD),
            ('descriptor', ctypes.c_void_p),
            ('inherit_handle', wintypes.BOOL),
        ]

    class _AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ('ace_count', wintypes.DWORD),
            ('bytes_in_use', wintypes.DWORD),
            ('bytes_free', wintypes.DWORD),
        ]

    class _AceHeader(ctypes.Structure):
        _fields_ = [('type', ctypes.c_ubyte), ('flags', ctypes.c_ubyte), ('size', ctypes.c_ushort)]

    _POINTER = ctypes.POINTER(ctypes.c_void_p)
    _advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, _POINTER, _POINTER, _POINTER, _POINTER, _POINTER,
    ]
    _advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, _POINTER, _POINTER, _POINTER, _POINTER, _POINTER,
    ]
    _advapi.GetSecurityInfo.restype = wintypes.DWORD
    _advapi.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    _advapi.GetAclInformation.restype = wintypes.BOOL
    _advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, _POINTER]
    _advapi.GetAce.restype = wintypes.BOOL
    _advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    _advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, _POINTER]
    _advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi.LookupAccountSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_int),
    ]
    _advapi.LookupAccountSidW.restype = wintypes.BOOL
    _advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi.OpenProcessToken.restype = wintypes.BOOL
    _advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi.GetTokenInformation.restype = wintypes.BOOL
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, _POINTER, ctypes.POINTER(wintypes.ULONG),
    ]
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel.LocalFree.restype = ctypes.c_void_p
    _kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_SecurityAttributes)]
    _kernel.CreateDirectoryW.restype = wintypes.BOOL
    _kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel.CreateFileW.restype = wintypes.HANDLE

    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x1
    _DACL_SECURITY_INFORMATION = 0x4
    _ACL_SIZE_INFORMATION_CLASS = 2
    _TOKEN_QUERY = 0x8
    _TOKEN_USER_CLASS = 1
    _SDDL_REVISION_1 = 1
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _FILE_SHARE_READ = 1
    _CREATE_NEW = 1
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _ERROR_FILE_EXISTS = 80
    _ERROR_ALREADY_EXISTS = 183
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    def _raise_last_error():
        raise ctypes.WinError(ctypes.get_last_error())

    def _sid_string(sid) -> str:
        text = wintypes.LPWSTR()
        if not _advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            _raise_last_error()
        try:
            return text.value
        finally:
            _kernel.LocalFree(text)

    def current_user_sid() -> str:
        token = wintypes.HANDLE()
        if not _advapi.OpenProcessToken(_kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
            _raise_last_error()
        try:
            needed = wintypes.DWORD()
            _advapi.GetTokenInformation(token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            if not _advapi.GetTokenInformation(token, _TOKEN_USER_CLASS, buffer, needed, ctypes.byref(needed)):
                _raise_last_error()
            # TOKEN_USER begins with the user's SID pointer.
            return _sid_string(ctypes.c_void_p.from_buffer(buffer).value)
        finally:
            _kernel.CloseHandle(token)

    def account_name(sid: str) -> str:
        """DOMAIN\\name for a SID, or the SID itself when Windows cannot name it."""
        binary = ctypes.c_void_p()
        if not _advapi.ConvertStringSidToSidW(sid, ctypes.byref(binary)):
            return sid
        try:
            name = ctypes.create_unicode_buffer(256)
            domain = ctypes.create_unicode_buffer(256)
            name_length = wintypes.DWORD(len(name))
            domain_length = wintypes.DWORD(len(domain))
            use = ctypes.c_int()
            if not _advapi.LookupAccountSidW(
                None, binary, name, ctypes.byref(name_length), domain, ctypes.byref(domain_length), ctypes.byref(use)
            ):
                return sid
        finally:
            _kernel.LocalFree(binary)
        if domain.value:
            return f'{domain.value}\\{name.value}'
        return name.value

    def _access_entries(dacl) -> list[AccessEntry]:
        size = _AclSizeInformation()
        if not _advapi.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), _ACL_SIZE_INFORMATION_CLASS):
            _raise_last_error()
        entries = []
        for index in range(size.ace_count):
            ace = ctypes.c_void_p()
            if not _advapi.GetAce(dacl, index, ctypes.byref(ace)):
                _raise_last_error()
            header = _AceHeader.from_address(ace.value)
            if header.type in (ACCESS_DENIED_ACE_TYPE, ACCESS_DENIED_CALLBACK_ACE_TYPE):
                # A deny entry only narrows access.
                continue
            if header.type not in (ACCESS_ALLOWED_ACE_TYPE, ACCESS_ALLOWED_CALLBACK_ACE_TYPE):
                # Object entries put their SID elsewhere; never let one pass unread.
                entries.append(AccessEntry('', allowed=True, mask=GENERIC_ALL))
                continue
            # An allowed entry is its header, an access mask, then the SID.
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            sid = _sid_string(ctypes.c_void_p(ace.value + 8))
            # Inherit-only entries still decide what new keys and folders get.
            entries.append(AccessEntry(sid, allowed=True, mask=mask))
        return entries

    def read_security(path: Path, descriptor: int | None = None) -> Security:
        """The owner and DACL of an open key, or of a folder the caller keeps pinned."""
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        security_descriptor = ctypes.c_void_p()
        information = _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION
        if descriptor is not None:
            status = _advapi.GetSecurityInfo(
                msvcrt.get_osfhandle(descriptor), _SE_FILE_OBJECT, information,
                ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(security_descriptor),
            )
        else:
            status = _advapi.GetNamedSecurityInfoW(
                str(path), _SE_FILE_OBJECT, information,
                ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(security_descriptor),
            )
        if status:
            raise ctypes.WinError(status)
        try:
            entries = None
            if dacl.value:
                entries = _access_entries(dacl)
            return Security(owner=_sid_string(owner), entries=entries)
        finally:
            _kernel.LocalFree(security_descriptor)

    def _private_attributes(sddl: str):
        descriptor = ctypes.c_void_p()
        if not _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            _raise_last_error()
        attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        return attributes, descriptor

    def create_private_directory(path: Path) -> None:
        """Create one folder that only its owner, SYSTEM and Administrators can use."""
        attributes, descriptor = _private_attributes(PRIVATE_DIRECTORY_SDDL)
        try:
            if not _kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                error = ctypes.get_last_error()
                if error == _ERROR_ALREADY_EXISTS:
                    raise FileExistsError(error, 'The Runner folder already exists', str(path))
                raise ctypes.WinError(error)
        finally:
            _kernel.LocalFree(descriptor)

    def create_private_file(path: Path) -> int:
        """Create a new key file that is private from its first moment; returns a write descriptor."""
        attributes, descriptor = _private_attributes(PRIVATE_FILE_SDDL)
        try:
            handle = _kernel.CreateFileW(
                str(path), _GENERIC_READ | _GENERIC_WRITE, _FILE_SHARE_READ, ctypes.byref(attributes),
                _CREATE_NEW, _FILE_ATTRIBUTE_NORMAL, None,
            )
        finally:
            _kernel.LocalFree(descriptor)
        if handle == _INVALID_HANDLE or handle is None:
            error = ctypes.get_last_error()
            if error in (_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS):
                raise FileExistsError(error, 'The Runner key already exists', str(path))
            raise ctypes.WinError(error)
        try:
            # open_osfhandle takes ownership of the handle on success.
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except OSError:
            _kernel.CloseHandle(handle)
            raise
else:
    def account_name(sid: str) -> str:
        return sid
