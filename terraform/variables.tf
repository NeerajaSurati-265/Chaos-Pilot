variable "aws_region" {
  description = "AWS region for Chaos-Pilot"
  type        = string
  default     = "ap-south-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the Chaos-Pilot VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  description = "CIDR block for the public subnet"
  type        = string
  default     = "10.0.1.0/24"
}

variable "private_subnet_cidr" {
  description = "CIDR block for the private subnet"
  type        = string
  default     = "10.0.2.0/24"
}

variable "public_subnet_b_cidr" {
  description = "CIDR block for the second public subnet"
  type        = string
  default     = "10.0.3.0/24"
}

variable "private_subnet_b_cidr" {
  description = "CIDR block for the second private subnet"
  type        = string
  default     = "10.0.4.0/24"
}

variable "availability_zone" {
  description = "Availability Zone for the initial Chaos-Pilot deployment"
  type        = string
  default     = "ap-south-1a"
}

variable "second_availability_zone" {
  description = "Second Availability Zone for high availability"
  type        = string
  default     = "ap-south-1b"
}