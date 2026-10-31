FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        mininet openvswitch-switch openvswitch-testcontroller \
        python3 python3-pip iproute2 iputils-ping net-tools procps psmisc kmod \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir "setuptools<68" "eventlet==0.30.2" "ryu==4.34" \
    https://github.com/mininet/mininet/archive/refs/tags/2.3.1b4.tar.gz

WORKDIR /project
COPY docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["bash", "/entrypoint.sh"]
CMD ["bash"]
