import json
import uuid
import tempfile
import os
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict

# Import configuration variables.
# BASE_DIR etc are used for local storage.
# STORAGE_BACKEND determines which logic branch (local/gcp/azure) runs.
from common.config import BASE_DIR, LOCAL_INPUT_DIR, LOCAL_OUTPUT_DIR, LOCAL_JOBS_FILE, STORAGE_BACKEND

# Import the Job data model.
from common.job_schema import Job, JobStatus

# ------------------------------------------------------------------------------
# CONDITIONAL IMPORTS
# We wrap cloud SDK imports in try/except blocks.
# This prevents the app from crashing locally if you haven't installed the cloud libraries yet,
# or if you are running in a minimal environment that only needs one of them.
# ------------------------------------------------------------------------------

# 1. Google Cloud Storage SDK
try:
    from google.cloud import storage as gcs
except ImportError:
    gcs = None  # Set to None so we can check if it's available later

# 2. Azure Blob Storage SDK
try:
    from azure.storage.blob import BlobServiceClient
except ImportError:
    BlobServiceClient = None

# ------------------------------------------------------------------------------
# CONSTANTS
# These define the folder structure inside our buckets/containers.
# ------------------------------------------------------------------------------
JOBS_OBJECT = "jobs/jobs.json"  # The file acting as our "database"
INPUT_PREFIX = "input/"         # Folder for uploaded images
OUTPUT_PREFIX = "output/"       # Folder for processed images

# Azure configuration from Environment Variables
# We read these here so they are available to the helper functions below.
AZURE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_CONTAINER = os.getenv("AZURE_CONTAINER") 


# ------------------------------------------------------------------------------
# LOCAL FILESYSTEM HELPERS
# Used when STORAGE_BACKEND="local". 
# ------------------------------------------------------------------------------

def _read_local_jobs() -> List[Job]:
    """Reads the array of jobs from the local JSON file."""
    # Read text from data/jobs.json. If empty or missing, default to "[]"
    content = LOCAL_JOBS_FILE.read_text() if LOCAL_JOBS_FILE.exists() else "[]"
    if not content.strip():
        content = "[]"
    data = json.loads(content)
    # Convert list of dicts -> List[Job objects]
    return [Job(**x) for x in data]

def _write_local_jobs(jobs: List[Job]) -> None:
    """Writes the list of Job objects back to the local JSON file."""
    # Convert List[Job objects] -> list of dicts -> JSON string
    LOCAL_JOBS_FILE.write_text(json.dumps([j.dict() for j in jobs], indent=2))


# ------------------------------------------------------------------------------
# GOOGLE CLOUD STORAGE (GCS) HELPERS
# Used when STORAGE_BACKEND="gcp".
# ------------------------------------------------------------------------------

def _get_gcs_client():
    """Returns an authenticated GCS client."""
    if not gcs:
        raise RuntimeError("google-cloud-storage library is not installed.")
    return gcs.Client()

def _ensure_bucket_exists(bucket_name: str):
    """Checks if bucket exists; creates it if not. Returns the Bucket object."""
    client = _get_gcs_client()
    try:
        bucket = client.get_bucket(bucket_name)
    except Exception:
        # If bucket doesn't exist or we can't access it, try creating it.
        # Note: In production, you usually assume infrastructure exists.
        bucket = client.create_bucket(bucket_name)
    return bucket

def _read_gcs_jobs(bucket_name: str) -> List[Job]:
    """Downloads jobs/jobs.json from GCS and parses it."""
    client = _get_gcs_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(JOBS_OBJECT)
    
    if not blob.exists():
        return []
    
    data = blob.download_as_text()
    return [Job(**x) for x in json.loads(data)]

def _write_gcs_jobs(bucket_name: str, jobs: List[Job]):
    """Serializes jobs list to JSON and uploads it to GCS."""
    client = _get_gcs_client()
    # We don't use _ensure_bucket_exists every time for speed, 
    # but we assume the bucket exists by this point.
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(JOBS_OBJECT)
    
    # upload_from_string handles the creation/overwrite of the file
    blob.upload_from_string(
        json.dumps([j.dict() for j in jobs], indent=2), 
        content_type="application/json"
    )


# ------------------------------------------------------------------------------
# AZURE BLOB STORAGE HELPERS
# Used when STORAGE_BACKEND="azure".
# ------------------------------------------------------------------------------

def _get_azure_client():
    """Creates a BlobServiceClient using the connection string."""
    if not BlobServiceClient:
        raise RuntimeError("azure-storage-blob library is not installed.")
    if not AZURE_CONN_STR:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING env var is missing.")
    return BlobServiceClient.from_connection_string(AZURE_CONN_STR)

def _ensure_container_exists(client, container_name: str):
    """Ensures the Azure container exists."""
    # Check if container exists by trying to get a client for it
    container_client = client.get_container_client(container_name)
    if not container_client.exists():
        container_client.create_container()
    return container_client

def _read_azure_jobs(container_name: str) -> List[Job]:
    """Downloads jobs/jobs.json from Azure Blob and parses it."""
    client = _get_azure_client()
    container_client = client.get_container_client(container_name)
    
    # Get a blob client specifically for our database file
    blob_client = container_client.get_blob_client(JOBS_OBJECT)
    
    if not blob_client.exists():
        return []
    
    # Download content as text
    data = blob_client.download_blob().readall()
    return [Job(**x) for x in json.loads(data)]

def _write_azure_jobs(container_name: str, jobs: List[Job]):
    """Serializes jobs list to JSON and uploads/overwrites it on Azure."""
    client = _get_azure_client()
    container_client = client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(JOBS_OBJECT)
    
    # Upload the JSON string, overwriting if it exists
    blob_client.upload_blob(
        json.dumps([j.dict() for j in jobs], indent=2), 
        overwrite=True
    )


# ------------------------------------------------------------------------------
# PUBLIC API FUNCTIONS
# These functions abstract away the backend logic. The API and Worker call THESE.
# They check STORAGE_BACKEND and route to the correct helper above.
# ------------------------------------------------------------------------------

def create_job_from_bytes(filename: str, content_bytes: bytes, gcs_bucket: Optional[str] = None) -> Job:
    """
    1. Uploads the raw file bytes to the configured storage (input/ folder).
    2. Creates a new Job object pointing to that file.
    3. Adds the Job to the central jobs list (database).
    """
    job_id = str(uuid.uuid4())
    
    if STORAGE_BACKEND == "local":
        # Ensure directory exists
        LOCAL_INPUT_DIR.mkdir(parents=True, exist_ok=True)
        dest = LOCAL_INPUT_DIR / filename
        dest.write_bytes(content_bytes)
        
        # Create job pointing to local path
        job = Job(id=job_id, image_path=str(dest), status=JobStatus.PENDING)
        
        # Update "Database"
        jobs = _read_local_jobs()
        jobs.append(job)
        _write_local_jobs(jobs)
        return job

    elif STORAGE_BACKEND == "gcp":
        if not gcs_bucket:
            raise ValueError("gcs_bucket is required for GCP backend")
            
        # Upload file to GCS
        client = _get_gcs_client()
        bucket = _ensure_bucket_exists(gcs_bucket)
        object_name = f"{INPUT_PREFIX}{filename}" # e.g. input/image.jpg
        blob = bucket.blob(object_name)
        blob.upload_from_string(content_bytes, content_type="application/octet-stream")
        
        # Standard GCS URI format: gs://bucket-name/path/to/obj
        image_path = f"gs://{gcs_bucket}/{object_name}"

        # Update "Database"
        job = Job(id=job_id, image_path=image_path, status=JobStatus.PENDING)
        jobs = _read_gcs_jobs(gcs_bucket)
        jobs.append(job)
        _write_gcs_jobs(gcs_bucket, jobs)
        return job

    elif STORAGE_BACKEND == "azure":
        if not AZURE_CONTAINER:
            raise ValueError("AZURE_CONTAINER env var is required for Azure backend")
            
        client = _get_azure_client()
        container_client = _ensure_container_exists(client, AZURE_CONTAINER)
        
        object_name = f"{INPUT_PREFIX}{filename}" # e.g. input/image.jpg
        blob_client = container_client.get_blob_client(object_name)
        
        # Upload file to Azure Blob
        blob_client.upload_blob(content_bytes, overwrite=True)
        
        # We invent a custom URI scheme for internal tracking: az://container/path
        # This makes it easy to recognize Azure paths later.
        image_path = f"az://{AZURE_CONTAINER}/{object_name}"
        
        # Update "Database"
        job = Job(id=job_id, image_path=image_path, status=JobStatus.PENDING)
        jobs = _read_azure_jobs(AZURE_CONTAINER)
        jobs.append(job)
        _write_azure_jobs(AZURE_CONTAINER, jobs)
        return job

    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")


def get_job(job_id: str, gcs_bucket: Optional[str] = None) -> Optional[Job]:
    """Retrieves a specific job by ID from the backend's job list."""
    
    if STORAGE_BACKEND == "local":
        jobs = _read_local_jobs()
    elif STORAGE_BACKEND == "gcp":
        jobs = _read_gcs_jobs(gcs_bucket)
    elif STORAGE_BACKEND == "azure":
        jobs = _read_azure_jobs(AZURE_CONTAINER)
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")

    # Search list for matching ID
    return next((j for j in jobs if j.id == job_id), None)


def update_job(job: Job, gcs_bucket: Optional[str] = None) -> None:
    """Updates the status or result path of a job in the database."""
    
    # 1. Read current list
    if STORAGE_BACKEND == "local":
        jobs = _read_local_jobs()
    elif STORAGE_BACKEND == "gcp":
        jobs = _read_gcs_jobs(gcs_bucket)
    elif STORAGE_BACKEND == "azure":
        jobs = _read_azure_jobs(AZURE_CONTAINER)
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")

    # 2. Modify the specific job in the list
    for i, j in enumerate(jobs):
        if j.id == job.id:
            jobs[i] = job
            break
            
    # 3. Write the list back
    if STORAGE_BACKEND == "local":
        _write_local_jobs(jobs)
    elif STORAGE_BACKEND == "gcp":
        _write_gcs_jobs(gcs_bucket, jobs)
    elif STORAGE_BACKEND == "azure":
        _write_azure_jobs(AZURE_CONTAINER, jobs)


def get_next_pending_job(gcs_bucket: Optional[str] = None) -> Optional[Job]:
    """Finds the first job with status='PENDING'."""
    
    if STORAGE_BACKEND == "local":
        jobs = _read_local_jobs()
    elif STORAGE_BACKEND == "gcp":
        jobs = _read_gcs_jobs(gcs_bucket)
    elif STORAGE_BACKEND == "azure":
        jobs = _read_azure_jobs(AZURE_CONTAINER)
    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")
        
    for j in jobs:
        if j.status == JobStatus.PENDING:
            return j
    return None


def download_input_to_tempfile(job: Job, gcs_bucket: Optional[str] = None) -> Path:
    """
    Downloads the 'image_path' from the job to a local temporary file.
    Used by the worker to process the image.
    Returns the path to the temporary file on the worker's disk.
    """
    if STORAGE_BACKEND == "local":
        # Local backend just points to the file on disk directly
        return Path(job.image_path)

    elif STORAGE_BACKEND == "gcp":
        # Parse URI: gs://bucket/input/image.png
        if job.image_path.startswith("gs://"):
            _, _, remainder = job.image_path.partition("gs://")
            bucket_name, _, object_name = remainder.partition("/")
            
            client = _get_gcs_client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            
            # Create a temp file to hold the download
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(object_name).suffix)
            blob.download_to_filename(tmp.name)
            return Path(tmp.name)
        else:
            raise ValueError(f"Invalid GCS path: {job.image_path}")

    elif STORAGE_BACKEND == "azure":
        # Parse URI: az://container/input/image.png
        if job.image_path.startswith("az://"):
            _, _, remainder = job.image_path.partition("az://")
            container_name, _, object_name = remainder.partition("/")
            
            client = _get_azure_client()
            container_client = client.get_container_client(container_name)
            blob_client = container_client.get_blob_client(object_name)
            
            # Create temp file and download
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(object_name).suffix)
            with open(tmp.name, "wb") as f:
                blob_data = blob_client.download_blob()
                blob_data.readinto(f)
            return Path(tmp.name)
        else:
             raise ValueError(f"Invalid Azure path: {job.image_path}")

    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")


def upload_output_from_local(local_output_path: str, job: Job, gcs_bucket: Optional[str] = None) -> str:
    """
    Takes a processed file from local disk and uploads it to the backend output folder.
    Returns the new remote path string (e.g. gs://... or az://...).
    """
    if STORAGE_BACKEND == "local":
        # For local, we just update the job record to point to the file we already wrote
        out_path = Path(local_output_path)
        job.result_path = str(out_path)
        update_job(job)
        return job.result_path

    elif STORAGE_BACKEND == "gcp":
        client = _get_gcs_client()
        bucket = _ensure_bucket_exists(gcs_bucket)
        
        # Name: output/{job_id}.png
        object_name = f"{OUTPUT_PREFIX}{job.id}{Path(local_output_path).suffix}"
        
        blob = bucket.blob(object_name)
        blob.upload_from_filename(local_output_path, content_type="image/png")
        
        result_path = f"gs://{gcs_bucket}/{object_name}"
        job.result_path = result_path
        
        update_job(job, gcs_bucket=gcs_bucket)
        return result_path

    elif STORAGE_BACKEND == "azure":
        client = _get_azure_client()
        if not AZURE_CONTAINER:
             raise ValueError("AZURE_CONTAINER missing")
        container_client = client.get_container_client(AZURE_CONTAINER)
        
        # Name: output/{job_id}.png
        object_name = f"{OUTPUT_PREFIX}{job.id}{Path(local_output_path).suffix}"
        blob_client = container_client.get_blob_client(object_name)
        
        with open(local_output_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)
            
        result_path = f"az://{AZURE_CONTAINER}/{object_name}"
        job.result_path = result_path
        
        update_job(job) # azure container is global/env var, so no arg passed here usually, but we check implementation
        return result_path

    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")


def download_result_to_tempfile(job: Job, gcs_bucket: Optional[str] = None) -> Path:
    """
    Used by the API to serve the result image to the user.
    Downloads the 'result_path' to a temp file so the API can return it as a FileResponse.
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

    elif STORAGE_BACKEND == "azure":
        client = _get_azure_client()
        if job.result_path and job.result_path.startswith("az://"):
            _, _, remainder = job.result_path.partition("az://")
            container_name, _, object_name = remainder.partition("/")
            
            container_client = client.get_container_client(container_name)
            blob_client = container_client.get_blob_client(object_name)
            
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(object_name).suffix)
            with open(tmp.name, "wb") as f:
                blob_data = blob_client.download_blob()
                blob_data.readinto(f)
            return Path(tmp.name)
        else:
            raise ValueError("job.result_path missing or not an Azure path")

    else:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND: {STORAGE_BACKEND}")
