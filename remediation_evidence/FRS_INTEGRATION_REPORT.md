# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Face Recognition System (FRS) Architecture & Integration Report (Workstream E)

- **Document ID**: `REMED-FRS-INTEG-01`
- **Date**: 20 November 2026
- **Status**: ARCHITECTURE COMPLETE / BLOCKED (Hardware Camera Sensor)
- **Security Priority**: P0 (Gate 5)

---

### 1. FRS Inference Pipeline Architecture

The Salow Workspace Face Recognition System operates as a privacy-preserving, server-side CPU inference pipeline implemented in `apps/face/engine/pipeline.py`.

```
[Camera Stream / Client Frames]
             │ (Volatile Memory Only - Zero Disk/DB Retention)
             ▼
[Stage 1: Face Detection] ───────► YuNet (Apache-2.0, OpenCV Foundation)
             │                     - 320x320 RGB frame
             │                     - Bounding box [x,y,w,h] + 5 facial landmarks
             ▼
[Stage 2: Quality Assessment] ───► check_face_quality()
             │                     - Brightness: [40, 220]
             │                     - Sharpness (Laplacian var): >= 45.0
             │                     - Resolution: min 80x80px; Tilt: |yaw|, |pitch| <= 30°
             ▼
[Stage 3: Passive PAD] ──────────► MiniFASNet (Apache-2.0 / BSD, MiniFASNetV2)
             │                     - Multi-scale patch analysis (80x80 & 128x128)
             │                     - Liveness threshold: >= 0.65
             ▼
[Stage 4: Active Motion] ────────► verify_active_challenge()
             │                     - Server-issued random challenge (TURN_LEFT, TURN_RIGHT, NOD_DOWN)
             │                     - Enforces angular delta >= 14.0° within 45s TTL
             ▼
[Stage 5: 1:1 Embedding Match] ──► SFace (Apache-2.0, CASIA / OpenCV)
             │                     - 128-dimensional L2-normalized embedding vector
             │                     - Cosine similarity against decrypted template >= 0.70
             ▼
[Output: BiometricAssertion] ────► Cryptographic transaction assertion in DB
```

---

### 2. Model Licensing Compliance

All deep learning model architectures selected for Salow Workspace adhere to permissive open-source licenses:
- **YuNet**: `Apache-2.0` (OpenCV Foundation).
- **SFace**: `Apache-2.0` (CASIA / OpenCV Foundation).
- **MiniFASNet**: `Apache-2.0 / BSD` (MiniFASNetV2 Open Source Biometrics).

---

### 3. Security Invariants & Guardrails

1. **Strict 1:1 Verification**:
   - Verification is exclusively performed against the authenticated user's enrolled template.
   - Searching across arbitrary employees (1:N directory scanning / surveillance) is prohibited and raises `Biometric1ToNForbiddenError`.
2. **Rejection of Client-Side Assertions**:
   - `FaceInferencePipeline.reject_client_side_override(data)` scans request payloads.
   - Any request containing `face_verified: True`, `is_face_verified: True`, or `override_liveness: True` is immediately rejected with HTTP 400 (`ValueError`).
3. **Zero Raw Frame Retention**:
   - Video frames and decoded image buffers are processed in volatile memory and wiped upon completion.
   - Only 128-dimensional embeddings encrypted via AES-256-GCM (`FaceTemplate.encrypted_template`) are stored at rest.

---

### 4. Automated Test Verification

| Test Suite | Test Count | Result |
|---|---|---|
| `tests/unit/test_face_pipeline.py` | 6 | **ALL 6 PASSED** |
| `tests/unit/test_face_spec.py` | 8 | **ALL 8 PASSED** |
| `tests/unit/test_face_engine.py` | 5 | **ALL 5 PASSED** |
| **Total FRS Tests** | **19** | **100% PASSED** |

---

### 5. External Dependency Blocker Classification

- **Status**: **BLOCKED — EXTERNAL DEPENDENCY**
- **Blockers**:
  1. **Physical Camera Hardware Sensor**: Live video frames cannot be captured on a headless Linux/Windows server without attached USB/MIPI camera hardware.
  2. **ONNX Runtime Engine**: Native ONNX Runtime library (`onnxruntime`) and binary `.onnx` model weights must be provisioned on host systems with AVX2 CPU instructions or GPU acceleration.
- **Remediation**: The software architecture, protocol interfaces, quality assessment, crypto envelope, active challenge validator, and stub fallbacks are completely implemented and verified. Real physical camera streams will be activated upon physical device provisioning.
