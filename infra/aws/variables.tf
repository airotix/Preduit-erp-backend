variable "project" {
  default = "preduit"
}

variable "env" {
  default     = "prod"
  description = "Environment name (prod, staging, dev)"
}

variable "aws_region" {
  default = "eu-west-1"
}

variable "vpc_cidr" {
  default = "10.0.0.0/16"
}

variable "az_count" {
  default     = 2
  description = "Number of availability zones"
}

# --- Database ---
variable "db_instance_class" {
  default = "db.t3.micro"
}

variable "db_name" {
  default = "preduit"
}

variable "db_master_username" {
  default   = "erp_admin"
  sensitive = true
}

# --- ECS ---
variable "backend_cpu" {
  default = 512
}

variable "backend_memory" {
  default = 1024
}

variable "backend_desired_count" {
  default = 2
}

variable "frontend_cpu" {
  default = 256
}

variable "frontend_memory" {
  default = 512
}

variable "frontend_desired_count" {
  default = 2
}

# --- ECR image tags (set during CI/CD) ---
variable "backend_image_tag" {
  default = "latest"
}

variable "frontend_image_tag" {
  default = "latest"
}

# --- Redis ---
variable "redis_node_type" {
  default = "cache.t4g.micro"
}

# --- Domain ---
variable "domain_name" {
  default     = ""
  description = "Custom domain (e.g. app.preduit.com). Leave blank to use ALB DNS."
}

variable "certificate_arn" {
  default     = ""
  description = "ACM certificate ARN for HTTPS. Required if domain_name is set."
}
