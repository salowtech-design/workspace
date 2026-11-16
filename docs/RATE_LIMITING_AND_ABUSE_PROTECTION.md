# Salow Workspace — Distributed Rate Limiting & Abuse Protection

**Authoritative Specification**: Salow Workspace IAM Architecture  
**Document Status**: Official Engineering Architecture Guide  
**Applies To**: `apps.core.middleware.RateLimitMiddleware`, `apps.core.models.RateLimitRecord`  

---

## 1. Threat Model & Mitigation Strategy

Salow Workspace protects against automated credential abuse, distributed denial-of-service (DDoS), and brute-force attacks across all sensitive authentication surfaces:
1. **Password Guessing & Credential Stuffing**: Thwarted by IP + Identifier multi-dimensional sliding windows.
2. **TOTP & Recovery Code Enumeration**: Thwarted by aggressive endpoint-specific rate thresholds.
3. **Account Lockout DoS Attacks**: Mitigated by progressive delays rather than permanent unrecoverable account lockouts.

---

## 2. Distributed Sliding-Window Architecture

To maintain consistent rate limit quotas across multiple Django worker processes and application instances without relying exclusively on single-node in-memory caches, Salow Workspace uses **persistent database-backed rate limit records** with in-memory caching:

### 2.1 Model Specification (`RateLimitRecord`)
- **`key`**: Composite hash of IP address, target endpoint, and user identifier (`VARCHAR(255)`, indexed).
- **`count`**: Number of requests in the current window.
- **`window_start`**: Timestamp marking the beginning of the active rate window.
- **`expires_at`**: Timestamp after which the record is automatically purged.

### 2.2 Rate Limit Policies by Endpoint

| Endpoint | Target Surface | Window Duration | Max Requests | Response on Breach |
|---|---|---|---|---|
| `/accounts/login/` | Password Submission | 60 seconds | 5 requests | HTTP 429 Too Many Requests |
| `/accounts/login/totp/` | TOTP Challenge | 60 seconds | 5 requests | HTTP 429 Too Many Requests |
| `/accounts/activate/` | Activation Code Verification | 300 seconds | 5 requests | HTTP 429 Too Many Requests |
| `/accounts/recovery/code/` | Single-Use Recovery Code | 300 seconds | 3 requests | HTTP 429 Too Many Requests |
| `/accounts/passkeys/login/` | Passkey Assertion | 60 seconds | 10 requests | HTTP 429 Too Many Requests |
| Global Workspace Pages | Standard Browsing | 60 seconds | 120 requests | HTTP 429 Too Many Requests |

---

## 3. Trusted Reverse Proxy Header Coordination

In production environments behind reverse proxies or load balancers (e.g. AWS ALB, Cloudflare, Nginx):
1. **No Blind Trust of `X-Forwarded-For`**: Attackers can spoof arbitrary IP addresses in `X-Forwarded-For` headers.
2. **`SECURE_PROXY_SSL_HEADER`**: Configured to `('HTTP_X_FORWARDED_PROTO', 'https')`.
3. **`TRUSTED_PROXIES` Configuration**: The application parses client IP addresses from the rightmost untrusted hop in `X-Forwarded-For`, ensuring rate limits apply to the actual client machine rather than upstream reverse proxies.

---

## 4. Bot Mitigation & Generic Error Responses
- **Response Uniformity**: Failed login attempts, invalid recovery codes, and missing accounts all return generic error text: *"Invalid employee ID or credentials. Please verify your details."*
- **Error Code Consistency**: No error messages indicate whether an employee ID exists or whether an account has MFA enabled.
- **Security Headers**: Standard headers `Retry-After: {seconds}` are emitted on HTTP 429 responses to instruct legitimate clients when to retry.
