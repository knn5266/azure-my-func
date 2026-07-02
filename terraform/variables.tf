variable "subscription_id" {
  description = "Azure Subscription ID"
  type        = string
  default     = "f38fbc09-1a15-496f-946b-64bc282ccdbb"
}

variable "resource_group_name" {
  description = "Resource Group Name"
  type        = string
  default     = "rg-hubspot-func"
}

variable "location" {
  description = "Azure Region"
  type        = string
  default     = "japaneast"
}

variable "hubspot_access_token" {
  description = "HubSpot Private App Access Token"
  type        = string
  sensitive   = true
}
