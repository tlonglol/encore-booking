# encore.tlong.dev in front of the existing API Gateway. The execute-api URL keeps working alongside it.
#
# The tlong.dev zone is a data lookup on purpose. It's my personal domain and gets created by hand, so
# nothing here can ever delete or replace it.
variable "domain_name" {
  type        = string
  default     = "encore.tlong.dev"
  description = "Public hostname for the demo."
}

data "aws_route53_zone" "root" {
  name = "tlong.dev"
}

# Same region as the API. Only CloudFront needs its certs in us-east-1.
resource "aws_acm_certificate" "site" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for option in aws_acm_certificate.site.domain_validation_options : option.domain_name => {
      name   = option.resource_record_name
      type   = option.resource_record_type
      record = option.resource_record_value
    }
  }

  zone_id = data.aws_route53_zone.root.zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60
}

resource "aws_acm_certificate_validation" "site" {
  certificate_arn         = aws_acm_certificate.site.arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

resource "aws_apigatewayv2_domain_name" "site" {
  domain_name = var.domain_name

  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.site.certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

# No api_mapping_key, so paths are the same as on the execute-api URL.
resource "aws_apigatewayv2_api_mapping" "site" {
  api_id      = aws_apigatewayv2_api.api.id
  domain_name = aws_apigatewayv2_domain_name.site.id
  stage       = aws_apigatewayv2_stage.default.id
}

resource "aws_route53_record" "site" {
  zone_id = data.aws_route53_zone.root.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_apigatewayv2_domain_name.site.domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.site.domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}
