import azure.functions as func
import azure.durable_functions as df  
import logging

app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.route(route="webhook", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
def hubspotWebhook(req: func.HttpRequest) -> func.HttpResponse:
    import json
    logging.info("HubSpot Webhook 受信")
    try:
        events = req.get_json()
        for event in events:
            logging.info(f"イベント種別: {event.get('subscriptionType')}")
            logging.info(f"オブジェクトID: {event.get('objectId')}")
            logging.info(f"変更内容: {event.get('propertyName')} → {event.get('propertyValue')}")
        return func.HttpResponse("OK", status_code=200)
    except Exception as e:
        logging.error(f"Webhook エラー: {e}")
        return func.HttpResponse("Error", status_code=500)


@app.route(route="httpTrigger", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
def httpTrigger(req: func.HttpRequest) -> func.HttpResponse:
    import os, json
    from hubspot import HubSpot
    from hubspot.crm.contacts import SimplePublicObjectInputForCreate, ApiException

    logging.info('HubSpot コンタクト作成リクエスト受信')

    try:
        body = req.get_json()
        email = body.get("email")
        firstname = body.get("firstname")
        lastname = body.get("lastname")

        if not email:
            return func.HttpResponse("email is required", status_code=400)

        token = os.environ.get("HUBSPOT_ACCESS_TOKEN")
        api_client = HubSpot(access_token=token)

        new_contact = SimplePublicObjectInputForCreate(properties={
            "email": email,
            "firstname": firstname or "",
            "lastname": lastname or "",
        })
        created = api_client.crm.contacts.basic_api.create(simple_public_object_input_for_create=new_contact)
        logging.info(f"作成完了: id={created.id}")

        return func.HttpResponse(
            json.dumps({"id": created.id, "email": email}, ensure_ascii=False),
            mimetype="application/json",
            status_code=201
        )

    except ApiException as e:
        if e.status == 409:
            return func.HttpResponse("Contact already exists", status_code=409)
        logging.error(f"HubSpot APIエラー: {e}")
        return func.HttpResponse("HubSpot API error", status_code=500)

    except Exception as e:
        logging.error(f"エラー: {e}")
        return func.HttpResponse("Internal Server Error", status_code=500)


@app.timer_trigger(schedule="0 */5 * * * *", arg_name="myTimer", run_on_startup=True, use_monitor=False)
def timerTrigger(myTimer: func.TimerRequest) -> None:
    import os, json
    from datetime import datetime, timezone, timedelta
    from hubspot import HubSpot
    from hubspot.crm.contacts import (
        ApiException, SimplePublicObjectInputForCreate, SimplePublicObjectInput,
        PublicObjectSearchRequest, Filter, FilterGroup
    )
    from azure.storage.blob import BlobServiceClient

    if myTimer.past_due:
        logging.info('The timer is past due!')

    token = os.environ.get("HUBSPOT_ACCESS_TOKEN")
    api_client = HubSpot(access_token=token)

    try:
        properties = ["email", "firstname", "lastname", "phone", "company", "jobtitle"]
        contacts = api_client.crm.contacts.get_all(properties=properties)
        logging.info(f"全件取得件数: {len(contacts)}")

        conn_str = os.environ.get("AzureWebJobsStorage")
        blob_service = BlobServiceClient.from_connection_string(conn_str)
        container = blob_service.get_container_client("contacts")
        if not container.exists():
            container.create_container()

        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        diff_request = PublicObjectSearchRequest(
            filter_groups=[FilterGroup(filters=[
                Filter(property_name="lastmodifieddate", operator="GTE", value=five_min_ago)
            ])],
            properties=properties
        )
        diff_result = api_client.crm.contacts.search_api.do_search(public_object_search_request=diff_request)
        logging.info(f"差分件数: {diff_result.total}")

        if diff_result.total > 0:
            blob_name = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") + "_diff.json"
            container.upload_blob(blob_name, json.dumps([r.to_dict() for r in diff_result.results], default=str))
            logging.info(f"差分 Blob保存完了: {blob_name}")
        else:
            logging.info("差分なし：保存スキップ")

        try:
            created = api_client.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=SimplePublicObjectInputForCreate(properties={
                    "email": "test.azure@example.com",
                    "firstname": "Azure",
                    "lastname": "Functions",
                    "jobtitle": "Engineer",
                    "company": "Microsoft"
                })
            )
            logging.info(f"新規作成完了: id={created.id}")
        except ApiException as e:
            if e.status == 409:
                logging.info("コンタクトは既に存在します（スキップ）")
            else:
                raise

        brian = next((c for c in contacts if c.properties.get("firstname") == "Brian"), None)
        if brian:
            api_client.crm.contacts.basic_api.update(
                contact_id=brian.id,
                simple_public_object_input=SimplePublicObjectInput(properties={"jobtitle": "CEO"})
            )
            logging.info(f"更新完了: Brian → jobtitle=CEO")
        else:
            logging.info("Brianが見つかりませんでした")

        and_result = api_client.crm.contacts.search_api.do_search(
            public_object_search_request=PublicObjectSearchRequest(
                filter_groups=[FilterGroup(filters=[
                    Filter(property_name="email", operator="CONTAINS_TOKEN", value="*@hubspot.com"),
                    Filter(property_name="firstname", operator="EQ", value="Brian"),
                ])],
                properties=["email", "firstname", "lastname"]
            )
        )
        logging.info(f"AND検索結果件数: {and_result.total}")

        or_result = api_client.crm.contacts.search_api.do_search(
            public_object_search_request=PublicObjectSearchRequest(
                filter_groups=[
                    FilterGroup(filters=[Filter(property_name="firstname", operator="EQ", value="Brian")]),
                    FilterGroup(filters=[Filter(property_name="firstname", operator="EQ", value="Maria")]),
                ],
                properties=["email", "firstname", "lastname"]
            )
        )
        logging.info(f"OR検索結果件数: {or_result.total}")

        deals = api_client.crm.deals.get_all(properties=["dealname", "amount", "dealstage", "closedate", "pipeline"])
        logging.info(f"Deals取得件数: {len(deals)}")
        blob_name_deals = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") + "_deals.json"
        container.upload_blob(blob_name_deals, json.dumps([d.to_dict() for d in deals], default=str))
        logging.info(f"Deals Blob保存完了: {blob_name_deals}")

        companies = api_client.crm.companies.get_all(properties=["name", "domain", "industry", "city", "phone"])
        logging.info(f"Companies取得件数: {len(companies)}")
        blob_name_companies = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") + "_companies.json"
        container.upload_blob(blob_name_companies, json.dumps([c.to_dict() for c in companies], default=str))
        logging.info(f"Companies Blob保存完了: {blob_name_companies}")

    except ApiException as e:
        logging.error(f"HubSpot APIエラー: {e}")
    except Exception as e:
        logging.error(f"エラー: {e}")

@app.timer_trigger(schedule="0 40 2 * * *", arg_name="batchTimer", run_on_startup=False, use_monitor=False)
@app.durable_client_input(client_name="client")
async def companyBatchImportDaily(batchTimer: func.TimerRequest, client: df.DurableOrchestrationClient) -> None:
    """毎日 JST 11:40 に Durable オーケストレーションを起動するタイマー"""
    logging.info("会社バッチ 毎日JST11時40分 Durable 起動")
    instance_id = await client.start_new("companyBatchOrchestrator")
    logging.info(f"オーケストレーション開始: instance_id={instance_id}")


@app.route(route="durableCompanyBatch", methods=["POST"])
@app.durable_client_input(client_name="client")
async def durableCompanyBatchHttp(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    """HTTP POST で手動起動。レスポンスに進捗確認 URL が含まれる"""
    instance_id = await client.start_new("companyBatchOrchestrator")
    logging.info(f"Durable オーケストレーション開始: instance_id={instance_id}")
    return client.create_check_status_response(req, instance_id)


@app.orchestration_trigger(context_name="context")
def companyBatchOrchestrator(context: df.DurableOrchestrationContext):
    """
    オーケストレーター関数
      Step1: CSV取得 (fetchCsvActivity)
      Step2: 各社を並列 upsert (upsertCompanyActivity × N) ← Fan-out
      Step3: 結果集約・Blob保存 (saveResultActivity)         ← Fan-in
    """
    logging.info("=== companyBatchOrchestrator 開始 ===")

    # Step1: Blob から CSV を取得
    rows = yield context.call_activity("fetchCsvActivity", None)
    logging.info(f"CSV レコード数: {len(rows)}")

    # Step2: Fan-out — 各行を並列で HubSpot へ upsert
    tasks = [context.call_activity("upsertCompanyActivity", row) for row in rows]
    results = yield context.task_all(tasks)
    logging.info(f"Fan-out 完了: {len(results)} 件処理")

    # Step3: 結果集約して Blob に保存
    summary = yield context.call_activity("saveResultActivity", results)
    logging.info(f"=== companyBatchOrchestrator 完了 === {summary}")
    return summary


@app.activity_trigger(input_name="payload")
def fetchCsvActivity(payload) -> list:
    """Activity1: Blob Storage の companies-import/companies.csv を取得してリストで返す"""
    import os, csv, io
    from azure.storage.blob import BlobServiceClient

    conn_str = os.environ.get("AzureWebJobsStorage")
    blob_service = BlobServiceClient.from_connection_string(conn_str)
    blob_client = blob_service.get_blob_client("companies-import", "companies.csv")
    csv_data = blob_client.download_blob().readall().decode("utf-8-sig")
    logging.info("fetchCsvActivity: CSV 取得完了")

    return [dict(row) for row in csv.DictReader(io.StringIO(csv_data))]


@app.activity_trigger(input_name="row")
def upsertCompanyActivity(row: dict) -> dict:
    """
    Activity2: 1社分の upsert
      - domain で HubSpot を検索
      - 既存なら更新 / なければ新規作成
    """
    import os
    from hubspot import HubSpot
    from hubspot.crm.companies import (
        SimplePublicObjectInputForCreate, SimplePublicObjectInput,
        ApiException, PublicObjectSearchRequest, Filter, FilterGroup
    )

    name = row.get("name", "").strip()
    domain = row.get("domain", "").strip()

    if not name:
        logging.warning("upsertCompanyActivity: name が空のためスキップ")
        return {"name": name, "status": "skipped"}

    token = os.environ.get("HUBSPOT_ACCESS_TOKEN")
    api_client = HubSpot(access_token=token)
    props = {k: v.strip() for k, v in row.items() if v and v.strip()}

    try:
        search_result = api_client.crm.companies.search_api.do_search(
            public_object_search_request=PublicObjectSearchRequest(
                filter_groups=[FilterGroup(filters=[
                    Filter(property_name="domain", operator="EQ", value=domain)
                ])],
                properties=["name", "domain"]
            )
        )

        if search_result.total > 0:
            company_id = search_result.results[0].id
            api_client.crm.companies.basic_api.update(
                company_id=company_id,
                simple_public_object_input=SimplePublicObjectInput(properties=props)
            )
            logging.info(f"upsertCompanyActivity: 更新 {name} (id={company_id})")
            return {"name": name, "status": "updated", "id": company_id}
        else:
            created = api_client.crm.companies.basic_api.create(
                simple_public_object_input_for_create=SimplePublicObjectInputForCreate(properties=props)
            )
            logging.info(f"upsertCompanyActivity: 作成 {name} (id={created.id})")
            return {"name": name, "status": "created", "id": created.id}

    except ApiException as e:
        logging.error(f"upsertCompanyActivity: HubSpot エラー ({name}): {e}")
        return {"name": name, "status": "error", "message": str(e)}


@app.activity_trigger(input_name="results")
def saveResultActivity(results: list) -> dict:
    """Activity3: 全社の処理結果を集約して Blob に保存する"""
    import os, json
    from datetime import datetime, timezone
    from azure.storage.blob import BlobServiceClient

    now = datetime.now(timezone.utc)
    summary = {
        "executed_at": now.isoformat(),
        "total": len(results),
        "created": sum(1 for r in results if r.get("status") == "created"),
        "updated": sum(1 for r in results if r.get("status") == "updated"),
        "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        "error":   sum(1 for r in results if r.get("status") == "error"),
        "details": results,
    }
    logging.info(
        f"saveResultActivity: 作成={summary['created']} 更新={summary['updated']} "
        f"スキップ={summary['skipped']} エラー={summary['error']}"
    )

    conn_str = os.environ.get("AzureWebJobsStorage")
    blob_service = BlobServiceClient.from_connection_string(conn_str)
    result_container = blob_service.get_container_client("companies-import-results")
    if not result_container.exists():
        result_container.create_container()

    blob_name = now.strftime("%Y-%m-%d_%H-%M-%S") + "_durable_import_result.json"
    result_container.upload_blob(blob_name, json.dumps(summary, ensure_ascii=False, default=str))
    logging.info(f"saveResultActivity: 結果保存 {blob_name}")
    return summary
