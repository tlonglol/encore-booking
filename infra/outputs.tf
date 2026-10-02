output "function_url" {
  description = "The whole system: landing page, funnel, admin and API. Set public_base_url to this (no trailing slash) and apply again."
  value       = aws_lambda_function_url.api.function_url
}

output "webhook_url" {
  description = "Register exactly this in the Square dashboard, subscribed to payment.created, payment.updated, refund.created and refund.updated. The signature is computed over this URL, so a trailing-slash difference is a 401 on every delivery."
  value       = "${trimsuffix(aws_lambda_function_url.api.function_url, "/")}/api/webhooks/square"
}

output "admin_url" {
  value = "${trimsuffix(aws_lambda_function_url.api.function_url, "/")}/admin.html"
}

output "log_group" {
  description = "aws logs tail <this> --follow"
  value       = aws_cloudwatch_log_group.api.name
}

output "public_base_url_configured" {
  description = "What the app currently believes its own address is. Empty means the first apply has not been followed by the second, so Square redirects and webhooks are not wired yet."
  value       = var.public_base_url
}
