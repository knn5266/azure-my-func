import azure.functions as func
import logging

app = func.FunctionApp()

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
        properties = ["email", "firstname", "lastname", "phone", "company", "jobtitle"]
        contacts = api_client.crm.contacts.get_all(properties=properties)
        logging.info(f"全件取得件数: {len(contacts)}")

        # Blob Storageに保存
        conn_str = os.environ.get("AzureWebJobsStorage")
        blob_service = BlobServiceClient.from_connection_string(conn_str)
        container = blob_service.get_container_client("contacts")

        if not container.exists():
            container.create_container()

        # 差分だけ保存（前回実行から5分以内に更新されたもの）
        from hubspot.crm.contacts import PublicObjectSearchRequest, Filter, FilterGroup
        from datetime import timedelta
        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        diff_filter = Filter(property_name="lastmodifieddate", operator="GTE", value=five_min_ago)
        diff_request = PublicObjectSearchRequest(
            filter_groups=[FilterGroup(filters=[diff_filter])],
            properties=properties
        )
        diff_result = api_client.crm.contacts.search_api.do_search(public_object_search_request=diff_request)
        logging.info(f"差分件数: {diff_result.total}")

        if diff_result.total > 0:
            diff_data = [r.to_dict() for r in diff_result.results]
            blob_name = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") + "_diff.json"
            container.upload_blob(blob_name, json.dumps(diff_data, default=str))
            logging.info(f"差分 Blob保存完了: {blob_name}")
        else:
            logging.info("差分なし：保存スキップ")

        # コンタクトを新規作成
        from hubspot.crm.contacts import SimplePublicObjectInputForCreate
        new_contact = SimplePublicObjectInputForCreate(properties={
            "email": "test.azure@example.com",
            "firstname": "Azure",
            "lastname": "Functions",
            "jobtitle": "Engineer",
            "company": "Microsoft"
        })
        try:
            created = api_client.crm.contacts.basic_api.create(simple_public_object_input_for_create=new_contact)
            logging.info(f"新規作成完了: id={created.id}, email={created.properties.get('email')}")
        except ApiException as e:
            if e.status == 409:
                logging.info("コンタクトは既に存在します（スキップ）")
            else:
                raise

        # コンタクトのプロパティを更新（Brianのjobtitleを更新）
        from hubspot.crm.contacts import SimplePublicObjectInput
        brian = next((c for c in contacts if c.properties.get("firstname") == "Brian"), None)
        if brian:
            update_input = SimplePublicObjectInput(properties={"jobtitle": "CEO"})
            api_client.crm.contacts.basic_api.update(contact_id=brian.id, simple_public_object_input=update_input)
            logging.info(f"更新完了: {brian.properties.get('firstname')} → jobtitle=CEO")
        else:
            logging.info("Brianが見つかりませんでした")

        # 複数フィルター検索
        from hubspot.crm.contacts import PublicObjectSearchRequest, Filter, FilterGroup

        # AND条件: @hubspot.com かつ firstname が Brian
        and_filters = FilterGroup(filters=[
            Filter(property_name="email", operator="CONTAINS_TOKEN", value="*@hubspot.com"),
            Filter(property_name="firstname", operator="EQ", value="Brian"),
        ])
        and_request = PublicObjectSearchRequest(filter_groups=[and_filters], properties=["email", "firstname", "lastname"])
        and_result = api_client.crm.contacts.search_api.do_search(public_object_search_request=and_request)
        logging.info(f"AND検索結果件数: {and_result.total}")

        # OR条件: firstname が Brian OR firstname が Maria
        or_request = PublicObjectSearchRequest(
            filter_groups=[
                FilterGroup(filters=[Filter(property_name="firstname", operator="EQ", value="Brian")]),
                FilterGroup(filters=[Filter(property_name="firstname", operator="EQ", value="Maria")]),
            ],
            properties=["email", "firstname", "lastname"]
        )
        or_result = api_client.crm.contacts.search_api.do_search(public_object_search_request=or_request)
        logging.info(f"OR検索結果件数: {or_result.total}")

        # Dealsも取得
        deal_properties = ["dealname", "amount", "dealstage", "closedate", "pipeline"]
        deals = api_client.crm.deals.get_all(properties=deal_properties)
        logging.info(f"Deals取得件数: {len(deals)}")

        deals_data = [d.to_dict() for d in deals]
        blob_name_deals = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") + "_deals.json"
        container.upload_blob(blob_name_deals, json.dumps(deals_data, default=str))
        logging.info(f"Deals Blob保存完了: {blob_name_deals}")

        # Companiesも取得
        company_properties = ["name", "domain", "industry", "city", "phone"]
        companies = api_client.crm.companies.get_all(properties=company_properties)
        logging.info(f"Companies取得件数: {len(companies)}")

        companies_data = [c.to_dict() for c in companies]
        blob_name_companies = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") + "_companies.json"
        container.upload_blob(blob_name_companies, json.dumps(companies_data, default=str))
        logging.info(f"Companies Blob保存完了: {blob_name_companies}")

    except ApiException as e:
        logging.error(f"HubSpot APIエラー: {e}")
    except Exception as e:
        logging.error(f"エラー: {e}")


def _run_company_batch_import():
    import os
    import json
    import csv
    import io
    from datetime import datetime, timezone
    from hubspot import HubSpot
    from hubspot.crm.companies import SimplePublicObjectInputForCreate, SimplePublicObjectInput, ApiException
    from hubspot.crm.companies import PublicObjectSearchRequest, Filter, FilterGroup
    from azure.storage.blob import BlobServiceClient

    logging.info("=== 会社一括インポートバッチ 開始 ===")
    start_time = datetime.now(timezone.utc)

    conn_str = os.environ.get("AzureWebJobsStorage")
    token = os.environ.get("HUBSPOT_ACCESS_TOKEN")
    api_client = HubSpot(access_token=token)
    blob_service = BlobServiceClient.from_connection_string(conn_str)

    created_count = 0
    updated_count = 0
    skipped_count = 0
    error_count = 0
    results = []

    # Blob Storage から CSV 取得
    container = blob_service.get_container_client("companies-import")
    if not container.exists():
        container.create_container()

    blob_client = container.get_blob_client("companies.csv")
    csv_data = blob_client.download_blob().readall().decode("utf-8-sig")
    logging.info("CSV ファイル取得完了")

    reader = csv.DictReader(io.StringIO(csv_data))
    rows = list(reader)
    logging.info(f"CSV レコード数: {len(rows)}")

    for row in rows:
        name = row.get("name", "").strip()
        domain = row.get("domain", "").strip()
        industry = row.get("industry", "").strip()
        city = row.get("city", "").strip()
        phone = row.get("phone", "").strip()

        if not name:
            logging.warning(f"name が空のためスキップ: {row}")
            skipped_count += 1
            continue

        try:
            # domain で既存会社を検索
            search_request = PublicObjectSearchRequest(
                filter_groups=[FilterGroup(filters=[
                    Filter(property_name="domain", operator="EQ", value=domain)
                ])],
                properties=["name", "domain"]
            )
            search_result = api_client.crm.companies.search_api.do_search(
                public_object_search_request=search_request
            )

            props = {
                "name": name,
                "domain": domain,
                "industry": industry,
                "city": city,
                "phone": phone,
            }

            if search_result.total > 0:
                # 既存 → 更新
                company_id = search_result.results[0].id
                update_input = SimplePublicObjectInput(properties=props)
                api_client.crm.companies.basic_api.update(
                    company_id=company_id,
                    simple_public_object_input=update_input
                )
                logging.info(f"更新: {name} (id={company_id})")
                updated_count += 1
                results.append({"name": name, "status": "updated", "id": company_id})
            else:
                # 新規作成
                create_input = SimplePublicObjectInputForCreate(properties=props)
                created = api_client.crm.companies.basic_api.create(
                    simple_public_object_input_for_create=create_input
                )
                logging.info(f"作成: {name} (id={created.id})")
                created_count += 1
                results.append({"name": name, "status": "created", "id": created.id})

        except ApiException as e:
            logging.error(f"HubSpot エラー ({name}): {e}")
            error_count += 1
            results.append({"name": name, "status": "error", "message": str(e)})

    # 処理結果を Blob に保存
    end_time = datetime.now(timezone.utc)
    summary = {
        "executed_at": start_time.isoformat(),
        "duration_seconds": (end_time - start_time).seconds,
        "total": len(rows),
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "error": error_count,
        "details": results,
    }
    result_blob_name = start_time.strftime("%Y-%m-%d_%H-%M-%S") + "_company_import_result.json"
    result_container = blob_service.get_container_client("companies-import-results")
    if not result_container.exists():
        result_container.create_container()
    result_container.upload_blob(result_blob_name, json.dumps(summary, ensure_ascii=False, default=str))

    logging.info("=== 会社一括インポートバッチ 完了 ===")
    logging.info(f"作成: {created_count}件 / 更新: {updated_count}件 / スキップ: {skipped_count}件 / エラー: {error_count}件")
    logging.info(f"結果保存: {result_blob_name}")

    return summary


@app.timer_trigger(schedule="0 0 2 * * *", arg_name="batchTimer", run_on_startup=False, use_monitor=False)
def companyBatchImport(batchTimer: func.TimerRequest) -> None:
    try:
        _run_company_batch_import()
    except Exception as e:
        logging.error(f"バッチ全体エラー: {e}")
        raise


@app.route(route="runCompanyBatch", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
def runCompanyBatchHttp(req: func.HttpRequest) -> func.HttpResponse:
    import json
    logging.info("会社一括インポートバッチ 手動実行")
    try:
        summary = _run_company_batch_import()
        return func.HttpResponse(
            json.dumps(summary, ensure_ascii=False, default=str),
            mimetype="application/json",
            status_code=200
        )
    except Exception as e:
        logging.error(f"バッチ手動実行エラー: {e}")
        return func.HttpResponse(f"Error: {e}", status_code=500)
