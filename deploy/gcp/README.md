# Google Cloud deployment

SaxScribe uses two Cloud Run resources built from the same container:

- `saxscribe-web`: the Vite application and FastAPI job API.
- `saxscribe-worker`: an asynchronous Cloud Run Job that performs separation, transcription and MusicXML generation.

Uploads and generated files are stored in a private Cloud Storage bucket. Firestore stores job progress. Hosted separation defaults to the LALAL.AI API, so the Cloud Run worker does not need a GPU or the locally downloaded UVR checkpoint.

## Prerequisites

1. Create or select a Google Cloud project with billing enabled.
2. Install and authenticate the Google Cloud CLI.
3. Obtain a LALAL.AI business API key.
4. Store that key in Secret Manager:

```bash
gcloud secrets create lalal-api-key --replication-policy=automatic
gcloud secrets versions add lalal-api-key --data-file=-
```

The second command reads the key from standard input and does not place it in this repository.

## Deploy

From the project root:

```bash
export PROJECT_ID=your-google-cloud-project
bash scripts/deploy-gcp.sh
```

Optional settings include `REGION`, `BUCKET`, `SERVICE`, `JOB`, `FIRESTORE_LOCATION`, and `LALAL_SECRET_NAME`.

The script creates missing Google Cloud resources, builds the container, deploys the worker job and deploys the public web service. It does not bundle the UVR model or expose the LALAL key to the browser.

## Current upload constraint

The first hosted version receives the multipart upload through the Cloud Run web service before copying it to Cloud Storage. `MAX_UPLOAD_MB` is therefore set to 30 MB in the deployment script. This covers compressed full-song MP3/M4A files but not every uncompressed WAV. A later production hardening step should add resumable browser-to-Cloud-Storage uploads.

## Privacy

Configure a Cloud Storage lifecycle rule to delete `jobs/` objects after one day. Firestore job documents should be deleted on a similar schedule. The application deletes LALAL source files immediately after downloading the wind stem, but LALAL download links may remain cached according to its API contract.
