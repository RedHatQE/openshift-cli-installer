import os
from clouds.azure.azure_utils import get_aro_supported_versions
from clouds.azure.session_clients import (
    get_subscription_id,
    get_azure_credentials,
    get_aro_client,
    get_network_client,
    get_resource_client,
    get_authorization_client,
)
from clouds.azure.vars import azure_client_credentials_env_vars
from openshift_cli_installer.utils.general import random_resource_postfix
from openshift_cli_installer.libs.clusters.ocp_cluster import OCPCluster
from simple_logger.logger import get_logger


class AROCluster(OCPCluster):
    # See Azure SDK generated samples for more info:
    # https://github.com/Azure/azure-sdk-for-python/tree/main/sdk
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(f"{self.__class__.__module__}-{self.__class__.__name__}")
        self.__set_azure_authentication()
        self.__set_cluster_params()

    def __set_azure_authentication(self):
        azure_credentials = get_azure_credentials()
        self.subscription_id = get_subscription_id()
        self.tenant_id = os.environ[azure_client_credentials_env_vars["tenant_id"]]
        self.client_id = os.environ[azure_client_credentials_env_vars["client_id"]]
        self.client_secret = os.environ[azure_client_credentials_env_vars["client_secret"]]

        self.aro_client = get_aro_client(credential=azure_credentials)
        self.network_client = get_network_client(credential=azure_credentials)
        self.resource_client = get_resource_client(credential=azure_credentials)
        self.authorization_client = get_authorization_client(credential=azure_credentials)

    def __set_cluster_params(self):
        # TODO: get cluster params from cluster_data.yaml if exists
        self.region = self.cluster_info.get("region")
        self.version = self.cluster_info.get("version")
        self.cluster_name = self.cluster_info.get("name", f"msi-aro-{random_resource_postfix()}")
        self.domain = self.cluster_info.get("domain", self.cluster_name)
        self.resource_group_name = self.cluster_info.get("resource-group-name", f"aro-rg-{random_resource_postfix()}")
        self.cluster_resource_group_name = self.cluster_info.get(
            "cluster-resource-group-name", f"{self.cluster_name}-rg"
        )
        self.virtual_network_name = self.cluster_info.get(
            "virtual-network-name", f"aro-vnet-{random_resource_postfix()}"
        )
        self.workers_subnet_name = self.cluster_info.get(
            "workers-subnet-name", f"workers-subnet-{random_resource_postfix()}"
        )
        self.master_subnet_name = self.cluster_info.get(
            "master-subnet-name", f"master-subnet-{random_resource_postfix()}"
        )
        self.master_vm_size = self.cluster_info.get("master-vm-size", "Standard_D8s_v3")
        self.worker_vm_size = self.cluster_info.get("workers-vm-size", "Standard_D4s_v3")
        self.workers_count = self.cluster_info.get("workers-count", 3)
        self.workers_disk_size = self.cluster_info.get("workers-disk-size", 128)
        self.network_pod_cidr = self.cluster_info.get("network-pod-cidr", "10.128.0.0/14")
        self.network_service_cidr = self.cluster_info.get("network-service-cidr", "172.30.0.0/16")
        self.fips = self.cluster_info.get("fips", False)

        if pull_secret_file := self.cluster_info.get("pull-secret-file"):
            with open(pull_secret_file, "r") as ps_file:
                self.pull_secret = ps_file.read()

        self.__assert_cluster_params_are_valid()

    def __assert_cluster_params_are_valid(self):
        def assert_is_supported_version():
            aro_supported_versions = get_aro_supported_versions(aro_client=self.aro_client, region=self.region)
            assert self.version in aro_supported_versions, (
                f"Version {self.version} is not supported for ARO, supported " f"versions: {aro_supported_versions}"
            )

        assert_is_supported_version()

    def __create_cluster_resources(self):
        self.__create_resource_group(resource_group_name=self.resource_group_name)
        self.__create_resource_group(resource_group_name=self.cluster_resource_group_name)
        self.__create_virtual_network()
        # TODO: write cluster resources to cluster_data.yaml

    def __destroy_cluster_resources(self):
        self.__delete_virtual_network()
        self.__delete_resource_group(resource_group_name=self.cluster_resource_group_name)
        self.__delete_resource_group(resource_group_name=self.resource_group_name)

    def __create_resource_group(self, resource_group_name=None):
        self.logger.info(f"Creating resource group {resource_group_name}")
        resource_group_create = self.resource_client.resource_groups.create_or_update(
            resource_group_name=resource_group_name, parameters={"location": self.region}
        )

        return resource_group_create

    def __delete_resource_group(self, resource_group_name=None):
        self.logger.info(f"Deleting resource group {resource_group_name}")
        resource_group_delete = self.resource_client.resource_groups.begin_delete(
            resource_group_name=resource_group_name
        )

        return resource_group_delete

    def __create_virtual_network(
        self,
        vnet_address_prefix="10.0.0.0/22",
        workers_subnet_address_prefix="10.0.0.0/23",
        master_subnet_address_prefix="10.0.2.0/23",
    ):
        def create_subnet(subnet_name, subnet_address_prefix):
            self.logger.info(
                f"Creating subnet {self.master_subnet_name} in virtual network {self.virtual_network_name}"
            )
            self.network_client.subnets.begin_create_or_update(
                resource_group_name=self.resource_group_name,
                virtual_network_name=self.virtual_network_name,
                subnet_name=subnet_name,
                subnet_parameters={"properties": {"addressPrefix": subnet_address_prefix}},
            ).result()

        self.logger.info(f"Creating virtual network {self.virtual_network_name}")
        vnet_create = self.network_client.virtual_networks.begin_create_or_update(
            virtual_network_name=self.virtual_network_name,
            resource_group_name=self.resource_group_name,
            parameters={"location": self.region, "address_space": {"address_prefixes": [vnet_address_prefix]}},
        ).result()

        # Create worker + master nodes subnets
        create_subnet(subnet_name=self.master_subnet_name, subnet_address_prefix=master_subnet_address_prefix)
        create_subnet(subnet_name=self.workers_subnet_name, subnet_address_prefix=workers_subnet_address_prefix)

        # # Assign client a NetworkContributor role on cluster's virtual network
        # role_assignment_scope = f"subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}"
        # network_contributor_role = [
        #     role
        #     for
        #     role in self.authorization_client.role_definitions.list(
        #         scope=role_assignment_scope,
        #         filter="roleName eq '{}'".format("Network Contributor")
        #     )
        # ][0]
        # response = self.authorization_client.role_assignments.create(
        #     scope=role_assignment_scope,
        #     role_assignment_name=network_contributor_role.id,
        #     parameters={
        #         "properties": {
        #             "principalId": self.client_id,
        #             "principalType": "ServicePrincipal",
        #             "roleDefinitionId": f"/subscriptions/{self.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{network_contributor_role.id}",
        #         }
        #     },
        # )

        return vnet_create

    def __delete_virtual_network(self):
        self.logger.info(f"Deleting virtual network {self.virtual_network_name}")
        vnet_delete = self.network_client.virtual_networks.begin_delete(
            virtual_network_name=self.virtual_network_name,
            resource_group_name=self.resource_group_name,
        ).result()

        return vnet_delete

    def create_cluster(self):
        self.__create_cluster_resources()
        self.timeout_watch = self.start_time_watcher()
        # TODO: check how to handle stage/prod
        cluster_params = {
            "clusterProfile": {
                "domain": self.domain,
                "fipsValidatedModules": "Enabled" if self.fips else "Disabled",
                "pullSecret": self.pull_secret,
                "resourceGroupId": f"/subscriptions/{self.subscription_id}/resourcegroups/{self.cluster_resource_group_name}",
                "version": self.version,
            },
            "masterProfile": {
                "encryptionAtHost": "Enabled",
                "subnetId": f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}/providers/Microsoft"
                f".Network/virtualNetworks/{self.virtual_network_name}/subnets/{self.master_subnet_name}",
                "vmSize": self.master_vm_size,
            },
            "workerProfiles": [
                {
                    "count": self.workers_count,
                    "diskSizeGB": self.workers_disk_size,
                    "name": "worker",
                    "encryptionAtHost": "Enabled",
                    "subnetId": f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}/providers/Microsoft"
                    f".Network/virtualNetworks/{self.virtual_network_name}/subnets/{self.workers_subnet_name}",
                    "vmSize": self.worker_vm_size,
                }
            ],
            "servicePrincipalProfile": {
                "clientId": self.client_id,
                "clientSecret": self.client_secret,
            },
            "apiserverProfile": {"visibility": "Public"},
            "consoleProfile": {},
            "ingressProfiles": [{"name": "default", "visibility": "Public"}],
            "networkProfile": {
                "podCidr": self.network_pod_cidr,
                "preconfiguredNSG": "Disabled",
                "serviceCidr": self.network_service_cidr,
            },
        }

        self.logger.info(f"Creating ARO cluster {self.cluster_name}")
        aro_cluster_create = self.aro_client.open_shift_clusters.begin_create_or_update(
            resource_name=self.cluster_name,
            resource_group_name=self.resource_group_name,
            parameters={"location": self.region, "properties": cluster_params},
        ).result()

        return aro_cluster_create

    def destroy_cluster(self):
        self.timeout_watch = self.start_time_watcher()
        self.logger.info(f"Destroying ARO cluster {self.cluster_name}")
        aro_cluster_delete = self.aro_client.open_shift_clusters.begin_delete(
            resource_group_name=self.cluster_resource_group_name,
            resource_name=self.cluster_name,
        ).result()

        self.__destroy_cluster_resources()

        return aro_cluster_delete
