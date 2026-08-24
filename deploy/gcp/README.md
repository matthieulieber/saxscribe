# Google Cloud deployment

SaxScribe uses two Cloud Run resources built from the same container:

- `saxscribe-web`: the Vite application and FastAPI job API.
- `saxscribe-worker`: an asynchronous Cloud Run Job that performs separation, transcription and MusicXML generation.

Uploads and generated files are stored in a private Cloud Storage bucket. Firestore stores job progress and one-time payment claims. The container includes the checksum-verified UVR wind model for Free jobs. Paid Enhanced jobs use LALAL.AI and the required OpenAI evidence review.

## Prerequisites

1. Create or select a Google Cloud project with billing enabled.
2. Install and authenticate the Google Cloud CLI.
3. Obtain a LALAL.AI business API key and an OpenAI API key.
4. Create a Stripe account, then create one active, non-recurring Price for SaxScribe Enhanced.
5. Store the three server keys in Secret Manager:

```bash
gcloud secrets create lalal-api-key --replication-policy=automatic
gcloud secrets versions add lalal-api-key --data-file=-
gcloud secrets create openai-api-key --replication-policy=automatic
gcloud secrets versions add openai-api-key --data-file=-
gcloud secrets create stripe-secret-key --replication-policy=automatic
gcloud secrets versions add stripe-secret-key --data-file=-
```

The second command reads the key from standard input and does not place it in this repository.

## Deploy

From the project root:

```bash
export PROJECT_ID=your-google-cloud-project
export STRIPE_ENHANCED_PRICE_ID=price_...
bash scripts/deploy-gcp.sh
```

Optional settings include `REGION`, `BUCKET`, `SERVICE`, `JOB`, `FIRESTORE_LOCATION`, `LALAL_SECRET_NAME`, `OPENAI_SECRET_NAME`, and `STRIPE_SECRET_NAME`.

The script creates missing Google Cloud resources, builds the container, deploys the worker job, deploys the public web service, and writes the resulting Cloud Run URL back as `PUBLIC_BASE_URL`. It never exposes LALAL.AI, OpenAI, or Stripe secret keys to the browser.

## Payment behavior

Stripe hosts the card-entry page. After checkout, SaxScribe retrieves the Checkout Session on the server and requires all of the following before accepting an Enhanced upload:

- one-time `payment` mode;
- `complete` status and `paid` payment status;
- `saxscribe_plan=enhanced` metadata;
- a line item matching `STRIPE_ENHANCED_PRICE_ID`;
- no previous Firestore claim by another job.

A successful job keeps the claim. If the worker catches a processing failure, it releases the claim so the same browser can retry without another payment. Operational failures that prevent the worker from starting at all still require an administrator to release the Firestore claim manually.

Free jobs bypass billing and are forced to UVR with AI disabled. Enhanced jobs are forced to LALAL.AI with AI enabled. Local mode rejects Enhanced entirely.

For webhook audit records, create a Stripe endpoint at `https://YOUR_SERVICE/api/billing/webhook`, copy its `whsec_...` signing secret into a `stripe-webhook-secret` Secret Manager secret, then redeploy. Checkout and job redemption already validate the paid Session synchronously; the webhook is optional for this upload-after-payment MVP.

## Current upload constraint

The first hosted version receives the multipart upload through the Cloud Run web service before copying it to Cloud Storage. `MAX_UPLOAD_MB` is therefore set to 30 MB in the deployment script. This covers compressed full-song MP3/M4A files but not every uncompressed WAV. A later production hardening step should add resumable browser-to-Cloud-Storage uploads.

## Privacy

Configure a Cloud Storage lifecycle rule to delete `jobs/` objects after one day. Firestore job documents should be deleted on a similar schedule. The application deletes LALAL source files immediately after downloading the wind stem, but LALAL download links may remain cached according to its API contract.
