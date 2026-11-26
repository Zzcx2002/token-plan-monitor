"""Update a macOS generic password without removing its previous value first."""
import objc
from Foundation import NSBundle, NSData
from keyring.backends.macOS import Keyring
from keyring.errors import PasswordSetError

_security = NSBundle.bundleWithPath_("/System/Library/Frameworks/Security.framework")
_symbols = {}
objc.loadBundleVariables(_security, _symbols, [(name, b"@") for name in (
    "kSecClass", "kSecClassGenericPassword", "kSecAttrService", "kSecAttrAccount", "kSecValueData",
)])
objc.loadBundleFunctions(_security, _symbols, [("SecItemUpdate", b"i@@"), ("SecItemAdd", b"i@^v")])
_ITEM_NOT_FOUND = -25300
_DUPLICATE_ITEM = -25299


class AtomicKeyring(Keyring):
    def set_password(self, service, username, password):
        query = {_symbols["kSecClass"]: _symbols["kSecClassGenericPassword"],
                 _symbols["kSecAttrService"]: service, _symbols["kSecAttrAccount"]: username}
        encoded = password.encode("utf-8")
        value = {_symbols["kSecValueData"]: NSData.dataWithBytes_length_(encoded, len(encoded))}
        status = _symbols["SecItemUpdate"](query, value)
        if status == _ITEM_NOT_FOUND:
            status = _symbols["SecItemAdd"](query | value, 0)
            if status == _DUPLICATE_ITEM:
                status = _symbols["SecItemUpdate"](query, value)
        if status:
            raise PasswordSetError(f"Keychain update failed (OSStatus {status})")
