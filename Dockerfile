# This builder provides tools for building images with support for several platforms, more info at https://github.com/tonistiigi/xx
FROM --platform=$BUILDPLATFORM tonistiigi/xx AS xx

FROM python:3.11-alpine
# copy xx scripts to your build stage
COPY --from=xx / /

ARG TARGETPLATFORM

RUN --mount=source=.,target=/root/src/proxpi,rw \
    uname -a && cat /etc/issue && xx-apk --version && python --version && pip --version \
 && xx-apk --update --no-cache add g++ gcc libxslt-dev libxml2-dev git \
 && git -C /root/src/proxpi restore .dockerignore \
 && pip install --no-cache-dir --no-deps \
    --requirement /root/src/proxpi/app.requirements.txt \
 && pip install --no-cache-dir --no-deps /root/src/proxpi/ \
 && pip show gunicorn \
 && xx-apk del --purge g++ gcc libxslt-dev libxml2-dev git \
 && pip list

ENTRYPOINT [ \
    "gunicorn", \
    "--preload", \
    "--access-logfile", "-", \
    "--access-logformat", "%(h)s \"%(r)s\" %(s)s %(b)s %(M)dms", \
    "--logger-class", "proxpi.server._GunicornLogger", \
    "proxpi.server:app" \
]
CMD ["--bind", "0.0.0.0:5000", "--threads", "2"]
