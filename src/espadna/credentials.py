"""Windows user-bound encrypted token file; environment fallback for other hosts."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import sys
import uuid
from .packs import private_path

HEADER = b'ESPADNA-DPAPI-1\x00'
ENTROPY = b'espadna-sim-content-agent/telegram-token/v1'


def validate_token(token):
    if not isinstance(token, str) or not re.fullmatch(r'[0-9]{5,16}:[A-Za-z0-9_-]{20,200}', token.strip()):
        raise ValueError('invalid Telegram token format; token is never printed')
    return token.strip()


def _crypt(raw, protect):
    if sys.platform != 'win32':
        raise ValueError('Windows encrypted token files require the same Windows user; use a private environment secret on other hosts')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    source_buffer = ctypes.create_string_buffer(raw)
    entropy_buffer = ctypes.create_string_buffer(ENTROPY)
    source = Blob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if protect:
        operation = library.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = 'Espadna Sim Telegram credential'
    else:
        operation = library.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = None
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), description, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
        raise ValueError('Windows credential encryption/decryption failed for this user')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def save_token(path, token):
    path = private_path(path)
    token = validate_token(token)
    encrypted = HEADER + _crypt(token.encode('ascii'), True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as file:
            file.write(encrypted)
        temporary.replace(path)  # Explicit Save action supports token rotation.
    finally:
        temporary.unlink(missing_ok=True)


def read_token(path):
    path = private_path(path)
    with path.open('rb') as file:
        data = file.read(16385)
    if len(data) > 16384 or not data.startswith(HEADER):
        raise ValueError('invalid encrypted credential file')
    try:
        return validate_token(_crypt(data[len(HEADER):], False).decode('ascii'))
    except UnicodeError:
        raise ValueError('invalid encrypted credential data') from None


def get_token(database):
    env = os.environ.get('TELEGRAM_BOT_TOKEN')
    if env:
        return validate_token(env)
    path = Path(os.environ.get('ESPADNA_TOKEN_FILE') or Path(database).resolve().with_name('telegram-token.dpapi'))
    return read_token(path) if path.is_file() else None
