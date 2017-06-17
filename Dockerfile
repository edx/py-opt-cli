FROM ubuntu:16.04
MAINTAINER Gabe Mulley <gabe@edx.org>

# Heavily inspired by https://hub.docker.com/r/airdock/base/

USER root

# Never prompts the user for choices on installation/configuration of packages
# No dialog on apt-get update
# Work around initramfs-tools running on kernel 'upgrade': <http://bugs.debian.org/cgi-bin/bugreport.cgi?bug=594189>
# Define en_US.

ENV DEBIAN_FRONTEND=noninteractive \
    TERM=linux \
    INITRD=No \
    LANGUAGE=en_US.UTF-8 \
    LANG=en_US.UTF-8  \
    LC_ALL=en_US.UTF-8 \
    LC_CTYPE=en_US.UTF-8 \
    LC_MESSAGES=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    GOSU_VERSION=1.9 \
    TINI_VERSION=v0.13.2


# Install curl, locales, apt-utils, gosu and tini
# create en_US.UTF-8
# update distribution package
# add few common alias to root user
# add utilities (create user, post install script)
# create airdock user list
RUN set -x && \
  apt-get update -qq && \
  apt-get install -y apt-utils curl locales && \
  apt-get install -y --no-install-recommends ca-certificates wget && \
  sed -i 's/^# en_US.UTF-8 UTF-8$/en_US.UTF-8 UTF-8/g' /etc/locale.gen && locale-gen && \
  update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 && \
  apt-get update -y && \
  dpkgArch="$(dpkg --print-architecture | awk -F- '{ print $NF }')"  && \
  wget -O /usr/local/bin/gosu "https://github.com/tianon/gosu/releases/download/$GOSU_VERSION/gosu-$dpkgArch"  && \
  wget -O /usr/local/bin/gosu.asc "https://github.com/tianon/gosu/releases/download/$GOSU_VERSION/gosu-$dpkgArch.asc"  && \
  export GNUPGHOME="$(mktemp -d)"  && \
  gpg --keyserver ha.pool.sks-keyservers.net --recv-keys B42F6819007F00F88E364FD4036A9C25BF357DD4  && \
  gpg --batch --verify /usr/local/bin/gosu.asc /usr/local/bin/gosu  && \
  rm -r /usr/local/bin/gosu.asc  && \
  chmod +x /usr/local/bin/gosu  && \
  gosu nobody true  && \
  wget -O /usr/local/bin/tini "https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini" && \
  wget -O /usr/local/bin/tini.asc "https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini.asc" && \
  gpg --keyserver ha.pool.sks-keyservers.net --recv-keys 595E85A6B1B4779EA4DAAEC70B588DFF0527A9B7 && \
  gpg --batch --verify /usr/local/bin/tini.asc /usr/local/bin/tini  && \
  rm -r "$GNUPGHOME" /usr/local/bin/tini.asc  && \
  chmod +x /usr/local/bin/tini  && \
  apt-get purge -y --auto-remove ca-certificates wget

# The stuff above this is pretty standard image-prep, now we throw our customer layers down.
RUN set -x && \
  apt-get update -qq && \
  apt-get install -y sudo python3-pip python3-dev git

RUN pip3 install -U pip

ADD . /src
RUN pip3 install -e /src/

VOLUME /workspace
WORKDIR /workspace
COPY docker-entrypoint.sh /docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/tini", "--", "/docker-entrypoint.sh"]
CMD ["/bin/bash"]
