import os
from azure.storage.blob import BlobServiceClient

conn_str = os.environ.get("AzureWebJobsStorage", "UseDevelopmentStorage=true")
blob_service = BlobServiceClient.from_connection_string(conn_str)

container = blob_service.get_container_client("companies-import")
if not container.exists():
    container.create_container()
    print("コンテナ作成: companies-import")

with open("test_data/companies.csv", "rb") as f:
    container.upload_blob("companies.csv", f, overwrite=True)

print("CSV アップロード完了: companies-import/companies.csv")
