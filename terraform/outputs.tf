output "alb_dns_name" {
  description = "Public address of the test workload"
  value       = aws_lb.chaos_pilot.dns_name
}

output "target_group_arn" {
  description = "Set as CHAOS_PILOT_TARGET_GROUP_ARN for the API"
  value       = aws_lb_target_group.chaos_pilot.arn
}

output "test_app_repository_url" {
  description = "Push the test app image here"
  value       = aws_ecr_repository.test_app.repository_url
}
