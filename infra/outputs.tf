output "base_url" {
  description = "The raw execute-api address. Still works, but custom_url is the one to use."
  value       = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
}

output "webhook_url" {
  description = "Register exactly this in the Square dashboard, subscribed to payment.created, payment.updated, refund.created and refund.updated. The signature is computed over this URL, so a trailing-slash difference is a 401 on every delivery."
  value       = "${var.public_base_url}/api/webhooks/square"
}

output "admin_url" {
  value = "${var.public_base_url}/admin.html"
}

output "log_group" {
  description = "aws logs tail <this> --follow"
  value       = aws_cloudwatch_log_group.api.name
}

output "public_base_url_configured" {
  description = "What the app currently believes its own address is. If this does not equal custom_url, the Square redirect and the webhook signature are pointed at the wrong place."
  value       = var.public_base_url
}

output "custom_url" {
  description = "The demo's public address."
  value       = "https://${aws_apigatewayv2_domain_name.site.domain_name}"
}
