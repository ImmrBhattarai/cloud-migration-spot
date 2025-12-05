import time
from pathlib import Path
from PIL import Image
from api.storage import get_next_pending_job, update_job, get_output_path
from common.job_schema import JobStatus

POLL_INTERVAL = 2  # seconds

def process_job(job):
    try:
        job.status = JobStatus.PROCESSING
        update_job(job)

        img = Image.open(job.image_path)
        # Example transformation: convert to grayscale
        img = img.convert("L")

        output_path = get_output_path(job.id)
        img.save(output_path)

        job.status = JobStatus.DONE
        job.result_path = str(output_path)
        update_job(job)
        print(f"Processed job {job.id}")
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        update_job(job)
        print(f"Failed job {job.id}: {e}")

def main():
    print("Worker started...")
    while True:
        job = get_next_pending_job()
        if job:
            process_job(job)
        else:
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
