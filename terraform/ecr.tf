resource "aws_ecr_repository" "test_app" {
  name                 = "chaos-pilot-test-app"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  tags = {
    Name = "chaos-pilot-test-app"
  }
}
