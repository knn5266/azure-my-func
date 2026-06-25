import azure.functions as func
import datetime
import json
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
    
    if myTimer.past_due:
        logging.info('The timer is past due!')

    import os
    api_key = os.environ.get("MY_API_KEY")
    logging.info(f'Python timer trigger function executed. API_KEY={api_key}')