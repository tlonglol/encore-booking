output "base_url" {
  description = "The whole system: landing page, funnel, admin and API. Set public_base_url to this and apply again."
  value       = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
}

output "webhook_url" {
  description = "Register exactly this in the Square dashboard, subscribed to payment.created, payment.updated, refund.created and refund.updated. The signature is computed over this URL, so a trailing-slash difference is a 401 on every delivery."
  value       = "${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}/api/webhooks/square"
}

output "admin_url" {
  value = "${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}/admin.html"
}

output "log_group" {
  description = "aws logs tail <this> --follow"
  value       = aws_cloudwatch_log_group.api.name
}

output "public_base_url_configured" {
  description = "What the app currently believes its own address is. If this does not equal base_url, the Square redirect and the webhook signature are pointed at the wrong place."
  value       = var.public_base_url
}
