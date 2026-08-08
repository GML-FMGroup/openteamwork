FROM python:3.14-slim

RUN mkdir -p /opt/openppx/openppx/runtime/sandbox \
    && touch /opt/openppx/openppx/__init__.py \
    && touch /opt/openppx/openppx/runtime/__init__.py \
    && touch /opt/openppx/openppx/runtime/sandbox/__init__.py

COPY openppx/runtime/sandbox/egress_policy.py /opt/openppx/openppx/runtime/sandbox/egress_policy.py
COPY openppx/runtime/sandbox/egress_proxy.py /opt/openppx/openppx/runtime/sandbox/egress_proxy.py

WORKDIR /opt/openppx
ENTRYPOINT ["python", "-m", "openppx.runtime.sandbox.egress_proxy"]
