# Resource Group
resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

# Storage Account
resource "azurerm_storage_account" "main" {
  name                     = "sthubspotfunc01"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

# App Service Plan (Consumption)
resource "azurerm_service_plan" "main" {
  name                = "JapanEastLinuxDynamicPlan"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "Y1"
}

# Azure Functions
resource "azurerm_linux_function_app" "main" {
  name                       = "func-hubspot01"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  storage_account_name       = azurerm_storage_account.main.name
  storage_account_access_key = azurerm_storage_account.main.primary_access_key
  service_plan_id            = azurerm_service_plan.main.id

  site_config {
    application_stack {
      python_version = "3.11"
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME"  = "python"
    "HUBSPOT_ACCESS_TOKEN"      = var.hubspot_access_token
    "SCM_DO_BUILD_DURING_DEPLOYMENT" = "true"
  }
}

# API Management
resource "azurerm_api_management" "main" {
  name                = "apim-hubspot01"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  publisher_name      = "Kanon"
  publisher_email     = "mkc35r.0@gmail.com"
  sku_name            = "Consumption_0"
}

# APIM API
resource "azurerm_api_management_api" "hubspot" {
  name                = "hubspot-api"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  revision            = "1"
  display_name        = "HubSpot API"
  path                = "hubspot"
  protocols           = ["https"]
  service_url         = "https://func-hubspot01.azurewebsites.net/api"
}

# APIM Operation: コンタクト作成
resource "azurerm_api_management_api_operation" "create_contact" {
  operation_id        = "create-contact"
  api_name            = azurerm_api_management_api.hubspot.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "コンタクト作成"
  method              = "POST"
  url_template        = "/httpTrigger"
}

# APIM Operation: Webhook受信
resource "azurerm_api_management_api_operation" "webhook" {
  operation_id        = "webhook"
  api_name            = azurerm_api_management_api.hubspot.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Webhook受信"
  method              = "POST"
  url_template        = "/webhook"
}
