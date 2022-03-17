FROM python:3.10-alpine

RUN --mount=source=.,target=/root/src/proxpi,rw \
    uname -a && cat /etc/issue && apk --version && python --version && pip --version \
 && apk --no-cache add git \
 && git -C /root/src/proxpi restore .dockerignore \
 && pip install /root/src/proxpi gunicorn \
 && apk del --purge git \
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
