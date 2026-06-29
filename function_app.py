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
