FROM python:alpine

RUN --mount=source=.,target=/root/src/proxpi,rw \
    uname -a && cat /etc/issue && apk --version && python --version && pip --version \
 && apk --no-cache add git \
 && git -C /root/src/proxpi restore .dockerignore \
 && pip install /root/src/proxpi \
 && apk del --purge git \
 && pip list

ENV FLASK_APP=proxpi.server
ENTRYPOINT ["flask", "run"]
CMD ["--host", "0.0.0.0"]
