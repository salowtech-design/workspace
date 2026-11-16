# Salow Workspace — Cryptography & Key Management Architecture

**Authoritative Specification**: Salow Workspace IAM Architecture  
**Document Status**: Official Engineering Architecture Guide  
**Applies To**: `apps.accounts.auth.crypto`, `config.settings`  

---

## 1. Cryptographic Standards & Purpose Matrix

Salow Workspace strictly enforces modern cryptographic primitives, avoiding outdated or weak ciphers (such as MD5, SHA-1 for storage, or DES/3DES):

| Security Purpose | Cryptographic Algorithm | Key Derivation / Envelope | Library & Standard |
|---|---|---|---|
| **Password Storage** | Argon2id ($t=2, m=19\,\text{MiB}, p=1$) | Unique 16B salt per user | `argon2-cffi` (RFC 9106) |
| **Activation Tokens** | Crockford Base32 (10 chars) | Keyed HMAC-SHA256 hash | Pure Python / `hashlib` |
| **Recovery Codes** | Alphanumeric (10 chars) | Keyed HMAC-SHA256 hash | Pure Python / `hashlib` |
| **TOTP Secret Storage** | AES-256-GCM (256-bit key) | 12B random nonce + 16B auth tag | `cryptography.hazmat` (NIST SP 800-38D) |
| **Passkey Public Keys** | ES256 (ECDSA P-256) / RS256 | COSE public key format | `webauthn` (FIDO2 / W3C Level 3) |
| **Certificate Integrity** | SHA-256 | Cryptographic document digest | `hashlib` |
| **Transport Layer** | TLS 1.2+ (TLS 1.3 preferred) | Ephemeral Diffie-Hellman | OpenSSL / Reverse Proxy |

---

## 2. Multi-Version KeyRing Architecture (`KeyRing`)

To allow seamless cryptographic key rotation without database downtime or data re-encryption locks:

### 2.1 KeyRing Layout
```python
class KeyRing:
    """
    Versioned KeyRing supporting authenticated encryption with AES-256-GCM.
    Encryption always uses the active primary key.
    Decryption inspects the key prefix to decrypt older records.
    """
    DEFAULT_KEY_ID = "k1"
```

### 2.2 Ciphertext Envelope Structure
Every encrypted payload written to the database contains a 4-byte key identifier header:
$$\text{Envelope} = \underbrace{\text{Key ID (4B)}}_{\text{"k1::"}} \parallel \underbrace{\text{Nonce (12B)}}_{\text{os.urandom(12)}} \parallel \underbrace{\text{Ciphertext}}_{\text{AES-GCM}} \parallel \underbrace{\text{Auth Tag (16B)}}_{\text{GCM Integrity Tag}}$$

### 2.3 Key Rotation Procedure
1. Add the new 256-bit AES key to the environment as `SALOW_KEYRING_K2`.
2. Update the default encryption key pointer: `DEFAULT_KEY_ID = "k2"`.
3. Existing records prefixed with `k1::` continue to decrypt seamlessly using the retained `k1` key.
4. Run a background re-encryption task if full migration of historical records is desired.

---

## 3. Key Isolation & Protection Rules
1. **Zero Key Hardcoding**: No cryptographic keys are embedded in Python source files, migrations, or Git commits.
2. **Environment & Secrets Manager**: Production keys are loaded through environment variables (`SALOW_KEYRING_*`, `SECRET_KEY`, `HMAC_KEY`) injected by enterprise secret vaults.
3. **Fail-Closed Operations**: If a key is missing or an authentication tag fails verification during decryption, the operation raises `DecryptionError` immediately and refuses to process unverified plaintext.
4. **Redacted Logging**: No key identifiers, keys, or nonces are ever written to stdout, stderr, or application log handlers.
