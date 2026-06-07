# syntax=docker/dockerfile:1.4
# 自訂 ES 映像（安裝 IK 分詞）；build 時需透過 scripts/compose-dev.sh 帶入 proxy
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy

FROM elasticsearch:9.0.1

USER root
COPY iap_elasticsearch/elasticsearch-analysis-ik-9.0.1.zip /tmp/elasticsearch-analysis-ik-9.0.1.zip
RUN if [ -f /tmp/elasticsearch-analysis-ik-9.0.1.zip ]; then \
      bin/elasticsearch-plugin install --batch file:///tmp/elasticsearch-analysis-ik-9.0.1.zip || true; \
    fi
USER elasticsearch
