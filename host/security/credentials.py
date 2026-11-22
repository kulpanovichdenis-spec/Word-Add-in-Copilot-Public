"""Хранение API-ключей в Windows Credential Manager, не в config.json."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

OPENROUTER_TARGET = "WordAddinCopilotPublic/OpenRouter"
DADATA_API_TARGET = "WordAddinCopilotPublic/DaDataApi"
DADATA_SECRET_TARGET = "WordAddinCopilotPublic/DaDataSecret"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def _advapi():
    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    library.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    library.CredReadW.restype = wintypes.BOOL
    library.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    library.CredWriteW.restype = wintypes.BOOL
    library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    library.CredDeleteW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]
    return library


class CredentialStore:
    def _get(self, target: str, env_name: str) -> str:
        env = os.environ.get(env_name, "").strip()
        if env:
            return env
        if os.name != "nt":
            return ""
        advapi = _advapi()
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            return ""
        try:
            cred = pointer.contents
            blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return blob.decode("utf-16-le")
        finally:
            advapi.CredFree(pointer)

    def _set(self, target: str, key: str, user_name: str, empty_message: str) -> None:
        key = key.strip()
        if not key:
            raise ValueError(empty_message)
        if os.name != "nt":
            raise OSError("Безопасное хранение токена поддерживается только в Windows")
        blob = key.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = _CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=target, CredentialBlobSize=len(blob),
                            CredentialBlob=buffer, Persist=CRED_PERSIST_LOCAL_MACHINE,
                            UserName=user_name)
        advapi = _advapi()
        if not advapi.CredWriteW(ctypes.byref(cred), 0):
            raise ctypes.WinError()

    def _delete(self, target: str) -> None:
        if os.name != "nt":
            return
        advapi = _advapi()
        if not advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
            error = ctypes.get_last_error()
            if error != 1168:  # ERROR_NOT_FOUND
                raise ctypes.WinError(error)

    def get_openrouter_key(self) -> str:
        return self._get(OPENROUTER_TARGET, "WAC_PUBLIC_OPENROUTER_API_KEY")

    def set_openrouter_key(self, key: str) -> None:
        self._set(OPENROUTER_TARGET, key, "OpenRouter API key", "Введите токен OpenRouter")

    def delete_openrouter_key(self) -> None:
        self._delete(OPENROUTER_TARGET)

    def has_openrouter_key(self) -> bool:
        return bool(self.get_openrouter_key())

    def get_dadata_api_key(self) -> str:
        return self._get(DADATA_API_TARGET, "WAC_PUBLIC_DADATA_API_KEY")

    def get_dadata_secret_key(self) -> str:
        return self._get(DADATA_SECRET_TARGET, "WAC_PUBLIC_DADATA_SECRET_KEY")

    def set_dadata_keys(self, api_key: str, secret_key: str) -> None:
        if not api_key.strip() or not secret_key.strip():
            raise ValueError("Введите API-ключ и секретный ключ DaData")
        self._set(DADATA_API_TARGET, api_key, "DaData API key", "Введите API-ключ DaData")
        try:
            self._set(DADATA_SECRET_TARGET, secret_key, "DaData secret key", "Введите секретный ключ DaData")
        except Exception:
            self._delete(DADATA_API_TARGET)
            raise

    def delete_dadata_keys(self) -> None:
        self._delete(DADATA_API_TARGET)
        self._delete(DADATA_SECRET_TARGET)

    def has_dadata_keys(self) -> bool:
        return bool(self.get_dadata_api_key() and self.get_dadata_secret_key())
