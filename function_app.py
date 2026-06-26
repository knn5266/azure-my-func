import azure.functions as func
import logging

app = func.FunctionApp()

@app.route(route="httpTrigger", auth_level=func.AuthLevel.ANONYMOUS)
def httpTrigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')

    try:
        name = req.params.get('name')
        if not name:
            try:
                req_body = req.get_json()
                name = req_body.get('name')
            except ValueError:
                pass

        if not name:
            raise ValueError("name is required")

        return func.HttpResponse(f"Hello, {name}.")

    except ValueError as e:
        logging.warning(f"Validation error: {e}")
        return func.HttpResponse(str(e), status_code=400)

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return func.HttpResponse("Internal Server Error", status_code=500)

@app.timer_trigger(schedule="0 */5 * * * *", arg_name="myTimer", run_on_startup=True,
              use_monitor=False)
def timerTrigger(myTimer: func.TimerRequest) -> None:
    import os
    import json
    from datetime import datetime, timezone
    from hubspot import HubSpot
    from hubspot.crm.contacts import ApiException
    from azure.storage.blob import BlobServiceClient

    if myTimer.past_due:
        logging.info('The timer is past due!')

    # HubSpotからデータ取得
    token = os.environ.get("HUBSPOT_ACCESS_TOKEN")
    api_client = HubSpot(access_token=token)

    try:
        contacts = api_client.crm.contacts.get_all()
        logging.info(f"取得件数: {len(contacts)}")

        # dict形式に変換
        contacts_data = [c.to_dict() for c in contacts]

        # Blob Storageに保存
        conn_str = os.environ.get("AzureWebJobsStorage")
        blob_service = BlobServiceClient.from_connection_string(conn_str)
        container = blob_service.get_container_client("contacts")

        if not container.exists():
            container.create_container()

        blob_name = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") + ".json"
        container.upload_blob(blob_name, json.dumps(contacts_data, default=str))
        logging.info(f"Blob保存完了: {blob_name}")

    except ApiException as e:
        logging.error(f"HubSpot APIエラー: {e}")
    except Exception as e:
        logging.error(f"エラー: {e}")
