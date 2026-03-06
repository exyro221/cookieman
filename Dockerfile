FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
  python3 python3-pip python3-venv \
  git \
  inkscape openscad potrace imagemagick \
  && rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/bin/openscad /usr/bin/openscad-nightly

WORKDIR /opt
RUN git clone https://github.com/Papooch/cookie-cutter-generator.git

RUN sed -i 's/openscad-nightly/openscad/g' /opt/cookie-cutter-generator/generate-model.sh
RUN sed -i 's/--enable=fast-csg//g' /opt/cookie-cutter-generator/generate-model.sh

WORKDIR /app
COPY app /app

# Create venv + install Python deps inside it (avoids PEP 668 issues)
RUN python3 -m venv /opt/venv \
  && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
  && /opt/venv/bin/pip install --no-cache-dir \
       fastapi uvicorn python-multipart \
       opencv-python-headless numpy

ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8088
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8088"]

