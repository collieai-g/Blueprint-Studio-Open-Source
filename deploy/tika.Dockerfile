# syntax=docker/dockerfile:1.7
FROM apache/tika:3.2.3.0-full
USER root
RUN --mount=type=secret,id=proxy_ca \
    if [ -s /run/secrets/proxy_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/proxy_ca > /tmp/build-ca.pem; \
      printf 'Acquire::https::CaInfo "/tmp/build-ca.pem";\n' > /etc/apt/apt.conf.d/99-build-ca; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends tesseract-ocr-chi-tra tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/* /tmp/build-ca.pem /etc/apt/apt.conf.d/99-build-ca
USER 10001:10001
