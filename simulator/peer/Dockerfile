FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends wireguard-tools iproute2 curl ca-certificates tini python3 \
 && rm -rf /var/lib/apt/lists/*
COPY traffic/ /opt/traffic/
RUN printf '#!/bin/sh\nexec python3 /opt/traffic/run.py "$@"\n' > /usr/local/bin/traffic \
 && chmod +x /usr/local/bin/traffic
COPY entrypoint.sh gw-ip /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/gw-ip
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
