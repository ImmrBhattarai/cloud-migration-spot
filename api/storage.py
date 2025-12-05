import json
import uuid
from pathlib import Path
from typing import List
from common.config import LOCAL_INPUT_DIR, LOCAL_OUTPUT_DIR, LOCAL_JOBS_FILE
from common.job_schema import Job, JobStatus

def _read_jobs() -> List[Job]:
    data = json.loads(LOCAL_JOBS_FILE.read_text())
    return [Job(**x) for x in data]

def _write_jobs(jobs: List[Job]) -> None:
    LOCAL_JOBS_FILE.write_text(json.dumps([j.dict() for j in jobs], indent=2))

def create_job_for_local_file(filename: str) -> Job:
    job_id = str(uuid.uuid4())
    # input file already saved in LOCAL_INPUT_DIR
    job = Job(
        id=job_id,
        image_path=str(LOCAL_INPUT_DIR / filename),
        status=JobStatus.PENDING,
    )
    jobs = _read_jobs()
    jobs.append(job)
    _write_jobs(jobs)
    return job

def get_job(job_id: str) -> Job | None:
    for job in _read_jobs():
        if job.id == job_id:
            return job
    return None

def update_job(job: Job) -> None:
    jobs = _read_jobs()
    for i, j in enumerate(jobs):
        if j.id == job.id:
            jobs[i] = job
            break
    _write_jobs(jobs)

def get_next_pending_job() -> Job | None:
    jobs = _read_jobs()
    for job in jobs:
        if job.status == JobStatus.PENDING:
            return job
    return None

def get_output_path(job_id: str) -> Path:
    return LOCAL_OUTPUT_DIR / f"{job_id}.png"
