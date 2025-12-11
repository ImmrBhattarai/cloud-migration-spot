import time
from pathlib import Path
from PIL import Image
from common.storage import get_next_pending_job, update_job, download_input_to_tempfile, upload_output_from_local
from common.job_schema import JobStatus

from os import getenv

POLL_INTERVAL = 2  # seconds

GCS_BUCKET = getenv("GCS_BUCKET", None)  # used when STORAGE_BACKEND=gcp

def process_job(job):
    try:
        job.status = JobStatus.PROCESSING
        update_job(job, gcs_bucket=GCS_BUCKET if job.image_path.startswith("gs://") else None)

        input_path = download_input_to_tempfile(job, gcs_bucket=GCS_BUCKET)
        img = Image.open(input_path)
        img = img.convert("L")
        output_path = str(Path("/tmp") / f"{job.id}.png")
        img.save(output_path)

        result_path = upload_output_from_local(output_path, job, gcs_bucket=GCS_BUCKET)
        job.status = JobStatus.DONE
        job.result_path = result_path
        update_job(job, gcs_bucket=GCS_BUCKET)
        print(f"Processed job {job.id}")
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        update_job(job, gcs_bucket=GCS_BUCKET)
        print(f"Failed job {job.id}: {e}")

def main():
    print("Worker started...")
    while True:
        job = get_next_pending_job(gcs_bucket=GCS_BUCKET) if getenv("STORAGE_BACKEND", "local") == "gcp" else get_next_pending_job()
        if job:
            process_job(job)
        else:
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
