# Input values for provider and create a VPC
provider "aws" {
  region  = var.region
  profile = var.profile
} # end provider

# create the VPC
resource "aws_vpc" "vpc" {
#  name = var.vpc_name
  cidr_block           = var.vpcCIDRblock
  instance_tenancy     = var.instanceTenancy
  enable_dns_support   = var.dnsSupport
  enable_dns_hostnames = var.dnsHostNames
  tags = {
    Name = var.vpc_name
  }
} # end resource

# create the Subnet
resource "aws_subnet" "subnet_public" {
  vpc_id                  = aws_vpc.vpc.id
  cidr_block              = var.subnetCIDRblock
  map_public_ip_on_launch = var.mapPublicIP
  availability_zone       = var.availabilityZonePub
  tags = {
    Name = "${var.vpc_name}-subnet_public"
  }
} # end resource


resource "aws_subnet" "subnet_private" {
  vpc_id                  = aws_vpc.vpc.id
  cidr_block              = var.subnetCIDRblock1
  map_public_ip_on_launch = false #this subnet will be publicy accessible if you do not explicity set this to false
  availability_zone       = var.availabilityZonePriv
  tags = {
    Name = "${var.vpc_name}-subnet_private"
  }
} # end resource


# Create the Security Group
resource "aws_security_group" "security_group_private" {
  name = "${var.vpc_name}-security_group_private"
  vpc_id      = aws_vpc.vpc.id
  description = "My VPC Security Group Private"
  ingress {
    security_groups = [aws_security_group.security_group_public.id]
    from_port       = 0
    to_port         = 0
    protocol        = "-1"
  }
  egress {
    cidr_blocks = ["0.0.0.0/0"]
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
  }
  tags = {
    Name = "${var.vpc_name}-security_group_private"
  }
}


resource "aws_security_group" "security_group_public" {
  name = "${var.vpc_name}-security_group_public"
  vpc_id      = aws_vpc.vpc.id
  description = "My VPC Security Group Public"
  ingress {
    cidr_blocks = ["71.63.125.93/32"]
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
  }

  egress {
    cidr_blocks = [var.ingressCIDRblockPub]
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
  }
  tags = {
    Name = "${var.vpc_name}-security_group_public"
  }
}


# Create the Internet Gateway
resource "aws_internet_gateway" "vpc_gw" {
  vpc_id = aws_vpc.vpc.id
  tags = {
    Name = "${var.vpc_name}-vpc_gw"
  }
} # end resource


# Create the Public Route Table
resource "aws_route_table" "public_route_table" {
  vpc_id = aws_vpc.vpc.id
  tags = {
    Name = "${var.vpc_name}-public_route_table"
  }
} # end resource

resource "aws_route_table" "private_route_table" {
  vpc_id = aws_vpc.vpc.id
  tags = {
    Name = "${var.vpc_name}-private_route_table"
  }
}
# Create the Internet Access
resource "aws_route" "vpc_internet_access" {
  route_table_id         = aws_route_table.public_route_table.id
  destination_cidr_block = var.destinationCIDRblock
  gateway_id             = aws_internet_gateway.vpc_gw.id
} # end resource


# Associate the Public Route Table with the Subnet
resource "aws_route_table_association" "vpc_public_association" {
  subnet_id      = aws_subnet.subnet_public.id
  route_table_id = aws_route_table.public_route_table.id
} # end resource

resource "aws_route_table_association" "vpc_private_association" {
  subnet_id      = aws_subnet.subnet_private.id
  route_table_id = aws_route_table.private_route_table.id
}

# aws_s3_bucket_ownership_controls
resource "aws_s3_bucket_ownership_controls" "s3_bucket_ownership_controls" {
  bucket = aws_s3_bucket.openshift-observability.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

# aws_s3_bucket_acl
resource "aws_s3_bucket_acl" "s3_bucket_acl" {
  depends_on = [aws_s3_bucket_ownership_controls.s3_bucket_ownership_controls]
  bucket = aws_s3_bucket.openshift-observability.bucket
  acl    = "private"
}

#create S3 bucket
resource "aws_s3_bucket" "openshift-observability" {
  bucket_prefix = var.bucket_name
  force_destroy = true
  tags = {
    Name        = var.bucket_name
    Environment = "openshift-cli-installer"
  }
}
# Generating a private_key
resource "tls_private_key" "endptkey" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "local_file" "private-key" {
  content  = tls_private_key.endptkey.private_key_pem
  filename = "endptkey.pem" #naming our key pair so that we can connect via ssh into our instances
}

resource "aws_key_pair" "deployer" {
  key_name   = "endptkey"
  public_key = tls_private_key.endptkey.public_key_openssh
}

resource "aws_instance" "public_instance" {
  ami                    = var.public_instance
  instance_type          = "t2.micro"
  subnet_id              = aws_subnet.subnet_public.id
  iam_instance_profile = aws_iam_instance_profile.ec2profile.name
  key_name               = var.key_name # insert your key file name here
  vpc_security_group_ids = [aws_security_group.security_group_public.id]
  tags = {
    Name = "${var.vpc_name}-public_instance"
  }
}

resource "aws_instance" "private_instance" {
  ami                    = var.private_instance
  instance_type          = "t2.micro"
  subnet_id              = aws_subnet.subnet_private.id
  key_name               = var.key_name # insert your key file name here
  vpc_security_group_ids = [aws_security_group.security_group_private.id]
  iam_instance_profile = aws_iam_instance_profile.ec2profile.name
  tags = {
    Name = "${var.vpc_name}-private_instance"
  }
}
resource "aws_iam_role" "ec2_s3_access_role" {
  name               = "${var.vpc_name}-ec2_s3_access"
  assume_role_policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Action": "sts:AssumeRole",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Effect": "Allow",
      "Sid": ""
    }
  ]
}
EOF
}

resource "aws_iam_instance_profile" "ec2profile" {
     name  = "${var.vpc_name}-ec2profile"
    role = aws_iam_role.ec2_s3_access_role.name
}

resource "aws_iam_policy" "policy" {
  name        = "${var.vpc_name}-policy"
  description = "Access to s3 policy from ec2"
  policy      = <<EOF
{
 "Version": "2012-10-17",
   "Statement": [
       {
           "Effect": "Allow",
           "Action": "s3:*",
           "Resource": "*"
       }

    ]
}
EOF
}


resource "aws_iam_role_policy_attachment" "ec2-attach" {
  role     = aws_iam_role.ec2_s3_access_role.name
  policy_arn = aws_iam_policy.policy.arn
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id       = aws_vpc.vpc.id
  service_name = "com.amazonaws.us-east-1.s3"

}
# associate route table with VPC endpoint
resource "aws_vpc_endpoint_route_table_association" "private_route_table_association" {
  route_table_id  = aws_route_table.private_route_table.id
  vpc_endpoint_id = aws_vpc_endpoint.s3.id
}

output "openshift-observability-bucket" {
  value = aws_s3_bucket.openshift-observability.bucket
}
