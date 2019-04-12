FROM python:3.6-slim
MAINTAINER Gabe Mulley <gabe@edx.org>

USER root

ENV DEBIAN_FRONTEND=noninteractive \
	PYTHONUNBUFFERED=1

# Install system packages from apt
RUN apt-get update -qq && apt-get install -y --no-install-recommends ca-certificates gosu git

# Update pip and install SSL certs
RUN pip3 install -U pip certifi

ADD . /src
RUN pip3 install -e /src/

VOLUME /workspace
WORKDIR /workspace
COPY docker-entrypoint.sh /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["/bin/bash"]
