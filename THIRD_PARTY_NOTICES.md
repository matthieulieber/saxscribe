# Third-party notices and license cautions

This Git repository does not contain neural-network model weights. Local setup downloads them on first use, and the hosted Docker build downloads the UVR wind checkpoint into the deployment image.

Do not publish or sell deployment images containing those checkpoints until their exact redistribution and commercial-service terms are confirmed. Source-code licensing and model-weight licensing are separate questions.

| Dependency | Code license | Practical note |
|---|---|---|
| audio-separator | MIT | The wrapper is permissive; each downloaded UVR model may have separate or undocumented terms. |
| Ultimate Vocal Remover code | MIT | Model provenance is not uniformly documented. |
| LALAL.AI API | Proprietary service | Use a business/API agreement for a customer-facing hosted product; a consumer plan is not a substitute. |
| hf_midi_transcription | Repository states MIT, but historically lacked a root LICENSE file | Personal experimentation is reasonable; obtain written clearance before commercial use. |
| FiloSax-derived sax checkpoints | Model cards may say MIT; training data is restricted to non-commercial research | Do not assume commercial clearance from the model-card label alone. |
| librosa | ISC | Preserve copyright/license notice when distributing. |
| pretty_midi | MIT | Preserve copyright/license notice when distributing. |
| music21 | BSD-3-Clause | Preserve copyright/license notice when distributing. |
| OpenAI Python SDK | Apache-2.0 | API usage is governed separately by OpenAI terms. |
| Stripe Python SDK | MIT | Stripe payment processing is governed separately by the Stripe services agreement. |
| Google Cloud Python SDKs | Apache-2.0 | Cloud service usage and stored media are governed separately by the Google Cloud agreement. |
| React / Vite | MIT | Preserve notices when distributing. |
| FFmpeg | LGPL or GPL depending on build flags | Homebrew installation varies; comply with the actual build's license. |
| MuseScore | GPL-3.0 | This app invokes a separately installed executable and does not redistribute it. |

Recordings and compositions are protected independently of software licenses. Keep outputs private, process only audio you are entitled to use, and do not publish generated scores without the necessary rights.

This file is an engineering summary, not legal advice.
