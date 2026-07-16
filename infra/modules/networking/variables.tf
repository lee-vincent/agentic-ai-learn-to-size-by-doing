variable "project" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "subnet_cidr" {
  type = string
}

variable "gpu_instance_type" {
  description = "Used only to discover which AZ in this region actually offers this instance type, so the subnet lands somewhere capacity can exist."
  type        = string
}

variable "ssh_ingress_cidrs" {
  type    = list(string)
  default = []
}

variable "internal_ingress_cidrs" {
  type = list(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
