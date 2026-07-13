variable "project" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "cluster_security_group_id" {
  type = string
}

variable "gpu_instance_type" {
  type = string
}

variable "gpu_node_count" {
  type = number
}

variable "instance_profile_name" {
  type = string
}

variable "root_volume_size_gb" {
  type = number
}

variable "ssh_key_name" {
  type    = string
  default = null
}

variable "fsx_dns_name" {
  type = string
}

variable "fsx_mount_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
