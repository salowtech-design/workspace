# Software Licenses & Compliance Manifest

*Last Updated: 2026-11-15*

## Runtime & Tooling Licenses

| Component | License | Commercial Use Allowed | Attribution Required |
| :--- | :--- | :--- | :--- |
| **Python** | PSF License | Yes | Yes (in standard notices) |
| **Django** | BSD 3-Clause | Yes | Yes |
| **django-tidb** | Apache 2.0 | Yes | Yes |
| **mysqlclient** | GPL v2 (FOSS Exception) | Yes (Dynamic link) | Yes |
| **python-dotenv** | BSD 3-Clause | Yes | Yes |
| **argon2-cffi** | MIT | Yes | Yes |
| **cryptography** | Apache 2.0 / BSD 3-Clause | Yes | Yes |
| **certifi** | MPL 2.0 | Yes | Yes |
| **Ruff** | MIT / Apache 2.0 | Yes | No |
| **pytest** | MIT | Yes | No |

## Face Inference Models & Weights (Phase M2 Evaluation)

| Asset | Source | Stated License | Commercial Verification Status |
| :--- | :--- | :--- | :--- |
| **OpenCV YuNet** | OpenCV Zoo | Apache 2.0 (repository licence) | Weights provenance not reviewed. Commercial use not cleared. |
| **OpenCV SFace** | OpenCV Zoo | Apache 2.0 (repository licence) | Unresolved: the training data and the upstream authorisation of the published weights are asked about in open upstream issues (opencv/opencv_zoo #318, zhongyy/SFace #9), A maintainer reply of 1 Oct 2026 on #318 refers to the Apache-2.0 licence; the training-data and upstream-authorisation questions remain unresolved. Commercial use not cleared. |
| **MiniFASNet** | Model weights source | Apache 2.0 (repository licence) / weights licence unresolved | Pending exact commit and weight licence verification; an open upstream issue asks for weight permission (minivision-ai/Silent-Face-Anti-Spoofing #146). Commercial use not cleared. |

No weight files are tracked in this repository (checked with git ls-files for .onnx, .caffemodel, .pth, .pt and .tflite), and the reviewed pipeline code (apps/face/engine/pipeline.py verify_1_to_1) loads no weights and cannot verify a face. This does not cover the dev and test stub verifier. This table records what was read from public pages, not legal advice or an approval.
