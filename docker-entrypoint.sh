#!/bin/bash

# From: https://denibertovic.com/posts/handling-permissions-with-docker-volumes/

# Add local user
# Either use the LOCAL_USER_ID if passed in at runtime or
# fallback

USER_ID=${LOCAL_USER_ID:-9001}

useradd --shell /bin/bash -u $USER_ID -o -c "" -m developer
echo "developer ALL=(root) NOPASSWD:ALL" >> /etc/sudoers
export HOME=/home/developer

exec gosu developer "$@"
