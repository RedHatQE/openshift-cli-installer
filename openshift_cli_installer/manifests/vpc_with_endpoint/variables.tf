variable "vpc_name" {
  type    = string
  default = "vpc-openshift-observability"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "profile" {
  type    = string
  default = "default"
}

variable "public_instance" {
  type    = string
  default = "ami-02354e95b39ca8dec"
}

variable "private_instance" {
  type    = string
  default = "ami-02354e95b39ca8dec"
}

variable "availabilityZonePub" {
  type    = string
  default = "us-east-1a"
}

variable "availabilityZonePriv" {
  type    = string
  default = "us-east-1b"
}
variable "instanceTenancy" {
  type    = string
  default = "default"
}
variable "dnsSupport" {
  type    = bool
  default = true
}
variable "dnsHostNames" {
  type    = bool
  default = true
}
variable "vpcCIDRblock" {
  type    = string
  default = "10.0.0.0/16"
}
variable "subnetCIDRblock" { # for private subnet
  type    = string
  default = "10.0.0.0/24"
}
variable "subnetCIDRblock1" { # for public subnet
  type    = string
  default = "10.0.1.0/24"
}
variable "destinationCIDRblock" {
  type    = string
  default = "0.0.0.0/0"
}
variable "ingressCIDRblockPriv" {
  type    = string
  default = "10.0.1.0/24"
}
variable "ingressCIDRblockPub" {
  type    = string
  default = "0.0.0.0/0"
}
variable "mapPublicIP" {
  type    = bool
  default = true
}

variable "bucket_name" {
  type    = string
  default = "openshift-observability"
}

variable "key_name" {
  type    = string
  default = "endptkey"
}
