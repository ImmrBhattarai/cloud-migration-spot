# data_migrator.py

# --- Import necessary libraries ---
import os
# Import the Google Cloud Storage client library.
from google.cloud import storage as gcs
# Import the Azure Storage Blob client library, specifically the main service client.
from azure.storage.blob import BlobServiceClient

# --- Configuration (Environment Variables) ---
# Read the name of the source GCS bucket from environment variables.
# This is where the data currently resides.
GCS_BUCKET = os.environ["GCS_BUCKET"]
# Read the Azure Storage connection string. This string contains credentials 
# to access the Azure Storage Account.
AZURE_CONN_STR = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
# Read the name of the destination Azure container from environment variables.
# This is where the data will be copied to.
AZURE_CONTAINER = os.environ["AZURE_CONTAINER"]

# --- Main Migration Function ---
def main():
    # 1. --- Initialize Google Cloud Storage Clients ---
    
    # Creates a GCS client object. This automatically handles authentication
    # using environment variables (e.g., GOOGLE_APPLICATION_CREDENTIALS).
    gcs_client = gcs.Client()
    
    # Selects the specific GCS bucket to work with using the client.
    gcs_bucket = gcs_client.bucket(GCS_BUCKET)

    # 2. --- Initialize Azure Blob Storage Clients ---
    
    # Creates a Blob Service Client using the connection string. This is the 
    # top-level object used to interact with the Azure Storage Account.
    az_client = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
    
    # --- Try to create the destination container ---
    try:
        # Attempt to create the Azure container. This ensures the destination exists.
        az_container = az_client.create_container(AZURE_CONTAINER)
    # --- Handle case where container already exists ---
    except Exception:
        # If the container already exists, creating it will throw an exception.
        # We catch the exception and instead get a reference to the existing container.
        az_container = az_client.get_container_client(AZURE_CONTAINER)

    # 3. --- Perform the Migration Loop ---
    
    # List all blobs (files) in the source GCS bucket.
    # The client.list_blobs() returns an iterable object.
    for blob in gcs_client.list_blobs(GCS_BUCKET):
        
        # Download the content of the current GCS blob into memory as a byte string.
        # NOTE: This method is suitable for smaller files. For very large files (GBs),
        # an asynchronous or streaming copy method would be more efficient to avoid OOM errors.
        data = blob.download_as_bytes()
        
        # Get a client reference for the destination blob in Azure.
        # The Azure blob will have the same name as the GCS blob (blob.name).
        dest = az_container.get_blob_client(blob.name)
        
        # Upload the downloaded byte data to the Azure Blob Storage destination.
        # overwrite=True ensures that if a file with the same name exists, it is replaced.
        dest.upload_blob(data, overwrite=True)
        
        # Print a message confirming the copy operation for traceability.
        print(f"Copied {blob.name}")

# Standard Python entry point. Ensures main() is called only when the script is executed directly.
if __name__ == "__main__":
    main()
