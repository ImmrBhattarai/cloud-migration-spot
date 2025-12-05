from pydantic import BaseModel
from typing import Optional
from enum import Enum

class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"

class Job(BaseModel):
    id: str
    image_path: str          # where source image is stored
    result_path: Optional[str] = None
    status: JobStatus = JobStatus.PENDING
    error: Optional[str] = None
