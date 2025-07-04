FROM python:3.12-alpine AS build

RUN \
    --mount=type=cache,target=/root/.cache/pip \
    --mount=source=.,target=/root/src/proxpi,rw \
    uname -a && cat /etc/issue && apk --version && python --version && pip --version \
 && apk --no-cache add git \
 && git -C /root/src/proxpi restore .dockerignore \
 && pip install build \
 && python -m build --outdir /srv/proxpi/dist /root/src/proxpi \
 && ls /srv/proxpi/dist

FROM python:3.12-alpine

RUN \
    --mount=type=cache,target=/root/.cache/pip \
    --mount=source=app.requirements.txt,target=/mnt/src/app.requirements.txt \
    uname -a && cat /etc/issue && python --version && pip --version \
 && pip install --no-deps --requirement /mnt/src/app.requirements.txt \
 && pip list

RUN \
    --mount=from=build,source=/srv/proxpi/dist,target=/srv/proxpi/dist \
    uname -a && cat /etc/issue && python --version && pip --version \
 && pip install --no-cache-dir --no-index \
        --find-links /srv/proxpi/dist \
        proxpi gunicorn \
    \
 && pip show proxpi gunicorn

ENTRYPOINT [ \
    "gunicorn", \
    "--preload", \
    "--access-logfile", "-", \
    "--access-logformat", "%(h)s \"%(r)s\" %(s)s %(b)s %(M)dms", \
    "--logger-class", "proxpi.server._GunicornLogger", \
    "proxpi.server:app" \
]
CMD ["--bind", "0.0.0.0:5000", "--threads", "2"]
