variable "aws_region" {
  type        = string
  description = "The region to create the ROSA cluster in"
}

variable "az_ids" {
  type = list
  description = "A list of region-mapped AZ IDs that a subnet should get deployed into"
}

variable "cluster_name" {
  type        = string
  description = "The name of the ROSA cluster to create"
}

variable "cidr" {
  type        = string
  description = "VPC cidr"
  default     = "10.0.0.0/16"
}

variable "private_subnets" {
  type = list
  description = "VPC private subnets"
  default = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "public_subnets" {
  type = list
  description = "VPC public subnets"
  default = ["10.0.101.0/24", "10.0.102.0/24"]
}

provider "aws" {
  region = var.aws_region
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "< 7.0.0"

  name = "${var.cluster_name}-vpc"
  cidr = "${var.cidr}"

  azs             = var.az_ids
  private_subnets = var.private_subnets
  public_subnets  = var.public_subnets

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
  enable_dns_support   = true
}

output "cluster-private-subnet" {
  value = module.vpc.private_subnets[0]
}

output "cluster-public-subnet" {
  value = module.vpc.public_subnets[0]
}

output "node-private-subnet" {
  value = module.vpc.private_subnets[1]
}
