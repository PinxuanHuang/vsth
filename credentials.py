"""Protect saved credentials with the current Windows user's DPAPI key."""
import base64
import ctypes
import os
from ctypes import wintypes


def _transform(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('密碼加密儲存僅支援 Windows。')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = Blob(len(data), source_buffer), Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('無法使用目前 Windows 使用者加解密登入密碼，請重新設定。')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def protect_password(value):
    return base64.b64encode(_transform(value.encode('utf-8'))).decode('ascii') if value else ''


def unprotect_password(value):
    if not value:
        return ''
    try:
        return _transform(base64.b64decode(value, validate=True), decrypt=True).decode('utf-8')
    except Exception:
        raise ValueError('無法讀取已儲存的登入密碼，請使用原 Windows 使用者或重新設定。') from None
