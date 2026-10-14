resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.chaos_pilot.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = true

  tags = {
    Name = "chaos-pilot-public-subnet"
  }
}

resource "aws_subnet" "private" {
  vpc_id                  = aws_vpc.chaos_pilot.id
  cidr_block              = var.private_subnet_cidr
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false

  tags = {
    Name = "chaos-pilot-private-subnet"
  }
}

resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.chaos_pilot.id
  cidr_block              = var.public_subnet_b_cidr
  availability_zone       = var.second_availability_zone
  map_public_ip_on_launch = true

  tags = {
    Name = "chaos-pilot-public-subnet-b"
  }
}

resource "aws_subnet" "private_b" {
  vpc_id                  = aws_vpc.chaos_pilot.id
  cidr_block              = var.private_subnet_b_cidr
  availability_zone       = var.second_availability_zone
  map_public_ip_on_launch = false

  tags = {
    Name = "chaos-pilot-private-subnet-b"
  }
}