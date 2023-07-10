# openshift-cli-installer
Basic openshift install cli wrapper

### Container
image locate at [openshift-cli-installer](https://quay.io/repository/redhat_msi/openshift-cli-installer)  
To pull the image: `podman pull quay.io/redhat_msi/openshift-cli-installer`

### Usages

```
podman run quay.io/redhat_msi/openshift-cli-installer --help
podman run quay.io/redhat_msi/openshift-cli-installer --action --help
podman run quay.io/redhat_msi/openshift-cli-installer --action create --cluster --help
```

### Local run

clone the [repository](https://github.com/RedHatQE/openshift-cli-installer.git)

```
git clone https://github.com/RedHatQE/openshift-cli-installer.git
```

Install [poetry](https://github.com/python-poetry/poetry)

Use `poetry run app/cli.py` to execute the cli.

```
poetry install
poetry run python app/cli.py --help
```

Each command can be run via container `podman run quay.io/redhat_msi/openshift-cli-installer` or via poetry command `poetry run app/cli.py`

## Clusters

Cluster/s to create or destroy.
Flag --action accepts either "create" or "destroy"

### User args

Pass `--parallel` to run clusters installation in parallel

Pass `--clusters-install-data-directory` or set `CLUSTER_INSTALL_DATA_DIRECTORY` env var to indicate path for clusters installation data gathered

Pass `--pull-secret-file` or set `PULL_SECRET` env var to indicate secret json file, which can also be obtained from console.redhat.com"

Pass `--s3-bucket-name` and/or `--s3-bucket-path` to give S3 bucket name and path to store install data backups

Flag `--cluster` accepts multiple args, the format is `arg=value;`

###### Required args:
* `name=name`: Name of the cluster to install/uninstall
* `base_domain`: Base domain for the cluster.
* `platform`: Cloud platform to install the cluster on. (Currently only AWS supported).
* `region`: Region to use for the cloud platform.
* `version`: Openshift cluster version to install

###### Cluster args:
Check install-config-template.j2 for variables that can be overwritten by the user, in order to modify cluster configuration.
Examples:
* `fips=true`: Enable fips on cluster
* `worker_flavor=m5.xlarge`: Set compute machine type 
* `worker_replicas=6`: Set number of replicas

### Create Cluster
##### One Cluster

```
podman run quay.io/redhat_msi/openshift-cli-installer \
    --action create \
    -c 'name=cluster1;base_domain=aws.domain.com;platform=aws;region=us-east-2;version=4.14.0-ec.2'
```

##### Multiple Clusters

To run multiple addons install in parallel pass -p,--parallel.

```
podman run quay.io/redhat_msi/openshift-cli-installer \
    --action create \
    -c 'name=cluster1;base_domain=aws.domain.com;platform=aws;region=us-east-2;version=4.14.0-ec.2' \
    -c 'name=cluster2;platform=aws;region=us-east-1;version=4.13' \
    --parallel
    
```

#### Destroy Cluster
##### One Cluster

```
podman run quay.io/redhat_msi/openshift-cli-installer \
    --action destroy \
    -c 'name=cluster1;base_domain=aws.domain.com;platform=aws;region=us-east-2;version=4.14.0-ec.2'
```

##### Multiple Cluster

To run multiple addons uninstall in parallel pass -p,--parallel.

```
podman run quay.io/redhat_msi/openshift-cli-installer \
    --action destroy \
    -c 'name=cluster1' \
    -c 'name=cluster2' \
    --parallel \
    --clusters-install-data-directory user_path
    
```
