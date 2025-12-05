import json
import uuid
import tempfile
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict

from common.config import BASE_DIR, LOCAL_INPUT_DIR, LOCAL_OUTPUT_DIR, LOCAL_JOBS_FILE, STORAGE_BACKEND

# Only import google storage when required to avoid imports on local-only runs
try:
    from google.cloud import storage as gcs
except Exception:
    gcs = None

JOBS_OBJECT = "jobs/jobs.json"
INPUT_PREFIX = "input/"
OUTPUT_PREFIX = "output/"

# Job model reused from common.job_schema
from common.job_schema import Job, JobStatus

def _read_local_jobs() -> List[Job]:
    data = json.loads(LOCAL_JOBS_FILE.read_text() or "[]")
    return [Job(**x) for x in data]

def _write_local_jobs(jobs: List[Job]) -> None:
    LOCAL_JOBS_FILE.write_text(json.dumps([j.dict() for j in jobs], indent=2))

# ---------- GCS helpers ----------
def _get_gcs_client():
    if not gcs:
        raise RuntimeError("google-cloud-storage is not installed or cannot be imported")
    return gcs.Client()

def _ensure_bucket_exists(bucket_name: str):
    client = _get_gcs_client()
    bucket = client.lookup_bucket(bucket_name)
    if bucket is None:
        bucket = client.create_bucket(bucket_name)
    return bucket

def _read_gcs_jobs(bucket_name: str) -> List[Job]:
    client = _get_gcs_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(JOBS_OBJECT)
    if not blob.exists():
        return []
    data = blob.download_as_text()
    return [Job(**x) for x in json.loads(data)]

def _write_gcs_jobs(bucket_name: str, jobs: List[Job]):
    client = _get_gcs_client()
    bucket = _ensure_bucket_exists(bucket_name)
    blob = bucket.blob(JOBS_OBJECT)
    blob.upload_from_string(json.dumps([j.dict() for j in jobs], indent=2), content_type="application/json")


# ---------- Public storage API (supports 'local' and 'gcp') ----------
def create_job_from_bytes(filename: str, content_bytes: bytes, gcs_bucket: Optional[str] = None) -> Job:
    """
    Create a job from an uploaded file (bytes).
    For local backend: save file into data/input and create job pointing to local path.
    For gcp backend: upload file to gcs://{bucket}/input/{filename} and create job with image_path referencing that object.
    """
    job_id = str(uuid.uuid4())
    if STORAGE_BACKEND == "local":
        LOCAL_INPUT_DIR.mkdir(parents=True, exist_ok=True)
        dest = LOCAL_INPUT_DIR / filename
        dest.write_bytes(content_bytes)
        job = Job(id=job_id, image_path=str(dest), status=JobStatus.PENDING)
        jobs = _read_local_jobs()
        jobs.append(job)
        _write_local_jobs(jobs)
        return job

    elif STORAGE_BACKEND == "gcp":
        if not gcs_bucket:
            raise ValueError("gcs_bucket is required for GCP backend")
        client = _get_gcs_client()
        bucket = _ensure_bucket_exists(gcs_bucket)
        object_name = f"{INPUT_PREFIX}{filename}"
        blob = bucket.blob(object_name)
        blob.upload_from_string(content_bytes, content_type="application/octet-stream")
        image_path = f"gs://{gcs_bucket}/{object_name}"

        # create job and store in jobs.json in bucket
        job = Job(id=job_id, image_path=image_path, status=JobStatus.PENDING)
        jobs = _read_gcs_jobs(gcs_bucket)
        jobs.append(job)
        _write_gcs_jobs(gcs_bucket, jobs)
        return job
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")

def get_job(job_id: str, gcs_bucket: Optional[str] = None) -> Optional[Job]:
    if STORAGE_BACKEND == "local":
        return next((j for j in _read_local_jobs() if j.id == job_id), None)
    elif STORAGE_BACKEND == "gcp":
        jobs = _read_gcs_jobs(gcs_bucket)
        return next((j for j in jobs if j.id == job_id), None)
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")

def update_job(job: Job, gcs_bucket: Optional[str] = None) -> None:
    if STORAGE_BACKEND == "local":
        jobs = _read_local_jobs()
        for i, j in enumerate(jobs):
            if j.id == job.id:
                jobs[i] = job
                break
        _write_local_jobs(jobs)
    elif STORAGE_BACKEND == "gcp":
        jobs = _read_gcs_jobs(gcs_bucket)
        for i, j in enumerate(jobs):
            if j.id == job.id:
                jobs[i] = job
                break
        _write_gcs_jobs(gcs_bucket, jobs)
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")

def get_next_pending_job(gcs_bucket: Optional[str] = None) -> Optional[Job]:
    if STORAGE_BACKEND == "local":
        for j in _read_local_jobs():
            if j.status == JobStatus.PENDING:
                return j
        return None
    elif STORAGE_BACKEND == "gcp":
        for j in _read_gcs_jobs(gcs_bucket):
            if j.status == JobStatus.PENDING:
                return j
        return None
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")

def download_input_to_tempfile(job: Job, gcs_bucket: Optional[str] = None) -> Path:
    """
    Download job input to a local temp file and return its path.
    If local backend, just return Path(job.image_path).
    If gcp, download the object to a tempfile and return its Path.
    """
    if STORAGE_BACKEND == "local":
        return Path(job.image_path)
    elif STORAGE_BACKEND == "gcp":
        # job.image_path should be gs://{bucket}/input/filename
        client = _get_gcs_client()
        # extract object path
        if job.image_path.startswith("gs://"):
            _, _, remainder = job.image_path.partition("gs://")
            bucket_name, _, object_name = remainder.partition("/")
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(object_name).suffix)
            blob.download_to_filename(tmp.name)
            return Path(tmp.name)
        else:
            raise ValueError("job.image_path does not look like a GCS path")
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")

def upload_output_from_local(local_output_path: str, job: Job, gcs_bucket: Optional[str] = None) -> str:
    """
    Upload processed result to output (local or GCS). Update job.result_path.
    Returns the result_path.
    """
    if STORAGE_BACKEND == "local":
        out_path = Path(local_output_path)
        job.result_path = str(out_path)
        update_job(job)
        return job.result_path
    elif STORAGE_BACKEND == "gcp":
        client = _get_gcs_client()
        bucket = _ensure_bucket_exists(gcs_bucket)
        object_name = f"{OUTPUT_PREFIX}{job.id}{Path(local_output_path).suffix}"
        blob = bucket.blob(object_name)
        blob.upload_from_filename(local_output_path, content_type="image/png")
        job.result_path = f"gs://{gcs_bucket}/{object_name}"
        update_job(job, gcs_bucket=gcs_bucket)
        return job.result_path
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")

def download_result_to_tempfile(job: Job, gcs_bucket: Optional[str] = None) -> Path:
    """
    If result is local, return Path. If gcs, download to tempfile and return path.
    """
    if STORAGE_BACKEND == "local":
        return Path(job.result_path)
    elif STORAGE_BACKEND == "gcp":
        client = _get_gcs_client()
        if job.result_path and job.result_path.startswith("gs://"):
            _, _, remainder = job.result_path.partition("gs://")
            bucket_name, _, object_name = remainder.partition("/")
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(object_name).suffix)
            blob.download_to_filename(tmp.name)
            return Path(tmp.name)
        else:
            raise ValueError("job.result_path missing or not a GCS path")
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")