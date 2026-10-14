resource "aws_vpc" "chaos_pilot" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "chaos-pilot-vpc"
  }
}