FROM python:alpine
RUN apk add --no-cache libxslt libxml2
ADD . /root/src/proxpi
RUN apk add --no-cache --virtual .build-deps gcc libc-dev libxslt-dev libxml2-dev \
 && pip install /root/src/proxpi \
 && apk del .build-deps
ENV FLASK_APP=proxpi.server
ENTRYPOINT ["flask"]
CMD ["run", "--host", "0.0.0.0"]
