variable "project" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "fsx_security_group_id" {
  type = string
}

variable "fsx_storage_capacity_gib" {
  type = number
}

variable "fsx_per_unit_storage_throughput" {
  type = number
}

variable "tags" {
  type    = map(string)
  default = {}
}
