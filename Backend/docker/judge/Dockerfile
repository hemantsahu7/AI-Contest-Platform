FROM debian:bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends g++ coreutils \
  && rm -rf /var/lib/apt/lists/* \
  && useradd --create-home --uid 1000 --shell /usr/sbin/nologin judge \
  && mkdir -p /workspace \
  && chown judge:judge /workspace

WORKDIR /workspace
USER judge
