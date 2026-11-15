# Salow Workspace — API Contracts

## Endpoints

### Health & Readiness Probes
- `GET /health`: Liveness probe. Returns `{"status": "pass"}`. No DB check.
- `GET /ready`: Readiness probe. Executes a TiDB ping. Returns `{"status": "pass", "database": "connected"}` or HTTP 503.

### Authentication Endpoints
- `GET /auth/login/`: Renders login form.
- `POST /auth/login/`: Authenticates employee ID and password.
- `POST /auth/logout/`: Invalidates active session (POST-only).

### Attendance Endpoints
- `GET /attendance/`: Renders attendance dashboard and panel.
- `POST /attendance/check-in/`: Submits check-in challenge response.
- `POST /attendance/check-out/`: Submits check-out request.
