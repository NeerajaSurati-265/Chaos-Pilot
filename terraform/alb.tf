resource "aws_lb" "chaos_pilot" {
  name               = "chaos-pilot-alb"
  internal           = false
  load_balancer_type = "application"

  subnets = [
    aws_subnet.public.id,
    aws_subnet.public_b.id
  ]

  security_groups = [
    aws_security_group.alb.id
  ]

  tags = {
    Name = "chaos-pilot-alb"
  }
}

resource "aws_lb_target_group" "chaos_pilot" {
  name        = "chaos-pilot-tg"
  port        = 80
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.chaos_pilot.id

  health_check {
    enabled             = true
    path                = var.health_check_path
    protocol            = "HTTP"
    port                = "traffic-port"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
  }

  tags = {
    Name = "chaos-pilot-target-group"
  }
}

resource "aws_lb_listener" "chaos_pilot" {
  load_balancer_arn = aws_lb.chaos_pilot.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.chaos_pilot.arn
  }
}