resource "aws_ecs_cluster" "chaos_pilot" {
  name = "chaos-pilot-cluster"

  tags = {
    Name = "chaos-pilot-cluster"
  }
}

resource "aws_ecs_task_definition" "chaos_pilot" {
  family                   = "chaos-pilot-app"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  cpu    = "256"
  memory = "512"

  execution_role_arn = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name      = "chaos-pilot-app"
      image     = var.app_image
      essential = true

      portMappings = [
        {
          containerPort = 80
          hostPort      = 80
          protocol      = "tcp"
        }
      ]
    }
  ])

  tags = {
    Name = "chaos-pilot-task"
  }
}

resource "aws_ecs_service" "chaos_pilot" {
  name            = "chaos-pilot-service"
  cluster         = aws_ecs_cluster.chaos_pilot.id
  task_definition = aws_ecs_task_definition.chaos_pilot.arn

  desired_count = 2

  launch_type      = "FARGATE"
  platform_version = "LATEST"

  network_configuration {
    subnets = [
      aws_subnet.private.id,
      aws_subnet.private_b.id
    ]

    security_groups = [
      aws_security_group.ecs.id
    ]

    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.chaos_pilot.arn
    container_name   = "chaos-pilot-app"
    container_port   = 80
  }

  # Chaos-Pilot registers new revisions and rolls back during experiments.
  # Terraform only sets the initial revision so it doesn't fight those changes.
  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = {
    Name = "chaos-pilot-service"
  }
}

