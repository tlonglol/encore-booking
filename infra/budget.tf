# Monthly spend ceiling with email alerts. A budget does not stop anything by itself; it is the one
# signal that catches a cost this file did not think of. The caps that actually stop spend are the
# stage throttle (apigateway.tf), reserved concurrency (lambda.tf) and the kill switch (killswitch.tf).
variable "alert_email" {
  type        = string
  default     = "ltyler446@gmail.com"
  description = "Where budget and kill-switch notices go."
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = "10"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
