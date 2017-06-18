FROM python:3.6-slim
MAINTAINER Gabe Mulley <gabe@edx.org>

USER root

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# Install system packages from apt
RUN apt-get update -qq && apt-get install -y --no-install-recommends git ca-certificates wget

# Install gosu
ARG GOSU_VERSION=1.10
RUN set -ex; \
	dpkgArch="$(dpkg --print-architecture | awk -F- '{ print $NF }')"; \
	wget -O /usr/local/bin/gosu "https://github.com/tianon/gosu/releases/download/$GOSU_VERSION/gosu-$dpkgArch"; \
	wget -O /usr/local/bin/gosu.asc "https://github.com/tianon/gosu/releases/download/$GOSU_VERSION/gosu-$dpkgArch.asc"; \
	\
# verify the signature
	export GNUPGHOME="$(mktemp -d)"; \
	gpg --keyserver ha.pool.sks-keyservers.net --recv-keys B42F6819007F00F88E364FD4036A9C25BF357DD4; \
	gpg --batch --verify /usr/local/bin/gosu.asc /usr/local/bin/gosu; \
	rm -r "$GNUPGHOME" /usr/local/bin/gosu.asc; \
	\
	chmod +x /usr/local/bin/gosu; \
# verify that the binary works
	gosu nobody true;

# Install tini
ARG TINI_VERSION=v0.14.0
RUN wget -O /usr/local/bin/tini "https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini"
RUN chmod +x /usr/local/bin/tini

# The stuff above this is pretty standard image-prep, now we throw our customer layers down.

# Update pip and install SSL certs
RUN pip3 install -U pip certifi

ADD . /src
RUN pip3 install -e /src/

VOLUME /workspace
WORKDIR /workspace
COPY docker-entrypoint.sh /docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/tini", "--", "/docker-entrypoint.sh"]
CMD ["/bin/bash"]
