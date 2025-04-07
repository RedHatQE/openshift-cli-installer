FROM python:3.13

ARG GITHUB_API_TOKEN

RUN apt-get update \
  && apt-get install -y ssh gnupg software-properties-common curl gpg wget vim \
  && apt-get clean autoclean \
  && apt-get autoremove --yes \
  && rm -rf /var/lib/{apt,dpkg,cache,log}/

# Install Hashicorp's APT repository for Terraform
RUN wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/hashicorp.list

RUN apt-get update \
  && apt-get install -y terraform

# Install the Rosa CLI
RUN curl -L https://mirror.openshift.com/pub/openshift-v4/clients/rosa/latest/rosa-linux.tar.gz --output /tmp/rosa-linux.tar.gz \
  && tar xvf /tmp/rosa-linux.tar.gz --no-same-owner \
  && mv rosa /usr/bin/rosa \
  && chmod +x /usr/bin/rosa \
  && rosa version

# Install the OpenShift CLI (OC)
RUN curl -L https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/stable/openshift-client-linux.tar.gz --output /tmp/openshift-client-linux.tar.gz \
  && tar xvf /tmp/openshift-client-linux.tar.gz --no-same-owner \
  && mv oc /usr/bin/oc \
  && mv kubectl /usr/bin/kubectl \
  && chmod +x /usr/bin/oc

# Install the kubernetes CLI (kubectl)
RUN chmod +x /usr/bin/kubectl \
  && curl -L https://github.com/regclient/regclient/releases/latest/download/regctl-linux-amd64 --output /usr/bin/regctl \
  && chmod +x /usr/bin/regctl

# Install the Advanced cluster management CLI (cm)
RUN curl -s https://api.github.com/repos/stolostron/cm-cli/releases/latest \
  | grep "browser_download_url.*linux_amd64.tar.gz" \
  | cut -d : -f 2,3 \
  | tr -d \" \
  | wget -i - \
  && tar xvf cm_linux_amd64.tar.gz --no-same-owner \
  && mv cm /usr/bin/cm

COPY pyproject.toml uv.lock README.md /openshift-cli-installer/
COPY openshift_cli_installer /openshift-cli-installer/openshift_cli_installer/

WORKDIR /openshift-cli-installer
RUN mkdir clusters-install-data \
  && mkdir ssh-key \
  && ssh-keygen -t rsa -N '' -f /openshift-cli-installer/ssh-key/id_rsa \
  && chmod 644 /openshift-cli-installer/ssh-key/id_rsa

ENV UV_PYTHON=python3.13
ENV UV_COMPILE_BYTECODE=1
ENV UV_NO_SYNC=1
ENV UV_CACHE_DIR=${APP_DIR}/.cache

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/bin/
RUN uv sync
RUN chgrp -R 0 ${APP_DIR}/.cache && \
    chmod -R g=u ${APP_DIR}/.cache
ENTRYPOINT ["uv", "run", "openshift_cli_installer/cli.py"]
